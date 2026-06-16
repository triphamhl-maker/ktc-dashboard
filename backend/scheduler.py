"""
APScheduler setup for periodic backlog crawling.
"""

import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from config import config

logger = logging.getLogger("scheduler")

scheduler = AsyncIOScheduler()


def setup_scheduler(crawl_func, fill_rate_func=None):
    """Initialize the scheduler with the crawl jobs."""
    interval = config.crawl_interval

    scheduler.add_job(
        crawl_func,
        trigger=IntervalTrigger(minutes=interval),
        id="backlog_crawl",
        name="Backlog KTC Crawler",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=60,
    )

    if fill_rate_func:
        scheduler.add_job(
            fill_rate_func,
            trigger=IntervalTrigger(minutes=interval),
            id="fill_rate_crawl",
            name="Fill Rate Crawler",
            replace_existing=True,
            max_instances=1,
            misfire_grace_time=60,
        )

    logger.info(f"[SCHEDULE] Configured: crawl every {interval} minutes")


def start_scheduler():
    """Start the scheduler."""
    if not scheduler.running:
        scheduler.start()
        logger.info("[OK] Scheduler started")


def stop_scheduler():
    """Stop the scheduler."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("[STOP] Scheduler stopped")


def update_interval(minutes: int):
    """Update the crawl interval without restart."""
    config.crawl_interval = minutes
    job = scheduler.get_job("backlog_crawl")
    if job:
        job.reschedule(trigger=IntervalTrigger(minutes=minutes))
        logger.info(f"[SCHEDULE] Interval updated to {minutes} minutes")


def get_next_run_time() -> str:
    """Get the next scheduled run time."""
    job = scheduler.get_job("backlog_crawl")
    if job and job.next_run_time:
        return job.next_run_time.isoformat()
    return ""
