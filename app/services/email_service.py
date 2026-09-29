"""
Email Notification Service — Phase 8

All 5 notification types:
  1. send_breach_alert       → SLA breach detected
  2. send_assignment_email   → Ticket assigned to worker
  3. send_blacklist_alert    → Worker blacklisted
  4. send_escalation_alert   → Ticket moved to CRITICAL_REVIEW
  5. send_emergency_alert    → Emergency ticket created

All functions are async and NEVER raise — email failure is logged
and swallowed so it never crashes the main application flow.

Gmail SMTP with App Password (set SMTP_USER + SMTP_PASSWORD in .env)
"""

import asyncio
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timezone
from typing import Optional

from app.config import settings


# ── Brand colors (matches frontend) ──────────────────────
NAVY   = "#0A192F"
BLUE   = "#007AC3"
PURPLE = "#6A2A88"
RED    = "#dc2626"
AMBER  = "#f59e0b"
GREEN  = "#10b981"
MAGENTA= "#B8205C"


# ── Core SMTP sender (runs in thread — SMTP is blocking) ──

def _send_sync(to_emails: list[str], subject: str, html_body: str) -> None:
    """
    Synchronous Gmail SMTP send. Called via asyncio.to_thread.
    Raises on failure so the async wrapper can catch and log it.
    """
    if not settings.SMTP_USER or not settings.SMTP_PASSWORD:
        raise ValueError("SMTP_USER or SMTP_PASSWORD not set in .env")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"Adani IMS Alerts <{settings.SMTP_USER}>"
    msg["To"]      = ", ".join(to_emails)

    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        smtp.sendmail(settings.SMTP_USER, to_emails, msg.as_string())


async def _send(to_emails: list[str], subject: str, html_body: str) -> bool:
    """
    Async wrapper. Returns True on success, False on failure.
    Never raises — always logs the error.
    """
    if not to_emails:
        print("   (email skipped: no recipients)")
        return False
    try:
        await asyncio.to_thread(_send_sync, to_emails, subject, html_body)
        print(f"   📧  Email sent → {', '.join(to_emails)} | {subject}")
        return True
    except Exception as e:
        print(f"   ⚠️  Email failed: {e}")
        return False


# ── HTML base template ────────────────────────────────────

