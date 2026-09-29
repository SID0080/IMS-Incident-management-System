import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from pydantic import BaseModel

from app.database import get_db
from app.models.user import User, UserRole
from app.models.ticket import Ticket, TicketStatus, TicketPriority
from app.models.audit_log import AuditLog
from app.schemas.user import UserResponse
from app.core.deps import require_role

router = APIRouter(tags=["Admin"])


# ── Schemas ───────────────────────────────────────────────

class AuditLogResponse(BaseModel):
    id: uuid.UUID
    ticket_id: uuid.UUID
    user_id: uuid.UUID
    action: str
    previous_status: Optional[str] = None
    new_status: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime
    model_config = {"from_attributes": True}


class UserStatusUpdate(BaseModel):
    is_active: bool


class UserRoleUpdate(BaseModel):
    role: UserRole


class DashboardStatsResponse(BaseModel):
    total_users: int
    total_tickets: int
    open_tickets: int
    critical_open: int
    sla_breaches: int
    new_this_week: int


# ── Phase 6: SLA Dashboard schemas ───────────────────────

class DeptSLAStats(BaseModel):
    total: int
    in_progress: int
    breached: int
    breach_rate: float


class WorkerPerformance(BaseModel):
    id: uuid.UUID
    full_name: str
    email: str
    department: Optional[str]
    active_tickets: int
    breach_count: int
    penalty_points: int
    is_blacklisted: bool


class SLADashboardResponse(BaseModel):
    total_active: int
    total_breached: int
    overall_breach_rate: float
    avg_resolution_hours: Optional[float]
    by_department: Dict[str, DeptSLAStats]
    worker_performance: list[WorkerPerformance]


# ── Routes ────────────────────────────────────────────────

@router.get("/dashboard", response_model=DashboardStatsResponse)
async def admin_dashboard(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(UserRole.ADMIN, UserRole.MANAGER)),
):
    """Full dashboard stats for admin panel."""
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)

    total_users   = await db.scalar(select(func.count()).select_from(User))
    total_tickets = await db.scalar(select(func.count()).select_from(Ticket))
    open_tickets  = await db.scalar(select(func.count()).where(Ticket.status == TicketStatus.OPEN))
    critical_open = await db.scalar(
        select(func.count()).where(
            Ticket.priority == TicketPriority.CRITICAL,
            Ticket.status.in_([TicketStatus.OPEN, TicketStatus.IN_PROGRESS])
        )
    )
    sla_breaches = await db.scalar(
        select(func.count()).where(
            Ticket.sla_deadline < now,
            Ticket.status.notin_([TicketStatus.RESOLVED, TicketStatus.CLOSED])
        )
    )
    new_this_week = await db.scalar(select(func.count()).where(Ticket.created_at >= week_ago))

    return DashboardStatsResponse(
        total_users=total_users or 0,
        total_tickets=total_tickets or 0,
        open_tickets=open_tickets or 0,
        critical_open=critical_open or 0,
        sla_breaches=sla_breaches or 0,
        new_this_week=new_this_week or 0,
    )


@router.get("/users", response_model=list[UserResponse])
async def list_all_users(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(UserRole.ADMIN)),
):
    result = await db.execute(select(User).order_by(User.created_at.desc()))
    return [UserResponse.model_validate(u) for u in result.scalars().all()]


@router.patch("/users/{user_id}/status", response_model=UserResponse)
async def toggle_user_status(
    user_id: uuid.UUID,
    data: UserStatusUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(UserRole.ADMIN)),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.is_active = data.is_active
    await db.flush()
    await db.refresh(user)
    return UserResponse.model_validate(user)


@router.patch("/users/{user_id}/role", response_model=UserResponse)
async def change_user_role(
    user_id: uuid.UUID,
    data: UserRoleUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(UserRole.ADMIN)),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.role = data.role
    await db.flush()
    await db.refresh(user)
    return UserResponse.model_validate(user)


