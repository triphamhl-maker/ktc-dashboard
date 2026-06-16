"""
SQLite database management for GHN Backlog KTC Dashboard.
Simplified schema: no warehouse dimension — single KTC view.
Data columns: aging_bucket, time_date, volume, percent_volume, lead_time.
Uses UNIQUE constraint to prevent duplicate data on re-crawl.
"""

import aiosqlite
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict

from config import DB_PATH, is_backlog_24h

logger = logging.getLogger("database")


async def get_db() -> aiosqlite.Connection:
    """Get async database connection."""
    db = await aiosqlite.connect(str(DB_PATH))
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    return db


# ── Schema ─────────────────────────────────────────────────

async def init_database():
    """Create tables and indexes if they don't exist."""
    db = await get_db()
    try:
        await db.executescript("""
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

            CREATE TABLE IF NOT EXISTS fill_rate (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trip_date TEXT NOT NULL,
                trip_code TEXT NOT NULL,
                route_code TEXT,
                route_name TEXT,
                vehicle_type TEXT,
                route_detail TEXT,
                license_plate TEXT,
                capacity INTEGER DEFAULT 0,
                std_orders INTEGER DEFAULT 0,
                std_weight INTEGER DEFAULT 0,
                actual_orders INTEGER DEFAULT 0,
                fill_rate_weight REAL DEFAULT 0,
                fill_rate_order REAL DEFAULT 0,
                crawled_at DATETIME DEFAULT (datetime('now')),
                UNIQUE(trip_date, trip_code)
            );

            CREATE INDEX IF NOT EXISTS idx_snapshot_time
                ON backlog_snapshots(time_date);
            CREATE INDEX IF NOT EXISTS idx_snapshot_bucket_date
                ON backlog_snapshots(aging_bucket, time_date);
            CREATE INDEX IF NOT EXISTS idx_kpi_crawled
                ON kpi_history(crawled_at);
            CREATE INDEX IF NOT EXISTS idx_fill_rate_date
                ON fill_rate(trip_date);
            CREATE INDEX IF NOT EXISTS idx_fill_rate_weight
                ON fill_rate(fill_rate_weight);
        """)
        await db.commit()
        logger.info("[DB] Schema initialized")
    finally:
        await db.close()


async def reset_database():
    """Drop and recreate all tables (used when upgrading schema)."""
    db = await get_db()
    try:
        await db.executescript("""
            DROP TABLE IF EXISTS backlog_snapshots;
            DROP TABLE IF EXISTS kpi_history;
            DROP TABLE IF EXISTS crawl_log;
            DROP TABLE IF EXISTS fill_rate;
        """)
        await db.commit()
        logger.info("[DB] Tables dropped for schema upgrade")
    finally:
        await db.close()
    await init_database()


# ── Data Insertion ─────────────────────────────────────────

