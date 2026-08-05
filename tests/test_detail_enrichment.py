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


if __name__ == "__main__":
    unittest.main()
