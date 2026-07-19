import unittest
from datetime import datetime
from unittest.mock import patch

from nrw_events import common
from nrw_events.sources import SOURCES, SOURCE_IDS, uni_bonn
from tests.helpers import patch_window


CHOIR_URL = (
    "https://www.uni-bonn.de/de/veranstaltungen/"
    "sommerkonzert-internationaler-chor-1"
)
ICAL = f"""BEGIN:VCALENDAR
VERSION:2.0
X-WR-TIMEZONE:Europe/Berlin
BEGIN:VEVENT
SUMMARY:Internationaler Chor: Sommerkonzert
DTSTART;TZID=Europe/Berlin:20260720T200000
DTEND;TZID=Europe/Berlin:20260720T211500
CATEGORIES:International Office,Campus International,Internationaler Chor
DESCRIPTION:Herzliche Einladung zum Semesterabschlusskonzert des Internationalen Chores mit Liedern aus aller Welt. Eintritt frei!
URL:{CHOIR_URL}
END:VEVENT
BEGIN:VEVENT
SUMMARY:Corrupt historical event
DTSTART:20251202T181500
DTEND:29251202T194500
DESCRIPTION:Broken Plone end date
URL:https://www.uni-bonn.de/de/veranstaltungen/corrupt
END:VEVENT
END:VCALENDAR
"""
DETAIL_HTML = """
<div id="event-wrapper">
  <div class="content-item">
    <div class="item-title"><span>Ort</span></div>
    <div class="item-value"><span>Hörsaalzentrum Campus Poppelsdorf</span></div>
  </div>
  <div class="content-item">
    <div class="item-title"><span>Raum</span></div>
    <div class="item-value"><span>Hörsaal 1</span></div>
  </div>
  <div class="content-item">
    <div class="item-title"><span>Reservierung</span></div>
    <div class="item-value"><span>nicht erforderlich</span></div>
  </div>
</div>
"""


class UniBonnSourceTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 7, 19), datetime(2026, 8, 2))

    def test_official_calendar_enriches_choir_event_with_detail_venue(self):
        requested = []

        def fake_fetch(url, **_kwargs):
            requested.append(url)
            if url == uni_bonn._ICAL_URL:
                return ICAL
            if url == CHOIR_URL:
                return DETAIL_HTML
            raise AssertionError(f"unexpected URL {url}")

        with patch.dict("os.environ", {"NRW_EVENTS_DETAIL_CACHE_TTL_HOURS": "0"}), \
                patch.object(common, "fetch_url", side_effect=fake_fetch):
            events = uni_bonn.fetch()

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["title"], "Internationaler Chor: Sommerkonzert")
        self.assertEqual(event["date"], "2026-07-20")
        self.assertEqual(event["time"], "20:00–21:15")
        self.assertEqual(event["start_at"], "2026-07-20T20:00+02:00")
        self.assertEqual(event["end_at"], "2026-07-20T21:15+02:00")
        self.assertEqual(
            event["venue"],
            "Hörsaalzentrum Campus Poppelsdorf, Hörsaal 1",
        )
        self.assertEqual(event["city"], "Bonn")
        self.assertIn("Liedern aus aller Welt", event["description"])
        self.assertEqual(event["price"], "kostenlos")
        self.assertEqual(event["link"], CHOIR_URL)
        self.assertEqual(event["source"], "Universität Bonn")
        self.assertEqual(event["source_id"], "uni-bonn")
        self.assertEqual(event["category_key"], "concert")
        self.assertEqual(requested, [uni_bonn._ICAL_URL, CHOIR_URL])

    def test_detail_failure_keeps_complete_ical_record(self):
        with patch.object(common, "log_source_error"):
            events = uni_bonn._enrich_details(
                [{
                    "title": "Campus event",
                    "description": "Description from iCal",
                    "venue": "",
                    "link": "https://www.uni-bonn.de/de/veranstaltungen/campus-event",
                }],
                detail_fetcher=lambda _url: (_ for _ in ()).throw(TimeoutError("detail timeout")),
            )

        self.assertEqual(events[0]["description"], "Description from iCal")
        self.assertEqual(events[0]["venue"], "")

    def test_source_is_registered_with_stable_id(self):
        self.assertIs(SOURCES["Universität Bonn"], uni_bonn.fetch)
        self.assertEqual(SOURCE_IDS["Universität Bonn"], "uni-bonn")


if __name__ == "__main__":
    unittest.main()
