"""
Accountability admin routes — manage workers, points, blacklists, levels.
Mount in main.py: app.include_router(accountability.router, prefix="/admin", ...)
"""
import uuid
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models.user import User, UserRole, Department, WorkerLevel
from app.core.deps import require_role

# Note: run-sla-check does NOT inject db — sla_engine manages its own
# session internally via AsyncSessionLocal.

router = APIRouter(tags=["Admin"])


# ── Schemas ───────────────────────────────────────────────

class WorkerStatusResponse(BaseModel):
    id: uuid.UUID
    full_name: str
    email: str
    department: Department | None
    level: WorkerLevel | None = None
    penalty_points: int
    breach_count: int
    is_blacklisted: bool

    model_config = {"from_attributes": True}


class SetDepartmentRequest(BaseModel):
    department: Department


class SetLevelRequest(BaseModel):
    level: WorkerLevel


class SLACheckResponse(BaseModel):
    breached: int
    message: str


# ── GET /admin/workers ────────────────────────────────────
@router.get("/workers", response_model=list[WorkerStatusResponse])
async def list_workers(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(UserRole.ADMIN, UserRole.MANAGER)),
):
    result = await db.execute(
        select(User).where(User.role == UserRole.WORKER).order_by(User.penalty_points.desc())
    )
    return [WorkerStatusResponse.model_validate(w) for w in result.scalars().all()]


# ── PATCH /admin/workers/{id}/department ──────────────────
@router.patch("/workers/{user_id}/department", response_model=WorkerStatusResponse)
async def set_worker_department(
    user_id: uuid.UUID,
    data: SetDepartmentRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(UserRole.ADMIN)),
):
    worker = await db.get(User, user_id)
    if not worker:
        raise HTTPException(404, "User not found")
    worker.department = data.department
    await db.flush()
    await db.refresh(worker)
    return WorkerStatusResponse.model_validate(worker)


# ── PATCH /admin/workers/{id}/level ───────────────────────
# Set a worker's engineer tier (L1/L2/L3). Routing sends LOW/MEDIUM
# tickets to L1, HIGH to L2, CRITICAL to L3 (with fallback to the
# other levels when nobody at the preferred tier is available).
@router.patch("/workers/{user_id}/level", response_model=WorkerStatusResponse)
async def set_worker_level(
    user_id: uuid.UUID,
    data: SetLevelRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(UserRole.ADMIN)),
):
    worker = await db.get(User, user_id)
    if not worker:
        raise HTTPException(404, "User not found")
    if worker.role != UserRole.WORKER:
        raise HTTPException(400, "Only workers can be assigned an engineer level")
    worker.level = data.level
    await db.flush()
    await db.refresh(worker)
    return WorkerStatusResponse.model_validate(worker)


# ── POST /admin/workers/{id}/clear-blacklist ──────────────
# Resets points to 0 and removes blacklist flag. Admin only.
# breach_count is kept as a permanent historical record.
@router.post("/workers/{user_id}/clear-blacklist", response_model=WorkerStatusResponse)
async def clear_blacklist(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(UserRole.ADMIN)),
):
    worker = await db.get(User, user_id)
    if not worker:
        raise HTTPException(404, "User not found")
    worker.penalty_points = 0
    worker.is_blacklisted = False
    await db.flush()
    await db.refresh(worker)
    return WorkerStatusResponse.model_validate(worker)


# ── POST /admin/run-sla-check ─────────────────────────────
@router.post("/run-sla-check", response_model=SLACheckResponse)
async def run_sla_check_now(
    _: User = Depends(require_role(UserRole.ADMIN)),
):
    """Manually trigger the SLA breach check instead of waiting for the scheduler."""
    from app.services.sla_engine import check_sla_breaches
    count = await check_sla_breaches()   # sla_engine opens its own session internally
    return SLACheckResponse(breached=count, message=f"{count} ticket(s) processed")