async def insert_snapshot_batch(rows: List[Dict], source: str = "crawl"):
    """Insert or update a batch of snapshot rows. Uses INSERT OR REPLACE
    to avoid duplicates on (aging_bucket, time_date)."""
    if not rows:
        return 0

    db = await get_db()
    try:
        now = datetime.utcnow().isoformat()
        count = 0

        for row in rows:
            is_bl = 1 if is_backlog_24h(row.get("aging_bucket", "")) else 0
            await db.execute(
                """INSERT OR REPLACE INTO backlog_snapshots
                   (aging_bucket, time_date, volume, percent_volume,
                    lead_time, is_backlog, crawled_at, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row.get("aging_bucket", ""),
                    row.get("time_date", ""),
                    row.get("volume", 0),
                    row.get("percent_volume", 0.0),
                    row.get("lead_time", 0.0),
                    is_bl,
                    now,
                    source,
                ),
            )
            count += 1

        # Compute and save KPI history
        total_vol = sum(r.get("volume", 0) for r in rows)
        backlog_vol = sum(
            r.get("volume", 0) for r in rows
            if is_backlog_24h(r.get("aging_bucket", ""))
        )
        backlog_pct = (backlog_vol / total_vol * 100) if total_vol > 0 else 0

        weighted_lt = sum(r.get("lead_time", 0) * r.get("volume", 0) for r in rows)
        avg_lt = (weighted_lt / total_vol) if total_vol > 0 else 0

        await db.execute(
            """INSERT INTO kpi_history
               (crawled_at, total_volume, backlog_gt24h_volume,
                backlog_gt24h_percent, avg_lead_time)
               VALUES (?, ?, ?, ?, ?)""",
            (now, total_vol, backlog_vol, round(backlog_pct, 4), round(avg_lt, 2)),
        )

        await db.commit()
        logger.info(f"[DB] Upserted {count} records")
        return count
    finally:
        await db.close()


# ── Query Helpers ──────────────────────────────────────────

async def get_latest_snapshots(
    page: int = 1,
    limit: int = 50,
    search: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> tuple:
    """Get all snapshot rows (paginated, searchable, date-filtered)."""
    db = await get_db()
    try:
        conditions = []
        params = []

        if search:
            conditions.append("(aging_bucket LIKE ? OR time_date LIKE ?)")
            params.extend([f"%{search}%", f"%{search}%"])
        if start_date:
            conditions.append("time_date >= ?")
            params.append(start_date)
        if end_date:
            conditions.append("time_date <= ?")
            params.append(end_date)

        where = " AND ".join(conditions) if conditions else "1=1"

        # Count
        cursor = await db.execute(
            f"SELECT COUNT(*) FROM backlog_snapshots WHERE {where}",
            params,
        )
        total = (await cursor.fetchone())[0]

        # Data
        offset = (page - 1) * limit
        cursor = await db.execute(
            f"""SELECT id, aging_bucket, time_date, volume,
                       percent_volume, lead_time, is_backlog, crawled_at
                FROM backlog_snapshots
                WHERE {where}
                ORDER BY time_date DESC, aging_bucket
                LIMIT ? OFFSET ?""",
            params + [limit, offset],
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows], total
    finally:
        await db.close()


async def get_overview_kpi(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Dict:
    """Get KPI overview — aggregates across selected date range.
    
    When a date range is specified, KPIs are summed/averaged across ALL days
    in the range. When no range is specified, shows the latest day only.
    """
    db = await get_db()
    try:
        # Build date filter
        date_conds = []
        date_params = []
        if start_date:
            date_conds.append("time_date >= ?")
            date_params.append(start_date)
        if end_date:
            date_conds.append("time_date <= ?")
            date_params.append(end_date)
        date_where = (" AND " + " AND ".join(date_conds)) if date_conds else ""
        has_date_filter = bool(start_date or end_date)

        # Get totals within range
        cursor = await db.execute(
            f"""SELECT
                SUM(volume) as total_volume,
                SUM(CASE WHEN is_backlog = 1 THEN volume ELSE 0 END) as backlog_vol,
                SUM(lead_time * volume) as weighted_lt,
                MAX(time_date) as latest_date,
                MIN(time_date) as earliest_date,
                MAX(crawled_at) as crawled_at,
                COUNT(DISTINCT time_date) as day_count
               FROM backlog_snapshots WHERE 1=1{date_where}""",
            date_params,
        )
        data = dict(await cursor.fetchone())
        range_vol = data.get("total_volume") or 0
        if range_vol == 0:
            return {}

        range_bl = data.get("backlog_vol") or 0
        range_pct = (range_bl / range_vol * 100) if range_vol > 0 else 0
        range_lt = (data.get("weighted_lt") or 0) / range_vol if range_vol > 0 else 0
        latest_date = data.get("latest_date")
        earliest_date = data.get("earliest_date")
        day_count = data.get("day_count") or 1

        # Total lead time summed across range
        cursor = await db.execute(
            f"""SELECT SUM(lead_time) as total_lt
               FROM backlog_snapshots WHERE 1=1{date_where}""",
            date_params,
        )
        lt_row = dict(await cursor.fetchone())
        total_lt = lt_row.get("total_lt") or 0

        if has_date_filter:
            # ── Date range mode: aggregate across ALL days in range ──
            display_vol = range_vol
            display_bl = range_bl
            display_pct = range_pct
            display_lt = range_lt
            display_label = f"{earliest_date} → {latest_date}" if earliest_date != latest_date else latest_date

            # Change: compare the range vs an equivalent period before it
            if start_date and end_date:
                from datetime import datetime as dt, timedelta
                try:
                    sd = dt.strptime(start_date, "%Y-%m-%d")
                    ed = dt.strptime(end_date, "%Y-%m-%d")
                    span = (ed - sd).days + 1
                    prev_end = sd - timedelta(days=1)
                    prev_start = prev_end - timedelta(days=span - 1)
                    ps = prev_start.strftime("%Y-%m-%d")
                    pe = prev_end.strftime("%Y-%m-%d")

                    cursor = await db.execute(
                        """SELECT SUM(volume) as pv,
                                  SUM(CASE WHEN is_backlog = 1 THEN volume ELSE 0 END) as pb,
                                  SUM(lead_time * volume) as pwlt
                           FROM backlog_snapshots
                           WHERE time_date >= ? AND time_date <= ?""",
                        (ps, pe),
                    )
                    prev = dict(await cursor.fetchone())
                    pv = prev.get("pv") or 0
                    pb = prev.get("pb") or 0
                    prev_pct = (pb / pv * 100) if pv > 0 else 0
                    prev_lt = (prev.get("pwlt") or 0) / pv if pv > 0 else 0

                    vol_change = display_vol - pv
                    pct_change = round(display_pct - prev_pct, 4)
                    lt_change = round(display_lt - prev_lt, 2)
                except Exception:
                    vol_change = 0
                    pct_change = 0.0
                    lt_change = 0.0
            else:
                vol_change = 0
                pct_change = 0.0
                lt_change = 0.0
        else:
            # ── No filter: show latest day only (original behavior) ──
            cursor = await db.execute(
                """SELECT SUM(volume) as day_vol,
                          SUM(CASE WHEN is_backlog = 1 THEN volume ELSE 0 END) as day_bl
                   FROM backlog_snapshots WHERE time_date = ?""",
                (latest_date,),
            )
            day_data = dict(await cursor.fetchone())
            display_vol = day_data.get("day_vol") or 0
            display_bl = day_data.get("day_bl") or 0
            display_pct = (display_bl / display_vol * 100) if display_vol > 0 else 0
            display_lt = range_lt
            display_label = latest_date

            # Compare vs previous day
            cursor = await db.execute(
                """SELECT time_date FROM backlog_snapshots
                   WHERE time_date < ?
                   GROUP BY time_date
                   ORDER BY time_date DESC LIMIT 1""",
                (latest_date,),
            )
            prev_date_row = await cursor.fetchone()
            vol_change = 0
            pct_change = 0.0
            lt_change = 0.0

            if prev_date_row:
                prev_date = prev_date_row[0]
                cursor = await db.execute(
                    """SELECT SUM(volume) as pv,
                              SUM(CASE WHEN is_backlog = 1 THEN volume ELSE 0 END) as pb,
                              SUM(lead_time * volume) as pwlt
                       FROM backlog_snapshots WHERE time_date = ?""",
                    (prev_date,),
                )
                prev = dict(await cursor.fetchone())
                pv = prev.get("pv") or 0
                pb = prev.get("pb") or 0
                prev_pct = (pb / pv * 100) if pv > 0 else 0
                prev_lt = (prev.get("pwlt") or 0) / pv if pv > 0 else 0

                vol_change = display_vol - pv
                pct_change = round(display_pct - prev_pct, 4)
                lt_change = round(display_lt - prev_lt, 2)

        return {
            "total_volume": display_vol,
            "backlog_gt24h_volume": display_bl,
            "backlog_gt24h_percent": round(display_pct, 4),
            "avg_lead_time": round(display_lt, 2),
            "total_lead_time": round(total_lt, 2),
            "latest_date": display_label,
            "crawled_at": data.get("crawled_at"),
            "volume_change": vol_change,
            "backlog_percent_change": pct_change,
            "lead_time_change": lt_change,
        }
    finally:
        await db.close()


async def get_trend_data(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> List[Dict]:
    """Get trend data grouped by time_date (for line chart), with optional date range."""
    db = await get_db()
    try:
        conds = []
        params = []
        if start_date:
            conds.append("time_date >= ?")
            params.append(start_date)
        if end_date:
            conds.append("time_date <= ?")
            params.append(end_date)
        where = ("WHERE " + " AND ".join(conds)) if conds else ""

        cursor = await db.execute(
            f"""SELECT time_date,
                SUM(volume) as total_volume,
                SUM(CASE WHEN is_backlog = 1 THEN volume ELSE 0 END) as backlog_volume,
                SUM(lead_time * volume) as weighted_lt
               FROM backlog_snapshots
               {where}
               GROUP BY time_date
               ORDER BY time_date ASC""",
            params,
        )
        rows = await cursor.fetchall()
        result = []
        for r in rows:
            d = dict(r)
            tv = d.get("total_volume") or 0
            bv = d.get("backlog_volume") or 0
            bp = (bv / tv * 100) if tv > 0 else 0
            al = (d.get("weighted_lt") or 0) / tv if tv > 0 else 0
            result.append({
                "time_date": d["time_date"],
                "total_volume": tv,
                "backlog_volume": bv,
                "backlog_percent": round(bp, 4),
                "avg_lead_time": round(al, 2),
            })
        return result
    finally:
        await db.close()


async def get_backlog_daily(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> List[Dict]:
    """Get daily backlog stats for Bar/Pie chart — %volume >24h per day."""
    db = await get_db()
    try:
        conds = []
        params = []
        if start_date:
            conds.append("time_date >= ?")
            params.append(start_date)
        if end_date:
            conds.append("time_date <= ?")
            params.append(end_date)
        where = ("WHERE " + " AND ".join(conds)) if conds else ""

        cursor = await db.execute(
            f"""SELECT time_date,
                SUM(volume) as total_volume,
                SUM(CASE WHEN is_backlog = 1 THEN volume ELSE 0 END) as backlog_volume,
                SUM(lead_time) as total_lt
               FROM backlog_snapshots
               {where}
               GROUP BY time_date
               ORDER BY time_date ASC""",
            params,
        )
        rows = await cursor.fetchall()
        result = []
        for r in rows:
            d = dict(r)
            tv = d.get("total_volume") or 0
            bv = d.get("backlog_volume") or 0
            bp = (bv / tv * 100) if tv > 0 else 0
            result.append({
                "time_date": d["time_date"],
                "total_volume": tv,
                "backlog_volume": bv,
                "backlog_percent": round(bp, 2),
                "total_lead_time": round(d.get("total_lt") or 0, 2),
            })
        return result
    finally:
        await db.close()


async def get_kpi_history(hours: int = 24) -> List[Dict]:
    """Get KPI history over time."""
    db = await get_db()
    try:
        cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
        cursor = await db.execute(
            """SELECT crawled_at, total_volume, backlog_gt24h_volume,
                      backlog_gt24h_percent, avg_lead_time
               FROM kpi_history
               WHERE crawled_at >= ?
               ORDER BY crawled_at ASC""",
            (cutoff,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def get_snapshot_count() -> int:
    """Get total snapshot rows."""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT COUNT(*) FROM backlog_snapshots")
        row = await cursor.fetchone()
        return row[0] if row else 0
    finally:
        await db.close()


async def get_sla_summary(threshold_pct: float = 0.5) -> Dict:
    """Compute SLA summary across all daily data.
    
    SLA thresholds:
    - Safe: backlog% < threshold * 0.5
    - Warning: threshold * 0.5 <= backlog% < threshold
    - Danger: backlog% >= threshold
    """
    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT time_date,
                SUM(volume) as total_volume,
                SUM(CASE WHEN is_backlog = 1 THEN volume ELSE 0 END) as backlog_volume,
                SUM(lead_time * volume) as weighted_lt
               FROM backlog_snapshots
               GROUP BY time_date
               ORDER BY time_date ASC"""
        )
        rows = await cursor.fetchall()
        if not rows:
            return {"overall_status": "safe", "daily_details": [], "total_days": 0}

        daily = []
        days_above = 0
        warn_threshold = threshold_pct * 0.5

        for r in rows:
            d = dict(r)
            tv = d.get("total_volume") or 0
            bv = d.get("backlog_volume") or 0
            bp = (bv / tv * 100) if tv > 0 else 0
            al = (d.get("weighted_lt") or 0) / tv if tv > 0 else 0

            if bp >= threshold_pct:
                status = "danger"
                days_above += 1
            elif bp >= warn_threshold:
                status = "warning"
            else:
                status = "safe"

            daily.append({
                "time_date": d["time_date"],
                "total_volume": tv,
                "backlog_volume": bv,
                "backlog_percent": round(bp, 4),
                "avg_lead_time": round(al, 2),
                "sla_status": status,
            })

        # Latest day
        latest = daily[-1] if daily else {}

        # Last 7 days averages
        last_7 = daily[-7:] if len(daily) >= 7 else daily
        avg_bp_7d = sum(d["backlog_percent"] for d in last_7) / len(last_7) if last_7 else 0
        avg_lt_7d = sum(d["avg_lead_time"] for d in last_7) / len(last_7) if last_7 else 0

        # Trend direction (last 3 days)
        trend = "flat"
        if len(daily) >= 3:
            recent = [d["backlog_percent"] for d in daily[-3:]]
            if recent[-1] > recent[0] * 1.1:
                trend = "up"
            elif recent[-1] < recent[0] * 0.9:
                trend = "down"

        # Overall status based on latest day
        overall = latest.get("sla_status", "safe")

        return {
            "overall_status": overall,
            "latest_date": latest.get("time_date"),
            "latest_backlog_percent": latest.get("backlog_percent", 0),
            "latest_lead_time": latest.get("avg_lead_time", 0),
            "latest_total_volume": latest.get("total_volume", 0),
            "latest_backlog_volume": latest.get("backlog_volume", 0),
            "avg_backlog_percent_7d": round(avg_bp_7d, 4),
            "avg_lead_time_7d": round(avg_lt_7d, 2),
            "trend_direction": trend,
            "days_above_threshold": days_above,
            "total_days": len(daily),
            "daily_details": daily,
        }
    finally:
        await db.close()


