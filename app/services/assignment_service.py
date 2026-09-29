"""
Worker routing & assignment service — v3

── What changed from v2 ─────────────────────────────────────
1. NO MORE AUTO-PAUSE. Assigning a higher-priority ticket to a busy
   worker no longer freezes their current work, and new tickets never
   start paused/queued. EVERY assignment starts IN_PROGRESS with a
   live SLA clock. A worker can hold multiple active tickets — all
   clocks ticking.

2. ADMIN-APPROVED HOLDS replace the automatic queue:
     worker Request Hold (with remark) → manager/admin approves →
     worker clicks Hold (ticket pauses, SLA frozen) → blocking ticket
     resolved → held ticket auto-resumes with its saved SLA time.
   See create_hold_request / approve_hold_request / deny_hold_request /
   execute_hold / resume_held_tickets_for.

3. L1/L2/L3 ROUTING. Tickets route directly to engineers by tier:
     LOW/MEDIUM → L1,  HIGH → L2,  CRITICAL → L3
   with fallback to the other levels (then unleveled workers) if
   nobody at the preferred level is available.

Kept from v2: MAX_WORKER_LOAD cap, combined-load computation (primary +
additional assignee), approval-gated help requests, direct in-session
emails (no asyncio.create_task).
"""
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from app.config import settings
from app.models.user import User, UserRole, WorkerLevel
from app.models.ticket import Ticket, TicketStatus, TicketPriority
from app.models.audit_log import AuditLog, AuditAction


# ── Worker load cap ───────────────────────────────────────
# Maximum number of in-progress tickets (active + held) a single worker
# may hold at once. A worker at or above this is skipped for new assignment.
MAX_WORKER_LOAD = 3


# ── Priority ordering ─────────────────────────────────────
PRIORITY_ORDER: dict[TicketPriority, int] = {
    TicketPriority.LOW:      1,
    TicketPriority.MEDIUM:   2,
    TicketPriority.HIGH:     3,
    TicketPriority.CRITICAL: 4,
}

_SLA_HOURS: dict[TicketPriority, float] = {
    TicketPriority.LOW:      settings.SLA_LOW_RESPONSE_HRS,
    TicketPriority.MEDIUM:   settings.SLA_MEDIUM_RESPONSE_HRS,
    TicketPriority.HIGH:     settings.SLA_HIGH_RESPONSE_HRS,
    TicketPriority.CRITICAL: settings.SLA_CRITICAL_RESPONSE_HRS,
}

# ── L1/L2/L3 routing preference ───────────────────────────
# For each priority, the order in which engineer levels are tried.
# First level with an available worker wins; if none of the three
# levels has anyone, a final level-agnostic pass runs (catches
# unleveled workers so a half-migrated DB still routes).
LEVEL_PREFERENCE: dict[TicketPriority, list[WorkerLevel]] = {
    TicketPriority.LOW:      [WorkerLevel.L1, WorkerLevel.L2, WorkerLevel.L3],
    TicketPriority.MEDIUM:   [WorkerLevel.L1, WorkerLevel.L2, WorkerLevel.L3],
    TicketPriority.HIGH:     [WorkerLevel.L2, WorkerLevel.L3, WorkerLevel.L1],
    TicketPriority.CRITICAL: [WorkerLevel.L3, WorkerLevel.L2, WorkerLevel.L1],
}


def _deadline_from_now(priority: TicketPriority, now: datetime) -> datetime:
    """Compute absolute SLA deadline from current time."""
    return now + timedelta(hours=_SLA_HOURS.get(priority, 24.0))


# ── Worker availability ───────────────────────────────────

