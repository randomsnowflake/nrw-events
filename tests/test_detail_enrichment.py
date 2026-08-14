import json
import unittest
from unittest.mock import patch

from nrw_events import detail_enrichment, richtext


class DetailEnrichmentTests(unittest.TestCase):
    def event(self, **overrides):
        event_date = detail_enrichment.common.TODAY.strftime("%Y-%m-%d")
        return {
            "title": "Workshop: Architekturfotografie",
            "date": event_date,
            "start_date": event_date,
            "end_date": event_date,
            "description": "Kurzer Teaser.",
            "description_html": "<p>Kurzer Teaser.</p>",
            "price": "",
            "venue": "Historisches Archiv",
            "venue_address": "",
            "link": "https://www.stadt-koeln.de/leben-in-koeln/veranstaltungen/daten/40109/index.html",
            "source": "Köln Open Data",
            **overrides,
        }

    def test_extracts_full_semantic_copy_and_sibling_logistics(self):
        document = """
        <main itemtype="http://schema.org/Event" itemscope>
          <span itemprop="price">Kostenfrei</span>
          <span itemprop="age">Ab 16 Jahren</span>
          <div><strong>Anmeldung:</strong><span>Bitte bis 25. August per E-Mail anmelden.</span></div>
          <div itemprop="description"><div class="tinyblock">
            <p>Der ausführliche erste Absatz erklärt Architektur, Materialien und Kontraste.</p>
            <p>Bitte bringen Sie eine Kamera oder ein Handy mit.</p>
            <p><strong>Mit:</strong> Michael Albers</p>
          </div></div>
          <div itemprop="location" itemscope itemtype="http://schema.org/Place">
            <span itemprop="name">Historisches Archiv mit Rheinischem Bildarchiv</span>
            <span itemprop="streetAddress">Eifelwall 5</span>
            <span itemprop="postalCode">50674</span>
            <span itemprop="addressLocality">Köln</span>
          </div>
        </main>
        """

        context = detail_enrichment.extract_detail_context(document, self.event())

        self.assertIn("ausführliche erste Absatz", context["description"])
        self.assertIn("Bitte bringen", context["description"])
        self.assertIn("Michael Albers", context["description"])
        self.assertIn("Ab 16 Jahren", context["description"])
        self.assertIn("Bitte bis 25. August", context["description"])
        self.assertIn("<strong>Mit:</strong>", context["description_html"])
        self.assertEqual(context["price"], "Kostenfrei")
        self.assertEqual(context["venue_address"], "Eifelwall 5 50674 Köln")

    def test_visible_tribe_cost_overrides_incorrect_jsonld_currency(self):
        document = """
        <script type="application/ld+json">
        {
          "@type": "Event",
          "name": "Salsa Cubana, Bachata & Discofox Workshops",
          "offers": {"@type": "Offer", "price": "5", "priceCurrency": "USD"}
        }
        </script>
        <span class="tribe-events-cost">€5 + MVZ €5</span>
        <li class="tribe-events-meta-item">
          <span class="tribe-events-event-cost-label tribe-events-meta-label">Eintritt:</span>
          <span class="tribe-events-event-cost tribe-events-meta-value">€5 + MVZ €5</span>
        </li>
        """

        context = detail_enrichment.extract_detail_context(
            document,
            self.event(
                title="Salsa Cubana, Bachata & Discofox Workshops",
                link="https://www.salsainbonn.de/event/salsa-cubana-bachata-discofox-workshops-6/",
            ),
        )

        self.assertEqual(context["price"], "€5 + MVZ €5")

    def test_richer_detail_replaces_teaser_and_explicit_price_is_reclassified(self):
        context = {
            "description": "Eine deutlich längere, vollständige Beschreibung mit allen wichtigen Hinweisen für den Besuch.",
            "description_html": "<p>Eine deutlich längere, vollständige Beschreibung mit allen wichtigen Hinweisen für den Besuch.</p>",
            "price": "12 Euro",
            "venue": "",
            "venue_address": "Eifelwall 5 50674 Köln",
        }

        enriched = detail_enrichment.apply_detail_context(self.event(), context)

        self.assertEqual(enriched["description"], context["description"])
        self.assertEqual(enriched["description_html"], context["description_html"])
        self.assertEqual(enriched["price"], "12 Euro")
        self.assertEqual(enriched["admission_basis"], "explicit")
        self.assertEqual(enriched["venue_address"], "Eifelwall 5 50674 Köln")

    def test_richer_detail_reopens_an_unlocked_teaser_classification(self):
        source = self.event(
            title="Klassik am Rinderstall",
            category="Outdoor",
            category_key="outdoor",
            category_label="Führungen & Outdoor",
            category_confidence=0.6,
            category_reason="outdoor:source_category=outdoor",
        )
        context = {
            "description": (
                "Ein ausführlich beschriebenes Benefizkonzert mit Kammermusik und "
                "international renommierten Musikerinnen und Musikern."
            ),
            "description_html": "<p>Ein ausführlich beschriebenes Benefizkonzert mit Kammermusik und international renommierten Musikerinnen und Musikern.</p>",
            "price": "",
            "venue": "",
            "venue_address": "",
        }

        enriched = detail_enrichment.apply_detail_context(source, context)

        self.assertNotIn("category_key", enriched)
        self.assertNotIn("category_label", enriched)
        self.assertNotIn("category_confidence", enriched)
        self.assertNotIn("category_reason", enriched)

    def test_richer_detail_preserves_an_explicitly_locked_category(self):
        source = self.event(
            category="Bühne",
            category_key="stage",
            category_label="Theater & Bühne",
            category_confidence=1.0,
            category_reason="source:locked-default:stage",
        )
        context = {
            "description": "Eine deutlich längere vollständige Beschreibung des Bühnenprogramms für diesen Abend.",
            "description_html": "<p>Eine deutlich längere vollständige Beschreibung des Bühnenprogramms für diesen Abend.</p>",
            "price": "",
            "venue": "",
            "venue_address": "",
        }

        enriched = detail_enrichment.apply_detail_context(source, context)

        self.assertEqual(enriched["category_key"], "stage")

    def test_fetches_each_unique_detail_but_skips_shared_overview(self):
        unique = self.event()
        shared_one = self.event(title="Termin eins", link="https://events.example.net/kalender/")
        shared_two = self.event(title="Termin zwei", link="https://events.example.net/kalender/")
        document = '<div itemprop="description"><p>Eine vollständige und wesentlich längere Beschreibung der Veranstaltung.</p></div>'

        with patch.object(detail_enrichment.common, "fetch_detail_url", return_value=document) as fetch:
            enriched = detail_enrichment.enrich_events([unique, shared_one, shared_two])

        fetch.assert_called_once()
        self.assertIn("wesentlich längere", enriched[0]["description"])
        self.assertEqual(enriched[1]["description"], "Kurzer Teaser.")
        self.assertEqual(enriched[2]["description"], "Kurzer Teaser.")

    def test_skips_network_for_an_already_complete_event(self):
        prose = "Vollständige Beschreibung mit belastbaren Besuchsinformationen. " * 8
        complete = self.event(
            description=prose,
            description_html=f"<p>{prose}</p>",
            venue="Historisches Archiv",
        )

        with patch.object(detail_enrichment.common, "fetch_detail_url") as fetch:
            enriched = detail_enrichment.enrich_events([complete])

        fetch.assert_not_called()
        self.assertEqual(complete, enriched[0])

    def test_detail_batch_budget_keeps_unenriched_events_available(self):
        first = self.event(title="Erster Termin")
        second = self.event(
            title="Zweiter Termin",
            link="https://events.example.net/zweiter-termin",
        )
        document = '<div itemprop="description"><p>Ausführliche Veranstaltungsbeschreibung.</p></div>'

        with patch.dict("os.environ", {"NRW_EVENTS_DETAIL_BATCH_TIMEOUT_SECONDS": "90"}), patch.object(
            detail_enrichment.time, "monotonic", side_effect=[100, 100, 191]
        ), patch.object(
            detail_enrichment.common, "fetch_detail_url", return_value=document
        ) as fetch:
            enriched = detail_enrichment.enrich_events([first, second])

        fetch.assert_called_once()
        self.assertEqual("Kurzer Teaser.", enriched[1]["description"])

    def test_script_and_style_content_cannot_reach_stored_html(self):
        document = """
        <div itemprop="description">
          <p>Sichtbarer Veranstaltungstext mit ausreichender Länge für die Auswahl.</p>
          <script>alert('x')</script><style>.secret { display:none }</style>
        </div>
        """
        context = detail_enrichment.extract_detail_context(document, self.event())
        self.assertNotIn("alert", context["description_html"])
        self.assertNotIn("secret", context["description_html"])
        self.assertEqual(
            richtext.to_plain_text(context["description_html"]),
            "Sichtbarer Veranstaltungstext mit ausreichender Länge für die Auswahl.",
        )

    def test_complete_html_survives_when_searchable_plain_text_is_bounded(self):
        paragraphs = [f"Vollständiger Absatz {index} mit belegtem Veranstaltungsinhalt." for index in range(300)]
        full_html = "".join(f"<p>{paragraph}</p>" for paragraph in paragraphs)
        context = {
            "description": "\n\n".join(paragraphs),
            "description_html": full_html,
            "price": "",
            "venue": "",
            "venue_address": "",
        }

        enriched = detail_enrichment.apply_detail_context(self.event(), context)

        self.assertLessEqual(len(enriched["description"]), 8000)
        self.assertEqual(enriched["description_html"], full_html)
        self.assertIn("Vollständiger Absatz 299", enriched["description_html"])

    def test_extracts_arp_museum_content_and_labeled_free_admission(self):
        document = """
        <div class="va-content">
          <figure><a>vergrößern</a></figure>
          <p><strong>IUMA – Gartentour 2026</strong></p>
          <p>Die Singer-Songwriterin spielt ein intimes Konzert in den geheimen Gärten.</p>
          <p><strong>Kosten:</strong> kostenfrei, Hutspende erwünscht</p>
          <p><strong>Hinweis:</strong> Bitte eine Sitzgelegenheit mitbringen.</p>
          <div class="va-content-cta">Termin speichern Diese Seite teilen</div>
        </div>
        """

        context = detail_enrichment.extract_detail_context(document, self.event(title="IUMA"))

        self.assertIn("intimes Konzert", context["description"])
        self.assertIn("Sitzgelegenheit", context["description_html"])
        self.assertEqual(context["price"], "kostenfrei, Hutspende erwünscht")
        self.assertNotIn("vergrößern", context["description"])
        self.assertNotIn("Diese Seite teilen", context["description"])

    def test_extracts_shapehub_content(self):
        document = """
        <div class="shapehub-detail-description">
          <p>Auf einer geführten Wanderung werden Rebsorten und Weinbau erklärt.</p>
          <p>Im Anschluss folgt eine ausführliche Verkostung bei einem Weingut.</p>
        </div>
        """

        context = detail_enrichment.extract_detail_context(
            document, self.event(title="Wein-Entdecker-Tour"),
        )

        self.assertIn("Rebsorten", context["description"])
        self.assertIn("Verkostung", context["description_html"])

    def test_extracts_clickaround_body_price_and_address(self):
        document = """
        <body id="events_page" class="events_page_detail">
          <div class="content"><div class="description">
            <b>Adresse:</b><br>Burg Namedy, 56626 Andernach
          </div></div>
          <div class="ui attached segment">
            Erlebt ein ausführlich beschriebenes Konzert im Schlossgarten.
            <br><br><b>Preise:</b><br>22,00 € zzgl. VVK-Gebühren<br>
          </div>
        </body>
        """

        context = detail_enrichment.extract_detail_context(document, self.event(title="Jazz im Park"))

        self.assertIn("ausführlich beschriebenes Konzert", context["description"])
        self.assertEqual(context["price"], "22,00 € zzgl. VVK-Gebühren")
        self.assertEqual(context["venue_address"], "Burg Namedy, 56626 Andernach")

    def test_void_itemprop_meta_cannot_swallow_page_furniture(self):
        document = """
        <meta itemprop="description" content="Kurzer strukturierter Teaser">
        <section class="entry-content">
          <p>Der ausführliche Veranstaltungstext enthält alle wichtigen Hinweise.</p>
          <p>Ein zweiter Absatz beschreibt das Programm und die Teilnahme.</p>
        </section>
        <nav>Startseite Programm Kalender und viele fremde Folgetermine</nav>
        """

        context = detail_enrichment.extract_detail_context(document, self.event())

        self.assertIn("ausführliche Veranstaltungstext", context["description"])
        self.assertNotIn("Startseite", context["description"])
        self.assertNotIn("Folgetermine", context["description_html"])

    def test_article_body_outranks_short_description_teaser(self):
        document = """
        <div itemprop="description"><p>Kurzer Teaser zur Veranstaltung.</p></div>
        <div itemprop="articleBody">
          <p>Die vollständige Beschreibung erklärt das Programm und den Ablauf ausführlich.</p>
          <p>Neue Teilnehmende sind jederzeit willkommen.</p>
        </div>
        """

        context = detail_enrichment.extract_detail_context(document, self.event())

        self.assertIn("vollständige Beschreibung", context["description"])
        self.assertIn("jederzeit willkommen", context["description_html"])

    def test_springmaus_rich_text_and_event_price_are_extracted(self):
        document = """
        <base href="https://www.springmaus-theater.de/">
        <div class="mb-4">30,00 € / 24,20 € (ermäßigt)</div>
        <div class="MyEventButton"></div>
        <div class="rich-text mb-2">
          <p>Die vollständige Improvisationstheater-Beschreibung führt durch das Sommerspecial.</p>
          <p>Besetzungsänderungen sind möglich.</p>
        </div>
        """

        context = detail_enrichment.extract_detail_context(document, self.event())

        self.assertIn("Sommerspecial", context["description"])
        self.assertEqual(context["price"], "30,00 € / 24,20 € (ermäßigt)")

    def test_extracts_adfc_fastboot_copy_tour_facts_price_and_address(self):
        payload = {
            "tourLocations": [{
                "type": "Startpunkt",
                "street": "Bahnhof Hennef / Hennefer Wirtshaus",
                "zipCode": "53773",
                "city": "Hennef (Sieg)",
            }],
            "eventItem": {
                "title": "Über Berg und Tal von Dorf zu Dorf",
                "description": (
                    '<p>Zunächst schieben wir die Räder hinter den Bahnhof.</p>'
                    '<p>Danach führt die Tour über mehrere Dörfer zurück nach Hennef.</p>'
                ),
                "cShortDescription": (
                    "Die Feierabendtour führt über ruhige Straßen und Wirtschaftswege."
                ),
                "cTourLengthKm": 24,
                "cTourSpeedKmh": 15,
                "cTourHeight": 380,
            },
            "eventItemPrices": [{
                "groupName": "Nichtmitglieder",
                "price": 2,
                "cMemberPrice": False,
            }],
            "itemTags": [
                {"category": "Geeignet für", "tag": "Pedelec"},
                {"category": "Besondere Charakteristik /Thema", "tag": "Natur"},
            ],
        }
        encoded = json.dumps(json.dumps(payload))
        document = f"""
        <meta name="description" content="Kurzer generischer ADFC-Teaser">
        <h4>Tourdaten</h4>
        <table>
          <thead><tr>
            <th>Tourlänge</th><th>Geschwindigkeit</th>
            <th>Oberflächenqualität</th><th>Anstiege</th><th>Höhenmeter</th>
          </tr></thead>
          <tbody><tr>
            <td>24 km</td><td>15 km/h</td><td>unebener Untergrund</td>
            <td>hügelig</td><td>380 m</td>
          </tr></tbody>
        </table>
        <script type="fastboot/shoebox" id="adfc-event">{encoded}</script>
        """
        event = self.event(
            title='ADFC-Feierabendtour "Über Berg und Tal von Dorf zu Dorf"',
            description=(
                '„ADFC-Feierabendtour \"Über Berg und Tal von Dorf zu Dorf\"“ '
                "findet am 13.08.2026 statt."
            ),
            description_html="",
            venue="Bahnhof Hennef",
            link=(
                "https://touren-termine.adfc.de/radveranstaltung/"
                "197023-uber-berg-und-tal-von-dorf-zu-dorf"
            ),
            source="ADFC Hennef",
        )

        context = detail_enrichment.extract_detail_context(document, event)
        enriched = detail_enrichment.apply_detail_context(event, context)

        self.assertIn("ruhige Straßen", enriched["description"])
        self.assertIn("mehrere Dörfer", enriched["description"])
        self.assertIn("Tourlänge: 24 km", enriched["description"])
        self.assertIn("Oberflächenqualität: unebener Untergrund", enriched["description"])
        self.assertIn("Geeignet für: Pedelec", enriched["description"])
        self.assertIn("<h3>Tourdaten</h3>", enriched["description_html"])
        self.assertEqual(enriched["description_source"], "scraped")
        self.assertEqual(enriched["price"], "Nichtmitglieder: 2 €")
        self.assertEqual(enriched["admission_basis"], "explicit")
        self.assertEqual(
            enriched["venue_address"],
            "Bahnhof Hennef / Hennefer Wirtshaus, 53773 Hennef (Sieg)",
        )

    def test_malformed_adfc_fastboot_payload_falls_back_without_raising(self):
        document = """
        <meta name="description" content="Belastbarer öffentlicher Ersatztext">
        <script type="fastboot/shoebox">not-json</script>
        """

        context = detail_enrichment.extract_detail_context(
            document,
            self.event(link="https://touren-termine.adfc.de/radveranstaltung/defekt"),
        )

        self.assertEqual(context["description"], "Belastbarer öffentlicher Ersatztext")

    def test_shared_unkel_overview_is_title_bounded_and_fetched_once(self):
        document = """
        <table><tbody>
          <tr><td class="event-time"><div class="datum">12. August 2026</div></td>
            <td class="event-description"><div class="accordion_container">
              <h3 class="accordion_head">Repair-Café in Unkel<span>+</span></h3>
              <div class="accordion_body"><p>Reparaturen, Tipps und Infos für viele Dinge des Alltags.</p>
                <p>Ort: Tröötetempel in Unkel.</p>
                <div class="locationlink">Veranstaltungsort: <i><a>Tröötetempel</a></i></div>
              </div></div></td></tr>
          <tr><td class="event-time"><div class="datum">16. August 2026</div></td>
            <td class="event-description"><div class="accordion_container">
              <h3 class="accordion_head">Wein – Wandern – Naturgenuss<span>+</span></h3>
              <div class="accordion_body"><p>Eine kulturhistorisch-geologische Wanderung mit Weinprobe.</p>
                <p><strong>Kosten:</strong> 54 Euro</p>
              </div></div></td></tr>
        </tbody></table>
        """
        link = "https://rhein.info/unkel/"
        event_date = detail_enrichment.common.TODAY.strftime("%Y-%m-%d")
        repair = self.event(
            title="Repair-Café in Unkel", date=event_date,
            start_date=event_date, end_date=event_date,
            description="", description_html="", venue="", link=link,
            source="VG Unkel", source_id="vg-unkel",
        )
        wine = self.event(
            title="Wein - Wandern - Naturgenuss", date=event_date,
            start_date=event_date, end_date=event_date,
            description="", description_html="", link=link,
            source="VG Unkel", source_id="vg-unkel",
        )

        with patch.object(detail_enrichment.common, "fetch_detail_url", return_value=document) as fetch:
            enriched = detail_enrichment.enrich_events([repair, wine])

        fetch.assert_called_once()
        self.assertIn("Reparaturen, Tipps", enriched[0]["description"])
        self.assertNotIn("Weinprobe", enriched[0]["description"])
        self.assertEqual(enriched[0]["venue"], "Tröötetempel")
        self.assertIn("Weinprobe", enriched[1]["description"])
        self.assertEqual(enriched[1]["price"], "54 Euro")

    def test_extracts_shared_rheinbach_sommerkino_facts(self):
        document = """
        <h2>Sommerkino für den guten Zweck</h2>
        <p>Das Sommerkino bringt an sechs Abenden Open-Air-Kinoatmosphäre in die Rheinbacher Innenstadt.</p>
        <p>Die Erlöse fließen in soziale Projekte in Rheinbach.</p>
        <h2>Informationen zum Rheinbacher Sommerkino</h2>
        <p>Es gilt das Jugendschutzgesetz.</p>
        <p><strong>Einlass:</strong><br>Der Einlass beginnt an allen Tagen um 19:30 Uhr.</p>
        <p><strong>Kosten:</strong><br>Die Karten kosten im Vorverkauf 8 Euro. Beim Erwerb aller Filmabende sind es 40 Euro.</p>
        """

        context = detail_enrichment.extract_detail_context(
            document,
            self.event(
                title="Sommerkino Rheinbach: Ach, diese Lücke",
                link="https://www.wir-fuer-rheinbach.de/sommerkino",
                source="Wir für Rheinbach", source_id="wir-fuer-rheinbach",
            ),
        )

        self.assertIn("Open-Air-Kinoatmosphäre", context["description"])
        self.assertIn("Jugendschutzgesetz", context["description"])
        self.assertEqual(context["price"], "8 Euro im Vorverkauf")
        self.assertEqual(context["venue_address"], "Bachstraße, Rheinbach")

    def test_dein_phonzimmer_shared_page_extracts_intro_and_matching_occurrence(self):
        document = """
        <article><div class="entry-content">
          <p><a href="https://dein-phonzimmer.de">zurück zur Startseite</a></p>
          <figure><img src="poster.jpg"></figure>
          <p>Genießen Sie einen lauen Sommerabend am Rhein, bei französischer Musik zum Mitsingen.</p>
          <p>Texthefte inklusive Übersetzungen werden bereitgestellt.</p>
          <p>Bei Regen wird das Konzert unter die Konrad-Adenauer-Brücke verlegt.</p>
          <p><strong>Termine:</strong></p>
          <p><strong>19.08.2026 – „Dance &amp; Chante“</strong> – französische Chansons zum Mitsingen und Mittanzen</p>
          <p>… <a href="/playlist-19">zur Playlist vom 19.08.2026</a></p>
          <p><strong>26.08.2026 – „L'univers d'Édith Piaf“</strong> – Chansons von Édith Piaf</p>
          <figure><img src="gallery.jpg"></figure>
        </div></article>
        """

        context = detail_enrichment.extract_detail_context(
            document,
            self.event(
                title="Mitsingkonzert Französisch und Kölsch",
                start_date="2026-08-19", date="2026-08-19", end_date="2026-08-19",
                link="https://dein-phonzimmer.de/mirecourtplatzkonzert-2/",
                source="Bonn.de Events", source_id="bonn-de-events",
            ),
        )

        self.assertIn("lauen Sommerabend", context["description"])
        self.assertIn("Dance & Chante", context["description"])
        self.assertIn("Bei Regen", context["description"])
        self.assertNotIn("Édith Piaf", context["description"])
        self.assertNotIn("zurück zur Startseite", context["description"])
        self.assertNotIn("zur Playlist", context["description"])

    def test_dein_phonzimmer_shared_page_is_fetched_once_and_enriches_each_date(self):
        document = """
        <article><div class="entry-content">
          <p>Französische Musik zum Mitsingen mit bereitgestellten Textheften und Übersetzungen.</p>
          <p>Bei Regen findet das Konzert unter der Konrad-Adenauer-Brücke statt.</p>
          <p><strong>Termine:</strong></p>
          <p><strong>19.08.2026 – „Dance &amp; Chante“</strong> – französische Chansons zum Mittanzen</p>
          <p><strong>26.08.2026 – „Édith Piaf“</strong> – französische Chansons zum Mitsingen</p>
        </div></article>
        """
        link = "https://dein-phonzimmer.de/mirecourtplatzkonzert-2/"
        events = [
            self.event(title="Termin eins", start_date="2026-08-19", date="2026-08-19", end_date="2026-08-19", link=link),
            self.event(title="Termin zwei", start_date="2026-08-26", date="2026-08-26", end_date="2026-08-26", link=link),
        ]

        with patch.object(detail_enrichment.common, "event_in_window", return_value=True), patch.object(
            detail_enrichment.common, "fetch_detail_url", return_value=document,
        ) as fetch:
            enriched = detail_enrichment.enrich_events(events)

        fetch.assert_called_once()
        self.assertIn("Dance & Chante", enriched[0]["description"])
        self.assertNotIn("Édith Piaf", enriched[0]["description"])
        self.assertIn("Édith Piaf", enriched[1]["description"])
        self.assertEqual(enriched[0]["description_source"], "scraped")
        self.assertEqual(enriched[1]["description_source"], "scraped")

    def test_extracts_exact_pantheon_program_block_and_ticket_price(self):
        document = """
        <li id="t639994"><h2 class="event-title">Die Offene Bühne</h2>
          <dl class="event-ticket-detail"><dt>Tickets</dt><dd>EUR 7.00 im Vorverkauf</dd></dl>
          <div class="event-detail"><p>Mindestens sechs unterschiedliche Acts zeigen neue Nummern.</p>
            <p>Das Publikum erlebt zwei abwechslungsreiche Stunden.</p>
            <div class="event-less">Weniger Infos</div></div>
        </li>
        <li id="t643395"><h2 class="event-title">Cat Stevens Tribute</h2>
          <div class="event-detail"><p>Ein anderer Konzerttext darf nicht übernommen werden.</p></div>
        </li>
        """

        context = detail_enrichment.extract_detail_context(
            document,
            self.event(
                title="Die Offene Bühne", link="https://www.pantheon.de/programm/#t639994",
                source="Pantheon Bonn", source_id="pantheon-bonn",
            ),
        )

        self.assertIn("sechs unterschiedliche Acts", context["description"])
        self.assertIn("abwechslungsreiche Stunden", context["description"])
        self.assertNotIn("anderer Konzerttext", context["description"])
        self.assertNotIn("Weniger Infos", context["description"])
        self.assertEqual(context["price"], "7,00 € im Vorverkauf")

    def test_extracts_rathausmusik_band_copy_by_visual_position(self):
        document = """
        <div class="xr_txt Normal_text xr_s6" style="position:absolute;top:1383px">
          <span>13. August</span><span>Second Arrangement</span>
        </div>
        <div class="xr_txt Normal_text xr_s4" style="position:absolute;top:1503px">
          <span>Second Arrangement ist eine 2019 gegründete zehnköpfige Band.</span>
          <span>Sie spielt Rock, Jazz und Pop mit authentischem Bläsersatz.</span>
        </div>
        <div class="xr_txt Normal_text xr_s6" style="position:absolute;top:1915px">
          <span>20. August First Lane</span>
        </div>
        <div class="xr_txt Normal_text xr_s4" style="position:absolute;top:1979px">
          <span>First Lane ist eine Bonner Melodic-Rock-Band.</span>
        </div>
        """

        context = detail_enrichment.extract_detail_context(
            document,
            self.event(
                title="Musik auf der Rathaustreppe: Second Arrangement",
                link="http://www.rathausmusik.com/", source_id="rathausmusik",
            ),
        )

        self.assertIn("zehnköpfige Band", context["description"])
        self.assertIn("authentischem Bläsersatz", context["description"])
        self.assertNotIn("First Lane", context["description"])

    def test_extracts_eitorf_event_body_free_admission_and_venue(self):
        document = """
        <section class="section single-page"><div class="content">
          <div class="intro-text"><p>Weinfest vom 14. bis 16. August.</p></div>
          <div class="text"><p><strong>Unser Programm</strong><br>Freitag und Samstag DJ Gabor.</p>
            <p><strong>Unsere Winzer</strong><br>Weingüter aus mehreren Regionen.</p></div>
          <div class="event-page-info">
            <p class="subtitle event-place">Parkplatz vor dem Sportplatz Eitorf</p>
            <p class="subtitle event-price">Preis: freier Eintritt</p>
          </div>
        </div></section>
        """

        context = detail_enrichment.extract_detail_context(
            document,
            self.event(
                title="Weinfest in Eitorf",
                link="https://www.eitorf.de/veranstaltungen/submited-events/eitorfer-weinfest/",
                source_id="eitorf-events",
            ),
        )

        self.assertIn("DJ Gabor", context["description"])
        self.assertIn("Unsere Winzer", context["description_html"])
        self.assertEqual(context["price"], "freier Eintritt")
        self.assertEqual(context["venue"], "Parkplatz vor dem Sportplatz Eitorf")

    def test_extracts_froscon_visit_facts_without_page_scripts(self):
        document = """
        <main><article id="content" class="content">
          <h3>Ort &amp; Uhrzeit</h3>
          <p>Hochschule Bonn-Rhein-Sieg<br>Grantham-Allee 20<br>53757 Sankt Augustin</p>
          <p>Die Veranstaltung findet vom 15. bis 16. August 2026 statt.</p>
          <p>Das Programm startet am Samstag um 09:30 Uhr und am Sonntag um 10:00 Uhr.</p>
          <script>window.unrelated = "Navigation";</script>
          <h3>Tickets</h3>
          <p>Der Eintritt zur FrOSCon ist frei. Ihr braucht euch nicht zu registrieren.</p>
          <h3>Verpflegung</h3><p>Speisen und Getränke können vor Ort erworben werden.</p>
        </article></main>
        """

        context = detail_enrichment.extract_detail_context(
            document,
            self.event(title="FrOSCon in Sankt Augustin", link="https://froscon.org/info/", source_id="froscon"),
        )

        self.assertIn("Programm startet", context["description"])
        self.assertIn("nicht zu registrieren", context["description"])
        self.assertNotIn("window.unrelated", context["description_html"])
        self.assertEqual(context["price"], "kostenlos")
        self.assertEqual(context["venue"], "Hochschule Bonn-Rhein-Sieg")
        self.assertEqual(context["venue_address"], "Grantham-Allee 20, 53757 Sankt Augustin")

    def test_repeated_marabu_detail_page_enriches_each_occurrence(self):
        document = """
        <meta property="og:description" content="Eine ausführliche Theaterbeschreibung über Wahrheit, Täuschung und Manipulation für Jugendliche ab 14 Jahren.">
        """
        link = "https://www.theater-marabu.de/stueck/j-e-m-escape-at/"
        event_date = detail_enrichment.common.TODAY.strftime("%Y-%m-%d")
        events = [
            self.event(title="J.E.M. Neues Stück", date=date, start_date=date, end_date=date,
                       description="", description_html="", link=link,
                       source="Brotfabrik Bonn", source_id="brotfabrik-bonn")
            for date in (event_date, event_date)
        ]

        with patch.object(detail_enrichment.common, "fetch_detail_url", return_value=document) as fetch:
            enriched = detail_enrichment.enrich_events(events)

        fetch.assert_called_once()
        self.assertTrue(all("Täuschung und Manipulation" in event["description"] for event in enriched))

    def test_shared_klimaviertel_calendar_replaces_one_character_venues(self):
        event_date = detail_enrichment.common.TODAY.strftime("%Y-%m-%d")
        document = f"""
        <script type="application/ld+json">[
          {{"@type":"Event","name":"Ein Garten für Beuel","startDate":"{event_date}T18:00:00+02:00",
           "description":"Erster Spatenstich im Gemeinschaftsgarten.",
           "location":{{"@type":"Place","name":"Gemeinschaftsgarten Ecke Hermannstr./Feldstr.",
             "address":{{"streetAddress":"Feldstraße","postalCode":"53225","addressLocality":"Bonn"}}}}}},
          {{"@type":"Event","name":"Repair Café in der Brotfabrik","startDate":"{event_date}T15:00:00+02:00",
           "location":{{"@type":"Place","name":"Brotfabrik, Studio 6"}}}}
        ]</script>
        """
        events = [
            self.event(
                title="Ein Garten für Beuel", venue="g", link="https://klimaviertel-beuel.de/termine/",
                description="x" * 300, description_html=f"<p>{'x' * 300}</p>",
            ),
            self.event(
                title="Repair Café in der Brotfabrik", venue="x", link="https://klimaviertel-beuel.de/termine/",
                description="y" * 300, description_html=f"<p>{'y' * 300}</p>",
            ),
        ]

        with patch.object(detail_enrichment.common, "fetch_detail_url", return_value=document) as fetch:
            enriched = detail_enrichment.enrich_events(events)

        fetch.assert_called_once()
        self.assertEqual(enriched[0]["venue"], "Gemeinschaftsgarten Ecke Hermannstr./Feldstr.")
        self.assertEqual(enriched[0]["venue_address"], "Feldstraße 53225 Bonn")
        self.assertEqual(enriched[1]["venue"], "Brotfabrik, Studio 6")

    def test_shared_klimaviertel_calendar_does_not_use_a_different_occurrence(self):
        document = """
        <script type="application/ld+json">{
          "@type":"Event","name":"Ein Garten für Beuel","startDate":"2026-09-19T18:00:00+02:00",
          "location":{"@type":"Place","name":"Ein anderer Terminort"}
        }</script>
        """
        event = self.event(
            title="Ein Garten für Beuel", date="2026-08-13", start_date="2026-08-13",
            venue="g", link="https://klimaviertel-beuel.de/termine/",
        )

        context = detail_enrichment.extract_detail_context(document, event)
        enriched = detail_enrichment.apply_detail_context(event, context)

        self.assertEqual(context["venue"], "")
        self.assertEqual(enriched["venue"], "g")

    def test_meetup_and_ruhr_guide_keep_only_extracted_master_data(self):
        document = """
        <meta property="og:description" content="Redaktioneller Plattformtext, der nicht veröffentlicht werden darf.">
        <script type="application/ld+json">{
          "@context": "https://schema.org", "@type": "Event", "name": "Open Data Bonn",
          "location": {"@type": "Place", "name": "Testhalle", "address": {
            "@type": "PostalAddress", "streetAddress": "Testweg 1",
            "postalCode": "53111", "addressLocality": "Bonn"}}
        }</script>
        """
        for source, source_id, link in (
            ("Meetup", "meetup-open-data-bonn", "https://www.meetup.com/open-data-bonn/events/1/"),
            ("Ruhr-Guide", "ruhr-guide", "https://www.ruhr-guide.de/veranstaltung/open-data-bonn/"),
        ):
            with self.subTest(source=source):
                context = detail_enrichment.extract_detail_context(
                    document, self.event(title="Open Data Bonn", source=source, source_id=source_id, link=link),
                )
                self.assertEqual(context["description"], "")
                self.assertEqual(context["description_html"], "")
                self.assertEqual(context["venue"], "Testhalle")
                self.assertEqual(context["venue_address"], "Testweg 1 53111 Bonn")

    def test_unmatched_shared_unkel_page_does_not_fall_back_to_another_event(self):
        document = """
        <main class="entry-content">
          <h3 class="accordion_head">Kochkurs Español &amp; Vinos</h3>
          <div class="accordion_body"><p>Paella-Abend.</p><p><strong>Eintritt</strong>: 79 Euro</p></div>
        </main>
        """

        context = detail_enrichment.extract_detail_context(
            document,
            self.event(
                title="Konzert am Salmenfang: Los Manolos",
                link="https://rhein.info/unkel/",
                source_id="vg-unkel",
            ),
        )

        self.assertEqual(context["description"], "")
        self.assertEqual(context["price"], "")

    def test_structured_detail_replaces_a_duplicated_trailing_city(self):
        event = self.event(
            venue_address="Joseph-Schumpeter-Allee 1, Bonn Bonn",
            link="https://www.meetup.com/open-data-bonn/events/1/",
            source_id="meetup-open-data-bonn",
        )

        enriched = detail_enrichment.apply_detail_context(event, {
            "description": "",
            "description_html": "",
            "price": "",
            "venue": "",
            "venue_address": "Joseph-Schumpeter-Allee 1, Bonn",
        })

        self.assertEqual(enriched["venue_address"], "Joseph-Schumpeter-Allee 1, Bonn")


if __name__ == "__main__":
    unittest.main()
