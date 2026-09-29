from datetime import datetime, timezone
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from pydantic import BaseModel
from typing import Dict

from app.database import get_db
from app.models.user import User
from app.models.ticket import Ticket, TicketStatus
from app.core.deps import get_current_user

router = APIRouter(tags=["Analytics"])


class AnalyticsSummaryResponse(BaseModel):
    total_tickets: int
    by_status: Dict[str, int]
    by_priority: Dict[str, int]
    by_category: Dict[str, int]
    sla_breaches: int


# ── GET /analytics/summary ────────────────────────────────
@router.get("/summary", response_model=AnalyticsSummaryResponse)  # FIX: added response_model
async def analytics_summary(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    total = await db.scalar(select(func.count(Ticket.id)))

    status_rows = await db.execute(
        select(Ticket.status, func.count(Ticket.id)).group_by(Ticket.status)
    )
    by_status = {s.value: c for s, c in status_rows.all()}

    priority_rows = await db.execute(
        select(Ticket.priority, func.count(Ticket.id)).group_by(Ticket.priority)
    )
    by_priority = {p.value: c for p, c in priority_rows.all()}

    category_rows = await db.execute(
        select(Ticket.category, func.count(Ticket.id)).group_by(Ticket.category)
    )
    by_category = {(cat or "uncategorized"): c for cat, c in category_rows.all()}

    now = datetime.now(timezone.utc)
    sla_breaches = await db.scalar(
        select(func.count(Ticket.id)).where(
            Ticket.sla_deadline.is_not(None),   # FIX: SQLAlchemy 2.0 safe comparison
            Ticket.sla_deadline < now,
            Ticket.status.notin_([TicketStatus.RESOLVED, TicketStatus.CLOSED]),
        )
    )

    return AnalyticsSummaryResponse(
        total_tickets=total or 0,
        by_status=by_status,
        by_priority=by_priority,
        by_category=by_category,
        sla_breaches=sla_breaches or 0,
    )
