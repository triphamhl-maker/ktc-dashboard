"""
Configuration management for the GHN Backlog KTC Dashboard.
Supports runtime updates via API without restart.
Data source: Google Sheets (published CSV export).
"""

import os
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

DB_PATH = DATA_DIR / "backlog.db"
CONFIG_FILE = DATA_DIR / "config.json"

# Google Sheet configuration
SHEET_ID = "16nhZJyAiCX7xzBujieAF1AOas6bgh2-4X6ePQixWHJE"
SHEET_GID = "0"

# Default configuration
DEFAULT_CONFIG = {
    "sheet_id": SHEET_ID,
    "sheet_gid": SHEET_GID,
    "crawl_interval_minutes": 10,
    "request_timeout_seconds": 30,
    "user_agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
}

# Aging bucket patterns — support multiple formats
AGING_BUCKETS_STANDARD = ["0-6h", "6-12h", "12-24h", ">24h"]
AGING_BUCKETS_DETAILED = ["0h - 4h", "4h - 8h", "8h - 12h", "12h - 24h", ">24h"]

# Mapping to identify >24h backlog records
BACKLOG_PATTERNS = [">24", "> 24", "trên 24", "tren 24", "over 24"]


def is_backlog_24h(moc_gio: str) -> bool:
    """Check if an aging bucket represents >24h backlog."""
    if not moc_gio:
        return False
    s = str(moc_gio).lower().strip()
    return any(pattern in s for pattern in BACKLOG_PATTERNS)


def build_sheet_csv_url(sheet_id: str = None, gid: str = None) -> str:
    """Build the CSV export URL for a Google Sheet."""
    sid = sheet_id or SHEET_ID
    g = gid or SHEET_GID
    return f"https://docs.google.com/spreadsheets/d/{sid}/export?format=csv&gid={g}"


def build_sheet_tsv_url(sheet_id: str = None, gid: str = None) -> str:
    """Build the TSV export URL (alternative fallback)."""
    sid = sheet_id or SHEET_ID
    g = gid or SHEET_GID
    return f"https://docs.google.com/spreadsheets/d/{sid}/gviz/tq?tqx=out:csv&gid={g}"


class AppConfig:
    """Runtime configuration manager with file persistence."""

    def __init__(self):
        self._config = dict(DEFAULT_CONFIG)
        self._load()

    def _load(self):
        """Load config from file if exists."""
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                self._config.update(saved)
            except (json.JSONDecodeError, IOError):
                pass

    def _save(self):
        """Persist config to file."""
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(self._config, f, indent=2, ensure_ascii=False)

    def get(self, key: str, default=None):
        return self._config.get(key, default)

    def set(self, key: str, value):
        self._config[key] = value
        self._save()

    @property
    def sheet_id(self) -> str:
        return self._config.get("sheet_id", SHEET_ID)

    @sheet_id.setter
    def sheet_id(self, value: str):
        self._config["sheet_id"] = value
        self._save()

    @property
    def sheet_gid(self) -> str:
        return self._config.get("sheet_gid", SHEET_GID)

    @property
    def crawl_interval(self) -> int:
        return self._config.get("crawl_interval_minutes", 10)

    @crawl_interval.setter
    def crawl_interval(self, minutes: int):
        self._config["crawl_interval_minutes"] = minutes
        self._save()

    def to_dict(self) -> dict:
        """Return safe config."""
        return dict(self._config)


# Singleton
config = AppConfig()
