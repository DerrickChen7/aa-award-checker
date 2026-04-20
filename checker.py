"""
One polling run: for every enabled route, check every date in the window,
keep itineraries matching cabin + max_miles, and email the user when a
previously-unseen (or re-appeared) itinerary shows up.
"""

from __future__ import annotations

import logging
import sys
import time
from datetime import date, datetime, timedelta
from urllib.parse import urlencode

import aeroplan_client as client
import emailer
from db import get_conn

log = logging.getLogger(__name__)

# If an itinerary hasn't been seen for this long, the next appearance is
# treated as a fresh one and re-alerts.
REAPPEAR_AFTER = timedelta(hours=2)

# Drop old alert_state rows after this long so the table doesn't grow forever.
PRUNE_AFTER = timedelta(days=2)

SLEEP_BETWEEN_CALLS_SEC = 2.0


def _iter_dates(start: str, end: str):
    d0 = date.fromisoformat(start)
    d1 = date.fromisoformat(end)
    if d1 < d0:
        return
    cur = d0
    while cur <= d1:
        yield cur.isoformat()
        cur += timedelta(days=1)


def _deeplink(itin: client.Itinerary, passengers: int) -> str:
    params = {
        "tripType": "OneWay",
        "lang": "en-CA",
        "bookingType": "redeem",
        "org0": itin.origin,
        "dest0": itin.destination,
        "departureDate0": itin.date,
        "ADT": passengers,
    }
    return "https://www.aircanada.com/aeroplan/redeem/availability/outbound?" + urlencode(params)


def _format_email(route: dict, matches: list[client.Itinerary]) -> tuple[str, str]:
    best = min(matches, key=lambda m: m.miles)
    subject = (
        f"[Aeroplan] {route['origin']}->{route['destination']} "
        f"{route['cabin'].title()} from {best.miles:,} mi on {best.date}"
    )

    lines = [
        f"Match for route #{route['id']}: "
        f"{route['origin']}->{route['destination']} "
        f"{route['cabin']} <= {route['max_miles']:,} mi, "
        f"{route['passengers']} pax, "
        f"carrier={route['carrier_filter']}, "
        f"window {route['start_date']}..{route['end_date']}",
        "",
    ]
    for m in sorted(matches, key=lambda x: (x.date, x.miles)):
        lines.append(
            f"  {m.date}  {m.operating_carrier:<3} {m.flight_numbers:<16}  "
            f"stops={m.stops}  {m.miles:>7,} mi + ${m.taxes_usd:.2f}  "
            f"{m.depart_time} -> {m.arrive_time}"
        )
        lines.append(f"    {_deeplink(m, route['passengers'])}")
    return subject, "\n".join(lines)


def _load_routes(conn) -> list[dict]:
    rows = conn.execute("SELECT * FROM routes WHERE enabled = 1").fetchall()
    return [dict(r) for r in rows]


def _prior_seen(conn, route_id: int, key: str):
    row = conn.execute(
        "SELECT last_seen_available_at, alerted_at FROM alert_state "
        "WHERE route_id = ? AND flight_key = ?",
        (route_id, key),
    ).fetchone()
    return dict(row) if row else None


def _upsert_state(conn, route_id: int, key: str, now_iso: str, did_alert: bool):
    existing = _prior_seen(conn, route_id, key)
    if existing is None:
        conn.execute(
            "INSERT INTO alert_state(route_id, flight_key, last_seen_available_at, alerted_at) "
            "VALUES (?, ?, ?, ?)",
            (route_id, key, now_iso, now_iso if did_alert else ""),
        )
        return
    new_alerted = now_iso if did_alert else existing["alerted_at"]
    conn.execute(
        "UPDATE alert_state SET last_seen_available_at = ?, alerted_at = ? "
        "WHERE route_id = ? AND flight_key = ?",
        (now_iso, new_alerted, route_id, key),
    )


def _prune(conn, now: datetime):
    cutoff = (now - PRUNE_AFTER).isoformat()
    conn.execute("DELETE FROM alert_state WHERE last_seen_available_at < ?", (cutoff,))


def _check_route(conn, route: dict, now: datetime) -> int:
    """Check a single route. Returns number of alerts sent."""
    matches_to_alert: list[client.Itinerary] = []
    now_iso = now.isoformat()

    for d in _iter_dates(route["start_date"], route["end_date"]):
        try:
            itins = client.search(
                route["origin"], route["destination"], d, route["passengers"]
            )
        except Exception as e:  # client already swallows most, this is belt-and-suspenders
            log.exception("search crashed for route %s on %s: %s", route["id"], d, e)
            itins = []

        for itin in itins:
            if itin.cabin != route["cabin"]:
                continue
            if itin.miles > route["max_miles"]:
                continue
            if route["carrier_filter"] == "ac_only" and itin.operating_carrier != "AC":
                continue

            key = itin.flight_key()
            prior = _prior_seen(conn, route["id"], key)
            should_alert = True
            if prior and prior["last_seen_available_at"]:
                last_seen = datetime.fromisoformat(prior["last_seen_available_at"])
                if now - last_seen < REAPPEAR_AFTER:
                    should_alert = False  # still "continuously available", stay muted

            if should_alert:
                matches_to_alert.append(itin)
                _upsert_state(conn, route["id"], key, now_iso, did_alert=True)
            else:
                _upsert_state(conn, route["id"], key, now_iso, did_alert=False)

        time.sleep(SLEEP_BETWEEN_CALLS_SEC)

    if matches_to_alert:
        subject, body = _format_email(route, matches_to_alert)
        emailer.send_alert(subject, body)
        return 1
    return 0


def run_once() -> dict:
    """Run one polling pass. Returns a small summary dict."""
    now = datetime.now()
    summary = {"routes_checked": 0, "alerts_sent": 0, "errors": 0}
    with get_conn() as conn:
        routes = _load_routes(conn)
        summary["routes_checked"] = len(routes)
        for r in routes:
            try:
                summary["alerts_sent"] += _check_route(conn, r, now)
            except Exception:
                log.exception("Route %s failed", r["id"])
                summary["errors"] += 1
        _prune(conn, now)
        conn.commit()
    log.info("Run complete: %s", summary)
    return summary


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    run_once()
