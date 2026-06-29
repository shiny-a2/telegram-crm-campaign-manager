"""پاسخِ خودکارِ دایرکتِ تلگرام با مغزِ فروش (روی همان یوزرباتِ سندر).

- تاریخچه‌آگاه: چند پیامِ آخرِ گفتگو را به مغز (/api/chat) می‌دهد تا آگاهانه جواب دهد.
- درجا جواب می‌دهد؛ مگر اپراتور اخیراً واردِ چت شده باشد — آن‌وقت REPLY_GRACE_SEC ثانیه صبر می‌کند
  تا اگر اپراتور جواب نداد، ربات وارد شود (اولویت با اپراتور تا OPERATOR_ACTIVE_WINDOW).
- فالوآپ: مشتریانِ بی‌پیگیری (گفتگوی ساکت) را یک‌بار و محترمانه پیگیری می‌کند.
"""
from __future__ import annotations

import asyncio
import base64
import datetime
import json
import random
import time
import urllib.request

from telethon import events

import clock
import config
import db

_TEHRAN = datetime.timezone(datetime.timedelta(hours=3, minutes=30))

_pending = {}            # chat_id → asyncio.Task (پاسخِ زمان‌بندی‌شده)
_bot_recent_send = {}    # chat_id → monotonic زمانِ آخرین ارسالِ خودِ ربات (برای تشخیص از اپراتور)
_operator_active = {}    # chat_id → monotonic زمانِ آخرین پیامِ اپراتورِ انسانی (→ حالتِ تأخیری)
_voice_cache = {}        # message_id → متنِ ترنسکرایب‌شدهٔ وویس (تا دوباره ترنسکرایب نشود)

_FOLLOWUP_TEXT = (
    "سلام مجدد 🌟 از فروشگاهِ نمونه.\n"
    "خواستم پیگیر باشم؛ اگه هنوز دنبالِ ساعتِ مناسب هستید یا سوالی براتون مونده، "
    "با کمالِ میل همین‌جا راهنماییتون می‌کنم 🙏"
)


def _within_hours():
    h = clock.tehran_now().hour  # ساعتِ تصحیح‌شده (ساعتِ خودِ سرور ممکن است کج باشد)
    return config.AUTOREPLY_HOUR_START <= h < config.AUTOREPLY_HOUR_END


def _within_followup_hours():
    h = clock.tehran_now().hour  # ساعتِ تصحیح‌شده
    return config.FOLLOWUP_HOUR_START <= h < config.FOLLOWUP_HOUR_END


_TRANSCRIBE_URL = config.BRAIN_CHAT_URL.replace("/api/chat", "/api/transcribe")


def _post_brain_sync(messages, reply_context=None):
    payload = {"messages": messages, "user_prompt": "", "max_tokens": 800,
               "cards_as_text": False}  # کارت‌ها را ساختاریافته بگیر (خودمان عکس می‌فرستیم)
    if reply_context:
        payload["reply_context"] = reply_context
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        config.BRAIN_CHAT_URL, data=body, method="POST",
        headers={"Content-Type": "application/json", "X-SB-Token": config.SALE_BRAIN_TOKEN},
    )
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode("utf-8"))  # دیکشنریِ کامل: text + cards + ...


