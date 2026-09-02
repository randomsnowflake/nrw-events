import json
import unittest
import urllib.error
from datetime import datetime
from email.message import Message
from unittest.mock import Mock, patch

from nrw_events import common, report, validation
from nrw_events.sources import SOURCES, bonn, bonn_districts


BEUEL_HTML = """
<div class="yel"><a href="/events/#31.07.2026"><span class="title">Green Juice Festival 2026</span><br>
<b>Fr. 31.07. – 01.08. 23:59</b> | in 17 Tagen | <a href="/map/?q=Park Neu-Vilich">Park Neu-Vilich</a><br>
<a href="https://www.green-juice.de/festival/">externer Link</a></a></div>
<div class="yel"><a href="/events/#14.08.2026"><span class="title">Kirmes Oberkassel</span><br>
<b>Fr. 14.08. – 18.08. 23:59</b> | in 31 Tagen | <a href="/map/?q=Oberkassel">Oberkassel</a><br>
Ab Freitag wird gemeinsam im Stadtteil gefeiert.<br><small>externer Link: <a href="https://example.test/kirmes">mehr</a></small></a></div>
"""

JMJ_KIRMES_HTML = """
<div class="site-navigation">Nicht als Beschreibung übernehmen.</div>
<div class="entry-content">
  <h2>Programm</h2>
  <script>window.noisyPayload = "Kirmes endet im Script";</script>
  <p><strong>Freitag, 14. August 2026</strong></p>
  <p><strong>18.00 Uhr</strong> Generalprobe und Aufstellen der Vogelstange.</p>
  <h2>Große Neuheit ab der Kirmes 2026</h2>
  <p>Ab 2026 startet die Kirmes schon am Freitag mit dem Aufstellen der Vogelstange. Der Samstag rückt stärker in den Mittelpunkt und die Saalabende finden samstags, sonntags und montags statt. Die Kirmes endet am Dienstag mit der Beerdigung des Kirmeskerls.</p>
  <p>Werbetext, der nicht Teil der Übersicht ist.</p>
</div><!-- .entry-content -->
"""

BURG_LEDE_URL = "https://www.burglede.de/veranstaltungen-2026/"
BURG_LEDE_HTML = """
<html><title>Veranstaltungen 2026 | Wasserburg in Bonn-Vilich</title>
<body>Der Verein der Freunde und Förderer der Burg Lede e.V. lädt ein.</body></html>
"""

BAD_GODESBERG_HTML = """
<article class="post-6621 kalender type-kalender">
  <h2>19 April, 2026 -</h2><h2>19 April, 2026</h2>
  <h4><a href="https://bad-godesberg.info/veranstaltungen_st/familien-flohmarkt">Familien Flohmarkt</a></h4>
</article>
"""

# Trimmed from https://bv-holzlar.de/veranstaltungen — two Elementor loop items,
# one single-day and one multi-day range.
HOLZLAR_HTML = """
<div class="elementor e-loop-item post-1897 veranstaltung type-veranstaltung status-publish">
  <div class="elementor-widget-heading"><p class="elementor-heading-title elementor-size-default">14.-15.</p></div>
  <div class="elementor-widget-icon-list"><ul class="elementor-icon-list-items">
    <li><span class="elementor-icon-list-text">August</span></li>
    <li><span class="elementor-icon-list-text">2026</span></li>
  </ul></div>
  <div class="elementor-widget-theme-post-title"><h2 class="elementor-heading-title elementor-size-default">BV Kohlkaul: Weinfest</h2></div>
  <div class="elementor-widget-icon-list"><ul class="elementor-icon-list-items">
    <li><span class="elementor-icon-list-text">Georg-Fenninger-Platz</span></li>
  </ul></div>
  <a href="https://bv-holzlar.de/veranstaltung/bv-kohlkaul-weinfest/">Mehr erfahren</a>
</div>
<div class="elementor e-loop-item post-1901 veranstaltung type-veranstaltung status-publish">
  <div class="elementor-widget-heading"><p class="elementor-heading-title elementor-size-default">04</p></div>
  <div class="elementor-widget-icon-list"><ul class="elementor-icon-list-items">
    <li><span class="elementor-icon-list-text">November</span></li>
    <li><span class="elementor-icon-list-text">2026</span></li>
  </ul></div>
  <div class="elementor-widget-theme-post-title"><h2 class="elementor-heading-title elementor-size-default">Martinszug Holzlar</h2></div>
  <div class="elementor-widget-icon-list"><ul class="elementor-icon-list-items">
    <li><span class="elementor-icon-list-text">Holzlar / Kirchwiese</span></li>
  </ul></div>
  <a href="https://bv-holzlar.de/veranstaltung/martinszug-holzlar/">Mehr erfahren</a>
</div>
"""


