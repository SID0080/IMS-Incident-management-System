"""
Registration code admin routes.
ADD to main.py:  app.include_router(reg_codes.router, prefix="/admin")
"""
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

from app.database import get_db
from app.models.user import User, UserRole, Department
from app.models.registration_code import RegistrationCode
from app.core.deps import require_role

router = APIRouter(tags=["Admin"])


class RegCodeResponse(BaseModel):
    id:             uuid.UUID
    code:           str
    assigned_email: str
    role:           UserRole
    department:     Department
    label:          Optional[str]
    is_used:        bool
    created_at:     datetime
    used_at:        Optional[datetime]
    model_config = {"from_attributes": True}


class CreateCodeRequest(BaseModel):
    code:           str
    assigned_email: str
    role:           UserRole
    department:     Department
    label:          Optional[str] = None


# ── GET /admin/registration-codes ─────────────────────────
@router.get("/registration-codes", response_model=list[RegCodeResponse])
async def list_codes(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(UserRole.ADMIN)),
):
    result = await db.execute(
        select(RegistrationCode).order_by(RegistrationCode.department, RegistrationCode.code)
    )
    return [RegCodeResponse.model_validate(c) for c in result.scalars().all()]


# ── POST /admin/registration-codes ────────────────────────
@router.post("/registration-codes", response_model=RegCodeResponse, status_code=201)
async def create_code(
    data: CreateCodeRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(UserRole.ADMIN)),
):
    """Admin creates a new email-bound registration code."""
    existing = await db.execute(
        select(RegistrationCode).where(RegistrationCode.code == data.code)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(400, "Code already exists")

    code = RegistrationCode(
        code=data.code.strip(),
        assigned_email=data.assigned_email.lower().strip(),
        role=data.role,
        department=data.department,
        label=data.label,
    )
    db.add(code)
    await db.flush()
    await db.refresh(code)
    return RegCodeResponse.model_validate(code)


# ── DELETE /admin/registration-codes/{id} ─────────────────
@router.delete("/registration-codes/{code_id}")
async def delete_code(
    code_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(UserRole.ADMIN)),
):
    code = await db.get(RegistrationCode, code_id)
    if not code:
        raise HTTPException(404, "Code not found")
    await db.delete(code)
    return {"message": "Code deleted", "code": code.code}
