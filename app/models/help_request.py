import uuid
import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.ticket import Ticket


class HelpRequestStatus(str, enum.Enum):
    PENDING  = "PENDING"
    APPROVED = "APPROVED"
    DENIED   = "DENIED"


class HelpRequest(Base):
    """
    A lead worker's request to bring a helper onto their HIGH/CRITICAL
    ticket. Stays PENDING until a manager or admin in the same department
    approves or denies it. The helper is only actually added to
    ticket.additional_assignee_ids (with full pause/SLA-sharing logic)
    after approval — see assignment_service.delegate_helper().
    """
    __tablename__ = "help_requests"

    id:              Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    ticket_id:       Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False, index=True)
    requested_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id",   ondelete="CASCADE"), nullable=False)   # the lead
    helper_id:       Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id",   ondelete="CASCADE"), nullable=False)   # proposed helper

    status: Mapped[HelpRequestStatus] = mapped_column(
        SAEnum(HelpRequestStatus), default=HelpRequestStatus.PENDING, nullable=False, index=True
    )

    created_at:     Mapped[datetime]        = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_by_id:  Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_at:     Mapped[datetime | None]  = mapped_column(DateTime(timezone=True), nullable=True)
    decision_notes: Mapped[str | None]       = mapped_column(Text, nullable=True)

    ticket:       Mapped["Ticket"] = relationship("Ticket", foreign_keys=[ticket_id], lazy="selectin")
    requested_by: Mapped["User"]   = relationship("User", foreign_keys=[requested_by_id], lazy="selectin")
    helper:       Mapped["User"]   = relationship("User", foreign_keys=[helper_id], lazy="selectin")
    decided_by:   Mapped["User | None"] = relationship("User", foreign_keys=[decided_by_id], lazy="selectin")
