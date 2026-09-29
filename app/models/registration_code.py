import uuid
from datetime import datetime
from sqlalchemy import String, Boolean, DateTime, Enum as SAEnum, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.user import UserRole, Department


class RegistrationCode(Base):
    """
    Admin pre-creates one code per employee, bound to a specific email.
    Registration requires: code exists + not used + assigned_email matches.
    The code's role + department are authoritative for the new account.
    """
    __tablename__ = "registration_codes"

    id:             Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    code:           Mapped[str]       = mapped_column(String(50), unique=True, nullable=False, index=True)
    assigned_email: Mapped[str]       = mapped_column(String(255), nullable=False, index=True)
    role:           Mapped[UserRole]  = mapped_column(SAEnum(UserRole), nullable=False)
    department:     Mapped[Department] = mapped_column(SAEnum(Department), nullable=False)
    label:          Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_used:        Mapped[bool]      = mapped_column(Boolean, default=False, nullable=False)
    created_at:     Mapped[datetime]  = mapped_column(DateTime(timezone=True), server_default=func.now())
    used_at:        Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
