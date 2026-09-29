"""
Worker self-service router — with priority queue resume logic.

GET  /worker/me                               → profile + active ticket + queue + breach history
PATCH /worker/me/tickets/{ticket_id}/status   → resolve/close own ticket; resumes paused queue
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from pydantic import BaseModel

from app.database import get_db
from app.models.ticket import Ticket, TicketStatus, TicketPriority
from app.models.user import User, UserRole, Department
from app.models.audit_log import AuditLog, AuditAction
from app.core.deps import get_current_user

router = APIRouter(tags=["Worker"])

_SLA_TOTAL = {
    TicketPriority.CRITICAL: 0.5  * 3600,
    TicketPriority.HIGH:     2    * 3600,
    TicketPriority.MEDIUM:   8    * 3600,
    TicketPriority.LOW:      24   * 3600,
}

BREACH_POINTS = {TicketPriority.CRITICAL: 20, TicketPriority.HIGH: 10,
                 TicketPriority.MEDIUM: 5, TicketPriority.LOW: 2}

WORKER_ALLOWED_TRANSITIONS = {
    TicketStatus.IN_PROGRESS:     TicketStatus.RESOLVED,
    TicketStatus.CRITICAL_REVIEW: TicketStatus.RESOLVED,
    TicketStatus.RESOLVED:        TicketStatus.CLOSED,
}


# ── Schemas ───────────────────────────────────────────────

class WorkerTicketItem(BaseModel):
    id:                     uuid.UUID
    ticket_number:          Optional[str] = None
    title:                  str
    priority:               TicketPriority
    status:                 TicketStatus
    category:               Optional[str]
    location:               Optional[str]
    sla_deadline:           Optional[datetime]
    assigned_at:            Optional[datetime]
    resolved_at:            Optional[datetime]
    time_remaining_seconds: Optional[int]
    risk_level:             str
    is_paused:              bool
    remaining_sla_seconds:  Optional[int]
    next_allowed_status:    Optional[str]
    is_additional_assignee: bool = False   # True if worker is helper, not primary
    # ── Hold workflow ──
    # None | PENDING | APPROVED | EXECUTED — drives which button the UI shows:
    #   None (and a higher-priority ticket exists) → "Request Hold"
    #   PENDING  → disabled "Awaiting approval"
    #   APPROVED → active "Hold" button
    #   EXECUTED → ticket is paused (shows in On Hold tab)
    hold_status:     Optional[str]       = None
    hold_request_id: Optional[uuid.UUID] = None
    created_at:             datetime

    model_config = {"from_attributes": True}


class BreachHistoryItem(BaseModel):
    ticket_id:       uuid.UUID
    ticket_title:    str
    priority:        TicketPriority
    escalated_at:    datetime
    points_deducted: int

    model_config = {"from_attributes": True}


class WorkerProfileResponse(BaseModel):
    id:                     uuid.UUID
    full_name:              str
    email:                  str
    department:             Optional[Department]
    penalty_points:         int
    breach_count:           int
    is_blacklisted:         bool
    active_ticket_count:    int
    queued_ticket_count:    int
    points_until_blacklist: int

    model_config = {"from_attributes": True}


class WorkerDashboardResponse(BaseModel):
    profile:          WorkerProfileResponse
    active_tickets:   list[WorkerTicketItem]   # non-paused
    queued_tickets:   list[WorkerTicketItem]   # paused, waiting in queue
    resolved_tickets: list[WorkerTicketItem]
    breach_history:   list[BreachHistoryItem]


class WorkerStatusUpdate(BaseModel):
    status: TicketStatus


class WorkerDelegateRequest(BaseModel):
    helper_id: uuid.UUID


class DelegatableWorkerItem(BaseModel):
    id:        uuid.UUID
    full_name: str
    is_busy:   bool   # has an active (non-paused) ticket right now

    model_config = {"from_attributes": True}


# ── Helpers ───────────────────────────────────────────────

def _risk(ticket: Ticket, now: datetime) -> tuple[Optional[int], str]:
    if ticket.is_paused:
        return ticket.remaining_sla_seconds, "paused"
    if not ticket.sla_deadline:
        return None, "safe"
    remaining = int((ticket.sla_deadline - now).total_seconds())
    total     = _SLA_TOTAL.get(ticket.priority, 86400)
    pct       = remaining / total if total else 0
    if remaining <= 0:  return remaining, "breached"
    if pct < 0.25:      return remaining, "danger"
    if pct < 0.50:      return remaining, "warning"
    return remaining, "safe"


def _build_item(ticket: Ticket, now: datetime, is_additional: bool = False) -> WorkerTicketItem:
    remaining, risk = _risk(ticket, now)
    next_s = WORKER_ALLOWED_TRANSITIONS.get(ticket.status)
    return WorkerTicketItem(
        id=ticket.id, ticket_number=getattr(ticket, "ticket_number", None),
        title=ticket.title, priority=ticket.priority,
        status=ticket.status, category=ticket.category, location=ticket.location,
        sla_deadline=ticket.sla_deadline, assigned_at=ticket.assigned_at,
        resolved_at=ticket.resolved_at, time_remaining_seconds=remaining,
        risk_level=risk, is_paused=ticket.is_paused,
        remaining_sla_seconds=ticket.remaining_sla_seconds,
        next_allowed_status=next_s.value if next_s else None,
        is_additional_assignee=is_additional,
        created_at=ticket.created_at,
    )


# ── GET /worker/me ────────────────────────────────────────

@router.get("/me", response_model=WorkerDashboardResponse)
async def worker_dashboard(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role not in (UserRole.WORKER, UserRole.MANAGER, UserRole.ADMIN):
        raise HTTPException(403, "Workers and managers only")

    now = datetime.now(timezone.utc)
    uid_str = str(current_user.id)

    # Primary assignee tickets (active + queued)
    primary_res = await db.execute(
        select(Ticket).where(
            Ticket.assignee_id == current_user.id,
            Ticket.status.in_([TicketStatus.IN_PROGRESS, TicketStatus.CRITICAL_REVIEW]),
        ).order_by(Ticket.sla_deadline.asc().nullslast())
    )
    primary_tickets = primary_res.scalars().all()

    # Additional assignee tickets (worker is helper)
    additional_res = await db.execute(
        select(Ticket).where(
            Ticket.additional_assignee_ids.like(f'%{uid_str}%'),
            Ticket.status.in_([TicketStatus.IN_PROGRESS, TicketStatus.CRITICAL_REVIEW]),
        )
    )
    additional_tickets = [
        t for t in additional_res.scalars().all()
        if t.assignee_id != current_user.id  # deduplicate
    ]

    active_items  = [_build_item(t, now, False) for t in primary_tickets if not t.is_paused]
    active_items += [_build_item(t, now, True)  for t in additional_tickets if not t.is_paused]
    queued_items  = [_build_item(t, now, False) for t in primary_tickets if t.is_paused]

    # ── Stamp hold-request status onto each item ──────────
    # Drives the Request Hold / Awaiting approval / Hold buttons in the UI.
    from app.models.hold_request import HoldRequest, HoldRequestStatus
    hold_res = await db.execute(
        select(HoldRequest).where(
            HoldRequest.requested_by_id == current_user.id,
            HoldRequest.status.in_([
                HoldRequestStatus.PENDING,
                HoldRequestStatus.APPROVED,
                HoldRequestStatus.EXECUTED,
            ]),
        )
    )
    holds_by_ticket = {h.ticket_id: h for h in hold_res.scalars().all()}
    for item in active_items + queued_items:
        h = holds_by_ticket.get(item.id)
        if h:
            item.hold_status     = h.status.value
            item.hold_request_id = h.id

    # Resolved/closed (last 10)
    done_res = await db.execute(
        select(Ticket).where(
            Ticket.assignee_id == current_user.id,
            Ticket.status.in_([TicketStatus.RESOLVED, TicketStatus.CLOSED]),
        ).order_by(Ticket.resolved_at.desc().nullslast()).limit(10)
    )
    done_tickets = done_res.scalars().all()

    # Breach history
    breach_res = await db.execute(
        select(Ticket).where(
            Ticket.assignee_id == current_user.id,
            Ticket.escalated_at.is_not(None),
        ).order_by(Ticket.escalated_at.desc()).limit(20)
    )
    breach_history = [
        BreachHistoryItem(
            ticket_id=t.id, ticket_title=t.title,
            priority=t.priority, escalated_at=t.escalated_at,
            points_deducted=BREACH_POINTS.get(t.priority, 5),
        ) for t in breach_res.scalars().all()
    ]

    from app.core.accountability import BLACKLIST_THRESHOLD
    pts_until = max(0, BLACKLIST_THRESHOLD - current_user.penalty_points)

    profile = WorkerProfileResponse(
        id=current_user.id, full_name=current_user.full_name,
        email=current_user.email, department=current_user.department,
        penalty_points=current_user.penalty_points,
        breach_count=current_user.breach_count,
        is_blacklisted=current_user.is_blacklisted,
        active_ticket_count=len(active_items),
        queued_ticket_count=len(queued_items),
        points_until_blacklist=pts_until,
    )

    return WorkerDashboardResponse(
        profile=profile,
        active_tickets=active_items,
        queued_tickets=queued_items,
        resolved_tickets=[_build_item(t, now) for t in done_tickets],
        breach_history=breach_history,
    )


# ── PATCH /worker/me/tickets/{ticket_id}/status ───────────

@router.patch("/me/tickets/{ticket_id}/status", response_model=WorkerTicketItem)
async def update_my_ticket_status(
    ticket_id: uuid.UUID,
    data: WorkerStatusUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Worker updates status of their own ticket.
    On resolve: automatically resumes the highest-priority paused ticket in their queue.
    """
    if current_user.role not in (UserRole.WORKER, UserRole.MANAGER, UserRole.ADMIN):
        raise HTTPException(403, "Workers only")

    ticket = await db.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")

    uid_str = str(current_user.id)
    is_primary    = (ticket.assignee_id == current_user.id)
    is_additional = uid_str in ticket.additional_assignee_list

    if not is_primary and not is_additional:
        raise HTTPException(403, "This ticket is not assigned to you")

    allowed_next = WORKER_ALLOWED_TRANSITIONS.get(ticket.status)
    if not allowed_next or data.status != allowed_next:
        raise HTTPException(
            400,
            f"Cannot move from '{ticket.status.value}' to '{data.status.value}'. "
            f"Allowed: '{allowed_next.value if allowed_next else 'none'}'",
        )

    prev_status   = ticket.status
    ticket.status = data.status
    now           = datetime.now(timezone.utc)

    if data.status == TicketStatus.RESOLVED:
        ticket.resolved_at = now
    elif data.status == TicketStatus.CLOSED:
        ticket.closed_at = now

    db.add(AuditLog(
        ticket_id=ticket.id,
        user_id=current_user.id,
        action=AuditAction.STATUS_CHANGE,
        previous_status=prev_status.value,
        new_status=data.status.value,
        notes=f"Worker self-update by {current_user.full_name}",
    ))

    # ── Resume tickets that were held BECAUSE OF this ticket ──
    # Under the approval-gated hold model, resumption is tied to the
    # specific blocking ticket: every EXECUTED hold pointing at this
    # ticket gets its held ticket un-paused (SLA restarts with the
    # exact remaining time saved at pause).
    if data.status == TicketStatus.RESOLVED:
        from app.services.assignment_service import resume_held_tickets_for

        resumed = await resume_held_tickets_for(ticket, db)
        for r in resumed:
            print(f"▶️  Hold released — resumed '{r.title}' (#{getattr(r, 'ticket_number', '')})")

        # ── Completion confirmation email ──────────────────
        # FIX: was using asyncio.create_task() + new DB session which caused a
        # race: bg_db.get(T, ticket_id) ran before commit and returned None.
        # Now sends directly using in-memory ticket + current db session.
        try:
            from app.services.email_service import (
                send_completion_email,
                get_manager_emails_for_dept,
                get_admin_emails,
            )
            mgr_emails   = await get_manager_emails_for_dept(ticket.category or "", db) if ticket.category else []
            admin_emails = await get_admin_emails(db)
            recipients   = list(set(mgr_emails + admin_emails))
            if recipients:
                await send_completion_email(
                    ticket=ticket,
                    resolved_by_name=current_user.full_name,
                    recipient_emails=recipients,
                )
        except Exception as e:
            print(f"   (completion email skipped: {e})")

    await db.flush()
    await db.refresh(ticket)
    return _build_item(ticket, now)