class BonnDistrictSourceTests(unittest.TestCase):
    def setUp(self):
        self.today = patch.object(common, "TODAY", datetime(2026, 4, 1))
        self.end = patch.object(common, "END_DATE", datetime(2026, 12, 31))
        self.today.start()
        self.end.start()

    def tearDown(self):
        self.end.stop()
        self.today.stop()

    def test_all_sources_are_registered_separately(self):
        self.assertIs(SOURCES["Bürgerverein Vilich-Müldorf"], bonn_districts.fetch_vilich_mueldorf)
        self.assertIs(SOURCES["Beuel.net"], bonn_districts.fetch_beuel)
        self.assertIs(SOURCES["Bad Godesberg Stadtmarketing"], bonn_districts.fetch_bad_godesberg)
        self.assertIs(SOURCES["Hardtberg Kultur"], bonn_districts.fetch_hardtberg)
        self.assertIs(SOURCES["BSV Roleber"], bonn_districts.fetch_roleber)
        self.assertIs(SOURCES["BV Holzlar"], bonn_districts.fetch_holzlar)

    def test_new_districts_have_resolvable_coordinates(self):
        for city in (
            "Bonn-Beuel", "Bonn-Bad Godesberg", "Bonn-Duisdorf",
            "Bonn-Oberkassel", "Bonn-Pützchen", "Bonn-Roleber",
            "Bonn-Vilich", "Bonn-Vilich-Müldorf", "Bonn-Holzlar",
        ):
            coordinates, confidence, source = common.resolve_location(city)
            self.assertIsNotNone(coordinates, city)
            self.assertEqual(confidence, "known_city")
            self.assertEqual(source, "configured_city")

    def test_beuel_parser_uses_specific_districts_and_only_master_data(self):
        events = bonn_districts.events_from_beuel_html(BEUEL_HTML)

        self.assertEqual([event["city"] for event in events], ["Bonn-Vilich", "Bonn-Oberkassel"])
        self.assertTrue(all(event["description"] for event in events))
        self.assertIn("findet", events[0]["description"])
        self.assertNotIn("gemeinsam", events[1]["description"])
        self.assertTrue(all(event["description_source"] == "generated" for event in events))
        self.assertEqual(events[0]["start_date"], "2026-07-31")
        self.assertEqual(events[0]["end_date"], "2026-08-01")
        self.assertEqual(events[0]["time"], "")
        self.assertEqual(events[0]["category_key"], "festival")

    def test_beuel_rathaus_flea_market_uses_shared_canonical_identity(self):
        html = """
        <div class="yel"><a href="/events/#26.07.2026"><span class="title">Flohmarkt</span><br>
        <b>So. 26.07.2026</b> | <a href="/map/?q=Möhneplatz">Möhneplatz</a><br>
        <a href="https://beuelhats.de/">externer Link</a></a></div>
        """

        event = bonn_districts.events_from_beuel_html(html)[0]

        self.assertEqual(event["title"], "Floh- und Trödelmarkt Beueler Rathausplatz")
        self.assertEqual(event["venue"], "Beueler Rathausplatz (Möhneplatz)")
        self.assertEqual(event["city"], "Bonn-Beuel")

    def test_beuel_combined_festival_is_kept_once(self):
        html = """
        <div class="yel"><a href="/events/#05.09.2026"><span class="title">🍻 Beuelfest und Promenadenfest</span><br>
        <b>Sa. 05.09.2026 10:00</b> | <a href="/map/?q=Möhneplatz">Möhneplatz</a><br>
        <a href="https://beuelhats.de/veranstaltungen">externer Link</a></a></div>
        <div class="yel"><a href="/events/#05.09.2026"><span class="title">🍻 Promenadenfest und Beuelfest</span><br>
        <b>Sa. 05.09. – 06.09. 18:00</b> | <a href="/map/?q=Rheinufer">Rheinufer</a><br>
        <a href="https://beuelhats.de/veranstaltungen">externer Link</a></a></div>
        """

        events = bonn_districts.events_from_beuel_html(html)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["start_date"], "2026-09-05")
        self.assertEqual(events[0]["end_date"], "2026-09-06")
        self.assertEqual(events[0]["venue"], "Rheinufer")

    def test_beuel_combined_festival_programme_items_stay_distinct(self):
        html = """
        <div class="yel"><a href="/events/#05.09.2026"><span class="title">Beuelfest und Promenadenfest: Kinderprogramm</span><br>
        <b>Sa. 05.09.2026 10:00</b> | <a href="/map/?q=Möhneplatz">Möhneplatz</a><br>
        <a href="https://beuelhats.de/veranstaltungen">externer Link</a></a></div>
        <div class="yel"><a href="/events/#05.09.2026"><span class="title">Beuelfest und Promenadenfest: Abendkonzert</span><br>
        <b>Sa. 05.09.2026 18:00</b> | <a href="/map/?q=Rheinufer">Rheinufer</a><br>
        <a href="https://beuelhats.de/veranstaltungen">externer Link</a></a></div>
        """

        events = bonn_districts.events_from_beuel_html(html)

        self.assertEqual(len(events), 2)
        self.assertEqual([event["time"] for event in events], ["10:00", "18:00"])

    def test_beuel_events_sharing_a_primary_page_are_not_generically_collapsed(self):
        html = """
        <div class="yel"><a href="/events/#05.09.2026"><span class="title">Kinderfest</span><br>
        <b>Sa. 05.09.2026 10:00</b> | <a href="/map/?q=Möhneplatz">Möhneplatz</a><br>
        <a href="https://beuelhats.de/veranstaltungen">externer Link</a></a></div>
        <div class="yel"><a href="/events/#05.09.2026"><span class="title">Abendkonzert</span><br>
        <b>Sa. 05.09.2026 18:00</b> | <a href="/map/?q=Möhneplatz">Möhneplatz</a><br>
        <a href="https://beuelhats.de/veranstaltungen">externer Link</a></a></div>
        """

        events = bonn_districts.events_from_beuel_html(html)

        self.assertEqual([event["title"] for event in events], ["Kinderfest", "Abendkonzert"])

    def test_beuel_discovery_record_is_only_kept_after_primary_source_fetch(self):
        discovered = bonn_districts.events_from_beuel_html(BEUEL_HTML)
        fetched = []

        events = bonn_districts._confirm_beuel_primary_sources(
            discovered,
            primary_fetcher=lambda url: fetched.append(url) or "<html><title>Primärquelle</title></html>",
        )

        self.assertEqual(fetched, [
            "https://www.green-juice.de/festival/",
            "https://example.test/kirmes",
        ])
        self.assertEqual([event["source"] for event in events], ["green-juice.de", "example.test"])
        self.assertTrue(all(event["source_id"] == "beuel-net" for event in events))
        self.assertTrue(all(event["source_role"] == "primary" for event in events))
        self.assertTrue(all(event["discovered_via"] == ["beuel-net"] for event in events))

    def test_burg_lede_primary_uses_validated_brightdata_once_for_duplicate_cards(self):
        discovered = bonn_districts.events_from_beuel_html(BEUEL_HTML)[0]
        discovered["link"] = BURG_LEDE_URL
        duplicate = {**discovered, "title": "Second Burg Lede programme item"}
        direct_error = urllib.error.HTTPError(
            BURG_LEDE_URL, 403, "Forbidden", Message(), None,
        )
        self.addCleanup(direct_error.close)
        bright_response = Mock()
        bright_response.status = 200
        bright_response.headers = Message()
        bright_response.read.return_value = json.dumps({
            "status_code": 200,
            "body": BURG_LEDE_HTML,
        }).encode()

        with (
            patch.dict("os.environ", {
                "BRIGHT_DATA_API_KEY": "secret-key",
                "BRIGHT_DATA_ZONE": "events-unlocker",
                "NRW_EVENTS_DETAIL_CACHE_TTL_HOURS": "0",
            }),
            patch.object(
                bonn_districts.regional_common, "fetch_html_events",
                return_value=[discovered, duplicate],
            ),
            patch.object(
                bonn_districts.common, "fetch_url", side_effect=direct_error,
            ) as direct,
            patch("nrw_events.common.urllib.request.urlopen", return_value=bright_response) as urlopen,
        ):
            events = bonn_districts.fetch_beuel()

        self.assertEqual(len(events), 2)
        event = events[0]
        self.assertEqual(event["link"], BURG_LEDE_URL)
        self.assertEqual(event["source"], "burglede.de")
        self.assertEqual(event["source_role"], "primary")
        self.assertEqual(event["discovered_via"], ["beuel-net"])
        direct.assert_called_once_with(BURG_LEDE_URL, timeout=20)
        urlopen.assert_called_once()
        payload = json.loads(urlopen.call_args.args[0].data)
        self.assertEqual(payload["url"], BURG_LEDE_URL)

    def test_burg_lede_primary_403_without_credentials_is_not_promoted(self):
        discovered = bonn_districts.events_from_beuel_html(BEUEL_HTML)[0]
        discovered["link"] = BURG_LEDE_URL
        direct_error = urllib.error.HTTPError(
            BURG_LEDE_URL, 403, "Forbidden", Message(), None,
        )
        self.addCleanup(direct_error.close)

        with (
            patch.dict("os.environ", {
                "BRIGHT_DATA_API_KEY": "",
                "BRIGHT_DATA_ZONE": "",
                "NRW_EVENTS_DETAIL_CACHE_TTL_HOURS": "0",
            }),
            patch.object(bonn_districts.regional_common, "fetch_html_events", return_value=[discovered]),
            patch.object(bonn_districts.common, "fetch_url", side_effect=direct_error),
            patch.object(bonn_districts.common, "fetch_url_with_brightdata") as brightdata,
            patch.object(bonn_districts.common, "log_source_error"),
        ):
            events = bonn_districts.fetch_beuel()

        self.assertEqual(events, [])
        brightdata.assert_not_called()

    def test_burg_lede_primary_fallback_failure_is_attempted_once_for_duplicate_cards(self):
        discovered = bonn_districts.events_from_beuel_html(BEUEL_HTML)[0]
        discovered["link"] = BURG_LEDE_URL
        duplicate = {**discovered, "title": "Second Burg Lede programme item"}
        direct_error = urllib.error.HTTPError(
            BURG_LEDE_URL, 403, "Forbidden", Message(), None,
        )
        self.addCleanup(direct_error.close)

        with (
            patch.dict("os.environ", {
                "BRIGHT_DATA_API_KEY": "secret-key",
                "BRIGHT_DATA_ZONE": "events-unlocker",
                "NRW_EVENTS_DETAIL_CACHE_TTL_HOURS": "0",
            }),
            patch.object(
                bonn_districts.regional_common, "fetch_html_events",
                return_value=[discovered, duplicate],
            ),
            patch.object(
                bonn_districts.common, "fetch_url", side_effect=direct_error,
            ) as direct,
            patch.object(
                bonn_districts.common, "fetch_url_with_brightdata",
                side_effect=RuntimeError("Bright Data failed"),
            ) as brightdata,
            patch.object(bonn_districts.common, "log_source_error"),
        ):
            events = bonn_districts.fetch_beuel()

        self.assertEqual(events, [])
        direct.assert_called_once_with(BURG_LEDE_URL, timeout=20)
        brightdata.assert_called_once()

    def test_burg_lede_primary_direct_success_does_not_use_brightdata(self):
        discovered = bonn_districts.events_from_beuel_html(BEUEL_HTML)[0]
        discovered["link"] = BURG_LEDE_URL

        with (
            patch.dict("os.environ", {"NRW_EVENTS_DETAIL_CACHE_TTL_HOURS": "0"}),
            patch.object(bonn_districts.regional_common, "fetch_html_events", return_value=[discovered]),
            patch.object(bonn_districts.common, "fetch_url", return_value=BURG_LEDE_HTML) as direct,
            patch.object(bonn_districts.common, "fetch_url_with_brightdata") as brightdata,
        ):
            [event] = bonn_districts.fetch_beuel()

        self.assertEqual(event["source_role"], "primary")
        direct.assert_called_once_with(BURG_LEDE_URL, timeout=20)
        brightdata.assert_not_called()

    def test_beuel_replaces_obsolete_nikolausmarkt_endpoint_before_fetch(self):
        html = """
        <div class="yel"><a href="/events/#27.11.2026"><span class="title">Nikolausmarkt 🎅</span><br>
        <b>Fr. 27.11. – 29.11.</b> | <a href="/map/?q=St Josef">St Josef</a><br>
        <a href="https://www.bonn.de/veranstaltungskalender/veranstaltungen/hauptkalender/Nikolausmarkt-in-Beuel.php">externer Link</a></a></div>
        """
        [discovered] = bonn_districts.events_from_beuel_html(html)
        fetched = []

        with patch.object(bonn_districts.common, "log_source_error") as log_source_error:
            [event] = bonn_districts._confirm_beuel_primary_sources(
                [discovered],
                primary_fetcher=lambda url: fetched.append(url) or "<html><title>Bundesstadt Bonn</title></html>",
            )

        self.assertEqual(fetched, [
            "https://www.bonn.de/pressemitteilungen/dezember/abwechslungsreiches-veranstaltungsjahr-2026-in-bonn.php"
        ])
        self.assertEqual(event["link"], fetched[0])
        self.assertEqual(event["source"], "Bonn district festivals (Beuel.net discovery)")
        self.assertEqual(event["source_role"], "primary")
        self.assertEqual(event["discovered_via"], ["beuel-net"])
        log_source_error.assert_not_called()

    def test_beuel_replaces_mirecourtplatz_homepage_with_primary_programme(self):
        event = {
            "title": "Mitsingkonzert Französisch und Kölsch",
            "start_date": "2026-08-26", "end_date": "2026-08-26",
            "venue": "Mirecourtplatz", "city": "Bonn-Beuel",
            "link": "https://dein-phonzimmer.de/",
        }
        fetched = []

        [confirmed] = bonn_districts._confirm_beuel_primary_sources(
            [event],
            primary_fetcher=lambda url: fetched.append(url) or "<html>Primärprogramm</html>",
        )

        self.assertEqual(fetched, [
            "https://dein-phonzimmer.de/mirecourtplatzkonzert-2/",
        ])
        self.assertEqual(confirmed["link"], fetched[0])
        self.assertEqual(confirmed["source"], "dein-phonzimmer.de")

    def test_beuel_nikolausmarkt_does_not_override_official_press_metadata(self):
        html = """
        <div class="yel"><a href="/events/#27.11.2026"><span class="title">Nikolausmarkt 🎅</span><br>
        <b>Fr. 27.11. – 29.11.</b> | <a href="/map/?q=St Josef">St Josef</a><br>
        <a href="https://www.bonn.de/veranstaltungskalender/veranstaltungen/hauptkalender/Nikolausmarkt-in-Beuel.php">externer Link</a></a></div>
        """
        [discovered] = bonn_districts.events_from_beuel_html(html)
        [promoted] = bonn_districts._confirm_beuel_primary_sources(
            [discovered],
            primary_fetcher=lambda _url: "<html><title>Bundesstadt Bonn</title></html>",
        )
        press_html = """
        <ul><li>
        Nikolausmarkt Beuel, Hermannstraße, 27. bis 29. November 2026,
        Bundesstadt Bonn
        </li></ul>
        """
        with (
            patch.object(common, "TODAY", datetime(2026, 11, 1)),
            patch.object(common, "END_DATE", datetime(2026, 12, 31)),
            patch.object(common, "fetch_url", return_value=press_html),
        ):
            [official] = bonn.fetch_press_festivals()

        [deduped] = report.deduplicate([promoted, official])

        self.assertEqual(deduped["source"], "Bonn district festivals")
        self.assertEqual(deduped["title"], "Nikolausmarkt Beuel")
        self.assertEqual(deduped["venue"], "Hermannstraße")
        self.assertEqual(deduped["discovered_via"], ["beuel-net"])

    def test_beuel_nikolausmarkt_replacement_is_limited_to_2026_occurrence(self):
        [discovered] = bonn_districts.events_from_beuel_html(BEUEL_HTML[:BEUEL_HTML.index("</div>") + 6])
        obsolete_url = (
            "https://www.bonn.de/veranstaltungskalender/veranstaltungen/"
            "hauptkalender/Nikolausmarkt-in-Beuel.php"
        )
        discovered.update({
            "link": obsolete_url,
            "start_date": "2027-11-26",
            "end_date": "2027-11-28",
        })
        fetched = []

        bonn_districts._confirm_beuel_primary_sources(
            [discovered],
            primary_fetcher=lambda url: fetched.append(url) or "<html>current occurrence</html>",
        )

        self.assertEqual(fetched, [obsolete_url])

    def test_jmj_kirmes_uses_the_confirmed_primary_overview(self):
        discovered = bonn_districts.events_from_beuel_html(BEUEL_HTML)[1]
        discovered["link"] = "https://www.jmj-online.de/"
        [event] = bonn_districts._confirm_beuel_primary_sources(
            [discovered],
            primary_fetcher=lambda _url: JMJ_KIRMES_HTML,
        )

        self.assertIn("startet die Kirmes schon am Freitag", event["description"])
        self.assertIn("Beerdigung des Kirmeskerls", event["description"])
        self.assertIn("am Dienstag", event["description"])
        self.assertNotIn("Außerordentliche Mitgliederversammlung", event["description"])
        self.assertNotIn("Werbetext", event["description"])
        self.assertNotIn("noisyPayload", event["description"])
        self.assertEqual(event["description_source"], "scraped")
        self.assertEqual(event["source_role"], "primary")
        self.assertEqual(event["discovered_via"], ["beuel-net"])

        canonical = validation.validate_event(event)

        self.assertIn("startet die Kirmes schon am Freitag", canonical.description)
        self.assertEqual(canonical.description_source, "scraped")

    def test_non_jmj_beuel_primary_stays_master_data_only(self):
        event = bonn_districts.events_from_beuel_html(BEUEL_HTML)[1]
        event["source"] = "example.test"
        event["source_id"] = "beuel-net"
        event["link"] = "https://example.test/kirmes"
        event["description"] = "Fremder redaktioneller Beschreibungstext."
        event["description_source"] = "scraped"

        canonical = validation.validate_event(event)

        self.assertNotIn("Fremder redaktioneller", canonical.description)
        self.assertEqual(canonical.description_source, "generated")

    def test_bonn_district_refinement_prefers_specific_configured_place(self):
        self.assertEqual(
            common.refine_city_from_text(
                "Bonn-Beuel", "Spielplatz Bonn Beuel, Vilich-Müldorf"
            ),
            "Bonn-Vilich-Müldorf",
        )
        self.assertEqual(
            common.refine_city_from_text("Bonn-Hardtberg", "Turnhalle in Duisdorf"),
            "Bonn-Duisdorf",
        )
        self.assertEqual(
            common.refine_city_from_text("Köln", "Ausflug nach Vilich"),
            "Köln",
        )

    def test_bad_godesberg_combines_calendar_date_with_detail_copy(self):
        descriptions = {
            "https://bad-godesberg.info/veranstaltungen_st/familien-flohmarkt":
                "Auf der Rigal'schen Wiese wird nach Herzenslust getrödelt."
        }
        events = bonn_districts.events_from_bad_godesberg_html(BAD_GODESBERG_HTML, descriptions)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["date"], "2026-04-19")
        self.assertEqual(events[0]["city"], "Bonn-Bad Godesberg")
        self.assertEqual(events[0]["venue"], "Rigal'sche Wiese")
        self.assertIn("Herzenslust", events[0]["description"])

    def test_bad_godesberg_uses_explicit_theaterplatz_but_keeps_multi_area_market_broad(self):
        self.assertEqual(
            bonn_districts._bad_godesberg_venue(
                "Street Food Festival",
                "Mehr als 20 Stände bieten ihre Speisen auf dem Theaterplatz an.",
            ),
            "Theaterplatz",
        )
        self.assertEqual(
            bonn_districts._bad_godesberg_venue(
                "Antik- und Trödelmarkt",
                "Stände stehen auf dem Theaterplatz, am Fronhof und am Michaelshof.",
            ),
            "Bad Godesberger Innenstadt",
        )

    def test_hardtberg_rest_parser_keeps_event_time_and_excerpt(self):
        raw = json.dumps([{
            "date": "2026-07-19T17:00:00",
            "link": "https://www.hardtbergkultur.de/2026/07/19/farbspuren/",
            "title": {"rendered": "Vernissage FARBspuren"},
            "excerpt": {"rendered": "<p>Eine vielfältige Auswahl neuer Arbeiten.</p>"},
            "content": {"rendered": ""},
        }])
        events = bonn_districts.events_from_hardtberg_json(raw)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["city"], "Bonn-Duisdorf")
        self.assertEqual(events[0]["time"], "17:00")
        self.assertEqual(events[0]["end_at"], "")
        self.assertEqual(events[0]["end_date"], "2026-07-19")
        self.assertIn("vielfältige Auswahl", events[0]["description"])

    def test_ical_wrappers_guarantee_a_description(self):
        empty_event = {
            "title": "Wochenmarkt", "description": "", "start_date": "2026-07-14",
            "time": "15:00", "venue": "Mühlenbachhalle", "city": "Bonn-Vilich-Müldorf",
        }
        with patch.object(bonn_districts.common, "fetch_ical", return_value=[empty_event]):
            events = bonn_districts.fetch_vilich_mueldorf()

        self.assertIn("Wochenmarkt", events[0]["description"])
        self.assertIn("Mühlenbachhalle", events[0]["description"])

    def test_roleber_replaces_low_signal_registration_copy(self):
        event = {
            "title": "Sommer: Fußballcamp", "description": "featured by Stegis Kicker",
            "start_date": "2026-07-27", "time": "08:00–16:00", "venue": "",
            "city": "Bonn-Roleber", "link": "https://bsvroleber.de/event/sommer-fussballcamp/",
            "score": 0.23,
        }
        with patch.object(bonn_districts.common, "fetch_ical", return_value=[event]), \
                patch.object(bonn_districts.common, "fetch_detail_url", return_value=""):
            events = bonn_districts.fetch_roleber()

        self.assertIn("findet", events[0]["description"])
        self.assertIn("Bonn-Roleber", events[0]["description"])
        self.assertEqual(events[0]["score"], 0.45)


    def test_holzlar_parses_single_days_and_ranges_with_german_months(self):
        events = bonn_districts.events_from_holzlar_html(HOLZLAR_HTML)

        self.assertEqual([event["title"] for event in events],
                         ["BV Kohlkaul: Weinfest", "Martinszug Holzlar"])
        self.assertEqual(events[0]["start_date"], "2026-08-14")
        self.assertEqual(events[0]["end_date"], "2026-08-15")
        self.assertEqual(events[1]["start_date"], "2026-11-04")
        self.assertEqual(events[1]["end_date"], "2026-11-04")
        self.assertEqual(events[0]["venue"], "Georg-Fenninger-Platz")
        self.assertEqual(events[1]["city"], "Bonn-Holzlar")
        self.assertEqual(events[0]["category_key"], "food")
        self.assertEqual(events[1]["category_key"], "kids")
        self.assertEqual(events[1]["link"],
                         "https://bv-holzlar.de/veranstaltung/martinszug-holzlar/")
        self.assertTrue(all(event["description"] for event in events))

    def test_holzlar_fallback_represents_range_and_external_destination(self):
        html = HOLZLAR_HTML + """
        <div class="elementor e-loop-item post-1902 veranstaltung type-veranstaltung status-publish">
          <div class="elementor-widget-heading"><p class="elementor-heading-title elementor-size-default">05</p></div>
          <div class="elementor-widget-icon-list"><ul class="elementor-icon-list-items">
            <li><span class="elementor-icon-list-text">September</span></li>
            <li><span class="elementor-icon-list-text">2026</span></li>
            <li><span class="elementor-icon-list-text">Wuppertal Schwebodrohm</span></li>
          </ul></div>
          <div class="elementor-widget-theme-post-title"><h2 class="elementor-heading-title elementor-size-default">BV Roleber-Gielgen / BV Holzlar: Herbstfahrt</h2></div>
          <a href="https://bv-holzlar.de/veranstaltung/bv-roleber-gielgen-bv-holzlar-herbstfahrt/">Mehr erfahren</a>
        </div>
        """

        events = bonn_districts.events_from_holzlar_html(html)
        weinfest = next(event for event in events if event["title"] == "BV Kohlkaul: Weinfest")
        herbstfahrt = next(event for event in events if event["title"].endswith("Herbstfahrt"))

        self.assertIn("14.08.2026 bis 15.08.2026", weinfest["description"])
        self.assertEqual(herbstfahrt["city"], "Wuppertal")
        self.assertIn("Wuppertal Schwebodrohm", herbstfahrt["description"])
        self.assertNotIn("Bonn-Holzlar", herbstfahrt["description"])

    def test_holzlar_destination_mention_does_not_move_bonn_departure(self):
        html = """
        <div class="elementor e-loop-item post-1903 veranstaltung type-veranstaltung status-publish">
          <div class="elementor-widget-heading"><p class="elementor-heading-title elementor-size-default">06</p></div>
          <div class="elementor-widget-icon-list"><ul class="elementor-icon-list-items">
            <li><span class="elementor-icon-list-text">September</span></li>
            <li><span class="elementor-icon-list-text">2026</span></li>
            <li><span class="elementor-icon-list-text">Treffpunkt Holzlar für Busfahrt nach Wuppertal</span></li>
          </ul></div>
          <div class="elementor-widget-theme-post-title"><h2 class="elementor-heading-title elementor-size-default">Tagesfahrt</h2></div>
          <a href="https://bv-holzlar.de/veranstaltung/tagesfahrt/">Mehr erfahren</a>
        </div>
        """

        [event] = bonn_districts.events_from_holzlar_html(html)

        self.assertEqual(event["city"], "Bonn-Holzlar")

    def test_holzlar_range_across_a_month_boundary_keeps_start_before_end(self):
        start, end = bonn_districts._holzlar_dates("30.-02.", "September", "2026")

        self.assertEqual(start, datetime(2026, 8, 30))
        self.assertEqual(end, datetime(2026, 9, 2))

    def test_holzlar_skips_items_without_a_usable_date(self):
        html = HOLZLAR_HTML.replace("August", "").replace("2026", "", 1)

        self.assertEqual(len(bonn_districts.events_from_holzlar_html(html)), 1)
    def test_bonn_postcodes_resolve_the_outer_stadtbezirke(self):
        for venue, expected in (
            ("Siegburger Str. 42, 53229 Bonn", "Bonn-Beuel"),
            ("Kurfürstenallee 2-3, 53177 Bonn", "Bonn-Bad Godesberg"),
            ("Stadthalle, 53123 Bonn", "Bonn-Hardtberg"),
        ):
            event = common.make_event(
                "Testtermin", datetime(2026, 9, 1), None, venue, "Bonn",
                "Beschreibung", "https://example.test/e", "Testquelle", "kultur", 1.0,
            )
            with self.subTest(venue=venue):
                self.assertEqual(event["city"], expected)

    def test_central_postcodes_and_generic_venue_words_stay_plain_bonn(self):
        # The central Stadtbezirk is itself named "Bonn", and "Zentrum" is an
        # everyday word in venue names rather than a district. 53125 spans
        # Stadtbezirke Bonn and Hardtberg and cannot be resolved from PLZ alone.
        for venue in (
            "Frongasse 8a, 53121 Bonn",
            "Unbekannter Veranstaltungsort, 53125 Bonn",
            "Max7 Zentrum, Oxfordstr. 6",
        ):
            event = common.make_event(
                "Testtermin", datetime(2026, 9, 1), None, venue, "Bonn",
                "Beschreibung", "https://example.test/e", "Testquelle", "kultur", 1.0,
            )
            with self.subTest(venue=venue):
                self.assertEqual(event["city"], "Bonn")

    def test_non_bonn_cities_are_never_rewritten(self):
        event = common.make_event(
            "Testtermin", datetime(2026, 9, 1), None, "Marktplatz, 53721 Siegburg",
            "Siegburg", "Beschreibung", "https://example.test/e", "Testquelle", "kultur", 1.0,
        )

        self.assertEqual(event["city"], "Siegburg")


if __name__ == "__main__":
    unittest.main()
