import asyncio
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_, text

from app.config import settings
from app.models.ticket import Ticket, TicketStatus, TicketPriority
from app.models.audit_log import AuditLog, AuditAction
from app.schemas.ticket import TicketCreate
from app.services.categorizer import categorize   # text ML

# ── SLA hours per priority ────────────────────────────────
SLA_HOURS: dict[TicketPriority, float] = {
    TicketPriority.LOW:      settings.SLA_LOW_RESPONSE_HRS,
    TicketPriority.MEDIUM:   settings.SLA_MEDIUM_RESPONSE_HRS,
    TicketPriority.HIGH:     settings.SLA_HIGH_RESPONSE_HRS,
    TicketPriority.CRITICAL: settings.SLA_CRITICAL_RESPONSE_HRS,
}

# Confidence thresholds
TEXT_THRESHOLD  = settings.ML_CONFIDENCE_THRESHOLD   # 0.6
# Raised from 0.15 → 0.35. ImageNet is broad and often maps CCTV
# cameras / PA equipment / metal enclosures to the wrong keywords
# (loudspeaker → marketing, bulletproof_vest → security). We now
# require the image ML to be substantially confident before it's
# allowed to steer routing at all.
IMAGE_THRESHOLD = 0.35
# When text and image disagree, image only wins if it's this much
# more confident than text. Keeps the more reliable text ML in
# charge for typical tickets while still leaving room for image ML
# to correct clearly wrong text predictions.
IMAGE_OVERRIDE_MARGIN = 1.5


async def next_ticket_number(db: AsyncSession) -> str:
    """
    Fetch the next 8-digit ticket number from the Postgres sequence.
    Sequence starts at 10000001, so every value is exactly 8 digits and
    guaranteed unique even under concurrent ticket creation (sequences
    are atomic — two simultaneous requests can never get the same number).
    """
    result = await db.execute(text("SELECT nextval('ticket_number_seq')"))
    return str(result.scalar())


def compute_sla_deadline(ticket: Ticket) -> datetime | None:
    """SLA clock starts at assigned_at. Returns None if not yet assigned."""
    if not ticket.assigned_at:
        return None
    hours = SLA_HOURS.get(ticket.priority, 24.0)
    return ticket.assigned_at + timedelta(hours=hours)


# ── Dual-engine combiner ──────────────────────────────────

def _combine_ml_results(
    text_category:   Optional[str],
    text_confidence: float,
    image_result:    Optional[dict],
) -> tuple[Optional[str], float, str]:
    """
    Merge text ML + image ML into one final department decision.

    Returns: (final_category, final_confidence, method)

    method values (written to audit log so you can see exactly what happened):
      both_engines_agree    → text and image picked the same dept → boosted confidence
      text_overrides_image  → both ran, text had higher confidence
      image_overrides_text  → both ran, image had higher confidence
      text_only             → only text ML had a valid result
      image_only            → text failed/low-confidence, image recovered it
      uncategorized         → both failed → managers alerted
    """
    image_category   = None
    image_confidence = 0.0

    if image_result and image_result.get("success"):
        image_category   = image_result.get("department")
        image_confidence = float(image_result.get("confidence", 0.0))

    text_valid  = (text_category  is not None) and (text_confidence  >= TEXT_THRESHOLD)
    image_valid = (image_category is not None) and (image_confidence >= IMAGE_THRESHOLD)

    if text_valid and image_valid:
        if text_category == image_category:
            # ── Both agree ── boost confidence slightly.
            # Text ML is more reliable (custom-trained on incident text),
            # so we weight it heavier in the blended confidence.
            combined = min(1.0, text_confidence * 0.75 + image_confidence * 0.25 + 0.05)
            return text_category, round(combined, 4), "both_engines_agree"
        else:
            # ── Disagree ── text wins by default. Image only overrides
            # if it's IMAGE_OVERRIDE_MARGIN× more confident than text —
            # e.g. text 0.62 vs image 0.94 flips (0.94 ≥ 1.5 × 0.62=0.93).
            # This prevents a weak 0.35 image guess from beating a solid
            # 0.7 text prediction, which was the old failure mode.
            if image_confidence >= text_confidence * IMAGE_OVERRIDE_MARGIN:
                return image_category, image_confidence, "image_overrides_text"
            else:
                return text_category, text_confidence, "text_overrides_image"

    elif text_valid:
        return text_category, text_confidence, "text_only"

    elif image_valid:
        return image_category, image_confidence, "image_only"

    else:
        best_conf = max(text_confidence, image_confidence)
        return None, round(best_conf, 4), "uncategorized"


