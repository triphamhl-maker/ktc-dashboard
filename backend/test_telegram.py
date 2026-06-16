# -*- coding: utf-8 -*-
"""Send a sample report with Top 10 to preview the new format."""
import urllib.request
import json
import ssl
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')

BOT_TOKEN = "8613582110:AAG5NhCo9KEdoghFkS5_W4KIhyakhvuFfkc"
CHAT_ID = "-5446996688"

now = datetime.now().strftime("%H:%M — %d/%m/%Y")

# Sample Top 10 data to preview format
top10_lines = [
    "━━ 🏆 TOP 10 VƯỢT TẢI ━━━",
    "🥇 <b>185.3%</b> | 51C-12345 | HCM_SGN_Hub1",
    "🥈 <b>172.8%</b> | 61A-98765 | BinhDuong_GHN_2",
    "🥉 <b>168.5%</b> | 62B-11111 | LongAn_Hub3",
    "4. <b>155.2%</b> | 59C-22222 | HCM_TanBinh_1",
    "5. <b>148.7%</b> | 50F-33333 | CanTho_GHN_1",
    "6. <b>142.1%</b> | 51D-44444 | DaNang_Hub2",
    "7. <b>138.9%</b> | 72A-55555 | BaRia_VT_1",
    "8. <b>131.4%</b> | 60B-66666 | DongNai_GHN_1",
    "9. <b>125.8%</b> | 51E-77777 | HCM_Q7_Hub",
    "10. <b>118.3%</b> | 92A-88888 | QuangNam_1",
]
top10_text = "\n".join(top10_lines)

message = (
    f"📊 <b>BÁO CÁO VẬN HÀNH KTC</b>\n"
    f"🕐 {now}\n"
    f"━━━━━━━━━━━━━━━━━━━━\n"
    f"\n"
    f"📦 <b>Tổng sản lượng:</b> 1.315.858\n"
    f"⏱ <b>LeadTime TB:</b> 4.8h\n"
    f"📅 <b>Ngày dữ liệu:</b> 2026-06-15\n"
    f"\n"
    f"━━ 📋 BACKLOG ━━━━━━━━━\n"
    f"✅ <b>Backlog &gt;24h:</b> 0.09% (1.145 đơn)\n"
    f"🎯 Ngưỡng SLA: 0.2% → <b>AN TOÀN</b>\n"
    f"\n"
    f"━━ 🚛 LẤP ĐẦY TẢI ━━━━━\n"
    f"⚖️ TB Lấp đầy (KL): <b>86.6%</b>\n"
    f"📦 TB Lấp đầy (Đơn): <b>58.3%</b>\n"
    f"⚠️ Vượt tải: <b>408 chuyến</b>\n"
    f"\n"
    f"{top10_text}\n"
    f"\n"
    f"━━━━━━━━━━━━━━━━━━━━\n"
    f"🔗 <a href='https://ktc-dashboard.onrender.com'>Xem Dashboard</a>"
)

url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
data = json.dumps({
    "chat_id": CHAT_ID,
    "text": message,
    "parse_mode": "HTML",
    "disable_web_page_preview": True,
}).encode("utf-8")

req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
ctx = ssl.create_default_context()

try:
    with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
        result = json.loads(resp.read().decode("utf-8"))
        if result.get("ok"):
            print("✅ Báo cáo mẫu đã gửi thành công! Kiểm tra Telegram.")
        else:
            print(f"❌ Lỗi: {result}")
except urllib.error.HTTPError as e:
    body = e.read().decode("utf-8")
    print(f"HTTP {e.code}: {body}")
except Exception as e:
    print(f"Error: {e}")