# ── GET /worker/me/tickets/{ticket_id}/delegatable-workers ─

@router.get("/me/tickets/{ticket_id}/delegatable-workers", response_model=list[DelegatableWorkerItem])
async def list_delegatable_workers(
    ticket_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Lists workers in the SAME department as this ticket who could be
    delegated as a helper. Only the LEAD assignee can call this.

    Excludes: the lead themselves, blacklisted workers, and anyone
    already delegated on this ticket.
    """
    ticket = await db.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")

    if ticket.assignee_id != current_user.id:
        raise HTTPException(403, "Only the lead worker on this ticket can view delegation options")

    if ticket.priority not in (TicketPriority.HIGH, TicketPriority.CRITICAL):
        raise HTTPException(400, "Delegation is only available for HIGH and CRITICAL tickets")

    already_delegated = set(ticket.additional_assignee_list)

    workers_res = await db.execute(
        select(User).where(
            User.role == UserRole.WORKER,
            User.department == ticket.category,
            User.is_active == True,           # noqa: E712
            User.is_blacklisted == False,      # noqa: E712
            User.id != current_user.id,
        )
    )
    workers = [w for w in workers_res.scalars().all() if str(w.id) not in already_delegated]

    items = []
    for w in workers:
        from app.services.assignment_service import get_worker_active_ticket
        active = await get_worker_active_ticket(w.id, db)
        items.append(DelegatableWorkerItem(
            id=w.id, full_name=w.full_name, is_busy=active is not None
        ))
    return items


# ── POST /worker/me/tickets/{ticket_id}/request-help ──────

@router.post("/me/tickets/{ticket_id}/request-help", response_model=WorkerTicketItem)
async def request_help(
    ticket_id: uuid.UUID,
    data: WorkerDelegateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Lead worker REQUESTS a helper — does NOT attach them yet.

    Rules:
      - Only the LEAD assignee (ticket.assignee_id) can request help
      - Only HIGH/CRITICAL tickets support this
      - Multiple helpers can be requested (each is a separate approval)
      - Lead continues working alone while the request is pending —
        nothing changes on the ticket until a manager/admin approves
      - Helper must be in the same department, active, not blacklisted

    A manager or admin in the department (or any admin) must approve
    via POST /admin/help-requests/{id}/approve before the helper is
    actually added — see assignment_service.approve_help_request().

    Notification: department manager(s) + admin are emailed immediately
    asking them to approve or deny.
    """
    ticket = await db.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")

    if ticket.assignee_id != current_user.id:
        raise HTTPException(403, "Only the lead worker on this ticket can request help")

    if ticket.priority not in (TicketPriority.HIGH, TicketPriority.CRITICAL):
        raise HTTPException(400, "Help requests are only available for HIGH and CRITICAL tickets")

    helper = await db.get(User, data.helper_id)
    if not helper or helper.role != UserRole.WORKER:
        raise HTTPException(404, "Helper not found")
    if helper.department != ticket.category:
        raise HTTPException(400, "Helper must be in the same department as this ticket")

    from app.services.assignment_service import create_help_request
    result = await create_help_request(ticket, current_user, helper, db)

    if not result["requested"]:
        raise HTTPException(400, result["reason"])

    await db.flush()

    # ── Notify department manager(s) + admin ──────────────
    # FIX: was using asyncio.create_task + new DB session (same race
    # condition as the critical alert fix — bg_db.get() can run before
    # the session commits and return None). Now sends directly using
    # the in-memory ticket object and current db session.
    try:
        from app.services.email_service import send_help_request_alert, get_manager_emails_for_dept, get_admin_emails
        mgr_emails   = await get_manager_emails_for_dept(ticket.category or "", db) if ticket.category else []
        admin_emails = await get_admin_emails(db)
        recipients   = list(set(mgr_emails + admin_emails))
        if recipients:
            await send_help_request_alert(
                ticket=ticket,
                lead_name=current_user.full_name,
                helper_name=helper.full_name,
                recipient_emails=recipients,
            )
    except Exception as e:
        print(f"   (help request email skipped: {e})")

    now = datetime.now(timezone.utc)
    return _build_item(ticket, now)


# ── GET /worker/me/tickets/{ticket_id}/help-requests ──────

class HelpRequestItem(BaseModel):
    id:           uuid.UUID
    helper_id:    uuid.UUID
    helper_name:  str
    status:       str
    created_at:   datetime
    decided_at:   Optional[datetime] = None
    decision_notes: Optional[str] = None

    model_config = {"from_attributes": True}


@router.get("/me/tickets/{ticket_id}/help-requests", response_model=list[HelpRequestItem])
async def my_help_requests_for_ticket(
    ticket_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Lead worker checks the status of their help requests on this ticket."""
    from app.models.help_request import HelpRequest

    ticket = await db.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    if ticket.assignee_id != current_user.id:
        raise HTTPException(403, "Only the lead worker can view this ticket's help requests")

    result = await db.execute(
        select(HelpRequest).where(HelpRequest.ticket_id == ticket_id).order_by(HelpRequest.created_at.desc())
    )
    reqs = result.scalars().all()
    return [
        HelpRequestItem(
            id=r.id, helper_id=r.helper_id, helper_name=r.helper.full_name,
            status=r.status.value, created_at=r.created_at,
            decided_at=r.decided_at, decision_notes=r.decision_notes,
        ) for r in reqs
    ]


# ═══════════════════════════════════════════════════════════
#  HOLD WORKFLOW (worker side)
#  Request Hold → manager/admin approves → worker clicks Hold
# ═══════════════════════════════════════════════════════════

class HoldRequestCreate(BaseModel):
    blocking_ticket_id: uuid.UUID          # the higher-priority ticket that needs full focus
    remark:             str                # why the manager should allow this hold


class HoldRequestItem(BaseModel):
    id:                     uuid.UUID
    ticket_id:              uuid.UUID
    blocking_ticket_id:     Optional[uuid.UUID] = None
    blocking_ticket_title:  Optional[str] = None
    blocking_ticket_number: Optional[str] = None
    remark:                 str
    status:                 str
    created_at:             datetime
    decided_at:             Optional[datetime] = None
    decision_notes:         Optional[str] = None

    model_config = {"from_attributes": True}


# ── POST /worker/me/tickets/{ticket_id}/request-hold ──────

@router.post("/me/tickets/{ticket_id}/request-hold", response_model=WorkerTicketItem)
async def request_hold(
    ticket_id: uuid.UUID,
    data: HoldRequestCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Worker asks permission to PAUSE this ticket because a strictly
    higher-priority ticket (blocking_ticket_id, also assigned to them)
    needs their full attention.

    Nothing pauses here — the SLA clock keeps running until a
    manager/admin approves AND the worker clicks Hold (execute-hold).
    The remark is mandatory: it's the worker's justification and lands
    in the manager's Hold Requests panel + email.
    """
    ticket = await db.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")

    blocking = await db.get(Ticket, data.blocking_ticket_id)
    if not blocking:
        raise HTTPException(404, "Blocking ticket not found")

    from app.services.assignment_service import create_hold_request
    result = await create_hold_request(ticket, current_user, blocking, data.remark, db)
    if not result["requested"]:
        raise HTTPException(400, result["reason"])

    await db.flush()

    # ── Notify department manager(s) + admin ──────────────
    try:
        from app.services.email_service import (
            send_hold_request_alert,
            get_manager_emails_for_dept,
            get_admin_emails,
        )
        mgr_emails   = await get_manager_emails_for_dept(ticket.category or "", db) if ticket.category else []
        admin_emails = await get_admin_emails(db)
        recipients   = list(set(mgr_emails + admin_emails))
        if recipients:
            await send_hold_request_alert(
                ticket=ticket,
                blocking_ticket=blocking,
                worker_name=current_user.full_name,
                remark=data.remark,
                recipient_emails=recipients,
            )
    except Exception as e:
        print(f"   (hold request email skipped: {e})")

    now = datetime.now(timezone.utc)
    item = _build_item(ticket, now)
    item.hold_status     = "PENDING"
    item.hold_request_id = result["request_id"]
    return item


# ── POST /worker/me/tickets/{ticket_id}/execute-hold ──────

@router.post("/me/tickets/{ticket_id}/execute-hold", response_model=WorkerTicketItem)
async def execute_hold_endpoint(
    ticket_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Worker clicks the HOLD button (only active after manager/admin
    approval). THIS is the moment the ticket actually pauses:
    remaining SLA time is saved, the deadline is cleared, and the
    SLA engine starts skipping the ticket.

    When the blocking ticket is resolved, the hold auto-releases and
    the SLA clock restarts from the saved remaining time.
    """
    ticket = await db.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")

    from app.services.assignment_service import execute_hold
    result = await execute_hold(ticket, current_user, db)
    if not result["held"]:
        raise HTTPException(400, result["reason"])

    await db.flush()
    await db.refresh(ticket)

    now = datetime.now(timezone.utc)
    item = _build_item(ticket, now)
    item.hold_status     = "EXECUTED"
    item.hold_request_id = result["request_id"]
    return item


# ── GET /worker/me/tickets/{ticket_id}/hold-requests ──────

@router.get("/me/tickets/{ticket_id}/hold-requests", response_model=list[HoldRequestItem])
async def my_hold_requests_for_ticket(
    ticket_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Worker checks the status of their hold requests on this ticket."""
    from app.models.hold_request import HoldRequest

    ticket = await db.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")

    uid_str = str(current_user.id)
    if ticket.assignee_id != current_user.id and uid_str not in ticket.additional_assignee_list:
        raise HTTPException(403, "This ticket is not assigned to you")

    result = await db.execute(
        select(HoldRequest)
        .where(
            HoldRequest.ticket_id == ticket_id,
            HoldRequest.requested_by_id == current_user.id,
        )
        .order_by(HoldRequest.created_at.desc())
    )
    reqs = result.scalars().all()
    return [
        HoldRequestItem(
            id=r.id,
            ticket_id=r.ticket_id,
            blocking_ticket_id=r.blocking_ticket_id,
            blocking_ticket_title=r.blocking_ticket.title if r.blocking_ticket else None,
            blocking_ticket_number=getattr(r.blocking_ticket, "ticket_number", None) if r.blocking_ticket else None,
            remark=r.remark,
            status=r.status.value,
            created_at=r.created_at,
            decided_at=r.decided_at,
            decision_notes=r.decision_notes,
        ) for r in reqs
    ]
