"""پیکربندی متمرکز که از فایل .env خوانده می‌شود."""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def _get(name, default=None):
    return os.getenv(name, default)


def _int(name, default):
    raw = os.getenv(name)
    try:
        return int(raw) if raw not in (None, "") else default
    except ValueError:
        return default


def _bool(name, default=False):
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on", "بله")


# ---------- تلگرام ----------
TG_API_ID = _int("TG_API_ID", 0)
TG_API_HASH = _get("TG_API_HASH", "")
TG_PHONE = _get("TG_PHONE", "")
TG_SESSION = _get("TG_SESSION", "data/outreach.session")

# ---------- ضدبلاک ----------
DAILY_CAP = _int("DAILY_CAP", 30)
WARMUP_START = _int("WARMUP_START", 15)
WARMUP_STEP = _int("WARMUP_STEP", 5)
DELAY_MIN_SEC = _int("DELAY_MIN_SEC", 90)
DELAY_MAX_SEC = _int("DELAY_MAX_SEC", 210)
SEND_HOUR_START = _int("SEND_HOUR_START", 10)
SEND_HOUR_END = _int("SEND_HOUR_END", 21)
START_PAUSED = _bool("START_PAUSED", True)
# بازه‌ای: BURST_SIZE پیام، بعد BURST_PAUSE_MIN دقیقه استراحت، دوباره
BURST_SIZE = _int("BURST_SIZE", 50)
BURST_PAUSE_MIN = _int("BURST_PAUSE_MIN", 60)
# مخاطبین بعد از ارسال در دفترچه‌ی اکانت بمانند (سیو شوند) نه پاک
KEEP_CONTACTS = _bool("KEEP_CONTACTS", True)

# ---------- داشبورد ----------
DASH_HOST = _get("DASH_HOST", "0.0.0.0")
DASH_PORT = _int("DASH_PORT", 8091)
DASH_TOKEN = _get("DASH_TOKEN", "")

DB_PATH = _get("DB_PATH", "data/outreach.db")

# ---------- ووکامرس (برای کشیدن زنده‌ی مخاطبین از سفارش‌ها) ----------
WOO_URL = (_get("WOO_URL", "") or "").rstrip("/")
WOO_CK = _get("WOO_CK", "")
WOO_CS = _get("WOO_CS", "")

# ---------- خروجی مخاطبین CRM (endpoint افزونه‌ی a2-crm) ----------
CRM_EXPORT_URL = (_get("CRM_EXPORT_URL", "") or "").rstrip("/")
CRM_EXPORT_TOKEN = _get("CRM_EXPORT_TOKEN", "")
# هر چند ساعت یک‌بار مخاطبین جدیدِ CRM خودکار سینک شوند (۰ = خاموش)
SYNC_INTERVAL_HOURS = _int("SYNC_INTERVAL_HOURS", 6)


def telegram_ready():
    return bool(TG_API_ID and TG_API_HASH and TG_PHONE)


def woo_ready():
    return bool(WOO_URL and WOO_CK and WOO_CS)
