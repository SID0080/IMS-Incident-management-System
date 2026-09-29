import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, require_role
from app.database import get_db
from app.models.ticket import Ticket, TicketStatus, TicketPriority, Terminal, is_valid_transition
from app.models.user import User, UserRole
from app.models.audit_log import AuditLog, AuditAction
from app.schemas.ticket import (
    TicketCreate,
    TicketResponse,
    TicketStatusUpdate,
    TicketAssign,
)
from app.services.ticket_service import (
    compute_sla_deadline,
    create_ticket,
    get_dashboard_stats,
    get_ticket_by_id,
    list_tickets,
)

router = APIRouter()

UPLOAD_DIR = "uploads"
ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf"}
MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10MB


# ── Helper — build response with computed sla_deadline ────
def to_response(ticket: Ticket) -> TicketResponse:
    resp = TicketResponse.model_validate(ticket)
    resp.sla_deadline = compute_sla_deadline(ticket)
    return resp


# ── GET /tickets ──────────────────────────────────────────
@router.get("", response_model=list[TicketResponse])
async def get_tickets(
    limit: int = Query(default=100, le=500),
    status: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None),
    terminal: Optional[str] = Query(default=None, description="Filter by terminal: T2 or T3"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    tickets = await list_tickets(db, limit=limit, status=status, search=search, terminal=terminal)
    return [to_response(t) for t in tickets]


# ── POST /tickets ─────────────────────────────────────────
@router.post("", response_model=TicketResponse, status_code=status.HTTP_201_CREATED)
async def create_new_ticket(
    title: str = Form(..., min_length=3, max_length=255),
    description: str = Form(..., min_length=10),
    priority: TicketPriority = Form(...),
    location: Optional[str] = Form(None),
    terminal: Optional[Terminal] = Form(None),
    equipment_model: Optional[str] = Form(None),
    device_type: Optional[str] = Form(None),
    image: Optional[UploadFile] = File(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    image_path = None

    if image is not None and image.filename:
        ext = os.path.splitext(image.filename)[1].lower()
        if ext not in ALLOWED_IMAGE_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type '{ext}'. Allowed: jpg, jpeg, png, pdf.",
            )

        contents = await image.read()
        if len(contents) > MAX_IMAGE_SIZE:
            raise HTTPException(status_code=400, detail="File too large (max 10MB).")

        os.makedirs(UPLOAD_DIR, exist_ok=True)
        filename = f"{uuid.uuid4()}{ext}"
        filepath = f"{UPLOAD_DIR}/{filename}"

        # ── FIX: Synchronous context with an absolute hardware flush ──
        # This blocks execution just long enough to guarantee the OS writes the bytes,
        # ensuring the downstream ML pipeline doesn't read a 0-byte file descriptor.
        with open(filepath, "wb") as f:
            f.write(contents)
            f.flush()
            os.fsync(f.fileno())

        image_path = filepath

    data = TicketCreate(
        title=title,
        description=description,
        priority=priority,
        location=location,
        terminal=terminal,
        equipment_model=equipment_model,
        device_type=device_type,
    )
    ticket = await create_ticket(data, reporter_id=current_user.id, db=db, image_path=image_path)
    return to_response(ticket)


# ── GET /tickets/{ticket_id} ──────────────────────────────
@router.get("/{ticket_id}", response_model=TicketResponse)
async def get_ticket(
    ticket_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ticket = await get_ticket_by_id(ticket_id, db)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return to_response(ticket)


# ── PATCH /tickets/{ticket_id}/status ─────────────────────
@router.patch("/{ticket_id}/status", response_model=TicketResponse)
async def update_ticket_status(
    ticket_id: uuid.UUID,
    data: TicketStatusUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.MANAGER, UserRole.ADMIN)),
):
    ticket = await get_ticket_by_id(ticket_id, db)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    if not is_valid_transition(ticket.status, data.status):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid transition: {ticket.status} → {data.status}",
        )

    prev_status   = ticket.status
    ticket.status = data.status

    now = datetime.now(timezone.utc)
    if data.status == TicketStatus.IN_PROGRESS and not ticket.assigned_at:
        ticket.assigned_at = now
        ticket.sla_deadline = compute_sla_deadline(ticket)
    elif data.status == TicketStatus.RESOLVED:
        ticket.resolved_at = now
    elif data.status == TicketStatus.CLOSED:
        ticket.closed_at = now
    elif data.status == TicketStatus.OPEN:
        # Reopen — reset the SLA clock so the next assignment starts fresh.
        ticket.assigned_at = None
        ticket.sla_deadline = None
        ticket.resolved_at = None
        ticket.closed_at = None

    log = AuditLog(
        ticket_id=ticket.id,
        user_id=current_user.id,
        action=AuditAction.REOPENED if data.status == TicketStatus.OPEN else AuditAction.STATUS_CHANGE,
        previous_status=prev_status.value,
        new_status=data.status.value,
        notes=data.notes,
    )
    db.add(log)

    # When a manager/admin resolves a ticket, release every hold that was
    # blocked by it: EXECUTED hold requests pointing at this ticket get
    # their held tickets resumed with the exact SLA time saved at pause.
    if data.status == TicketStatus.RESOLVED:
        from app.services.assignment_service import resume_held_tickets_for
        await resume_held_tickets_for(ticket, db)

    await db.flush()
    await db.refresh(ticket)
    return to_response(ticket)


