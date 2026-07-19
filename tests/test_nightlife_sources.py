import json
import unittest
from datetime import datetime

from scripts.nrw_events import common
from scripts.nrw_events.sources import afterjobparty, max7, rheinevents, salsainbonn
from scripts.nrw_events.validation import canonicalize_event


class NightlifeSourceTests(unittest.TestCase):
    def setUp(self):
        self.old_today = common.TODAY
        self.old_end_date = common.END_DATE
        common.TODAY = datetime(2026, 7, 19)
        common.END_DATE = datetime(2026, 9, 10)

    def tearDown(self):
        common.TODAY = self.old_today
        common.END_DATE = self.old_end_date

    def assert_canonical(self, event):
        canonical = canonicalize_event(event)
        self.assertEqual(canonical.category_key, "nightlife")
        self.assertEqual(canonical.category_label, "Nachtleben & Party")
        self.assertTrue(canonical.link.startswith("https://"))
        self.assertEqual(canonical.city, "Bonn")

    def test_max7_keeps_party_rows_and_reuses_detail_enrichment(self):
        html = """
        <div class='workshop-group-header '>Fr, 24.07.26</div>
        <div class='workshop-group-header-2 '>Max7 Zentrum, Oxfordstr. 6</div>
        <div class="workshop_or_party Workshop">
          <a href='/workshop'><div class='line3'>ab 20:00 Workshop</div></a>
        </div>
        <div class="workshop_or_party Workshop Party">
          <a href='/tanzkurse-bonn/workshop∔party/6323/freitags?interval=0'>
            <div class='line2 title'>Workshops</div>
            <div class='line3'>ab 22:00 Salsa Bachata Discofox Party</div>
          </a>
        </div>
        <div class='workshop-group-header '>Sa, 25.07.26</div>
        <div class='workshop-group-header-2 '>Max7 Zentrum, Oxfordstr. 6</div>
        <div class="workshop_or_party Workshop">
          <a href='/workshop'><div class='line3'></div></a>
        </div>
        <div class='workshop-group-header '>Fr, 31.07.26</div>
        <div class='workshop-group-header-2 '>Max7 Zentrum, Oxfordstr. 6</div>
        <div class="workshop_or_party Workshop Party">
          <a href='/tanzkurse-bonn/workshop∔party/6323/freitags?interval=1'>
            <div class='line3'>ab 22:00 Salsa Bachata Discofox Party</div>
          </a>
        </div>
        """
        detail = """
        <h2>Beschreibung</h2><div>Ab 22:00 Party in drei Räumen: Salsa,
        Bachata und Discofox. Wechselnde DJs.</div><div>PREISE:
        Partyeintritt 5€ + 5€ Verzehr</div><div class="col-lg-6">
        """
        calls = []

        def load(url):
            calls.append(url)
            return detail

        events = max7._events_from_listing(html, load)
        self.assertEqual(len(events), 2)
        self.assertEqual(len(calls), 1)
        self.assertEqual(events[0]["time"], "22:00")
        self.assertEqual(events[0]["price"], "5 € + 5 € Verzehr")
        self.assertNotIn("Workshop", events[0]["title"])
        self.assert_canonical(events[0])

    def test_afterjobparty_uses_row_description_and_filters_vouchers(self):
        def row(event_id, item, description):
            return (
                f'<tr><td id="event-row-{event_id}"><script type="application/ld+json">'
                f'{json.dumps(item)}</script><li><span><i aria-hidden>info</i>'
                f'<span>{description}</span></span></li></td></tr>'
            )

        base = {
            "@type": "MusicEvent", "startDate": "2026-07-23T17:45:00+02:00",
            "endDate": "2026-07-24T02:00:00+02:00", "eventStatus": "EventScheduled",
            "location": {"name": "KD Anleger", "address": {"addressLocality": "Bonn"},
                         "geo": {"latitude": "50.735345", "longitude": "7.107923"}},
            "offers": {"price": 30, "availability": "InStock"},
            "url": "https://afterjobparty.ticket.io/hBG9DqSM/",
        }
        html = row("voucher", {**base, "name": "GUTSCHEINE AfterJobParty Bonn"}, "Gutschein")
        html += row("party", {**base, "name": "AfterJob IBIZA auf dem Rhein mit MOGUAI"},
                    "Party auf dem Rhein mit MOGUAI, Drinks und AfterJob-Resident DJ.")
        events = afterjobparty._events_from_listing(html)
        self.assertEqual(len(events), 1)
        self.assertIn("MOGUAI", events[0]["description"])
        self.assertEqual(events[0]["price"], "ab 30 €")
        self.assertEqual(events[0]["time"], "17:45–02:00")
        self.assert_canonical(events[0])

    def test_rheinevents_parses_next_data_and_converts_utc_to_local_time(self):
        payload = {"props": {"pageProps": {"sellerPage": {"events": [{
            "name": "Barfuss am Strand Summer Closing",
            "start": "2026-09-06T12:00:00.000Z", "end": "2026-09-06T20:00:00.000Z",
            "locationName": "Bikini Beach", "locationCity": "Bonn",
            "url": "barfuss-am-strand-season-closing-dhpmfm",
            "startingPrice": 22, "saleStatus": "onSale",
            "slogan": "w/ Felix Kröcher, Format :B und Wankelmut",
        }]}}}}
        html = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script>'
        events = rheinevents._events_from_listing(html)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["time"], "14:00–22:00")
        self.assertEqual(events[0]["price"], "ab 22 €")
        self.assertIn("Felix Kröcher", events[0]["description"])
        self.assert_canonical(events[0])

    def test_salsa_in_bonn_keeps_public_dances_and_filters_meetings(self):
        payload = {"events": [
            {
                "title": "Tanzen in der Weingalerie",
                "start_date": "2026-08-03 18:00:00", "end_date": "2026-08-03 23:00:00",
                "url": "https://www.salsainbonn.de/event/tanzen-in-der-weingalerie-3/",
                "description": "<p>Salsa-Musik und gemeinsames Tanzen in der Bonngasse.</p>",
                "cost": "Eintritt frei",
                "venue": {"venue": "Weingalerie Bonn", "city": "Bonn"},
            },
            {
                "title": "Mitgliederversammlung Salsa in Bonn",
                "start_date": "2026-08-04 18:00:00", "end_date": "2026-08-04 20:00:00",
                "url": "https://www.salsainbonn.de/event/versammlung/",
                "description": "Vereinsversammlung", "venue": {"city": "Bonn"},
            },
        ]}
        events = salsainbonn._events_from_payload(payload)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["time"], "18:00–23:00")
        self.assertEqual(events[0]["price"], "kostenlos")
        self.assert_canonical(events[0])


if __name__ == "__main__":
    unittest.main()
