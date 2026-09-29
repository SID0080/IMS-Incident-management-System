import uuid
import enum
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import String, Boolean, DateTime, Integer, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.audit_log import AuditLog


class UserRole(str, enum.Enum):
    WORKER  = "WORKER"
    MANAGER = "MANAGER"
    ADMIN   = "ADMIN"


class Department(str, enum.Enum):
    it                   = "it"
    security             = "security"
    hr                   = "hr"
    technical_operations = "technical_operations"
    marketing            = "marketing"
    legal                = "legal"


class WorkerLevel(str, enum.Enum):
    """
    Support engineer tier. Tickets route directly by priority:
      LOW / MEDIUM → L1    (first-line engineers)
      HIGH         → L2    (specialist engineers)
      CRITICAL     → L3    (senior/expert engineers)
    Fallback: if nobody at the preferred level is available, the next
    levels are tried — see assignment_service.LEVEL_PREFERENCE.
    NULL for managers/admins (and workers not yet leveled — they act
    as a last-resort pool).
    """
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"


class User(Base):
    __tablename__ = "users"

    id:              Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email:           Mapped[str]       = mapped_column(String(255), unique=True, nullable=False, index=True)
    full_name:       Mapped[str]       = mapped_column(String(255), nullable=False)
    hashed_password: Mapped[str]       = mapped_column(String(255), nullable=False)
    role:            Mapped[UserRole]  = mapped_column(SAEnum(UserRole), default=UserRole.WORKER, nullable=False)
    is_active:       Mapped[bool]      = mapped_column(Boolean, default=True, nullable=False)

    # Personal access code (NULL for ADMIN). Validated at every login.
    access_code: Mapped[str | None] = mapped_column(String(50), unique=True, nullable=True)

    department:     Mapped[Department | None]  = mapped_column(SAEnum(Department), nullable=True, index=True)
    level:          Mapped[WorkerLevel | None] = mapped_column(SAEnum(WorkerLevel), nullable=True, index=True)
    penalty_points: Mapped[int]                = mapped_column(Integer, default=0, nullable=False)
    is_blacklisted: Mapped[bool]               = mapped_column(Boolean, default=False, nullable=False)
    breach_count:   Mapped[int]                = mapped_column(Integer, default=0, nullable=False)

    audit_logs: Mapped[list["AuditLog"]] = relationship("AuditLog", back_populates="user", lazy="selectin")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
