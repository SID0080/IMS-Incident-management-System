"""
Seed sample IT engineers — 2 each at L1, L2, L3.

Run from the project root (same folder as the `app/` package),
with your venv active and the database reachable:

    python seed_engineers.py

Safe to re-run: existing emails are skipped.

Routing behaviour after seeding (department = it):
    LOW / MEDIUM  →  L1  (Ravi / Sneha, least-busy wins)
    HIGH          →  L2  (Arjun / Pooja)
    CRITICAL      →  L3  (Vikram / Meera)
If everyone at the preferred level is at capacity/blacklisted, the
other levels are tried in fallback order.

Each worker logs in with their email + password + personal access code.
"""
import asyncio

from app.database import AsyncSessionLocal
from app.models.user import UserRole, Department, WorkerLevel
from app.schemas.user import UserCreate
from app.services.user_service import create_user, get_user_by_email

# ── Demo roster ───────────────────────────────────────────
# (email, full_name, level, access_code)
ENGINEERS = [
    ("ravi.l1@adani.com",   "Ravi Sharma",    WorkerLevel.L1, "IT-L1-RAVI"),
    ("sneha.l1@adani.com",  "Sneha Verma",    WorkerLevel.L1, "IT-L1-SNEHA"),
    ("arjun.l2@adani.com",  "Arjun Mehta",    WorkerLevel.L2, "IT-L2-ARJUN"),
    ("pooja.l2@adani.com",  "Pooja Iyer",     WorkerLevel.L2, "IT-L2-POOJA"),
    ("vikram.l3@adani.com", "Vikram Singh",   WorkerLevel.L3, "IT-L3-VIKRAM"),
    ("meera.l3@adani.com",  "Meera Krishnan", WorkerLevel.L3, "IT-L3-MEERA"),
]

PASSWORD = "engineer123"   # same demo password for all six


async def seed() -> None:
    async with AsyncSessionLocal() as db:
        created, skipped = 0, 0

        for email, name, level, code in ENGINEERS:
            if await get_user_by_email(email, db):
                print(f"   ⏭️  {email} already exists — skipped")
                skipped += 1
                continue

            user = await create_user(
                UserCreate(
                    email=email,
                    full_name=name,
                    password=PASSWORD,
                    role=UserRole.WORKER,
                    department=Department.it,
                    level=level,
                    access_code=code,
                ),
                db,
            )
            print(f"   ✅  {user.full_name:<16} {level.value}  |  {email}  |  code: {code}")
            created += 1

        await db.commit()

    print(f"\n🌱  Done — {created} created, {skipped} skipped.")
    print(f"    Password for all: {PASSWORD}")
    print("    Login needs email + password + the personal access code shown above.")
    print("\n    Demo: report a MEDIUM IT issue → routes to Ravi/Sneha (L1);")
    print("          a HIGH one → Arjun/Pooja (L2); a CRITICAL → Vikram/Meera (L3).")


if __name__ == "__main__":
    asyncio.run(seed())
