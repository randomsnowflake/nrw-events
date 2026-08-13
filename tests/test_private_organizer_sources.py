import json
import unittest
from datetime import date, datetime
from unittest.mock import patch

from nrw_events import common, report, series
from nrw_events.sources import (
    b_future_festival,
    beethovenfest_bonn,
    bonnlive,
    fedcon_events,
    kunstrasen_bonn,
    rif_events,
)
from nrw_events.validation import canonicalize_event


def next_data(events):
    payload = {"props": {"pageProps": {"sellerPage": {"events": events}}}}
    return f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script>'


class PrivateOrganizerSourceTests(unittest.TestCase):
    def setUp(self):
        self.today = patch.object(common, "TODAY", datetime(2026, 8, 13))
        self.end = patch.object(common, "END_DATE", datetime(2027, 12, 31))
        self.today.start()
        self.end.start()
        self.addCleanup(self.end.stop)
        self.addCleanup(self.today.stop)

    def test_bonnlive_merges_exact_ticket_time_and_keeps_unticketed_event(self):
        listing = """
        <div role="listitem" class="collection-item w-dyn-item"><div class="event_component">
          <div class="event_category">Theater</div><div class="event_title">Das Dschungelbuch | Junges Theater #2</div>
          <div class="date-text date-day">30</div><div class="date-text date-month">August</div><div class="date-text">2026</div>
          <div class="event_location">Kulturgarten am Post Tower</div><a href="/events/dschungelbuch">Eventdetails</a></div></div>
        <div role="listitem" class="collection-item w-dyn-item"><div class="event_component">
          <div class="event_category">Konzerte</div><div class="event_title">CelloFellos</div>
          <div class="date-text date-day">10</div><div class="date-text date-month">September</div><div class="date-text">2026</div>
          <div class="event_location">Kulturgarten am Post Tower</div><div class="event_description"><p>Zwei Celli, acht Saiten und ein Open-Air-Konzert.</p></div>
          <a href="/events/cellofellos">Eventdetails</a></div></div>
        """
        tickets = next_data([{
            "name": "Das Dschungelbuch | Junges Theater Bonn #2",
            "start": "2026-08-30T12:00:00.000Z", "end": "2026-08-30T13:00:00.000Z",
            "startingPrice": 8, "locationCity": "Bonn",
        }])
        events = bonnlive._events_from_pages(listing, tickets)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["time"], "14:00–15:00")
        self.assertEqual(events[0]["price"], "ab 8 €")
        self.assertEqual(events[0]["series_title"], "Das Dschungelbuch | Junges Theater Bonn")
        self.assertIn("Zwei Celli", events[1]["description"])

    def test_kunstrasen_reads_page_data_price_and_cached_detail_time(self):
        payload = {"tourTeasers": [{"name": "Aktuelle Veranstaltungen", "tours": [{
            "artist": "SAVATAGE </br>13.08.2026", "title": "Prelude to Madness Summer Tour 2026",
            "url": "savatage-tickets-94.html", "minprice": "72.4",
        }, {"artist": "External </br>14.08.2026", "title": "Other", "url": "https://elsewhere.test/event"}]}]}
        html = f'<script>wlec.pageData = {json.dumps(payload)};</script>'
        detail = '<a data-eventdate="2026-08-13" aria-label="SAVATAGE, Bonn, KUNST!RASEN BONN, 19:00 Uhr, 13.08.2026">Tickets</a>'
        [event] = kunstrasen_bonn._events_from_listing(html, detail_fetcher=lambda _url: detail)
        self.assertEqual(event["title"], "Savatage")
        self.assertEqual(event["time"], "19:00")
        self.assertEqual(event["price"], "ab 72.4 €")

    def test_beethovenfest_preserves_rich_copy_status_and_excludes_berlin(self):
        items = [{
            "id": 1, "title": {"de": "Bühne frei für Beethoven"},
            "date_and_time": "2026-09-05T12:00:00+02:00",
            "description": {"de": "<p>Ein <strong>offenes</strong> Konzert.</p>"},
            "slug": {"de": "buehne-frei"}, "venue_obj": {"name": "Markt Bonn"},
            "genres": [{"title": "Vokal"}], "button_status": "free",
        }, {
            "id": 2, "title": {"de": "Gastspiel Berlin"},
            "date_and_time": "2026-09-06T12:00:00+02:00", "description": {"de": "<p>Ferntermin.</p>"},
            "slug": {"de": "berlin"}, "venue_obj": {"name": "James-Simon-Galerie, Auditorium"},
        }]
        [event] = beethovenfest_bonn._events_from_items(items)
        self.assertEqual(event["price"], "kostenlos")
        self.assertIn("<strong>offenes</strong>", event["description_html"])
        self.assertEqual(event["city"], "Bonn")

    def test_beethovenfest_fetch_is_atomic_when_pagination_is_incomplete(self):
        payload = {"count": 2, "results": [{"id": 1}], "next": None}
        with patch.object(beethovenfest_bonn.common, "fetch_json", return_value=payload), \
             patch.object(beethovenfest_bonn.common, "log_source_error") as log_error:
            self.assertEqual(beethovenfest_bonn.fetch(), [])
        log_error.assert_called_once()

    def test_rif_filters_non_bonn_dates_and_keeps_ticket_facts(self):
        html = next_data([{
            "name": "FrequenzFabrik Open-Air", "start": "2026-09-05T12:00:00.000Z",
            "end": "2026-09-05T21:00:00.000Z", "locationName": "Rheinaue Bonn",
            "locationCity": "Bonn", "startingPrice": 52.45, "url": "frequenzfabrik",
        }, {
            "name": "Köln-Party", "start": "2026-09-05T12:00:00.000Z",
            "locationName": "Tanzbrunnen", "locationCity": "Köln", "url": "koeln",
        }])
        [event] = rif_events._events_from_listing(html)
        self.assertEqual(event["city"], "Bonn")
        self.assertEqual(event["time"], "14:00–23:00")
        self.assertEqual(event["price"], "ab 52.45 €")

    def test_fedcon_parser_handles_both_brands_and_partial_fetch_failure(self):
        magic = "MagicCon 8 – triff vom 02.10. - 04.10.2026 im Maritim Hotel Bonn deine Stars."
        fedcon = "FedCon 35 – triff vom 14.05. - 16.05.2027 im Maritim Hotel Bonn deine Stars."
        self.assertEqual(fedcon_events._event_from_page(magic, "https://magic.test")["end_date"], "2026-10-04")
        self.assertEqual(fedcon_events._event_from_page(fedcon, "https://fed.test")["title"], "FedCon 35")
        with patch.object(fedcon_events.common, "fetch_url", side_effect=[magic, TimeoutError("slow")]), \
             patch.object(fedcon_events.common, "log_source_error"):
            [event] = fedcon_events.fetch()
        self.assertEqual(event["title"], "MagicCon 8")

    def test_b_future_parses_program_time_room_description_and_ticket(self):
        html = """
        <h2><time datetime="2026-10-01">01.10.2026</time></h2>
        <article class="event-list-item"><p class="event-list-item__date"><time datetime="13:00">13:00</time> – <time datetime="17:00">17:00</time></p>
        <p class="event-list-item__room">// Museum Koenig</p><p class="event-list-item__ticket"><svg><path></path></svg>Mit Festivalticket</p>
        <h3 class="event-list-item__headline">Zukunft des Journalismus</h3><p class="event-list-item__description">Ein praxisnaher Deep Dive.</p>
        <a class="event-list-item__link" href="/2026/event/zukunft">Details</a></article>
        """
        [event] = b_future_festival._events_from_program(html)
        self.assertEqual(event["time"], "13:00–17:00")
        self.assertEqual(event["venue"], "Museum Koenig Bonn")
        self.assertEqual(event["price"], "Festivalticket erforderlich")
        self.assertIn("praxisnaher", event["description"])

    def test_primary_sources_deduplicate_and_preserve_richer_copy(self):
        base = {"title": "Bläck Fööss & Cécile Quartett", "start_date": "2026-09-09", "end_date": "2026-09-09", "time": "19:30", "venue": "Kulturgarten am Post Tower", "city": "Bonn", "category": "Konzert", "score": 5}
        bonnlive_event = canonicalize_event({**base, "description": "Kurzer Hinweis.", "source": "BonnLive", "source_id": "bonnlive", "link": "https://bonn-live.com/event"})
        beethovenfest_event = canonicalize_event({**base, "description": "Ausführliche offizielle Beschreibung des gemeinsamen Konzertabends.", "source": "Beethovenfest Bonn", "source_id": "beethovenfest-bonn", "link": "https://beethovenfest.de/event", "score": 6})
        [winner] = report.deduplicate([bonnlive_event, beethovenfest_event])
        self.assertEqual(winner.source_id, "beethovenfest-bonn")
        self.assertIn("Ausführliche", winner.description)

    def test_kunstrasen_headliner_deduplicates_aggregator_guest_suffix(self):
        base = {"start_date": "2026-08-13", "end_date": "2026-08-13", "start_at": "2026-08-13T19:00+02:00", "time": "19:00", "venue": "KUNST!RASEN Bonn", "city": "Bonn", "category": "Konzert", "category_key": "concert", "score": 5, "description": "Konzert."}
        official = canonicalize_event({**base, "start_at": "", "time": "", "title": "Savatage", "source": "KUNST!RASEN Bonn", "source_id": "kunstrasen-bonn", "link": "https://tickets.kunstrasen-bonn.de/savatage"})
        aggregator = canonicalize_event({**base, "title": "Savatage - Special Guest: Nevermore", "source": "Bonn.jetzt", "source_id": "bonn-jetzt", "link": "https://bonn.jetzt/savatage"})
        [winner] = report.deduplicate([aggregator, official])
        self.assertEqual(winner.source_id, "kunstrasen-bonn")

        omd_official = canonicalize_event({**base, "title": "OMD", "source": "KUNST!RASEN Bonn", "source_id": "kunstrasen-bonn", "link": "https://tickets.kunstrasen-bonn.de/omd"})
        omd_aggregator = canonicalize_event({**base, "title": "OMD - OMD`s Summer of Hits", "source": "Bonn.jetzt", "source_id": "bonn-jetzt", "link": "https://bonn.jetzt/omd"})
        [omd_winner] = report.deduplicate([omd_aggregator, omd_official])
        self.assertEqual(omd_winner.source_id, "kunstrasen-bonn")

    def test_numbered_bonnlive_performances_form_series_but_remain_occurrences(self):
        events = []
        for number, day in ((1, "2026-08-29"), (2, "2026-08-30")):
            events.append({
                "title": f"Das Dschungelbuch | Junges Theater Bonn #{number}",
                "series_title": "Das Dschungelbuch | Junges Theater Bonn",
                "start_date": day, "end_date": day, "venue": "Kulturgarten am Post Tower",
                "city": "Bonn", "source": "BonnLive", "source_id": "bonnlive",
                "link": f"https://bonn-live.com/{number}", "score": 5, "category": "Theater",
            })
        deduped = report.deduplicate([canonicalize_event(event) for event in events])
        self.assertEqual(len(deduped), 2)
        rows, metadata, _ledger = series.enrich_events(deduped, {"schema_version": 1, "series": {}}, today=date(2026, 8, 13), generated_at="2026-08-13T12:00:00")
        self.assertEqual(len({row["series_id"] for row in rows}), 1)
        self.assertEqual(metadata[0]["occurrence_dates"], ["2026-08-29", "2026-08-30"])


if __name__ == "__main__":
    unittest.main()