def _build_audit_note(
    text_category:   Optional[str],
    text_confidence: float,
    image_result:    Optional[dict],
    final_category:  Optional[str],
    final_confidence: float,
    method:          str,
) -> str:
    """Build a detailed audit note showing exactly what each engine contributed."""
    parts = [
        f"Final: {final_category or 'needs_review'} ({final_confidence:.2f}) via [{method}]",
        f"Text ML: {text_category or 'none'} ({text_confidence:.2f})",
    ]
    if image_result:
        img_dept = image_result.get("department") or "none"
        img_conf = image_result.get("confidence", 0.0)
        img_top  = image_result.get("raw_top_prediction") or "—"
        parts.append(
            f"Image ML: {img_dept} ({img_conf:.2f}) "
            f"[top label: {img_top}]"
        )
    return "  |  ".join(parts)


# ── Background task helpers ───────────────────────────────

async def _alert_managers_uncategorized(ticket: Ticket, db: AsyncSession, confidence: float):
    """Email all managers + admins when neither ML engine could categorize."""
    try:
        from app.services.email_service import (
            send_uncategorized_alert,
            get_all_manager_and_admin_emails,
        )
        
        # Fetch emails using the ACTIVE session to avoid transaction isolation bugs
        recipients = await get_all_manager_and_admin_emails(db)
        
        if recipients:
            # Fire the SMTP thread in the background so we don't block the API response
            # Safe because it only uses the in-memory ticket object, not the DB
            asyncio.create_task(
                send_uncategorized_alert(
                    ticket=ticket,
                    recipient_emails=recipients,
                    ml_confidence=confidence,
                )
            )
    except Exception as e:
        print(f"   (uncategorized alert skipped: {e})")


# ── create_ticket ─────────────────────────────────────────

async def create_ticket(
    data: TicketCreate,
    reporter_id: uuid.UUID,
    db: AsyncSession,
    image_path: Optional[str] = None,
) -> Ticket:
    """
    Dual-engine ML pipeline:

    1. Text ML (scikit-learn) → category + confidence (synchronous, fast)
    2. Image ML (TensorFlow)  → category + confidence (synchronous, awaited)
    3. Combine both results   → final_category using _combine_ml_results()
    4. Create ticket with final category
    5. Route ticket (auto-assign Low/Medium, leave High/Critical for manager)
    6. Fire background email alerts:
       - HIGH/CRITICAL → alert managers + admins immediately
       - Uncategorized → alert managers to review manually
    """

    # ── Step 1: Text ML ───────────────────────────────────
    text = f"{data.title}. {data.description}"
    text_category, text_confidence = categorize(text)
    print(f"   📝 Text ML: {text_category or 'none'} ({text_confidence:.2f})")

    # ── Step 2: Image ML (run synchronously so we can combine) ──
    image_result = None
    if image_path:
        try:
            from app.services.image_analyzer import analyze_image
            image_result = await analyze_image(image_path)   # await here — not background
            img_dept = image_result.get("department") or "none"
            img_conf = image_result.get("confidence", 0)
            print(f"   🧠 Image ML: {img_dept} ({img_conf:.2f})")
        except Exception as e:
            print(f"   ⚠️  Image ML skipped: {e}")
            image_result = None

    # ── Step 3: Combine both results ──────────────────────
    final_category, final_confidence, method = _combine_ml_results(
        text_category, text_confidence, image_result
    )
    print(f"   ✅ Combined: {final_category or 'needs_review'} ({final_confidence:.2f}) via [{method}]")

    # ── Step 4: Create ticket ─────────────────────────────
    ticket = Ticket(
        ticket_number=await next_ticket_number(db),
        title=data.title,
        description=data.description,
        priority=data.priority,
        status=TicketStatus.OPEN,
        location=data.location,
        terminal=data.terminal,
        equipment_model=data.equipment_model,
        device_type=data.device_type,
        reporter_id=reporter_id,
        image_path=image_path,
        # Combined ML result
        category=final_category,
        ml_confidence=final_confidence,
        # Raw image analysis stored separately for the audit endpoint
        image_analysis_notes=(
            image_result.get("description") if image_result else None
        ),
        image_analysis_department=(
            image_result.get("department") if image_result else None
        ),
        image_analysis_confidence=(
            image_result.get("confidence") if image_result else None
        ),
    )
    db.add(ticket)
    await db.flush()

    # ── Audit: record what both engines decided ───────────
    audit_note = _build_audit_note(
        text_category, text_confidence,
        image_result,
        final_category, final_confidence, method,
    )
    db.add(AuditLog(
        ticket_id=ticket.id,
        user_id=reporter_id,
        action=AuditAction.CATEGORY_SET,
        notes=audit_note,
    ))
    await db.flush()

    # ── Step 5: Route ticket ──────────────────────────────
    from app.services.assignment_service import route_ticket
    routing = await route_ticket(ticket, db)
    print(f"   🔀 Routing: {routing}")

    await db.flush()
    await db.refresh(ticket)

    # ── Step 6: Background email alerts ───────────────────
    # HIGH/CRITICAL manager notification now fires from inside
    # assignment_service.route_ticket() (Step 5 above) since it needs
    # to know which single lead worker was assigned — see that file.

    # Uncategorized → alert managers to review manually
    if not final_category:
        await _alert_managers_uncategorized(ticket, db, final_confidence)
        print(f"   ⚠️  Uncategorized — manager alert queued")

    return ticket


