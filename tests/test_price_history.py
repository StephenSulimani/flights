"""Unit tests for price-history insights parsing and flight pinning."""

import json
import pathlib
import unittest

from fast_flights import FlightQuery, create_query, parse_flight_number
from fast_flights.pb.flights_pb2 import Info
from fast_flights.price_history import parse_price_insights_payload

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


class ParseFlightNumberTests(unittest.TestCase):
    def test_spaced_and_compact(self) -> None:
        self.assertEqual(parse_flight_number("F9 3288"), ("F9", "3288"))
        self.assertEqual(parse_flight_number("f93288"), ("F9", "3288"))
        self.assertEqual(parse_flight_number("UA-837"), ("UA", "837"))

    def test_rejects_garbage(self) -> None:
        with self.assertRaises(ValueError):
            parse_flight_number("not-a-flight")


class BookingTfsTests(unittest.TestCase):
    def test_selected_flight_is_encoded(self) -> None:
        query = create_query(
            flights=[
                FlightQuery(
                    date="2026-11-12",
                    from_airport="ATL",
                    to_airport="LGA",
                    flight_number="F9 3288",
                )
            ],
            seat="economy",
            trip="one-way",
        )
        info = Info.FromString(query.to_bytes())
        selected = info.data[0].selected_flight

        self.assertEqual(selected.from_airport, "ATL")
        self.assertEqual(selected.to_airport, "LGA")
        self.assertEqual(selected.date, "2026-11-12")
        self.assertEqual(selected.airline, "F9")
        self.assertEqual(selected.flight_number, "3288")


class PriceInsightsFixtureTests(unittest.TestCase):
    def _payload(self, block: list) -> list:
        payload: list = [None] * 6
        payload[5] = block
        return payload

    def test_parses_typical_band_and_history(self) -> None:
        fixture = json.loads(
            (FIXTURES / "atl_lga_price_insights.json").read_text()
        )
        insights = parse_price_insights_payload(
            self._payload(fixture["price_insights"])
        )

        self.assertEqual(insights.lowest_price, 102)
        self.assertEqual(insights.typical_price_range, (30, 140))
        self.assertEqual(insights.price_level, "typical")
        self.assertEqual(insights.estimated_savings, -40)
        self.assertEqual(insights.destination_city, "New York")
        self.assertEqual(len(insights.price_history), 9)
        self.assertEqual(insights.price_history[0].iso_date, "2026-08-11")
        self.assertEqual(insights.price_history[0].price, 104)
        self.assertEqual(insights.price_history[-1].iso_date, "2026-10-10")
        self.assertEqual(insights.price_history[-1].price, 102)

    def test_price_level_uses_flight_price_when_provided(self) -> None:
        fixture = json.loads(
            (FIXTURES / "atl_lga_price_insights.json").read_text()
        )
        high = parse_price_insights_payload(
            self._payload(fixture["price_insights"]),
            flight_price=200,
        )
        low = parse_price_insights_payload(
            self._payload(fixture["price_insights"]),
            flight_price=20,
        )
        self.assertEqual(high.price_level, "high")
        self.assertEqual(high.flight_price, 200)
        self.assertEqual(low.price_level, "low")
        self.assertEqual(low.flight_price, 20)

    def test_airline_filtered_histories_differ(self) -> None:
        f9 = json.loads(
            (FIXTURES / "atl_lax_f9_price_insights.json").read_text()
        )["price_insights"]
        dl = json.loads(
            (FIXTURES / "atl_lax_dl_price_insights.json").read_text()
        )["price_insights"]

        f9_insights = parse_price_insights_payload(self._payload(f9))
        dl_insights = parse_price_insights_payload(self._payload(dl))

        self.assertNotEqual(
            f9_insights.typical_price_range,
            dl_insights.typical_price_range,
        )
        self.assertNotEqual(
            [p.price for p in f9_insights.price_history],
            [p.price for p in dl_insights.price_history],
        )
        # Frontier peer band is below Delta's (ULCC vs legacy).
        self.assertLess(
            f9_insights.typical_price_range[1],
            dl_insights.typical_price_range[0],
        )
