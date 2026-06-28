"""لایه‌ی داده‌ی SQLite: مخاطبین، قالب‌ها، لاگ ارسال، وضعیت و آمار."""
from __future__ import annotations

import datetime
import os
import sqlite3
import threading

import config

_HERE = os.path.dirname(os.path.abspath(__file__))
_LOCK = threading.Lock()
_CONN = None

_TEHRAN = datetime.timezone(datetime.timedelta(hours=3, minutes=30))

# قالب‌های پیش‌فرض (متنوع، برای کاهش الگوی اسپم). با {name} شخصی‌سازی می‌شوند.
_DEFAULT_TEMPLATES = [
    "سلام {name} عزیز 🌟 از فروشگاهِ نمونه هستم. سری جدید ساعت و زیورمون رسیده؛ دوست داری چند مدل خوش‌قیمت برات بفرستم؟",
    "{name} جان سلام 😊 وقتت بخیر. این روزها چند مدل خاص و محدود داریم. علاقه داری ببینی‌شون؟",
    "سلام {name} 🙏 امیدوارم حالت خوب باشه. اگه دنبال هدیه یا ساعت و زیورِ خاصی هستی، خوشحال می‌شم راهنماییت کنم.",
    "{name} عزیز سلام ✨ از فروشگاهِ نمونه مزاحمت شدم. اگه بخوای، جدیدترین‌ها و پیشنهادهای ویژه‌مون رو برات می‌فرستم.",
    "سلام {name} 👋 یه سری مدل قشنگ تازه اومده که فکر کردم شاید پسندت بشه. بگو تا برات بفرستم 🌹",
]

_NAME_FALLBACK = "دوست عزیز"


def _now_tehran():
    return datetime.datetime.now(_TEHRAN)


def today_str():
    return _now_tehran().strftime("%Y-%m-%d")


def now_str():
    return _now_tehran().strftime("%Y-%m-%d %H:%M:%S")