async def find_available_workers(
    department: str,
    db: AsyncSession,
    limit: Optional[int] = None,
    ignore_load_cap: bool = False,
    level: Optional[WorkerLevel] = None,
) -> list[User]:
    """
    Find eligible workers ordered by COMBINED load (primary + additional
    assignee), optionally filtered to one engineer level (L1/L2/L3).

    Combined load counts BOTH roles on every active ticket:
    assignee_id AND additional_assignee_ids — otherwise a worker helping
    on a CRITICAL would look free and keep getting picked.
    """
    # Step 1: Fetch all eligible workers in the department (± level)
    conditions = [
        User.role == UserRole.WORKER,
        User.department == department,
        User.is_active == True,           # noqa: E712
        User.is_blacklisted == False,     # noqa: E712
    ]
    if level is not None:
        conditions.append(User.level == level)

    result = await db.execute(select(User).where(*conditions))
    workers = list(result.scalars().all())
    if not workers:
        return []

    # Step 2: Fetch ALL active in-progress tickets system-wide
    tickets_result = await db.execute(
        select(Ticket).where(Ticket.status == TicketStatus.IN_PROGRESS)
    )
    all_active = list(tickets_result.scalars().all())

    # Step 3: Compute TRUE combined load per worker
    combined_load: dict[str, int] = {}
    for t in all_active:
        if t.assignee_id:
            wid = str(t.assignee_id)
            combined_load[wid] = combined_load.get(wid, 0) + 1
        for extra_id in t.additional_assignee_list:
            combined_load[extra_id] = combined_load.get(extra_id, 0) + 1

    # Step 4: Apply load cap (skipped for CRITICAL — urgency > balance)
    if not ignore_load_cap:
        workers = [
            w for w in workers
            if combined_load.get(str(w.id), 0) < MAX_WORKER_LOAD
        ]

    # Step 5: Sort by combined load — least busy first
    workers.sort(key=lambda w: combined_load.get(str(w.id), 0))

    # Step 6: Apply limit AFTER sorting so we always pick the least-busy N
    if limit is not None:
        workers = workers[:limit]

    return workers


async def get_worker_active_ticket(worker_id: uuid.UUID, db: AsyncSession) -> Optional[Ticket]:
    """
    Return the highest-priority NON-PAUSED IN_PROGRESS ticket for this
    worker (primary or additional assignee), or None.

    Note: under the no-auto-pause model this no longer gates assignment —
    it's used for UI signals like the delegatable-workers "is_busy" flag.
    """
    uid_str = str(worker_id)

    primary_res = await db.execute(
        select(Ticket).where(
            Ticket.assignee_id == worker_id,
            Ticket.status    == TicketStatus.IN_PROGRESS,
            Ticket.is_paused == False,        # noqa: E712
        )
    )
    primary_tickets = list(primary_res.scalars().all())

    additional_res = await db.execute(
        select(Ticket).where(
            Ticket.additional_assignee_ids.like(f'%{uid_str}%'),
            Ticket.status    == TicketStatus.IN_PROGRESS,
            Ticket.is_paused == False,        # noqa: E712
        )
    )
    additional_tickets = [
        t for t in additional_res.scalars().all()
        if t.assignee_id != worker_id
    ]

    all_active = primary_tickets + additional_tickets
    if not all_active:
        return None

    return max(all_active, key=lambda t: PRIORITY_ORDER[t.priority])


def _worker_is_on_ticket(ticket: Ticket, worker_id: uuid.UUID) -> bool:
    """True if the worker is the lead OR an approved additional assignee."""
    return (
        ticket.assignee_id == worker_id
        or str(worker_id) in ticket.additional_assignee_list
    )


# ── Pause (only via approved holds now) ───────────────────

async def pause_ticket(ticket: Ticket, db: AsyncSession) -> None:
    """
    Pause a running ticket: save remaining SLA time, clear deadline.
    ONLY called from execute_hold() — after a manager/admin approved
    the worker's hold request. Never automatic.
    """
    now = datetime.now(timezone.utc)
    if ticket.sla_deadline and not ticket.is_paused:
        remaining = int((ticket.sla_deadline - now).total_seconds())
        ticket.remaining_sla_seconds = max(0, remaining)
    elif not ticket.remaining_sla_seconds:
        # Fallback: save full window
        ticket.remaining_sla_seconds = int(_SLA_HOURS.get(ticket.priority, 24) * 3600)

    ticket.is_paused    = True
    ticket.paused_at    = now
    ticket.sla_deadline = None

    db.add(AuditLog(
        ticket_id=ticket.id,
        user_id=ticket.assignee_id,
        action="PAUSED",
        previous_status=ticket.status.value,
        new_status=ticket.status.value,
        notes=f"Put on hold (approved) — {ticket.remaining_sla_seconds}s remaining on SLA clock",
    ))


def _resume_ticket(ticket: Ticket, now: datetime) -> int:
    """
    Un-pause a held ticket: restart the SLA clock with the exact time
    that was remaining at the moment of pause. Returns seconds restored.
    """
    remaining = ticket.remaining_sla_seconds
    if not remaining or remaining <= 0:
        remaining = int(_SLA_HOURS.get(ticket.priority, 24) * 3600)

    ticket.is_paused             = False
    ticket.paused_at             = None
    ticket.remaining_sla_seconds = None
    ticket.sla_deadline          = now + timedelta(seconds=remaining)
    return remaining


