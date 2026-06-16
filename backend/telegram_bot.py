"""
Telegram Bot — Daily Report for KTC Dashboard.
Sends automated reports at 09:00 and 23:00 (Asia/Ho_Chi_Minh timezone).

Environment variables required:
  TELEGRAM_BOT_TOKEN — Bot token from @BotFather
  TELEGRAM_CHAT_ID   — Chat/Group ID to send reports to

Usage:
  Integrated into backend via APScheduler (see scheduler.py).
  Can also run standalone: python telegram_bot.py
"""

import os
import logging
import asyncio
from datetime import datetime

import httpx

logger = logging.getLogger("telegram_bot")

# ── Configuration ──────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
SLA_THRESHOLD = 0.2  # 0.2% — backlog >24h threshold for urgent alerts

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"


# ── Send Message ───────────────────────────────────────────

async def send_telegram_message(text: str, parse_mode: str = "HTML") -> bool:
    """Send a message to the configured Telegram chat."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("[TelegramBot] Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID env vars. Skipping.")
        return False

    url = TELEGRAM_API_URL.format(token=TELEGRAM_BOT_TOKEN)
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                logger.info("[TelegramBot] Message sent successfully")
                return True
            else:
                logger.error(f"[TelegramBot] Failed to send: HTTP {resp.status_code} — {resp.text[:200]}")
                return False
    except Exception as e:
        logger.error(f"[TelegramBot] Send error: {e}")
        return False


# ── Build Report ───────────────────────────────────────────

def _format_number(n, decimals=0):
    """Format number with dot separators (Vietnamese style)."""
    if decimals > 0:
        formatted = f"{n:,.{decimals}f}"
    else:
        formatted = f"{n:,.0f}"
    # Convert to Vietnamese format: 1,234,567 → 1.234.567
    return formatted.replace(",", ".")


async def build_report_message() -> str:
    """Build the daily report message by querying the database directly.
    Reports data for N-1 (yesterday) only.
    """
    from database import (
        get_overview_kpi, get_trend_data,
        get_fill_rate_daily, get_fill_rate_top_overweight,
    )
    from datetime import timedelta

    # N-1 = yesterday
    now = datetime.now()
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M — %d/%m/%Y")

    try:
        # Query all data filtered to yesterday only
        overview = await get_overview_kpi(start_date=yesterday, end_date=yesterday)
        trend_data = await get_trend_data(start_date=yesterday, end_date=yesterday)
        fill_rate_daily = await get_fill_rate_daily(start_date=yesterday, end_date=yesterday)
        top_overweight = await get_fill_rate_top_overweight(limit=10, start_date=yesterday, end_date=yesterday)
    except Exception as e:
        logger.error(f"[TelegramBot] Failed to query database: {e}")
        return _build_error_message(str(e))

    if not overview or not overview.get("total_volume"):
        return _build_error_message(f"Không có dữ liệu ngày {yesterday}")

    # ── Extract N-1 backlog data ──
    total_volume = overview.get("total_volume", 0)
    backlog_pct = overview.get("backlog_gt24h_percent", 0)
    backlog_vol = overview.get("backlog_gt24h_volume", 0)
    avg_leadtime = overview.get("avg_lead_time", 0)
    data_date = overview.get("latest_date", yesterday)

    # SLA status
    if backlog_pct >= SLA_THRESHOLD:
        sla_icon = "🔴"
        sla_status = f"⛔ VƯỢT NGƯỠNG"
    else:
        sla_icon = "✅"
        sla_status = f"✅ AN TOÀN"

    # ── Extract N-1 fill rate data ──
    fr_day = fill_rate_daily[0] if fill_rate_daily else None
    fr_trips = fr_day.get("total_trips", 0) if fr_day else 0
    fr_weight = fr_day.get("avg_fill_weight", 0) if fr_day else 0
    fr_order = fr_day.get("avg_fill_order", 0) if fr_day else 0
    fr_overweight = fr_day.get("overweight_count", 0) if fr_day else 0

    # ── Build report ──
    report = (
        f"📊 <b>BÁO CÁO VẬN HÀNH KTC</b>\n"
        f"🕐 {time_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"\n"
        f"📦 <b>Tổng sản lượng:</b> {_format_number(total_volume)}\n"
        f"⏱ <b>LeadTime TB:</b> {avg_leadtime:.1f}h\n"
        f"📅 <b>Ngày dữ liệu:</b> {data_date}\n"
        f"\n"
        f"━━ 📋 BACKLOG ━━━━━━━━━\n"
        f"{sla_icon} <b>% Backlog &gt;24h:</b> {backlog_pct:.2f}%\n"
        f"{sla_icon} <b>Số đơn tồn &gt;24h:</b> {_format_number(backlog_vol)}\n"
        f"🎯 Ngưỡng SLA: &gt;{SLA_THRESHOLD}% → <b>{sla_status}</b>\n"
    )

    # Fill rate section
    if fr_trips > 0:
        ow_text = f"⚠️ Vượt tải (&gt;100%): <b>{fr_overweight} chuyến</b>" if fr_overweight > 0 else "✅ Vượt tải (&gt;100%): <b>0 chuyến</b>"
        report += (
            f"\n"
            f"━━ 🚛 LẤP ĐẦY TẢI ━━━━━━\n"
            f"📅 Ngày: <b>{_format_trip_date(yesterday)}</b> — {fr_trips} chuyến\n"
            f"⚖️ TB Lấp đầy (KL): <b>{fr_weight:.1f}%</b>\n"
            f"📦 TB Lấp đầy (Đơn): <b>{fr_order:.1f}%</b>\n"
            f"{ow_text}\n"
        )

    # Top 10 overweight
    top10_text = _build_top10_section(top_overweight)
    if top10_text:
        report += f"\n{top10_text}\n"

    report += (
        f"\n━━━━━━━━━━━━━━━━━━━━\n"
        f"🔗 <a href='https://ktc-dashboard.onrender.com'>Xem Dashboard</a>"
    )
    return report


def _format_trip_date(date_str: str) -> str:
    """Convert YYYY-MM-DD to dd/mm format for compact display."""
    if not date_str or date_str in ("", "null", "N/A"):
        return "N/A"
    try:
        parts = date_str.split("-")
        return f"{parts[2]}/{parts[1]}"
    except (IndexError, AttributeError):
        return date_str


def _build_top10_section(top_overweight: list) -> str:
    """Build the Top 10 overweight trips section for the report.
    Trips with DongNai_GHN_3 or license plate 50H77777 are already
    filtered out by the database query."""
    if not top_overweight:
        return ""

    lines = ["━━ 🏆 TOP 10 VƯỢT TẢI ━━━"]
    for i, trip in enumerate(top_overweight, 1):
        route = trip.get("route_name") or ""
        # Handle null/empty route names
        if not route or route.lower() in ("null", "none", ""):
            route = "Chưa xác định"
        # Truncate long route names for Telegram readability
        if len(route) > 25:
            route = route[:22] + "..."
        plate = trip.get("license_plate", "N/A")
        fr = trip.get("fill_rate_weight", 0)
        trip_date = _format_trip_date(trip.get("trip_date", ""))

        # Emoji ranking for top 3
        if i == 1:
            rank = "🥇"
        elif i == 2:
            rank = "🥈"
        elif i == 3:
            rank = "🥉"
        else:
            rank = f"{i}."

        lines.append(f"{rank} 📅{trip_date} | {plate} | {route} | <b>{fr:.1f}%</b>")

    return "\n".join(lines)


def _build_error_message(error: str) -> str:
    """Build error notification message."""
    now = datetime.now()
    return (
        f"⚠️ <b>LỖI BÁO CÁO KTC</b>\n"
        f"🕐 {now.strftime('%H:%M — %d/%m/%Y')}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Không thể tạo báo cáo: {error[:100]}\n"
        f"🔗 <a href='https://ktc-dashboard.onrender.com'>Kiểm tra Dashboard</a>"
    )


# ── Main Send Function (called by scheduler) ──────────────

async def send_daily_report():
    """Build and send the daily report. Called by APScheduler."""
    logger.info("[TelegramBot] Building daily report...")
    message = await build_report_message()
    success = await send_telegram_message(message)
    if success:
        logger.info("[TelegramBot] Daily report sent successfully")
    else:
        logger.warning("[TelegramBot] Daily report failed to send")


def trigger_send_report():
    """Synchronous wrapper for APScheduler (which doesn't natively support async jobs).
    Creates an event loop if needed to run the async send function."""
    try:
        loop = asyncio.get_running_loop()
        # If there's already a running loop (inside uvicorn), schedule as task
        loop.create_task(send_daily_report())
    except RuntimeError:
        # No running loop — create one (standalone mode)
        asyncio.run(send_daily_report())


# ── Standalone Mode ────────────────────────────────────────

if __name__ == "__main__":
    """Run standalone to test sending a report immediately."""
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    # Add parent dir to path so imports work
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("❌ Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID environment variables first!")
        print("   Example:")
        print("   set TELEGRAM_BOT_TOKEN=123456:ABC-DEF...")
        print("   set TELEGRAM_CHAT_ID=-1001234567890")
        sys.exit(1)

    print("📤 Sending test report...")
    asyncio.run(send_daily_report())
    print("✅ Done!")
