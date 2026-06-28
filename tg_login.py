"""ورود به تلگرام به‌صورت دومرحله‌ای و غیرتعاملی (قابل اجرا از راه دور).

مرحله‌ها (به ترتیب):
  python tg_login.py send            → کد ورود به تلگرامِ شماره ارسال می‌شود
  python tg_login.py code 12345      → ورود با کد
  python tg_login.py password ****   → فقط اگر رمز دومرحله‌ای داشتی
  python tg_login.py status          → بررسی وضعیت ورود
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

import config

_STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", ".login_state.json")


def _client():
    return TelegramClient(config.TG_SESSION, config.TG_API_ID, config.TG_API_HASH)


def _save_hash(h):
    os.makedirs(os.path.dirname(_STATE), exist_ok=True)
    json.dump({"hash": h}, open(_STATE, "w"))


def _load_hash():
    try:
        return json.load(open(_STATE))["hash"]
    except Exception:
        return None


async def _send():
    c = _client()
    await c.connect()
    if await c.is_user_authorized():
        print("از قبل وارد شده‌ای ✅"); await c.disconnect(); return
    sent = await c.send_code_request(config.TG_PHONE)
    _save_hash(sent.phone_code_hash)
    print(f"کد ورود به {config.TG_PHONE} ارسال شد. حالا: tg_login.py code <کد>")
    await c.disconnect()


async def _code(code):
    c = _client()
    await c.connect()
    try:
        await c.sign_in(phone=config.TG_PHONE, code=str(code), phone_code_hash=_load_hash())
        me = await c.get_me()
        print(f"ورود موفق ✅ — {getattr(me,'first_name','')} ({getattr(me,'phone','')})")
    except SessionPasswordNeededError:
        print("NEED_PASSWORD: رمز دومرحله‌ای فعال است → tg_login.py password <رمز>")
    await c.disconnect()


async def _password(pwd):
    c = _client()
    await c.connect()
    await c.sign_in(password=str(pwd))
    me = await c.get_me()
    print(f"ورود موفق ✅ — {getattr(me,'first_name','')} ({getattr(me,'phone','')})")
    await c.disconnect()


async def _status():
    c = _client()
    await c.connect()
    print("وارد شده ✅" if await c.is_user_authorized() else "هنوز وارد نشده ❌")
    await c.disconnect()


if __name__ == "__main__":
    if not config.telegram_ready():
        print("ابتدا TG_API_ID/HASH/PHONE را در .env بگذار."); sys.exit(1)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    arg = sys.argv[2] if len(sys.argv) > 2 else None
    if cmd == "send":
        asyncio.run(_send())
    elif cmd == "code" and arg:
        asyncio.run(_code(arg))
    elif cmd == "password" and arg:
        asyncio.run(_password(arg))
    else:
        asyncio.run(_status())
