"""
Seed synthetic Adani Group users — with personal access codes

Creates:
  - 1 manager per department (6 managers)
  - 5 workers per department (30 workers)
  = 36 total synthetic users

Each user gets their personal access_code (e.g. ADN-IT-W1) so they can
log in under the email-bound access code system.

Run from project root:
    python scripts/seed_users.py

Passwords:
  managers → Manager@123
  workers  → Worker@123
"""

import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import AsyncSessionLocal
from app.models.user import User, UserRole, Department, WorkerLevel
from app.models.registration_code import RegistrationCode
from app.core.security import hash_password
from sqlalchemy import select, func
from datetime import datetime, timezone


# ── Synthetic user data with access codes ─────────────────
# Each entry: (full_name, email, access_code)

USERS_BY_DEPARTMENT = {
    Department.it: {
        "code_prefix": "IT",
        "manager": ("Ananya Singh", "ananya.singh@adani.com", "ADN-IT-M1"),
        "workers": [
            ("Arjun Sharma",    "arjun.sharma@adani.com",    "ADN-IT-W1"),
            ("Priya Patel",     "priya.patel@adani.com",     "ADN-IT-W2"),
            ("Rohan Mehta",     "rohan.mehta@adani.com",     "ADN-IT-W3"),
            ("Kavya Reddy",     "kavya.reddy@adani.com",     "ADN-IT-W4"),
            ("Siddharth Joshi", "siddharth.joshi@adani.com", "ADN-IT-W5"),
        ],
    },
    Department.security: {
        "code_prefix": "SEC",
        "manager": ("Deepak Verma", "deepak.verma@adani.com", "ADN-SEC-M1"),
        "workers": [
            ("Vikram Tiwari", "vikram.tiwari@adani.com", "ADN-SEC-W1"),
            ("Sunita Gupta",  "sunita.gupta@adani.com",  "ADN-SEC-W2"),
            ("Rajesh Kumar",  "rajesh.kumar@adani.com",  "ADN-SEC-W3"),
            ("Pooja Nair",    "pooja.nair@adani.com",    "ADN-SEC-W4"),
            ("Amit Chauhan",  "amit.chauhan@adani.com",  "ADN-SEC-W5"),
        ],
    },
    Department.hr: {
        "code_prefix": "HR",
        "manager": ("Ritu Sharma", "ritu.sharma@adani.com", "ADN-HR-M1"),
        "workers": [
            ("Neha Agarwal",   "neha.agarwal@adani.com",   "ADN-HR-W1"),
            ("Suresh Rao",     "suresh.rao@adani.com",     "ADN-HR-W2"),
            ("Divya Krishnan", "divya.krishnan@adani.com", "ADN-HR-W3"),
            ("Manish Bhatia",  "manish.bhatia@adani.com",  "ADN-HR-W4"),
            ("Swati Mishra",   "swati.mishra@adani.com",   "ADN-HR-W5"),
        ],
    },
    Department.technical_operations: {
        "code_prefix": "TO",
        "manager": ("Vijay Nath", "vijay.nath@adani.com", "ADN-TO-M1"),
        "workers": [
            ("Aakash Pande",   "aakash.pande@adani.com",   "ADN-TO-W1"),
            ("Sanjay Dubey",   "sanjay.dubey@adani.com",   "ADN-TO-W2"),
            ("Meera Iyer",     "meera.iyer@adani.com",     "ADN-TO-W3"),
            ("Kiran Desai",    "kiran.desai@adani.com",    "ADN-TO-W4"),
            ("Tarun Malhotra", "tarun.malhotra@adani.com", "ADN-TO-W5"),
        ],
    },
    Department.marketing: {
        "code_prefix": "MK",
        "manager": ("Varun Kapoor", "varun.kapoor@adani.com", "ADN-MK-M1"),
        "workers": [
            ("Ankita Sood",   "ankita.sood@adani.com",   "ADN-MK-W1"),
            ("Gaurav Saxena", "gaurav.saxena@adani.com", "ADN-MK-W2"),
            ("Shreya Pillai", "shreya.pillai@adani.com", "ADN-MK-W3"),
            ("Harish Naidu",  "harish.naidu@adani.com",  "ADN-MK-W4"),
            ("Riya Banerjee", "riya.banerjee@adani.com", "ADN-MK-W5"),
        ],
    },
    Department.legal: {
        "code_prefix": "LG",
        "manager": ("Karthik Menon", "karthik.menon@adani.com", "ADN-LG-M1"),
        "workers": [
            ("Aditi Bhatt",   "aditi.bhatt@adani.com",   "ADN-LG-W1"),
            ("Rohit Chandra", "rohit.chandra@adani.com", "ADN-LG-W2"),
            ("Prachi Wagh",   "prachi.wagh@adani.com",   "ADN-LG-W3"),
            ("Sameer Ali",    "sameer.ali@adani.com",    "ADN-LG-W4"),
            ("Leena Mathur",  "leena.mathur@adani.com",  "ADN-LG-W5"),
        ],
    },
}