# ── Core assignment (no queue logic anymore) ──────────────

async def _assign_to_worker(
    ticket: Ticket,
    worker: User,
    db: AsyncSession,
    is_additional: bool = False,
) -> None:
    """
    Assign a ticket to one worker. The ticket ALWAYS becomes active
    immediately with a live SLA clock — nothing pauses, nothing queues.
    If the worker wants to pause other work to focus on this, they must
    request a hold and get manager/admin approval.
    """
    now = datetime.now(timezone.utc)

    if not is_additional:
        ticket.assignee_id = worker.id

    ticket.status       = TicketStatus.IN_PROGRESS
    ticket.assigned_at  = now
    ticket.is_paused    = False
    ticket.sla_deadline = _deadline_from_now(ticket.priority, now)

    level_tag = f" [{worker.level.value}]" if worker.level else ""
    db.add(AuditLog(
        ticket_id=ticket.id,
        user_id=worker.id,
        action=AuditAction.ASSIGNED,
        previous_status=TicketStatus.OPEN.value,
        new_status=TicketStatus.IN_PROGRESS.value,
        notes=f"Assigned to {worker.full_name}{level_tag} — SLA clock started",
    ))


# ═══════════════════════════════════════════════════════════
#  HOLD REQUEST WORKFLOW (replaces the automatic pause queue)
# ═══════════════════════════════════════════════════════════

async def create_hold_request(
    ticket: Ticket,
    worker: User,
    blocking_ticket: Ticket,
    remark: str,
    db: AsyncSession,
) -> dict:
    """
    Worker REQUESTS permission to pause `ticket` because `blocking_ticket`
    (higher priority, also assigned to them) needs their full attention.
    Creates a PENDING HoldRequest — the SLA on `ticket` keeps running
    until a manager/admin approves AND the worker clicks Hold.
    """
    from app.models.hold_request import HoldRequest, HoldRequestStatus

    if not _worker_is_on_ticket(ticket, worker.id):
        return {"requested": False, "reason": "This ticket is not assigned to you"}
    if ticket.status != TicketStatus.IN_PROGRESS:
        return {"requested": False, "reason": "Only in-progress tickets can be put on hold"}
    if ticket.is_paused:
        return {"requested": False, "reason": "This ticket is already on hold"}
    if not _worker_is_on_ticket(blocking_ticket, worker.id):
        return {"requested": False, "reason": "The blocking ticket is not assigned to you"}
    if blocking_ticket.id == ticket.id:
        return {"requested": False, "reason": "A ticket cannot block itself"}
    if blocking_ticket.status != TicketStatus.IN_PROGRESS or blocking_ticket.is_paused:
        return {"requested": False, "reason": "The blocking ticket is not an active in-progress ticket"}
    if PRIORITY_ORDER[blocking_ticket.priority] <= PRIORITY_ORDER[ticket.priority]:
        return {"requested": False,
                "reason": "Holds are only allowed when the blocking ticket has a STRICTLY higher priority"}
    if not remark or not remark.strip():
        return {"requested": False, "reason": "A remark explaining why you need the hold is required"}

    # Block duplicate open requests for the same ticket
    existing = await db.execute(
        select(HoldRequest).where(
            HoldRequest.ticket_id == ticket.id,
            HoldRequest.status.in_([HoldRequestStatus.PENDING, HoldRequestStatus.APPROVED]),
        )
    )
    if existing.scalars().first():
        return {"requested": False, "reason": "An open hold request already exists for this ticket"}

    req = HoldRequest(
        ticket_id=ticket.id,
        blocking_ticket_id=blocking_ticket.id,
        requested_by_id=worker.id,
        remark=remark.strip(),
        status=HoldRequestStatus.PENDING,
    )
    db.add(req)

    db.add(AuditLog(
        ticket_id=ticket.id,
        user_id=worker.id,
        action=AuditAction.ASSIGNED,
        previous_status=ticket.status.value,
        new_status=ticket.status.value,
        notes=(
            f"{worker.full_name} requested a HOLD (blocked by "
            f"{blocking_ticket.priority.value.upper()} ticket "
            f"#{getattr(blocking_ticket, 'ticket_number', None) or str(blocking_ticket.id)[:8]}) — "
            f"awaiting manager/admin approval. Remark: {remark.strip()}"
        ),
    ))

    await db.flush()
    await db.refresh(req)

    return {"requested": True, "request_id": req.id}


