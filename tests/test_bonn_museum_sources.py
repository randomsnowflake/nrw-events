import unittest
from datetime import datetime
from unittest.mock import patch

from nrw_events import common, report
from nrw_events.sources import (
    SOURCES,
    bonn,
    deutsches_museum_bonn,
    haus_der_geschichte,
    kunstmuseum_bonn,
    museum_koenig,
)
from tests.helpers import patch_window


class BonnMuseumSourceTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 8, 1), datetime(2026, 8, 31))

    def test_haus_der_geschichte_recurring_guided_tours_create_occurrences(self):
        html = """
        <h4>Begleitungen zur Wechselausstellung „Nach Hitler“</h4>
        <p><strong>Öffentliche Begleitungen</strong><br>
        Am Donnerstag, Samstag und Sonntag jeweils um 15 Uhr.<br>
        Eine Anmeldung ist erforderlich:
        <a href="https://bonn-ticket.hdg.de/#/museum/2">zum Buchungsportal</a></p>
        <h4>Begleitungen im Museumsgarten</h4>
        <p><strong>Öffentliche Begleitungen</strong><br>
        Samstag und Sonntag, jeweils um 14:30 Uhr.<br>
        Eine Anmeldung ist erforderlich:
        <a href="https://bonn-ticket.hdg.de/#/exhibition/11">zum Buchungsportal</a></p>
        <h4>Begleitungen zu den historischen Orten</h4>
        """

        events = haus_der_geschichte.guided_tours_from_html(html)
        sunday = [event for event in events if event["start_date"] == "2026-08-02"]

        self.assertEqual(
            [(event["title"], event["time"]) for event in sunday],
            [
                ("Öffentliche Begleitung „Nach Hitler“", "15:00"),
                ("Öffentliche Begleitung im Museumsgarten", "14:30"),
            ],
        )
        self.assertTrue(all(event["price"] == "kostenlos" for event in sunday))
        self.assertTrue(all("Anmeldung erforderlich" in event["description"] for event in sunday))
        self.assertTrue(all(
            event["source_id"] == "haus-der-geschichte-begleitungen"
            for event in sunday
        ))
        self.assertTrue(all(event["score"] >= 0.4 for event in sunday))

    def test_haus_der_geschichte_embedded_family_tours_are_searchable_occurrences(self):
        html = """
        <div class="panel bonn" data-eventtype="77" data-date="20260802">
          <div class="panel-heading">
            <div class="calendar-events-time">12:30 Uhr</div>
            <h6>Kinder- und Familienprogramm <span class="black"></span></h6>
            <h4>Offenes Atelier „Du bist unterwegs!“</h4>
            Eintritt frei
          </div>
          <div class="calendar-bodycopy"><p>
            Das Offene Atelier lädt zum Mitmachen ein. Um 14 und 15:30 Uhr finden
            Familienbegleitungen (60 Min.) zum Thema „Urlaub, Reisen und Erholung“ statt.
          </p></div>
          <a class="hidden" href="/haus-der-geschichte/veranstaltungen/offenes-atelier">Details</a>
        </div>
        """

        events = haus_der_geschichte.events_from_html(html)

        self.assertEqual(
            [(event["title"], event["time"]) for event in events],
            [
                ("Offenes Atelier „Du bist unterwegs!“", "12:30"),
                ("Familienbegleitung „Urlaub, Reisen und Erholung“", "14:00–15:00"),
                ("Familienbegleitung „Urlaub, Reisen und Erholung“", "15:30–16:30"),
            ],
        )
        self.assertEqual(events[1]["category_key"], "kids")
        self.assertEqual(len(report.deduplicate(events)), 3)
        self.assertTrue(all(event["score"] >= 0.4 for event in events))

    def test_haus_der_geschichte_keeps_calendar_when_guided_tour_page_fails(self):
        calendar = """
        <div class="panel bonn" data-date="20260802">
          <div class="calendar-events-time">12:30 Uhr</div>
          <h6>Museum <span class="black"></span></h6><h4>Offenes Atelier</h4>
          <div class="calendar-bodycopy"><p>Familienprogramm im Museum.</p></div>
        </div>
        """
        with patch("nrw_events.common.fetch_url", side_effect=[calendar, RuntimeError("guided tours unavailable")]), patch(
            "nrw_events.common.log_source_error"
        ) as log_error:
            events = haus_der_geschichte.fetch()

        self.assertEqual([event["title"] for event in events], ["Offenes Atelier"])
        log_error.assert_called_once()
        self.assertEqual(log_error.call_args.kwargs["source_id"], "haus-der-geschichte-begleitungen")

    def test_museum_koenig_calendar_cards_create_complete_occurrences(self):
        html = """
        <li class="p-card e-lib-event-calendar__list-item" data-publication-date="2026-08-02 11:00:00" data-search="Wir lesen vor;Lesung;Für Familien;Kostenlos">
          <div class="e-lib-event-calendar__icon-wrapper"><img alt="kostenfrei"></div>
          <p class="e-lib-event-calendar__date-location">Sonntag, 02.08.2026, 11:00 Uhr, Festsaal</p>
          <h2 class="e-lib-event-calendar__list-item-title"><a href="/de/veranstaltungen/wir-lesen-vor.html">Wir lesen vor</a></h2>
          <div class="e-lib-cards__tag-wrapper"><span class="e-lib-cards__tag">Lesung</span><span class="e-lib-cards__tag">Für Familien</span></div>
        </li>
        <li class="p-card e-lib-event-calendar__list-item" data-publication-date="2026-08-02 14:00:00" data-search="Öffentliche Familienführung;Führung;Für Familien;Kostenlos">
          <p class="e-lib-event-calendar__date-location">Sonntag, 02.08.2026, 14:00 Uhr, Foyer</p>
          <h2 class="e-lib-event-calendar__list-item-title"><a href="/de/veranstaltungen/familienfuehrung.html">Öffentliche Familienführung</a></h2>
          <span class="e-lib-cards__tag">Führung</span><span class="e-lib-cards__tag">Für Familien</span>
        </li>
        """

        events = museum_koenig.events_from_html(html)

        self.assertEqual([event["title"] for event in events], ["Wir lesen vor", "Öffentliche Familienführung"])
        self.assertEqual([event["time"] for event in events], ["11:00", "14:00"])
        self.assertTrue(all(event["venue"] == "Museum Koenig Bonn" for event in events))
        self.assertTrue(all(event["price"] == "" for event in events))
        self.assertTrue(all("Museumseintritt kann zusätzlich anfallen" in event["description"] for event in events))
        self.assertEqual(events[1]["category_key"], "exhibition")
        self.assertEqual(events[0]["link"], "https://bonn.leibniz-lib.de/de/veranstaltungen/wir-lesen-vor.html")
        self.assertTrue(all(event["score"] >= 0.4 for event in events))

    def test_museum_koenig_does_not_mislabel_free_tour_plus_paid_entry(self):
        detail = """
        <main><p>Jeden Sonntag zeigen wir Lieblingsorte im Museum.</p>
        <dl><dt>Preis</dt><dd>Führung kostenlos zzgl. Eintritt in das Museum</dd></dl></main>
        """
        parsed = museum_koenig._detail_description(detail, {})

        self.assertIn("regulärer Museumseintritt ist erforderlich", parsed["description"])

    def test_museum_koenig_external_event_uses_detail_meeting_point(self):
        listing = """
        <li class="e-lib-event-calendar__list-item" data-publication-date="2026-08-08 11:00:00">
          <p class="e-lib-event-calendar__date-location">Samstag, 08.08.2026, 11:00 Uhr, Externe Veranstaltung</p>
          <h2 class="e-lib-event-calendar__list-item-title"><a href="/de/veranstaltungen/waldspaziergang.html">Waldspaziergang Kunst und Biologie</a></h2>
          <span class="e-lib-cards__tag">Exkursion</span>
        </li>
        """
        detail = """
        <main><p>Ein Waldspaziergang mit Kunst und Biologie.</p>
        <div class="e-list__item-keyword"><p>Treffpunkt</p></div>
        <div class="e-list__item-text"><span><p>Zugang zum Klufterbachtal in Bonn-Friesdorf</p></span></div>
        <p>Kostenlos für AKG-Mitglieder, Nichtmitglieder: 10 Euro</p></main>
        """

        events = museum_koenig.events_from_html(listing, detail_fetcher=lambda _url: detail)

        self.assertEqual(events[0]["venue"], "Zugang zum Klufterbachtal in Bonn-Friesdorf")
        self.assertEqual(events[0]["price"], "")
        self.assertIn("Nichtmitglieder: 10 Euro", events[0]["description"])

    def test_deutsches_museum_ajax_cards_preserve_end_time_and_description(self):
        html = """
        <article class="events-teaser__content">
          <a href="/bonn/programm/veranstaltung/mitgliederfuehrung-besuch-im-teilchenzoo"></a>
          <li class="events-teaser__content-label"><span>Mitgliederführung</span></li>
          <h3 class="events-teaser__content-title"><a href="/bonn/programm/veranstaltung/mitgliederfuehrung-besuch-im-teilchenzoo"><span>Mitgliederführung: Besuch im Teilchenzoo</span></a></h3>
          <p>Exklusiver KI:ckstart im Erlebnisraum „Elementares“.</p>
          <i class="icon-pin"></i><p>Deutsches Museum Bonn</p>
          <i class="icon-clock"></i><time datetime="2026-08-22 13:00">22. August 2026, 13:00 bis 14:00 Uhr</time>
        </article>
        """

        events = deutsches_museum_bonn.events_from_html(html)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["time"], "13:00–14:00")
        self.assertEqual(events[0]["start_at"], "2026-08-22T13:00+02:00")
        self.assertEqual(events[0]["end_at"], "2026-08-22T14:00+02:00")
        self.assertEqual(events[0]["venue"], "Deutsches Museum Bonn")
        self.assertIn("Exklusiver KI:ckstart", events[0]["description"])
        self.assertGreaterEqual(events[0]["score"], 0.4)

    def test_deutsches_museum_detail_uses_body_copy_and_cost_facts(self):
        detail = """
        <div data-teaser-text-target><p>Kurzer Teaser.</p></div>
        <section><h2>Kosten</h2><li>Der Eintritt ist frei.</li>
        <span>Nur nach Anmeldung für Mitglieder des Deutschen Museums.</span></section>
        <div class="event-detail-text"><p>Die Führung erklärt anschaulich den Einsatz von KI in der Teilchenphysik.</p></div>
        """

        parsed = deutsches_museum_bonn._detail_description(detail, {})

        self.assertIn("Einsatz von KI", parsed["description"])
        self.assertIn("Eintritt frei", parsed["description"])
        self.assertIn("Nur nach Anmeldung", parsed["description"])
        self.assertNotIn("Kurzer Teaser", parsed["description"])

    def test_deutsches_museum_preserves_admission_included_semantics(self):
        detail = """
        <div class="event-detail-text"><p>Programmierwerkstatt für Kinder.</p></div>
        <section><h2>Kosten</h2><li>Die Teilnahme ist im Museumseintritt enthalten.</li></section>
        """

        parsed = deutsches_museum_bonn._detail_description(detail, {})

        self.assertIn("Teilnahme im Museumseintritt enthalten", parsed["description"])
        self.assertNotIn("Eintritt frei", parsed["description"])

    def test_deutsches_museum_fetch_discovers_current_ajax_endpoint(self):
        page = '<div data-ajaxUri="/bonn/programm/ems/indices.html?x=1&amp;y=2" data-ajax-indices></div>'
        cards = """
        <article class="events-teaser__content">
          <h3 class="events-teaser__content-title"><a href="/bonn/programm/veranstaltung/test"><span>Testführung</span></a></h3>
          <p>Eine öffentliche Führung.</p><time datetime="2026-08-22 13:00">13:00 bis 14:00 Uhr</time>
        </article>
        """
        with patch("nrw_events.common.fetch_url", side_effect=[page, cards]) as fetch:
            events = deutsches_museum_bonn.fetch()

        self.assertEqual(len(events), 1)
        self.assertEqual(fetch.call_args_list[1].args[0], "https://www.deutsches-museum.de/bonn/programm/ems/indices.html?x=1&y=2")

    def test_deutsches_museum_fetch_reports_structural_parser_drift(self):
        page = '<div data-ajaxUri="/bonn/programm/ems/indices.html?x=1" data-ajax-indices></div>'
        cards = '<article class="events-teaser__content"><h3>Redesigned card</h3></article>'
        with patch("nrw_events.common.fetch_url", side_effect=[page, cards]), patch(
            "nrw_events.common.log_source_error"
        ) as log_error:
            events = deutsches_museum_bonn.fetch()

        self.assertEqual(events, [])
        log_error.assert_called_once()
        self.assertEqual(log_error.call_args.kwargs["source_id"], "deutsches-museum-bonn")

    def test_new_primary_sources_are_registered(self):
        self.assertIn("Museum Koenig Bonn", SOURCES)
        self.assertIn("Deutsches Museum Bonn", SOURCES)
        self.assertIn("Haus der Geschichte Begleitungen", SOURCES)
        self.assertIs(SOURCES["Kunstmuseum Bonn"], kunstmuseum_bonn.fetch)

    def test_kunstmuseum_record_deduplicates_bonn_calendar_copy(self):
        listing = """
        <a href="https://www.kunstmuseum-bonn.de/de/besuch/kalender/atelier-am-sonntag-144/">
          <figure><figcaption class="teaser-caption">
            <p class="teaser-date">So. 23.08.2026, 11:15 Uhr</p>
            <h4 class="teaser-title">Atelier am Sonntag</h4>
            <p class="teaser-meta">Workshop</p>
          </figcaption></figure>
        </a>
        """
        [direct] = kunstmuseum_bonn.events_from_html(listing)
        municipal = common.make_event(
            "Atelier am Sonntag",
            datetime(2026, 8, 23, 11, 15),
            None,
            "Kunstmuseum Bonn",
            "Bonn",
            "Workshop im Kunstmuseum Bonn.",
            "https://www.bonn.de/veranstaltungskalender/atelier-am-sonntag.php",
            "Bonn.de Events",
            "Workshop",
            0.95,
            "11:15",
            all_day=False,
        )

        events = report.deduplicate([municipal, direct])

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["source"], "Kunstmuseum Bonn")
        self.assertEqual(events[0]["source_id"], "kunstmuseum-bonn")
        self.assertTrue(events[0]["link"].startswith("https://www.kunstmuseum-bonn.de/"))

    def test_bonn_copy_promotes_kunstmuseum_exhibition_primary_source(self):
        municipal_url = (
            "https://www.bonn.de/veranstaltungskalender/veranstaltungen/"
            "hauptkalender/kunstmuseum/kunstmuseum-i-feel-you.php"
        )
        primary_url = "https://www.kunstmuseum-bonn.de/de/ausstellungen/ifeelyou/"
        context = bonn._parse_detail_context(
            f'<a href="{primary_url}" target="_blank">Kunstmuseum Bonn</a>'
        )
        event = {"link": municipal_url, "source_links": []}

        promoted = bonn._apply_detail_source_link(event, context)

        self.assertEqual(promoted["link"], primary_url)
        self.assertEqual(promoted["link_kind"], "detail")
        self.assertEqual(promoted["source_links"], [municipal_url, primary_url])

    def test_primary_museum_record_deduplicates_bonn_calendar_copy(self):
        direct = common.make_event(
            "Öffentliche Familienführung",
            datetime(2026, 8, 2, 14),
            None,
            "Museum Koenig Bonn",
            "Bonn",
            "Führung für Familien. Kostenlos.",
            "https://bonn.leibniz-lib.de/de/veranstaltungen/familienfuehrung.html",
            "Museum Koenig Bonn",
            "Führung Museum",
            1.0,
            "14:00",
            all_day=False,
        )
        municipal = common.make_event(
            "Öffentliche Familienführung",
            datetime(2026, 8, 2, 14),
            None,
            "Museum Koenig Bonn",
            "Bonn",
            "Führung.",
            "https://www.bonn.de/veranstaltungskalender/familienfuehrung.php",
            "Bonn.de Events",
            "Führung",
            0.95,
            "14:00",
            all_day=False,
        )

        events = report.deduplicate([municipal, direct])

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["source"], "Museum Koenig Bonn")
        self.assertTrue(events[0]["link"].startswith("https://bonn.leibniz-lib.de/"))

    def test_bonn_fallback_never_replaces_primary_source_content(self):
        direct = common.make_event(
            "Öffentliche Familienführung",
            datetime(2026, 8, 2, 14),
            None,
            "Museum Koenig Bonn",
            "Bonn",
            "Kurzer Primärtext des Museums.",
            "https://bonn.leibniz-lib.de/de/veranstaltungen/familienfuehrung.html",
            "Museum Koenig Bonn",
            "Führung Museum",
            1.0,
            "14:00",
            all_day=False,
        )
        municipal = common.make_event(
            "Öffentliche Familienführung",
            datetime(2026, 8, 2, 14),
            None,
            "Museum Koenig Bonn",
            "Bonn",
            "Sehr viel längerer Bonn-Kalendertext, der nicht übernommen werden darf.",
            "https://www.bonn.de/veranstaltungskalender/familienfuehrung.php",
            "Bonn.de Events",
            "Führung",
            0.95,
            "14:00",
            all_day=False,
        )
        direct["source_id"] = "museum-koenig-bonn"
        municipal["source_id"] = "bonn-de-events"
        municipal["ai_summary"] = "Aus Bonn-Material erzeugte Zusammenfassung."

        [event] = report.deduplicate([municipal, direct])

        self.assertEqual(event["source_id"], "museum-koenig-bonn")
        self.assertEqual(event["description"], "Kurzer Primärtext des Museums.")
        self.assertEqual(event["description_source"], "scraped")
        self.assertEqual(event.get("ai_summary", ""), "")

    def test_dedup_does_not_turn_free_tour_plus_paid_entry_into_free_admission(self):
        direct = common.make_event(
            "Öffentliche Familienführung",
            datetime(2026, 8, 2, 14), None, "Museum Koenig Bonn", "Bonn",
            "Teilnahme an der Führung ohne Aufpreis; Museumseintritt fällt zusätzlich an.",
            "https://bonn.leibniz-lib.de/de/veranstaltungen/familienfuehrung.html",
            "Museum Koenig Bonn", "Führung Museum", 1.0, "14:00", all_day=False,
        )
        municipal = common.make_event(
            "Öffentliche Familienführung",
            datetime(2026, 8, 2, 14), None, "Museum Koenig Bonn", "Bonn",
            "Kostenlose Führung für Familien.",
            "https://www.bonn.de/veranstaltungskalender/familienfuehrung.php",
            "Bonn.de Events", "Führung", 0.95, "14:00", all_day=False,
        )

        event = report.deduplicate([municipal, direct])[0]

        self.assertEqual(event["source"], "Museum Koenig Bonn")
        self.assertEqual(event["price"], "")
        self.assertIn("Museumseintritt fällt zusätzlich an", event["description"])

    def test_dedup_uses_shared_paid_museum_predicates(self):
        phrases = (
            "Der Museumseintritt ist zu bezahlen.",
            "Der Museumseintritt muss entrichtet werden.",
            "Der reguläre Museumseintritt wird erhoben.",
            "Zuzüglich Museumseintritt.",
        )
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                self.assertTrue(report._has_separate_admission_charge({
                    "description": f"Die Führung ist kostenlos. {phrase}",
                    "price": "kostenlos",
                }))


if __name__ == "__main__":
    unittest.main()