# ── Level spread for the 5 workers in each department ─────
# W1, W2 → L1 (first line)   W3, W4 → L2 (specialist)   W5 → L3 (senior)
# Routing: LOW/MEDIUM → L1, HIGH → L2, CRITICAL → L3 (with fallback).
WORKER_LEVELS = [WorkerLevel.L1, WorkerLevel.L1, WorkerLevel.L2, WorkerLevel.L2, WorkerLevel.L3]

MANAGER_PASSWORD = "Manager@123"
WORKER_PASSWORD  = "Worker@123"


async def _ensure_reg_code(db, code, email, role, dept, label):
    """Create the registration_code row if missing, mark used."""
    existing = await db.scalar(select(RegistrationCode).where(RegistrationCode.code == code))
    if existing:
        existing.is_used = True
        existing.used_at = datetime.now(timezone.utc)
    else:
        db.add(RegistrationCode(
            code=code,
            assigned_email=email,
            role=role,
            department=dept,
            label=label,
            is_used=True,
            used_at=datetime.now(timezone.utc),
        ))


async def seed():
    async with AsyncSessionLocal() as db:
        created = 0
        skipped = 0

        for dept, data in USERS_BY_DEPARTMENT.items():
            dept_name = dept.value.replace("_", " ").title()

            # ── Manager ──────────────────────────────────
            mgr_name, mgr_email, mgr_code = data["manager"]
            existing = await db.scalar(select(User).where(User.email == mgr_email))
            if existing:
                # Backfill access_code if missing
                if not existing.access_code:
                    existing.access_code = mgr_code
                    await _ensure_reg_code(db, mgr_code, mgr_email, UserRole.MANAGER, dept,
                                           f"{dept_name} Manager - {mgr_name}")
                    print(f"  🔑 BACKFILL code {mgr_code} → {mgr_email}")
                else:
                    print(f"  ⏭  SKIP  {mgr_email} (already exists)")
                skipped += 1
            else:
                db.add(User(
                    email=mgr_email,
                    full_name=mgr_name,
                    hashed_password=hash_password(MANAGER_PASSWORD),
                    role=UserRole.MANAGER,
                    department=dept,
                    access_code=mgr_code,
                    is_active=True,
                ))
                await _ensure_reg_code(db, mgr_code, mgr_email, UserRole.MANAGER, dept,
                                       f"{dept_name} Manager - {mgr_name}")
                print(f"  ✅ MANAGER  [{dept_name}]  {mgr_name} — {mgr_email}  ({mgr_code})")
                created += 1

            # ── Workers ───────────────────────────────────
            for idx, (worker_name, worker_email, worker_code) in enumerate(data["workers"]):
                level = WORKER_LEVELS[idx % len(WORKER_LEVELS)]
                existing = await db.scalar(select(User).where(User.email == worker_email))
                if existing:
                    changed = False
                    if not existing.access_code:
                        existing.access_code = worker_code
                        await _ensure_reg_code(db, worker_code, worker_email, UserRole.WORKER, dept,
                                               f"{dept_name} Worker - {worker_name}")
                        print(f"  🔑 BACKFILL code {worker_code} → {worker_email}")
                        changed = True
                    # Backfill engineer level for workers seeded before the
                    # L1/L2/L3 system existed
                    if existing.level is None:
                        existing.level = level
                        print(f"  🎚  BACKFILL level {level.value} → {worker_email}")
                        changed = True
                    if not changed:
                        print(f"  ⏭  SKIP  {worker_email} (already exists)")
                    skipped += 1
                else:
                    db.add(User(
                        email=worker_email,
                        full_name=worker_name,
                        hashed_password=hash_password(WORKER_PASSWORD),
                        role=UserRole.WORKER,
                        department=dept,
                        level=level,
                        access_code=worker_code,
                        is_active=True,
                    ))
                    await _ensure_reg_code(db, worker_code, worker_email, UserRole.WORKER, dept,
                                           f"{dept_name} Worker - {worker_name}")
                    print(f"  ✅ WORKER   [{dept_name}]  {worker_name} ({level.value}) — {worker_email}  ({worker_code})")
                    created += 1

        await db.commit()

        print(f"\n{'─'*60}")
        print(f"  Seeding complete: {created} created, {skipped} skipped")
        print(f"{'─'*60}")
        print(f"\n  Manager password : {MANAGER_PASSWORD}")
        print(f"  Worker  password : {WORKER_PASSWORD}")
        print(f"\n  Levels per department: W1,W2 = L1  |  W3,W4 = L2  |  W5 = L3")
        print(f"  Routing: LOW/MEDIUM → L1, HIGH → L2, CRITICAL → L3")
        print(f"\n  Login requires: email + password + personal access code")
        print(f"  Example: arjun.sharma@adani.com / Worker@123 / ADN-IT-W1")

        total = await db.scalar(select(func.count()).select_from(User))
        print(f"\n  Total users in system: {total}")


if __name__ == "__main__":
    print("\n🌱 Seeding Adani Group synthetic users with access codes...\n")
    asyncio.run(seed())
