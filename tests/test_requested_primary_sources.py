import unittest
from datetime import datetime
from unittest.mock import patch

from nrw_events import detail_enrichment, report
from nrw_events.sources import in_guten_kreisen, lupe_events
from nrw_events.validation import canonicalize_event


class RequestedPrimarySourceTests(unittest.TestCase):
    def test_lupe_lessenich_is_reconciled_to_reviewed_calendar_occurrence(self):
        raw = [{
            "title": "Kirmes in Lessenich 2026",
            "start_date": "2026-08-14",
            "end_date": "2026-08-17",
            "end_at": "2026-08-18T00:00:00+02:00",
            "link": lupe_events._LESSENICH_URL,
            "source": "LuPe Events",
            "source_id": "lupe-events",
        }]
        with patch.object(lupe_events.common, "fetch_ical", return_value=raw):
            [event] = lupe_events.fetch()
        self.assertEqual(event["title"], "Laurentius-Kirmes")
        self.assertEqual(event["end_date"], "2026-08-16")
        self.assertEqual(event["venue"], "Dorfplatz Lessenich")

    def test_lupe_wins_laurentius_dedup_and_keeps_richer_civic_copy(self):
        base = {
            "title": "Laurentius-Kirmes", "start_date": "2026-08-14",
            "end_date": "2026-08-16", "venue": "Dorfplatz Lessenich",
            "city": "Bonn", "category": "Kirmes", "score": 8,
        }
        lupe = canonicalize_event({**base, "description": "", "source": "LuPe Events",
            "source_id": "lupe-events", "link": lupe_events._LESSENICH_URL})
        civic = canonicalize_event({**base, "description": "Ausführliche bestätigte Angaben des Ortsausschusses.",
            "source": "Bonn district festivals", "source_id": "bonn-district-festivals",
            "link": "https://www.bonn.de/pressemitteilungen/example"})
        [winner] = report.deduplicate([civic, lupe])
        self.assertEqual(winner.source_id, "lupe-events")
        self.assertEqual(winner.link, lupe_events._LESSENICH_URL)
        self.assertIn("Ortsausschusses", winner.description)

    def test_in_guten_kreisen_adds_official_friday_series_beside_ics(self):
        official = "<p>Tickets bekommt ihr für 10,- € zu unseren Öffnungszeiten.</p><p>Ihr könnt euer Ticket an einem beliebigen Freitagabend einsetzen.</p>"
        with patch.object(in_guten_kreisen.common, "fetch_ical", return_value=[]), \
             patch.object(in_guten_kreisen.common, "fetch_detail_url", return_value=official), \
             patch.object(in_guten_kreisen, "_fridays", return_value=[datetime(2026, 8, 14, 19)]):
            [event] = in_guten_kreisen.fetch()
        self.assertEqual(event["title"], "Wein ins Wochenende")
        self.assertEqual(event["source_id"], "in-guten-kreisen")
        self.assertEqual(event["link"], in_guten_kreisen._WEEKEND_URL)
        self.assertEqual(event["time"], "19:00–20:00")
        self.assertEqual(event["price"], "10 €")

    def test_in_guten_kreisen_keeps_ics_events_when_series_page_fails(self):
        ical_event = {"title": "Wine Basics", "source_id": "in-guten-kreisen"}
        with patch.object(in_guten_kreisen.common, "fetch_ical", return_value=[ical_event]), \
             patch.object(in_guten_kreisen.common, "fetch_detail_url", side_effect=TimeoutError("slow")), \
             patch.object(in_guten_kreisen.common, "log_source_error") as log_error:
            events = in_guten_kreisen.fetch()
        self.assertEqual(events, [ical_event])
        log_error.assert_called_once()

    def test_in_guten_kreisen_wins_over_bonn_jetzt_for_friday_series(self):
        base = {
            "title": "Wein ins Wochenende", "start_date": "2026-08-14",
            "end_date": "2026-08-14", "time": "19:00–20:00",
            "venue": "In guten Kreisen", "city": "Bonn",
            "category": "Weinprobe", "score": 8,
        }
        official = canonicalize_event({
            **base, "description": "Am Freitagabend werden um 19 Uhr drei Weine vorgestellt und anschließend verkostet.",
            "admission_basis": "explicit", "source": "In guten Kreisen",
            "source_id": "in-guten-kreisen", "link": in_guten_kreisen._WEEKEND_URL,
        })
        aggregator = canonicalize_event({
            **base, "description": "Aggregierter Veranstaltungshinweis.",
            "source": "Bonn.jetzt", "source_id": "bonn-jetzt",
            "link": "https://bonn.jetzt/event/wein-ins-wochenende-41",
        })
        [winner] = report.deduplicate([aggregator, official])
        self.assertEqual(winner.source_id, "in-guten-kreisen")
        self.assertEqual(winner.link, in_guten_kreisen._WEEKEND_URL)


    def test_bundeskunsthalle_intro_grid_is_complete_and_bounded(self):
        document = """
        <main id="main-content">
          <section class="section section--intro"><p class="page-header__date">1 May to 1 November 2026 // Admission free</p></section>
          <section class="section pt-0"><div class="grid">
            <div class="ce-wrap"><p>Erster ausführlicher Absatz zur Ausstellung und ihrem Konzept.</p></div>
            <div class="ce-wrap"><p>Zweiter Absatz mit weiteren Werken und Beteiligungsmöglichkeiten.</p></div>
          </div></section>
          <section class="section"><div class="ce-wrap"><p>Verwandte Veranstaltung, nicht übernehmen.</p></div></section>
        </main>
        """
        event = {"title": "Interactions 2026", "link": "https://www.bundeskunsthalle.de/en/interactions2026"}
        context = detail_enrichment.extract_detail_context(document, event)
        self.assertIn("Erster ausführlicher", context["description"])
        self.assertIn("Zweiter Absatz", context["description"])
        self.assertNotIn("Verwandte Veranstaltung", context["description"])
        self.assertEqual(context["price"], "kostenlos")


if __name__ == "__main__":
    unittest.main()
