import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from nrw_events import common
from nrw_events.sources import bonn
from tests.helpers import patch_window


DETAIL_LINK = (
    "https://www.bonn.de/veranstaltungskalender/veranstaltungen/"
    "hauptkalender/extern/seelenklaenge.php"
)


def detail_html(description: str = "Ein Konzert mit berührenden Klängen.") -> str:
    return f"""
<div class="SP-ArticleHeader__intro SP-Intro"><p>{description}</p></div>
<script type="application/ld+json">
{{"@type":"Event","location":{{"@type":"Place","name":"Collegium Leoninum","address":{{"@type":"PostalAddress","addressLocality":"Bonn"}}}}}}
</script>
"""


class BonnDetailEnrichmentTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 7, 13), datetime(2026, 7, 27))
        bonn._reset_detail_context_cache()

    def tearDown(self):
        bonn._reset_detail_context_cache()

    def test_sparse_listing_uses_detail_description_and_venue_once(self):
        html = """
<article class="SP-Teaser">
  <a class="SP-Teaser__inner" href="/veranstaltungskalender/veranstaltungen/hauptkalender/extern/seelenklaenge.php">
    <span class="SP-Kicker__text">Musik/Konzert</span>
    <div class="SP-Scheduling"><span><span class="SP-Scheduling__date">18.07.2026</span></span></div>
    <h1 class="SP-Teaser__headline">Seelenklänge</h1>
    <div class="SP-Teaser__abstract">(Anmeldung)</div>
  </a>
</article>
"""
        context = {
            "description": "Ein Konzert mit berührenden Klängen.",
            "venue": "Collegium Leoninum",
            "city": "Bonn",
        }

        with patch.object(bonn, "_fetch_detail_context", return_value=context) as fetch_detail:
            events = bonn._calendar_listing_events_from_html(html, "Bonn.de Events")

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["description"], context["description"])
        self.assertEqual(events[0]["venue"], context["venue"])
        fetch_detail.assert_called_once_with(DETAIL_LINK)

    def test_meaningful_listing_abstract_does_not_fetch_detail(self):
        html = """
<article class="SP-Teaser">
  <a class="SP-Teaser__inner" href="/veranstaltungskalender/veranstaltungen/hauptkalender/extern/seelenklaenge.php">
    <span class="SP-Kicker__text">Musik/Konzert</span>
    <div class="SP-Scheduling"><span><span class="SP-Scheduling__date">18.07.2026</span></span></div>
    <h1 class="SP-Teaser__headline">Seelenklänge</h1>
    <div class="SP-Teaser__abstract">Ein Konzert mit berührenden Klängen.</div>
  </a>
</article>
"""

        with patch.object(bonn, "_fetch_detail_context") as fetch_detail:
            events = bonn._calendar_listing_events_from_html(html, "Bonn.de Events")

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["description"], "Ein Konzert mit berührenden Klängen.")
        fetch_detail.assert_not_called()

    def test_detail_copy_does_not_override_trusted_listing_category_or_score(self):
        html = """
<article class="SP-Teaser">
  <a class="SP-Teaser__inner" href="/veranstaltungskalender/veranstaltungen/hauptkalender/extern/Nachtwache.php">
    <span class="SP-Kicker__text">Aktion/Workshop | Ausstellung</span>
    <div class="SP-Scheduling"><span><span class="SP-Scheduling__date">14.07.2026</span></span></div>
    <h1 class="SP-Teaser__headline">Nachtwache</h1>
  </a>
</article>
"""
        context = {
            "description": (
                "Ein Escape Game im Museum für Familien und Rätselfans. "
                "Die Ausstellungsstücke erwachen nachts zu einem verborgenen Leben."
            ),
            "venue": "LVR-Landesmuseum Bonn",
            "city": "Bonn",
        }

        with patch.object(bonn, "_fetch_detail_context", return_value=context):
            events = bonn._calendar_listing_events_from_html(html, "Bonn.de Events")

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["description"], context["description"])
        self.assertEqual(events[0]["category_key"], "workshop")
        self.assertGreaterEqual(events[0]["score"], 0.4)

    def test_detail_description_is_trimmed_before_it_is_cached_or_exported(self):
        long_description = " ".join(
            [
                "Der Abend beginnt mit einer kurzen Einführung.",
                "Danach erklingen Werke verschiedener Komponisten.",
                "Das Publikum erlebt ein abwechslungsreiches Programm.",
                "Dieser letzte Satz soll nicht mehr vollständig exportiert werden.",
            ]
        )

        with patch.dict(
            "os.environ",
            {"NRW_EVENTS_BONN_DETAIL_DESCRIPTION_MAX_CHARS": "120"},
        ):
            context = bonn._parse_detail_context(detail_html(long_description))

        self.assertLessEqual(len(context["description"]), 120)
        self.assertTrue(context["description"].endswith("…"))
        self.assertNotIn("Dieser letzte Satz", context["description"])

    def test_detail_excerpt_prefers_explanatory_paragraphs_over_logistics(self):
        html = """
<div class="SP-ArticleHeader__intro SP-Intro"><p>Benefizkonzert für einen guten Zweck</p></div>
<div data-sp-table class="SP-Paragraph">
  <p>BENEFIZ-KONZERT MIT DER KÜNSTLERIN LISA SAHATQIU</p>
  <p>Datum: 14.07.2026</p>
  <p>Uhrzeit: 18:30 Uhr</p>
  <p>Lokation: Alte Kirche, Bonn</p>
  <p>Tickets: 10 Euro an der Abendkasse</p>
  <p>Anmeldung: Bitte per E-Mail anmelden.</p>
  <p>Über die Künstlerin</p>
  <p>Die junge Pianistin erhielt früh ihren ersten Klavierunterricht. Heute tritt sie bei internationalen Wettbewerben und Festivals auf.</p>
  <p>Ein weiterer sehr langer Absatz, der nicht mehr Teil des kompakten Exports sein soll.</p>
</div>
"""

        with patch.dict(
            "os.environ", {"NRW_EVENTS_BONN_DETAIL_DESCRIPTION_MAX_CHARS": "260"}
        ):
            description = bonn._parse_detail_context(html)["description"]

        self.assertIn("Die junge Pianistin", description)
        self.assertNotIn("Datum:", description)
        self.assertNotIn("Anmeldung:", description)
        self.assertNotIn("Ein weiterer sehr langer Absatz", description)
        self.assertNotRegex(description, r"\b(?:die|der|und|mit)…$")

    def test_detail_excerpt_does_not_end_on_an_unexplained_heading(self):
        html = """
<div class="SP-ArticleHeader__intro SP-Intro"><p>Ein Escape Game im Museum</p></div>
<div data-sp-table class="SP-Paragraph">
  <p>Sommerferien-Event für Familien und Rätselfans</p>
  <p>Was passiert eigentlich nachts im Museum? Diesen Sommer lässt sich das Geheimnis im Escape Game lüften.</p>
  <p>Was erwartet euch?</p>
  <p>Im Landesmuseum übernehmt ihr den nächtlichen Rundgang und löst gemeinsam überraschende Rätsel.</p>
</div>
"""

        with patch.dict(
            "os.environ", {"NRW_EVENTS_BONN_DETAIL_DESCRIPTION_MAX_CHARS": "180"}
        ):
            description = bonn._parse_detail_context(html)["description"]

        self.assertNotIn("Was erwartet euch?…", description)
        self.assertTrue(description.endswith(("lüften.", "Rätsel.", "lüften.…", "Rätsel.…")))

    def test_persistent_cache_avoids_detail_request_on_a_later_run(self):
        with tempfile.TemporaryDirectory() as cache_dir, patch.dict(
            "os.environ",
            {
                "NRW_EVENTS_CACHE_DIR": cache_dir,
                "NRW_EVENTS_DETAIL_CACHE_TTL_HOURS": "24",
            },
        ):
            with patch.object(common, "fetch_url", return_value=detail_html()) as fetch_url:
                first = bonn._fetch_detail_context(DETAIL_LINK)
            self.assertEqual(fetch_url.call_count, 1)
            self.assertEqual(first["venue"], "Collegium Leoninum")

            bonn._reset_detail_context_cache()
            with patch.object(common, "fetch_url", side_effect=AssertionError("cache miss")) as fetch_url:
                second = bonn._fetch_detail_context(DETAIL_LINK)

        self.assertEqual(first, second)
        fetch_url.assert_not_called()

    def test_persistent_cache_avoids_repeating_successful_empty_detail_page(self):
        with tempfile.TemporaryDirectory() as cache_dir, patch.dict(
            "os.environ",
            {
                "NRW_EVENTS_CACHE_DIR": cache_dir,
                "NRW_EVENTS_DETAIL_CACHE_TTL_HOURS": "24",
            },
        ):
            with patch.object(common, "fetch_url", return_value="<main>No event metadata</main>") as fetch_url:
                first = bonn._fetch_detail_context(DETAIL_LINK)
            self.assertEqual(fetch_url.call_count, 1)
            self.assertEqual(first, {"description": "", "venue": "", "city": ""})

            bonn._reset_detail_context_cache()
            with patch.object(common, "fetch_url", side_effect=AssertionError("cache miss")) as fetch_url:
                second = bonn._fetch_detail_context(DETAIL_LINK)

        self.assertEqual(first, second)
        fetch_url.assert_not_called()

    def test_persistent_cache_does_not_hide_transient_detail_failure(self):
        with tempfile.TemporaryDirectory() as cache_dir, patch.dict(
            "os.environ",
            {
                "NRW_EVENTS_CACHE_DIR": cache_dir,
                "NRW_EVENTS_DETAIL_CACHE_TTL_HOURS": "24",
            },
        ):
            with patch.object(common, "fetch_url", side_effect=TimeoutError("temporary")):
                self.assertEqual(bonn._fetch_detail_context(DETAIL_LINK), {})

            bonn._reset_detail_context_cache()
            with patch.object(common, "fetch_url", return_value=detail_html()) as fetch_url:
                refreshed = bonn._fetch_detail_context(DETAIL_LINK)

        self.assertEqual(fetch_url.call_count, 1)
        self.assertEqual(refreshed["venue"], "Collegium Leoninum")

    def test_expired_persistent_cache_is_refreshed(self):
        with tempfile.TemporaryDirectory() as cache_dir, patch.dict(
            "os.environ",
            {
                "NRW_EVENTS_CACHE_DIR": cache_dir,
                "NRW_EVENTS_DETAIL_CACHE_TTL_HOURS": "1",
            },
        ):
            with patch.object(common.time, "time", return_value=1_000), patch.object(
                common, "fetch_url", return_value=detail_html("Alte Beschreibung.")
            ):
                bonn._fetch_detail_context(DETAIL_LINK)

            bonn._reset_detail_context_cache()
            with patch.object(common.time, "time", return_value=4_601), patch.object(
                common, "fetch_url", return_value=detail_html("Neue Beschreibung.")
            ) as fetch_url:
                refreshed = bonn._fetch_detail_context(DETAIL_LINK)

        self.assertEqual(fetch_url.call_count, 1)
        self.assertEqual(refreshed["description"], "Neue Beschreibung.")

    def test_malformed_cache_payload_fails_soft_and_refetches(self):
        with tempfile.TemporaryDirectory() as cache_dir, patch.dict(
            "os.environ",
            {
                "NRW_EVENTS_CACHE_DIR": cache_dir,
                "NRW_EVENTS_DETAIL_CACHE_TTL_HOURS": "24",
            },
        ):
            Path(cache_dir, "detail-pages-bonn-detail-v1.json").write_text("[]")
            with patch.object(common, "fetch_url", return_value=detail_html()) as fetch_url:
                context = bonn._fetch_detail_context(DETAIL_LINK)

        self.assertEqual(fetch_url.call_count, 1)
        self.assertEqual(context["venue"], "Collegium Leoninum")


if __name__ == "__main__":
    unittest.main()
