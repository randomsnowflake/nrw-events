"""Contract tests for the municipal Modern Events Calendar market source.

The REST payload carries no event date, so the date must come from the per-event
calendar. Those reads must go through the TTL cache, because there is one per event.
"""

import json
import unittest
from datetime import datetime
from unittest import mock

from nrw_events import common
from nrw_events.health import SourceResult, SourceStatus
from nrw_events.sources import SOURCES, mec_municipal


def _rest_item(post_id, title, content="Irgendein Text."):
    return {
        "id": post_id,
        "title": {"rendered": title},
        "content": {"rendered": f"<p>{content}</p>"},
        "link": f"https://www.hennef.de/veranstaltungen/{post_id}/",
        "mec_category": [74],
    }


def _calendar(
    summary,
    start="20260906T080000Z",
    end="20260906T140000Z",
    location="Hennef-Lichtenberg",
    categories="Ausstellungen und Märkte,Bürgervereine und Dörfer",
):
    return "\r\n".join(
        [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//WordPress - MECv6.9.0//EN",
            "BEGIN:VEVENT",
            f"UID:MEC-{summary}@hennef.de",
            f"DTSTART:{start}",
            f"DTEND:{end}",
            f"SUMMARY:{summary}",
            f"CATEGORIES:{categories}",
            f"LOCATION:{location}",
            "END:VEVENT",
            "END:VCALENDAR",
        ]
    )


class MecCandidateFilterTests(unittest.TestCase):
    def test_source_is_registered(self):
        self.assertIs(SOURCES["Municipal MEC markets"], mec_municipal.fetch)

    def test_second_hand_formats_are_candidates(self):
        items = [
            _rest_item(1, "Garagenflohmarkt in Hennef-Lichtenberg"),
            _rest_item(2, "5. Dorfflohmarkt in Söven"),
            _rest_item(3, "15. Gassenflohmarkt in Weldergoven"),
            _rest_item(4, "Mädelsflohmarkt"),
            _rest_item(5, "Kindersachen-Kram-Basar"),
            _rest_item(6, "Antik- und Trödelmarkt"),
        ]

        self.assertEqual(len(mec_municipal.market_candidates(items)), 6)

    def test_produce_and_non_market_boersen_are_excluded(self):
        """ "Börse" alone is too broad — these are not second-hand markets."""
        items = [
            _rest_item(10, "Pflanzentauschbörse im Schaugarten"),
            _rest_item(11, "Samenbörse"),
            _rest_item(12, "Wochenmarkt auf dem Marktplatz"),
            _rest_item(13, "Bauernmarkt"),
            _rest_item(14, "Digitale Helferbörse"),
            _rest_item(15, "Berufsstarterbörse"),
            _rest_item(16, "Zen-Meditation im Zen Haus"),
        ]

        self.assertEqual(mec_municipal.market_candidates(items), [])

    def test_malformed_entries_are_skipped(self):
        items = [
            {"id": "not-an-int", "title": {"rendered": "Flohmarkt"}},
            {"title": {"rendered": "Flohmarkt ohne id"}},
            {"id": 20, "title": {"rendered": ""}},
            "not a dict",
        ]

        self.assertEqual(mec_municipal.market_candidates(items), [])


class MecCalendarFetchTests(unittest.TestCase):
    def test_calendar_reads_go_through_the_ttl_cache(self):
        with mock.patch.object(common, "fetch_detail_url", return_value="ok") as cached:
            mec_municipal._cached_calendar_fetcher("https://www.hennef.de/?method=ical&id=1", timeout=99, accept="x")

        self.assertEqual(cached.call_args.kwargs["cache_namespace"], "mec-municipal")
        self.assertEqual(cached.call_args.kwargs["timeout"], 20)

    def test_fetch_ical_uses_the_injected_fetcher(self):
        payload = _calendar("Garagenflohmarkt in Hennef-Lichtenberg")
        calls = []

        def fetcher(url, **kwargs):
            calls.append(url)
            return payload

        with (
            mock.patch.object(common, "TODAY", datetime(2026, 9, 1)),
            mock.patch.object(common, "END_DATE", datetime(2026, 9, 30)),
            mock.patch.object(common, "fetch_url") as plain,
        ):
            events = common.fetch_ical("https://example.test/e.ics", "Test", "Hennef", fetcher=fetcher)

        plain.assert_not_called()
        self.assertEqual(calls, ["https://example.test/e.ics"])
        self.assertEqual(events[0]["start_date"], "2026-09-06")


