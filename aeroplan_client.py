"""
Wrapper around Air Canada's internal Aeroplan award-search JSON endpoint.

This endpoint is unofficial and Air Canada may change it at any time. If the
parser stops returning matches, open aircanada.com in a browser, do a real
award search with DevTools > Network open, and adjust `_build_body` and
`_parse` to match the current request/response shapes.

Set AEROPLAN_MOCK=1 in the environment to return a deterministic fake
itinerary without hitting the network. The mock is intentionally NOT random
so that email + dedup flows can be verified reliably end-to-end.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass, asdict
from typing import Any

import requests

log = logging.getLogger(__name__)

# Best-effort known shape of Air Canada's award endpoint. Verify against
# aircanada.com DevTools if results look wrong.
ENDPOINT = "https://akamai-gw.dbaas.aircanada.com/loyalty/dfwr/api/v2/search/award"

# Internal cabin name -> Air Canada / partner fare-family code prefixes seen
# in responses. Lower-cased matching, any-prefix-hit wins.
CABIN_MATCHES: dict[str, tuple[str, ...]] = {
    "economy": ("eco", "economy"),
    "premium_economy": ("premeco", "premium"),
    "business": ("business", "signature", "executive"),
    "first": ("first",),  # rare on AC; mostly partners (LH, NH, SQ)
}


@dataclass
class Itinerary:
    date: str
    origin: str
    destination: str
    cabin: str
    miles: int
    taxes_usd: float
    flight_numbers: str          # e.g. "AC870,AC43"
    operating_carrier: str       # e.g. "AC", "UA", "LH"
    depart_time: str
    arrive_time: str
    stops: int

    def flight_key(self) -> str:
        raw = (
            f"{self.date}|{self.origin}|{self.destination}|"
            f"{self.cabin}|{self.operating_carrier}|{self.flight_numbers}"
        )
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _headers() -> dict[str, str]:
    return {
        "accept": "application/json, text/plain, */*",
        "accept-language": "en-CA,en;q=0.9",
        "content-type": "application/json",
        "origin": "https://www.aircanada.com",
        "referer": "https://www.aircanada.com/aeroplan/redeem/availability/outbound",
        "user-agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
    }


def _build_body(origin: str, destination: str, date: str, passengers: int) -> dict[str, Any]:
    return {
        "bookingType": "redeem",
        "tripType": "OneWay",
        "passengers": {"adult": int(passengers), "child": 0, "infant": 0},
        "bounds": [
            {
                "originLocationCode": origin.upper(),
                "destinationLocationCode": destination.upper(),
                "departureDate": date,
                "cabin": "",
            }
        ],
        "isFlexibleDate": False,
        "lang": "en-CA",
        "pos": "CA",
    }


def _cabin_from_fare(fare_family: str) -> str | None:
    lower = (fare_family or "").lower()
    for cabin, prefixes in CABIN_MATCHES.items():
        if any(p in lower for p in prefixes):
            return cabin
    return None


def _parse(data: dict[str, Any], origin: str, destination: str, date: str) -> list[Itinerary]:
    results: list[Itinerary] = []
    bounds = data.get("bounds") or []
    for bound in bounds:
        segments = bound.get("segments") or []
        flight_numbers = ",".join(
            f"{seg.get('marketingAirline','')}{seg.get('flightNumber','')}"
            for seg in segments
        )
        operating_carriers = [
            (seg.get("operatingAirline") or seg.get("marketingAirline") or "").upper()
            for seg in segments
        ]
        # If any segment is operated by AC, we call it AC for "ac_only" filter.
        operating = "AC" if "AC" in operating_carriers else (
            operating_carriers[0] if operating_carriers else ""
        )
        depart = (segments[0].get("departureDateTime") if segments else "") or ""
        arrive = (segments[-1].get("arrivalDateTime") if segments else "") or ""
        stops = max(len(segments) - 1, 0)

        for fare in bound.get("fareFamilies") or []:
            miles = int(fare.get("points") or 0)
            if miles <= 0:
                continue
            cabin = _cabin_from_fare(fare.get("cabin") or fare.get("name") or "")
            if cabin is None:
                continue
            taxes_block = fare.get("taxesAndFees") or {}
            try:
                taxes = float(taxes_block.get("amount") or 0.0)
            except (TypeError, ValueError):
                taxes = 0.0

            results.append(
                Itinerary(
                    date=date,
                    origin=origin.upper(),
                    destination=destination.upper(),
                    cabin=cabin,
                    miles=miles,
                    taxes_usd=taxes,
                    flight_numbers=flight_numbers or "?",
                    operating_carrier=operating or "?",
                    depart_time=depart,
                    arrive_time=arrive,
                    stops=stops,
                )
            )
    return results


def _mock_search(origin: str, destination: str, date: str) -> list[Itinerary]:
    """Deterministic, always-matching result so email/dedup tests are reliable."""
    return [
        Itinerary(
            date=date,
            origin=origin.upper(),
            destination=destination.upper(),
            cabin="business",
            miles=55000,
            taxes_usd=45.50,
            flight_numbers="AC870",
            operating_carrier="AC",
            depart_time=f"{date}T18:30:00",
            arrive_time=f"{date}T06:00:00",
            stops=0,
        ),
        Itinerary(
            date=date,
            origin=origin.upper(),
            destination=destination.upper(),
            cabin="economy",
            miles=35000,
            taxes_usd=120.00,
            flight_numbers="UA123",
            operating_carrier="UA",
            depart_time=f"{date}T09:15:00",
            arrive_time=f"{date}T21:40:00",
            stops=1,
        ),
    ]


def search(origin: str, destination: str, date: str, passengers: int = 1) -> list[Itinerary]:
    """Fetch one-way Aeroplan award itineraries for a single date. Returns [] on any error."""
    if os.getenv("AEROPLAN_MOCK") == "1":
        return _mock_search(origin, destination, date)

    try:
        resp = requests.post(
            ENDPOINT,
            json=_build_body(origin, destination, date, passengers),
            headers=_headers(),
            timeout=20,
        )
    except requests.RequestException as e:
        log.warning("Aeroplan request failed for %s->%s %s: %s", origin, destination, date, e)
        return []

    if resp.status_code != 200:
        log.warning(
            "Aeroplan returned %s for %s->%s %s: %s",
            resp.status_code, origin, destination, date, resp.text[:200],
        )
        return []

    try:
        data = resp.json()
    except ValueError:
        log.warning("Aeroplan returned non-JSON for %s->%s %s", origin, destination, date)
        return []

    return _parse(data, origin, destination, date)
