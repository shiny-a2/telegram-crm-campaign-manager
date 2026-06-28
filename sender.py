"""ارسال‌کننده‌ی یوزربات تلگرام (Telethon) — محافظه‌کار و ضدبلاک.

سیاست‌ها: قالب چرخشی، تأخیر تصادفی، سقف روزانه‌ی گرم‌شونده، فقط در ساعت مجاز،
و توقف خودکار هنگام محدودیت اسپم (PeerFlood). موتور فقط وقتی sender_state='running'
باشد ارسال می‌کند؛ کنترلش از داشبورد است.
"""
from __future__ import annotations

import asyncio
import datetime
import random

from telethon import TelegramClient, events
from telethon.errors import (
    FloodWaitError,
    PeerFloodError,
    PhoneNumberBannedError,
    UserPrivacyRestrictedError,
)
from telethon.tl.functions.contacts import (
    DeleteContactsRequest,
    GetContactsRequest,
    ImportContactsRequest,
)
from telethon.tl.types import InputPhoneContact, User

import config
import db


def _disp_name(u):
    nm = (getattr(u, "first_name", "") or "").strip()
    ln = (getattr(u, "last_name", "") or "").strip()
    full = (nm + " " + ln).strip()
    return full or (getattr(u, "username", "") or "")


async def _harvest_account_recipients(client):
    """شماره‌ها/آیدی‌هایی که در کانتکتِ اکانت‌اند یا در چت‌ها هستند (و قبلاً پیام نخورده‌اند)
    را به انتهای صف اضافه می‌کند تا برایشان هم پیام برود. در دیتابیس می‌مانند (سری بعد هم قابل‌استفاده)."""
    added = 0
    # ۱) کانتکت‌های دفترچهٔ اکانت
    try:
        res = await client(GetContactsRequest(hash=0))
        for u in getattr(res, "users", []) or []:
            if not isinstance(u, User) or getattr(u, "bot", False) or getattr(u, "is_self", False):
                continue
            if db.add_account_contact(u.id, getattr(u, "phone", None), _disp_name(u), "contact"):
                added += 1
    except Exception as e:  # noqa: BLE001
        print(f"[harvest] خواندنِ کانتکت‌ها ناموفق: {type(e).__name__}: {e}")
    # ۲) طرف‌های گفتگوهای خصوصی (dialogs)
    try:
        async for d in client.iter_dialogs():
            if not getattr(d, "is_user", False):
                continue
            u = d.entity
            if not isinstance(u, User) or getattr(u, "bot", False) or getattr(u, "is_self", False):
                continue
            if db.add_account_contact(u.id, getattr(u, "phone", None), _disp_name(u), "chat"):
                added += 1
    except Exception as e:  # noqa: BLE001
        print(f"[harvest] خواندنِ چت‌ها ناموفق: {type(e).__name__}: {e}")
    print(f"[harvest] {added} مخاطبِ تازه از کانتکت/چتِ اکانت به انتهای صف اضافه شد")
    return added

_TEHRAN = datetime.timezone(datetime.timedelta(hours=3, minutes=30))


def _within_hours():
    h = datetime.datetime.now(_TEHRAN).hour
    return config.SEND_HOUR_START <= h < config.SEND_HOUR_END


def _pause(reason):
    db.set_meta("sender_state", "paused")
    db.set_meta("paused_reason", reason)
    print(f"[sender] متوقف شد: {reason}")


async def _send_one(client, contact):
    """تلاش برای ارسال به یک مخاطب. خروجی: True اگر ارسال شمرده شد (موفق/قطعی)."""
    phone = contact["phone"]
    crm_name = contact["name"] or ""
    tg_id = contact.get("tg_id")
    label = phone or (f"id:{tg_id}" if tg_id else "?")
    tpl = db.pick_template()
    if not tpl:
        _pause("هیچ قالب فعالی وجود ندارد")
        return False

    # گیرنده را پیدا کن: مخاطبِ کانتکت/چت با tg_id (warm، بدونِ ImportContacts) یا با شماره
    user = None
    imported_now = False
    if tg_id:
        try:
            user = await client.get_entity(int(tg_id))
        except FloodWaitError as e:
            print(f"[sender] FloodWait {e.seconds}s (entity) — صبر می‌کنیم")
            await asyncio.sleep(e.seconds + 5)
            return False
        except Exception:  # noqa: BLE001
            user = None
        if user is None:
            db.mark_contact(contact["id"], "no_telegram", "entity (کانتکت/چت) در دسترس نبود", tpl["id"], label, crm_name)
            return True
    else:
        try:
            res = await client(ImportContactsRequest(
                [InputPhoneContact(client_id=0, phone=phone, first_name=(crm_name or "مشتری")[:60], last_name="")]
            ))
            imported_now = True
        except FloodWaitError as e:
            print(f"[sender] FloodWait {e.seconds}s (import) — صبر می‌کنیم")
            await asyncio.sleep(e.seconds + 5)
            return False
        except PeerFloodError:
            db.set_meta("paused_reason", "PeerFlood — تلگرام ارسال انبوه را محدود کرد")
            _pause("PeerFlood — تلگرام ارسال انبوه را محدود کرد؛ چند روز استراحت لازم است")
            return False
        if not res.users:
            # یعنی با شماره resolve نشد — یا تلگرام ندارد، یا (اغلب) privacyِ «پیدا‌شدن با شماره» محدود است.
            db.mark_contact(contact["id"], "no_telegram", "با شماره پیدا نشد (تلگرام ندارد یا privacy)", tpl["id"], phone, crm_name)
            return True
        user = res.users[0]

    # نامِ پیام = نامِ تلگرامیِ خودِ شخص (اگر نبود، نام CRM)
    tg_name = (getattr(user, "first_name", "") or "").strip()
    name = tg_name or crm_name
    text = db.render(tpl["body"], name)
    try:
        await client.send_message(user, text)
        db.mark_contact(contact["id"], "sent", None, tpl["id"], label, name)
        print(f"[sender] ارسال شد → {label}")
        ok = True
    except FloodWaitError as e:
        print(f"[sender] FloodWait {e.seconds}s (send) — صبر می‌کنیم")
        await asyncio.sleep(e.seconds + 5)
        ok = False
    except PeerFloodError:
        _pause("PeerFlood — تلگرام ارسال انبوه را محدود کرد؛ چند روز استراحت لازم است")
        ok = False
    except UserPrivacyRestrictedError:
        db.mark_contact(contact["id"], "failed", "privacy — کاربر پیام از غریبه نمی‌گیرد", tpl["id"], label, name)
        ok = True
    except PhoneNumberBannedError:
        _pause("شماره‌ی ارسال‌کننده بن شده است")
        ok = False
    except Exception as e:  # noqa: BLE001
        db.mark_contact(contact["id"], "failed", f"{type(e).__name__}: {e}", tpl["id"], label, name)
        ok = True

    # فقط مخاطبی که همین حالا با شماره وارد کردیم را (در صورت خاموش‌بودنِ KEEP) پاک کن؛
    # کانتکت/چتِ خودِ اکانت را هرگز پاک نکن.
    if imported_now and not config.KEEP_CONTACTS:
        try:
            await client(DeleteContactsRequest([user.id]))
        except Exception:
            pass
    return ok


