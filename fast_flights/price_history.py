"""Fetch and parse Google Flights price-history insights."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from itertools import chain
from typing import Any, Literal

from selectolax.lexbor import LexborHTMLParser

from .exceptions import FlightsNotFound
from .fetcher import fetch_flights_html
from .model import PriceHistoryPoint, PriceInsights
from .querying import FlightQuery, Query, create_query, parse_flight_number
from .types import Currency, Language, SeatType

_DATA_RE = re.compile(r"data:(\[.*\])\s*,\s*sideChannel:", re.DOTALL)


def _unwrap_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, list) and len(value) > 1 and isinstance(value[1], int):
        return value[1]
    return None


def _price_level(
    price: int | None, low: int | None, high: int | None
) -> Literal["low", "typical", "high", ""]:
    if price is None or low is None or high is None:
        return ""
    if price < low:
        return "low"
    if price > high:
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


def parse_price_insights_payload(
    payload: list[Any],
    *,
    flight_price: int | None = None,
) -> PriceInsights:
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
    assessed = flight_price if flight_price is not None else lowest

    return PriceInsights(
        lowest_price=lowest,
        price_level=_price_level(assessed, typical_low, typical_high),
        typical_price_range=typical_range,
        price_history=history,
        estimated_savings=estimated,
        destination_city=city,
        flight_price=flight_price,
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


def parse_price_insights_html(
    html: str,
    *,
    flight_price: int | None = None,
) -> PriceInsights:
    """Extract price insights from a Google Flights search HTML document."""
    return parse_price_insights_payload(
        _load_ds1_payload(html),
        flight_price=flight_price,
    )


def _itinerary_segments(row: list[Any]) -> list[tuple[str, str]]:
    flight = row[0] if row else None
    if not isinstance(flight, list) or len(flight) <= 2:
        return []
    segments: list[tuple[str, str]] = []
    for seg in flight[2] or []:
        if not isinstance(seg, list) or len(seg) <= 22:
            continue
        entry = seg[22]
        if (
            isinstance(entry, list)
            and len(entry) >= 2
            and entry[0]
            and entry[1] is not None
        ):
            segments.append((str(entry[0]), str(entry[1])))
    return segments


def _itinerary_price(row: list[Any]) -> int | None:
    fare_block = row[1] if len(row) > 1 else None
    if not isinstance(fare_block, list) or not fare_block:
        return None
    fare = fare_block[0]
    if isinstance(fare, list) and len(fare) > 1 and isinstance(fare[1], int):
        return fare[1]
    return None


def find_flight_in_payload(
    payload: list[Any],
    airline: str,
    number: str,
) -> tuple[int | None, int] | None:
    """Return ``(price, stop_count)`` for ``airline``/``number`` in results."""
    groups = [section[0] or [] for section in payload[2:4] if section]
    for row in chain.from_iterable(groups):
        if not isinstance(row, list):
            continue
        segments = _itinerary_segments(row)
        if any(code == airline and num == number for code, num in segments):
            stops = max(len(segments) - 1, 0)
            return _itinerary_price(row), stops
    return None


def _search_query(
    *,
    date: str,
    from_airport: str,
    to_airport: str,
    airline: str,
    seat: SeatType,
    language: str | Literal[""] | Language,
    currency: str | Literal[""] | Currency,
    max_stops: int | None,
) -> Query:
    return create_query(
        flights=[
            FlightQuery(
                date=date,
                from_airport=from_airport,
                to_airport=to_airport,
                airlines=[airline],
                max_stops=max_stops,
            )
        ],
        seat=seat,
        trip="one-way",
        language=language,
        currency=currency,
    )


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
    """Fetch the price-history graph for a specific flight.

    Google's booking UI scopes “Price history for these flights” to a peer
    cohort around the selected itinerary. Filtering the search by the
    flight's airline (and ``max_stops=0`` when the match is nonstop) yields
    that itinerary-specific band and history — not the route-wide cheapest
    series.

    Args:
        flight_number: e.g. ``"F9 3215"`` or ``"DL 353"``.
        date: Departure date ``YYYY-MM-DD``.
        from_airport: Origin IATA code.
        to_airport: Destination IATA code.
        seat: Cabin class.
        language: UI language (blank lets Google decide).
        currency: Fare currency (blank lets Google decide).
        proxy: Optional proxy for the default fetcher.
    """
    airline, number = parse_flight_number(flight_number)

    query = _search_query(
        date=date,
        from_airport=from_airport,
        to_airport=to_airport,
        airline=airline,
        seat=seat,
        language=language,
        currency=currency,
        max_stops=None,
    )
    html = fetch_flights_html(query, proxy=proxy)
    payload = _load_ds1_payload(html)
    match = find_flight_in_payload(payload, airline, number)

    flight_price: int | None = None
    if match is not None:
        flight_price, stops = match
        # Nonstop selections use a tighter peer cohort in the booking UI.
        if stops == 0:
            refined = _search_query(
                date=date,
                from_airport=from_airport,
                to_airport=to_airport,
                airline=airline,
                seat=seat,
                language=language,
                currency=currency,
                max_stops=0,
            )
            html = fetch_flights_html(refined, proxy=proxy)
            payload = _load_ds1_payload(html)
            refined_match = find_flight_in_payload(payload, airline, number)
            if refined_match is not None:
                flight_price = refined_match[0]

    return parse_price_insights_payload(payload, flight_price=flight_price)
