import json
import unittest
from datetime import date, datetime
from unittest.mock import patch

from nrw_events.sources import bonn

from tests.helpers import patch_window

BASE = "https://www.bonn.de/veranstaltungskalender/veranstaltungen/hauptkalender/"
HOMEWORK = BASE + "internationale-begegnungsstaette/kinder-und-jugendliche/hausaufgabenhilfe-in-der-internationalen-begegnungsstaette.php"
IN_JSON = BASE + "extern/konzert.php"
SESSION = BASE + "stadtverwaltung/sitzung.php"


def rss(*items: tuple[str, str, str]) -> str:
    body = "".join(
        f"<item><title>{title}</title><link>{link}</link>"
        f"<description>Kurztext</description><pubDate>{pub}</pubDate></item>"
        for title, link, pub in items
    )
    return f'<?xml version="1.0"?><rss version="2.0"><channel>{body}</channel></rss>'


# Shape of bonn.de's iCal export (2026-10-14): folded CATEGORIES line.
HOMEWORK_ICAL = (
    "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:Hausaufgabenhilfe\r\n"
    "CATEGORIES:Bonn,Familien/Kinder,Integration/Migration/Interkultur,Internati\r\n"
    " onales,Aktion/Workshop,Jugendliche,Kinder (5 bis 12 Jahre)\r\n"
    "END:VEVENT\r\nEND:VCALENDAR\r\n"
)

# Real eventSchedule of the IBS homework help: weekly Tue-Thu, autumn break skipped.
HOMEWORK_DETAIL = """
<div class="SP-ArticleHeader__intro SP-Intro"><p>Die Hausaufgabenhilfe steht allen Bonner Schülerinnen und Schülern offen. Die Teilnahme ist kostenlos.</p></div>
<script type="application/ld+json">""" + json.dumps({
    "@type": "Event",
    "location": [{"@type": "Place", "name": "Internationale Begegnungsstätte", "address": {
        "@type": "PostalAddress", "postalCode": "53113", "addressLocality": "Bonn",
        "streetAddress": "Ollenhauerstraße 1"}}],
    "eventSchedule": [
        {"@type": "Schedule", "startDate": "2026-09-02", "endDate": "2026-10-15", "startTime": "15:00",
         "endTime": "17:00", "repeatFrequency": "P1W",
         "byDay": ["http://schema.org/Tuesday", "http://schema.org/Wednesday", "http://schema.org/Thursday"]},
        {"@type": "Schedule", "startDate": "2026-11-04", "endDate": "2026-12-10", "startTime": "15:00",
         "endTime": "17:00", "repeatFrequency": "P1W",
         "byDay": ["http://schema.org/Tuesday", "http://schema.org/Wednesday", "http://schema.org/Thursday"]},
    ],
}) + "</script>"


class BonnRssWindowTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 10, 14), datetime(2026, 11, 10))
        bonn._reset_detail_context_cache()
        self.addCleanup(bonn._reset_detail_context_cache)
        self.requested: list[str] = []

    def fake_fetch(self, url, *args, **kwargs):
        self.requested.append(url)
        if "sp%3Aout=rss" in url:
            if "sp%3Apage" in url:
                return rss()
            return rss(
                ("Konzert", IN_JSON, "Wed, 14 Oct 2026 20:00:00 +0200"),
                ("Hausaufgabenhilfe", HOMEWORK, "Wed, 14 Oct 2026 15:00:00 +0200"),
                ("Ausschusssitzung", SESSION, "Thu, 15 Oct 2026 17:00:00 +0200"),
            )
        if url == HOMEWORK + "?sp%3Aout=iCal":
            return HOMEWORK_ICAL
        if url == SESSION + "?sp%3Aout=iCal":
            return "BEGIN:VEVENT\r\nCATEGORIES:Sitzung,Stadtverwaltung\r\nEND:VEVENT\r\n"
        if url == HOMEWORK:
            return HOMEWORK_DETAIL
        raise AssertionError(f"unexpected URL {url}")

    def fetch(self):
        with patch("nrw_events.common.fetch_url", side_effect=self.fake_fetch), \
             patch("nrw_events.http.fetch_url", side_effect=self.fake_fetch), \
             patch.dict("os.environ", {"NRW_EVENTS_DETAIL_CACHE_TTL_HOURS": "0"}), \
             patch.object(bonn, "_venue_points", return_value={}):
            return bonn._fetch_rss_window_events("Bonn.de Events", {IN_JSON})

    def test_records_missing_from_json_expand_their_weekly_schedule(self):
        events = self.fetch()

        self.assertEqual(
            [event["date"] for event in events],
            ["2026-10-14", "2026-10-15", "2026-11-04", "2026-11-05", "2026-11-10"],
        )
        first = events[0]
        self.assertEqual(first["title"], "Hausaufgabenhilfe")
        self.assertEqual(first["venue"], "Internationale Begegnungsstätte")
        self.assertEqual(first["venue_address"], "Ollenhauerstraße 1, 53113 Bonn")
        self.assertEqual(first["time"], "15:00–17:00")
        self.assertEqual(first["price"], "kostenlos")
        self.assertEqual(first["link"], HOMEWORK)
        # JSON-known record and blocked topic never cost a detail request.
        self.assertNotIn(IN_JSON + "?sp%3Aout=iCal", self.requested)
        self.assertNotIn(SESSION, self.requested)

    def test_stale_schedule_keeps_only_the_listed_occurrence(self):
        stale = HOMEWORK_DETAIL.replace("2026-10-15", "2026-10-13")
        original = self.fake_fetch

        def fetch(url, *args, **kwargs):
            return stale if url == HOMEWORK else original(url, *args, **kwargs)

        self.fake_fetch = fetch
        events = self.fetch()

        self.assertEqual([(event["date"], event["time"]) for event in events], [("2026-10-14", "15:00–17:00")])

    def test_schedule_expansion_skips_unsupported_repeats(self):
        weekly = {"startDate": "2026-10-01", "endDate": "2026-10-31", "repeatFrequency": "P1W",
                  "byDay": "https://schema.org/Saturday", "startTime": "10:00:00", "exceptDate": ["2026-10-24"]}
        self.assertEqual(
            bonn._schedule_occurrences([weekly], date(2026, 10, 14), date(2026, 11, 10)),
            [(date(2026, 10, 17), "10:00", ""), (date(2026, 10, 31), "10:00", "")],
        )
        monthly = {"startDate": "2026-10-01", "endDate": "2026-12-31", "repeatFrequency": "P1M"}
        self.assertEqual(bonn._schedule_occurrences([monthly], date(2026, 10, 14), date(2026, 11, 10)), [])


if __name__ == "__main__":
    unittest.main()
