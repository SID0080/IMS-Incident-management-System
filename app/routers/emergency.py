import uuid
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.models.ticket import Ticket, TicketStatus, TicketPriority
from app.models.audit_log import AuditLog, AuditAction
from app.core.deps import get_current_user

router = APIRouter(tags=["Emergency"])


class EmergencyEscalateRequest(BaseModel):
    type: str = Field(min_length=1)
    location: str = Field(min_length=1)
    description: str = Field(min_length=1)


class EmergencyEscalateResponse(BaseModel):
    id: uuid.UUID
    status: str


@router.post(
    "/escalate",
    response_model=EmergencyEscalateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def escalate_emergency(
    data: EmergencyEscalateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Emergency reports are intentionally MANAGER-TRIAGE-ONLY — they do
    NOT auto-route to a department/worker the way a normal CRITICAL
    ticket does. A human must look at the situation and decide who
    handles it.

    Fix applied: category is left as None (not "emergency_fire" etc).
    The old fake category value didn't match any real department, so
    it could never be routed even if someone tried, and it polluted
    analytics ("Emergency Fire" showing up next to real department
    names on the charts).

    With category=None, this ticket now shows up in the existing
    "⚠️ Uncategorized" tab on manager.html — reusing infrastructure
    that already exists rather than building a parallel path. A
    manager picks the real department there and the ticket routes
    through the normal pipeline (full pause/queue logic applies).

    The emergency TYPE the reporter selected (fire/medical/etc) is
    preserved in the ticket title so nothing is lost.
    """
    from app.services.ticket_service import next_ticket_number

    ticket = Ticket(
        ticket_number=await next_ticket_number(db),
        title=f"EMERGENCY: {data.type.upper()} — {data.location}",
        description=data.description,
        priority=TicketPriority.CRITICAL,
        status=TicketStatus.OPEN,
        location=data.location,
        category=None,   # ← FIX: was f"emergency_{data.type}" — fake dept, broke routing + analytics
        reporter_id=current_user.id,
    )
    db.add(ticket)
    await db.flush()
    await db.refresh(ticket)

    log = AuditLog(
        ticket_id=ticket.id,
        user_id=current_user.id,
        action=AuditAction.ESCALATED,
        new_status=ticket.status.value,
        notes=(
            f"Emergency escalation: {data.type} at {data.location}. "
            f"No department auto-assigned — manager must triage via the "
            f"Uncategorized tab or assign directly from Critical tab."
        ),
    )
    db.add(log)
    await db.flush()

    # ── Email all managers + admins immediately ───────────
    try:
        from app.services.email_service import (
            send_emergency_alert,
            get_all_manager_and_admin_emails,
        )
        recipients = await get_all_manager_and_admin_emails(db)
        await send_emergency_alert(ticket=ticket, recipient_emails=recipients)
    except Exception as e:
        print(f"   (emergency email skipped: {e})")

    return EmergencyEscalateResponse(id=ticket.id, status="escalated")
