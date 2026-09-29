"""
SLA breach engine — skips paused tickets in priority queue.

A background job (APScheduler) calls check_sla_breaches() every minute.
For every IN_PROGRESS, NON-PAUSED ticket past its sla_deadline:
  1. Escalates ticket → CRITICAL_REVIEW
  2. Penalizes assigned worker
  3. Blacklists worker if threshold crossed
  4. Logs ESCALATED audit entry
  5. Sends email alerts
"""
from datetime import datetime, timezone
import uuid

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models.ticket import Ticket, TicketStatus
from app.models.user import User
from app.models.audit_log import AuditLog, AuditAction
from app.core.accountability import BREACH_PENALTY_POINTS, BLACKLIST_THRESHOLD


async def check_sla_breaches() -> int:
    now = datetime.now(timezone.utc)
    breached_count = 0

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Ticket).where(
                and_(
                    Ticket.status == TicketStatus.IN_PROGRESS,
                    Ticket.is_paused    == False,          # ← KEY: skip paused tickets
                    Ticket.sla_deadline != None,           # noqa: E711
                    Ticket.sla_deadline <  now,
                )
            )
        )
        overdue = list(result.scalars().all())

        for ticket in overdue:
            await _process_breach(ticket, db)
            breached_count += 1

        if breached_count:
            await db.commit()

    if breached_count:
        print(f"⚠️  SLA engine: {breached_count} ticket(s) breached and escalated")

    return breached_count


async def _process_breach(ticket: Ticket, db: AsyncSession) -> None:
    prev_status = ticket.status

    # 1. Escalate
    ticket.status       = TicketStatus.CRITICAL_REVIEW
    ticket.escalated_at = datetime.now(timezone.utc)

    points = BREACH_PENALTY_POINTS.get(ticket.priority, 5)

    # Gather EVERY assigned worker: primary assignee + any additional
    # (HIGH/CRITICAL fan-out) helpers. All share accountability for the breach.
    worker_ids: list[uuid.UUID] = []
    if ticket.assignee_id:
        worker_ids.append(ticket.assignee_id)
    for wid in ticket.additional_assignee_list:
        try:
            worker_ids.append(uuid.UUID(str(wid)))
        except (ValueError, TypeError):
            continue
    worker_ids = list(dict.fromkeys(worker_ids))   # de-dupe, preserve order

    note_parts: list[str] = []
    # (email, name, points) for each worker who just crossed the blacklist line
    newly_blacklisted: list[tuple[str, str, int]] = []

    # 2+3. Penalize each assigned worker and check blacklist individually
    for wid in worker_ids:
        worker = await db.get(User, wid)
        if not worker:
            continue
        worker.penalty_points += points
        worker.breach_count   += 1
        part = f"{worker.full_name} +{points}pts (total {worker.penalty_points})"

        if worker.penalty_points >= BLACKLIST_THRESHOLD and not worker.is_blacklisted:
            worker.is_blacklisted = True
            part += " — BLACKLISTED"
            newly_blacklisted.append((worker.email, worker.full_name, worker.penalty_points))

        note_parts.append(part)

    worker_note = "; ".join(note_parts) if note_parts else "unassigned"

    # 4. Audit log
    db.add(AuditLog(
        ticket_id=ticket.id,
        user_id=ticket.assignee_id or ticket.reporter_id,
        action=AuditAction.ESCALATED,
        previous_status=prev_status.value,
        new_status=TicketStatus.CRITICAL_REVIEW.value,
        notes=f"SLA breach — {points}pts to {len(worker_ids)} assignee(s). {worker_note}",
    ))

    # 5. Email alerts
    try:
        from app.services.email_service import (
            send_breach_alert,
            send_escalation_alert,
            send_blacklist_alert,
            get_manager_emails_for_dept,
            get_admin_emails,
        )
        dept           = ticket.category or ""
        mgr_emails     = await get_manager_emails_for_dept(dept, db) if dept else []
        admin_emails   = await get_admin_emails(db)
        all_recipients = list(set(mgr_emails + admin_emails))

        await send_breach_alert(ticket=ticket, worker_note=worker_note, manager_emails=all_recipients)
        await send_escalation_alert(ticket=ticket, recipient_emails=all_recipients)

        # One blacklist alert per worker who crossed the threshold
        for (b_email, b_name, b_points) in newly_blacklisted:
            await send_blacklist_alert(
                worker_email=b_email,
                worker_name=b_name,
                penalty_points=b_points,
                admin_emails=admin_emails,
            )
    except Exception as e:
        print(f"   (email alerts skipped: {e})")
