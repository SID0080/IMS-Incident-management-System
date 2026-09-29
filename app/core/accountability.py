"""
Accountability / points-system configuration.

Tune these numbers to change how harsh the flagging system is.
"""
from app.models.ticket import TicketPriority

# ── Penalty points per breached ticket, by priority ───────
# Critical breaches hurt the most. Confirmed intentional values.
# On a multi-worker ticket (HIGH/CRITICAL with an approved helper) this
# penalty is applied to EVERY assigned worker, not just the primary —
# see sla_engine._process_breach.
BREACH_PENALTY_POINTS: dict[TicketPriority, int] = {
    TicketPriority.CRITICAL: 15,
    TicketPriority.HIGH:     10,
    TicketPriority.MEDIUM:    5,
    TicketPriority.LOW:       2,
}

# ── Blacklist threshold ───────────────────────────────────
# When a worker's penalty_points reaches/exceeds this, they are
# blacklisted and stop receiving auto-assignments until an admin
# clears their points.
BLACKLIST_THRESHOLD = 30

# NOTE: MANAGER_ASSIGN_PRIORITIES / AUTO_ASSIGN_PRIORITIES were removed.
# They described the OLD routing model ("Critical/High → manager assigns
# manually"). Since the single-worker-lead architecture, EVERY priority
# auto-assigns to one worker via assignment_service.route_ticket() —
# CRITICAL/HIGH just skip the MAX_WORKER_LOAD cap (urgency > balance).
# Nothing in the codebase imports these constants anymore.
