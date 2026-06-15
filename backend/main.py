"""
FastAPI application: REST API endpoints + static file serving.
GHN Backlog KTC Operational Dashboard — Google Sheets data source.
"""

import os
import re
import math
import time
import logging
import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Query, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from pathlib import Path

from auth import router as auth_router, get_current_user, PUBLIC_PATHS, PUBLIC_PREFIXES, COOKIE_NAME

from config import config, is_backlog_24h, validate_sheet_id
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
    # Disable docs in production (default to production for safety)
    docs_url="/docs" if os.environ.get("ENVIRONMENT") == "development" else None,
    redoc_url=None,
)

# ── Mount Auth Routes ──────────────────────────────────────
app.include_router(auth_router)


# ── Security Headers Middleware ────────────────────────────

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses per security-rules.md."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' https://cdn.jsdelivr.net https://unpkg.com; "
            "style-src 'self' https://fonts.googleapis.com 'unsafe-inline'; "
            "font-src https://fonts.gstatic.com; "
            "img-src 'self' data: https://lh3.googleusercontent.com; "
            "connect-src 'self'; "
            "frame-src https://accounts.google.com"
        )
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        # Remove server identification headers
        response.headers.pop("server", None)
        response.headers.pop("x-powered-by", None)
        return response


# ── Rate Limiting Middleware ───────────────────────────────

class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    In-memory per-IP rate limiting.
    GET endpoints: 120 req/min
    POST endpoints: 10 req/min
    """

    def __init__(self, app, get_limit: int = 120, post_limit: int = 10, window: int = 60):
        super().__init__(app)
        self.get_limit = get_limit
        self.post_limit = post_limit
        self.window = window
        self._requests: dict = defaultdict(list)  # {ip: [timestamps]}

    def _clean_old(self, ip: str, now: float):
        cutoff = now - self.window
        self._requests[ip] = [t for t in self._requests[ip] if t[0] > cutoff]

    async def dispatch(self, request: Request, call_next):
        # Skip rate limiting for static files
        path = request.url.path
        if not path.startswith("/api/"):
            return await call_next(request)

        ip = request.client.host if request.client else "unknown"
        now = time.time()
        method = request.method.upper()

        self._clean_old(ip, now)

        # Count requests by method type
        method_key = "POST" if method in ("POST", "PUT", "PATCH", "DELETE") else "GET"
        count = sum(1 for t, m in self._requests[ip] if m == method_key)
        limit = self.post_limit if method_key == "POST" else self.get_limit

        if count >= limit:
            retry_after = self.window
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests. Please try again later."},
                headers={"Retry-After": str(retry_after)},
            )

        self._requests[ip].append((now, method_key))
        return await call_next(request)


# ── Authentication Middleware ──────────────────────────────

class AuthMiddleware(BaseHTTPMiddleware):
    """Require authentication on all routes except public paths."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Allow public paths (login, auth callbacks, static assets)
        if path in PUBLIC_PATHS:
            return await call_next(request)
        if any(path.startswith(p) for p in PUBLIC_PREFIXES):
            return await call_next(request)

        # Allow static assets (CSS, JS, fonts, images) without auth
        static_exts = ('.css', '.js', '.woff', '.woff2', '.ttf', '.png', '.jpg', '.svg', '.ico')
        if any(path.endswith(ext) for ext in static_exts):
            return await call_next(request)

        # Check authentication
        user = get_current_user(request)
        if not user:
            # For API calls, return 401 JSON
            if path.startswith("/api/"):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Not authenticated"},
                )
            # For page requests, redirect to login
            return RedirectResponse(url="/login", status_code=302)

        return await call_next(request)


# ── Apply Middleware (order matters: last added = first executed) ──

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(AuthMiddleware)
app.add_middleware(RateLimitMiddleware, get_limit=120, post_limit=10, window=60)

# CORS — restricted origins from env var
_allowed_origins = os.environ.get("ALLOWED_ORIGINS", "http://localhost:8000,http://localhost:3000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _allowed_origins],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Accept"],
)


