"""
ADD THIS TO app/routers/admin.py

Paste the schemas into the Schemas section at the top,
and paste the route function anywhere below the existing routes.

Requires these additional imports at the top of admin.py:
    from sqlalchemy import select, func, and_
    (func and select are already imported — just add `and_`)
"""

# ── New imports needed in admin.py ────────────────────────
# Add `and_` to the existing sqlalchemy import line:
#   from sqlalchemy import select, func, and_

# ── Paste these schemas into admin.py ─────────────────────

from pydantic import BaseModel
from typing import Dict

class DeptSLAStats(BaseModel):
    total: int
    in_progress: int
    breached: int
    breach_rate: float   # 0.0–1.0


class WorkerPerformance(BaseModel):
    id: uuid.UUID
    full_name: str
    email: str
    department: str | None
    active_tickets: int
    breach_count: int
    penalty_points: int
    is_blacklisted: bool


class SLADashboardResponse(BaseModel):
    total_active: int
    total_breached: int
    overall_breach_rate: float
    avg_resolution_hours: float | None
    by_department: Dict[str, DeptSLAStats]
    worker_performance: list[WorkerPerformance]


# ── Paste this route into admin.py ────────────────────────

@router.get("/sla-dashboard", response_model=SLADashboardResponse)
async def sla_dashboard(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(UserRole.ADMIN, UserRole.MANAGER)),
):
    """
    Aggregate SLA health view:
    - breach rate per department
    - average resolution time
    - worker performance ranking
    """
    from datetime import timezone
    from sqlalchemy import and_
    from app.models.ticket import TicketStatus as TS

    now = datetime.now(timezone.utc)

    # ── Active tickets (IN_PROGRESS + CRITICAL_REVIEW) ───
    active_result = await db.execute(
        select(Ticket).where(
            Ticket.status.in_([TS.IN_PROGRESS, TS.CRITICAL_REVIEW])
        )
    )
    active_tickets = active_result.scalars().all()

    # ── Resolved tickets for avg resolution time ─────────
    resolved_result = await db.execute(
        select(Ticket).where(
            Ticket.status == TS.RESOLVED,
            Ticket.resolved_at.is_not(None),
            Ticket.assigned_at.is_not(None),
        )
    )
    resolved_tickets = resolved_result.scalars().all()

    # ── Avg resolution time ───────────────────────────────
    avg_resolution_hours = None
    if resolved_tickets:
        total_hours = sum(
            (t.resolved_at - t.assigned_at).total_seconds() / 3600
            for t in resolved_tickets
            if t.resolved_at and t.assigned_at
        )
        avg_resolution_hours = round(total_hours / len(resolved_tickets), 2)

    # ── Breach detection ──────────────────────────────────
    def is_breached(t):
        return (
            t.sla_deadline is not None and
            t.sla_deadline < now and
            t.status not in (TS.RESOLVED, TS.CLOSED)
        )

    # ── By department (ticket.category maps to dept) ──────
    dept_map: dict[str, dict] = {}
    for t in active_tickets:
        dept = t.category or "uncategorized"
        if dept not in dept_map:
            dept_map[dept] = {"total": 0, "in_progress": 0, "breached": 0}
        dept_map[dept]["total"] += 1
        if t.status == TS.IN_PROGRESS:
            dept_map[dept]["in_progress"] += 1
        if is_breached(t):
            dept_map[dept]["breached"] += 1

    by_department: dict[str, DeptSLAStats] = {}
    for dept, stats in dept_map.items():
        total = stats["total"]
        breach_rate = round(stats["breached"] / total, 4) if total else 0.0
        by_department[dept] = DeptSLAStats(
            total=total,
            in_progress=stats["in_progress"],
            breached=stats["breached"],
            breach_rate=breach_rate,
        )

    # ── Worker performance ────────────────────────────────
    workers_result = await db.execute(
        select(User).where(User.role == UserRole.WORKER)
    )
    all_workers = workers_result.scalars().all()

    # Count active tickets per worker
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

    # ── Totals ────────────────────────────────────────────
    total_active   = len(active_tickets)
    total_breached = sum(1 for t in active_tickets if is_breached(t))
    overall_breach_rate = round(total_breached / total_active, 4) if total_active else 0.0

    return SLADashboardResponse(
        total_active=total_active,
        total_breached=total_breached,
        overall_breach_rate=overall_breach_rate,
        avg_resolution_hours=avg_resolution_hours,
        by_department=by_department,
        worker_performance=worker_performance,
    )