async def approve_hold_request(
    request_id: uuid.UUID,
    approver: User,
    db: AsyncSession,
) -> dict:
    """
    Manager/admin APPROVES a pending hold request.
    Nothing pauses yet — this only unlocks the worker's Hold button.
    The worker decides when to actually execute (see execute_hold).
    """
    from app.models.hold_request import HoldRequest, HoldRequestStatus

    req = await db.get(HoldRequest, request_id)
    if not req:
        return {"approved": False, "reason": "Request not found"}
    if req.status != HoldRequestStatus.PENDING:
        return {"approved": False, "reason": f"Request is already {req.status.value}"}

    ticket = await db.get(Ticket, req.ticket_id)
    worker = await db.get(User, req.requested_by_id)
    if not ticket or not worker:
        return {"approved": False, "reason": "Ticket or worker no longer exists"}
    if ticket.status != TicketStatus.IN_PROGRESS or ticket.is_paused:
        req.status         = HoldRequestStatus.DENIED
        req.decided_by_id  = approver.id
        req.decided_at     = datetime.now(timezone.utc)
        req.decision_notes = "Auto-denied on approval attempt: ticket is no longer active"
        return {"approved": False, "reason": "Ticket is no longer active — request auto-denied"}

    req.status        = HoldRequestStatus.APPROVED
    req.decided_by_id = approver.id
    req.decided_at    = datetime.now(timezone.utc)

    db.add(AuditLog(
        ticket_id=ticket.id,
        user_id=approver.id,
        action=AuditAction.ASSIGNED,
        previous_status=ticket.status.value,
        new_status=ticket.status.value,
        notes=(
            f"Hold request APPROVED by {approver.full_name} — "
            f"{worker.full_name} may now put this ticket on hold"
        ),
    ))

    return {"approved": True, "ticket": ticket, "worker": worker, "request": req}


async def deny_hold_request(
    request_id: uuid.UUID,
    approver: User,
    notes: Optional[str],
    db: AsyncSession,
) -> dict:
    """Manager/admin DENIES a pending hold request. SLA keeps running."""
    from app.models.hold_request import HoldRequest, HoldRequestStatus

    req = await db.get(HoldRequest, request_id)
    if not req:
        return {"denied": False, "reason": "Request not found"}
    if req.status != HoldRequestStatus.PENDING:
        return {"denied": False, "reason": f"Request is already {req.status.value}"}

    req.status         = HoldRequestStatus.DENIED
    req.decided_by_id  = approver.id
    req.decided_at     = datetime.now(timezone.utc)
    req.decision_notes = notes

    ticket = await db.get(Ticket, req.ticket_id)
    worker = await db.get(User, req.requested_by_id)
    if ticket:
        db.add(AuditLog(
            ticket_id=ticket.id,
            user_id=approver.id,
            action=AuditAction.ASSIGNED,
            previous_status=ticket.status.value,
            new_status=ticket.status.value,
            notes=(
                f"Hold request DENIED by {approver.full_name}"
                + (f" — {notes}" if notes else "")
                + ". SLA clock continues."
            ),
        ))

    return {"denied": True, "request_id": req.id, "ticket": ticket, "worker": worker}


