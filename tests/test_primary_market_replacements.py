import unittest
from datetime import datetime
from unittest import mock

from nrw_events import config, runner
from nrw_events.market_source_fallbacks import partition_directory_fallbacks
from nrw_events.observability import configure_logging
from nrw_events.runtime import EventWindow, RunContext
from nrw_events.sources import (
    SOURCES,
    regional_common,
    rieder_markets,
    rossel_wilberhofen,
    schmitt_markets,
)
from nrw_events.validation import validate_event
from tests.helpers import patch_window


class PrimaryMarketReplacementTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 8, 16), datetime(2026, 9, 13))

    def test_sources_are_registered(self):
        self.assertIs(
            SOURCES["Bürgerverein Rossel-Wilberhofen"],
            rossel_wilberhofen.fetch,
        )
        self.assertIs(SOURCES["Schmitt Veranstaltungen"], schmitt_markets.fetch)
        self.assertIs(SOURCES["Rieder Märkte"], rieder_markets.fetch)

    def test_rossel_wilberhofen_requires_news_and_calendar_corroboration(self):
        news = """
        <meta property="og:site_name" content="Bürgerverein Rossel-Wilberhofen">
        <h2>Traditionelles Rochusfest am 15. &amp; 16.08.</h2>
        <p>Am Sonntag, den 16. August findet zudem in der Zeit zwischen
        10.00-18.00 Uhr wieder ein Dorf-Flohmarkt im gesamten Ort statt.</p>
        """
        calendar = """
        <h2>Termine und Veranstaltungen</h2><h3>2026</h3>
        <tr><td>16.08.</td><td>ab 09:30</td><td>Rund um die Rochuskapelle
        in Wilberhofen</td><td>Traditionelles Rochusfest</td></tr>
        """

        [event] = rossel_wilberhofen._events_from_pages(news, calendar, strict=True)

        self.assertEqual(event["date"], "2026-08-16")
        self.assertEqual(event["time"], "10:00–18:00")
        self.assertEqual(event["city"], "Windeck")
        self.assertEqual(event["venue"], "Wilberhofen")
        self.assertEqual(event["source_id"], "rossel-wilberhofen-dorfflohmarkt")
        self.assertEqual(event["organizer"], "Bürgerverein Rossel-Wilberhofen")

        with self.assertRaises(regional_common.ParserEmptyError):
            rossel_wilberhofen._events_from_pages(news, calendar.replace("2026", ""), strict=True)

    def test_rossel_wilberhofen_derives_the_day_from_the_calendar(self):
        news = """
        <meta property="og:site_name" content="Bürgerverein Rossel-Wilberhofen">
        <p>Am Sonntag, den 17. August findet zudem in der Zeit zwischen
        10.00-18.00 Uhr wieder ein Dorf-Flohmarkt im gesamten Ort statt.</p>
        """
        calendar = """
        <h2>Termine und Veranstaltungen</h2><h3>2026</h3>
        <tr><td>17.08.</td><td>ab 09:30</td><td>Rund um die Rochuskapelle</td>
        <td>Traditionelles Rochusfest</td></tr>
        """

        [event] = rossel_wilberhofen._events_from_pages(news, calendar, strict=True)

        self.assertEqual(event["date"], "2026-08-17")

        with self.assertRaisesRegex(
            regional_common.ParserEmptyError,
            "news/calendar dates disagree",
        ):
            rossel_wilberhofen._events_from_pages(
                news,
                calendar.replace("17.08.", "16.08."),
                strict=True,
            )

    def test_schmitt_parses_official_calendar_and_visitor_start(self):
        html = """
        <h2>Unsere Markttermine - Für weitere Infos einfach Termin anklicken!</h2>
        <p>Unser nächster Flohmarkt 16.8. 56626 Andernach, Kaufland
        Platzvergabe ab 6.00 Uhr! Verkauf ab 11.00 Uhr!</p>
        <div>16.08.2026</div><div><a href="/event-andernach">
        56626 Andernach, Kaufland, Koblenzer Straße 51</a></div>
        <div>23.08.2026</div><div><a href="/event-muelheim">
        56218 Mülheim- Kärlich, Kaufland, Industriestraße 4 Verkauf: ab 11.00 Uhr</a></div>
        """

        events = schmitt_markets._events_from_page(html, strict=True)

        self.assertEqual([event["city"] for event in events], ["Andernach", "Mülheim-Kärlich"])
        self.assertTrue(all(event["time"] == "11:00" for event in events))
        self.assertTrue(all(event["source_id"] == "schmitt-veranstaltungen" for event in events))
        self.assertEqual(events[0]["link"], "https://fmarkt.de/event-andernach")

    def test_schmitt_refuses_rows_without_a_visitor_start(self):
        html = """
        <h2>Unsere Markttermine - Für weitere Infos einfach Termin anklicken!</h2>
        <div>23.08.2026</div><div><a href="/event">
        56218 Mülheim-Kärlich, Kaufland, Industriestraße 4</a></div>
        """
        with self.assertRaisesRegex(regional_common.ParserEmptyError, "visitor start"):
            schmitt_markets._events_from_page(html, strict=True)

    def test_rieder_combines_dated_card_with_official_location_facts(self):
        terms = """
        <a href="https://www.rieder-maerkte.de/produkt/solingen-16-08-2026/">
        <h2 class="woocommerce-loop-product__title">16.08.2026
        Solingen-Aufderhöhe, REWE Ihr Kaufpark</h2></a>
        """
        location = """
        <h1>Solingen, REWE Ihr Kaufpark</h1>
        <p>Friedenstraße 96, 42699 Solingen</p>
        <p>Die offiziellen Verkaufszeiten sind an Sonn‐ &amp; Feiertagen von 11 bis 18 Uhr.</p>
        """

        [event] = rieder_markets._events_from_pages(terms, location, strict=True)

        self.assertEqual(event["date"], "2026-08-16")
        self.assertEqual(event["time"], "11:00–18:00")
        self.assertEqual(event["venue"], "REWE Ihr Kaufpark")
        self.assertEqual(event["venue_address"], "Friedenstraße 96")
        self.assertEqual(event["source_id"], "rieder-solingen-rewe")

    def test_rieder_refuses_uncorroborated_hours(self):
        terms = """
        <a href="/event"><h2>16.08.2026 Solingen-Aufderhöhe,
        REWE Ihr Kaufpark</h2></a>
        """
        location = "<p>Friedenstraße 96, 42699 Solingen</p>"
        with self.assertRaisesRegex(regional_common.ParserEmptyError, "address or hours"):
            rieder_markets._events_from_pages(terms, location, strict=True)

    @staticmethod
    def _canonical(
        title, date, city, source, source_id, organizer="", venue="Marktplatz",
    ):
        return validate_event({
            "title": title,
            "source": source,
            "source_id": source_id,
            "date": date,
            "start_date": date,
            "end_date": date,
            "city": city,
            "venue": venue,
            "organizer": organizer,
            "score": 1.0,
        })

    def _directory_cohort(self):
        rows = (
            ("Dorf-Flohmarkt Dorf-Trödel Windeck", "2026-08-16", "Windeck", "BV Wilberhofen-Rossel", "Wilberhofen"),
            ("Flohmarkt 56626 Andernach, Kaufland", "2026-08-16", "Andernach", "Schmitt Veranstaltungen", "Kaufland"),
            ("Flohmarkt REWE Ihr Kaufpark Solingen-Aufderhöhe", "2026-08-16", "Solingen", "Rieder-Märkte", "REWE Ihr Kaufpark Solingen-Aufderhöhe"),
            ("Flohmarkt 56218 Mülheim-Kärlich, Kaufland", "2026-08-23", "Mülheim-Kärlich", "Schmitt Veranstaltungen", "Kaufland"),
            ("Flohmarkt 56170 Bendorf, Kaufland", "2026-08-30", "Bendorf", "Schmitt Veranstaltungen", "Kaufland"),
            ("Flohmarkt HELLWEG Monheim", "2026-09-06", "Monheim Am Rhein", "Rieder-Märkte", "HELLWEG"),
            ("Flohmarkt Kirmesplatz Reisholz", "2026-09-13", "Düsseldorf", "Rieder-Märkte", "Kirmesplatz"),
        )
        return [
            self._canonical(title, date, city, "marktcom", "marktcom", organizer, venue)
            for title, date, city, organizer, venue in rows
        ]

    def _primary_cohort(self):
        rows = (
            ("Dorf-Flohmarkt Wilberhofen", "2026-08-16", "Windeck", "Bürgerverein Rossel-Wilberhofen", "rossel-wilberhofen-dorfflohmarkt"),
            ("Flohmarkt Andernach, Kaufland", "2026-08-16", "Andernach", "Schmitt Veranstaltungen", "schmitt-veranstaltungen"),
            ("Trödelmarkt Solingen-Aufderhöhe, REWE Ihr Kaufpark", "2026-08-16", "Solingen", "Rieder Märkte", "rieder-solingen-rewe"),
            ("Flohmarkt Mülheim-Kärlich, Kaufland", "2026-08-23", "Mülheim-Kärlich", "Schmitt Veranstaltungen", "schmitt-veranstaltungen"),
            ("Flohmarkt Bendorf, Kaufland", "2026-08-30", "Bendorf", "Schmitt Veranstaltungen", "schmitt-veranstaltungen"),
        )
        return [
            self._canonical(title, date, city, source, source_id)
            for title, date, city, source, source_id in rows
        ]

    def test_primary_sources_preserve_the_full_seven_event_directory_cohort(self):
        kept, replaced = partition_directory_fallbacks([
            *self._directory_cohort(), *self._primary_cohort(),
        ])

        self.assertEqual(len(kept), 7)
        self.assertEqual(len(replaced), 5)
        self.assertEqual(
            {event.source_id for event in kept},
            {
                "rossel-wilberhofen-dorfflohmarkt",
                "schmitt-veranstaltungen",
                "rieder-solingen-rewe",
                "marktcom",
            },
        )
        self.assertEqual(
            {(event.start_date, event.city) for event in kept},
            {(event.start_date, event.city) for event in self._directory_cohort()},
        )

    def test_marktcom_is_retained_when_primary_sources_return_nothing(self):
        directory = self._directory_cohort()

        kept, replaced = partition_directory_fallbacks(directory)

        self.assertEqual(kept, directory)
        self.assertEqual(replaced, [])

    def test_partial_primary_results_replace_only_matching_occurrences(self):
        directory = self._directory_cohort()
        [_windeck, andernach, *_rest] = self._primary_cohort()

        kept, replaced = partition_directory_fallbacks([*directory, andernach])

        self.assertEqual(len(kept), 7)
        self.assertEqual(len(replaced), 1)
        self.assertEqual(replaced[0].city, "Andernach")
        self.assertTrue(any(
            event.city == "Windeck" and event.source_id == "marktcom"
            for event in kept
        ))

    def test_runner_uses_marktcom_only_for_primary_occurrence_gaps(self):
        directory = [event.to_dict() for event in self._directory_cohort()]
        primary = [event.to_dict() for event in self._primary_cohort()]
        sources = {
            "marktcom": lambda: directory,
            "Bürgerverein Rossel-Wilberhofen": lambda: primary[:1],
            "Schmitt Veranstaltungen": lambda: [primary[1], *primary[3:]],
            "Rieder Märkte": lambda: primary[2:3],
        }
        context = RunContext(
            config.RuntimeConfig(series_ledger_json=""),
            EventWindow(datetime(2026, 8, 16), datetime(2026, 9, 13)),
            "market-fallbacks",
            configure_logging("market-fallbacks", "ERROR", "", ""),
            clock=lambda: datetime(2026, 8, 16, 12),
        )

        with mock.patch.object(runner, "_previous_snapshot", return_value={}), \
             mock.patch.object(
                 runner.detail_enrichment,
                 "enrich_events",
                 side_effect=lambda events, **_kwargs: events,
             ):
            result = runner.run_import(context, sources)

        self.assertEqual(len(result.events), 7)
        self.assertEqual(
            result.source_results["marktcom"].rejection_reasons,
            {"filter:first_party_replacement": 5},
        )

    def test_runner_retains_fallback_when_primary_record_is_not_publishable(self):
        directory = [event.to_dict() for event in self._directory_cohort()]
        [windeck, *_rest] = [event.to_dict() for event in self._primary_cohort()]
        windeck["score"] = 0.1
        context = RunContext(
            config.RuntimeConfig(series_ledger_json="", score_floor=0.8),
            EventWindow(datetime(2026, 8, 16), datetime(2026, 9, 13)),
            "market-filtered-primary",
            configure_logging("market-filtered-primary", "ERROR", "", ""),
            clock=lambda: datetime(2026, 8, 16, 12),
        )

        with mock.patch.object(runner, "_previous_snapshot", return_value={}), \
             mock.patch.object(
                 runner.detail_enrichment,
                 "enrich_events",
                 side_effect=lambda events, **_kwargs: events,
             ):
            result = runner.run_import(context, {
                "marktcom": lambda: directory,
                "Bürgerverein Rossel-Wilberhofen": lambda: [windeck],
            })

        self.assertEqual(len(result.events), 7)
        self.assertTrue(all(event.source_id == "marktcom" for event in result.events))


if __name__ == "__main__":
    unittest.main()
