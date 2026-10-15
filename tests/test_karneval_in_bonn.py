import unittest
from dataclasses import replace
from datetime import datetime
from unittest.mock import patch

from nrw_events import event_builder, http
from nrw_events.bonn_jetzt_fallbacks import partition_bonn_jetzt_fallbacks
from nrw_events.deduplication import deduplicate
from nrw_events.identity import event_id
from nrw_events.sources import SOURCES, karneval_in_bonn
from nrw_events.validation import canonicalize_event

from tests.helpers import patch_window

_BASE = "https://www.karneval-in-bonn.de/start/Termine/newse/"


def _row(day: str, month: str, year: str, time: str, slug: str, title: str) -> str:
    # Exact alphabit list markup as served on 2026-10-13.
    return f"""
<div class="Item">
<div class='LineOdd'><span class="Information"><div class="HeadingLine">
\t<div class="DateAndTime"><span class='Date'><span class='Day'>{day}. </span><span class='Month'>{month} </span><span class='Year'>{year}</span></span><div class='Time'>{time}</div></div>
\t\t<h3 ><a href='{_BASE}{slug}.html?xz=0&amp;ci=1&amp;cs=2&amp;cc=1&amp;sd=1'>{title} </a></h3>
</div></span></div> </div>
"""


LISTING_HTML = "".join((
    _row("11", "November", "2026", "10:30",
         "Eroeffnung-der-Karnevalssession--Festausschuss-BONNER-KARNEVAL-eV__3258",
         "Eröffnung der Karnevalssession  „Tradition em Hätze, fastelovend he und jetzt!&#34;"
         "  - Festausschuss BONNER KARNEVAL e.V."),
    _row("11", "November", "2026", "19:00",
         "Sessionseroeffnung---Die-Bezirksbuergermeisterin-des-Stadtbezirks-Bonn__3260",
         "Sessionseröffnung - Die Bezirksbürgermeisterin des Stadtbezirks Bonn"),
    _row("14", "November", "2026", "18:11",
         "Kneipensitzung-Elferrat-goes-Treppchen---KG-Wiesse-Muues-eV__3823",
         "Kneipensitzung &#34;Elferrat goes Treppchen&#34; - KG Wiesse Müüs e.V."),
    _row("09", "Januar", "2027", "19:00", "Ordensfest-Out-Of-Window__3999",
         "Ordensfest - KG Außerhalb e.V."),
))


def _detail(title: str, venue: str, organizer: str, address: str, content: str) -> str:
    return f"""
<div class="EventNews Module"><div class="Item">
<h2>{title}</h2>
\t<div class="Row Location">
\t\t<label class="Label Location" for="Location">Ort</label>
\t\t<div class="Location">{venue}</div>
\t</div>
<div class="Text"><div class="Story">
\t<div class="Row Content">{content}</div>
\t\t<div class="Row Organizer"><label class="Label Organizer" for="Organizer">Veranstalter</label>
\t\t\t<div class="Organizer">{organizer}</div></div>
\t\t\t<div class="Row Adress"><label class="Label Adress" for="Adress">Adresse</label>
\t\t\t\t<div class="Adress">{address}</div></div>
</div></div></div></div>
"""


DETAILS = {
    "3258": _detail(
        "Eröffnung der Karnevalssession",
        "Marktplatz Bonn",
        "Festausschuss BONNER KARNEVAL e.V.",
        "<p>Marktplatz Bonn<br />Markt<br />53111 Bonn-Zentrum</p>",
        "<p>Eröffnung der Karnevalssession mit dem Bonner Prinzenpaar und dem "
        "Bonner Kinder-Prinzenpaar.</p>",
    ),
    "3260": _detail(
        "Sessionseröffnung", "Haus der Springmaus",
        "Die Bezirksbürgermeisterin des Stadtbezirks Bonn",
        "<p>Haus der Springmaus<br />Frongasse 8<br />53121 Bonn-Endenich</p>",
        "<p>Nur auf besondere Einladung</p>",
    ),
    "3823": _detail(
        "Kneipensitzung", "Treppchen", "KG Wiesse Müüe e.V. 11er Rat",
        "<p>Treppchen<br /><br />Weberstrasse 42, 53113 Bonn</p>",
        "<p>Eintritt frei</p>",
    ),
}


def _fetch_detail(url: str) -> str:
    return DETAILS[url.split("__", 1)[1].split(".html", 1)[0]]


class KarnevalInBonnSourceTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 11, 10), datetime(2026, 12, 8))

    def _events(self):
        with patch.object(karneval_in_bonn.detail_cache, "fetch_detail_url",
                          side_effect=lambda url, **_: _fetch_detail(url)) as fetch_detail:
            events = karneval_in_bonn.events_from_html(
                LISTING_HTML,
                detail_fetcher=lambda url: karneval_in_bonn.detail_cache.fetch_detail_url(url),
            )
        return events, fetch_detail

    def test_public_in_window_rows_become_detail_backed_events(self):
        events, fetch_detail = self._events()

        # Out-of-window rows never cost a crawl-delayed detail request.
        self.assertEqual(fetch_detail.call_count, 3)
        self.assertEqual(
            [(e["title"], e["start_date"], e["time"]) for e in events],
            [
                ("Eröffnung der Karnevalssession „Tradition em Hätze, fastelovend he und jetzt!\"",
                 "2026-11-11", "10:30"),
                ("Kneipensitzung \"Elferrat goes Treppchen\"", "2026-11-14", "18:11"),
            ],
        )
        opening, kneipe = events
        self.assertEqual(opening["venue"], "Marktplatz Bonn")
        self.assertEqual(opening["venue_address"], "Marktplatz Bonn, Markt, 53111 Bonn-Zentrum")
        self.assertEqual(opening["organizer"], "Festausschuss BONNER KARNEVAL e.V.")
        self.assertEqual(opening["source_id"], "karneval-in-bonn")
        self.assertIn("__3258.html", opening["link"])
        self.assertNotIn("&amp;", opening["link"])
        # Organizer suffix removed even when the detail page spells it differently.
        self.assertEqual(kneipe["organizer"], "KG Wiesse Müüe e.V. 11er Rat")
        self.assertEqual(kneipe["price"], "kostenlos")

    def test_category_is_festival_and_survives_canonicalization(self):
        events, _ = self._events()
        canonical = [canonicalize_event(dict(event)) for event in events]
        # "Kinder-Prinzenpaar" in the prose must not reclassify the opening as kids.
        self.assertEqual({event["category_key"] for event in canonical}, {"festival"})

    def test_bonn_de_session_opening_merges_into_organizer_record(self):
        events, _ = self._events()
        bonn_de = event_builder.make_event(
            "Sessionseröffnung Bonn", datetime(2026, 11, 11, 10, 30), datetime(2026, 11, 11, 10, 30),
            "Marktplatz", "Bonn",
            "Bühnenprogramm vor dem Alten Rathaus, um 11:11 Uhr Kanonendonner.",
            "https://www.bonn.de/veranstaltungskalender/veranstaltungen/hauptkalender/karneval/"
            "sessionseroeffnung-bonn.php",
            "Bonn.de Events", "karneval", time_text="10:30", source_id="bonn-de-events",
        )
        merged = deduplicate([canonicalize_event(dict(e)) for e in [*events, bonn_de]])
        on_11_11 = [e for e in merged if e["start_date"] == "2026-11-11"]
        self.assertEqual(len(on_11_11), 1)
        self.assertEqual(on_11_11[0]["source"], "Festausschuss Bonner Karneval")

    def _live_siblings(self, **beuel_overrides):
        # Exact sibling records of the 2026-10-15 production import.
        district = canonicalize_event({
            "title": "Karnevalseröffnung", "start_date": "2026-11-11", "end_date": "2026-11-11",
            "venue": "Markt, zwischen Rathaus und Obelisk", "city": "Bonn",
            "description": "Karnevalseröffnung, Markt, zwischen Rathaus und Obelisk, "
                           "11. November 2026, Festausschuss Bonner Karneval",
            "link": "https://www.bonn.de/pressemitteilungen/dezember/"
                    "abwechslungsreiches-veranstaltungsjahr-2026-in-bonn.php",
            "source": "Bonn district festivals", "source_id": "bonn-district-festivals",
            "category_key": "festival", "score": 1.0,
        })
        beuel = canonicalize_event({
            "title": "⭐️ Eröffnung der Karnevals-Session 🧺👸",
            "start_date": "2026-11-11", "end_date": "2026-11-11",
            "start_at": "2026-11-11T10:00+01:00", "end_at": "2026-11-11T14:00+01:00",
            "time": "10:00–14:00", "venue": "Bonner Marktplatz", "city": "Bonn-Beuel",
            "link": "https://www.karneval-in-bonn.de/", "source": "karneval-in-bonn.de",
            "source_id": "beuel-net", "discovered_via": ["beuel-net"],
            "category_key": "kids", "score": 1.0, **beuel_overrides,
        })
        return district, beuel

    def test_live_11_11_siblings_collapse_into_organizer_record(self):
        events, _ = self._events()
        [opening] = [canonicalize_event(dict(e)) for e in events if e["start_date"] == "2026-11-11"]
        district, beuel = self._live_siblings()
        for rows in ([opening, district, beuel], [beuel, district, opening]):
            kept, replaced = partition_bonn_jetzt_fallbacks(rows)
            self.assertEqual(replaced, [beuel])
            [winner] = deduplicate(kept)
            self.assertEqual(winner["source_id"], "karneval-in-bonn")
            self.assertEqual(winner["start_at"], "2026-11-11T10:30+01:00")
            # Both published sibling URLs redirect to the organizer page.
            self.assertTrue({event_id(district), event_id(beuel)} <= set(winner["previous_event_ids"]))

    def test_replaced_cards_forward_their_earlier_aliases(self):
        events, _ = self._events()
        [opening] = [canonicalize_event(dict(e)) for e in events if e["start_date"] == "2026-11-11"]
        _district, beuel = self._live_siblings(previous_event_ids=["renamed-beuel-card"])
        bonn_jetzt = canonicalize_event({
            "title": "Eröffnung der Karnevals-Session", "start_date": "2026-11-11",
            "end_date": "2026-11-11", "start_at": "2026-11-11T10:00+01:00",
            "venue": "Bonner Marktplatz", "city": "Bonn", "link": "https://bonn.jetzt/e/1",
            "source": "Bonn.jetzt", "source_id": "bonn-jetzt", "category_key": "festival",
            "score": 1.0, "previous_event_ids": ["renamed-bonn-jetzt-card"],
        })
        for rows in ([bonn_jetzt, beuel, opening], [opening, beuel, bonn_jetzt]):
            kept, replaced = partition_bonn_jetzt_fallbacks(rows)
            self.assertEqual(kept, [replace(opening, previous_event_ids=kept[0].previous_event_ids)])
            self.assertCountEqual(replaced, [bonn_jetzt, beuel])
            self.assertTrue({
                "renamed-beuel-card", event_id(beuel), "renamed-bonn-jetzt-card", event_id(bonn_jetzt),
            } <= set(kept[0].previous_event_ids))

    def test_beuel_card_stays_without_linked_site_record(self):
        events, _ = self._events()
        [opening] = [canonicalize_event(dict(e)) for e in events if e["start_date"] == "2026-11-11"]
        _district, beuel = self._live_siblings()
        for card in (
            self._live_siblings(link="https://beuelhats.de/")[1],
            self._live_siblings(start_date="2026-11-12", end_date="2026-11-12",
                                start_at="2026-11-12T10:00+01:00", end_at="2026-11-12T14:00+01:00")[1],
            self._live_siblings(title="Kinderflohmarkt")[1],
            self._live_siblings(city="Köln")[1],
        ):
            with self.subTest(card=(card.title, card.link, card.start_date, card.city)):
                _kept, replaced = partition_bonn_jetzt_fallbacks([card, opening])
                self.assertEqual(replaced, [])
        # Without the organizer record the card is still published.
        self.assertEqual(partition_bonn_jetzt_fallbacks([beuel])[1], [])

    def test_all_out_of_window_calendar_is_healthy_empty(self):
        patch_window(self, datetime(2027, 3, 1), datetime(2027, 3, 29))
        with patch.object(karneval_in_bonn.http, "fetch_url", return_value=LISTING_HTML), \
                patch.object(karneval_in_bonn.run_state, "log_source_error") as log_error:
            self.assertEqual(karneval_in_bonn.fetch(), [])
        log_error.assert_not_called()

    def test_redesigned_listing_reports_parser_empty(self):
        with patch.object(karneval_in_bonn.http, "fetch_url", return_value="<div>Termine</div>"), \
                patch.object(karneval_in_bonn.run_state, "log_source_error") as log_error:
            self.assertEqual(karneval_in_bonn.fetch(), [])
        self.assertEqual(log_error.call_args.kwargs["source_id"], "karneval-in-bonn")
        self.assertIn("parser returned no event records", str(log_error.call_args.args[1]))

    def test_host_honors_robots_crawl_delay(self):
        self.assertEqual(
            http._throttle_bucket("https://www.karneval-in-bonn.de/start/Termine/index.html"),
            ("karneval-in-bonn.de", 10.0),
        )

    def test_source_is_registered(self):
        self.assertIs(SOURCES["Festausschuss Bonner Karneval"], karneval_in_bonn.fetch)


if __name__ == "__main__":
    unittest.main()
