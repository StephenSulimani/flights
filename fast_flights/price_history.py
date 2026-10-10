"""Fetch and parse Google Flights price-history insights."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Literal

from selectolax.lexbor import LexborHTMLParser

from .exceptions import FlightsNotFound
from .fetcher import fetch_flights_html
from .model import PriceHistoryPoint, PriceInsights
from .querying import FlightQuery, create_query, parse_flight_number
from .types import Currency, Language, SeatType

_DATA_RE = re.compile(r"data:(\[.*\])\s*,\s*sideChannel:", re.DOTALL)


def _unwrap_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, list) and len(value) > 1 and isinstance(value[1], int):
        return value[1]
    return None


def _price_level(
    lowest: int | None, low: int | None, high: int | None
) -> Literal["low", "typical", "high", ""]:
    if lowest is None or low is None or high is None:
        return ""
    if lowest < low:
        return "low"
    if lowest > high:
        return "high"
    return "typical"


def _history_points(raw: Any) -> list[PriceHistoryPoint]:
    if not isinstance(raw, list) or not raw:
        return []
    series = raw[0] if isinstance(raw[0], list) else raw
    points: list[PriceHistoryPoint] = []
    for item in series:
        if not isinstance(item, list) or len(item) < 2:
            continue
        ts, price = item[0], item[1]
        if not isinstance(ts, (int, float)) or not isinstance(price, int):
            continue
        # Google emits millisecond timestamps.
        seconds = ts / 1000 if ts > 1_000_000_000_000 else ts
        iso_date = datetime.fromtimestamp(seconds, tz=timezone.utc).strftime("%Y-%m-%d")
        points.append(PriceHistoryPoint(price=price, iso_date=iso_date))
    return points


def parse_price_insights_payload(payload: list[Any]) -> PriceInsights:
    """Parse price insights from a decoded ``ds:1`` JSON payload."""
    if len(payload) <= 5 or not payload[5]:
        raise FlightsNotFound("no price insights in response")

    block = payload[5]
    if not isinstance(block, list):
        raise FlightsNotFound("malformed price insights block")

    lowest = _unwrap_int(block[1]) if len(block) > 1 else None
    estimated = _unwrap_int(block[3]) if len(block) > 3 else None
    typical_low = _unwrap_int(block[4]) if len(block) > 4 else None
    typical_high = _unwrap_int(block[5]) if len(block) > 5 else None
    history = _history_points(block[10]) if len(block) > 10 else []
    city = block[12] if len(block) > 12 and isinstance(block[12], str) else None

    typical_range = (
        (typical_low, typical_high)
        if typical_low is not None and typical_high is not None
        else None
    )

    return PriceInsights(
        lowest_price=lowest,
        price_level=_price_level(lowest, typical_low, typical_high),
        typical_price_range=typical_range,
        price_history=history,
        estimated_savings=estimated,
        destination_city=city,
    )


def _load_ds1_payload(html: str) -> list[Any]:
    script = LexborHTMLParser(html).css_first(r"script.ds\:1")
    if script is None:
        raise FlightsNotFound("no flights data script in response")

    text = script.text() or ""
    match = _DATA_RE.search(text)
    if match:
        raw = match.group(1)
    else:
        if "data:" not in text:
            raise FlightsNotFound("no flights data in response")
        raw = text.split("data:", 1)[1].rsplit(",", 1)[0]

    if raw.endswith("errorHasStatus: true"):
        raise FlightsNotFound("no flights found; received error")

    payload = json.loads(raw)
    if not isinstance(payload, list):
        raise FlightsNotFound("malformed flights data payload")
    return payload


def parse_price_insights_html(html: str) -> PriceInsights:
    """Extract price insights from a Google Flights search HTML document."""
    return parse_price_insights_payload(_load_ds1_payload(html))


def get_price_history(
    flight_number: str,
    date: str,
    from_airport: str,
    to_airport: str,
    /,
    *,
    seat: SeatType = "economy",
    language: str | Literal[""] | Language = "",
    currency: str | Literal[""] | Currency = "",
    proxy: str | None = None,
) -> PriceInsights:
    """Fetch the price-history graph for a flight / route / date.

    Google attaches the “Price history for these flights” series to the
    search results for ``from_airport`` → ``to_airport`` on ``date``.
    ``flight_number`` identifies the flight for callers (and validates the
    airline/number shape); the insights block itself is route-scoped, so the
    fetch uses a normal search ``tfs`` without a pinned selected-flight
    (booking-style pins drop the insights payload).

    Args:
        flight_number: e.g. ``"F9 3288"``.
        date: Departure date ``YYYY-MM-DD``.
        from_airport: Origin IATA code.
        to_airport: Destination IATA code.
        seat: Cabin class.
        language: UI language (blank lets Google decide).
        currency: Fare currency (blank lets Google decide).
        proxy: Optional proxy for the default fetcher.
    """
    # Validate / normalize up front so bad input fails before the network call.
    parse_flight_number(flight_number)

    query = create_query(
        flights=[
            FlightQuery(
                date=date,
                from_airport=from_airport,
                to_airport=to_airport,
            )
        ],
        seat=seat,
        trip="one-way",
        language=language,
        currency=currency,
    )
    html = fetch_flights_html(query, proxy=proxy)
    return parse_price_insights_html(html)
