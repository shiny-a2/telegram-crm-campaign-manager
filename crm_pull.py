"""کشیدن مخاطبین از endpoint خروجیِ افزونه‌ی CRM (با احترام به opt-out).

پیش‌نیاز: endpoint طبق docs/crm-contacts-export-spec.md در a2-crm-plugin ساخته شود،
و CRM_EXPORT_URL + CRM_EXPORT_TOKEN در .env تنظیم شوند.

اجرا: python crm_pull.py
"""
from __future__ import annotations

import asyncio
import urllib.request
import urllib.parse
import json

import config
import db
from importer import normalize_phone


def _fetch(page, per_page=1000):
    url = f"{config.CRM_EXPORT_URL}/wp-json/a2crm/v1/contacts?" + urllib.parse.urlencode(
        {"page": page, "per_page": per_page}
    )
    req = urllib.request.Request(url, headers={"X-A2-Token": config.CRM_EXPORT_TOKEN})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read().decode("utf-8"))


async def pull():
    db.init()
    if not (config.CRM_EXPORT_URL and config.CRM_EXPORT_TOKEN):
        print("CRM_EXPORT_URL/TOKEN در .env تنظیم نشده."); return 0
    page, rows, seen = 1, [], set()
    while True:
        data = await asyncio.to_thread(_fetch, page)
        contacts = data.get("contacts") or []
        if not contacts:
            break
        for c in contacts:
            phone = normalize_phone(c.get("phone"))
            if not phone or phone in seen:
                continue
            seen.add(phone)
            rows.append({
                "phone": phone,
                "name": (c.get("name") or "").strip(),
                "optout": (c.get("consent") or "").strip().lower() == "optout",
            })
        print(f"صفحه {page}/{data.get('pages','?')} | یکتا تا اینجا: {len(seen)}")
        if page >= int(data.get("pages") or page):
            break
        page += 1
    added, updated, optout, skipped = db.import_contacts(rows)
    print(f"از CRM کشیده شد ✅ جدید: {added} | به‌روز: {updated} | انصراف: {optout} | کل یکتا: {len(seen)}")
    return len(seen)


if __name__ == "__main__":
    asyncio.run(pull())
