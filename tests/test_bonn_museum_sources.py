import unittest
from datetime import datetime
from unittest.mock import patch

from nrw_events import common, report
from nrw_events.sources import (
    SOURCES,
    deutsches_museum_bonn,
    haus_der_geschichte,
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
        self.assertEqual(events[1]["category_key"], "outdoor")
        self.assertEqual(events[0]["link"], "https://bonn.leibniz-lib.de/de/veranstaltungen/wir-lesen-vor.html")

    def test_museum_koenig_does_not_mislabel_free_tour_plus_paid_entry(self):
        detail = """
        <main><p>Jeden Sonntag zeigen wir Lieblingsorte im Museum.</p>
        <dl><dt>Preis</dt><dd>Führung kostenlos zzgl. Eintritt in das Museum</dd></dl></main>
        """
        parsed = museum_koenig._detail_description(detail, {})

        self.assertIn("regulärer Museumseintritt ist erforderlich", parsed["description"])

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

    def test_new_primary_sources_are_registered(self):
        self.assertIn("Museum Koenig Bonn", SOURCES)
        self.assertIn("Deutsches Museum Bonn", SOURCES)
        self.assertIn("Haus der Geschichte Begleitungen", SOURCES)

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


if __name__ == "__main__":
    unittest.main()
