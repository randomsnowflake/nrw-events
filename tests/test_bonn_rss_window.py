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

    def test_source_address_beats_umbrella_venue_name(self):
        # bonn.de romantasy.php (2026-10-22): "Stadtbibliothek Bonn" names every branch; the address names the building.
        detail = HOMEWORK_DETAIL.replace(json.dumps("Internationale Begegnungsstätte"), json.dumps("Stadtbibliothek Bonn")).replace(
            json.dumps("Ollenhauerstraße 1"), json.dumps("Mülheimer Platz 1")).replace('"53113"', '"53111"')
        original = self.fake_fetch
        self.fake_fetch = lambda url, *args, **kwargs: detail if url == HOMEWORK else original(url, *args, **kwargs)

        first = self.fetch()[0]

        self.assertEqual(first["venue_address"], "Mülheimer Platz 1, 53111 Bonn")
        self.assertNotEqual(first["location_source"], "venue_registry")
        self.assertNotEqual(first["venue_latitude"], 50.7567024)  # Stadtteilbibliothek Auerberg


class BonnStageScheduleTests(unittest.TestCase):
    LINK = BASE + "extern/GOP-Variet-Theater-Bonn-On-Air.php"
    TITLE = "GOP Varieté-Theater Bonn: On Air"

    def setUp(self):
        patch_window(self, datetime(2026, 11, 13), datetime(2026, 12, 10))

    def row(self, day="2026-11-13", clock="17:30", **extra):
        return {
            "title": self.TITLE, "link": self.LINK, "source": "Bonn.de Events",
            "source_id": "bonn-de-events", "date": day, "start_date": day, "end_date": day,
            "venue": "GOP-Varieté-Theater Bonn", "city": "Bonn",
            "venue_address": "Karl-Carstens-Straße 1, 53113 Bonn", "category_key": "stage", "score": 1.0,
            "time": clock, "start_at": f"{day}T{clock}:00+01:00", "end_at": "",
            "description": "Ein internationales Ensemble zeigt Artistik und Comedy.", **extra,
        }

    def context(self, notice):
        # Visitor copy on the Bonn.de GOP detail page, read 13 November 2026.
        html = '<div data-sp-table class="SP-Paragraph"><p>' + notice + '</p></div>'
        return bonn._parse_detail_context(html)

    def enrich(self, rows, notice):
        with patch.object(bonn, "_fetch_detail_context", return_value=self.context(notice)):
            return bonn._enrich_listing_details(rows)

    def test_merging_keeps_distinct_clocks_and_enriches_the_same_clock(self):
        first = self.row()
        second = self.row(clock="20:30")
        richer = self.row(time="17:30–19:30", end_at="2026-11-13T19:30:00+01:00")
        result = bonn._merge_fallback_events([first], [second, richer])
        self.assertEqual([r["time"] for r in result], ["17:30–19:30", "20:30"])
        self.assertEqual(result[0]["end_at"], richer["end_at"])
        self.assertEqual(len(bonn._merge_fallback_events(result, [second])), 2)

    def test_single_unknown_clock_copy_can_be_enriched(self):
        unknown = self.row(time="", start_at="")
        result = bonn._merge_fallback_events([unknown], [self.row(), self.row(clock="20:30")])
        self.assertEqual([r["time"] for r in result], ["17:30", "20:30"])
        self.assertEqual(len(bonn._merge_fallback_events([self.row()], [unknown])), 1)

    def test_explicit_closed_date_overrides_a_complete_listing(self):
        rows = [self.row("2026-11-14"), self.row("2026-11-14", "20:30"), self.row()]
        result = self.enrich(rows, "Abweichende Termine: 14.11.2026: Keine Shows")
        self.assertEqual([r.get("status", "scheduled") for r in result], ["cancelled", "cancelled", "scheduled"])
        self.assertEqual(result[0]["link"], self.LINK)
        self.assertEqual(result[-1]["time"], "17:30")

    def test_only_one_show_replaces_all_standard_clocks_without_inventing_an_end(self):
        row = self.row("2026-11-15", "14:00", time="14:00–16:00", end_at="2026-11-15T16:00:00+01:00")
        result = self.enrich([row, self.row("2026-11-15", "20:30")],
                             "Abweichende Termine: 15.11.2026 Volkstrauertag: Nur eine Show um 18:00 Uhr")
        self.assertEqual(len(result), 1)
        self.assertEqual((result[0]["time"], result[0]["start_at"], result[0]["end_at"]),
                         ("18:00", "2026-11-15T18:00+01:00", ""))
        self.assertEqual(result[0]["end_date"], "2026-11-15")

    def test_multiple_explicit_clocks_and_adjacent_dates_stay_separate(self):
        result = self.enrich([self.row("2026-11-15", "14:00"), self.row("2026-11-14")],
                             "Abweichende Termine: 15.11.2026: Showbeginn 16:00 und 19:00 Uhr; "
                             "14.11.2026: Keine Shows")
        self.assertEqual([(r["date"], r["time"], r.get("status", "scheduled")) for r in result],
                         [("2026-11-15", "16:00", "scheduled"), ("2026-11-15", "19:00", "scheduled"),
                          ("2026-11-14", "17:30", "cancelled")])

    def test_unlabelled_unsupported_and_invalid_notices_do_not_change_rows(self):
        for notice in ["14.11.2026: Keine Shows", "Abweichende Termine: 14.11.2025: Keine Shows",
                       "Abweichende Termine: 31.11.2026: Keine Shows",
                       "Abweichende Termine: 14.11.2026: Showbeginn 99:00 Uhr",
                       "Abweichende Termine: 14.11.2026: Abendkasse geschlossen"]:
            with self.subTest(notice=notice):
                row = self.row("2026-11-14")
                result = self.enrich([row], notice)
                self.assertEqual((result[0]["time"], result[0].get("status", "scheduled")), ("17:30", "scheduled"))

    def test_midnight_is_an_explicit_start_and_unknown_clocks_remain_ambiguous(self):
        result = self.enrich([self.row("2026-11-15", "14:00")],
                             "Abweichende Termine: 15.11.2026: Showbeginn 00:00 Uhr")
        self.assertEqual((result[0]["time"], result[0]["start_at"]), ("00:00", "2026-11-15T00:00+01:00"))
        rows = [self.row(), self.row(clock="20:30")]
        result = bonn._merge_fallback_events(rows, [self.row(time="", start_at="")])
        self.assertEqual([r["time"] for r in result], ["17:30", "20:30", ""])

    def test_corrected_sessions_keep_the_published_run_identity(self):
        from nrw_events.exhibition_runs import merge_exhibition_opening_days
        from nrw_events.identity import assign_event_ids
        from nrw_events.identity_reconciliation import _reconcile_published_ids
        from nrw_events.validation import validate_event

        rows = self.enrich([self.row(), self.row(clock="20:30"), self.row("2026-11-14"),
                            self.row("2026-11-15", "14:00"), self.row("2026-11-17", "19:00")],
                           "Abweichende Termine: 14.11.2026: Keine Shows; "
                           "15.11.2026: Nur eine Show um 18:00 Uhr")
        old = validate_event({**self.row("2026-10-20"), "end_date": "2026-12-10"}).to_dict()
        old["event_id"] = "gop-variete-theater-bonn-on-air-45b4f38a10"
        previous = {"events": [old]}
        runs = merge_exhibition_opening_days([validate_event(r) for r in rows], previous)
        runs = _reconcile_published_ids(runs, previous)
        [run] = assign_event_ids(runs)
        self.assertEqual(run["event_id"], old["event_id"])
        self.assertEqual([(s["date"], s["start_at"][11:16]) for s in run["daily_schedule"]],
                         [("2026-11-13", "17:30"), ("2026-11-13", "20:30"),
                          ("2026-11-15", "18:00"), ("2026-11-17", "19:00")])

    def test_optional_html_listing_keeps_both_same_day_shows(self):
        dates = "".join('<span><span class="SP-Scheduling__date">13.11.2026</span>'
                        f'<span class="SP-Scheduling__time">{clock} Uhr</span></span>'
                        for clock in ["17:30", "20:30"])
        html = (f'<article class="SP-Teaser"><a class="SP-Teaser__inner" href="{self.LINK}">'
                '<span class="SP-Kicker__text">Theater</span>'
                f'<div class="SP-Scheduling">{dates}</div><h1 class="SP-Teaser__headline">{self.TITLE}</h1>'
                '</a></article>')
        rows = bonn._listing_events_from_html(html, "Bonn.de Events", fetch_details=False)
        self.assertEqual([r["time"] for r in rows], ["17:30", "20:30"])

    def test_an_existing_cancellation_is_not_reopened_by_a_clock_notice(self):
        result = self.enrich([self.row("2026-11-15", "14:00", status="cancelled")],
                             "Abweichende Termine: 15.11.2026: Nur eine Show um 18:00 Uhr")
        self.assertEqual(result[0]["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
