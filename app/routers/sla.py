"""
SLA Monitoring Router — Phase 6

GET /sla/status          → all active tickets with countdown + risk level
GET /sla/status/{id}     → single ticket SLA detail
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from pydantic import BaseModel

from app.database import get_db
from app.models.ticket import Ticket, TicketStatus, TicketPriority, Terminal
from app.models.user import User
from app.core.deps import require_role
from app.config import settings

router = APIRouter(tags=["SLA"])

# ── SLA total seconds per priority (from .env) ────────────
SLA_TOTAL_SECONDS: dict[TicketPriority, float] = {
    TicketPriority.CRITICAL: settings.SLA_CRITICAL_RESPONSE_HRS * 3600,
    TicketPriority.HIGH:     settings.SLA_HIGH_RESPONSE_HRS     * 3600,
    TicketPriority.MEDIUM:   settings.SLA_MEDIUM_RESPONSE_HRS   * 3600,
    TicketPriority.LOW:      settings.SLA_LOW_RESPONSE_HRS      * 3600,
}

# Active statuses — tickets we care about for SLA monitoring
ACTIVE_STATUSES = [TicketStatus.IN_PROGRESS, TicketStatus.CRITICAL_REVIEW]


# ── Schemas ───────────────────────────────────────────────

class SLATicketResponse(BaseModel):
    id: uuid.UUID
    ticket_number: Optional[str] = None
    title: str
    priority: TicketPriority
    status: TicketStatus
    category: Optional[str]
    location: Optional[str]
    terminal: Optional[Terminal] = None

    # Assignment
    assignee_id:   Optional[uuid.UUID]
    assignee_name: Optional[str]
    assignee_dept: Optional[str]

    # SLA timing
    assigned_at:            Optional[datetime]
    sla_deadline:           Optional[datetime]
    time_remaining_seconds: Optional[int]    # negative = already breached
    total_sla_seconds:      Optional[int]    # total window for this priority
    pct_remaining:          Optional[float]  # 0.0–1.0, can be negative

    # Risk classification
    risk_level: str   # safe | warning | danger | breached | paused
    is_paused:  bool  # True when preempted by a higher-priority ticket

    created_at: datetime

    model_config = {"from_attributes": True}


class SLASummaryResponse(BaseModel):
    total_active: int
    safe: int
    warning: int
    danger: int
    breached: int
    paused: int
    tickets: list[SLATicketResponse]


# ── Helpers ───────────────────────────────────────────────

def _compute_risk(ticket: Ticket, now: datetime) -> tuple[int | None, float | None, str]:
    """
    Returns (time_remaining_seconds, pct_remaining, risk_level).
    risk_level: safe | warning | danger | breached | paused
    """
    # Paused (preempted by a higher-priority ticket) — SLA clock is frozen.
    # Report the saved remaining time so the UI can show it static, not ticking.
    if ticket.is_paused:
        remaining = ticket.remaining_sla_seconds
        total     = SLA_TOTAL_SECONDS.get(ticket.priority, 86400)
        pct       = round(remaining / total, 4) if (remaining is not None and total) else None
        return remaining, pct, "paused"

    if not ticket.sla_deadline or not ticket.assigned_at:
        return None, None, "safe"

    remaining = int((ticket.sla_deadline - now).total_seconds())
    total     = SLA_TOTAL_SECONDS.get(ticket.priority, 86400)
    pct       = remaining / total if total else 0.0

    if remaining <= 0:
        risk = "breached"
    elif pct < 0.25:
        risk = "danger"
    elif pct < 0.50:
        risk = "warning"
    else:
        risk = "safe"

    return remaining, round(pct, 4), risk


def _build_sla_ticket(ticket: Ticket, now: datetime) -> SLATicketResponse:
    remaining, pct, risk = _compute_risk(ticket, now)
    total = int(SLA_TOTAL_SECONDS.get(ticket.priority, 86400))

    assignee      = ticket.assignee
    assignee_name = assignee.full_name if assignee else None
    assignee_dept = assignee.department.value if assignee and assignee.department else None

    return SLATicketResponse(
        id=ticket.id,
        ticket_number=getattr(ticket, "ticket_number", None),
        title=ticket.title,
        priority=ticket.priority,
        status=ticket.status,
        category=ticket.category,
        location=ticket.location,
        terminal=ticket.terminal,
        assignee_id=ticket.assignee_id,
        assignee_name=assignee_name,
        assignee_dept=assignee_dept,
        assigned_at=ticket.assigned_at,
        sla_deadline=ticket.sla_deadline,
        time_remaining_seconds=remaining,
        total_sla_seconds=total,
        pct_remaining=pct,
        risk_level=risk,
        is_paused=ticket.is_paused,
        created_at=ticket.created_at,
    )


# ── GET /sla/status ───────────────────────────────────────

@router.get("/status", response_model=SLASummaryResponse)
async def get_sla_status(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("MANAGER", "ADMIN")),
):
    """
    Returns all active (IN_PROGRESS + CRITICAL_REVIEW) tickets with
    per-ticket SLA countdown and risk level.
    Sorted: breached first → danger → warning → safe.
    """
    now = datetime.now(timezone.utc)

    result = await db.execute(
        select(Ticket).where(
            Ticket.status.in_(ACTIVE_STATUSES)
        )
    )
    tickets = result.scalars().all()

    sla_tickets = [_build_sla_ticket(t, now) for t in tickets]

    # Sort: breached first, then by time_remaining ascending (most urgent first)
    risk_order = {"breached": 0, "danger": 1, "warning": 2, "safe": 3, "paused": 4}
    sla_tickets.sort(key=lambda t: (
        risk_order.get(t.risk_level, 5),
        t.time_remaining_seconds if t.time_remaining_seconds is not None else 999999
    ))

    return SLASummaryResponse(
        total_active=len(sla_tickets),
        safe=    sum(1 for t in sla_tickets if t.risk_level == "safe"),
        warning= sum(1 for t in sla_tickets if t.risk_level == "warning"),
        danger=  sum(1 for t in sla_tickets if t.risk_level == "danger"),
        breached=sum(1 for t in sla_tickets if t.risk_level == "breached"),
        paused=  sum(1 for t in sla_tickets if t.risk_level == "paused"),
        tickets=sla_tickets,
    )


# ── GET /sla/status/{ticket_id} ───────────────────────────

@router.get("/status/{ticket_id}", response_model=SLATicketResponse)
async def get_ticket_sla(
    ticket_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("MANAGER", "ADMIN", "WORKER")),
):
    """Single ticket SLA detail — used by the countdown timer in the UI."""
    ticket = await db.get(Ticket, ticket_id)
    if not ticket:
        from fastapi import HTTPException
        raise HTTPException(404, "Ticket not found")

    now = datetime.now(timezone.utc)
    return _build_sla_ticket(ticket, now)
