import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field

from app.models.user import UserRole, Department, WorkerLevel


class UserCreate(BaseModel):
    """Internal/admin user creation. No code required."""
    email:       EmailStr
    full_name:   str        = Field(min_length=2, max_length=255)
    password:    str        = Field(min_length=6)
    role:        UserRole   = UserRole.WORKER
    department:  Optional[Department] = None
    level:       Optional[WorkerLevel] = None   # L1/L2/L3 — workers only
    access_code: Optional[str] = None


class PublicRegisterRequest(BaseModel):
    """
    Corporate self-registration with a personal, email-bound access code.

    The submitted access_code must:
      - exist in registration_codes
      - be unused
      - have assigned_email == this email

    Role and department come from the CODE (authoritative), so the form's
    role/department selections are advisory only.
    """
    email:       EmailStr
    full_name:   str    = Field(min_length=2, max_length=255)
    password:    str    = Field(min_length=6)
    access_code: str    = Field(min_length=1, description="Your personal access code")


class LoginRequest(BaseModel):
    email:       EmailStr
    password:    str
    access_code: Optional[str] = None   # Required for WORKER/MANAGER, ignored for ADMIN


class UserResponse(BaseModel):
    id:         uuid.UUID
    email:      EmailStr
    full_name:  str
    role:       UserRole
    department: Optional[Department] = None
    level:      Optional[WorkerLevel] = None
    is_active:  bool
    created_at: datetime

    model_config = {"from_attributes": True}


class LoginResponse(BaseModel):
    access_token: str
    token_type:   str = "bearer"
    user:         UserResponse