async def run():
    db.init()
    db.set_meta("tg_authorized", "0")

    if not config.telegram_ready():
        print("[sender] تلگرام پیکربندی نشده (.env). فقط داشبورد فعال است؛ ارسال غیرفعال.")
        while True:  # بی‌کار بمان تا پیکربندی شود (با ری‌استارت بالا می‌آید)
            await asyncio.sleep(3600)

    client = TelegramClient(config.TG_SESSION, config.TG_API_ID, config.TG_API_HASH)
    await client.connect()
    if not await client.is_user_authorized():
        print("[sender] هنوز وارد نشده‌ای. یک‌بار `python login.py` را اجرا کن. ارسال غیرفعال است.")
        db.set_meta("tg_authorized", "0")
        while not await client.is_user_authorized():
            await asyncio.sleep(60)
            try:
                await client.connect()
            except Exception:
                pass

    db.set_meta("tg_authorized", "1")
    me = await client.get_me()
    print(f"[sender] وارد شد به‌عنوان: {getattr(me, 'first_name', '')} ({getattr(me, 'phone', '')})")

    # مخاطبینِ کانتکت/چتِ خودِ اکانت را هم به انتهای صف اضافه کن (warm، deduped، ماندگار)
    try:
        await _harvest_account_recipients(client)
    except Exception as e:  # noqa: BLE001
        print(f"[harvest] ناموفق: {type(e).__name__}: {e}")

    @client.on(events.MessageRead(inbox=False))
    async def _on_read(event):  # وقتی گیرنده پیام ما را خواند (سین)
        try:
            db.mark_seen(event.chat_id)
        except Exception:
            pass

    burst = 0
    while True:
        try:
            db.ensure_today()
            if db.get_meta("sender_state") != "running":
                await asyncio.sleep(5)
                continue
            if not _within_hours():
                await asyncio.sleep(60)
                continue
            if db.sent_today() >= db.today_cap():
                await asyncio.sleep(60)
                continue
            # پایان بازه → استراحت (تنظیماتِ زنده از داشبورد)
            burst_size = db.setting_int("burst_size", config.BURST_SIZE)
            if burst >= burst_size:
                pause_min = db.setting_int("burst_pause_min", config.BURST_PAUSE_MIN)
                print(f"[sender] بازه‌ی {burst_size}تایی تمام شد — {pause_min} دقیقه استراحت")
                db.set_meta("paused_reason", f"استراحتِ بین‌بازه‌ای ({pause_min} دقیقه)")
                await asyncio.sleep(pause_min * 60)
                db.set_meta("paused_reason", "")
                burst = 0
                continue
            contact = db.next_pending()
            if not contact:
                await asyncio.sleep(30)
                continue

            counted = await _send_one(client, contact)
            if counted:
                burst += 1
                # تأخیر انسانیِ تصادفی فقط بعد از یک اقدام واقعی (تنظیماتِ زنده)
                dmin = db.setting_int("delay_min", config.DELAY_MIN_SEC)
                dmax = db.setting_int("delay_max", config.DELAY_MAX_SEC)
                delay = random.randint(dmin, max(dmin, dmax))
                await asyncio.sleep(delay)
            else:
                await asyncio.sleep(5)
        except Exception as e:  # noqa: BLE001 — هیچ خطایی لوپ را نکُشد
            print(f"[sender] خطای لوپ: {type(e).__name__}: {e}")
            await asyncio.sleep(15)
