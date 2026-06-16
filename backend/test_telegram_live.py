# -*- coding: utf-8 -*-
"""
Test Telegram bot: send a LIVE report using real database data.
This tests the full pipeline: database query → message build → Telegram send.
"""
import os
import sys
import asyncio
import logging

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Set env vars from the known config
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "8613582110:AAG5NhCo9KEdoghFkS5_W4KIhyakhvuFfkc")
os.environ.setdefault("TELEGRAM_CHAT_ID", "-5446996688")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)


async def main():
    from database import init_database
    from telegram_bot import build_report_message, send_telegram_message

    # Ensure DB schema exists
    await init_database()

    print("📤 Building report from live database...")
    message = await build_report_message()

    print("\n" + "=" * 50)
    print("📋 PREVIEW (raw HTML):")
    print("=" * 50)
    print(message)
    print("=" * 50)

    print("\n📤 Sending to Telegram...")
    success = await send_telegram_message(message)

    if success:
        print("✅ Báo cáo đã gửi thành công! Kiểm tra Telegram.")
    else:
        print("❌ Gửi thất bại. Kiểm tra token/chat_id.")


if __name__ == "__main__":
    asyncio.run(main())
