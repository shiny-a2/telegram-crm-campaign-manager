"""ایندکسِ چنلِ عکس/ویدئوی مچ‌دست → نقشهٔ «رفرانس → پیام‌های مدیا».

ساختار چنل: یک گروه مدیا (عکس/ویدئو)، بعد یک پیامِ متنیِ کوتاه که رفرانس است و
به همان گروه ریپلای شده. همهٔ مدیاهای از تِرمیناتورِ قبلی تا این رفرانس، برای آن‌اند.

اجرا: python media_index.py     (newest ~2500 پیام را ایندکس می‌کند)
"""
from __future__ import annotations

import asyncio
import json
import os
import re

from telethon import TelegramClient

import config

CHANNEL = "your_products_channel"
_HERE = os.path.dirname(os.path.abspath(__file__))
_OUT = os.path.join(_HERE, "data", "media_index.json")

_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-/_]{3,30}$")
_PROD_URL = re.compile(r"yourstore-gallery\.com/product/([^\s)]+)", re.I)


def norm_ref(s):
    return re.sub(r"[^A-Za-z0-9]", "", (s or "")).upper()


def _is_ref(text):
    t = (text or "").strip()
    if not t or " " in t or "\n" in t or "http" in t.lower():
        return None
    if not _REF_RE.match(t):
        return None
    if not (any(c.isalpha() for c in t) and any(c.isdigit() for c in t)):
        return None
    return t


async def build(limit=2500):
    c = TelegramClient(config.TG_SESSION, config.TG_API_ID, config.TG_API_HASH)
    await c.connect()
    ent = await c.get_entity(CHANNEL)
    msgs = []
    async for msg in c.iter_messages(ent, limit=limit):
        msgs.append(msg)
    msgs.reverse()  # قدیمی → جدید

    refs, urls, pending = {}, {}, []
    for msg in msgs:
        if msg.photo or msg.video:
            pending.append(msg.id)
            pending = pending[-12:]
            m = _PROD_URL.search(msg.message or "")
            if m:
                slug = m.group(1).rstrip("/").lower()
                urls.setdefault(slug, [])
                if msg.id not in urls[slug]:
                    urls[slug].append(msg.id)
        else:  # پیام متنی = تِرمیناتورِ گروه (رفرانس یا لینکِ محصول)
            txt = msg.message or ""
            ref = _is_ref(txt)
            mu = _PROD_URL.search(txt)
            if ref and pending:
                refs[norm_ref(ref)] = {"display": ref, "ids": list(pending)}
            elif mu and pending:
                urls[mu.group(1).rstrip("/").lower()] = list(pending)
            pending = []

    data = {"channel": CHANNEL, "refs": refs, "urls": urls}
    os.makedirs(os.path.dirname(_OUT), exist_ok=True)
    json.dump(data, open(_OUT, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"ایندکس شد: {len(refs)} رفرانس، {len(urls)} لینک → {_OUT}")
    # چند نمونه
    for k, v in list(refs.items())[:5]:
        print("  ", v["display"], "→", len(v["ids"]), "مدیا")
    await c.disconnect()


if __name__ == "__main__":
    asyncio.run(build())
