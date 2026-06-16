"""
Script to fetch data from Google Sheets and update the local SQLite database.
Uses the same parsing logic as the crawler but runs as a standalone script.
"""

import csv
import io
import re
import sqlite3
import httpx
import sys
from datetime import datetime
from pathlib import Path

# ── Configuration ──────────────────────────────────────────
SHEET_ID = "16nhZJyAiCX7xzBujieAF1AOas6bgh2-4X6ePQixWHJE"
SHEET_GID = "0"
DB_PATH = Path(__file__).resolve().parent / "data" / "backlog.db"

BACKLOG_PATTERNS = [">24", "> 24", "trên 24", "tren 24", "over 24"]


def is_backlog_24h(moc_gio: str) -> bool:
    if not moc_gio:
        return False
    s = str(moc_gio).lower().strip()
    return any(pattern in s for pattern in BACKLOG_PATTERNS)


# ── Parsing Helpers (from crawler.py) ──────────────────────

def _normalize_date(val: str) -> str:
    val = val.strip()
    if ' - ' in val:
        val = val.split(' - ')[0].strip()
    if re.match(r'^\d{4}-\d{2}-\d{2}', val):
        return val[:10]
    m = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{4})', val)
    if m:
        return f"{m.group(3)}-{m.group(2).zfill(2)}-{m.group(1).zfill(2)}"
    return val


def _clean_aging_bucket(val: str) -> str:
    val = val.strip()
    cleaned = re.sub(r'^\d+\.\s*', '', val)
    return cleaned


def _parse_percent(val: str) -> float:
    val = val.strip().replace('%', '').replace(',', '.')
    try:
        num = float(val)
        if num > 1:
            return num / 100
        return num
    except (ValueError, TypeError):
        return 0.0


def _parse_leadtime(val: str) -> float:
    val = val.strip()
    if not val:
        return 0.0
    dot_count = val.count('.')
    if dot_count >= 2:
        raw = val.replace('.', '')
        try:
            num = int(raw)
            return round(num / 1_000_000_000, 2)
        except (ValueError, TypeError):
            return 0.0
    else:
        try:
            return float(val.replace(',', '.'))
        except (ValueError, TypeError):
            return 0.0


def _safe_int(val) -> int:
    if not val:
        return 0
    try:
        cleaned = re.sub(r'[^\d]', '', str(val))
        return int(cleaned) if cleaned else 0
    except (ValueError, TypeError):
        return 0


def parse_csv(text: str) -> list:
    """Parse the CSV text from Google Sheets."""
    if text.startswith('\ufeff'):
        text = text[1:]

    lines = text.split('\n')
    csv_start = 0
    for i, line in enumerate(lines):
        stripped = line.strip().replace('\r', '')
        if 'mốc giờ' in stripped.lower() or 'moc gio' in stripped.lower():
            csv_start = i
            break
        if stripped.count(',') >= 3 and any(kw in stripped.lower() for kw in ['volume', 'leadtime']):
            csv_start = i
            break

    csv_text = '\n'.join(lines[csv_start:])
    reader = csv.reader(io.StringIO(csv_text))
    rows_out = []

    try:
        header = next(reader)
    except StopIteration:
        return []

    # Detect columns
    header_lower = [h.strip().lower() for h in header]
    col_map = None
    idx_map = {}
    for i, h in enumerate(header_lower):
        if "mốc" in h or "moc" in h or "aging" in h:
            idx_map["moc_gio"] = i
        elif h in ("time", "ngày", "date") or "ngày" in h:
            idx_map["time"] = i
        elif h == "volume" or "sản lượng" in h:
            idx_map["volume"] = i
        elif "%" in h or "percent" in h:
            idx_map["percent"] = i
        elif "leadtime" in h or "lead time" in h:
            idx_map["leadtime"] = i

    if all(k in idx_map for k in ("moc_gio", "time", "volume", "percent", "leadtime")):
        col_map = idx_map
    elif len(header_lower) == 5:
        col_map = {"moc_gio": 0, "time": 1, "volume": 2, "percent": 3, "leadtime": 4}

    if col_map is None:
        print(f"ERROR: Could not detect columns from header: {header}")
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

            moc_gio = _clean_aging_bucket(raw_moc_gio)
            time_date = _normalize_date(raw_time)
            volume = _safe_int(raw_vol)
            pct = _parse_percent(raw_pct)
            lt = _parse_leadtime(raw_lt)

            rows_out.append({
                'aging_bucket': moc_gio,
                'time_date': time_date,
                'volume': volume,
                'percent_volume': pct,
                'lead_time': lt,
            })
        except (IndexError, ValueError) as e:
            continue

    return rows_out


