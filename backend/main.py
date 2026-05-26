"""
FastAPI application: REST API endpoints + static file serving.
GHN Backlog KTC Operational Dashboard — Google Sheets data source.
"""

import os
import math
import logging
import asyncio
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Query, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pathlib import Path

from config import config, is_backlog_24h
from models import (
    KPIOverview, TrendResponse, TrendPoint,
    SnapshotListResponse, SnapshotRow,
    CrawlerStatus, CrawlIntervalRequest,
    KPIHistoryPoint, SheetConfigRequest,
    BacklogDailyResponse, BacklogDailyPoint,
    SLASummary, SLADailyDetail,
)
from database import (
    init_database, reset_database,
    get_overview_kpi, get_latest_snapshots,
    get_trend_data, get_kpi_history,
    get_backlog_daily, insert_snapshot_batch,
    get_sla_summary, get_distribution_latest,
    get_snapshot_count,
)
from crawler import crawl_backlog_data, crawler_state, parse_sheet_csv
from scheduler import (
    setup_scheduler, start_scheduler, stop_scheduler,
    get_next_run_time, update_interval,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("main")

FRONTEND_DIR = Path(__file__).resolve().parent.parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    logger.info("[STARTUP] Starting GHN Backlog KTC Dashboard...")

    # Check if DB needs schema upgrade (UNIQUE constraint)
    try:
        await init_database()
        count = await get_snapshot_count()
        if count > 0:
            # Test if UNIQUE constraint exists by checking schema
            import aiosqlite
            from config import DB_PATH
            db = await aiosqlite.connect(str(DB_PATH))
            cursor = await db.execute("SELECT sql FROM sqlite_master WHERE name='backlog_snapshots'")
            row = await cursor.fetchone()
            await db.close()
            if row and "UNIQUE" not in (row[0] or ""):
                logger.info("[DB] Schema upgrade needed — resetting database")
                await reset_database()
    except Exception as e:
        logger.warning(f"[DB] Schema check issue: {e}")
        await reset_database()

    # Setup scheduler
    setup_scheduler(crawl_backlog_data)
    start_scheduler()

    # Trigger immediate first crawl
    logger.info("[CRAWL] Triggering initial data fetch from Google Sheets...")
    asyncio.create_task(crawl_backlog_data())

    port = int(os.environ.get("PORT", 8000))
    logger.info(f"[OK] Dashboard ready at http://localhost:{port}")
    yield
    stop_scheduler()
    logger.info("[STOP] Dashboard stopped")


app = FastAPI(
    title="GHN Backlog KTC Dashboard",
    description="Operational Dashboard — Google Sheets data source",
    version="3.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── API Endpoints ──────────────────────────────────────────

@app.get("/api/overview")
async def api_overview(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
):
    """Get KPI overview: total volume, backlog >24h%, avg leadtime."""
    data = await get_overview_kpi(start_date=start_date, end_date=end_date)
    if not data:
        return KPIOverview()
    return KPIOverview(**data)


@app.get("/api/trend")
async def api_trend(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
):
    """Get trend data grouped by date (for line chart)."""
    data = await get_trend_data(start_date=start_date, end_date=end_date)
    points = [TrendPoint(**d) for d in data]
    return TrendResponse(points=points)


@app.get("/api/backlog-daily")
async def api_backlog_daily(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
):
    """Get daily backlog stats for Bar/Pie chart — %volume >24h per day."""
    data = await get_backlog_daily(start_date=start_date, end_date=end_date)
    points = [BacklogDailyPoint(**d) for d in data]
    return BacklogDailyResponse(points=points)


@app.get("/api/snapshots")
async def api_snapshots(
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=1000),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
):
    """Get snapshot data (paginated, searchable, date-filtered)."""
    rows, total = await get_latest_snapshots(
        page, limit, search,
        start_date=start_date, end_date=end_date,
    )
    return SnapshotListResponse(
        rows=[SnapshotRow(**r) for r in rows],
        total=total,
        page=page,
        limit=limit,
        total_pages=math.ceil(total / limit) if total > 0 else 0,
    )


@app.get("/api/kpi-history")
async def api_kpi_history(hours: int = Query(24, ge=1, le=168)):
    """Get KPI history over time."""
    data = await get_kpi_history(hours)
    return [KPIHistoryPoint(**d) for d in data]


@app.get("/api/sla-summary")
async def api_sla_summary(threshold: float = Query(0.5, ge=0.01, le=50)):
    """Get SLA summary with daily breakdown."""
    data = await get_sla_summary(threshold)
    return SLASummary(
        overall_status=data.get("overall_status", "safe"),
        latest_date=data.get("latest_date"),
        latest_backlog_percent=data.get("latest_backlog_percent", 0),
        latest_lead_time=data.get("latest_lead_time", 0),
        latest_total_volume=data.get("latest_total_volume", 0),
        latest_backlog_volume=data.get("latest_backlog_volume", 0),
        avg_backlog_percent_7d=data.get("avg_backlog_percent_7d", 0),
        avg_lead_time_7d=data.get("avg_lead_time_7d", 0),
        trend_direction=data.get("trend_direction", "flat"),
        days_above_threshold=data.get("days_above_threshold", 0),
        total_days=data.get("total_days", 0),
        daily_details=[SLADailyDetail(**d) for d in data.get("daily_details", [])],
    )


@app.get("/api/distribution")
async def api_distribution():
    """Get aging bucket distribution for latest date (doughnut chart)."""
    data = await get_distribution_latest()
    return data


# ── Crawler Management ────────────────────────────────────

@app.get("/api/crawler/status")
async def api_crawler_status():
    """Get crawler status."""
    return CrawlerStatus(
        is_running=crawler_state.is_running,
        last_run_at=crawler_state.last_run_at,
        next_run_at=get_next_run_time(),
        last_duration_seconds=crawler_state.last_duration_seconds,
        last_records_count=crawler_state.last_records_count,
        last_error=crawler_state.last_error,
        consecutive_errors=crawler_state.consecutive_errors,
        crawl_interval_minutes=config.crawl_interval,
        sheet_configured=True,
    )


@app.post("/api/crawler/trigger")
async def api_trigger_crawl():
    """Manually trigger a crawl from Google Sheets."""
    if crawler_state.is_running:
        raise HTTPException(status_code=409, detail="Crawler is already running")
    asyncio.create_task(crawl_backlog_data())
    return {"message": "Crawl triggered", "status": "started"}


@app.post("/api/config/interval")
async def api_update_interval(req: CrawlIntervalRequest):
    """Update the crawl interval."""
    update_interval(req.interval_minutes)
    return {"message": f"Interval updated to {req.interval_minutes} minutes"}


@app.post("/api/config/sheet")
async def api_update_sheet(req: SheetConfigRequest):
    """Update the Google Sheet ID and GID."""
    config.sheet_id = req.sheet_id
    config.set("sheet_gid", req.sheet_gid)
    return {"message": "Sheet config updated"}


@app.get("/api/config")
async def api_get_config():
    """Get current configuration."""
    return config.to_dict()


# ── Static Files ──────────────────────────────────────────

@app.get("/")
async def serve_index():
    index_path = FRONTEND_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return JSONResponse({"error": "Frontend not found"}, status_code=404)


@app.get("/{filename:path}")
async def serve_static(filename: str):
    file_path = FRONTEND_DIR / filename
    if file_path.exists() and file_path.is_file():
        return FileResponse(str(file_path))
    raise HTTPException(status_code=404, detail="File not found")


# ── Run ────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