async def execute_hold(
    ticket: Ticket,
    worker: User,
    db: AsyncSession,
) -> dict:
    """
    Worker clicks HOLD on a ticket with an APPROVED hold request.
    THIS is the moment the ticket actually pauses and the SLA freezes.
    """
    from app.models.hold_request import HoldRequest, HoldRequestStatus

    if not _worker_is_on_ticket(ticket, worker.id):
        return {"held": False, "reason": "This ticket is not assigned to you"}
    if ticket.is_paused:
        return {"held": False, "reason": "This ticket is already on hold"}
    if ticket.status != TicketStatus.IN_PROGRESS:
        return {"held": False, "reason": "Only in-progress tickets can be put on hold"}

    res = await db.execute(
        select(HoldRequest).where(
            HoldRequest.ticket_id == ticket.id,
            HoldRequest.requested_by_id == worker.id,
            HoldRequest.status == HoldRequestStatus.APPROVED,
        ).order_by(HoldRequest.decided_at.desc())
    )
    req = res.scalars().first()
    if not req:
        return {"held": False, "reason": "No approved hold request found for this ticket — request one first"}

    # If the blocking ticket was already resolved, the reason for the
    # hold is gone — refuse and close the request out.
    if req.blocking_ticket_id:
        blocking = await db.get(Ticket, req.blocking_ticket_id)
        if blocking and blocking.status in (TicketStatus.RESOLVED, TicketStatus.CLOSED):
            req.status       = HoldRequestStatus.COMPLETED
            req.completed_at = datetime.now(timezone.utc)
            return {"held": False,
                    "reason": "The higher-priority ticket is already resolved — no hold needed anymore"}

    await pause_ticket(ticket, db)
    req.status      = HoldRequestStatus.EXECUTED
    req.executed_at = datetime.now(timezone.utc)

    return {"held": True, "request_id": req.id,
            "remaining_sla_seconds": ticket.remaining_sla_seconds}


async def resume_held_tickets_for(resolved_ticket: Ticket, db: AsyncSession) -> list[Ticket]:
    """
    Call when a ticket is RESOLVED (or closed).

    Finds every EXECUTED hold whose blocking_ticket_id is this ticket
    and resumes the held tickets — their SLA clocks restart with the
    exact remaining time saved at pause, and the hold requests are
    marked COMPLETED.

    This replaces the old resume_highest_paused_ticket()/
    resume_queues_for_ticket_assignees() pair: resumption is now tied
    to the SPECIFIC blocking ticket, not to whichever paused ticket a
    worker happened to have.
    """
    from app.models.hold_request import HoldRequest, HoldRequestStatus

    res = await db.execute(
        select(HoldRequest).where(
            HoldRequest.blocking_ticket_id == resolved_ticket.id,
            HoldRequest.status == HoldRequestStatus.EXECUTED,
        )
    )
    holds = list(res.scalars().all())
    if not holds:
        return []

    now = datetime.now(timezone.utc)
    resumed: list[Ticket] = []

    for hold in holds:
        held = await db.get(Ticket, hold.ticket_id)
        hold.status       = HoldRequestStatus.COMPLETED
        hold.completed_at = now

        if not held or not held.is_paused or held.status != TicketStatus.IN_PROGRESS:
            continue   # ticket vanished / already resumed / already resolved

        remaining = _resume_ticket(held, now)
        db.add(AuditLog(
            ticket_id=held.id,
            user_id=hold.requested_by_id,
            action="RESUMED",
            previous_status=held.status.value,
            new_status=held.status.value,
            notes=(
                f"Auto-resumed — blocking ticket "
                f"#{getattr(resolved_ticket, 'ticket_number', None) or str(resolved_ticket.id)[:8]} "
                f"was resolved. {remaining}s restored on SLA clock"
            ),
        ))
        resumed.append(held)
        print(f"▶️  Resumed held ticket {held.id} — {remaining}s left")

    return resumed


async def cancel_open_holds_for_ticket(
    ticket: Ticket,
    actor: User,
    reason: str,
    db: AsyncSession,
) -> int:
    """
    Auto-deny PENDING/APPROVED holds on a ticket (used when the ticket
    is reassigned to a different worker — the old worker's hold request
    no longer makes sense). Returns how many were cancelled.
    """
    from app.models.hold_request import HoldRequest, HoldRequestStatus

    res = await db.execute(
        select(HoldRequest).where(
            HoldRequest.ticket_id == ticket.id,
            HoldRequest.status.in_([HoldRequestStatus.PENDING, HoldRequestStatus.APPROVED]),
        )
    )
    open_holds = list(res.scalars().all())
    now = datetime.now(timezone.utc)
    for h in open_holds:
        h.status         = HoldRequestStatus.DENIED
        h.decided_by_id  = actor.id
        h.decided_at     = now
        h.decision_notes = f"Auto-denied — {reason}"
    return len(open_holds)


# ── Delegation (lead worker assigns a helper) ─────────────

