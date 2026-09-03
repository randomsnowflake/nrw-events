import unittest
from datetime import datetime
from unittest.mock import patch

from nrw_events import common, detail_enrichment
from nrw_events.sources import kihapp

from tests.helpers import patch_window

LISTING_PAGE_1 = """
<table>
<tr data-date='1786406400' data-upcoming>
  <td><a href="/tournaments/23960-6th-gsba-world-championships">6th GSBA World Championships</a>
  <div class='location'>Sportpark Nord Bonn, Germany</div>
  <span class='dates'>Aug 11 to 16, 2026</span></td>
</tr>
<tr data-date='1789171200' data-upcoming>
  <td><a href="/tournaments/22972-wfmc-outcast-fighting-idm-2026">WFMC &amp; Outcast Fighting: IDM 2026</a>
  <div class='location'>Wettkampfhalle Meckenheim, Germany</div>
  <span class='dates'>September 12, 2026</span></td>
</tr>
</table>
<a rel="next" data-remote="true" href="/tournaments?country=Germany&amp;page=2">Next page</a>
"""

LISTING_PAGE_2 = r'''
  toAppend = $("<tr data-date='1795824000' data-upcoming>\n<td>\n<a href=\"/tournaments/26804-wka-open-world-championships-50th-anniversary\">WKA Open World Championships<\/a>\n<div class='location'>Glaspalast Sindelfingen, Germany<\/div>\n<span class='dates'>November 28 to 29, 2026<\/span>\n<\/td>\n<\/tr>");
  $("#pagination").html("");
'''

GSBA_DETAIL = """
<html><head>
<meta content='Aug 11 to 16, 2026. Traditional Forms, Live Stick and Padded Stick. Powered by Kihapp.' name='description'>
</head><body>
<h1><span itemprop='name'>6th GSBA World&nbsp;Championships</span></h1>
<div class='date-and-location-container'>
  <span class='dates'>Aug 11 to 16, 2026</span>
  <p class='location'><a href='#venue'>Sportpark Nord Bonn</a></p>
</div>
<div class='map-container' data-latitude='50.7469675' data-longitude='7.0846435'></div>
</body></html>
"""

MECKENHEIM_DETAIL = """
<html><head>
<meta name='description' content='September 12, 2026. Pointfighting and Kick Light. Powered by Kihapp.'>
</head><body>
<h1><span itemprop='name'>WFMC &amp; Outcast Fighting: IDM 2026</span></h1>
<span class='dates'>September 12, 2026</span>
<p class='location'><a href='#venue'>Wettkampfhalle Meckenheim</a></p>
<div class='map-container' data-latitude='50.6259' data-longitude='7.0188'></div>
</body></html>
"""

SINDELFINGEN_DETAIL = """
<html><body>
<h1><span itemprop='name'>WKA Open World Championships</span></h1>
<span class='dates'>November 28 to 29, 2026</span>
<p class='location'><a href='#venue'>Glaspalast Sindelfingen</a></p>
<div class='map-container' data-latitude='48.7045' data-longitude='9.0147'></div>
</body></html>
"""


class KihappParserTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 8, 11), datetime(2026, 12, 1))

    def test_fetch_paginates_xhr_listing_and_keeps_only_in_radius_tournaments(self):
        calls = []

        def fake_fetch(url, **kwargs):
            calls.append((url, kwargs))
            if url == kihapp.URL:
                return LISTING_PAGE_1
            if url.endswith("page=2"):
                self.assertEqual(kwargs["headers"]["X-Requested-With"], "XMLHttpRequest")
                return LISTING_PAGE_2
            if "23960-" in url:
                return GSBA_DETAIL
            if "22972-" in url:
                return MECKENHEIM_DETAIL
            if "26804-" in url:
                return SINDELFINGEN_DETAIL
            self.fail(f"unexpected URL: {url}")

        with patch.object(common, "fetch_url", side_effect=fake_fetch):
            events = kihapp.fetch()

        self.assertEqual([event["title"] for event in events], [
            "6th GSBA World Championships",
            "WFMC & Outcast Fighting: IDM 2026",
        ])
        self.assertTrue(any(url.endswith("page=2") for url, _ in calls))
        self.assertFalse(any(url.endswith("page=3") for url, _ in calls))
        self.assertTrue(any("26804-" in url for url, _ in calls))

    def test_gsba_occurrence_uses_visible_multi_day_range_and_primary_provenance(self):
        [candidate, *_] = kihapp._listing_candidates(LISTING_PAGE_1)

        event = kihapp._event_from_detail(candidate, GSBA_DETAIL)

        self.assertIsNotNone(event)
        self.assertEqual(event["title"], "6th GSBA World Championships")
        self.assertEqual(event["start_date"], "2026-08-11")
        self.assertEqual(event["end_date"], "2026-08-16")
        self.assertEqual(event["city"], "Bonn")
        self.assertEqual(event["venue"], "Sportpark Nord")
        self.assertEqual(event["venue_address"], "Kölnstraße 250, 53117 Bonn")
        self.assertEqual(event["source"], kihapp.SOURCE)
        self.assertEqual(event["source_id"], "kihapp")
        self.assertEqual(event["source_role"], "primary")
        self.assertEqual(event["link"], "https://www.kihapp.com/tournaments/23960-6th-gsba-world-championships")
        self.assertEqual(event["category_key"], "sports")
        self.assertNotIn("Powered by Kihapp", event["description"])
        self.assertNotIn("Aug 11", event["description"])

    def test_shared_detail_pass_does_not_refetch_adapter_owned_kihapp_pages(self):
        [candidate, *_] = kihapp._listing_candidates(LISTING_PAGE_1)
        event = kihapp._event_from_detail(candidate, GSBA_DETAIL)

        with patch.object(detail_enrichment.common, "fetch_detail_url") as fetch_detail:
            enriched = detail_enrichment.enrich_events([event])

        self.assertEqual(enriched, [event])
        fetch_detail.assert_not_called()

    def test_detail_failure_keeps_local_listing_event(self):
        listing = LISTING_PAGE_1.split("</tr>", 1)[0] + "</tr>"

        with patch.object(common, "log_source_error") as log_error:
            events = kihapp.fetch(
                listing_fetcher=lambda _url, **_kwargs: listing,
                detail_fetcher=lambda _url, **_kwargs: (_ for _ in ()).throw(TimeoutError("timed out")),
            )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["title"], "6th GSBA World Championships")
        self.assertEqual(events[0]["start_date"], "2026-08-11")
        self.assertEqual(events[0]["end_date"], "2026-08-16")
        self.assertEqual(events[0]["city"], "Bonn")
        self.assertIn("Kihapp-Turnierkalender", events[0]["description"])
        log_error.assert_called_once()

    def test_reviewed_occurrence_survives_move_from_upcoming_to_past(self):
        unrelated = """
        <tr data-date='1789171200' data-upcoming>
          <td><a href='/tournaments/22972-idm-2026'>IDM 2026</a></td>
          <td><span class='dates'>Sep 12, 2026</span><div class='location'>Meckenheim</div></td>
        </tr>
        """
        detail_calls = []

        def detail_fetch(url, **_kwargs):
            detail_calls.append(url)
            return GSBA_DETAIL

        with (
            patch.object(common, "TODAY", datetime(2026, 8, 12)),
            patch.object(common, "END_DATE", datetime(2026, 8, 13)),
        ):
            events = kihapp.fetch(
                listing_fetcher=lambda _url, **_kwargs: unrelated,
                detail_fetcher=detail_fetch,
            )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["start_date"], "2026-08-11")
        self.assertEqual(events[0]["end_date"], "2026-08-16")
        self.assertEqual(detail_calls, [kihapp._REVIEWED_OCCURRENCES[0]["link"]])

    def test_date_parser_handles_compact_dashes_and_new_year_ranges(self):
        start, end = kihapp._date_range("Aug 11–16, 2026")
        self.assertEqual((start, end), (datetime(2026, 8, 11), datetime(2026, 8, 16)))

        start, end = kihapp._date_range("Dec 31 to Jan 1, 2027")
        self.assertEqual((start, end), (datetime(2026, 12, 31), datetime(2027, 1, 1)))

        start, end = kihapp._date_range("Dec 31, 2026 to Jan 1, 2027")
        self.assertEqual((start, end), (datetime(2026, 12, 31), datetime(2027, 1, 1)))

    def test_non_upcoming_pagination_boundary_is_not_parser_failure(self):
        page = "<tr><td><a href='/tournaments/1-old'>Old tournament</a></td></tr>"

        with (
            patch.object(common, "TODAY", datetime(2027, 1, 1)),
            patch.object(common, "END_DATE", datetime(2027, 1, 2)),
            patch.object(common, "log_source_error") as log_error,
        ):
            events = kihapp.fetch(listing_fetcher=lambda _url, **_kwargs: page)

        self.assertEqual(events, [])
        log_error.assert_not_called()

    def test_listing_parser_rejects_changed_rows_without_required_fields(self):
        html = "<tr data-upcoming><td><div class='location'>Bonn</div></td></tr>"

        self.assertEqual(kihapp._listing_candidates(html), [])

    def test_malformed_upcoming_tournament_row_is_a_parser_failure(self):
        html = "<tr data-upcoming><td><a href='/tournaments/1-changed'>Changed</a></td></tr>"

        with (
            patch.object(common, "TODAY", datetime(2027, 1, 1)),
            patch.object(common, "END_DATE", datetime(2027, 1, 2)),
            patch.object(common, "log_source_error") as log_error,
        ):
            events = kihapp.fetch(listing_fetcher=lambda _url, **_kwargs: html)

        self.assertEqual(events, [])
        log_error.assert_called_once()


if __name__ == "__main__":
    unittest.main()
