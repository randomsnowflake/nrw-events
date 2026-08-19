import unittest
from datetime import datetime
from unittest import mock

from nrw_events import common, config
from nrw_events.source_specs import AdapterType
from nrw_events.sources import SOURCE_SPECS
from nrw_events.sources import regional_ionas4, regional_sitekit
from tests.helpers import patch_window


def _ionas4_cities():
    return {city for city, _url, _cal, _trust in regional_ionas4._CALENDARS}


def _sitekit_cities():
    return {city for city, _sid, _url, _trust in regional_sitekit._CALENDARS}


class Tier1SourceRegistrationTests(unittest.TestCase):
    """The newly wired municipal calendars stay registered and well-formed."""

    def test_ionas4_covers_roesrath_and_ruppichteroth(self):
        self.assertLessEqual({"Rösrath", "Ruppichteroth"}, _ionas4_cities())

    def test_new_ionas4_cities_fetch_event_detail_pages(self):
        # Without detail enrichment these calendars ship a bare title, which
        # degrades description and venue quality for every imported event.
        for city in ("Rösrath", "Ruppichteroth"):
            with self.subTest(city=city):
                self.assertIsNotNone(regional_ionas4._detail_fetcher_for_city(city))

    def test_ionas4_calendar_url_is_the_events_json_parent(self):
        for city, url, calendar_url, _trust in regional_ionas4._CALENDARS:
            with self.subTest(city=city):
                self.assertTrue(url.startswith(calendar_url), f"{url} not under {calendar_url}")
                self.assertTrue(calendar_url.endswith("/"))

    def test_sitekit_covers_the_sitepark_cluster(self):
        self.assertLessEqual(
            {"Frechen", "Hürth", "Erftstadt", "Zülpich"}, _sitekit_cities()
        )

    def test_sitekit_source_ids_are_unique(self):
        ids = [source_id for _city, source_id, _url, _trust in regional_sitekit._CALENDARS]
        self.assertEqual(len(ids), len(set(ids)))

    def test_sitekit_detail_failures_do_not_consume_the_batch_on_retries(self):
        patch_window(self, datetime(2026, 8, 14), datetime(2026, 8, 31))
        listing = (
            '<article class="SP-Teaser">'
            '<a class="SP-Teaser__inner" href="/events/bingo">'
            '<h4 class="SP-Teaser__headline">Bingo!</h4>'
            '<span class="SP-Scheduling__date">21.08.2026</span>'
            '</a></article>'
        )

        with mock.patch.object(
            regional_sitekit,
            "_CALENDARS",
            [("Wesseling", "sitekit-wesseling", "https://example.test/events", 0.9)],
        ), mock.patch.object(common, "fetch_url", return_value=listing), mock.patch.object(
            common, "fetch_detail_url", return_value=""
        ) as detail_fetch:
            regional_sitekit.fetch()

        detail_fetch.assert_called_once_with(
            "https://example.test/events/bingo",
            cache_namespace="regional-sitekit-detail",
            timeout=15,
            retry_attempts=1,
        )

    def test_sitekit_pagination_metadata_and_url_are_supported(self):
        html = (
            '<div class="SP-Pagination" '
            'data-page="{&quot;min&quot;:1,&quot;max&quot;:11}"></div>'
        )

        self.assertEqual(regional_sitekit._pagination_max(html), 11)
        self.assertIn(
            "sp%3Apage%5BeventSearch-1.form%5D%5B0%5D=3",
            regional_sitekit._page_url("https://example.test/events", 3),
        )
        self.assertTrue(
            regional_sitekit._page_starts_after_window(
                '<span class="SP-Scheduling__date">01.09.2026</span>'
            )
        )

    def test_waldbroel_is_registered_as_an_ical_spec(self):
        spec = next((s for s in SOURCE_SPECS if s.id == "waldbroel"), None)
        self.assertIsNotNone(spec, "Waldbröl spec missing")
        self.assertIs(spec.adapter, AdapterType.ICAL)
        self.assertEqual(spec.city, "Waldbröl")
        self.assertTrue(spec.urls[0].endswith("ical=1"))


