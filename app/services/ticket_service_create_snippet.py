# This shows the UPDATED create_ticket function.
# Replace the create_ticket function in your existing ticket_service.py with this.
# Everything else in ticket_service.py stays the same.

import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ticket import Ticket, TicketStatus
from app.models.audit_log import AuditLog, AuditAction
from app.schemas.ticket import TicketCreate
from app.services.categorizer import categorize


async def create_ticket(
    data: TicketCreate,
    reporter_id: uuid.UUID,
    db: AsyncSession,
    image_path: Optional[str] = None,
) -> Ticket:
    # 1. ML categorization
    text = f"{data.title}. {data.description}"
    category, confidence = categorize(text)

    ticket = Ticket(
        title=data.title,
        description=data.description,
        priority=data.priority,
        status=TicketStatus.OPEN,
        location=data.location,
        reporter_id=reporter_id,
        image_path=image_path,
        category=category,
        ml_confidence=confidence,
    )
    db.add(ticket)
    await db.flush()

    # 2. Log categorization
    cat_label = category if category else "needs_review"
    db.add(AuditLog(
        ticket_id=ticket.id,
        user_id=reporter_id,
        action=AuditAction.CATEGORY_SET,
        notes=f"ML category: {cat_label} (confidence {confidence:.2f})",
    ))

    # 3. Route the ticket (auto-assign Low/Med, leave High/Crit for manager)
    from app.services.assignment_service import route_ticket
    routing = await route_ticket(ticket, db)
    print(f"   Routing: {routing}")

    await db.flush()
    await db.refresh(ticket)
    return ticket
