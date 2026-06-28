"""واردکردن مخاطبین از خروجی CSV دیتابیس CRM به دیتابیس محلی.

ستون‌ها به‌صورت خودکار تشخیص داده می‌شوند (نام ستون‌ها مهم نیست) و شماره‌ها به
فرمت بین‌المللی (+98…) نرمال می‌شوند. opt-out از ستون رضایت تشخیص داده می‌شود.

اجرا:  python importer.py مسیر\\contacts.csv
"""
from __future__ import annotations

import csv
import re
import sys

import db

_FA_DIGITS = {ord(p): str(i) for i, p in enumerate("۰۱۲۳۴۵۶۷۸۹")}
_AR_DIGITS = {ord(p): str(i) for i, p in enumerate("٠١٢٣٤٥٦٧٨٩")}

_PHONE_KEYS = ["phone_primary", "phone", "mobile", "tel", "phone_number", "شماره", "موبایل", "تلفن"]
_NAME_KEYS = ["name", "full_name", "نام", "fullname"]
_FIRST_KEYS = ["first_name", "firstname", "نام"]
_LAST_KEYS = ["last_name", "lastname", "نام خانوادگی", "family"]
_CONSENT_KEYS = ["consent_status", "consent", "opt", "optout", "رضایت", "status"]


def normalize_phone(raw):
    """به فرمت +98XXXXXXXXXX. اگر نامعتبر بود، رشته‌ی خالی."""
    if not raw:
        return ""
    s = str(raw).translate(_FA_DIGITS).translate(_AR_DIGITS)
    digits = re.sub(r"\D", "", s)
    if not digits:
        return ""
    if digits.startswith("0098"):
        digits = digits[4:]
    elif digits.startswith("98") and len(digits) >= 12:
        digits = digits[2:]
    elif digits.startswith("0"):
        digits = digits[1:]
    # حالا باید ۱۰ رقم باشد و با 9 شروع شود (موبایل ایران)
    if len(digits) == 10 and digits.startswith("9"):
        return "+98" + digits
    return ""


def _pick(row_lower, keys):
    for k in keys:
        if k in row_lower and (row_lower[k] or "").strip():
            return row_lower[k].strip()
    return ""


def _is_optout(val):
    v = (val or "").strip().lower()
    return v in ("optout", "opt-out", "opt_out", "unsubscribed", "blacklist", "blacklisted", "0", "no", "لغو", "انصراف")


def parse_csv(path):
    rows = []
    seen = set()
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            row_lower = { (k or "").strip().lower(): (v or "") for k, v in raw.items() }
            phone = normalize_phone(_pick(row_lower, _PHONE_KEYS))
            if not phone or phone in seen:
                continue
            seen.add(phone)
            name = _pick(row_lower, _NAME_KEYS)
            if not name:
                fn = _pick(row_lower, _FIRST_KEYS)
                ln = _pick(row_lower, _LAST_KEYS)
                name = (fn + " " + ln).strip()
            optout = False
            consent = _pick(row_lower, _CONSENT_KEYS)
            if consent and _is_optout(consent):
                optout = True
            rows.append({"phone": phone, "name": name, "optout": optout})
    return rows


def import_file(path):
    db.init()
    rows = parse_csv(path)
    added, updated, optout, skipped = db.import_contacts(rows)
    print(f"خوانده‌شده: {len(rows)} | جدید: {added} | به‌روز: {updated} | optout: {optout} | ردشده: {skipped}")
    return added, updated, optout, skipped


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("استفاده: python importer.py مسیر\\contacts.csv")
        sys.exit(1)
    import_file(sys.argv[1])
