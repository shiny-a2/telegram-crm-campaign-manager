"""نقطه‌ی ورود: داشبورد + موتور ارسال در یک حلقه‌ی asyncio.

خودترمیم: هرگز خارج نمی‌شود؛ لاگ با تایم‌استمپِ تهران روی data/app.log.
"""
from __future__ import annotations

import asyncio
import datetime
import os
import sys
import time

import config
import crm_pull
import dashboard
import db
import media_index
import sender

_HERE = os.path.dirname(os.path.abspath(__file__))
_LOG = os.path.join(_HERE, "data", "app.log")
_TEHRAN = datetime.timezone(datetime.timedelta(hours=3, minutes=30))


class _Stamped:
    def __init__(self, stream):
        self._s = stream
        self._line_start = True

    def write(self, text):
        if not text:
            return
        ts = datetime.datetime.now(_TEHRAN).strftime("%m-%d %H:%M:%S")
        out = []
        for piece in text.splitlines(keepends=True):
            if self._line_start:
                out.append(f"[{ts}] ")
            out.append(piece)
            self._line_start = piece.endswith("\n")
        self._s.write("".join(out))

    def flush(self):
        self._s.flush()

    def isatty(self):
        return False


def _setup_logging():
    try:
        os.makedirs(os.path.join(_HERE, "data"), exist_ok=True)
        mode = "a"
        if os.path.exists(_LOG) and os.path.getsize(_LOG) > 2_000_000:
            mode = "w"
        stream = _Stamped(open(_LOG, mode, encoding="utf-8", buffering=1))
        sys.stdout = stream
        sys.stderr = stream
    except Exception:
        pass


async def _syncer():
    """هر چند ساعت یک‌بار مخاطبین جدید CRM را خودکار اضافه می‌کند."""
    if config.SYNC_INTERVAL_HOURS <= 0 or not (config.CRM_EXPORT_URL and config.CRM_EXPORT_TOKEN):
        return
    while True:
        await asyncio.sleep(config.SYNC_INTERVAL_HOURS * 3600)
        try:
            print("[sync] سینک دوره‌ای مخاطبین از CRM…")
            n = await crm_pull.pull()
            print(f"[sync] سینک انجام شد ({n} یکتا).")
        except Exception as e:  # noqa: BLE001
            print(f"[sync] خطای سینک: {type(e).__name__}: {e}")
        try:
            await media_index.build()  # ایندکسِ عکس/ویدئوی چنل
        except Exception as e:  # noqa: BLE001
            print(f"[sync] خطای ایندکس مدیا: {type(e).__name__}: {e}")


async def main():
    db.init()
    tasks = [
        asyncio.create_task(dashboard.serve()),
        asyncio.create_task(sender.run()),
        asyncio.create_task(_syncer()),
    ]
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    _setup_logging()
    os.chdir(_HERE)
    print("[boot] راه‌اندازی سامانه‌ی پیام‌رسانی…")
    while True:
        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            break
        except BaseException as e:  # هیچ خطایی پراسس را نکُشد
            print(f"[fatal] {e!r} — ۱۵ ثانیه دیگر تلاش مجدد")
            try:
                time.sleep(15)
            except Exception:
                pass
