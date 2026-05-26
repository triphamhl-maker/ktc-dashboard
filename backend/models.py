"""
Pydantic models for API request/response schemas.
Data source: Google Sheets with 5 columns (Mốc giờ, Time, Volume, %Volume, LeadTime).
No warehouse dimension — single KTC view.
"""

from pydantic import BaseModel, Field
from typing import Optional, List


# ── Request Models ──────────────────────────────────────────

class CrawlIntervalRequest(BaseModel):
    interval_minutes: int = Field(..., ge=1, le=60, description="Crawl interval in minutes")


class SheetConfigRequest(BaseModel):
    sheet_id: str = Field(..., description="Google Sheet ID")
    sheet_gid: str = Field("0", description="Google Sheet GID (tab)")


# ── Response Models ─────────────────────────────────────────

class KPIOverview(BaseModel):
    total_volume: int = 0
    backlog_gt24h_volume: int = 0
    backlog_gt24h_percent: float = 0.0
    avg_lead_time: float = 0.0
    total_lead_time: float = 0.0
    latest_date: Optional[str] = None
    crawled_at: Optional[str] = None
    # Changes vs previous crawl
    volume_change: int = 0
    backlog_percent_change: float = 0.0
    lead_time_change: float = 0.0


class SnapshotRow(BaseModel):
    id: Optional[int] = None
    aging_bucket: str = ""
    time_date: str = ""
    volume: int = 0
    percent_volume: float = 0.0
    lead_time: float = 0.0
    is_backlog: bool = False
    crawled_at: Optional[str] = None


class SnapshotListResponse(BaseModel):
    rows: List[SnapshotRow] = []
    total: int = 0
    page: int = 1
    limit: int = 50
    total_pages: int = 0


class TrendPoint(BaseModel):
    time_date: str
    backlog_percent: float = 0.0
    total_volume: int = 0
    backlog_volume: int = 0
    avg_lead_time: float = 0.0


class TrendResponse(BaseModel):
    points: List[TrendPoint] = []


class BacklogDailyPoint(BaseModel):
    """For Bar/Pie chart: %volume >24h per day."""
    time_date: str
    total_volume: int = 0
    backlog_volume: int = 0
    backlog_percent: float = 0.0
    total_lead_time: float = 0.0


class BacklogDailyResponse(BaseModel):
    points: List[BacklogDailyPoint] = []


class CrawlerStatus(BaseModel):
    is_running: bool = False
    last_run_at: Optional[str] = None
    next_run_at: Optional[str] = None
    last_duration_seconds: Optional[float] = None
    last_records_count: int = 0
    last_error: Optional[str] = None
    consecutive_errors: int = 0
    crawl_interval_minutes: int = 10
    sheet_configured: bool = True


class KPIHistoryPoint(BaseModel):
    crawled_at: str
    total_volume: int = 0
    backlog_gt24h_volume: int = 0
    backlog_gt24h_percent: float = 0.0
    avg_lead_time: float = 0.0


# ── SLA Models ──────────────────────────────────────────────

class SLADailyDetail(BaseModel):
    """SLA status per day."""
    time_date: str
    total_volume: int = 0
    backlog_volume: int = 0
    backlog_percent: float = 0.0
    avg_lead_time: float = 0.0
    sla_status: str = "safe"  # "safe" | "warning" | "danger"


class SLASummary(BaseModel):
    """Overall SLA summary across all data."""
    overall_status: str = "safe"
    latest_date: Optional[str] = None
    latest_backlog_percent: float = 0.0
    latest_lead_time: float = 0.0
    latest_total_volume: int = 0
    latest_backlog_volume: int = 0
    avg_backlog_percent_7d: float = 0.0
    avg_lead_time_7d: float = 0.0
    trend_direction: str = "flat"  # "up" | "down" | "flat"
    days_above_threshold: int = 0
    total_days: int = 0
    daily_details: List[SLADailyDetail] = []
