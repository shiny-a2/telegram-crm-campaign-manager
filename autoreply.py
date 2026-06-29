"""پاسخِ خودکارِ دایرکتِ تلگرام با مغزِ فروش (روی همان یوزرباتِ سندر).

- تاریخچه‌آگاه: چند پیامِ آخرِ گفتگو را به مغز (/api/chat) می‌دهد تا آگاهانه جواب دهد.
- گاردِ اپراتور: بعد از پیامِ مشتری، REPLY_GRACE_SEC ثانیه صبر می‌کند؛ اگر «آدم» (اپراتور) جواب داد،
  ربات عقب می‌کشد؛ اگر کسی جواب نداد، ربات وارد می‌شود و گفتگو را ادامه می‌دهد.
- فالوآپ: مشتریانِ بی‌پیگیری (گفتگوی ساکت) را یک‌بار و محترمانه پیگیری می‌کند.
"""
from __future__ import annotations

import asyncio
import datetime
import json
import random
import time
import urllib.request

from telethon import events

import config
import db

_TEHRAN = datetime.timezone(datetime.timedelta(hours=3, minutes=30))

_pending = {}          # chat_id → asyncio.Task (پاسخِ زمان‌بندی‌شده)
_bot_recent_send = {}  # chat_id → monotonic زمانِ آخرین ارسالِ خودِ ربات (برای تشخیص از اپراتور)

_FOLLOWUP_TEXT = (
    "سلام مجدد 🌟 از فروشگاهِ نمونه.\n"
    "خواستم پیگیر باشم؛ اگه هنوز دنبالِ ساعتِ مناسب هستید یا سوالی براتون مونده، "
    "با کمالِ میل همین‌جا راهنماییتون می‌کنم 🙏"
)


def _within_hours():
    h = datetime.datetime.now(_TEHRAN).hour
    return config.AUTOREPLY_HOUR_START <= h < config.AUTOREPLY_HOUR_END


def _post_brain_sync(messages):
    body = json.dumps({"messages": messages, "user_prompt": "", "max_tokens": 800}).encode("utf-8")
    req = urllib.request.Request(
        config.BRAIN_CHAT_URL, data=body, method="POST",
        headers={"Content-Type": "application/json", "X-SB-Token": config.SALE_BRAIN_TOKEN},
    )
    with urllib.request.urlopen(req, timeout=90) as r:
        return (json.loads(r.read().decode("utf-8")).get("text") or "").strip()


async def _ask_brain(messages):
    try:
        return await asyncio.to_thread(_post_brain_sync, messages)
    except Exception as e:  # noqa: BLE001
        print(f"[autoreply] خطای مغز: {type(e).__name__}: {e}")
        return ""


async def _history(client, chat_id):
    """چند پیامِ آخرِ گفتگو را به فرمتِ messages مغز می‌سازد (out=assistant، in=user)."""
    msgs = await client.get_messages(chat_id, limit=config.HISTORY_LIMIT)
    out = []
    for m in reversed(list(msgs)):  # قدیمی‌ترین → جدیدترین
        txt = (getattr(m, "message", None) or getattr(m, "text", None) or "").strip()
        if not txt:
            continue
        out.append({"role": "assistant" if getattr(m, "out", False) else "user", "content": txt})
    return out


async def _delayed_reply(client, chat_id, name):
    try:
        await asyncio.sleep(config.REPLY_GRACE_SEC)  # پنجرهٔ اپراتور
    except asyncio.CancelledError:
        return
    _pending.pop(chat_id, None)
    if db.get_meta("autoreply") != "on" or not _within_hours():
        return
    if db.get_meta("account_locked") == "1":
        return
    try:
        history = await _history(client, chat_id)
        if not history or history[-1]["role"] != "user":
            return  # آخرین پیام از مشتری نیست (یعنی یک نفر جواب داده)
        reply = await _ask_brain(history)
        if not reply:
            return
        if len(reply) > config.MAX_AUTOREPLY_CHARS:
            reply = reply[: config.MAX_AUTOREPLY_CHARS].rstrip() + " …"
        _bot_recent_send[chat_id] = time.monotonic()
        await client.send_message(chat_id, reply)
        _bot_recent_send[chat_id] = time.monotonic()
        db.log_autoreply(chat_id, name, history[-1]["content"], reply)
        print(f"[autoreply] پاسخ به {name or chat_id}")
    except Exception as e:  # noqa: BLE001
        print(f"[autoreply] ارسالِ پاسخ ناموفق: {type(e).__name__}: {e}")