class Tier1GeoCoverageTests(unittest.TestCase):
    """A source city without coordinates silently bypasses the radius filter."""

    def setUp(self):
        patch_window(self, datetime(2026, 7, 13), datetime(2026, 8, 31))

    def test_every_new_source_city_has_configured_coordinates(self):
        cities = _ionas4_cities() | _sitekit_cities() | {
            spec.city for spec in SOURCE_SPECS if spec.city
        }
        for city in sorted(cities):
            with self.subTest(city=city):
                coords, kind, _reason = common.resolve_location(city, None)
                self.assertIsNotNone(coords, f"{city} has no configured coordinates")
                self.assertEqual(kind, "known_city")

    def test_new_source_cities_sit_inside_the_report_radius(self):
        for city in ["Rösrath", "Ruppichteroth", "Frechen", "Hürth",
                     "Erftstadt", "Zülpich", "Waldbröl"]:
            with self.subTest(city=city):
                coords, _kind, _reason = common.resolve_location(city, None)
                distance = common.haversine(common.BONN_LAT, common.BONN_LON, *coords)
                self.assertLessEqual(distance, config.MAX_RADIUS_KM)


class MalformedSourceUrlTests(unittest.TestCase):
    def test_windows_style_separators_are_repaired(self):
        # Ruppichteroth publishes "http:\\www.ernteverein.de" for its
        # Erntedankfest; unrepaired the link is dead on the public site.
        self.assertEqual(
            common.normalize_url("http:\\\\www.ernteverein.de"),
            "http://www.ernteverein.de",
        )

    def test_backslashes_inside_a_path_are_repaired(self):
        self.assertEqual(
            common.normalize_url("https://example.de\\termine\\fest"),
            "https://example.de/termine/fest",
        )

    def test_wellformed_urls_are_untouched(self):
        for url in ("https://www.roesrath.de/kalender/",
                    "http://example.de/a?b=1#c",
                    ""):
            with self.subTest(url=url):
                self.assertEqual(common.normalize_url(url), url)


