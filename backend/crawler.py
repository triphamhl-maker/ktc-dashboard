"""
Google Sheets crawler for GHN Backlog KTC data.
Fetches CSV export from published Google Sheet with 5 columns:
  Mốc giờ | Time | Volume | %Volume | LeadTime
"""

import csv
import io
import httpx
import logging
import re
import time as time_mod
from datetime import datetime
from typing import Optional, List, Dict

from config import config, is_backlog_24h, build_sheet_csv_url, build_sheet_tsv_url

logger = logging.getLogger("crawler")


class CrawlerState:
    """Track crawler runtime state."""
    is_running: bool = False
    last_run_at: Optional[str] = None
    last_duration_seconds: Optional[float] = None
    last_records_count: int = 0
    last_error: Optional[str] = None
    consecutive_errors: int = 0


crawler_state = CrawlerState()


async def crawl_backlog_data():
    """
    Main crawl function. Fetches data from Google Sheets.

    Strategy:
    1. Try CSV export URL first
    2. Fallback to gviz/tq CSV URL
    3. Parse 5-column CSV (Mốc giờ, Time, Volume, %Volume, LeadTime)
    4. Insert into database
    """
    from database import insert_snapshot_batch

    if crawler_state.is_running:
        logger.warning("Crawler is already running, skipping...")
        return

    crawler_state.is_running = True
    start_time = time_mod.time()

    try:
        sheet_id = config.sheet_id
        sheet_gid = config.sheet_gid

        urls = [
            build_sheet_csv_url(sheet_id, sheet_gid),
            build_sheet_tsv_url(sheet_id, sheet_gid),
        ]

        headers = {
            "User-Agent": config.get("user_agent"),
            "Accept": "text/csv, text/plain, */*",
        }

        csv_text = None
        max_retries = 3

        async with httpx.AsyncClient(
            timeout=config.get("request_timeout_seconds", 30),
            follow_redirects=True,
        ) as client:
            for url in urls:
                for attempt in range(1, max_retries + 1):
                    try:
                        logger.info(f"Trying ({attempt}/{max_retries}): {url[:80]}...")
                        resp = await client.get(url, headers=headers)
                        if resp.status_code == 200:
                            csv_text = resp.text
                            logger.info(f"[OK] Fetched {len(csv_text)} bytes from Google Sheets")
                            break
                        else:
                            logger.warning(f"HTTP {resp.status_code} from {url[:60]}")
                    except Exception as e:
                        logger.warning(f"Request failed (attempt {attempt}): {e}")
                        if attempt < max_retries:
                            import asyncio
                            await asyncio.sleep(2 ** attempt)  # 2s, 4s backoff
                        continue
                if csv_text:
                    break

        if not csv_text:
            crawler_state.last_error = "Could not fetch data from Google Sheets (sheet may not be public)"
            crawler_state.consecutive_errors += 1
            logger.error("All Google Sheets URLs failed")
            return

        # Parse CSV
        rows = parse_sheet_csv(csv_text)

        if rows:
            count = await insert_snapshot_batch(rows, source="google_sheets")
            elapsed = time_mod.time() - start_time
            crawler_state.last_run_at = datetime.utcnow().isoformat()
            crawler_state.last_duration_seconds = round(elapsed, 2)
            crawler_state.last_records_count = count
            crawler_state.last_error = None
            crawler_state.consecutive_errors = 0
            logger.info(f"[OK] Crawl completed: {count} records in {elapsed:.1f}s")
        else:
            crawler_state.last_error = "No valid data parsed from Google Sheets CSV"
            crawler_state.consecutive_errors += 1
            logger.warning("CSV fetched but no data could be parsed")

    except Exception as e:
        crawler_state.last_error = str(e)
        crawler_state.consecutive_errors += 1
        logger.error(f"[ERROR] Crawl failed: {e}", exc_info=True)
    finally:
        crawler_state.is_running = False


