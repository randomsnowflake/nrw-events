import unittest
from datetime import datetime
from unittest.mock import patch

from nrw_events import http
from nrw_events.normalization import resolve_venue
from nrw_events.runtime import EventWindow
from nrw_events.sources import ruengsdorfer_kulturbad as source

WINDOW = EventWindow(datetime(2027, 1, 1), datetime(2027, 3, 1))
VENUE = {"venue": "Rüngsdorfer KulturBad", "address": "Rheinstraße 82", "city": "Bonn", "zip": "53179"}


def item(title, **fields):
    return {
        "title": title,
        "start_date": "2027-01-23 20:00:00",
        "end_date": "2027-01-23 22:00:00",
        "url": "https://ruengsdorfer-kulturbad.de/event/juergen-becker/",
        "description": "<p>Ein Abend mit neuem Programm und vielen Geschichten aus dem Rheinland.</p>",
        "venue": VENUE,
        **fields,
    }


class RuengsdorferKulturbadTests(unittest.TestCase):
    def parse(self, *items):
        return source._events_from_payload({"events": list(items)})

    def test_sold_out_show_stays_listed_with_status_and_ticket_range(self):
        event = self.parse(item(
            "AUSVERKAUFT!!! Kabarett im KulturBad: Jürgen Becker",
            cost="Euro23 – Euro25",
            cost_details={"currency_code": "USD", "values": ["23", "25"]},
            categories=[{"name": "Kabarett"}],
        ))[0]
        self.assertEqual(event["title"], "Kabarett im KulturBad: Jürgen Becker")
        self.assertEqual(event["availability"], "SoldOut")
        self.assertEqual(event["price"], "23–25 €")
        self.assertEqual(event["admission_basis"], "explicit")
        self.assertEqual(event["time"], "20:00–22:00")
        self.assertEqual(event["venue_address"], "Rheinstraße 82, 53179 Bonn")
        self.assertEqual(event["city"], "Bonn-Bad Godesberg")
        self.assertEqual(event["category_key"], "stage")
        self.assertEqual(event["source_id"], "ruengsdorfer-kulturbad")

    def test_schedule_prefixes_leave_title(self):
        titles = [event["title"] for event in self.parse(
            item("22.11.2026 | 11.30 Uhr: Filippa Gojo", start_date="2026-11-22 11:30:00", end_date="2026-11-22 12:30:00"),
            item("4.12 | 19.30 Uhr: Der talenTTurm", start_date="2026-12-04 19:30:00", end_date="2026-12-04 22:00:00"),
        )]
        self.assertEqual(titles, ["Filippa Gojo", "Der talenTTurm"])

    def test_donation_is_not_reported_as_free(self):
        event = self.parse(item("Literatur im KulturBad", cost="Spende", cost_details={"values": []}))[0]
        self.assertEqual(event["price"], "Spende")

    def test_source_category_overrides_keyword_guess(self):
        event = self.parse(item(
            "Tanznacht 53ONN", description="<p>Gemeinsam tanzen bis tief in die Nacht im KulturBad.</p>",
            categories=[{"name": "Tanzabend"}],
        ))[0]
        self.assertEqual(event["category_key"], "nightlife")

    def test_uncategorised_shows_use_source_title_for_category(self):
        cohen, talent = self.parse(
            item("Leonard Cohen Tribute-Abend", description="<p>Ein kreativer Dialog mit seinem Werk und seinen Songs im Bad.</p>"),
            item("Der talenTTurm – Die TalentBühne", start_date="2026-12-04 19:30:00", end_date="2026-12-04 22:00:00"),
        )
        self.assertEqual(cohen["category_key"], "concert")
        self.assertEqual(talent["category_key"], "stage")

    def test_implausible_concert_end_is_dropped(self):
        jazz, tanz = self.parse(
            item("Jazz im KulturBad: Trio", start_date="2027-02-14 11:30:00", end_date="2027-02-14 18:00:00",
                 categories=[{"name": "Jazz im KulturBad"}]),
            item("Karnevals-Tanznacht", start_date="2027-02-07 17:00:00", end_date="2027-02-07 23:00:00",
                 categories=[{"name": "Tanzabend"}]),
        )
        self.assertEqual(jazz["time"], "11:30")
        self.assertEqual(tanz["time"], "17:00–23:00")

    def test_fetch_reads_all_pages(self):
        pages = [
            {"events": [item("Abend eins")], "total_pages": 2},
            {"events": [item("Abend zwei", start_date="2027-01-24 20:00:00", end_date="2027-01-24 22:00:00")], "total_pages": 2},
        ]
        with patch.object(http, "fetch_json", side_effect=pages), patch.object(http, "_record_endpoint"), \
                patch.object(source.run_state, "runtime_window", return_value=WINDOW):
            events = source.fetch()
        self.assertEqual([event["title"] for event in events], ["Abend eins", "Abend zwei"])

    def test_all_venue_spellings_share_one_place(self):
        for name in ("Rüngsdorfer Kulturbad", "KulturBad Rüngsdorf", "Rüngsdorfer KulturBad e.V."):
            venue = resolve_venue(name, "Bonn")
            self.assertEqual(venue.venue, "Rüngsdorfer KulturBad")
            self.assertEqual(venue.venue_address, "Rheinstraße 82, 53179 Bonn")
            self.assertIsNotNone(venue.venue_latitude)

    def test_hidden_listing_is_skipped(self):
        self.assertEqual(self.parse(item("Interne Probe", hide_from_listings=True)), [])


if __name__ == "__main__":
    unittest.main()