# ── Other service functions (unchanged) ───────────────────

async def list_tickets(
    db: AsyncSession,
    limit: int = 100,
    status: Optional[str] = None,
    search: Optional[str] = None,
    terminal: Optional[str] = None,
) -> list[Ticket]:
    query = select(Ticket).order_by(Ticket.created_at.desc()).limit(limit)
    if status:
        query = query.where(Ticket.status == status.lower())
    if terminal:
        query = query.where(Ticket.terminal == terminal.upper())
    if search:
        # Match on the human-friendly ticket number too, with or without
        # a leading '#': searching "10000042" or "#10000042" both work.
        num_term = search.lstrip("#").strip()
        query = query.where(
            or_(
                Ticket.title.ilike(f"%{search}%"),
                Ticket.description.ilike(f"%{search}%"),
                Ticket.ticket_number.ilike(f"%{num_term}%"),
            )
        )
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_ticket_by_id(
    ticket_id: uuid.UUID,
    db: AsyncSession,
) -> Ticket | None:
    result = await db.execute(select(Ticket).where(Ticket.id == ticket_id))
    return result.scalar_one_or_none()


async def get_dashboard_stats(db: AsyncSession) -> dict:
    open_result = await db.execute(
        select(func.count(Ticket.id)).where(
            Ticket.status.in_([TicketStatus.OPEN, TicketStatus.IN_PROGRESS])
        )
    )
    open_count = open_result.scalar() or 0

    critical_result = await db.execute(
        select(func.count(Ticket.id)).where(
            and_(
                Ticket.priority == TicketPriority.CRITICAL,
                Ticket.status.in_([TicketStatus.OPEN, TicketStatus.IN_PROGRESS])
            )
        )
    )
    critical_count = critical_result.scalar() or 0

    now = datetime.now(timezone.utc)
    sla_result = await db.execute(
        select(func.count(Ticket.id)).where(
            and_(
                Ticket.sla_deadline != None,
                Ticket.sla_deadline < now,
                Ticket.status.in_([TicketStatus.OPEN, TicketStatus.IN_PROGRESS, TicketStatus.CRITICAL_REVIEW])
            )
        )
    )
    sla_breaches = sla_result.scalar() or 0

    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    resolved_result = await db.execute(
        select(func.count(Ticket.id)).where(
            and_(
                Ticket.status.in_([TicketStatus.RESOLVED, TicketStatus.CLOSED]),
                Ticket.resolved_at >= today_start,
            )
        )
    )
    resolved_today = resolved_result.scalar() or 0

    return {
        "open":           open_count,
        "critical":       critical_count,
        "sla_breaches":   sla_breaches,
        "resolved_today": resolved_today,
    }