"""Empty first-party calendar shapes captured on 2026-10-15."""
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from nrw_events import common
from nrw_events.sources import (
    geide,
    grote_hiller,
    hofflohmaerkte,
    lengsdorfer_weinfest,
    marktcom,
    regional_common,
    regional_html,
)

from tests.helpers import patch_window

FIXTURES = Path(__file__).parent / "fixtures"


class OctoberSourceHealthTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 10, 15), datetime(2026, 11, 11))

    def test_geide_explicit_empty_keeps_identity_contract(self):
        for filename, url in [("geide-hennef-empty.html", "https://www.geide-maerkte.de/hennef-sieg.html"),
                              ("geide-siegburg-empty.html", "https://www.geide-maerkte.de/siegburg.html")]:
            html = (FIXTURES / filename).read_text()
            with self.subTest(url=url):
                self.assertEqual(geide._events_from_page(html, url, strict=True), [])
                for broken in [html.replace("Aktuell sind keine Termine vorhanden.", ""),
                               html.replace("2026", "unknown"),
                               html.replace("53773", "unknown").replace("53721", "unknown")]:
                    with self.assertRaises(regional_common.ParserEmptyError):
                        geide._events_from_page(broken, url, strict=True)

    def test_hoffloh_season_notice_is_not_a_blanket_empty(self):
        html = (FIXTURES / "hoffloh-koeln-season-end.html").read_text()
        for document, empty in [(html, False), ("<html>Hofflohmärkte Köln</html>", True),
                                (html.replace("2027", "2026"), True),
                                (html.replace("hier in Kürze", "irgendwann"), True)]:
            with self.subTest(document=document), patch.object(hofflohmaerkte, "_fetch_page", return_value=document), \
                 patch.object(common, "_record_endpoint") as record, patch.object(common, "log_source_error") as warning:
                self.assertEqual(hofflohmaerkte.fetch(), [])
                self.assertEqual(record.call_args.kwargs["parser_empty"], empty)
                self.assertEqual(warning.called, empty)

    def test_hoffloh_season_notice_survives_the_year_rollover(self):
        html = (FIXTURES / "hoffloh-koeln-season-end.html").read_text()
        for window_start, document, empty in [(datetime(2027, 1, 10), html, False),
                                              (datetime(2027, 1, 10), html.replace("2027", "2026"), True),
                                              (datetime(2027, 6, 10), html, True)]:
            patch_window(self, window_start, window_start.replace(day=28))
            with self.subTest(window_start=window_start, document=document[-80:]), \
                 patch.object(hofflohmaerkte, "_fetch_page", return_value=document), \
                 patch.object(common, "_record_endpoint") as record, patch.object(common, "log_source_error"):
                hofflohmaerkte.fetch()
                self.assertEqual(record.call_args.kwargs["parser_empty"], empty)

    def test_alfter_closed_empty_list_not_missing_or_broken_list(self):
        html = (FIXTURES / "alfter-empty-list.html").read_text()
        self.assertTrue(regional_html._alfter_calendar_is_expected_empty(html))
        for broken in ["<html>Veranstaltungen</html>", html.replace("</div>", ""),
                       html.replace("></div>", "><article>broken event</article></div>")]:
            self.assertFalse(regional_html._alfter_calendar_is_expected_empty(broken))

    def test_grote_empty_section_not_missing_or_malformed_cards(self):
        html = (FIXTURES / "grote-hiller-empty-section.html").read_text()
        for document, empty in [(html, False), ("<html></html>", True),
                                (html.replace('</section>', '<article>broken card</article></section>'), True),
                                (html.replace('</section>', '<div id="markt0" class="listing"></div></section>'), True)]:
            with self.subTest(document=document), patch.object(common, "fetch_url", return_value=document), \
                 patch.object(common, "_record_endpoint") as record:
                self.assertEqual(grote_hiller.fetch(), [])
                self.assertTrue(record.called)
                self.assertTrue(all(call.kwargs["parser_empty"] == empty for call in record.call_args_list))

    def test_lengsdorf_future_save_date_not_missing_current_programme(self):
        html = (FIXTURES / "lengsdorf-future-edition.html").read_text()
        self.assertTrue(lengsdorfer_weinfest._future_edition(html))
        for broken in ["<html></html>", html.replace("Save the Date", ""),
                       html.replace("15-17", "15-99"), html.replace("15-17", "15-16"),
                       html.replace("2028", "2026")]:
            self.assertFalse(lengsdorfer_weinfest._future_edition(broken))

    def test_marktcom_explicit_no_results_is_healthy(self):
        html = (FIXTURES / "marktcom-empty-search.html").read_text()
        self.assertTrue(marktcom._listing_contract_present(html))
        self.assertFalse(marktcom._listing_contract_present(html.replace("alert alert-danger", "changed")))
        self.assertFalse(marktcom._listing_contract_present("<html>no results</html>"))