async def get_distribution_latest() -> List[Dict]:
    """Get aging bucket distribution for the latest date (for doughnut chart)."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT MAX(time_date) FROM backlog_snapshots"
        )
        row = await cursor.fetchone()
        latest_date = row[0] if row else None
        if not latest_date:
            return []

        cursor = await db.execute(
            """SELECT aging_bucket, volume, percent_volume, lead_time
               FROM backlog_snapshots
               WHERE time_date = ?
               ORDER BY aging_bucket ASC""",
            (latest_date,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


# ── Fill Rate Functions ────────────────────────────────────

async def insert_fill_rate_batch(rows: List[Dict]):
    """Insert or update a batch of fill rate rows."""
    if not rows:
        return 0

    db = await get_db()
    try:
        now = datetime.utcnow().isoformat()
        count = 0

        for row in rows:
            # Clamp integers to SQLite max to prevent overflow
            _max_int = 2**63 - 1
            _clamp = lambda v: min(int(v or 0), _max_int)

            await db.execute(
                """INSERT OR REPLACE INTO fill_rate
                   (trip_date, trip_code, route_code, route_name,
                    vehicle_type, route_detail, license_plate,
                    capacity, std_orders, std_weight,
                    actual_orders, fill_rate_weight, fill_rate_order, crawled_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row.get("trip_date", ""),
                    row.get("trip_code", ""),
                    row.get("route_code", ""),
                    row.get("route_name", ""),
                    row.get("vehicle_type", ""),
                    row.get("route_detail", ""),
                    row.get("license_plate", ""),
                    _clamp(row.get("capacity", 0)),
                    _clamp(row.get("std_orders", 0)),
                    _clamp(row.get("std_weight", 0)),
                    _clamp(row.get("actual_orders", 0)),
                    row.get("fill_rate_weight", 0.0),
                    row.get("fill_rate_order", 0.0),
                    now,
                ),
            )
            count += 1

        await db.commit()
        logger.info(f"[DB] Fill rate: upserted {count} records")
        return count
    finally:
        await db.close()


