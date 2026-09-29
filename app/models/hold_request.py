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


class HoldRequestStatus(str, enum.Enum):
    PENDING   = "PENDING"    # worker asked to pause — awaiting manager/admin decision
    APPROVED  = "APPROVED"   # allowed — worker's Hold button is now active
    DENIED    = "DENIED"     # refused — SLA keeps running
    EXECUTED  = "EXECUTED"   # worker clicked Hold — ticket paused, SLA frozen
    COMPLETED = "COMPLETED"  # blocking ticket resolved — held ticket auto-resumed


class HoldRequest(Base):
    """
    A worker's request to PAUSE one of their active tickets because a
    higher-priority ticket (`blocking_ticket_id`) landed on them.

    Nothing pauses automatically anymore. The SLA clock on the lower
    ticket keeps running until:
      1. the worker submits this request with a remark (why),
      2. a manager or admin APPROVES it,
      3. the worker actually clicks Hold (EXECUTED → ticket paused).

    When the blocking ticket is resolved, every EXECUTED hold pointing
    at it is auto-resumed (COMPLETED) — the held ticket's SLA restarts
    from exactly the remaining time saved at pause.
    """
    __tablename__ = "hold_requests"

    id:                 Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    ticket_id:          Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False, index=True)   # ticket to pause
    blocking_ticket_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True, index=True)  # the higher-priority ticket
    requested_by_id:    Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)                 # the worker

    remark: Mapped[str] = mapped_column(Text, nullable=False)   # worker's justification for the manager

    status: Mapped[HoldRequestStatus] = mapped_column(
        SAEnum(HoldRequestStatus), default=HoldRequestStatus.PENDING, nullable=False, index=True
    )

    created_at:     Mapped[datetime]         = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_by_id:  Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_at:     Mapped[datetime | None]  = mapped_column(DateTime(timezone=True), nullable=True)
    decision_notes: Mapped[str | None]       = mapped_column(Text, nullable=True)
    executed_at:    Mapped[datetime | None]  = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at:   Mapped[datetime | None]  = mapped_column(DateTime(timezone=True), nullable=True)

    ticket:          Mapped["Ticket"]        = relationship("Ticket", foreign_keys=[ticket_id], lazy="selectin")
    blocking_ticket: Mapped["Ticket | None"] = relationship("Ticket", foreign_keys=[blocking_ticket_id], lazy="selectin")
    requested_by:    Mapped["User"]          = relationship("User", foreign_keys=[requested_by_id], lazy="selectin")
    decided_by:      Mapped["User | None"]   = relationship("User", foreign_keys=[decided_by_id], lazy="selectin")