async def delegate_helper(
    ticket: Ticket,
    helper: User,
    db: AsyncSession,
) -> dict:
    """
    Attaches a helper to a ticket — the APPLY step after a manager/admin
    approves a HelpRequest.

    NO AUTO-PAUSE anymore: the helper's other active tickets keep their
    SLA clocks running. If the helper needs to pause something to focus
    on this, they request a hold like everyone else.
    """
    helper_id_str = str(helper.id)
    current_ids = ticket.additional_assignee_list

    if ticket.assignee_id == helper.id:
        return {"delegated": False, "reason": "This worker is already the lead assignee"}
    if helper_id_str in current_ids:
        return {"delegated": False, "reason": "This worker is already delegated on this ticket"}
    if helper.is_blacklisted:
        return {"delegated": False, "reason": "This worker is blacklisted"}

    current_ids.append(helper_id_str)
    ticket.additional_assignee_list = current_ids

    db.add(AuditLog(
        ticket_id=ticket.id,
        user_id=helper.id,
        action=AuditAction.ASSIGNED,
        previous_status=ticket.status.value,
        new_status=ticket.status.value,
        notes=(
            f"Helper {helper.full_name} added after manager approval — "
            f"their other tickets keep running (they can request a hold if needed)"
        ),
    ))

    return {"delegated": True, "helper": helper.full_name}


# ── Help Request workflow (approval-gated delegation) ─────

async def create_help_request(
    ticket: Ticket,
    lead: User,
    helper: User,
    db: AsyncSession,
) -> dict:
    """
    Lead worker REQUESTS a helper — does NOT add them yet.
    A manager or admin must approve before the helper is attached.
    """
    from app.models.help_request import HelpRequest, HelpRequestStatus

    if ticket.assignee_id != lead.id:
        return {"requested": False, "reason": "Only the lead worker on this ticket can request help"}
    if helper.id == lead.id:
        return {"requested": False, "reason": "Cannot request yourself as a helper"}
    if helper.is_blacklisted:
        return {"requested": False, "reason": "This worker is blacklisted"}
    if str(helper.id) in ticket.additional_assignee_list:
        return {"requested": False, "reason": "This worker is already an approved helper on this ticket"}

    existing = await db.execute(
        select(HelpRequest).where(
            HelpRequest.ticket_id == ticket.id,
            HelpRequest.helper_id == helper.id,
            HelpRequest.status == HelpRequestStatus.PENDING,
        )
    )
    if existing.scalar_one_or_none():
        return {"requested": False, "reason": "A pending request for this helper already exists"}

    req = HelpRequest(
        ticket_id=ticket.id,
        requested_by_id=lead.id,
        helper_id=helper.id,
        status=HelpRequestStatus.PENDING,
    )
    db.add(req)

    db.add(AuditLog(
        ticket_id=ticket.id,
        user_id=lead.id,
        action=AuditAction.ASSIGNED,
        previous_status=ticket.status.value,
        new_status=ticket.status.value,
        notes=f"{lead.full_name} requested help from {helper.full_name} — awaiting manager/admin approval",
    ))

    await db.flush()
    await db.refresh(req)

    return {"requested": True, "request_id": req.id, "helper": helper.full_name}


async def approve_help_request(
    request_id: uuid.UUID,
    approver: User,
    db: AsyncSession,
) -> dict:
    """
    Manager/admin approves a pending help request.
    Only then does the helper actually get attached via delegate_helper().
    """
    from app.models.help_request import HelpRequest, HelpRequestStatus

    req = await db.get(HelpRequest, request_id)
    if not req:
        return {"approved": False, "reason": "Request not found"}
    if req.status != HelpRequestStatus.PENDING:
        return {"approved": False, "reason": f"Request is already {req.status.value}"}

    ticket = await db.get(Ticket, req.ticket_id)
    helper = await db.get(User, req.helper_id)
    if not ticket or not helper:
        return {"approved": False, "reason": "Ticket or helper no longer exists"}

    result = await delegate_helper(ticket, helper, db)
    if not result["delegated"]:
        req.status         = HelpRequestStatus.DENIED
        req.decided_by_id  = approver.id
        req.decided_at     = datetime.now(timezone.utc)
        req.decision_notes = f"Auto-denied on approval attempt: {result['reason']}"
        return {"approved": False, "reason": result["reason"]}

    req.status        = HelpRequestStatus.APPROVED
    req.decided_by_id = approver.id
    req.decided_at    = datetime.now(timezone.utc)

    return {"approved": True, "ticket": ticket, "helper": helper, "lead_id": req.requested_by_id}


