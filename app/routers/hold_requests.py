"""
Manager/Admin Hold Request approval routes.
ADD to main.py:  app.include_router(hold_requests.router, prefix="/admin")

Mirrors the help-request approval flow:
  - Either a MANAGER or ADMIN can approve/deny — whoever acts first wins.
  - A manager only sees requests for their own department; an admin sees all.
  - Approval does NOT pause the ticket — it unlocks the worker's Hold
    button. The worker decides when to actually execute the hold.
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
from app.models.hold_request import HoldRequest, HoldRequestStatus
from app.core.deps import require_role

router = APIRouter(tags=["Hold Requests"])


# ── Schemas ───────────────────────────────────────────────

class PendingHoldRequestItem(BaseModel):
    id:                       uuid.UUID
    ticket_id:                uuid.UUID
    ticket_number:            Optional[str]
    ticket_title:             str
    ticket_priority:          str
    ticket_category:          Optional[str]
    blocking_ticket_id:       Optional[uuid.UUID]
    blocking_ticket_number:   Optional[str]
    blocking_ticket_title:    Optional[str]
    blocking_ticket_priority: Optional[str]
    worker_id:                uuid.UUID
    worker_name:              str
    remark:                   str
    created_at:               datetime


class DenyHoldRequest(BaseModel):
    notes: Optional[str] = None


# ── GET /admin/hold-requests ──────────────────────────────

@router.get("/hold-requests", response_model=list[PendingHoldRequestItem])
async def list_pending_hold_requests(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.MANAGER, UserRole.ADMIN)),
):
    """
    Pending hold requests awaiting approval.
    Managers see only their own department's requests; admins see all.
    """
    result = await db.execute(
        select(HoldRequest).where(HoldRequest.status == HoldRequestStatus.PENDING)
    )
    requests = result.scalars().all()

    items = []
    for r in requests:
        ticket = r.ticket
        if (
            current_user.role == UserRole.MANAGER
            and ticket.category != (current_user.department.value if current_user.department else None)
        ):
            continue   # manager only sees their own department

        blocking = r.blocking_ticket
        items.append(PendingHoldRequestItem(
            id=r.id,
            ticket_id=ticket.id,
            ticket_number=getattr(ticket, "ticket_number", None),
            ticket_title=ticket.title,
            ticket_priority=ticket.priority.value,
            ticket_category=ticket.category,
            blocking_ticket_id=blocking.id if blocking else None,
            blocking_ticket_number=getattr(blocking, "ticket_number", None) if blocking else None,
            blocking_ticket_title=blocking.title if blocking else None,
            blocking_ticket_priority=blocking.priority.value if blocking else None,
            worker_id=r.requested_by_id,
            worker_name=r.requested_by.full_name,
            remark=r.remark,
            created_at=r.created_at,
        ))

    items.sort(key=lambda x: x.created_at, reverse=True)
    return items


# ── POST /admin/hold-requests/{id}/approve ────────────────

@router.post("/hold-requests/{request_id}/approve")
async def approve_hold(
    request_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.MANAGER, UserRole.ADMIN)),
):
    """
    Approve a pending hold request. Does NOT pause the ticket — it
    unlocks the worker's Hold button; the worker executes the hold
    themselves. The worker is emailed immediately.
    """
    from app.services.assignment_service import approve_hold_request

    result = await approve_hold_request(request_id, current_user, db)
    if not result["approved"]:
        raise HTTPException(400, result["reason"])

    await db.flush()

    ticket = result["ticket"]
    worker = result["worker"]

    # ── Notify the worker their Hold button is active ─────
    try:
        from app.services.email_service import send_hold_request_approved
        await send_hold_request_approved(
            ticket=ticket,
            worker_name=worker.full_name,
            approver_name=current_user.full_name,
            recipient_emails=[worker.email],
        )
    except Exception as e:
        print(f"   (hold approval email skipped: {e})")

    return {"message": f"Approved — {worker.full_name} can now put the ticket on hold"}


# ── POST /admin/hold-requests/{id}/deny ───────────────────

@router.post("/hold-requests/{request_id}/deny")
async def deny_hold(
    request_id: uuid.UUID,
    data: DenyHoldRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.MANAGER, UserRole.ADMIN)),
):
    """Deny a pending hold request. The ticket's SLA keeps running."""
    from app.services.assignment_service import deny_hold_request

    req = await db.get(HoldRequest, request_id)
    if not req:
        raise HTTPException(404, "Request not found")

    ticket = req.ticket
    worker = req.requested_by

    result = await deny_hold_request(request_id, current_user, data.notes, db)
    if not result["denied"]:
        raise HTTPException(400, result["reason"])

    await db.flush()

    # ── Notify the worker ─────────────────────────────────
    try:
        from app.services.email_service import send_hold_request_denied
        await send_hold_request_denied(
            ticket=ticket,
            worker_name=worker.full_name,
            decider_name=current_user.full_name,
            notes=data.notes,
            recipient_emails=[worker.email],
        )
    except Exception as e:
        print(f"   (hold denial email skipped: {e})")

    return {"message": "Denied — the ticket's SLA clock keeps running"}
