"""
Wrapper around AA.com's internal award-search JSON endpoint.

This endpoint is unofficial and AA may change its shape at any time. If the
parser stops returning matches, open aa.com in a browser, do a real award
search with DevTools > Network open, and adjust `_build_body` / `_parse` to
match the current request/response.

Set AA_MOCK=1 in the environment to return deterministic fake itineraries
without hitting the network (useful for testing email + dedup flow).
"""

from __future__ import annotations

import hashlib
import logging
import os
import random
from dataclasses import dataclass, asdict
from typing import Any

import requests

log = logging.getLogger(__name__)

ENDPOINT = "https://www.aa.com/booking/api/search/itinerary"

# Map our internal cabin names to AA's productType prefixes seen in responses.
# AA uses codes like COACH, PECO (premium economy), PBIZ (business), FIRST.
CABIN_MATCHES: dict[str, tuple[str, ...]] = {
    "economy": ("COACH", "ECONOMY"),
    "premium_economy": ("PECO", "PREMIUM"),
    "business": ("PBIZ", "BUSINESS"),
    "first": ("FIRST", "FLAGSHIP"),
}


@dataclass
class Itinerary:
    date: str
    origin: str
    destination: str
    cabin: str
    miles: int
    taxes_usd: float
    flight_numbers: str          # e.g. "AA100,AA6123"
    depart_time: str             # ISO-ish string straight from AA
    arrive_time: str
    stops: int

    def flight_key(self) -> str:
        raw = f"{self.date}|{self.origin}|{self.destination}|{self.cabin}|{self.flight_numbers}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _headers() -> dict[str, str]:
    return {
        "accept": "application/json, text/plain, */*",
        "accept-language": "en-US,en;q=0.9",
        "content-type": "application/json",
        "origin": "https://www.aa.com",
        "referer": "https://www.aa.com/booking/find-flights",
        "user-agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
    }


def _build_body(origin: str, destination: str, date: str, passengers: int) -> dict[str, Any]:
    return {
        "metadata": {"selectedProducts": [], "tripType": "OneWay", "udo": {}},
        "passengers": [{"type": "adult", "count": int(passengers)}],
        "requestHeader": {"clientId": "AAcom"},
        "slices": [
            {
                "allCarriers": True,
                "cabin": "",
                "departureDate": date,
                "destination": destination.upper(),
                "destinationNearbyAirports": False,
                "maxStops": None,
                "origin": origin.upper(),
                "originNearbyAirports": False,
                "stopsFilter": None,
            }
        ],
        "tripOptions": {
            "corporateBooking": False,
            "fareType": "Lowest",
            "locale": "en_US",
            "pointOfSale": None,
            "searchType": "Award",
        },
        "loyaltyInfo": None,
        "version": "",
        "queryParams": {"sliceIndex": 0, "sessionId": "", "solutionId": "", "solutionSet": ""},
    }


def _cabin_from_product(product_type: str) -> str | None:
    upper = (product_type or "").upper()
    for cabin, prefixes in CABIN_MATCHES.items():
        if any(upper.startswith(p) for p in prefixes):
            return cabin
    return None


def _parse(data: dict[str, Any], origin: str, destination: str, date: str) -> list[Itinerary]:
    results: list[Itinerary] = []
    slices = data.get("slices") or []
    for sl in slices:
        segments = sl.get("segments") or []
        flight_numbers = ",".join(
            f"{(seg.get('flight') or {}).get('carrierCode','')}{(seg.get('flight') or {}).get('flightNumber','')}"
            for seg in segments
        )
        depart = (segments[0].get("departureDateTime") if segments else "") or ""
        arrive = (segments[-1].get("arrivalDateTime") if segments else "") or ""
        stops = max(len(segments) - 1, 0)

        for product in sl.get("pricingDetail") or []:
            miles = int(product.get("perPassengerAwardPoints") or 0)
            if miles <= 0:
                continue
            cabin = _cabin_from_product(product.get("productType", ""))
            if cabin is None:
                continue
            taxes = 0.0
            taxes_block = product.get("perPassengerTaxesAndFees") or {}
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
                    depart_time=depart,
                    arrive_time=arrive,
                    stops=stops,
                )
            )
    return results


def _mock_search(origin: str, destination: str, date: str) -> list[Itinerary]:
    """Deterministic-ish fake results so the rest of the pipeline can be tested."""
    rng = random.Random(f"{origin}{destination}{date}")
    out: list[Itinerary] = []
    for cabin, base in (("economy", 30000), ("business", 57500), ("first", 110000)):
        # Vary: sometimes available, sometimes not.
        if rng.random() < 0.5:
            continue
        out.append(
            Itinerary(
                date=date,
                origin=origin.upper(),
                destination=destination.upper(),
                cabin=cabin,
                miles=base + rng.randint(-5000, 20000),
                taxes_usd=round(5.6 + rng.random() * 200, 2),
                flight_numbers=f"AA{rng.randint(100, 9999)}",
                depart_time=f"{date}T{rng.randint(6,22):02d}:00:00",
                arrive_time=f"{date}T{rng.randint(6,22):02d}:30:00",
                stops=rng.choice([0, 0, 0, 1]),
            )
        )
    return out


def search(origin: str, destination: str, date: str, passengers: int = 1) -> list[Itinerary]:
    """Fetch one-way award itineraries for a single date. Returns [] on any error."""
    if os.getenv("AA_MOCK") == "1":
        return _mock_search(origin, destination, date)

    try:
        resp = requests.post(
            ENDPOINT,
            json=_build_body(origin, destination, date, passengers),
            headers=_headers(),
            timeout=20,
        )
    except requests.RequestException as e:
        log.warning("AA request failed for %s->%s %s: %s", origin, destination, date, e)
        return []

    if resp.status_code != 200:
        log.warning(
            "AA returned %s for %s->%s %s: %s",
            resp.status_code, origin, destination, date, resp.text[:200],
        )
        return []

    try:
        data = resp.json()
    except ValueError:
        log.warning("AA returned non-JSON for %s->%s %s", origin, destination, date)
        return []

    return _parse(data, origin, destination, date)