async def deny_help_request(
    request_id: uuid.UUID,
    approver: User,
    notes: Optional[str],
    db: AsyncSession,
) -> dict:
    """Manager/admin denies a pending help request. Nothing is attached."""
    from app.models.help_request import HelpRequest, HelpRequestStatus

    req = await db.get(HelpRequest, request_id)
    if not req:
        return {"denied": False, "reason": "Request not found"}
    if req.status != HelpRequestStatus.PENDING:
        return {"denied": False, "reason": f"Request is already {req.status.value}"}

    req.status         = HelpRequestStatus.DENIED
    req.decided_by_id  = approver.id
    req.decided_at     = datetime.now(timezone.utc)
    req.decision_notes = notes

    return {"denied": True, "request_id": req.id}


# ── Email helpers ─────────────────────────────────────────

async def _notify_managers_auto_assigned(
    ticket: Ticket,
    assignee: User,
    db: AsyncSession,
) -> None:
    """Email dept manager(s) + admins when a HIGH/CRITICAL auto-assigns."""
    try:
        from app.services.email_service import (
            send_high_priority_alert,
            get_manager_emails_for_dept,
            get_admin_emails,
        )
        mgr_emails   = await get_manager_emails_for_dept(ticket.category or "", db) if ticket.category else []
        admin_emails = await get_admin_emails(db)
        recipients   = list(set(mgr_emails + admin_emails))
        if recipients:
            await send_high_priority_alert(
                ticket=ticket,
                recipient_emails=recipients,
                assignee_name=assignee.full_name,
            )
    except Exception as e:
        print(f"   (manager notification skipped: {e})")


async def _send_worker_assignment_email(
    ticket: Ticket, worker: User
) -> None:
    """Send assignment email to one worker."""
    try:
        from app.services.email_service import send_assignment_email
        await send_assignment_email(
            worker_email=worker.email,
            worker_name=worker.full_name,
            ticket=ticket,
        )
    except Exception as e:
        print(f"   (assignment email to {worker.email} skipped: {e})")


# ── Main entry point ──────────────────────────────────────

async def route_ticket(ticket: Ticket, db: AsyncSession) -> dict:
    """
    Route a freshly-created, categorized ticket DIRECTLY to an engineer
    of the right level within the ML-detected department:

    Priority   │ Preferred level │ Fallback order
    ───────────┼─────────────────┼──────────────────────────
    LOW        │ L1              │ L2 → L3 → any/unleveled
    MEDIUM     │ L1              │ L2 → L3 → any/unleveled
    HIGH       │ L2              │ L3 → L1 → any/unleveled
    CRITICAL   │ L3              │ L2 → L1 → any/unleveled

    Within a level, the least-busy eligible worker wins. Workers at/above
    MAX_WORKER_LOAD are skipped (CRITICAL/HIGH ignore the cap).
    The assigned ticket starts with a LIVE SLA clock — nothing pauses.
    """
    if not ticket.category:
        return {"routed": "unrouted", "reason": "needs manual categorization — no category set"}

    dept = ticket.category
    is_urgent = ticket.priority in (TicketPriority.CRITICAL, TicketPriority.HIGH)

    worker = None
    routed_level = None
    for lvl in LEVEL_PREFERENCE.get(ticket.priority, []):
        candidates = await find_available_workers(
            dept, db, limit=1, ignore_load_cap=is_urgent, level=lvl
        )
        if candidates:
            worker = candidates[0]
            routed_level = lvl
            break

    if not worker:
        # Last resort: level-agnostic pass (catches unleveled workers)
        candidates = await find_available_workers(dept, db, limit=1, ignore_load_cap=is_urgent)
        worker = candidates[0] if candidates else None

    if not worker:
        return {"routed": "unrouted",
                "reason": f"no available worker in '{dept}' (all at capacity or blacklisted)"}

    ticket.assignee_id = worker.id
    await _assign_to_worker(ticket, worker, db, is_additional=False)
    await _send_worker_assignment_email(ticket, worker)

    if is_urgent:
        await _notify_managers_auto_assigned(ticket, worker, db)

    level_note = f" [{routed_level.value}]" if routed_level else (
        f" [{worker.level.value}]" if worker.level else " [unleveled]"
    )
    return {
        "routed":   "auto",
        "assignee": worker.full_name,
        "level":    worker.level.value if worker.level else None,
        "reason":   f"auto-assigned to {worker.full_name}{level_note}"
                    + (" (lead — can delegate helpers)" if is_urgent else ""),
    }