class Ionas4EventQualityTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 7, 13), datetime(2026, 8, 31))

    def test_json_location_and_category_survive_without_a_detail_page(self):
        items = [{
            "id": "30292:0",
            "start": "2026-08-15T10:30",
            "end": "2026-08-15T16:00",
            "allDay": False,
            "title": "KISABA - Kindersachenbasar Ruppichteroth",
            "website": "",
            "category": {"id": "5689", "name": "Allgemeines"},
            "tags": [],
            "location": {"name": "evangl. Gemeindehaus Ruppichteroth"},
        }]
        events = regional_ionas4._events_from_items(
            items, "Ruppichteroth", "https://www.ruppichteroth.de/kalender/", 0.95,
            detail_fetcher=None, source_id="ionas4-ruppichteroth",
        )
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["venue"], "evangl. Gemeindehaus Ruppichteroth")
        self.assertIn("Allgemeines", event["category"])
        self.assertEqual(event["time"], "10:30–16:00")

    def test_sparse_detail_uses_organizer_instead_of_a_generic_tag(self):
        items = [{
            "id": "30298:0",
            "start": "2026-08-09T00:00",
            "end": "2026-08-10T00:00",
            "allDay": True,
            "title": "Flohmarkt in Winterscheid",
            "website": "",
            "category": {"name": "Vereine"},
            "tags": [{"name": "Freizeit"}],
        }]
        detail = """
        <div class="tvm-event--description"></div>
        <p class="tvm-organiser-name">Heimatverein Winterscheid</p>
        <button onclick='navigator.clipboard.writeText(
          "https://www.ruppichteroth.de/kalender/veranstaltungen/flohmarkt/30298:0")'>
        </button>
        """

        events = regional_ionas4._events_from_items(
            items, "Ruppichteroth", "https://www.ruppichteroth.de/kalender/", 0.95,
            detail_fetcher=lambda _url: detail, source_id="ionas4-ruppichteroth",
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["organizer"], "Heimatverein Winterscheid")
        self.assertIn("Veranstalter: Heimatverein Winterscheid", events[0]["description"])
        self.assertNotIn("Freizeit.", events[0]["description"])

    def test_administrative_appointments_are_not_imported(self):
        # These municipal calendars mix public events with office hours; the
        # latter must not reach the site.
        items = [{
            "id": "30292:1",
            "start": "2026-08-07T09:00",
            "end": "2026-08-07T12:00",
            "allDay": False,
            "title": "Notarsprechtag im Rathaus",
            "website": "",
            "category": {"name": "Allgemeines"},
            "tags": [],
            "location": {"name": "Rathaus der Gemeinde Ruppichteroth"},
        }]
        events = regional_ionas4._events_from_items(
            items, "Ruppichteroth", "https://www.ruppichteroth.de/kalender/", 0.95,
            detail_fetcher=None, source_id="ionas4-ruppichteroth",
        )
        self.assertEqual(events, [])

    def test_malformed_website_becomes_a_usable_link(self):
        items = [{
            "id": "26596:0",
            "start": "2026-08-14T00:00",
            "end": "2026-08-17T00:00",
            "allDay": True,
            "title": "Bröltaler Erntedankfest",
            "website": "http:\\\\www.ernteverein.de",
            "category": {"name": "Allgemeines"},
            "tags": [],
            "location": {"name": "Festplatz Bruchhausen Röttgen"},
        }]
        events = regional_ionas4._events_from_items(
            items, "Ruppichteroth", "https://www.ruppichteroth.de/kalender/", 0.95,
            detail_fetcher=None, source_id="ionas4-ruppichteroth",
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["link"], "http://www.ernteverein.de")


class SitekitPaginationTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 7, 13), datetime(2026, 8, 31))

    @staticmethod
    def _teaser(title: str, date: str) -> str:
        return (
            '<article class="SP-Teaser">'
            f'<a class="SP-Teaser__inner" href="/events/{title.lower()}">'
            f'<h4 class="SP-Teaser__headline">{title}</h4>'
            f'<span class="SP-Scheduling__date">{date}</span>'
            '<div class="SP-Teaser__abstract">Offizielle Beschreibung.</div>'
            '</a></article>'
        )

    def test_fetch_follows_every_advertised_result_page(self):
        first = (
            self._teaser("Erster Markt", "01.08.2026")
            + '<div class="SP-Pagination" '
            'data-page="{&quot;min&quot;:1,&quot;max&quot;:2}"></div>'
        )
        second = self._teaser("Zweiter Markt", "02.08.2026")
        urls = []

        def fake_fetch(url, **_kwargs):
            urls.append(url)
            return first if len(urls) == 1 else second

        with mock.patch.object(
            regional_sitekit,
            "_CALENDARS",
            [("Teststadt", "sitekit-test", "https://example.test/events", 0.9)],
        ), mock.patch.object(common, "fetch_url", side_effect=fake_fetch), mock.patch.object(
            common, "fetch_detail_url", return_value=""
        ):
            events = regional_sitekit.fetch()

        self.assertEqual(
            {event["title"] for event in events},
            {"Erster Markt", "Zweiter Markt"},
        )
        self.assertEqual(len(urls), 2)
        self.assertTrue(
            all(len(event["description"]) >= 40 for event in events)
        )
        self.assertIn(
            "sp%3Apage%5BeventSearch-1.form%5D%5B0%5D=2",
            urls[1],
        )

    def test_teasers_parse_when_class_and_href_attributes_are_reordered(self):
        html = (
            '<article data-source="sitekit" class="SP-Teaser SP-Teaser--textual">'
            '<a href="/events/reordered" rel="bookmark" class="SP-Teaser__inner">'
            '<h4 class="SP-Teaser__headline">Reihenfolgefestes Stadtfest</h4>'
            '<span class="SP-Scheduling__date">01.08.2026</span>'
            '<div class="SP-Teaser__abstract">Offizielle Beschreibung.</div>'
            '</a></article>'
        )

        events = regional_sitekit._events_from_teasers(
            html, "https://example.test/events", "Teststadt", 0.9, "sitekit-test"
        )

        self.assertEqual([event["title"] for event in events], ["Reihenfolgefestes Stadtfest"])
        self.assertEqual(events[0]["link"], "https://example.test/events/reordered")

    def test_sitekit_midnight_placeholder_is_not_published_as_a_real_time(self):
        html = self._teaser("Ganztägige Ausstellung", "01.08.2026 00:00 Uhr")

        events = regional_sitekit._events_from_teasers(
            html, "https://example.test/events", "Teststadt", 0.9, "sitekit-test"
        )

        self.assertEqual(events[0]["time"], "")
        self.assertTrue(events[0]["all_day"])

    def test_sitekit_enriches_ambiguous_teasers_from_visible_detail_copy(self):
        listing = (
            '<article class="SP-Teaser">'
            '<a class="SP-Teaser__inner" href="/events/open-game">'
            '<h4 class="SP-Teaser__headline">Offene Spielrunde</h4>'
            '<span class="SP-Scheduling__date">21.08.2026</span>'
            '<div class="SP-Teaser__abstract">'
            'Ein Dämon geht nachts um. Wem kannst du trauen?'
            '</div></a></article>'
        )
        detail = (
            '<div class="SP-Text"><div class="SP-Paragraph">'
            '<p>Für alle ab 16 Jahre, die Fans von Social-Deduction-Spielen sind.</p>'
            '</div></div>'
            # Same class with a modifier: venue contact card and town-hall
            # footer. Neither describes the event.
            '<div class="SP-Contact__locality__text SP-Paragraph">'
            '<p>Bürgerhaus Teststadt Marktweg 1</p></div>'
            '<div class="SP-Paragraph SP-Paragraph--footer">'
            '<p>Stadt Teststadt Telefax 0000/1-2 Bürgeramt 8.00 - 12.00 Uhr</p></div>'
        )

        with mock.patch.object(
            regional_sitekit,
            "_CALENDARS",
            [("Teststadt", "sitekit-test", "https://example.test/events", 0.9)],
        ), mock.patch.object(common, "fetch_url", return_value=listing), mock.patch.object(
            common,
            "fetch_detail_url",
            return_value=detail,
        ):
            events = regional_sitekit.fetch()

        self.assertEqual(len(events), 1)
        self.assertIn("Social-Deduction-Spielen", events[0]["description"])
        self.assertNotIn("Telefax", events[0]["description"])
        self.assertNotIn("Bürgerhaus Teststadt", events[0]["description"])
        self.assertEqual(events[0]["category_key"], "activities")
        self.assertEqual(
            events[0]["category_reason"],
            "format:participatory-social-activity",
        )

    def test_sitekit_replaces_long_generated_fallback_with_shorter_detail_facts(self):
        listing = (
            '<article class="SP-Teaser">'
            '<a class="SP-Teaser__inner" href="/events/kinotag">'
            '<h4 class="SP-Teaser__headline">Kinotag im Rheinforum</h4>'
            '<span class="SP-Scheduling__date">18.08.2026 15:00</span>'
            '<div class="SP-Teaser__abstract">"Der gestiefelte Kater".</div>'
            '</a></article>'
        )
        detail = (
            '<div class="SP-Paragraph"><p>geeignet ab 8 Jahre, Laufzeit: 87 min</p></div>'
            '<div class="SP-Paragraph"><p>Eintritt: 2 Euro</p></div>'
        )

        with mock.patch.object(
            regional_sitekit,
            "_CALENDARS",
            [("Wesseling", "sitekit-wesseling", "https://example.test/events", 0.9)],
        ), mock.patch.object(common, "fetch_url", return_value=listing), mock.patch.object(
            common, "fetch_detail_url", return_value=detail,
        ):
            [event] = regional_sitekit.fetch()

        self.assertEqual(event["description_source"], "scraped")
        self.assertIn("geeignet ab 8 Jahre", event["description"])
        self.assertIn("2 Euro", event["description"])
        self.assertNotIn("Veranstaltungskalender Wesseling", event["description"])

    def test_sitekit_keeps_richer_scraped_teaser_during_metadata_enrichment(self):
        listing = (
            '<article class="SP-Teaser">'
            '<a class="SP-Teaser__inner" href="/events/sommerfest">'
            '<h4 class="SP-Teaser__headline">Sommerfest am Rhein</h4>'
            '<span class="SP-Scheduling__date">22.08.2026 15:00</span>'
            '<div class="SP-Teaser__abstract">'
            'Ein ausführlicher Nachmittag mit Musik, Spielen, Speisen und einem Programm für Familien.'
            '</div></a></article>'
        )
        detail = '<div class="SP-Paragraph"><p>Einlass ab 14:30 Uhr.</p></div>'

        with mock.patch.object(
            regional_sitekit,
            "_CALENDARS",
            [("Wesseling", "sitekit-wesseling", "https://example.test/events", 0.9)],
        ), mock.patch.object(common, "fetch_url", return_value=listing), mock.patch.object(
            common, "fetch_detail_url", return_value=detail,
        ):
            [event] = regional_sitekit.fetch()

        self.assertEqual(event["description_source"], "scraped")
        self.assertIn("ausführlicher Nachmittag", event["description"])
        self.assertNotIn("Einlass ab 14:30 Uhr", event["description"])

    def test_sitekit_reviewed_event_formats_get_deterministic_categories(self):
        events = [
            {
                "title": "Kinotag im Rheinforum",
                "description": '"Das kleine Gespenst".',
                "category_key": "other",
            },
            {
                "title": "ADFC: Zum Trodelööh",
                "description": "Rundtour (ca. 60 km, mittel) zur höchsten Erhebung von Köln.",
                "category_key": "other",
            },
            {
                "title": "BLUES Gig & SESSION",
                "description": "Erst Konzert, dann Session in der Kornkammer.",
                "category_key": "other",
            },
        ]

        regional_sitekit._correct_categories(events)

        self.assertEqual(
            [event["category_key"] for event in events],
            ["cinema", "outdoor", "concert"],
        )
        self.assertTrue(all(event["category_confidence"] == 1.0 for event in events))

    def test_sitekit_format_rules_do_not_reclassify_ambiguous_neighbors(self):
        events = [
            {
                "title": "ADFC: Mitgliederversammlung",
                "description": "Berichte und Vorstandswahl.",
                "category_key": "other",
            },
            {
                "title": "Open Session",
                "description": "Offenes Treffen für Interessierte.",
                "category_key": "activities",
            },
            {
                "title": "Kinotag: Vortrag über Filmförderung",
                "description": "Vortrag und Diskussion mit Branchenfachleuten.",
                "category_key": "talk",
                "category_label": "Vortrag & Wissen",
                "category_confidence": 0.9,
                "category_reason": "keyword:vortrag",
            },
            {
                "title": "BLUES Gig & SESSION",
                "description": "Erst Konzert, dann Session in der Kornkammer.",
                "category_key": "concert",
                "category_label": "Konzerte & Live-Musik",
                "category_confidence": 0.6,
                "category_reason": "keyword:musik",
            },
        ]

        regional_sitekit._correct_categories(events)

        self.assertEqual(
            [event["category_key"] for event in events],
            ["other", "activities", "talk", "concert"],
        )
        self.assertEqual(events[2]["category_reason"], "keyword:vortrag")
        self.assertEqual(events[3]["category_confidence"], 1.0)
        self.assertEqual(events[3]["category_reason"], "source-format:concert")


if __name__ == "__main__":
    unittest.main()
