import uuid
import enum
import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import String, Text, DateTime, Enum as SAEnum, ForeignKey, Float, Boolean, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.audit_log import AuditLog


class TicketPriority(str, enum.Enum):
    LOW      = "low"
    MEDIUM   = "medium"
    HIGH     = "high"
    CRITICAL = "critical"


class TicketStatus(str, enum.Enum):
    OPEN            = "open"
    IN_PROGRESS     = "in_progress"
    CRITICAL_REVIEW = "critical_review"
    RESOLVED        = "resolved"
    CLOSED          = "closed"


class Terminal(str, enum.Enum):
    """Airport terminal where the incident occurred. Used for location filtering."""
    T2 = "T2"
    T3 = "T3"


_VALID_TRANSITIONS: dict[TicketStatus, list[TicketStatus]] = {
    TicketStatus.OPEN:            [TicketStatus.IN_PROGRESS],
    TicketStatus.IN_PROGRESS:     [TicketStatus.RESOLVED, TicketStatus.CRITICAL_REVIEW, TicketStatus.OPEN],
    TicketStatus.CRITICAL_REVIEW: [TicketStatus.IN_PROGRESS, TicketStatus.RESOLVED],
    TicketStatus.RESOLVED:        [TicketStatus.CLOSED, TicketStatus.OPEN],
    TicketStatus.CLOSED:          [TicketStatus.OPEN],
}


def is_valid_transition(current: TicketStatus, next_status: TicketStatus) -> bool:
    return next_status in _VALID_TRANSITIONS.get(current, [])


class Ticket(Base):
    __tablename__ = "tickets"

    id:          Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    # ── Human-friendly reference number ────────────────────
    # 8-digit numeric code shown everywhere in the UI/emails/PDFs.
    # Generated from the Postgres sequence `ticket_number_seq`
    # (see MIGRATION). The UUID `id` stays the PK for all FKs/URLs.
    ticket_number: Mapped[str | None] = mapped_column(String(8), unique=True, nullable=True, index=True)

    title:       Mapped[str]       = mapped_column(String(500), nullable=False)
    description: Mapped[str]       = mapped_column(Text, nullable=False)
    location:    Mapped[str | None] = mapped_column(String(255), nullable=True)

    # ── Location filtering ─────────────────────────────────
    terminal:        Mapped[Terminal | None] = mapped_column(SAEnum(Terminal), nullable=True, index=True)
    equipment_model: Mapped[str | None]      = mapped_column(String(255), nullable=True)

    # ── Device dropdown (report form) ──────────────────────
    # Kept as a plain string (not an enum) so new device types can be
    # added in the frontend dropdown without a DB migration.
    device_type: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)

    priority: Mapped[TicketPriority] = mapped_column(
        SAEnum(TicketPriority), default=TicketPriority.MEDIUM, nullable=False
    )
    status: Mapped[TicketStatus] = mapped_column(
        SAEnum(TicketStatus), default=TicketStatus.OPEN, nullable=False
    )

    # Evidence
    image_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Phase 7: Image analysis results
    image_analysis_notes:      Mapped[str | None]   = mapped_column(Text,        nullable=True)
    image_analysis_department: Mapped[str | None]   = mapped_column(String(100), nullable=True)
    image_analysis_confidence: Mapped[float | None] = mapped_column(Float,       nullable=True)

    # ML text categorization
    category:      Mapped[str | None]   = mapped_column(String(100), nullable=True)
    ml_confidence: Mapped[float | None] = mapped_column(Float,       nullable=True)

    # SLA
    sla_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Priority Queue / Pause System ─────────────────────
    is_paused: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    remaining_sla_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ── Multi-worker assignment ────────────────────────────
    additional_assignee_ids: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Ownership
    reporter_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    reporter: Mapped["User"]        = relationship("User", foreign_keys=[reporter_id], lazy="selectin")
    assignee: Mapped["User | None"] = relationship("User", foreign_keys=[assignee_id], lazy="selectin")

    audit_logs: Mapped[list["AuditLog"]] = relationship(
        "AuditLog", back_populates="ticket", lazy="selectin"
    )

    # Timestamps
    created_at:   Mapped[datetime]        = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at:   Mapped[datetime]        = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    assigned_at:  Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at:  Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at:    Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Helpers ───────────────────────────────────────────
    @property
    def additional_assignee_list(self) -> list[str]:
        if self.additional_assignee_ids:
            try:
                return json.loads(self.additional_assignee_ids)
            except (json.JSONDecodeError, TypeError):
                return []
        return []

    @additional_assignee_list.setter
    def additional_assignee_list(self, uuids: list) -> None:
        self.additional_assignee_ids = json.dumps([str(u) for u in uuids]) if uuids else None
