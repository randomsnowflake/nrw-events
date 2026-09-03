"""Workshop query URLs must not turn registration terms into event facts."""

import json
import unittest
from unittest.mock import patch

from nrw_events import components, detail_enrichment, quality


class BrotfabrikDetailIdentityTests(unittest.TestCase):
    def event(self):
        date = detail_enrichment.common.TODAY.strftime("%Y-%m-%d")
        return {
            "title": "Friday Friday Chor", "date": date, "start_date": date, "end_date": date,
            "time": "19:45", "status": "scheduled", "city": "Bonn", "venue": "Kulturzentrum Brotfabrik",
            "description": "Ein gemeinsamer Chorabend mit Liedern aus Pop und Jazz.",
            "description_html": "<p>Ein gemeinsamer Chorabend mit Liedern aus Pop und Jazz.</p>",
            "description_source": "scraped", "source": "Brotfabrik Bonn", "source_id": "brotfabrik-bonn",
            "link": "https://bildungswerk-brotfabrik.de/workshops/?IDT=4478",
        }

    def overview(self, structured=""):
        return (
            '<main><h1>WORKSHOPS</h1><p>Informationen zur Anmeldung und zu den Teilnahmebedingungen.</p>'
            '<p>Das Bildungswerk benachrichtigt alle Teilnehmer, wenn der Kurs ausfällt oder belegt ist.</p>'
            '<p>Hier stehen die allgemeinen Bedingungen für viele verschiedene Veranstaltungen.</p>'
            '<p>Eintritt: 99 Euro</p></main>' + structured
        )

    def test_overview_preserves_authoritative_event_in_serial_and_parallel_modes(self):
        event = self.event()
        self.assertEqual(quality.evaluate_event_quality(event).action.value, "keep")
        for workers in (1, 3):
            with self.subTest(workers=workers), components.pool_scope(workers), patch.object(
                detail_enrichment.common, "fetch_detail_url", return_value=self.overview(),
            ), patch.object(detail_enrichment, "_needs_detail", return_value=True):
                result = detail_enrichment.enrich_events([event], parallel_components=True)
            self.assertEqual(result, [event])
            self.assertEqual(quality.evaluate_event_quality(result[0]).action.value, "keep")

    def test_only_exact_title_and_occurrence_can_supply_structured_copy(self):
        event = self.event()
        for title, date, matches in (
            (event["title"], event["date"], True),
            ("Ein anderer Chor", event["date"], False),
            (event["title"], "1999-01-01", False),
        ):
            description = "Der Chor singt zusammen bekannte Lieder. Dieses Konzert wurde abgesagt."
            structured = '<script type="application/ld+json">' + json.dumps({
                "@type": "Event", "name": title, "startDate": date, "description": description,
            }) + '</script>'
            with self.subTest(title=title, date=date):
                context = detail_enrichment.extract_detail_context(self.overview(structured), event)
                self.assertEqual(context["description"], description if matches else "")
                self.assertEqual(context.get("price", ""), "")
                if matches:
                    changed = {**event, "description": context["description"]}
                    self.assertEqual(quality.evaluate_event_quality(changed).action.value, "drop")

    def test_unrelated_host_keeps_its_existing_detail_extraction(self):
        event = {**self.event(), "link": "https://example.org/workshops/?IDT=4478"}
        context = detail_enrichment.extract_detail_context(
            '<main><div itemprop="description"><p>Ein ausführlicher und eindeutig beschriebener Konzertabend '
            'mit mehreren Musikern, gemeinsamem Gesang und einem abwechslungsreichen Programm.</p></div></main>', event,
        )
        self.assertIn("Konzertabend", context["description"])