def parse_sheet_csv(text: str) -> List[Dict]:
    """
    Parse Google Sheets CSV with 5 columns:
    Mốc giờ | Time | Volume | %Volume | LeadTime

    Real data format:
      Mốc giờ: "1.0 - 6h", "2.6 - 12h", "3.12 - 24h", "4.>24h"
      Time: "2026-04-01 - Thứ 4" (date + day name)
      Volume: "58904" (integer)
      %Volume: "75.30%" (percentage with % sign)
      LeadTime: "2.498.949.421" (Vietnamese number format, dots as thousand separators)
    """
    # Strip BOM and markdown wrapper if present
    if text.startswith('\ufeff'):
        text = text[1:]
    
    # Find actual CSV start (skip any markdown headers)
    lines = text.split('\n')
    csv_start = 0
    for i, line in enumerate(lines):
        stripped = line.strip().replace('\r', '')
        if 'mốc giờ' in stripped.lower() or 'moc gio' in stripped.lower() or 'time,volume' in stripped.lower():
            csv_start = i
            break
        # Also match the header by checking for column-like structure
        if stripped.count(',') >= 3 and any(kw in stripped.lower() for kw in ['volume', 'leadtime', 'lead time']):
            csv_start = i
            break
    
    csv_text = '\n'.join(lines[csv_start:])
    reader = csv.reader(io.StringIO(csv_text))
    rows_out = []

    # Read header row
    try:
        header = next(reader)
    except StopIteration:
        return []

    # Detect column layout
    header_lower = [h.strip().lower() for h in header]
    col_map = _detect_columns(header_lower)

    if col_map is None:
        logger.warning(f"Could not detect columns from header: {header}")
        return []

    for row in reader:
        if not row or all(c.strip() == '' for c in row):
            continue

        try:
            raw_moc_gio = row[col_map['moc_gio']].strip() if col_map['moc_gio'] < len(row) else ''
            raw_time = row[col_map['time']].strip() if col_map['time'] < len(row) else ''
            raw_vol = row[col_map['volume']].strip() if col_map['volume'] < len(row) else '0'
            raw_pct = row[col_map['percent']].strip() if col_map['percent'] < len(row) else '0'
            raw_lt = row[col_map['leadtime']].strip() if col_map['leadtime'] < len(row) else '0'

            if not raw_moc_gio or not raw_time:
                continue

            # Clean aging bucket: "1.0 - 6h" → "0 - 6h", "4.>24h" → ">24h"
            moc_gio = _clean_aging_bucket(raw_moc_gio)

            # Clean time: "2026-04-01 - Thứ 4" → "2026-04-01"
            time_date = _normalize_date(raw_time)

            # Parse volume (simple integer)
            volume = _safe_int(raw_vol)

            # Parse percent: "75.30%" → 0.7530
            pct = _parse_percent(raw_pct)

            # Parse leadtime: Vietnamese format "2.498.949.421" 
            # This is actually a decimal number with dots as thousand separators
            lt = _parse_leadtime(raw_lt)

            rows_out.append({
                'aging_bucket': moc_gio,
                'time_date': time_date,
                'volume': volume,
                'percent_volume': pct,
                'lead_time': lt,
            })
        except (IndexError, ValueError) as e:
            logger.debug(f"Skipping row: {row} — {e}")
            continue

    return rows_out


def _detect_columns(header_lower: List[str]) -> Optional[Dict[str, int]]:
    """Detect column indices from header names."""

    # Strategy 1: Named columns
    idx_map = {}
    for i, h in enumerate(header_lower):
        if "mốc" in h or "moc" in h or "aging" in h or "khoảng" in h:
            idx_map["moc_gio"] = i
        elif h in ("time", "ngày", "ngay", "date") or "ngày" in h:
            idx_map["time"] = i
        elif h == "volume" or "sản lượng" in h or "số lượng" in h:
            idx_map["volume"] = i
        elif "%" in h or "tỷ lệ" in h or "percent" in h:
            idx_map["percent"] = i
        elif "leadtime" in h or "lead time" in h or "lead_time" in h:
            idx_map["leadtime"] = i

    if all(k in idx_map for k in ("moc_gio", "time", "volume", "percent", "leadtime")):
        return idx_map

    # Strategy 2: Positional fallback for 5-column layout
    n = len(header_lower)
    if n == 5:
        return {"moc_gio": 0, "time": 1, "volume": 2, "percent": 3, "leadtime": 4}
    # 6-column layout (with Chi tiết first)
    if n >= 6:
        return {"moc_gio": 1, "time": 2, "volume": 3, "percent": 4, "leadtime": 5}

    return None