def main():
    print("=" * 60)
    print("  GHN Backlog KTC - Data Update Script")
    print("=" * 60)
    print(f"\n[INFO] Google Sheet: {SHEET_ID}")
    print(f"[INFO] Database: {DB_PATH}")

    # Step 1: Fetch CSV from Google Sheets
    print("\n[1/3] Fetching data from Google Sheets...")
    csv_url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={SHEET_GID}"
    
    try:
        resp = httpx.get(csv_url, follow_redirects=True, timeout=30, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0.0.0"
        })
        if resp.status_code != 200:
            print(f"   [FAIL] HTTP {resp.status_code} - failed to fetch sheet")
            sys.exit(1)
        csv_text = resp.text
        print(f"   [OK] Fetched {len(csv_text):,} bytes")
    except Exception as e:
        print(f"   [FAIL] Error: {e}")
        sys.exit(1)

    # Step 2: Parse CSV
    print("\n[2/3] Parsing CSV data...")
    rows = parse_csv(csv_text)
    print(f"   [OK] Parsed {len(rows)} records")

    if not rows:
        print("   [FAIL] No data parsed! Aborting.")
        sys.exit(1)

    # Show sample
    unique_dates = sorted(set(r['time_date'] for r in rows))
    unique_buckets = sorted(set(r['aging_bucket'] for r in rows))
    print(f"   Date range: {unique_dates[0]} -> {unique_dates[-1]} ({len(unique_dates)} days)")
    print(f"   Aging buckets: {', '.join(unique_buckets)}")

    # Step 3: Insert into database
    print(f"\n[3/3] Inserting into database...")
    
    # Ensure data directory exists
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()

    # Create table if not exists
    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS backlog_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            aging_bucket TEXT NOT NULL,
            time_date TEXT NOT NULL,
            volume INTEGER DEFAULT 0,
            percent_volume REAL DEFAULT 0,
            lead_time REAL DEFAULT 0,
            is_backlog INTEGER DEFAULT 0,
            crawled_at DATETIME DEFAULT (datetime('now')),
            source TEXT DEFAULT 'crawl',
            UNIQUE(aging_bucket, time_date)
        );

        CREATE TABLE IF NOT EXISTS kpi_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            crawled_at DATETIME DEFAULT (datetime('now')),
            total_volume INTEGER DEFAULT 0,
            backlog_gt24h_volume INTEGER DEFAULT 0,
            backlog_gt24h_percent REAL DEFAULT 0,
            avg_lead_time REAL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS crawl_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            crawled_at DATETIME DEFAULT (datetime('now')),
            records_count INTEGER DEFAULT 0,
            duration_seconds REAL DEFAULT 0,
            status TEXT DEFAULT 'success',
            error_message TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_snapshot_time
            ON backlog_snapshots(time_date);
        CREATE INDEX IF NOT EXISTS idx_snapshot_bucket_date
            ON backlog_snapshots(aging_bucket, time_date);
        CREATE INDEX IF NOT EXISTS idx_kpi_crawled
            ON kpi_history(crawled_at);
    """)

    now = datetime.utcnow().isoformat()
    count = 0

    for row in rows:
        is_bl = 1 if is_backlog_24h(row['aging_bucket']) else 0
        cursor.execute(
            """INSERT OR REPLACE INTO backlog_snapshots
               (aging_bucket, time_date, volume, percent_volume,
                lead_time, is_backlog, crawled_at, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row['aging_bucket'],
                row['time_date'],
                row['volume'],
                row['percent_volume'],
                row['lead_time'],
                is_bl,
                now,
                'google_sheets',
            ),
        )
        count += 1

    # Compute and save KPI history
    total_vol = sum(r['volume'] for r in rows)
    backlog_vol = sum(r['volume'] for r in rows if is_backlog_24h(r['aging_bucket']))
    backlog_pct = (backlog_vol / total_vol * 100) if total_vol > 0 else 0
    weighted_lt = sum(r['lead_time'] * r['volume'] for r in rows)
    avg_lt = (weighted_lt / total_vol) if total_vol > 0 else 0

    cursor.execute(
        """INSERT INTO kpi_history
           (crawled_at, total_volume, backlog_gt24h_volume,
            backlog_gt24h_percent, avg_lead_time)
           VALUES (?, ?, ?, ?, ?)""",
        (now, total_vol, backlog_vol, round(backlog_pct, 4), round(avg_lt, 2)),
    )

    # Log the crawl
    cursor.execute(
        """INSERT INTO crawl_log
           (crawled_at, records_count, duration_seconds, status)
           VALUES (?, ?, ?, ?)""",
        (now, count, 0, 'success'),
    )

    conn.commit()

    # Verify
    cursor.execute("SELECT COUNT(*) FROM backlog_snapshots")
    total_records = cursor.fetchone()[0]
    
    cursor.execute("SELECT MIN(time_date), MAX(time_date) FROM backlog_snapshots")
    date_range = cursor.fetchone()

    conn.close()

    print(f"   [OK] Upserted {count} records")
    print(f"\n{'=' * 60}")
    print(f"  [OK] UPDATE COMPLETE")
    print(f"{'=' * 60}")
    print(f"   Total records in DB: {total_records}")
    print(f"   Date range: {date_range[0]} -> {date_range[1]}")
    print(f"   Total volume: {total_vol:,}")
    print(f"   Backlog >24h: {backlog_vol:,} ({backlog_pct:.2f}%)")
    print(f"   Avg Lead Time: {avg_lt:.2f}h")
    print(f"\n   Now commit & push to redeploy on Render:")
    print(f"   git add . && git commit -m \"Update data from Google Sheets\" && git push")


if __name__ == "__main__":
    main()
