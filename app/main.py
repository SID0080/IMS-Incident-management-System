import os
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import engine, Base, AsyncSessionLocal

import app.models  # noqa: F401 — registers all models with Base.metadata


# ── Admin seeder ──────────────────────────────────────────
async def seed_admin(db: AsyncSession) -> None:
    from app.models.user import User, UserRole
    from app.core.security import hash_password

    result = await db.execute(select(User).where(User.role == UserRole.ADMIN))
    if result.scalar_one_or_none():
        return

    admin = User(
        email="admin@adani.com",
        full_name="Admin User",
        hashed_password=hash_password("admin123"),
        role=UserRole.ADMIN,
        is_active=True,
    )
    db.add(admin)
    await db.commit()
    print("🌱  Default admin seeded: admin@adani.com / admin123")


# ── Background ML warm-up ─────────────────────────────────
# TensorFlow takes several seconds to import + load MobileNetV2.
# Doing that inline blocked uvicorn startup, which made every page
# hang after a restart. Instead we load it on a daemon thread: the
# server starts serving immediately, and the model is warm by the
# time anyone actually uploads an image. If someone beats the
# thread, image_analyzer's own lazy loader just loads on demand.
def _warm_ml_models() -> None:
    try:
        from app.services.image_analyzer import _load_model
        _load_model()
        print("🧠  Image ML warmed (background)")
    except Exception as e:
        print(f"   (image ML warm-up skipped: {e})")


# ── Lifespan ──────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print(f"✅  {settings.APP_NAME} v{settings.VERSION} — DB tables ready")

    async with AsyncSessionLocal() as db:
        await seed_admin(db)

    from app.services.scheduler import start_scheduler
    start_scheduler()

    # Non-blocking: server starts serving right away
    threading.Thread(target=_warm_ml_models, daemon=True).start()

    yield

    from app.services.scheduler import stop_scheduler
    stop_scheduler()

    await engine.dispose()
    print("🔴  Shutdown complete")


app = FastAPI(
    title=settings.APP_NAME,
    description="Incident Management System for Adani Group",
    version=settings.VERSION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173", "http://127.0.0.1:5500", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

os.makedirs("uploads", exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

# ── Routers ───────────────────────────────────────────────

from app.routers import (  # noqa: E402
    auth,
    incidents,
    users,
    admin,
    emergency,
    analytics,
    accountability,
    sla,
    worker,
    reg_codes,
    help_requests,
    hold_requests,
    worker_performance,
)

app.include_router(auth.router,                     prefix="/auth")
app.include_router(incidents.router,                prefix="/tickets")
app.include_router(users.router,                    prefix="/users")
app.include_router(admin.router,                    prefix="/admin")
app.include_router(emergency.router,                prefix="/emergency")
app.include_router(analytics.router,                prefix="/analytics")
app.include_router(accountability.router,           prefix="/admin")
app.include_router(sla.router,                      prefix="/sla")
app.include_router(worker.router,                   prefix="/worker")
app.include_router(worker_performance.router,       prefix="/worker")   # /worker/me/performance + suggestions
app.include_router(worker_performance.admin_router, prefix="/admin")    # /admin/workers/{id}/performance
app.include_router(reg_codes.router,                prefix="/admin")    # /admin/reg-codes
app.include_router(help_requests.router,            prefix="/admin")    # /admin/help-requests approve/deny
app.include_router(hold_requests.router,            prefix="/admin")    # /admin/hold-requests approve/deny


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "ok", "service": settings.APP_NAME, "version": settings.VERSION}


@app.get("/dashboard/stats", tags=["Dashboard"])
async def dashboard_stats():
    from app.routers.incidents import get_stats
    async with AsyncSessionLocal() as db:
        return await get_stats(db=db)