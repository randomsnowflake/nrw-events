import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from nrw_events.ical import parse_ical
from nrw_events.sources import telekom_baskets
from nrw_events.validation import validate_event

from tests.helpers import patch_window

FIXTURE = Path(__file__).parent / "fixtures" / "telekom-baskets-bonn" / "calendar-2026-10-08.ics"


def _parse(raw: str) -> list:
    return telekom_baskets.home_games(parse_ical(
        raw, telekom_baskets.ICAL_URL, telekom_baskets.SOURCE, "Bonn", "Sport Basketball", 1.0,
        "telekom-baskets-bonn", event_filter=telekom_baskets._is_home_game,
        default_category_key="sports", category_locked=True,
    ))


def _calendar(*events: tuple[str, str, str]) -> str:
    body = "".join(
        f"BEGIN:VEVENT\r\nUID:{index}@google.com\r\nDTSTART:{start}\r\nDTEND:{start}\r\n"
        f"SUMMARY:{summary}\r\nLOCATION:{location}\r\nEND:VEVENT\r\n"
        for index, (summary, start, location) in enumerate(events)
    )
    return f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\n{body}END:VCALENDAR\r\n"


DOME = r"Telekom Dome\, Basketsring 1\, 53123 Bonn-Hardtberg\, Deutschland"


class TelekomBasketsFixtureTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 10, 8), datetime(2027, 6, 30))
        self.events = sorted(
            (event for event in _parse(FIXTURE.read_text(encoding="utf-8")) if event["start_date"] >= "2026-10-08"),
            key=lambda event: event["start_at"],
        )

    def test_next_home_games_with_berlin_clock_title_and_venue(self):
        self.assertEqual(
            [(event["start_at"], event["title"]) for event in self.events[:5]],
            [
                ("2026-10-09T20:00+02:00", "Telekom Baskets Bonn – Science City Jena (easyCredit BBL)"),
                ("2026-11-01T18:00+01:00", "Telekom Baskets Bonn – ALBA Berlin (easyCredit BBL)"),
                ("2026-11-04T20:00+01:00", "Telekom Baskets Bonn – Bnei Penlink Herzliya (Basketball Champions League)"),
                ("2026-11-07T18:00+01:00", "Telekom Baskets Bonn – Basketball Löwen Braunschweig (easyCredit BBL)"),
                ("2026-11-17T18:30+01:00", "Telekom Baskets Bonn – Bnei Penlink Herzliya (Basketball Champions League)"),
            ],
        )
        self.assertEqual(len(self.events), 18)
        for event in self.events:
            self.assertEqual(event["venue"], "Telekom Dome")
            self.assertEqual(event["city"], "Bonn-Hardtberg")
            self.assertEqual(event["category_key"], "sports")
            self.assertEqual(event["link"], telekom_baskets.SCHEDULE_URL)
            self.assertEqual(event["organizer"], "Telekom Baskets Bonn")
            self.assertEqual(event["price"], "")

    def test_away_games_and_non_bonn_first_entries_are_skipped(self):
        dates = {event["start_date"] for event in self.events}
        # Würzburg away (2 Oct), Oldenburg cup away (16 Oct), Badalona away (21 Oct).
        self.assertTrue(dates.isdisjoint({"2026-10-02", "2026-10-16", "2026-10-21"}))
        self.assertFalse(any("vs." in event["title"] for event in self.events))

    def test_upstream_venue_noise_and_separator_variants(self):
        by_date = {event["start_date"]: event for event in self.events}
        self.assertEqual(by_date["2026-12-16"]["venue"], "Telekom Dome")
        self.assertEqual(by_date["2027-02-06"]["title"], "Telekom Baskets Bonn – Niners Chemnitz (easyCredit BBL)")
        self.assertIn("gilt aber als Auswärtsspiel", by_date["2026-11-17"]["description"])

    def test_rows_survive_validation(self):
        validated = validate_event(dict(self.events[0]))
        self.assertEqual(validated["title"], self.events[0]["title"])
        self.assertEqual(validated["venue"], "Telekom Dome")

    def test_fetch_reads_the_official_calendar(self):
        with patch("nrw_events.ical._impl_http.fetch_url", return_value=FIXTURE.read_text(encoding="utf-8")) as fetch:
            events = telekom_baskets.fetch()
        self.assertEqual(fetch.call_args.args[0], telekom_baskets.ICAL_URL)
        self.assertTrue(events)


class TelekomBasketsNegativeTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 10, 1), datetime(2026, 12, 31))

    def test_home_named_game_elsewhere_and_placeholders_are_skipped(self):
        events = _parse(_calendar(
            ("Telekom Baskets Bonn vs. FC Porto - Basketball Champions League", "20261007T180000Z", r"Dragão Arena\, Porto"),
            ("Telekom Baskets Bonn vs. Gegner", "20261008T180000Z", ""),
            ("easyCredit BBL Playoffs 1/4-Finale Spiel 2", "20261009T180000Z", DOME),
            ("Saisoneröffnung vs. Nanterre 92", "20261010T143000Z", DOME),
            ("ALBA BERLIN vs. Telekom Baskets Bonn - easyCredit BBL", "20261011T180000Z", DOME),
        ))
        self.assertEqual(events, [])

    def test_missing_competition_keeps_the_opponent_only(self):
        [event] = _parse(_calendar(("Telekom Baskets Bonn vs. Nanterre 92", "20261012T180000Z", DOME)))
        self.assertEqual(event["title"], "Telekom Baskets Bonn – Nanterre 92")
        self.assertEqual(event["start_at"], "2026-10-12T20:00+02:00")


if __name__ == "__main__":
    unittest.main()