def _base_template(header_color: str, header_icon: str,
                   header_title: str, body_html: str) -> str:
    return f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#F8FAFC;font-family:Inter,Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0">
    <tr><td align="center" style="padding:32px 16px;">
      <table width="600" cellpadding="0" cellspacing="0"
             style="background:#fff;border-radius:16px;overflow:hidden;
                    box-shadow:0 4px 24px rgba(10,25,47,0.10);">

        <!-- Header -->
        <tr><td style="background:{header_color};padding:28px 32px;">
          <table width="100%"><tr>
            <td>
              <p style="margin:0;color:rgba(255,255,255,0.75);
                        font-size:11px;font-weight:600;
                        letter-spacing:2px;text-transform:uppercase;">
                ADANI GROUP — INCIDENT MANAGEMENT SYSTEM
              </p>
              <h1 style="margin:8px 0 0;color:#fff;font-size:22px;font-weight:800;">
                {header_icon} {header_title}
              </h1>
            </td>
            <td align="right" style="vertical-align:top;">
              <span style="background:rgba(255,255,255,0.15);color:#fff;
                           font-size:11px;font-weight:700;padding:4px 12px;
                           border-radius:20px;letter-spacing:1px;">
                IMS ALERT
              </span>
            </td>
          </tr></table>
        </td></tr>

        <!-- Body -->
        <tr><td style="padding:32px;">
          {body_html}
        </td></tr>

        <!-- Footer -->
        <tr><td style="background:#F1F5F9;padding:20px 32px;
                       border-top:1px solid #E2E8F0;">
          <p style="margin:0;color:#94a3b8;font-size:12px;">
            This is an automated alert from <strong>Adani IMS</strong>.
            Do not reply to this email.
            Log in to <a href="http://localhost:8000/docs"
                         style="color:{BLUE};">IMS Dashboard</a> to take action.
          </p>
          <p style="margin:8px 0 0;color:#cbd5e1;font-size:11px;">
            Sent at {datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC")}
          </p>
        </td></tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _ticket_info_block(ticket) -> str:
    """Reusable ticket summary block used in multiple templates."""
    priority_colors = {
        "critical": ("#fee2e2", RED),
        "high":     ("#ffedd5", "#ea580c"),
        "medium":   ("#fef3c7", "#d97706"),
        "low":      ("#f1f5f9", "#64748b"),
    }
    priority_val = ticket.priority.value if hasattr(ticket.priority, 'value') else str(ticket.priority)
    bg, fg = priority_colors.get(priority_val, ("#f1f5f9", "#64748b"))

    return f"""
    <table width="100%" cellpadding="0" cellspacing="0"
           style="background:#F8FAFC;border-radius:12px;
                  border:1px solid #E2E8F0;margin:20px 0;">
      <tr><td style="padding:20px;">
        <p style="margin:0 0 4px;color:#94a3b8;font-size:11px;
                  font-weight:700;letter-spacing:1.5px;text-transform:uppercase;">
          Incident Details
        </p>
        <h2 style="margin:0 0 16px;color:{NAVY};font-size:17px;font-weight:700;">
          {ticket.title}
        </h2>
        <table width="100%">
          <tr>
            <td width="50%" style="padding-bottom:8px;">
              <span style="color:#94a3b8;font-size:12px;">Priority</span><br>
              <span style="background:{bg};color:{fg};font-size:12px;
                           font-weight:700;padding:3px 10px;border-radius:20px;
                           text-transform:uppercase;letter-spacing:0.5px;">
                {priority_val}
              </span>
            </td>
            <td width="50%" style="padding-bottom:8px;">
              <span style="color:#94a3b8;font-size:12px;">Department</span><br>
              <strong style="color:{NAVY};font-size:14px;">
                {(ticket.category or 'Uncategorized').replace('_',' ').title()}
              </strong>
            </td>
          </tr>
          <tr>
            <td style="padding-bottom:8px;">
              <span style="color:#94a3b8;font-size:12px;">Ticket No.</span><br>
              <code style="color:#64748b;font-size:13px;font-weight:bold;">
                #{getattr(ticket, 'ticket_number', None) or str(ticket.id)[:8]}
              </code>
            </td>
            <td>
              <span style="color:#94a3b8;font-size:12px;">Created</span><br>
              <strong style="color:{NAVY};font-size:13px;">
                {ticket.created_at.strftime("%d %b %Y, %H:%M UTC") if ticket.created_at else "—"}
              </strong>
            </td>
          </tr>
        </table>
      </td></tr>
    </table>"""


# ── 1. SLA Breach Alert ───────────────────────────────────

async def send_breach_alert(ticket, worker_note: str,
                             manager_emails: Optional[list[str]] = None) -> bool:
    """
    Sent to: managers of the department + all admins.
    Triggered by: sla_engine.py when a ticket crosses its deadline.
    """
    to = list(set(manager_emails or []))
    if not to:
        print("   (breach alert skipped: no manager emails)")
        return False

    priority_val = ticket.priority.value if hasattr(ticket.priority, 'value') else str(ticket.priority)
    deadline_str = (
        ticket.sla_deadline.strftime("%d %b %Y, %H:%M UTC")
        if ticket.sla_deadline else "Not set"
    )

    body = f"""
    <p style="color:{NAVY};font-size:15px;margin:0 0 8px;">
      An incident has <strong style="color:{RED};">breached its SLA deadline</strong>
      and requires immediate attention.
    </p>
    <p style="color:#64748b;font-size:14px;margin:0 0 20px;">
      The ticket has been automatically escalated to
      <strong>Critical Review</strong> status.
    </p>

    {_ticket_info_block(ticket)}

    <table width="100%" cellpadding="0" cellspacing="0"
           style="background:#FEF2F2;border-radius:12px;
                  border:1px solid #FECACA;margin:0 0 20px;">
      <tr><td style="padding:16px 20px;">
        <p style="margin:0 0 8px;color:{RED};font-size:13px;font-weight:700;">
          ⏰ SLA Information
        </p>
        <p style="margin:0 0 4px;color:#7f1d1d;font-size:13px;">
          <strong>Deadline was:</strong> {deadline_str}
        </p>
        <p style="margin:0 0 4px;color:#7f1d1d;font-size:13px;">
          <strong>Worker impact:</strong> {worker_note}
        </p>
        <p style="margin:0;color:#7f1d1d;font-size:13px;">
          <strong>Priority:</strong> {priority_val.upper()}
        </p>
      </td></tr>
    </table>

    <p style="margin:0;color:#64748b;font-size:13px;">
      Please log in to the IMS Manager Console to reassign this ticket
      or change its status.
    </p>"""

    html = _base_template(RED, "🚨", "SLA Breach Detected", body)
    return await _send(to, f"🚨 SLA Breach — {ticket.title}", html)


# ── 2. Assignment Email ───────────────────────────────────

async def send_assignment_email(worker_email: str, worker_name: str,
                                 ticket) -> bool:
    """
    Sent to: the assigned worker.
    Triggered by: assignment_service.py (auto) and incidents.py (manual).
    """
    priority_val = ticket.priority.value if hasattr(ticket.priority, 'value') else str(ticket.priority)
    sla_hours = {
        "critical": "30 minutes",
        "high":     "2 hours",
        "medium":   "8 hours",
        "low":      "24 hours",
    }.get(priority_val, "24 hours")

    deadline_str = (
        ticket.sla_deadline.strftime("%d %b %Y, %H:%M UTC")
        if ticket.sla_deadline else "Will be set shortly"
    )

    body = f"""
    <p style="color:{NAVY};font-size:15px;margin:0 0 8px;">
      Hi <strong>{worker_name}</strong>,
    </p>
    <p style="color:#64748b;font-size:14px;margin:0 0 20px;">
      A new incident has been assigned to you.
      Please review and begin working on it within the SLA window.
    </p>

    {_ticket_info_block(ticket)}

    <table width="100%" cellpadding="0" cellspacing="0"
           style="background:#EFF8FF;border-radius:12px;
                  border:1px solid #BFDBFE;margin:0 0 20px;">
      <tr><td style="padding:16px 20px;">
        <p style="margin:0 0 8px;color:{BLUE};font-size:13px;font-weight:700;">
          ⏱ Your SLA Window
        </p>
        <p style="margin:0 0 4px;color:#1e3a5f;font-size:13px;">
          <strong>Resolution required within:</strong> {sla_hours}
        </p>
        <p style="margin:0;color:#1e3a5f;font-size:13px;">
          <strong>Deadline:</strong> {deadline_str}
        </p>
      </td></tr>
    </table>

    <p style="margin:0;color:#64748b;font-size:13px;">
      Missing this SLA will add penalty points to your account.
      Log in to the IMS Worker Dashboard to update your progress.
    </p>"""

    html = _base_template(BLUE, "📋", "New Incident Assigned to You", body)
    return await _send(
        [worker_email],
        f"📋 New Assignment — {ticket.title}",
        html
    )


# ── 3. Blacklist Alert ────────────────────────────────────

async def send_blacklist_alert(worker_email: str, worker_name: str,
                                penalty_points: int,
                                admin_emails: Optional[list[str]] = None) -> bool:
    """
    Sent to: the blacklisted worker AND all admins.
    Triggered by: sla_engine.py when penalty_points >= BLACKLIST_THRESHOLD.
    """
    to = list(set([worker_email] + (admin_emails or [])))

    body = f"""
    <p style="color:{NAVY};font-size:15px;margin:0 0 8px;">
      Worker <strong>{worker_name}</strong> has been
      <strong style="color:{RED};">automatically blacklisted</strong>
      after accumulating {penalty_points} penalty points.
    </p>
    <p style="color:#64748b;font-size:14px;margin:0 0 20px;">
      This worker will no longer receive automatic ticket assignments
      until an Administrator clears the blacklist.
    </p>

    <table width="100%" cellpadding="0" cellspacing="0"
           style="background:#FEF2F2;border-radius:12px;
                  border:1px solid #FECACA;margin:0 0 20px;">
      <tr><td style="padding:20px;">
        <p style="margin:0 0 8px;color:{RED};font-size:13px;font-weight:700;">
          ⛔ Blacklist Summary
        </p>
        <p style="margin:0 0 4px;color:#7f1d1d;font-size:13px;">
          <strong>Worker:</strong> {worker_name}
        </p>
        <p style="margin:0 0 4px;color:#7f1d1d;font-size:13px;">
          <strong>Email:</strong> {worker_email}
        </p>
        <p style="margin:0 0 4px;color:#7f1d1d;font-size:13px;">
          <strong>Total penalty points:</strong> {penalty_points}
        </p>
        <p style="margin:0;color:#7f1d1d;font-size:13px;">
          <strong>Action required:</strong> Admin must clear blacklist via
          POST /admin/workers/&#123;id&#125;/clear-blacklist
        </p>
      </td></tr>
    </table>

    <p style="margin:0;color:#64748b;font-size:13px;">
      Admins can clear this blacklist from the Manager Console →
      Worker Accountability table.
    </p>"""

    html = _base_template(RED, "⛔", f"Worker Blacklisted — {worker_name}", body)
    return await _send(
        to,
        f"⛔ Worker Blacklisted — {worker_name} ({penalty_points} pts)",
        html
    )


# ── 4. Escalation Alert ───────────────────────────────────

async def send_escalation_alert(ticket, recipient_emails: list[str]) -> bool:
    """
    Sent to: all managers + admins.
    Triggered by: sla_engine.py when ticket → CRITICAL_REVIEW.
    """
    escalated_str = (
        ticket.escalated_at.strftime("%d %b %Y, %H:%M UTC")
        if ticket.escalated_at else "Just now"
    )
    assignee_name = (
        ticket.assignee.full_name
        if ticket.assignee else "Unassigned"
    )

    body = f"""
    <p style="color:{NAVY};font-size:15px;margin:0 0 8px;">
      A ticket has been <strong style="color:{AMBER};">escalated to Critical Review</strong>
      due to an SLA breach.
    </p>
    <p style="color:#64748b;font-size:14px;margin:0 0 20px;">
      Immediate management intervention is required.
    </p>

    {_ticket_info_block(ticket)}

    <table width="100%" cellpadding="0" cellspacing="0"
           style="background:#FFFBEB;border-radius:12px;
                  border:1px solid #FDE68A;margin:0 0 20px;">
      <tr><td style="padding:16px 20px;">
        <p style="margin:0 0 8px;color:{AMBER};font-size:13px;font-weight:700;">
          ⚠️ Escalation Details
        </p>
        <p style="margin:0 0 4px;color:#78350f;font-size:13px;">
          <strong>Escalated at:</strong> {escalated_str}
        </p>
        <p style="margin:0;color:#78350f;font-size:13px;">
          <strong>Previously assigned to:</strong> {assignee_name}
        </p>
      </td></tr>
    </table>

    <p style="margin:0;color:#64748b;font-size:13px;">
      Please log in to the Manager Console to reassign or resolve this ticket.
    </p>"""

    html = _base_template(AMBER, "⚠️", "Ticket Escalated to Critical Review", body)
    return await _send(
        recipient_emails,
        f"⚠️ Escalation Alert — {ticket.title}",
        html
    )


# ── 5. Emergency Alert ────────────────────────────────────

async def send_emergency_alert(ticket, recipient_emails: list[str]) -> bool:
    """
    Sent to: all managers + admins immediately.
    Triggered by: emergency.py when POST /emergency/escalate is called.
    """
    body = f"""
    <p style="color:{NAVY};font-size:15px;margin:0 0 8px;">
      <strong style="color:{RED};">An emergency incident has been reported</strong>
      and requires immediate attention.
    </p>
    <p style="color:#64748b;font-size:14px;margin:0 0 20px;">
      This ticket has been automatically created with
      <strong>CRITICAL</strong> priority. SLA window is
      <strong>30 minutes</strong>.
    </p>

    {_ticket_info_block(ticket)}

    <table width="100%" cellpadding="0" cellspacing="0"
           style="background:#FEF2F2;border-radius:12px;
                  border:2px solid {RED};margin:0 0 20px;">
      <tr><td style="padding:20px;">
        <p style="margin:0 0 8px;color:{RED};font-size:14px;font-weight:700;">
          🆘 Emergency Response Required
        </p>
        <p style="margin:0 0 4px;color:#7f1d1d;font-size:13px;">
          <strong>Location:</strong>
          {ticket.location or "Not specified"}
        </p>
        <p style="margin:0 0 4px;color:#7f1d1d;font-size:13px;">
          <strong>Type:</strong>
          {(ticket.category or "Emergency").replace('emergency_','').replace('_',' ').title()}
        </p>
        <p style="margin:0;color:#7f1d1d;font-size:14px;font-weight:600;">
          Please assign a worker immediately via the Manager Console.
        </p>
      </td></tr>
    </table>"""

    html = _base_template(RED, "🆘", "EMERGENCY Incident Reported", body)
    return await _send(
        recipient_emails,
        f"🆘 EMERGENCY — {ticket.title}",
        html
    )


# ── DB helpers ────────────────────────────────────────────

async def get_manager_emails_for_dept(department: str, db) -> list[str]:
    """Get email addresses of all managers in a department."""
    from sqlalchemy import select
    from app.models.user import User, UserRole
    result = await db.execute(
        select(User.email).where(
            User.role == UserRole.MANAGER,
            User.department == department,
            User.is_active == True,           # noqa: E712
        )
    )
    return [row[0] for row in result.all()]


async def get_admin_emails(db) -> list[str]:
    """Get email addresses of all active admins."""
    from sqlalchemy import select
    from app.models.user import User, UserRole
    result = await db.execute(
        select(User.email).where(
            User.role == UserRole.ADMIN,
            User.is_active == True,           # noqa: E712
        )
    )
    return [row[0] for row in result.all()]


async def get_all_manager_and_admin_emails(db) -> list[str]:
    """Get emails of all managers + admins (for emergency/escalation alerts)."""
    from sqlalchemy import select
    from app.models.user import User, UserRole
    result = await db.execute(
        select(User.email).where(
            User.role.in_([UserRole.MANAGER, UserRole.ADMIN]),
            User.is_active == True,           # noqa: E712
        )
    )
    return list(set(row[0] for row in result.all()))


# ── 6. Uncategorized Ticket Alert ─────────────────────────

async def send_uncategorized_alert(ticket, recipient_emails: list[str],
                                    ml_confidence: float = 0.0) -> bool:
    """
    Sent to: all managers + admins immediately on ticket creation.
    Triggered by: ticket_service.py when ML confidence is below threshold.
    """
    priority_val = ticket.priority.value if hasattr(ticket.priority, 'value') else str(ticket.priority)
    conf_pct     = f"{ml_confidence:.0%}"

    body = f"""
    <p style="color:{NAVY};font-size:15px;margin:0 0 8px;">
      A new incident was reported but the
      <strong style="color:{AMBER};">ML engine could not confidently
      determine its department</strong> (confidence: {conf_pct},
      threshold: 60%).
    </p>
    <p style="color:#64748b;font-size:14px;margin:0 0 20px;">
      This ticket is sitting <strong>OPEN with no worker assigned</strong>
      and no SLA clock running. Manual review is required immediately.
    </p>

    {_ticket_info_block(ticket)}

    <table width="100%" cellpadding="0" cellspacing="0"
           style="background:#FFFBEB;border-radius:12px;
                  border:1px solid #FDE68A;margin:0 0 20px;">
      <tr><td style="padding:16px 20px;">
        <p style="margin:0 0 8px;color:{AMBER};font-size:13px;font-weight:700;">
          ⚠️ Why this happened
        </p>
        <p style="margin:0 0 4px;color:#78350f;font-size:13px;">
          <strong>ML confidence score:</strong> {conf_pct}
          (minimum required: 60%)
        </p>
        <p style="margin:0 0 4px;color:#78350f;font-size:13px;">
          <strong>Ticket status:</strong> OPEN — no worker assigned
        </p>
        <p style="margin:0;color:#78350f;font-size:13px;">
          <strong>SLA clock:</strong> Not started — will begin after assignment
        </p>
      </td></tr>
    </table>

    <table width="100%" cellpadding="0" cellspacing="0"
           style="background:#F0FDF4;border-radius:12px;
                  border:1px solid #BBF7D0;margin:0 0 8px;">
      <tr><td style="padding:16px 20px;">
        <p style="margin:0 0 8px;color:#15803d;font-size:13px;font-weight:700;">
          ✅ How to fix this
        </p>
        <p style="margin:0 0 4px;color:#14532d;font-size:13px;">
          In Swagger or Manager Console, call:
        </p>
        <code style="display:block;background:#dcfce7;padding:8px 12px;
                     border-radius:8px;color:#166534;font-size:12px;
                     margin-top:6px;">
          Ticket #{getattr(ticket, 'ticket_number', None) or str(ticket.id)[:8]} — PATCH /tickets/&#123;id&#125;/category
          &#123; "category": "it" &#125;
        </code>
        <p style="margin:8px 0 0;color:#14532d;font-size:12px;">
          This sets the department and automatically assigns the ticket
          to an available worker.
        </p>
      </td></tr>
    </table>"""

    html = _base_template(AMBER, "⚠️", "Uncategorized Incident Needs Review", body)
    return await _send(
        recipient_emails,
        f"⚠️ Uncategorized Incident — {ticket.title}",
        html
    )


# ── 7. High Priority Alert ────────────────────────────────

async def send_high_priority_alert(ticket, recipient_emails: list[str],
                                    assignee_name: str | None = None) -> bool:
    """
    Sent to: department manager(s) + all admins when a HIGH or CRITICAL
    ticket is created and auto-assigned to its single lead worker.

    Triggered by: assignment_service.py immediately after the lead
    worker is assigned. This is a notification, not an assignment
    request — the lead worker is already assigned and the SLA clock
    is already running.
    """
    priority_val = ticket.priority.value if hasattr(ticket.priority, 'value') else str(ticket.priority)
    is_critical  = priority_val == "critical"
    header_color = RED if is_critical else "#ea580c"
    icon         = "🚨" if is_critical else "⚡"

    sla_window = {
        "critical": "30 minutes",
        "high":     "2 hours",
    }.get(priority_val, "2 hours")

    dept_label = (ticket.category or "Unknown").replace("_", " ").title()
    lead_line  = f"<strong>{assignee_name}</strong>" if assignee_name else "a worker"

    body = f"""
    <p style="color:{NAVY};font-size:15px;margin:0 0 8px;">
      A <strong style="color:{header_color};">{priority_val.upper()} priority</strong>
      incident has been reported and was <strong>automatically assigned</strong>
      to {lead_line} in {dept_label}.
    </p>
    <p style="color:#64748b;font-size:14px;margin:0 0 20px;">
      This is a notification only — no action is required unless the lead
      worker needs to delegate help or you want to monitor progress.
      The SLA clock is already running with a <strong>{sla_window}</strong> window.
    </p>

    {_ticket_info_block(ticket)}

    <table width="100%" cellpadding="0" cellspacing="0"
           style="background:#FEF2F2;border-radius:12px;
                  border:2px solid {header_color};margin:0 0 20px;">
      <tr><td style="padding:20px;">
        <p style="margin:0 0 10px;color:{header_color};font-size:14px;font-weight:700;">
          {icon} Assignment Summary
        </p>
        <p style="margin:0 0 6px;color:#7f1d1d;font-size:13px;">
          <strong>Lead worker:</strong> {assignee_name or 'See ticket details'}
        </p>
        <p style="margin:0 0 6px;color:#7f1d1d;font-size:13px;">
          <strong>Department:</strong> {dept_label}
        </p>
        <p style="margin:0;color:#7f1d1d;font-size:13px;">
          <strong>SLA window:</strong> {sla_window}
        </p>
      </td></tr>
    </table>

    <p style="margin:0;color:#64748b;font-size:13px;">
      The lead worker can delegate additional helpers if needed — you'll be
      notified by email if that happens.
    </p>"""

    html = _base_template(header_color, icon,
                          f"{priority_val.upper()} Incident Auto-Assigned",
                          body)
    return await _send(
        recipient_emails,
        f"{icon} {priority_val.upper()} Incident — {ticket.title}",
        html
    )


# ── 8. Delegation Alert ───────────────────────────────────

async def send_delegation_alert(ticket, lead_name: str, helper_name: str,
                                 helper_email: str, recipient_emails: list[str]) -> bool:
    """
    Sent to: the new helper + the lead worker (confirmation) + admin.
    Triggered by: worker.py when a lead worker delegates part of their
    HIGH/CRITICAL ticket to a helper.
    """
    priority_val = ticket.priority.value if hasattr(ticket.priority, 'value') else str(ticket.priority)
    dept_label   = (ticket.category or "Unknown").replace("_", " ").title()

    body = f"""
    <p style="color:{NAVY};font-size:15px;margin:0 0 8px;">
      <strong>{lead_name}</strong> has delegated help on a
      <strong style="color:{RED if priority_val=='critical' else '#ea580c'};">
        {priority_val.upper()}
      </strong> incident to <strong>{helper_name}</strong>.
    </p>
    <p style="color:#64748b;font-size:14px;margin:0 0 20px;">
      {helper_name} is now an additional assignee on this ticket and will
      receive the same priority-queue treatment — their lower-priority
      work will pause while they assist.
    </p>

    {_ticket_info_block(ticket)}

    <table width="100%" cellpadding="0" cellspacing="0"
           style="background:#EFF8FF;border-radius:12px;
                  border:1px solid #BFDBFE;margin:0 0 20px;">
      <tr><td style="padding:16px 20px;">
        <p style="margin:0 0 8px;color:{BLUE};font-size:13px;font-weight:700;">
          👥 Delegation Details
        </p>
        <p style="margin:0 0 4px;color:#1e3a5f;font-size:13px;">
          <strong>Lead worker:</strong> {lead_name}
        </p>
        <p style="margin:0 0 4px;color:#1e3a5f;font-size:13px;">
          <strong>Helper assigned:</strong> {helper_name}
        </p>
        <p style="margin:0;color:#1e3a5f;font-size:13px;">
          <strong>Department:</strong> {dept_label}
        </p>
      </td></tr>
    </table>

    <p style="margin:0;color:#64748b;font-size:13px;">
      Note: penalty points for an SLA breach apply to both the lead worker
      and the delegated helper.
    </p>"""

    html = _base_template(BLUE, "👥", "Helper Delegated to Incident", body)
    return await _send(
        recipient_emails,
        f"👥 Helper Delegated — {ticket.title}",
        html
    )


# ── 9. Completion Confirmation ────────────────────────────

async def send_completion_email(ticket, resolved_by_name: str,
                                 recipient_emails: list[str]) -> bool:
    """
    Sent to: department manager(s) + all admins.
    Triggered by: worker.py the moment a ticket is marked RESOLVED.
    Confirms the incident is closed out for management visibility.
    """
    priority_val = ticket.priority.value if hasattr(ticket.priority, 'value') else str(ticket.priority)
    dept_label   = (ticket.category or "Unknown").replace("_", " ").title()

    resolved_str = (
        ticket.resolved_at.strftime("%d %b %Y, %H:%M UTC")
        if ticket.resolved_at else "Just now"
    )
    created_str = (
        ticket.created_at.strftime("%d %b %Y, %H:%M UTC")
        if ticket.created_at else "—"
    )

    body = f"""
    <p style="color:{NAVY};font-size:15px;margin:0 0 8px;">
      The following incident has been
      <strong style="color:{GREEN};">marked as resolved</strong>
      by <strong>{resolved_by_name}</strong>.
    </p>
    <p style="color:#64748b;font-size:14px;margin:0 0 20px;">
      This is a confirmation notice — no action is required.
    </p>

    {_ticket_info_block(ticket)}

    <table width="100%" cellpadding="0" cellspacing="0"
           style="background:#F0FDF4;border-radius:12px;
                  border:1px solid #BBF7D0;margin:0 0 20px;">
      <tr><td style="padding:16px 20px;">
        <p style="margin:0 0 8px;color:#15803d;font-size:13px;font-weight:700;">
          ✅ Resolution Summary
        </p>
        <p style="margin:0 0 4px;color:#14532d;font-size:13px;">
          <strong>Resolved by:</strong> {resolved_by_name}
        </p>
        <p style="margin:0 0 4px;color:#14532d;font-size:13px;">
          <strong>Department:</strong> {dept_label}
        </p>
        <p style="margin:0 0 4px;color:#14532d;font-size:13px;">
          <strong>Reported:</strong> {created_str}
        </p>
        <p style="margin:0;color:#14532d;font-size:13px;">
          <strong>Resolved:</strong> {resolved_str}
        </p>
      </td></tr>
    </table>"""

    html = _base_template(GREEN, "✅", "Incident Resolved", body)
    return await _send(
        recipient_emails,
        f"✅ Resolved — {ticket.title}",
        html
    )


# ── 10. Help Request — submitted (to manager + admin) ─────

async def send_help_request_alert(ticket, lead_name: str, helper_name: str,
                                   recipient_emails: list[str]) -> bool:
    """
    Sent to: department manager(s) + admin when a lead worker requests
    a helper on their HIGH/CRITICAL ticket. Nothing is attached yet —
    this is an approval request.
    """
    priority_val = ticket.priority.value if hasattr(ticket.priority, 'value') else str(ticket.priority)
    dept_label   = (ticket.category or "Unknown").replace("_", " ").title()

    body = f"""
    <p style="color:{NAVY};font-size:15px;margin:0 0 8px;">
      <strong>{lead_name}</strong> is requesting help from
      <strong>{helper_name}</strong> on a
      <strong style="color:{RED if priority_val=='critical' else '#ea580c'};">{priority_val.upper()}</strong>
      incident and needs your approval.
    </p>
    <p style="color:#64748b;font-size:14px;margin:0 0 20px;">
      The helper will <strong>not</strong> be added to this ticket until you approve.
      The lead worker continues working alone in the meantime.
    </p>

    {_ticket_info_block(ticket)}

    <table width="100%" cellpadding="0" cellspacing="0"
           style="background:#EFF8FF;border-radius:12px;
                  border:1px solid #BFDBFE;margin:0 0 20px;">
      <tr><td style="padding:16px 20px;">
        <p style="margin:0 0 8px;color:{BLUE};font-size:13px;font-weight:700;">
          🙋 Help Request Pending Your Decision
        </p>
        <p style="margin:0 0 4px;color:#1e3a5f;font-size:13px;">
          <strong>Lead worker:</strong> {lead_name}
        </p>
        <p style="margin:0 0 4px;color:#1e3a5f;font-size:13px;">
          <strong>Proposed helper:</strong> {helper_name}
        </p>
        <p style="margin:0;color:#1e3a5f;font-size:13px;">
          <strong>Department:</strong> {dept_label}
        </p>
      </td></tr>
    </table>

    <p style="margin:0;color:#64748b;font-size:13px;">
      Open the Manager Console → Help Requests tab to approve or deny.
    </p>"""

    html = _base_template(BLUE, "🙋", "Help Request Awaiting Approval", body)
    return await _send(
        recipient_emails,
        f"🙋 Help Request — {ticket.title}",
        html
    )


# ── 11. Help Request — approved (to helper + lead + admin) ─

async def send_help_request_approved(ticket, lead_name: str, helper_name: str,
                                      approver_name: str,
                                      recipient_emails: list[str]) -> bool:
    """
    Sent to: the helper + lead worker (confirmation) + admin.
    Triggered the moment a manager/admin approves a pending help request.
    """
    dept_label = (ticket.category or "Unknown").replace("_", " ").title()

    body = f"""
    <p style="color:{NAVY};font-size:15px;margin:0 0 8px;">
      <strong>{approver_name}</strong> has
      <strong style="color:{GREEN};">approved</strong> the request for
      <strong>{helper_name}</strong> to help <strong>{lead_name}</strong>
      on this incident.
    </p>
    <p style="color:#64748b;font-size:14px;margin:0 0 20px;">
      {helper_name} is now an active assignee on this ticket. If they had
      lower-priority work in progress, it has been paused while they assist.
    </p>

    {_ticket_info_block(ticket)}

    <table width="100%" cellpadding="0" cellspacing="0"
           style="background:#F0FDF4;border-radius:12px;
                  border:1px solid #BBF7D0;margin:0 0 20px;">
      <tr><td style="padding:16px 20px;">
        <p style="margin:0 0 8px;color:#15803d;font-size:13px;font-weight:700;">
          ✅ Approved
        </p>
        <p style="margin:0 0 4px;color:#14532d;font-size:13px;">
          <strong>Helper:</strong> {helper_name}
        </p>
        <p style="margin:0 0 4px;color:#14532d;font-size:13px;">
          <strong>Lead worker:</strong> {lead_name}
        </p>
        <p style="margin:0 0 4px;color:#14532d;font-size:13px;">
          <strong>Approved by:</strong> {approver_name}
        </p>
        <p style="margin:0;color:#14532d;font-size:13px;">
          <strong>Department:</strong> {dept_label}
        </p>
      </td></tr>
    </table>

    <p style="margin:0;color:#64748b;font-size:13px;">
      Note: if this ticket breaches its SLA, penalty points apply to both
      the lead worker and the approved helper.
    </p>"""

    html = _base_template(GREEN, "✅", "Help Request Approved", body)
    return await _send(
        recipient_emails,
        f"✅ Help Approved — {ticket.title}",
        html
    )


# ── 12. Help Request — denied (to lead worker) ─────────────

async def send_help_request_denied(ticket, lead_name: str, helper_name: str,
                                    decider_name: str, notes: str | None,
                                    recipient_emails: list[str]) -> bool:
    """Sent to: the lead worker who requested help. Confirms the denial."""
    dept_label = (ticket.category or "Unknown").replace("_", " ").title()
    note_line  = f'<p style="margin:8px 0 0;color:#7f1d1d;font-size:13px;"><strong>Note:</strong> {notes}</p>' if notes else ""

    body = f"""
    <p style="color:{NAVY};font-size:15px;margin:0 0 8px;">
      <strong>{decider_name}</strong> has
      <strong style="color:{RED};">denied</strong> your request for
      <strong>{helper_name}</strong> to help on this incident.
    </p>
    <p style="color:#64748b;font-size:14px;margin:0 0 20px;">
      You remain the sole assignee on this ticket. You can submit a new
      help request with a different teammate if needed.
    </p>

    {_ticket_info_block(ticket)}

    <table width="100%" cellpadding="0" cellspacing="0"
           style="background:#FEF2F2;border-radius:12px;
                  border:1px solid #FECACA;margin:0 0 20px;">
      <tr><td style="padding:16px 20px;">
        <p style="margin:0 0 8px;color:{RED};font-size:13px;font-weight:700;">
          ⛔ Request Denied
        </p>
        <p style="margin:0 0 4px;color:#7f1d1d;font-size:13px;">
          <strong>Requested helper:</strong> {helper_name}
        </p>
        <p style="margin:0;color:#7f1d1d;font-size:13px;">
          <strong>Denied by:</strong> {decider_name}
        </p>
        {note_line}
      </td></tr>
    </table>"""

    html = _base_template(RED, "⛔", "Help Request Denied", body)
    return await _send(
        recipient_emails,
        f"⛔ Help Request Denied — {ticket.title}",
        html
    )

# ── 14. Hold Request — awaiting approval (to managers + admin) ─

async def send_hold_request_alert(ticket, blocking_ticket, worker_name: str,
                                   remark: str, recipient_emails: list[str]) -> bool:
    """
    Sent to: department manager(s) + admin when a worker requests
    permission to PAUSE a ticket because a higher-priority ticket
    landed on them. Nothing is paused yet — the SLA keeps running
    until this is approved AND the worker clicks Hold.
    """
    priority_val = ticket.priority.value if hasattr(ticket.priority, 'value') else str(ticket.priority)
    blk_priority = blocking_ticket.priority.value if hasattr(blocking_ticket.priority, 'value') else str(blocking_ticket.priority)
    blk_number   = getattr(blocking_ticket, 'ticket_number', None) or str(blocking_ticket.id)[:8]
    dept_label   = (ticket.category or "Unknown").replace("_", " ").title()

    body = f"""
    <p style="color:{NAVY};font-size:15px;margin:0 0 8px;">
      <strong>{worker_name}</strong> is asking to put a
      <strong>{priority_val.upper()}</strong> ticket
      <strong style="color:{AMBER};">ON HOLD</strong> while they handle
      <strong style="color:{RED};">{blk_priority.upper()}</strong> ticket
      <strong>#{blk_number}</strong>.
    </p>
    <p style="color:#64748b;font-size:14px;margin:0 0 20px;">
      The SLA clock on the ticket below is <strong>still running</strong>.
      It only freezes after you approve and the worker executes the hold.
      When the higher-priority ticket is resolved, the hold auto-releases
      and the SLA resumes from the saved remaining time.
    </p>

    {_ticket_info_block(ticket)}

    <table width="100%" cellpadding="0" cellspacing="0"
           style="background:#FFFBEB;border-radius:12px;
                  border:1px solid #FDE68A;margin:0 0 20px;">
      <tr><td style="padding:16px 20px;">
        <p style="margin:0 0 8px;color:{AMBER};font-size:13px;font-weight:700;">
          ⏸️ Hold Request Pending Your Decision
        </p>
        <p style="margin:0 0 4px;color:#78350f;font-size:13px;">
          <strong>Worker:</strong> {worker_name}
        </p>
        <p style="margin:0 0 4px;color:#78350f;font-size:13px;">
          <strong>Blocking ticket:</strong> #{blk_number} — {blocking_ticket.title} ({blk_priority.upper()})
        </p>
        <p style="margin:0 0 4px;color:#78350f;font-size:13px;">
          <strong>Department:</strong> {dept_label}
        </p>
        <p style="margin:0;color:#78350f;font-size:13px;">
          <strong>Worker's remark:</strong> “{remark}”
        </p>
      </td></tr>
    </table>

    <p style="margin:0;color:#64748b;font-size:13px;">
      Open the Manager Console → Hold Requests tab to approve or deny.
    </p>"""

    html = _base_template(AMBER, "⏸️", "Hold Request Awaiting Approval", body)
    return await _send(
        recipient_emails,
        f"⏸️ Hold Request — {ticket.title}",
        html
    )


# ── 15. Hold Request — approved (to the worker) ───────────

async def send_hold_request_approved(ticket, worker_name: str,
                                      approver_name: str,
                                      recipient_emails: list[str]) -> bool:
    """
    Sent to: the worker the moment a manager/admin approves their hold
    request. The ticket is NOT paused yet — this tells them their Hold
    button is now active.
    """
    body = f"""
    <p style="color:{NAVY};font-size:15px;margin:0 0 8px;">
      <strong>{approver_name}</strong> has
      <strong style="color:{GREEN};">approved</strong> your hold request,
      {worker_name}.
    </p>
    <p style="color:#64748b;font-size:14px;margin:0 0 20px;">
      <strong>Important:</strong> the ticket below is still running.
      Open your Worker Dashboard and click <strong>Hold</strong> on it to
      actually pause the SLA clock. It will auto-resume when the
      higher-priority ticket is resolved.
    </p>

    {_ticket_info_block(ticket)}

    <table width="100%" cellpadding="0" cellspacing="0"
           style="background:#F0FDF4;border-radius:12px;
                  border:1px solid #BBF7D0;margin:0 0 20px;">
      <tr><td style="padding:16px 20px;">
        <p style="margin:0;color:#166534;font-size:13px;">
          ✅ Your <strong>Hold</strong> button is now active on this ticket.
        </p>
      </td></tr>
    </table>"""

    html = _base_template(GREEN, "✅", "Hold Request Approved", body)
    return await _send(
        recipient_emails,
        f"✅ Hold Approved — {ticket.title}",
        html
    )


# ── 16. Hold Request — denied (to the worker) ─────────────

async def send_hold_request_denied(ticket, worker_name: str,
                                    decider_name: str, notes,
                                    recipient_emails: list[str]) -> bool:
    """
    Sent to: the worker when a manager/admin denies their hold request.
    The ticket's SLA clock keeps running — they must manage both tickets.
    """
    notes_html = f"""
        <p style="margin:8px 0 0;color:#7f1d1d;font-size:13px;">
          <strong>Reason:</strong> “{notes}”
        </p>""" if notes else ""

    body = f"""
    <p style="color:{NAVY};font-size:15px;margin:0 0 8px;">
      <strong>{decider_name}</strong> has
      <strong style="color:{RED};">denied</strong> your hold request,
      {worker_name}.
    </p>
    <p style="color:#64748b;font-size:14px;margin:0 0 20px;">
      The SLA clock on the ticket below <strong>continues to run</strong>.
      Both of your tickets remain active — plan your time accordingly, or
      speak to your manager if the workload isn't feasible.
    </p>

    {_ticket_info_block(ticket)}

    <table width="100%" cellpadding="0" cellspacing="0"
           style="background:#FEF2F2;border-radius:12px;
                  border:1px solid #FECACA;margin:0 0 20px;">
      <tr><td style="padding:16px 20px;">
        <p style="margin:0;color:#7f1d1d;font-size:13px;">
          ❌ Hold not granted — SLA enforcement stays active on this ticket.
        </p>{notes_html}
      </td></tr>
    </table>"""

    html = _base_template(RED, "❌", "Hold Request Denied", body)
    return await _send(
        recipient_emails,
        f"❌ Hold Denied — {ticket.title}",
        html
    )
