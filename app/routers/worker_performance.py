"""
Worker Performance Analytics + ML Resolution Suggestions

Mount in main.py (these extend the existing /worker prefix):
    from app.routers import worker_performance
    app.include_router(worker_performance.router, prefix="/worker")

Also registers one admin route at /admin for manager/admin to view
any worker's breakdown — add that router too:
    app.include_router(worker_performance.admin_router, prefix="/admin")
"""
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from pydantic import BaseModel

from app.database import get_db
from app.models.ticket import Ticket, TicketStatus, TicketPriority
from app.models.audit_log import AuditLog
from app.models.user import User, UserRole
from app.core.deps import get_current_user, require_role

router       = APIRouter(tags=["Worker Analytics"])
admin_router = APIRouter(tags=["Admin Analytics"])

BREACH_POINTS = {
    TicketPriority.CRITICAL: 15,
    TicketPriority.HIGH:     10,
    TicketPriority.MEDIUM:    5,
    TicketPriority.LOW:       2,
}
BLACKLIST_THRESHOLD = 30

# ── Category-based resolution procedure templates ─────────
# Shown as structured step-by-step guides when no similar
# resolved ticket history exists (or alongside history results).
RESOLUTION_TEMPLATES: dict[str, list[dict]] = {
    "it": [
        {
            "title": "Hardware / Device Fault",
            "steps": [
                "Power-cycle the device and check indicator lights",
                "Run a hardware diagnostic (built-in tools or manufacturer utility)",
                "Check device cables, connections, and power supply",
                "Review Windows Event Viewer or syslog for error codes",
                "Replace the faulty component or arrange equipment swap",
                "Verify functionality, update asset register, close ticket",
            ],
        },
        {
            "title": "Network / Connectivity Issue",
            "steps": [
                "Confirm scope: one device or entire segment?",
                "Ping gateway and DNS to isolate the break point",
                "Restart NIC / re-seat patch cable at port and switch",
                "Check switch port status and VLAN assignment",
                "If access point: reboot and verify channel/band settings",
                "Document IP, MAC, port details for network team handoff if unresolved",
            ],
        },
        {
            "title": "Software / Application Error",
            "steps": [
                "Capture error message or screenshot for audit",
                "Check for pending OS or application updates",
                "Clear application cache / temp files and restart",
                "Re-install the application if corruption is suspected",
                "Check software licensing is current",
                "Escalate to vendor support with reproduction steps if needed",
            ],
        },
    ],
    "security": [
        {
            "title": "Physical Security Breach",
            "steps": [
                "Secure the affected area immediately — prevent further access",
                "Note exact time, location, and description of the incident",
                "Review CCTV footage for the relevant time window (preserve evidence)",
                "Notify the Security Operations Centre and document all findings",
                "Identify and contact witnesses; collect written statements",
                "File a formal incident report and track remediation actions",
            ],
        },
        {
            "title": "Access / Credential Issue",
            "steps": [
                "Verify the requestor's identity via HR records or photo ID",
                "Check access control logs for the affected zone/system",
                "Revoke and reissue credentials using the approved IAM workflow",
                "Audit who else had the compromised credential",
                "Update access logs and notify the requestor's manager",
                "Schedule a post-incident access review within 48 hours",
            ],
        },
    ],
    "hr": [
        {
            "title": "Employee Complaint / Conduct Issue",
            "steps": [
                "Acknowledge receipt of complaint in writing within 24 hours",
                "Schedule a private, confidential meeting with all parties separately",
                "Document all facts: dates, witnesses, supporting evidence",
                "Refer to the employee code of conduct and grievance policy",
                "Engage HR mediator or legal if the issue cannot be resolved bilaterally",
                "Issue a written resolution summary and retain it in the employee file",
            ],
        },
        {
            "title": "Medical / Wellness Emergency",
            "steps": [
                "Activate the nearest first-aid response team immediately",
                "Call emergency services (112) if the situation requires it",
                "Clear and secure the area for responders",
                "Document the incident: time, location, persons involved, response taken",
                "Notify next of kin following privacy and HR policy",
                "Complete an official incident report within 4 hours",
            ],
        },
    ],
    "technical_operations": [
        {
            "title": "Equipment / Machinery Fault",
            "steps": [
                "Isolate the faulty equipment — switch off at source if safe to do so",
                "Assess risk: is there a safety hazard to personnel or infrastructure?",
                "Run manufacturer diagnostic or visual inspection",
                "Order replacement parts or engage certified maintenance vendor",
                "Test the repaired unit before returning to service",
                "Update the maintenance log and notify operations supervisor",
            ],
        },
        {
            "title": "Utility / Infrastructure Failure",
            "steps": [
                "Identify affected zone and scope of impact",
                "Switch to backup system / redundant supply if available",
                "Contact the relevant utility provider with incident reference",
                "Post status updates to affected teams every 30 minutes",
                "Document root cause once restored",
                "Schedule a post-incident review to prevent recurrence",
            ],
        },
    ],
    "marketing": [
        {
            "title": "Campaign / Content Issue",
            "steps": [
                "Pause the affected campaign/channel to prevent further exposure",
                "Identify the root cause: incorrect copy, broken link, wrong audience?",
                "Coordinate with creative and vendor teams for corrected assets",
                "Get sign-off from the marketing manager before relaunching",
                "Implement the correction and verify across all affected channels",
                "Monitor engagement metrics for 24 hours post-fix",
            ],
        },
    ],
    "legal": [
        {
            "title": "Document / Contract Issue",
            "steps": [
                "Retrieve the original document and all amendment versions",
                "Identify the specific clause(s) in dispute or error",
                "Cross-reference against applicable legislation or company policy",
                "Consult with in-house legal counsel before communicating externally",
                "Draft a formal written advisory or amendment for review",
                "Retain all correspondence in the legal matter file",
            ],
        },
        {
            "title": "Compliance Breach",
            "steps": [
                "Assess severity: is regulatory disclosure required?",
                "Preserve all relevant records and restrict access to prevent tampering",
                "Notify compliance officer and document the timeline of events",
                "Engage external legal counsel if required",
                "Implement immediate corrective controls",
                "Submit regulatory notification if mandated within the deadline",
            ],
        },
    ],
    "default": [
        {
            "title": "General Incident Resolution",
            "steps": [
                "Confirm the exact nature and scope of the issue with the reporter",
                "Check for recent changes that may have triggered this incident",
                "Search for a known fix in your team's knowledge base",
                "Apply the fix and test in a controlled manner",
                "Document the root cause and resolution steps taken",
                "Confirm resolution with the reporter before closing the ticket",
            ],
        },
    ],
}


