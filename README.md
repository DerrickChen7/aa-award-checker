# aeroplan-award-checker

A small Python service that watches **Air Canada Aeroplan** award-ticket
availability and emails you when a route matching your criteria (cabin
class, max miles, carrier filter, date window) appears. Comes with a local
web UI for managing watched routes.

## How it works

- **`app.py`** — Flask web UI at `http://127.0.0.1:5000` for adding/editing/deleting routes (SQLite `app.db`).
- **`checker.py`** — Standalone script. Iterates enabled routes, calls Air Canada's Aeroplan search JSON endpoint, filters matches, and emails alerts via Gmail SMTP. Run it on a cron schedule.
- **`aeroplan_client.py`** — Thin wrapper around the Air Canada endpoint. Swap this file if you ever want to change data sources.
- **Dedup** — Each matching itinerary is "muted" while it stays continuously available (no spam). If it disappears and comes back, you get a new email.

## Setup

### 1. Python + dependencies

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Gmail app password

1. Turn on 2-Step Verification on your Google account.
2. Visit https://myaccount.google.com/apppasswords and create an app password for "Mail".
3. Copy the 16-character password.

### 3. Configure

```bash
cp .env.example .env
# edit .env and fill in GMAIL_USER, GMAIL_APP_PASSWORD, ALERT_TO
```

### 4. Initialize the database

```bash
python db.py
```

`python db.py` is idempotent — safe to run on a fresh install **or** on an
existing `app.db` (it auto-migrates the `carrier_filter` column).

## Run the web UI

```bash
python app.py
```

Open http://127.0.0.1:5000 and add a route, e.g.:

- `YYZ -> LHR`, business, max 70,000 miles, window of the next 30 days
- Carrier filter: **Air Canada only** (only AC metal) or **Any Star Alliance** (UA, LH, NH, SQ, etc.)

Click **Run check now** to trigger a poll immediately (runs in a background
thread; watch your inbox).

## Run the checker on a schedule

### macOS / Linux — cron

```bash
crontab -e
```

Add (adjust paths):

```
*/20 * * * * cd /absolute/path/to/aeroplan-award-checker && /absolute/path/to/venv/bin/python checker.py >> checker.log 2>&1
```

## Testing without hitting aircanada.com

Set `AEROPLAN_MOCK=1` to get a **deterministic, always-matching** itinerary —
useful for verifying Gmail delivery and the dedup logic without depending on
live award space:

```bash
AEROPLAN_MOCK=1 python checker.py
```

The mock emits two itineraries per queried date:

- `AC870` business, 55,000 mi (passes `ac_only` filter)
- `UA123` economy, 35,000 mi (passes `any_star_alliance` filter, blocked by `ac_only`)

So a route configured as `business` + `ac_only` + `max_miles >= 55000` will
reliably produce exactly one alert on the first run and zero on the second
(dedup working).

## File layout

```
app.py                 Flask web UI
checker.py             Main polling entry point (cron runs this)
aeroplan_client.py     Air Canada endpoint wrapper (swap to change data source)
emailer.py             Gmail SMTP sender
db.py                  SQLite helper (with idempotent schema migration)
schema.sql             Table definitions
templates/             Jinja templates for the web UI
requirements.txt
.env.example
```

## Notes / caveats

- The Air Canada endpoint (`akamai-gw.dbaas.aircanada.com/loyalty/dfwr/api/...`)
  is **not** an official public API. If AC changes its request/response shape,
  edit `_build_body` / `_parse` in `aeroplan_client.py`. Use your browser's
  DevTools Network tab on a real aircanada.com award search to inspect the
  current shape.
- Poll too aggressively and you'll get rate-limited or blocked. 20 minutes is
  a reasonable default; don't go below 5.
- This tool only *alerts* — it does not hold or book seats. Award inventory
  can disappear within minutes, so be ready to book when you see an email.
