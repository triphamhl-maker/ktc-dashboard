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
    """Build the daily report message by querying the database directly."""
    from database import get_overview_kpi, get_fill_rate_overview, get_fill_rate_top_overweight

    try:
        # Get backlog overview (latest day, no date filter)
        overview = await get_overview_kpi()
        fill_rate = await get_fill_rate_overview()
        # Top 10 overweight trips (>100%, excluding fake routes)
        top_overweight = await get_fill_rate_top_overweight(limit=10)
    except Exception as e:
        logger.error(f"[TelegramBot] Failed to query database: {e}")
        return _build_error_message(str(e))

    if not overview or not overview.get("total_volume"):
        return _build_error_message("Không có dữ liệu trong database")

    # Extract data
    total_volume = overview.get("total_volume", 0)
    backlog_pct = overview.get("backlog_gt24h_percent", 0)
    backlog_vol = overview.get("backlog_gt24h_volume", 0)
    avg_leadtime = overview.get("avg_lead_time", 0)
    latest_date = overview.get("latest_date", "N/A")

    # Fill rate data
    fr_weight = fill_rate.get("avg_fill_rate_weight", 0) if fill_rate else 0
    fr_order = fill_rate.get("avg_fill_rate_order", 0) if fill_rate else 0
    overweight_count = fill_rate.get("overweight_count", 0) if fill_rate else 0

    # Current time
    now = datetime.now()
    time_str = now.strftime("%H:%M — %d/%m/%Y")

    # Build top 10 overweight section
    top10_text = _build_top10_section(top_overweight)

    # Decide format based on SLA threshold
    if backlog_pct > SLA_THRESHOLD:
        return _build_urgent_alert(
            time_str, latest_date, total_volume, backlog_pct, backlog_vol,
            avg_leadtime, fr_weight, fr_order, overweight_count, top10_text,
        )
    else:
        return _build_normal_report(
            time_str, latest_date, total_volume, backlog_pct, backlog_vol,
            avg_leadtime, fr_weight, fr_order, overweight_count, top10_text,
        )


def _build_normal_report(
    time_str, latest_date, total_volume, backlog_pct, backlog_vol,
    avg_leadtime, fr_weight, fr_order, overweight_count, top10_text,
) -> str:
    """Build normal daily report (backlog <= SLA threshold)."""
    report = (
        f"📊 <b>BÁO CÁO VẬN HÀNH KTC</b>\n"
        f"🕐 {time_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"\n"
        f"📦 <b>Tổng sản lượng:</b> {_format_number(total_volume)}\n"
        f"⏱ <b>LeadTime TB:</b> {avg_leadtime:.1f}h\n"
        f"📅 <b>Ngày dữ liệu:</b> {latest_date}\n"
        f"\n"
        f"━━ 📋 BACKLOG ━━━━━━━━━\n"
        f"✅ <b>Backlog &gt;24h:</b> {backlog_pct:.2f}% ({_format_number(backlog_vol)} đơn)\n"
        f"🎯 Ngưỡng SLA: {SLA_THRESHOLD}% → <b>AN TOÀN</b>\n"
        f"\n"
        f"━━ 🚛 LẤP ĐẦY TẢI ━━━━━\n"
        f"⚖️ TB Lấp đầy (KL): <b>{fr_weight:.1f}%</b>\n"
        f"📦 TB Lấp đầy (Đơn): <b>{fr_order:.1f}%</b>\n"
        f"{'⚠️ Vượt tải: <b>' + str(overweight_count) + ' chuyến</b>' if overweight_count > 0 else '✅ Không có chuyến vượt tải'}\n"
    )
    if top10_text:
        report += f"\n{top10_text}\n"
    report += (
        f"\n━━━━━━━━━━━━━━━━━━━━\n"
        f"🔗 <a href='https://ktc-dashboard.onrender.com'>Xem Dashboard</a>"
    )
    return report


def _build_urgent_alert(
    time_str, latest_date, total_volume, backlog_pct, backlog_vol,
    avg_leadtime, fr_weight, fr_order, overweight_count, top10_text,
) -> str:
    """Build urgent alert message (backlog > SLA threshold)."""
    report = (
        f"🚨🚨🚨 <b>CẢNH BÁO KHẨN CẤP</b> 🚨🚨🚨\n"
        f"⚠️ <b>BACKLOG VƯỢT NGƯỠNG SLA!</b>\n"
        f"🕐 {time_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"\n"
        f"🔴 <b>% Backlog &gt;24h: {backlog_pct:.2f}%</b>\n"
        f"🔴 <b>Số đơn tồn &gt;24h: {_format_number(backlog_vol)}</b>\n"
        f"🎯 Ngưỡng SLA: {SLA_THRESHOLD}% → <b>⛔ VƯỢT NGƯỠNG</b>\n"
        f"\n"
        f"━━ 📊 CHI TIẾT ━━━━━━━━━\n"
        f"📦 Tổng sản lượng: {_format_number(total_volume)}\n"
        f"⏱ LeadTime TB: <b>{avg_leadtime:.1f}h</b>\n"
        f"📅 Ngày dữ liệu: {latest_date}\n"
        f"\n"
        f"━━ 🚛 LẤP ĐẦY TẢI ━━━━━\n"
        f"⚖️ TB Lấp đầy (KL): <b>{fr_weight:.1f}%</b>\n"
        f"📦 TB Lấp đầy (Đơn): <b>{fr_order:.1f}%</b>\n"
        f"{'🔴 Vượt tải: <b>' + str(overweight_count) + ' chuyến</b>' if overweight_count > 0 else '✅ Không có chuyến vượt tải'}\n"
    )
    if top10_text:
        report += f"\n{top10_text}\n"
    report += (
        f"\n━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ <b>Cần xử lý ngay!</b>\n"
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

        lines.append(f"{rank} <b>{fr:.1f}%</b> | {plate} | {route} | 📅{trip_date}")

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