async def get_fill_rate_overview(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Dict:
    """Get fill rate KPI overview — averages and overweight count."""
    db = await get_db()
    try:
        conds = []
        params = []
        if start_date:
            conds.append("trip_date >= ?")
            params.append(start_date)
        if end_date:
            conds.append("trip_date <= ?")
            params.append(end_date)
        where = ("WHERE " + " AND ".join(conds)) if conds else ""

        cursor = await db.execute(
            f"""SELECT
                COUNT(*) as total_trips,
                AVG(fill_rate_weight) as avg_fill_weight,
                AVG(fill_rate_order) as avg_fill_order,
                SUM(CASE WHEN fill_rate_weight > 100 THEN 1 ELSE 0 END) as overweight_count,
                MAX(trip_date) as latest_date,
                MIN(trip_date) as earliest_date
               FROM fill_rate {where}""",
            params,
        )
        data = dict(await cursor.fetchone())
        total = data.get("total_trips") or 0
        if total == 0:
            return {}

        return {
            "total_trips": total,
            "avg_fill_rate_weight": round(data.get("avg_fill_weight") or 0, 2),
            "avg_fill_rate_order": round(data.get("avg_fill_order") or 0, 2),
            "overweight_count": data.get("overweight_count") or 0,
            "latest_date": data.get("latest_date"),
            "earliest_date": data.get("earliest_date"),
        }
    finally:
        await db.close()


async def get_fill_rate_list(
    page: int = 1,
    limit: int = 50,
    search: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> tuple:
    """Get fill rate rows (paginated, searchable, date-filtered)."""
    db = await get_db()
    try:
        conditions = []
        params = []

        if search:
            conditions.append(
                "(trip_code LIKE ? OR route_name LIKE ? OR license_plate LIKE ? OR route_detail LIKE ?)"
            )
            params.extend([f"%{search}%"] * 4)
        if start_date:
            conditions.append("trip_date >= ?")
            params.append(start_date)
        if end_date:
            conditions.append("trip_date <= ?")
            params.append(end_date)

        where = " AND ".join(conditions) if conditions else "1=1"

        cursor = await db.execute(
            f"SELECT COUNT(*) FROM fill_rate WHERE {where}",
            params,
        )
        total = (await cursor.fetchone())[0]

        offset = (page - 1) * limit
        cursor = await db.execute(
            f"""SELECT id, trip_date, trip_code, route_code, route_name,
                       vehicle_type, route_detail, license_plate,
                       capacity, std_orders, std_weight,
                       actual_orders, fill_rate_weight, fill_rate_order
                FROM fill_rate
                WHERE {where}
                ORDER BY trip_date DESC, fill_rate_weight DESC
                LIMIT ? OFFSET ?""",
            params + [limit, offset],
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows], total
    finally:
        await db.close()


async def get_fill_rate_top_overweight(
    limit: int = 10,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> List[Dict]:
    """Get top N trips with fill_rate_weight > 100%, sorted by descending rate."""
    db = await get_db()
    try:
        conds = ["fill_rate_weight > 100"]
        params = []
        if start_date:
            conds.append("trip_date >= ?")
            params.append(start_date)
        if end_date:
            conds.append("trip_date <= ?")
            params.append(end_date)
        where = " AND ".join(conds)

        cursor = await db.execute(
            f"""SELECT id, trip_date, trip_code, route_code, route_name,
                       vehicle_type, license_plate, capacity,
                       fill_rate_weight, fill_rate_order
                FROM fill_rate
                WHERE {where}
                ORDER BY fill_rate_weight DESC
                LIMIT ?""",
            params + [limit],
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()
