"""
Manager/Admin Help Request approval routes.
ADD to main.py:  app.include_router(help_requests.router, prefix="/admin")

Either a MANAGER or ADMIN can approve/deny — whoever acts first wins.
A manager only sees requests for their own department; an admin sees all.
"""
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

from app.database import get_db
from app.models.user import User, UserRole
from app.models.help_request import HelpRequest, HelpRequestStatus
from app.models.ticket import Ticket
from app.core.deps import require_role, get_current_user

router = APIRouter(tags=["Help Requests"])


# ── Schemas ───────────────────────────────────────────────

class PendingHelpRequestItem(BaseModel):
    id:               uuid.UUID
    ticket_id:        uuid.UUID
    ticket_title:     str
    ticket_priority:  str
    ticket_category:  Optional[str]
    lead_id:          uuid.UUID
    lead_name:        str
    helper_id:        uuid.UUID
    helper_name:      str
    created_at:       datetime


class DenyRequest(BaseModel):
    notes: Optional[str] = None


# ── GET /admin/help-requests ───────────────────────────────

@router.get("/help-requests", response_model=list[PendingHelpRequestItem])
async def list_pending_help_requests(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.MANAGER, UserRole.ADMIN)),
):
    """
    Pending help requests awaiting approval.
    Managers see only their own department's requests.
    Admins see every pending request system-wide.
    """
    query = select(HelpRequest).where(HelpRequest.status == HelpRequestStatus.PENDING)
    result = await db.execute(query)
    requests = result.scalars().all()

    items = []
    for r in requests:
        ticket = r.ticket
        if current_user.role == UserRole.MANAGER and ticket.category != (current_user.department.value if current_user.department else None):
            continue   # manager only sees their own department
        items.append(PendingHelpRequestItem(
            id=r.id,
            ticket_id=ticket.id,
            ticket_title=ticket.title,
            ticket_priority=ticket.priority.value,
            ticket_category=ticket.category,
            lead_id=r.requested_by_id,
            lead_name=r.requested_by.full_name,
            helper_id=r.helper_id,
            helper_name=r.helper.full_name,
            created_at=r.created_at,
        ))

    items.sort(key=lambda x: x.created_at, reverse=True)
    return items


# ── POST /admin/help-requests/{id}/approve ────────────────

@router.post("/help-requests/{request_id}/approve")
async def approve_request(
    request_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.MANAGER, UserRole.ADMIN)),
):
    """
    Approve a pending help request. Actually attaches the helper to the
    ticket with full pause/SLA-sharing logic, then emails the helper,
    lead worker, and admin.
    """
    from app.services.assignment_service import approve_help_request

    result = await approve_help_request(request_id, current_user, db)
    if not result["approved"]:
        raise HTTPException(400, result["reason"])

    await db.flush()

    ticket = result["ticket"]
    helper = result["helper"]
    lead_id = result["lead_id"]

    # ── Notify helper + lead + admin ──────────────────────
    # FIX: send directly using in-memory objects — no new DB session needed.
    # The ticket/helper/lead objects are already available in this scope.
    try:
        from app.services.email_service import send_help_request_approved, get_admin_emails
        from sqlalchemy import select as sa_select
        lead_res = await db.execute(sa_select(User).where(User.id == lead_id))
        lead_user = lead_res.scalar_one_or_none()
        admin_emails = await get_admin_emails(db)
        recipients = list(set(
            [helper.email, lead_user.email] + admin_emails
        )) if lead_user else list(set([helper.email] + admin_emails))
        await send_help_request_approved(
            ticket=ticket, lead_name=lead_user.full_name if lead_user else "Lead worker",
            helper_name=helper.full_name,
            approver_name=current_user.full_name,
            recipient_emails=recipients,
        )
    except Exception as e:
        print(f"   (approval email skipped: {e})")

    return {"message": f"Approved — {helper.full_name} added to ticket"}


# ── POST /admin/help-requests/{id}/deny ───────────────────

@router.post("/help-requests/{request_id}/deny")
async def deny_request(
    request_id: uuid.UUID,
    data: DenyRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.MANAGER, UserRole.ADMIN)),
):
    """Deny a pending help request. Nothing is attached to the ticket."""
    from app.services.assignment_service import deny_help_request

    req = await db.get(HelpRequest, request_id)
    if not req:
        raise HTTPException(404, "Request not found")

    ticket = req.ticket
    helper = req.helper
    lead   = req.requested_by

    result = await deny_help_request(request_id, current_user, data.notes, db)
    if not result["denied"]:
        raise HTTPException(400, result["reason"])

    await db.flush()

    # ── Notify the lead worker ────────────────────────────
    # FIX: send directly — ticket is already committed, lead/helper are in scope.
    try:
        from app.services.email_service import send_help_request_denied
        await send_help_request_denied(
            ticket=ticket,
            lead_name=lead.full_name,
            helper_name=helper.full_name,
            decider_name=current_user.full_name,
            notes=data.notes,
            recipient_emails=[lead.email],
        )
    except Exception as e:
        print(f"   (denial email skipped: {e})")

    return {"message": f"Denied — {helper.full_name} was not added"}
