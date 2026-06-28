# Telegram CRM Campaign Manager

> A controlled, rate-limited engine for sending **scheduled customer messages** (announcements, follow-ups) to contacts from your CRM over Telegram, with a **live dashboard** for progress, counts and start/stop control.

Designed for **responsible, consent-based customer engagement** — not blasting. Every delivery policy in this project exists to keep volume low, human-paced, and respectful of recipients and of Telegram's limits.

**What this demonstrates:** async Python · rate-limiting, backoff & flood-control · a small data pipeline (CSV import, phone normalization, dedup, consent) · a live FastAPI control dashboard · self-healing supervisor process.

`Python` · `Telethon` · `FastAPI` · `SQLite` · `asyncio`

---

## Responsible-use design

This tool is built for messaging customers **who are already in your CRM** (existing relationships, opt-in lists), and it bakes in safeguards rather than leaving them to discipline:

- **Consent-aware** — the importer reads a `consent_status` column and **skips `optout` contacts**.
- **Warm-up ramp** — starts at a low daily volume and increases gradually over days, never a sudden spike.
- **Human pacing** — randomized delay between messages, bursts with rest periods, and a configurable **business-hours-only** window (timezone-aware).
- **Self-throttling** — automatically pauses when Telegram signals a rate limit (`PeerFlood`), instead of pushing through.
- **Per-contact dedup** — never messages the same contact twice for a campaign.
- **Starts paused** — the engine is idle by default; a human starts it from the dashboard.

Use it for legitimate customer communication and follow local regulations on electronic messaging.

---

## Features

- **CRM import** — reads contacts from a CSV export, with automatic column detection, phone-number normalization, and opt-out handling.
- **Rotating templates** — multiple message variants with `{name}` personalization, so traffic doesn't look identical.
- **Warm-up daily cap** — low on day one, ramping to a ceiling.
- **Anti-block pacing** — randomized delays, burst + rest cycles, business-hours window, automatic stop on rate limits.
- **Live dashboard** — progress %, counts, engine status, template management, and start/stop control.
- **CRM sync** — optionally pulls new contacts from your CRM on a schedule.

---

## Architecture

```
   CRM export (CSV) ──▶ importer.py ──▶  SQLite (db.py)  ◀── dashboard.py (FastAPI)
                                              │                    ▲   control + stats
                                              ▼                    │
                                         sender.py  (Telethon userbot)
                                   warm-up · delays · bursts · hours · PeerFlood guard
                                              │
                                              ▼
                                          Telegram
```

`main.py` is a self-healing supervisor that runs the sender and the dashboard together and restarts them on failure.

### Dashboard (sketch)

```
  Progress   ██████████████░░░░░░░░░░   58%   (5,640 / 9,698)
  ─────────────────────────────────────────────────────────
  Sent today   312 / 400 cap      Seen        3,110
  Engine       ● running          Failed         12
  ─────────────────────────────────────────────────────────
  [ Start ]  [ Pause ]      6 templates · next send ~47s
```

---

## Tech stack

- **Python 3.12**
- **Telethon** — Telegram client (userbot)
- **FastAPI + Uvicorn** — live dashboard and control API
- **SQLite** — contacts, templates, send log, engine state
- **httpx** — CRM / WooCommerce pulls

---

## Project structure

| File | Role |
|------|------|
| `config.py` | Loads configuration from `.env` |
| `db.py` | SQLite store (contacts / templates / send log / state) + stats |
| `importer.py` | CSV contact import (column detection, normalization, opt-out) |
| `sender.py` | Telethon userbot + anti-block delivery policies |
| `dashboard.py` | FastAPI dashboard + control |
| `crm_pull.py` / `wc_pull.py` | Pull contacts from a CRM / WooCommerce |
| `login.py` | One-time Telegram login |
| `main.py` | Entry point + self-healing supervisor |

A sample input file is provided at `data/sample_contacts.csv` (synthetic data) to show the expected CSV shape.

---

## Setup

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # Windows

cp .env.example .env        # then fill in your values
python login.py             # one-time Telegram login (enter the code)
python importer.py data/sample_contacts.csv
python main.py
```

Dashboard: `http://<host>:8091/?token=<DASH_TOKEN>`

The sender **starts paused** — press "Start" in the dashboard when you're ready.

### Configuration (`.env`)

| Variable | Description |
|----------|-------------|
| `TG_API_ID`, `TG_API_HASH` | From https://my.telegram.org → API development tools |
| `TG_PHONE` | Sending account's phone, with country code |
| `DASH_TOKEN` | Password for the dashboard |
| `DAILY_CAP`, `WARMUP_START`, `WARMUP_STEP` | Volume ramp |
| `DELAY_MIN_SEC`, `DELAY_MAX_SEC` | Randomized inter-message delay |
| `SEND_HOUR_START`, `SEND_HOUR_END` | Business-hours window (Tehran time) |

---

## Notes

- Comments are written in Persian, matching the original deployment.
- No secrets, Telegram sessions, databases, or real contact data are included in this repository — only `.env.example` placeholders and a synthetic sample CSV.

## License

MIT — see [LICENSE](LICENSE).
