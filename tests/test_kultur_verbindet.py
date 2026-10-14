import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from nrw_events.quality import evaluate_event_quality
from nrw_events.sources import kultur_verbindet
from nrw_events.validation import validate_event

from tests.helpers import patch_window

FIXTURES = Path(__file__).parent / "fixtures" / "kultur-verbindet"
FEEDS = {
    kultur_verbindet.ICAL_URLS[0]: FIXTURES / "kulturverbindetev-2026-10-14.ics",
    kultur_verbindet.ICAL_URLS[1]: FIXTURES / "klimaviertel-2026-10-14.ics",
}


def _fetch(feeds: dict) -> list:
    with patch("nrw_events.ical._impl_http.fetch_url", side_effect=lambda url, **_: feeds[url]):
        return kultur_verbindet.fetch()


class KulturVerbindetTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 10, 14), datetime(2027, 6, 30))
        feeds = {url: path.read_text(encoding="utf-8") for url, path in FEEDS.items()}
        self.events = sorted(_fetch(feeds), key=lambda event: event["start_at"])

    def test_workshops_pass_and_routine_offers_fall_to_quality_policy(self):
        # Consultations and routine meetups are dropped by the shared civic quality rules;
        # the recycling sewing series is an editorial keep despite civic.course.
        naehkurse = "Veranstaltungsreihe Up-, Down- und Recycling-Nähkurse"
        self.assertEqual(
            [(event["start_at"], event["title"]) for event in self.events],
            [
                ("2026-10-15T14:00+02:00", naehkurse),
                ("2026-10-29T14:00+01:00", naehkurse),
                ("2026-11-07T10:00+01:00", "Karneval Nähatelier mit Kostümtauschbörse"),
                ("2026-11-12T14:00+01:00", naehkurse),
                ("2026-11-12T19:00+01:00", "Change Clubs"),
                ("2026-11-26T14:00+01:00", naehkurse),
                ("2026-12-10T14:00+01:00", naehkurse),
            ],
        )
        self.assertTrue(all(validate_event(event) for event in self.events))
        self.assertEqual(self.events[2]["venue"], "Trinkpavillon an der Stadthalle")
        self.assertEqual(self.events[2]["city"], "Bonn-Bad Godesberg")
        self.assertEqual(self.events[0]["venue"], "Zentrenmanagement Bad Godesberg")

    def test_sewing_series_keep_is_scoped_to_this_source(self):
        series = next(event for event in self.events if "Nähkurse" in event["title"])
        elsewhere = {**series, "source_id": "vhs-bonn"}
        self.assertEqual(evaluate_event_quality(elsewhere).rule_id, "civic.course")

    def test_holidays_and_reparad_are_skipped(self):
        titles = {event["title"] for event in self.events}
        self.assertFalse({title for title in titles if "(§)" in title or "Weihnachtstag" in title})
        self.assertFalse({title for title in titles if "repa" in title.casefold() and "rad" in title.casefold()})

    def test_entry_in_both_sub_calendars_is_published_once(self):
        klima = FEEDS[kultur_verbindet.ICAL_URLS[1]].read_text(encoding="utf-8")
        empty = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n"
        once = _fetch({kultur_verbindet.ICAL_URLS[0]: empty, kultur_verbindet.ICAL_URLS[1]: klima})
        twice = _fetch(dict.fromkeys(kultur_verbindet.ICAL_URLS, klima))
        self.assertTrue(once)
        self.assertEqual(len(twice), len(once))


if __name__ == "__main__":
    unittest.main()