# ── Shared analytics logic ─────────────────────────────────

async def _compute_analytics(worker: User, db: AsyncSession) -> dict:
    """
    Compute daily / weekly / monthly breach stats for a worker.

    Sources every breach EVENT from audit_logs (action=ESCALATED) so that
    tickets which breached multiple times (the SLA engine can penalize a
    ticket on every pass while it stays past its deadline) show up as
    separate timeline rows with their own timestamps. Previously this
    queried tickets and each ticket produced only one row, so repeat
    breaches on the same ticket looked like a single event with the
    wrong point total.
    """
    now        = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start  = today_start - timedelta(days=today_start.weekday())   # Monday
    month_start = today_start.replace(day=1)

    uid_str = str(worker.id)

    # Pull every ESCALATED audit event on a ticket this worker was on
    # (primary assignee OR listed in additional_assignee_ids). Join to
    # tickets for priority + title.
    result = await db.execute(
        select(AuditLog, Ticket).join(Ticket, Ticket.id == AuditLog.ticket_id).where(
            AuditLog.action == "ESCALATED",
            or_(
                Ticket.assignee_id == worker.id,
                Ticket.additional_assignee_ids.like(f'%{uid_str}%'),
            ),
        ).order_by(AuditLog.created_at.desc()).limit(300)
    )
    breach_events = list(result.all())   # list of (AuditLog, Ticket) tuples

    def _period(events, start_dt):
        subset = [(log, t) for (log, t) in events if log.created_at >= start_dt]
        pts    = sum(BREACH_POINTS.get(t.priority, 5) for (_, t) in subset)
        by_priority = {}
        for (_, t) in subset:
            pv = t.priority.value
            by_priority[pv] = by_priority.get(pv, 0) + 1
        return {"count": len(subset), "points_lost": pts, "by_priority": by_priority}

    recent = [
        {
            "ticket_id":       str(t.id),
            "ticket_title":    t.title,
            "priority":        t.priority.value,
            "escalated_at":    log.created_at.isoformat(),
            "points_deducted": BREACH_POINTS.get(t.priority, 5),
        }
        for (log, t) in breach_events[:15]
    ]

    return {
        "worker_id":           str(worker.id),
        "worker_name":         worker.full_name,
        "current_total_points": worker.penalty_points,
        "breach_count_total":  worker.breach_count,
        "is_blacklisted":      worker.is_blacklisted,
        "points_until_blacklist": max(0, BLACKLIST_THRESHOLD - worker.penalty_points),
        "today":      _period(breach_events, today_start),
        "this_week":  _period(breach_events, week_start),
        "this_month": _period(breach_events, month_start),
        "recent_breaches": recent,
    }


