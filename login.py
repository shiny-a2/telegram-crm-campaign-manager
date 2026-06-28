"""ورود یک‌باره به تلگرام برای ساخت فایل نشست (session).

این را فقط یک‌بار و در ترمینالِ تعاملی روی سرور اجرا کن:
    .venv\\Scripts\\python.exe login.py
کد ورود (و در صورت داشتن، رمز دومرحله‌ای) را وارد می‌کنی. بعد از آن main.py
بدون نیاز به کد، با همان نشست کار می‌کند.
"""
from __future__ import annotations

import asyncio

from telethon import TelegramClient

import config


async def main():
    if not config.telegram_ready():
        print("ابتدا TG_API_ID و TG_API_HASH و TG_PHONE را در فایل .env بگذار.")
        return
    client = TelegramClient(config.TG_SESSION, config.TG_API_ID, config.TG_API_HASH)
    await client.start(phone=config.TG_PHONE)
    me = await client.get_me()
    print(f"ورود موفق ✅ — {getattr(me, 'first_name', '')} ({getattr(me, 'phone', '')})")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