# ── PATCH /tickets/{ticket_id}/assign ─────────────────────
@router.patch("/{ticket_id}/assign", response_model=TicketResponse)
async def assign_ticket(
    ticket_id: uuid.UUID,
    data: TicketAssign,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.MANAGER, UserRole.ADMIN)),
):
    """
    Manager/admin manually (re)assigns a ticket to ONE worker.

    This is a fresh, single-owner assignment — it deliberately CLEARS:
      - additional_assignee_ids (any previously delegated/approved helpers)
      - any PENDING help requests on this ticket (auto-denied, since the
        old lead worker they were attached to is being replaced)
    """
    ticket = await get_ticket_by_id(ticket_id, db)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    prev_status         = ticket.status
    ticket.assignee_id  = data.assignee_id
    ticket.status       = TicketStatus.IN_PROGRESS
    ticket.assigned_at  = datetime.now(timezone.utc)
    ticket.sla_deadline = compute_sla_deadline(ticket)

    # Fresh assignment always starts live — clear any leftover hold state
    ticket.is_paused             = False
    ticket.paused_at             = None
    ticket.remaining_sla_seconds = None

    # Clear stale helpers from any previous lead's delegation
    had_helpers = bool(ticket.additional_assignee_list)
    ticket.additional_assignee_ids = None

    # Auto-deny any pending help requests tied to the old lead
    from app.models.help_request import HelpRequest, HelpRequestStatus
    pending_res = await db.execute(
        select(HelpRequest).where(
            and_(
                HelpRequest.ticket_id == ticket_id,
                HelpRequest.status == HelpRequestStatus.PENDING,
            )
        )
    )
    pending_requests = pending_res.scalars().all()
    for req in pending_requests:
        req.status         = HelpRequestStatus.DENIED
        req.decided_by_id  = current_user.id
        req.decided_at     = datetime.now(timezone.utc)
        req.decision_notes = "Auto-denied — ticket was reassigned to a different worker"

    # Auto-deny any open hold requests too — they belonged to the old
    # worker; a freshly reassigned ticket starts with a clean live SLA.
    from app.services.assignment_service import cancel_open_holds_for_ticket
    cancelled_holds = await cancel_open_holds_for_ticket(
        ticket, current_user, "ticket was reassigned to a different worker", db
    )

    log = AuditLog(
        ticket_id=ticket.id,
        user_id=current_user.id,
        action=AuditAction.ASSIGNED,
        previous_status=prev_status.value,
        new_status=TicketStatus.IN_PROGRESS.value,
        notes=(
            "Manually reassigned by manager"
            + (" — cleared previous helpers" if had_helpers else "")
            + (f" — auto-denied {len(pending_requests)} pending help request(s)" if pending_requests else "")
            + (f" — cancelled {cancelled_holds} open hold request(s)" if cancelled_holds else "")
        ),
    )
    db.add(log)
    await db.flush()
    await db.refresh(ticket)

    # Email the newly assigned worker
    try:
        from sqlalchemy import select as sa_select
        from app.services.email_service import send_assignment_email
        worker_res = await db.execute(sa_select(User).where(User.id == data.assignee_id))
        worker = worker_res.scalar_one_or_none()
        if worker:
            await send_assignment_email(
                worker_email=worker.email,
                worker_name=worker.full_name,
                ticket=ticket,
            )
    except Exception as e:
        print(f"   (assignment email skipped: {e})")

    return to_response(ticket)


# ── PATCH /tickets/{ticket_id}/category ───────────────────
class TicketCategoryUpdate(BaseModel):
    category: str   # must match a valid Department enum value
    model_config = {"json_schema_extra": {"example": {"category": "it"}}}


@router.patch("/{ticket_id}/category", response_model=TicketResponse)
async def set_ticket_category(
    ticket_id: uuid.UUID,
    data: TicketCategoryUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.MANAGER, UserRole.ADMIN)),
):
    """
    Manually set the department category on an uncategorized ticket,
    then immediately re-trigger auto-routing.
    """
    from app.services.assignment_service import route_ticket

    ticket = await get_ticket_by_id(ticket_id, db)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    old_category     = ticket.category
    ticket.category  = data.category.lower().strip()

    # Re-trigger routing — will auto-assign if Low/Medium
    routing = await route_ticket(ticket, db)

    log = AuditLog(
        ticket_id=ticket.id,
        user_id=current_user.id,
        action=AuditAction.CATEGORY_SET,
        notes=(
            f"Category manually set by {current_user.full_name}: "
            f"'{old_category or 'none'}' → '{ticket.category}'. "
            f"Routing result: {routing.get('reason', routing.get('routed'))}"
        ),
    )
    db.add(log)
    await db.flush()
    await db.refresh(ticket)

    print(
        f"✅  Ticket {ticket_id} manually categorized as '{ticket.category}' "
        f"by {current_user.full_name} | routing: {routing}"
    )

    return to_response(ticket)


# ── Called by main.py /dashboard/stats ────────────────────
async def get_stats(db: AsyncSession) -> dict:
    return await get_dashboard_stats(db)