def _normalize_date(val: str) -> str:
    """Normalize date string to YYYY-MM-DD format.
    Handles: '2026-04-01 - Thứ 4', '2026-04-01', '01/04/2026', etc.
    """
    val = val.strip()
    # Strip day name: "2026-04-01 - Thứ 4" → "2026-04-01"
    if ' - ' in val:
        val = val.split(' - ')[0].strip()
    # Already in YYYY-MM-DD
    if re.match(r'^\d{4}-\d{2}-\d{2}', val):
        return val[:10]
    # DD/MM/YYYY
    m = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{4})', val)
    if m:
        return f"{m.group(3)}-{m.group(2).zfill(2)}-{m.group(1).zfill(2)}"
    try:
        dt = datetime.fromisoformat(val.replace('Z', '+00:00'))
        return dt.strftime('%Y-%m-%d')
    except (ValueError, TypeError):
        pass
    return val


def _clean_aging_bucket(val: str) -> str:
    """Clean aging bucket label.
    '1.0 - 6h' → '0 - 6h'
    '2.6 - 12h' → '6 - 12h'
    '3.12 - 24h' → '12 - 24h'
    '4.>24h' → '>24h'
    """
    val = val.strip()
    # Remove numeric prefix like "1.", "2.", "3.", "4."
    cleaned = re.sub(r'^\d+\.\s*', '', val)
    return cleaned


def _parse_percent(val: str) -> float:
    """Parse percentage string.
    '75.30%' → 0.7530
    '0.05%' → 0.0005
    '75.30' → 0.7530 (if > 1)
    '0.45' → 0.45 (if <= 1, treat as ratio)
    """
    val = val.strip().replace('%', '').replace(',', '.')
    try:
        num = float(val)
        if num > 1:
            return num / 100
        return num
    except (ValueError, TypeError):
        return 0.0


def _parse_leadtime(val: str) -> float:
    """Parse LeadTime from Vietnamese number format.
    Vietnamese format uses dots as thousand separators:
    '2.498.949.421' — this represents a large number.
    
    In GHN context, leadtime is typically in hours (e.g., 2.5h, 8.7h).
    The sheet uses dots as thousand separators for microsecond-precision:
    '2.498.949.421' likely means ~2.5 hours.
    
    Strategy: If value has multiple dots, treat dots as thousand separators
    and the result as the raw number, then divide by 1 billion to get hours.
    If it looks like a simple decimal, use as-is.
    """
    val = val.strip()
    if not val:
        return 0.0
    
    dot_count = val.count('.')
    
    if dot_count >= 2:
        # Vietnamese thousand-separator format: "2.498.949.421"
        # Remove all dots to get raw number, then interpret
        raw = val.replace('.', '')
        try:
            num = int(raw)
            # Convert from nanoseconds/microseconds to hours
            # 2498949421 → ~2.5 (hours)
            return round(num / 1_000_000_000, 2)
        except (ValueError, TypeError):
            return 0.0
    else:
        # Simple decimal: "2.5" or "8.73"
        try:
            return float(val.replace(',', '.'))
        except (ValueError, TypeError):
            return 0.0


def _safe_int(val) -> int:
    if not val:
        return 0
    try:
        # Remove thousand separators (dots in VN format) — but only for pure integers
        cleaned = re.sub(r'[^\d]', '', str(val))
        return int(cleaned) if cleaned else 0
    except (ValueError, TypeError):
        return 0


def _safe_float(val) -> float:
    if not val:
        return 0.0
    try:
        return float(str(val).replace(',', '.').strip())
    except (ValueError, TypeError):
        return 0.0