def conn():
    global _CONN
    if _CONN is None:
        path = config.DB_PATH
        if not os.path.isabs(path):
            path = os.path.join(_HERE, path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _CONN = sqlite3.connect(path, check_same_thread=False)
        _CONN.row_factory = sqlite3.Row
    return _CONN


def init():
    c = conn()
    with _LOCK:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS contacts (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              phone TEXT UNIQUE,
              name TEXT,
              status TEXT DEFAULT 'pending',
              attempts INTEGER DEFAULT 0,
              last_error TEXT,
              sent_at TEXT,
              created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS templates (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              body TEXT,
              enabled INTEGER DEFAULT 1,
              sent_count INTEGER DEFAULT 0,
              created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS sends (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              contact_id INTEGER,
              template_id INTEGER,
              phone TEXT,
              name TEXT,
              status TEXT,
              error TEXT,
              created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS meta (
              key TEXT PRIMARY KEY,
              value TEXT
            );
            CREATE TABLE IF NOT EXISTS seen (
              chat_id INTEGER PRIMARY KEY
            );
            CREATE INDEX IF NOT EXISTS idx_contacts_status ON contacts(status);
            """
        )
        c.commit()
        # بذرِ قالب‌های پیش‌فرض اگر خالی بود
        n = c.execute("SELECT COUNT(*) AS n FROM templates").fetchone()["n"]
        if not n:
            for body in _DEFAULT_TEMPLATES:
                c.execute(
                    "INSERT INTO templates(body, enabled, created_at) VALUES (?,1,?)",
                    (body, now_str()),
                )
            c.commit()
    # مقادیر اولیه‌ی وضعیت
    if get_meta("sender_state") is None:
        set_meta("sender_state", "paused" if config.START_PAUSED else "running")
    if get_meta("warming_day") is None:
        set_meta("warming_day", "1")
    if get_meta("today") is None:
        set_meta("today", today_str())
    if get_meta("sent_today") is None:
        set_meta("sent_today", "0")


# ---------- meta ----------
def get_meta(key, default=None):
    row = conn().execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(key, value):
    with _LOCK:
        conn().execute(
            "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )
        conn().commit()


def _int_meta(key, default=0):
    try:
        return int(get_meta(key, default))
    except (TypeError, ValueError):
        return default


# ---------- روز و سقف (گرم‌کردن) ----------
def ensure_today():
    """اگر روز عوض شده باشد: شمارنده‌ی امروز صفر و روزِ گرم‌کردن یک واحد جلو می‌رود."""
    cur = today_str()
    if get_meta("today") != cur:
        set_meta("today", cur)
        set_meta("sent_today", "0")
        set_meta("warming_day", str(_int_meta("warming_day", 1) + 1))


def today_cap():
    day = max(1, _int_meta("warming_day", 1))
    cap = config.WARMUP_START + config.WARMUP_STEP * (day - 1)
    return min(setting_int("daily_cap", config.DAILY_CAP), cap)


def sent_today():
    return _int_meta("sent_today", 0)


# ---------- تنظیماتِ زنده‌ی سرعت (قابل ویرایش از داشبورد؛ بازنویسیِ مقادیرِ .env) ----------
def setting_int(key, default):
    v = get_meta("cfg_" + key)
    try:
        return int(v) if v not in (None, "") else int(default)
    except (TypeError, ValueError):
        return int(default)


def current_settings():
    return {
        "burst_size": setting_int("burst_size", config.BURST_SIZE),
        "delay_min": setting_int("delay_min", config.DELAY_MIN_SEC),
        "delay_max": setting_int("delay_max", config.DELAY_MAX_SEC),
        "burst_pause_min": setting_int("burst_pause_min", config.BURST_PAUSE_MIN),
        "daily_cap": setting_int("daily_cap", config.DAILY_CAP),
    }


def save_settings(d):
    bounds = {
        "burst_size": (1, 1000), "delay_min": (5, 3600), "delay_max": (5, 7200),
        "burst_pause_min": (0, 1440), "daily_cap": (1, 100000),
    }
    for k, (lo, hi) in bounds.items():
        if k in d and d[k] not in (None, ""):
            try:
                v = int(d[k])
            except (TypeError, ValueError):
                continue
            set_meta("cfg_" + k, max(lo, min(hi, v)))
    s = current_settings()  # تضمین: حداکثرِ فاصله کمتر از حداقل نشود
    if s["delay_max"] < s["delay_min"]:
        set_meta("cfg_delay_max", s["delay_min"])
    return current_settings()


# ---------- قالب‌ها ----------
def list_templates():
    return [dict(r) for r in conn().execute("SELECT * FROM templates ORDER BY id").fetchall()]


def add_template(body):
    body = (body or "").strip()
    if not body:
        return
    with _LOCK:
        conn().execute(
            "INSERT INTO templates(body, enabled, created_at) VALUES (?,1,?)",
            (body, now_str()),
        )
        conn().commit()


def toggle_template(tid, enabled):
    with _LOCK:
        conn().execute("UPDATE templates SET enabled=? WHERE id=?", (1 if enabled else 0, tid))
        conn().commit()


def delete_template(tid):
    with _LOCK:
        conn().execute("DELETE FROM templates WHERE id=?", (tid,))
        conn().commit()


def pick_template():
    """یک قالبِ فعال را به‌صورت چرخشی (کم‌مصرف‌ترین) برمی‌گرداند تا توزیع یکنواخت شود."""
    row = conn().execute(
        "SELECT * FROM templates WHERE enabled=1 ORDER BY sent_count ASC, RANDOM() LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def render(body, name):
    nm = (name or "").strip() or _NAME_FALLBACK
    return body.replace("{name}", nm)


# ---------- مخاطبین ----------
def import_contacts(rows):
    """rows: لیست دیکشنری {phone, name, optout?}. درج/به‌روزرسانی بر اساس شماره.

    خروجی: (added, updated, optout, skipped)
    """
    added = updated = optout = skipped = 0
    c = conn()
    with _LOCK:
        for r in rows:
            phone = (r.get("phone") or "").strip()
            if not phone:
                skipped += 1
                continue
            name = (r.get("name") or "").strip()
            is_optout = bool(r.get("optout"))
            existing = c.execute("SELECT id, status FROM contacts WHERE phone=?", (phone,)).fetchone()
            if existing:
                if name:
                    c.execute("UPDATE contacts SET name=? WHERE id=?", (name, existing["id"]))
                if is_optout and existing["status"] != "optout":
                    c.execute("UPDATE contacts SET status='optout' WHERE id=?", (existing["id"],))
                    optout += 1
                else:
                    updated += 1
            else:
                c.execute(
                    "INSERT INTO contacts(phone,name,status,created_at) VALUES (?,?,?,?)",
                    (phone, name, "optout" if is_optout else "pending", now_str()),
                )
                if is_optout:
                    optout += 1
                else:
                    added += 1
        c.commit()
    return added, updated, optout, skipped


def next_pending():
    row = conn().execute(
        "SELECT * FROM contacts WHERE status='pending' ORDER BY id LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def mark_contact(contact_id, status, error=None, template_id=None, phone="", name=""):
    with _LOCK:
        c = conn()
        c.execute(
            "UPDATE contacts SET status=?, attempts=attempts+1, last_error=?, sent_at=? WHERE id=?",
            (status, error, now_str() if status == "sent" else None, contact_id),
        )
        c.execute(
            "INSERT INTO sends(contact_id,template_id,phone,name,status,error,created_at) VALUES (?,?,?,?,?,?,?)",
            (contact_id, template_id, phone, name, status, error, now_str()),
        )
        if status == "sent" and template_id:
            c.execute("UPDATE templates SET sent_count=sent_count+1 WHERE id=?", (template_id,))
        c.commit()
    if status == "sent":
        set_meta("sent_today", str(sent_today() + 1))
        set_meta("last_send_at", now_str())


def add_optout(phone):
    with _LOCK:
        conn().execute("UPDATE contacts SET status='optout' WHERE phone=?", ((phone or "").strip(),))
        conn().commit()


def mark_seen(chat_id):
    """ثبتِ یکتای مخاطبی که پیام را خوانده (سین کرده)."""
    if not chat_id:
        return
    with _LOCK:
        conn().execute("INSERT OR IGNORE INTO seen(chat_id) VALUES(?)", (int(chat_id),))
        conn().commit()


def seen_count():
    return conn().execute("SELECT COUNT(*) AS n FROM seen").fetchone()["n"]


# ---------- آمار ----------
def stats():
    c = conn()
    counts = {r["status"]: r["n"] for r in c.execute(
        "SELECT status, COUNT(*) AS n FROM contacts GROUP BY status"
    ).fetchall()}
    total = sum(counts.values())
    sent = counts.get("sent", 0)
    pending = counts.get("pending", 0)
    failed = counts.get("failed", 0)
    no_tg = counts.get("no_telegram", 0)
    opt = counts.get("optout", 0)
    processed = sent + failed + no_tg + opt
    pct = round((processed / total) * 100, 1) if total else 0.0
    return {
        "total": total,
        "sent": sent,
        "pending": pending,
        "failed": failed,
        "no_telegram": no_tg,
        "optout": opt,
        "processed": processed,
        "progress_pct": pct,
        "sent_today": sent_today(),
        "today_cap": today_cap(),
        "warming_day": _int_meta("warming_day", 1),
        "state": get_meta("sender_state", "paused"),
        "paused_reason": get_meta("paused_reason", ""),
        "last_send_at": get_meta("last_send_at", ""),
        "seen": seen_count(),
    }


def recent_sends(limit=20):
    rows = conn().execute(
        "SELECT phone, name, status, error, created_at FROM sends ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]
