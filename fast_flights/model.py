from dataclasses import dataclass
from typing import Literal


@dataclass
class Airline:
    code: str
    name: str


@dataclass
class Alliance:
    code: str
    name: str


@dataclass
class JsMetadata:
    airlines: list[Airline]
    alliances: list[Alliance]


@dataclass
class Airport:
    name: str
    code: str


@dataclass
class SimpleDatetime:
    date: tuple[int, int, int]
    time: tuple[int, int]


@dataclass
class SingleFlight:
    from_airport: Airport
    to_airport: Airport
    departure: SimpleDatetime
    arrival: SimpleDatetime

    duration: int
    """Unit: minutes"""

    plane_type: str

    flight_number: str

    codeshares: list[str]


@dataclass
class CarbonEmission:
    typical_on_route: int
    """Unit: grams"""

    emission: int
    """Unit: grams"""


@dataclass
class Flights:
    type: str | Literal["multi"]
    price: int | None
    """Fare amount, or None when Google returns an unpriced itinerary."""
    airlines: list[str]
    flights: list[SingleFlight]
    carbon: CarbonEmission


@dataclass
class PriceHistoryPoint:
    """One point on Google Flights' price-history graph."""

    price: int
    iso_date: str
    """Calendar date for this point. Format: ``YYYY-MM-DD`` (UTC)."""


@dataclass
class PriceInsights:
    """Price intelligence for a search (typical band + history graph)."""

    lowest_price: int | None
    """Lowest fare among returned flights in the insights cohort."""

    price_level: Literal["low", "typical", "high", ""]
    """Where the assessed fare sits relative to ``typical_price_range``.

    For ``get_price_history``, this uses the matched flight's price when
    found; otherwise Google's cohort ``lowest_price``.
    """

    typical_price_range: tuple[int, int] | None
    """``(low, high)`` band Google labels as typical for similar trips."""

    price_history: list[PriceHistoryPoint]
    """Series plotted as “Price history for these flights”."""

    estimated_savings: int | None = None
    """Signed delta vs a typical fare when Google provides one."""

    destination_city: str | None = None
    """City name used in the insights copy, when present."""

    flight_price: int | None = None
    """Price of the requested flight number, when present in results."""
