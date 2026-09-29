from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime, timezone

from app.database import get_db
from app.models.user import User, UserRole
from app.models.registration_code import RegistrationCode
from app.schemas.user import (
    LoginRequest,
    LoginResponse,
    PublicRegisterRequest,
    UserCreate,
    UserResponse,
)
from app.services.user_service import authenticate_user, create_user
from app.core.security import create_access_token
from app.core.deps import get_current_user

router = APIRouter(tags=["Authentication"])


# ── POST /auth/register ───────────────────────────────────
@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(data: PublicRegisterRequest, db: AsyncSession = Depends(get_db)):
    """
    Corporate self-registration with a personal, email-bound access code.

    Validation chain:
      1. Code must exist
      2. Code must be unused
      3. Code's assigned_email must match the registering email
      4. Role + department are taken from the code (authoritative)
    """
    code = data.access_code.strip()
    email = data.email.lower().strip()

    # 1. Look up the code
    result = await db.execute(
        select(RegistrationCode).where(RegistrationCode.code == code)
    )
    reg_code = result.scalar_one_or_none()

    if not reg_code:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid access code. Contact your administrator.",
        )

    # 2. Must be unused
    if reg_code.is_used:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This access code has already been used to register an account.",
        )

    # 3. Must match the assigned email
    if reg_code.assigned_email.lower().strip() != email:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This access code is not assigned to your email address.",
        )

    # 4. Create the user — role + department come from the CODE
    try:
        user_data = UserCreate(
            email=data.email,
            full_name=data.full_name,
            password=data.password,
            role=reg_code.role,
            department=reg_code.department,
            access_code=reg_code.code,
        )
        user = await create_user(user_data, db)

        # Mark the code as used
        reg_code.is_used = True
        reg_code.used_at = datetime.now(timezone.utc)

        await db.flush()
        await db.refresh(user)
        return UserResponse.model_validate(user)

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


# ── POST /auth/login ──────────────────────────────────────
@router.post("/login", response_model=LoginResponse)
async def login(data: LoginRequest, db: AsyncSession = Depends(get_db)):
    """
    Login with email + password + personal access code.
    WORKER/MANAGER: access_code must match their stored code.
    ADMIN: access_code is ignored.
    """
    user = await authenticate_user(data.email, data.password, db)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    # Validate personal access code for non-admin users
    if user.role != UserRole.ADMIN:
        submitted = (data.access_code or "").strip()
        if not submitted:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access code is required for staff accounts.",
            )
        if not user.access_code or submitted != user.access_code:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid access code for this account.",
            )

    token = create_access_token(subject=str(user.id))
    return LoginResponse(
        access_token=token,
        user=UserResponse.model_validate(user),
    )


# ── GET /auth/me ──────────────────────────────────────────
@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    return UserResponse.model_validate(current_user)
