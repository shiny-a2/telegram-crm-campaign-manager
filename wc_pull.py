"""کشیدن زنده‌ی مخاطبین از سفارش‌های ووکامرس (شماره‌ی صورت‌حساب).

همه‌ی خریدارها (حتی مهمان) را پوشش می‌دهد. شماره‌ها یکتا و نرمال می‌شوند.
اجرا:  python wc_pull.py            (همه)
       python wc_pull.py 1         (فقط ۱ صفحه برای تست)
"""
from __future__ import annotations

import asyncio
import sys

from woocommerce import API

import config
import db
from importer import normalize_phone

_api = None


def _client():
    global _api
    if _api is None:
        _api = API(
            url=config.WOO_URL,
            consumer_key=config.WOO_CK,
            consumer_secret=config.WOO_CS,
            version="wc/v3",
            timeout=40,
            query_string_auth=True,
        )
    return _api


def _get_orders(page):
    resp = _client().get(
        "orders",
        params={"per_page": 100, "page": page, "orderby": "id", "order": "asc", "status": "any"},
    )
    resp.raise_for_status()
    return resp.json()


async def pull(max_pages=1000):
    db.init()
    if not config.woo_ready():
        print("ووکامرس پیکربندی نشده (.env)."); return 0
    seen, rows, page = set(), [], 1
    while page <= max_pages:
        batch = await asyncio.to_thread(_get_orders, page)
        if not batch:
            break
        for o in batch:
            b = o.get("billing", {}) or {}
            phone = normalize_phone(b.get("phone"))
            if not phone or phone in seen:
                continue
            seen.add(phone)
            name = f"{b.get('first_name','')} {b.get('last_name','')}".strip()
            rows.append({"phone": phone, "name": name})
        print(f"صفحه {page} | شماره‌های یکتا تا اینجا: {len(seen)}")
        if len(batch) < 100:
            break
        page += 1
    added, updated, optout, skipped = db.import_contacts(rows)
    print(f"کشیده شد ✅ جدید: {added} | به‌روز: {updated} | کل یکتا: {len(seen)}")
    return len(seen)


if __name__ == "__main__":
    mp = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
    asyncio.run(pull(mp))
