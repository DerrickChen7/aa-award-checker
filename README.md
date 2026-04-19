# aa-award-checker

A small Python service that watches American Airlines award-ticket availability
and emails you when a route matching your criteria (cabin class, max miles,
date window) appears. Comes with a local web UI for managing watched routes.

## How it works

- **`app.py`** — Flask web UI at `http://127.0.0.1:5000` for adding/editing/deleting routes (SQLite `app.db`).
- **`checker.py`** — Standalone script. Iterates enabled routes, calls AA's award-search JSON endpoint, filters matches, and emails alerts via Gmail SMTP. Run it on a cron schedule.
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

## Run the web UI

```bash
python app.py
```

Open http://127.0.0.1:5000 and add a route, e.g. `JFK -> LHR`, business, 60000
max miles, over the next 30 days.

Click **Run check now** to trigger a poll immediately (runs in a background
thread; watch your inbox).

## Run the checker on a schedule

### macOS / Linux — cron

```bash
crontab -e
```

Add this line (adjust paths):

```
*/20 * * * * cd /absolute/path/to/aa-award-checker && /absolute/path/to/venv/bin/python checker.py >> checker.log 2>&1
```

### macOS — launchd (alternative)

Create `~/Library/LaunchAgents/com.aa-award-checker.plist` with a
`StartInterval` of `1200` (20 minutes). See Apple's `launchd.plist(5)` docs.

## Testing without hitting AA

Set `AA_MOCK=1` in your environment (or in `.env`) and the checker will return
deterministic fake itineraries instead of calling aa.com. Great for verifying
your Gmail setup and the dedup logic.

```bash
AA_MOCK=1 python checker.py
```

## File layout

```
app.py             Flask web UI
checker.py         Main polling entry point (cron runs this)
aa_client.py       AA search endpoint wrapper (swap this file to switch data source)
emailer.py         Gmail SMTP sender
db.py              SQLite helper
schema.sql         Table definitions
templates/         Jinja templates for the web UI
requirements.txt
.env.example
```

## Notes / caveats

- The AA endpoint (`www.aa.com/booking/api/search/itinerary`) is **not** an
  official public API. If AA changes its request/response shape, edit
  `aa_client.py` (`_build_body` / `_parse`). Use your browser's DevTools
  Network tab on a real aa.com award search to inspect the current shape.
- Poll too aggressively and you'll get blocked or rate-limited. 20 minutes is
  a reasonable default; don't go below 5.
- This tool only *alerts* — it does not hold or book seats. Award inventory
  can disappear within minutes, so be ready to book when you see an email.