def _post_transcribe_sync(audio_b64, filename):
    body = json.dumps({"audio_b64": audio_b64, "filename": filename}).encode("utf-8")
    req = urllib.request.Request(
        _TRANSCRIBE_URL, data=body, method="POST",
        headers={"Content-Type": "application/json", "X-SB-Token": config.SALE_BRAIN_TOKEN},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return (json.loads(r.read().decode("utf-8")).get("text") or "").strip()


async def _ask_brain(messages, reply_context=None):
    try:
        return await asyncio.to_thread(_post_brain_sync, messages, reply_context)
    except Exception as e:  # noqa: BLE001
        print(f"[autoreply] خطای مغز: {type(e).__name__}: {e}")
        return {}


async def _reply_card_context(client, chat_id):
    """اگر آخرین پیامِ مشتری ریپلای به یک کارتِ محصول باشد، نام/لینکِ آن کارت را برمی‌گرداند
    تا مغز همان محصول را دقیق resolve کند (نه محصولِ دیگری)."""
    try:
        last = await client.get_messages(chat_id, limit=1)
        if not last:
            return None
        m = last[0]
        if getattr(m, "out", False) or not getattr(m, "reply_to", None):
            return None
        replied = await m.get_reply_message()
        if not replied:
            return None
        cap = (getattr(replied, "message", None) or getattr(replied, "text", None) or "")
        name, url = "", ""
        for line in cap.splitlines():
            s = line.strip()
            if s.startswith("⌚"):
                name = s.lstrip("⌚").strip()
            elif s.startswith("🔗"):
                url = s.lstrip("🔗").strip()
        if name or url:
            return {"name": name, "url": url}
    except Exception as e:  # noqa: BLE001
        print(f"[autoreply] خواندنِ کارتِ ریپلای ناموفق: {type(e).__name__}: {e}")
    return None


async def _transcribe_msg(client, msg):
    """وویس/صوتِ مشتری را دانلود و با مغز (/api/transcribe، همان Whisper) به متن تبدیل می‌کند."""
    try:
        data = await msg.download_media(file=bytes)
        if not data:
            return ""
        b64 = base64.b64encode(data).decode("ascii")
        return await asyncio.to_thread(_post_transcribe_sync, b64, "voice.ogg")
    except Exception as e:  # noqa: BLE001
        print(f"[autoreply] ترنسکرایبِ وویس ناموفق: {type(e).__name__}: {e}")
        return ""


def _card_caption(c):
    """کپشنِ کارتِ محصول (یوزربات دکمهٔ اینلاین ندارد؛ لینک داخلِ کپشن می‌آید)."""
    lines = ["⌚ " + (c.get("name") or "")]
    if c.get("on_sale") and c.get("sale_price_label"):
        reg = c.get("regular_price_label", "")
        lines.append(f"🔖 {c['sale_price_label']}" + (f" (قبلاً {reg})" if reg else "") + " ✨")
    elif c.get("price_label"):
        lines.append("💰 " + c["price_label"])
    av, ship = c.get("availability", ""), c.get("shipping_time", "")
    if av or ship:
        emoji = "⚡" if ship == "ارسال فوری" else "🚚"
        lines.append(emoji + " " + " · ".join(x for x in (av, ship) if x))
    if c.get("url"):
        lines.append("🔗 " + c["url"])
    return "\n".join(lines)


async def _send_card(client, chat_id, c):
    cap = _card_caption(c)
    img = c.get("image")
    _bot_recent_send[chat_id] = time.monotonic()
    try:
        if img:
            await client.send_file(chat_id, file=img, caption=cap)
        else:
            await client.send_message(chat_id, cap, link_preview=False)
    except Exception as e:  # noqa: BLE001
        print(f"[autoreply] ارسالِ کارت ناموفق: {type(e).__name__}: {e}")
        try:
            await client.send_message(chat_id, cap, link_preview=False)  # fallbackِ متنی
        except Exception:  # noqa: BLE001
            pass
    await asyncio.sleep(0.4)  # فاصلهٔ کوچک بین کارت‌ها


async def _history(client, chat_id):
    """چند پیامِ آخرِ گفتگو را به فرمتِ messages مغز می‌سازد (out=assistant، in=user).

    وویس/صوتِ مشتری را در همین‌جا به متن تبدیل می‌کند (با کش، تا دوباره ترنسکرایب نشود)."""
    msgs = await client.get_messages(chat_id, limit=config.HISTORY_LIMIT)
    out = []
    for m in reversed(list(msgs)):  # قدیمی‌ترین → جدیدترین
        txt = (getattr(m, "message", None) or getattr(m, "text", None) or "").strip()
        if not txt and not getattr(m, "out", False) and (getattr(m, "voice", None) or getattr(m, "audio", None)):
            txt = _voice_cache.get(m.id)
            if txt is None:
                txt = await _transcribe_msg(client, m)
                _voice_cache[m.id] = txt
        if not txt:
            continue
        out.append({"role": "assistant" if getattr(m, "out", False) else "user", "content": txt})
    return out


async def _delayed_reply(client, chat_id, name, delay):
    try:
        if delay > 0:
            await asyncio.sleep(delay)  # حالتِ تأخیری (اپراتور واردِ چت شده) — پنجرهٔ اپراتور
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
        reply_ctx = await _reply_card_context(client, chat_id)  # ریپلای‌به‌کارت؟ → همان محصول
        resp = await _ask_brain(history, reply_ctx)
        text = (resp.get("text") or "").strip()
        cards = resp.get("cards") or []
        if not text and not cards:
            return
        if len(text) > config.MAX_AUTOREPLY_CHARS:
            text = text[: config.MAX_AUTOREPLY_CHARS].rstrip() + " …"
        _bot_recent_send[chat_id] = time.monotonic()
        if text:
            await client.send_message(chat_id, text, link_preview=False)
        for c in cards[:7]:  # کارتِ محصول‌ها را به‌صورت عکس بفرست (مثلِ رباتِ رسمی)
            await _send_card(client, chat_id, c)
        _bot_recent_send[chat_id] = time.monotonic()
        db.log_autoreply(chat_id, name, history[-1]["content"], text or f"[{len(cards)} کارتِ محصول]")
        print(f"[autoreply] پاسخ به {name or chat_id} ({len(cards)} کارت)")
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
            # درجا جواب بده؛ مگر اپراتور اخیراً واردِ چت شده باشد → آن‌وقت تأخیر (پنجرهٔ اپراتور)
            op_active = (time.monotonic() - _operator_active.get(event.chat_id, 0)) < config.OPERATOR_ACTIVE_WINDOW
            delay = config.REPLY_GRACE_SEC if op_active else 0
            print(f"[autoreply] DM از «{name or event.chat_id}» → زمان‌بندیِ پاسخ (delay={delay}s)")
            _cancel(event.chat_id)
            _pending[event.chat_id] = asyncio.create_task(_delayed_reply(client, event.chat_id, name, delay))
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
            # «آدم» (اپراتور) واردِ چت شد → چت تأخیری می‌شود و پاسخِ معلق لغو می‌شود
            _operator_active[event.chat_id] = time.monotonic()
            _cancel(event.chat_id)
        except Exception as e:  # noqa: BLE001
            print(f"[autoreply] هندلرِ خروجی: {type(e).__name__}: {e}")

    print("[autoreply] هندلرهای پاسخِ خودکار ثبت شد.")


# ---------- فالوآپِ مشتریانِ بی‌پیگیری ----------
async def followup_scan(client):
    """گفتگوهای خصوصیِ ساکت با سابقهٔ تعامل را یک‌بار محترمانه پیگیری می‌کند."""
    if db.get_meta("followup") != "on" or not _within_followup_hours() or db.get_meta("account_locked") == "1":
        return 0
    cutoff = clock.utcnow().replace(tzinfo=datetime.timezone.utc) - datetime.timedelta(hours=config.FOLLOWUP_AFTER_HOURS)
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
