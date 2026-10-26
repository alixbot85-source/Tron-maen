"""تنظیمات اصلی ربات.

مقادیر حساس را بهتر است به‌صورت متغیر محیطی تنظیم کنید تا داخل کد ذخیره نشوند.
نمونه: BOT_TOKEN=... ADMIN_IDS=123456789,987654321 python bot.py
"""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path


# توکن رباتی که از BotFather دریافت شده است. اجرای ربات بدون آن متوقف می‌شود.
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# شناسه‌های عددی ادمین‌ها، با کاما جدا شوند؛ مانند: 123456789,987654321
# فقط این شناسه‌ها به دستور و پنل /admin دسترسی دارند.
ADMIN_IDS = frozenset(
    int(value.strip())
    for value in os.getenv("ADMIN_IDS", "").split(",")
    if value.strip().isdigit()
)

# نام کاربری ربات بدون @. اگر خالی باشد، ربات هنگام اجرا آن را از تلگرام می‌خواند.
BOT_USERNAME = os.getenv("BOT_USERNAME", "").strip().lstrip("@")

# مسیر فایل SQLite محلی. به هیچ سرور دیتابیسی نیاز نیست.
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", "tron_mining_bot.sqlite3"))

# نرخ پایه ماین به واحد TRON در هر دقیقه.
BASE_MINING_RATE = Decimal(os.getenv("BASE_MINING_RATE", "0.01"))

# حداقل فاصله میان دو برداشت ماین، بر حسب ثانیه.
MINING_COOLDOWN_SECONDS = 60

# گزینه‌های سقف ماین که کاربر می‌تواند انتخاب کند، بر حسب TRON.
MINING_CAP_OPTIONS = (2000, 5000, 10000, 20000)

# سقف پیش‌فرض برای کاربر جدید، بر حسب TRON.
DEFAULT_MINING_CAP = Decimal(str(MINING_CAP_OPTIONS[0]))

# پکیج‌های سرعت: کلید ضریب سرعت و مقدار قیمت آن به واحد TRON است.
SPEED_PACKAGES = {
    2: Decimal("60"),
    3: Decimal("100"),
    4: Decimal("150"),
    5: Decimal("200"),
}

# آدرس ثابت دریافت TRON. ادمین می‌تواند از داخل ربات آن را تغییر دهد؛
# مقدار ذخیره‌شده در دیتابیس بر این مقدار اولویت دارد.
TRON_WALLET_ADDRESS = os.getenv("TRON_WALLET_ADDRESS", "").strip()

# پاداش ثابت دعوت‌کننده برای هر ثبت‌نام موفق از لینک دعوت.
REFERRAL_FIXED_REWARD = Decimal("0.5")

# سهم دعوت‌کننده از هر برداشت ماین دعوت‌شده؛ 0.10 یعنی 10 درصد.
REFERRAL_MINING_PERCENTAGE = Decimal("0.10")

# آدرس عمومی HTTPS وب‌اپ تلگرام در ریشهٔ دامنه. ادمین می‌تواند آن را از پنل تغییر دهد.
WEB_APP_URL = os.getenv("WEB_APP_URL", "").strip().rstrip("/")

# میزبان و پورت سرویس وب‌اپ محلی؛ معمولاً پشت reverse proxy با HTTPS قرار می‌گیرد.
WEB_APP_HOST = os.getenv("WEB_APP_HOST", "0.0.0.0").strip() or "0.0.0.0"
WEB_APP_PORT = int(os.getenv("WEB_APP_PORT", "8080"))