# ── Global Exception Handler ──────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch-all exception handler. Never expose stack traces or internal details."""
    logger.error(f"Unhandled error on {request.url.path}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


# ── Input Validation Helpers ──────────────────────────────

_DATE_PATTERN = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def validate_date_param(value: Optional[str], name: str) -> Optional[str]:
    """Validate date parameters are in YYYY-MM-DD format."""
    if value is None:
        return None
    value = value.strip()
    if not _DATE_PATTERN.match(value):
        raise HTTPException(status_code=400, detail=f"Invalid {name} format. Use YYYY-MM-DD.")
    return value


def validate_search_param(value: Optional[str]) -> Optional[str]:
    """Validate search parameter: limit length, strip dangerous chars."""
    if value is None:
        return None
    value = value.strip()[:100]  # Max 100 chars
    # Remove any SQL-dangerous patterns (extra safety on top of parameterized queries)
    value = re.sub(r'[;\\\x00]', '', value)
    return value if value else None


# ── API Endpoints ──────────────────────────────────────────

@app.get("/api/overview")
async def api_overview(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
):
    """Get KPI overview: total volume, backlog >24h%, avg leadtime."""
    start_date = validate_date_param(start_date, "start_date")
    end_date = validate_date_param(end_date, "end_date")
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
    start_date = validate_date_param(start_date, "start_date")
    end_date = validate_date_param(end_date, "end_date")
    data = await get_trend_data(start_date=start_date, end_date=end_date)
    points = [TrendPoint(**d) for d in data]
    return TrendResponse(points=points)


@app.get("/api/backlog-daily")
async def api_backlog_daily(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
):
    """Get daily backlog stats for Bar/Pie chart — %volume >24h per day."""
    start_date = validate_date_param(start_date, "start_date")
    end_date = validate_date_param(end_date, "end_date")
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
    start_date = validate_date_param(start_date, "start_date")
    end_date = validate_date_param(end_date, "end_date")
    search = validate_search_param(search)
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
    # Sanitize error message — never expose raw internal errors
    safe_error = None
    if crawler_state.last_error:
        safe_error = str(crawler_state.last_error)[:120]
        # Remove any file paths or sensitive info
        safe_error = re.sub(r'[/\\][a-zA-Z0-9_./-]+', '[path]', safe_error)
    return CrawlerStatus(
        is_running=crawler_state.is_running,
        last_run_at=crawler_state.last_run_at,
        next_run_at=get_next_run_time(),
        last_duration_seconds=crawler_state.last_duration_seconds,
        last_records_count=crawler_state.last_records_count,
        last_error=safe_error,
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
    # SSRF prevention: validate sheet_id format
    if not validate_sheet_id(req.sheet_id):
        raise HTTPException(status_code=400, detail="Invalid sheet_id format. Only alphanumeric, hyphens, and underscores allowed.")
    if not re.match(r'^\d{1,10}$', req.sheet_gid):
        raise HTTPException(status_code=400, detail="Invalid sheet_gid format. Must be a numeric value.")
    config.sheet_id = req.sheet_id
    config.set("sheet_gid", req.sheet_gid)
    return {"message": "Sheet config updated"}


@app.get("/api/config")
async def api_get_config(request: Request):
    """Get current configuration (safe fields only). Requires authentication."""
    # Auth is enforced by AuthMiddleware — this endpoint is no longer public
    return config.to_safe_dict()


# ── Static Files ──────────────────────────────────────────

@app.get("/login")
async def serve_login(request: Request):
    """Serve login page. If already logged in, redirect to dashboard."""
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/", status_code=302)
    login_path = FRONTEND_DIR / "login.html"
    if login_path.exists():
        return FileResponse(str(login_path))
    return JSONResponse({"error": "Login page not found"}, status_code=404)


@app.get("/")
async def serve_index(request: Request):
    """Serve dashboard. Auth is enforced by AuthMiddleware."""
    index_path = FRONTEND_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return JSONResponse({"error": "Frontend not found"}, status_code=404)


@app.get("/{filename:path}")
async def serve_static(filename: str):
    file_path = (FRONTEND_DIR / filename).resolve()
    # Path traversal protection: ensure resolved path is within FRONTEND_DIR
    try:
        file_path.relative_to(FRONTEND_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")
    if file_path.exists() and file_path.is_file():
        return FileResponse(str(file_path))
    raise HTTPException(status_code=404, detail="File not found")


# ── Run ────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