# ── GET /worker/me/performance ────────────────────────────

@router.get("/me/performance")
async def my_performance(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Worker views their own daily / weekly / monthly breach stats."""
    return await _compute_analytics(current_user, db)


# ── GET /worker/me/tickets/{ticket_id}/suggestions ─────────

@router.get("/me/tickets/{ticket_id}/suggestions")
async def get_suggestions(
    ticket_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    ML resolution suggestions for an in-progress ticket.

    Two layers:
      1. TF-IDF similarity against resolved tickets in the same category —
         finds real past incidents that were similar and were resolved.
         Uses sklearn.TfidfVectorizer + cosine_similarity on-the-fly
         (no stored model needed — just sklearn which is already installed).
      2. Category-based procedure templates — structured step-by-step guides
         for each of the 6 Adani departments. Always available as a baseline,
         even on day 1 with no resolved history.
    """
    ticket = await db.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")

    # Security: worker must be primary or additional assignee
    uid_str = str(current_user.id)
    is_assigned = (
        ticket.assignee_id == current_user.id
        or uid_str in ticket.additional_assignee_list
    )
    if not is_assigned and current_user.role not in (UserRole.MANAGER, UserRole.ADMIN):
        raise HTTPException(403, "Not assigned to this ticket")

    # ── Layer 1: TF-IDF similarity search ─────────────────
    similar_results = []
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity

        # Fetch up to 200 resolved tickets in the same category
        cat_filter = [Ticket.category == ticket.category] if ticket.category else []
        resolved_res = await db.execute(
            select(Ticket).where(
                Ticket.status.in_([TicketStatus.RESOLVED, TicketStatus.CLOSED]),
                Ticket.id != ticket.id,
                *cat_filter,
            ).order_by(Ticket.resolved_at.desc()).limit(200)
        )
        resolved_tickets = list(resolved_res.scalars().all())

        if len(resolved_tickets) >= 3:     # need enough history to be meaningful
            query_text = f"{ticket.title} {ticket.description}"
            corpus     = [f"{t.title} {t.description}" for t in resolved_tickets]

            vec    = TfidfVectorizer(ngram_range=(1, 2), max_features=8000, min_df=1)
            matrix = vec.fit_transform(corpus + [query_text])
            q_vec  = matrix[-1]
            sims   = cosine_similarity(q_vec, matrix[:-1])[0]

            top_idx = sims.argsort()[::-1][:5]
            for i in top_idx:
                score = float(sims[i])
                if score < 0.08:          # skip noise
                    break
                t = resolved_tickets[i]
                similar_results.append({
                    "similarity_score": round(score, 3),
                    "ticket_id":        str(t.id),
                    "title":            t.title,
                    "description":      t.description[:300] + ("…" if len(t.description) > 300 else ""),
                    "priority":         t.priority.value,
                    "resolved_at":      t.resolved_at.isoformat() if t.resolved_at else None,
                })
    except Exception as e:
        print(f"   (ML suggestions skipped: {e})")

    # ── Layer 2: Category procedure templates ──────────────
    templates = RESOLUTION_TEMPLATES.get(
        ticket.category or "default",
        RESOLUTION_TEMPLATES["default"],
    )

    return {
        "ticket_id":       str(ticket_id),
        "ticket_title":    ticket.title,
        "category":        ticket.category,
        "priority":        ticket.priority.value,
        "similar_resolved": similar_results,
        "similar_count":   len(similar_results),
        "templates":       templates,
        "ml_note": (
            f"Similarity search ran against {len(similar_results)} "
            f"resolved {'%s' % ticket.category or 'uncategorized'} tickets."
            if similar_results else
            "Not enough resolved ticket history to compute similarity — "
            "showing procedure templates only."
        ),
    }


# ── GET /admin/workers/{worker_id}/performance ─────────────
# (goes in admin_router, mounted at /admin prefix)

@admin_router.get("/workers/{worker_id}/performance")
async def worker_performance_detail(
    worker_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.MANAGER, UserRole.ADMIN)),
):
    """
    Manager / admin views any worker's daily / weekly / monthly breakdown.
    Managers are additionally restricted to workers in their own department.
    """
    worker = await db.get(User, worker_id)
    if not worker:
        raise HTTPException(404, "Worker not found")

    # Managers can only see their own department's workers
    if current_user.role == UserRole.MANAGER:
        if worker.department != current_user.department:
            raise HTTPException(403, "You can only view workers in your own department")

    return await _compute_analytics(worker, db)
