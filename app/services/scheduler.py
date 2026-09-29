"""
Background scheduler for the SLA engine.

Starts an APScheduler that runs check_sla_breaches() every few minutes.
Wired into the FastAPI lifespan in main.py.
"""
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.services.sla_engine import check_sla_breaches

# Check interval — every 1 minute for demo/testing.
# In production you might use 5–15 minutes.
SLA_CHECK_INTERVAL_MINUTES = 1

scheduler = AsyncIOScheduler()


def start_scheduler():
    if scheduler.running:
        return
    scheduler.add_job(
        check_sla_breaches,
        trigger="interval",
        minutes=SLA_CHECK_INTERVAL_MINUTES,
        id="sla_breach_check",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.start()
    print(f"⏰  SLA scheduler started (every {SLA_CHECK_INTERVAL_MINUTES} min)")


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
        print("⏰  SLA scheduler stopped")