@router.get("/audit-logs", response_model=list[AuditLogResponse])
async def get_audit_logs(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(UserRole.ADMIN)),
):
    result = await db.execute(
        select(AuditLog).order_by(AuditLog.created_at.desc()).limit(200)
    )
    return [AuditLogResponse.model_validate(log) for log in result.scalars().all()]


# ── Phase 6: GET /admin/sla-dashboard ────────────────────

@router.get("/sla-dashboard", response_model=SLADashboardResponse)
async def sla_dashboard(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(UserRole.ADMIN, UserRole.MANAGER)),
):
    """
    Aggregate SLA health:
    - breach rate per department
    - average resolution time across all resolved tickets
    - worker performance ranking sorted by penalty points
    """
    now = datetime.now(timezone.utc)

    # Active tickets
    active_res = await db.execute(
        select(Ticket).where(
            Ticket.status.in_([TicketStatus.IN_PROGRESS, TicketStatus.CRITICAL_REVIEW])
        )
    )
    active_tickets = active_res.scalars().all()

    # Resolved tickets for avg resolution time
    resolved_res = await db.execute(
        select(Ticket).where(
            Ticket.status == TicketStatus.RESOLVED,
            Ticket.resolved_at.is_not(None),
            Ticket.assigned_at.is_not(None),
        )
    )
    resolved_tickets = resolved_res.scalars().all()

    # Avg resolution hours
    avg_resolution_hours = None
    if resolved_tickets:
        total_hrs = sum(
            (t.resolved_at - t.assigned_at).total_seconds() / 3600
            for t in resolved_tickets
            if t.resolved_at and t.assigned_at
        )
        avg_resolution_hours = round(total_hrs / len(resolved_tickets), 2)

    # Breach check helper
    def is_breached(t: Ticket) -> bool:
        return (
            t.sla_deadline is not None and
            t.sla_deadline < now and
            t.status not in (TicketStatus.RESOLVED, TicketStatus.CLOSED)
        )

    # By department
    dept_map: dict[str, dict] = {}
    for t in active_tickets:
        dept = t.category or "uncategorized"
        if dept not in dept_map:
            dept_map[dept] = {"total": 0, "in_progress": 0, "breached": 0}
        dept_map[dept]["total"] += 1
        if t.status == TicketStatus.IN_PROGRESS:
            dept_map[dept]["in_progress"] += 1
        if is_breached(t):
            dept_map[dept]["breached"] += 1

    by_department = {
        dept: DeptSLAStats(
            total=s["total"],
            in_progress=s["in_progress"],
            breached=s["breached"],
            breach_rate=round(s["breached"] / s["total"], 4) if s["total"] else 0.0,
        )
        for dept, s in dept_map.items()
    }

    # Worker performance
    workers_res = await db.execute(select(User).where(User.role == UserRole.WORKER))
    all_workers = workers_res.scalars().all()

    worker_active: dict = {}
    for t in active_tickets:
        if t.assignee_id:
            worker_active[t.assignee_id] = worker_active.get(t.assignee_id, 0) + 1

    worker_performance = [
        WorkerPerformance(
            id=w.id,
            full_name=w.full_name,
            email=w.email,
            department=w.department.value if w.department else None,
            active_tickets=worker_active.get(w.id, 0),
            breach_count=w.breach_count,
            penalty_points=w.penalty_points,
            is_blacklisted=w.is_blacklisted,
        )
        for w in sorted(all_workers, key=lambda x: x.penalty_points, reverse=True)
    ]

    total_active   = len(active_tickets)
    total_breached = sum(1 for t in active_tickets if is_breached(t))

    return SLADashboardResponse(
        total_active=total_active,
        total_breached=total_breached,
        overall_breach_rate=round(total_breached / total_active, 4) if total_active else 0.0,
        avg_resolution_hours=avg_resolution_hours,
        by_department=by_department,
        worker_performance=worker_performance,
    )