class MecSiteIntegrationTests(unittest.TestCase):
    def test_only_market_candidates_trigger_a_calendar_request(self):
        site = mec_municipal.MecSite(
            "Hennef Märkte",
            "hennef-maerkte",
            "https://www.hennef.de",
            "Hennef",
            (74,),
        )
        listing = json.dumps(
            [
                _rest_item(19512, "Garagenflohmarkt in Hennef-Lichtenberg"),
                _rest_item(17802, "Pflanzentauschbörse im Schaugarten"),
                _rest_item(999, "Zen-Meditation im Zen Haus"),
            ]
        )
        requested = []

        def fake_detail(url, **kwargs):
            requested.append(url)
            return _calendar("Garagenflohmarkt in Hennef-Lichtenberg")

        with (
            mock.patch.object(common, "TODAY", datetime(2026, 9, 1)),
            mock.patch.object(common, "END_DATE", datetime(2026, 9, 30)),
            mock.patch.object(common, "fetch_url", return_value=listing),
            mock.patch.object(common, "fetch_detail_url", side_effect=fake_detail),
        ):
            events = mec_municipal.events_for_site(site)

        self.assertEqual(requested, ["https://www.hennef.de/?method=ical&id=19512"])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["start_date"], "2026-09-06")
        self.assertEqual(events[0]["city"], "Hennef")
        self.assertEqual(events[0]["source"], "Hennef Märkte")
        self.assertEqual(events[0]["source_id"], "hennef-maerkte")

    def test_listing_failure_does_not_break_the_run(self):
        site = mec_municipal.MecSite(
            "Broken",
            "broken-site",
            "https://broken.test",
            "Hennef",
            (1,),
        )
        result = SourceResult("Municipal MEC markets")

        with (
            mock.patch.object(common, "fetch_url", side_effect=OSError("boom")),
            mock.patch.object(common, "_SOURCE_CONTEXT") as context,
        ):
            context.result = result
            self.assertEqual(mec_municipal.events_for_site(site), [])

        self.assertEqual(result.warnings[0]["source_id"], "broken-site")

    def test_filtered_calendar_record_is_healthy_not_parser_empty(self):
        site = mec_municipal.MecSite(
            "Hennef Märkte",
            "hennef-maerkte",
            "https://www.hennef.de",
            "Hennef",
            (74,),
        )
        listing = json.dumps(
            [
                _rest_item(19512, "Garagenflohmarkt in Hennef-Lichtenberg"),
            ]
        )
        result = SourceResult("Municipal MEC markets")

        with (
            mock.patch.object(common, "TODAY", datetime(2026, 7, 26)),
            mock.patch.object(common, "END_DATE", datetime(2026, 7, 28)),
            mock.patch.object(common, "fetch_url", return_value=listing),
            mock.patch.object(
                common,
                "fetch_detail_url",
                return_value=_calendar(
                    "Wochenmarkt auf dem Marktplatz",
                    start="20260727T080000Z",
                    end="20260727T140000Z",
                ),
            ),
            mock.patch.object(common, "_SOURCE_CONTEXT") as context,
        ):
            context.result = result
            events = mec_municipal.events_for_site(site)

        result.finish(events)
        self.assertEqual(events, [])
        self.assertEqual(result.status, SourceStatus.HEALTHY_EMPTY)
        self.assertTrue(result.endpoints)
        self.assertFalse(any(endpoint.get("parser_empty") is True for endpoint in result.endpoints.values()))

    def test_listing_url_and_ical_url_shapes(self):
        site = mec_municipal.SITES[0]

        self.assertEqual(
            mec_municipal.ical_url(site, 19512),
            "https://www.hennef.de/?method=ical&id=19512",
        )
        self.assertIn("mec_category=74", mec_municipal._listing_url(site, 74, 1))
        self.assertIn("per_page=100", mec_municipal._listing_url(site, 74, 1))


if __name__ == "__main__":
    unittest.main()
