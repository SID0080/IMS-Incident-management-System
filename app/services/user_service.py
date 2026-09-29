from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.user import User, UserRole
from app.schemas.user import UserCreate
from app.core.security import hash_password, verify_password


async def get_user_by_email(email: str, db: AsyncSession) -> User | None:
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def create_user(data: UserCreate, db: AsyncSession) -> User:
    """Create a new user. Raises ValueError if email already exists."""
    existing = await get_user_by_email(data.email, db)
    if existing:
        raise ValueError("Email address is already registered")

    user = User(
        email=data.email,
        full_name=data.full_name,
        hashed_password=hash_password(data.password),
        role=data.role,
        department=data.department,
        level=getattr(data, "level", None),   # ← L1/L2/L3 engineer tier
        access_code=getattr(data, "access_code", None),   # ← personal code
    )
    # New workers default to L1 (first-line) — admin promotes to L2/L3
    # from the admin panel. Managers/admins stay unleveled.
    if user.role == UserRole.WORKER and user.level is None:
        from app.models.user import WorkerLevel
        user.level = WorkerLevel.L1
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


async def authenticate_user(email: str, password: str, db: AsyncSession) -> User | None:
    user = await get_user_by_email(email, db)
    if not user or not user.is_active:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user