def _cancel(chat_id):
    t = _pending.pop(chat_id, None)
    if t and not t.done():
        t.cancel()


def register(client):
    """هندلرهای پاسخِ خودکار را روی کلاینتِ سندر ثبت می‌کند."""

    @client.on(events.NewMessage(incoming=True))
    async def _on_incoming(event):
        try:
            if not event.is_private or db.get_meta("autoreply") != "on" or not _within_hours():
                return
            sender = await event.get_sender()
            if not sender or getattr(sender, "bot", False) or getattr(sender, "is_self", False):
                return
            name = (getattr(sender, "first_name", "") or getattr(sender, "username", "") or "").strip()
            _cancel(event.chat_id)  # پیامِ تازه → زمان‌بندیِ تازه (گریسِ اپراتور)
            _pending[event.chat_id] = asyncio.create_task(_delayed_reply(client, event.chat_id, name))
        except Exception as e:  # noqa: BLE001
            print(f"[autoreply] هندلرِ ورودی: {type(e).__name__}: {e}")

    @client.on(events.NewMessage(outgoing=True))
    async def _on_outgoing(event):
        try:
            if not event.is_private:
                return
            last = _bot_recent_send.get(event.chat_id, 0)
            if time.monotonic() - last < 8:  # ارسالِ خودِ ربات → نادیده
                return
            _cancel(event.chat_id)  # «آدم» (اپراتور) جواب داد → ربات عقب می‌کشد
        except Exception as e:  # noqa: BLE001
            print(f"[autoreply] هندلرِ خروجی: {type(e).__name__}: {e}")

    print("[autoreply] هندلرهای پاسخِ خودکار ثبت شد.")


# ---------- فالوآپِ مشتریانِ بی‌پیگیری ----------
async def followup_scan(client):
    """گفتگوهای خصوصیِ ساکت با سابقهٔ تعامل را یک‌بار محترمانه پیگیری می‌کند."""
    if db.get_meta("followup") != "on" or not _within_hours() or db.get_meta("account_locked") == "1":
        return 0
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=config.FOLLOWUP_AFTER_HOURS)
    done = 0
    try:
        async for d in client.iter_dialogs(limit=config.FOLLOWUP_DIALOGS_SCAN):
            if done >= config.FOLLOWUP_MAX_PER_RUN:
                break
            if not getattr(d, "is_user", False):
                continue
            ent = d.entity
            if getattr(ent, "bot", False) or getattr(ent, "is_self", False):
                continue
            chat_id = d.id
            if db.is_followed_up(chat_id):
                continue
            last_dt = getattr(d.message, "date", None)
            if not last_dt or last_dt > cutoff:  # هنوز ساکت نشده
                continue
            # سابقهٔ تعامل: هم پیامِ مشتری هم پاسخِ ما باشد
            recent = await client.get_messages(chat_id, limit=8)
            has_in = any(not getattr(m, "out", False) and (getattr(m, "message", "") or "") for m in recent)
            has_out = any(getattr(m, "out", False) and (getattr(m, "message", "") or "") for m in recent)
            if not (has_in and has_out):
                continue
            try:
                _bot_recent_send[chat_id] = time.monotonic()
                await client.send_message(chat_id, _FOLLOWUP_TEXT)
                _bot_recent_send[chat_id] = time.monotonic()
                db.mark_followed_up(chat_id)
                db.log_autoreply(chat_id, getattr(ent, "first_name", "") or "", "[فالوآپ]", _FOLLOWUP_TEXT)
                done += 1
                print(f"[followup] پیگیریِ {getattr(ent, 'first_name', '') or chat_id}")
                await asyncio.sleep(random.randint(30, 60))  # فاصلهٔ انسانی
            except Exception as e:  # noqa: BLE001
                print(f"[followup] ارسال ناموفق: {type(e).__name__}: {e}")
    except Exception as e:  # noqa: BLE001
        print(f"[followup] اسکن ناموفق: {type(e).__name__}: {e}")
    if done:
        print(f"[followup] {done} پیگیری انجام شد")
    return done


async def followup_loop(client):
    """هر FOLLOWUP_SCAN_HOURS ساعت یک‌بار اسکن می‌کند."""
    await asyncio.sleep(120)  # کمی بعد از بالا آمدن
    while True:
        try:
            await followup_scan(client)
        except Exception as e:  # noqa: BLE001
            print(f"[followup] حلقه: {type(e).__name__}: {e}")
        await asyncio.sleep(max(1, config.FOLLOWUP_SCAN_HOURS) * 3600)
