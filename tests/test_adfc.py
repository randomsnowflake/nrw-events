import unittest
from datetime import datetime
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from nrw_events.report import deduplicate
from nrw_events.sources import SOURCES, SOURCE_IDS, adfc_bonn
from nrw_events.validation import canonicalize_event
from tests.helpers import patch_window


LISTING_ITEM = {
    "eventItemId": "event-guid",
    "title": "Feierabendtour Bonn",
    "eventType": "Radtour",
    "beginning": "2026-09-01T16:00:00+00:00",
    "end": "2026-09-01T18:30:00+00:00",
    "cShortDescription": "Eine Tour entlang des Rheins.",
    "city": "Bonn",
    "latitude": 50.726216,
    "longitude": 7.09353,
    "cSlug": "204069-feierabendtour-bonn",
    "cStatus": "Published",
    "cUnitName": "ADFC Bonn/Rhein-Sieg",
    "cWithoutTime": False,
    "isCancelled": False,
    "tourLength": "20 - 39 km",
    "tourSpeed": "15-18 km/h",
    "organizer": "Frau Beispiel",
    "startLocation": "Poppelsdorfer Schlossweiherbrücke 53115 Bonn",
}

DETAIL_PAYLOAD = {
    "tourLocations": [{
        "position": 0,
        "type": "Startpunkt",
        "name": "Poppelsdorfer Schlossweiherbrücke",
        "street": "Meckenheimer Allee",
        "zipCode": "53115",
        "city": "Bonn",
        "latitude": 50.726216,
        "longitude": 7.09353,
        "beginning": "2026-09-01T16:00:00+00:00",
    }],
    "itemTags": [
        {"category": "Geeignet für", "tag": "Alltagsrad"},
        {"category": "Tourentyp", "tag": "Feierabendtour"},
    ],
    "eventItemPrices": [{"groupName": "Nichtmitglieder", "price": 2.0}],
    "eventItem": {
        **LISTING_ITEM,
        "description": "<p>Wir fahren gemeinsam am Rhein entlang.</p>",
        "cTourLengthKm": 28.0,
        "cTourSpeedKmh": 17.0,
        "cTourHeight": 120,
        "cUrl": "",
    },
}


class AdfcBonnSourceTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 8, 29), datetime(2026, 9, 26))

    def test_builds_canonical_event_from_detail_payload(self):
        [event] = adfc_bonn.events_from_payload(
            [LISTING_ITEM], detail_fetcher=lambda _slug: DETAIL_PAYLOAD,
        )
        canonical = canonicalize_event(event)

        self.assertEqual(canonical.source_id, "adfc-bonn")
        self.assertEqual(canonical.title, "Feierabendtour Bonn")
        self.assertEqual(canonical.start_at, "2026-09-01T18:00+02:00")
        self.assertEqual(canonical.end_at, "2026-09-01T20:30+02:00")
        self.assertEqual(canonical.venue, "Poppelsdorfer Schlossweiherbrücke")
        self.assertEqual(canonical.venue_address, "Meckenheimer Allee, 53115 Bonn")
        self.assertEqual(canonical.venue_latitude, 50.726216)
        self.assertEqual(canonical.venue_longitude, 7.09353)
        self.assertEqual(canonical.price, "Nichtmitglieder: 2 €")
        self.assertEqual(canonical.category_key, "outdoor")
        self.assertIn("Wir fahren gemeinsam", canonical.description)
        self.assertIn("Tourlänge: 28 km", canonical.description)
        self.assertIn("Feierabendtour", canonical.description)
        self.assertEqual(
            canonical.link,
            "https://touren-termine.adfc.de/radveranstaltung/204069-feierabendtour-bonn",
        )

    def test_all_day_date_is_not_shifted_by_utc_conversion(self):
        item = {
            **LISTING_ITEM,
            "title": "Fahrradklimatest startet",
            "eventType": "Termin",
            "beginning": "2026-09-01T00:00:00+00:00",
            "end": "2026-09-01T00:00:00+00:00",
            "cWithoutTime": True,
            "cSlug": "205323-fahrradklimatest-startet",
        }
        detail = {
            "eventItem": {**item, "description": "Jetzt beim Fahrradklimatest mitmachen."},
            "tourLocations": [{
                "position": 0, "type": "Startpunkt", "name": "ADFC-Geschäftsstelle",
                "street": "Breite Straße 71", "zipCode": "53111", "city": "Bonn",
                "latitude": 50.739771, "longitude": 7.096347,
            }],
            "itemTags": [], "eventItemPrices": [],
        }

        [event] = adfc_bonn.events_from_payload([item], detail_fetcher=lambda _slug: detail)

        self.assertEqual(event["start_date"], "2026-09-01")
        self.assertTrue(event["all_day"])
        self.assertEqual(event["start_at"], "")

    def test_cancelled_detail_is_retained_as_cancelled_occurrence(self):
        item = {**LISTING_ITEM, "isCancelled": True, "cStatus": "Cancelled"}
        detail = {**DETAIL_PAYLOAD, "eventItem": {**DETAIL_PAYLOAD["eventItem"], **item}}

        [event] = adfc_bonn.events_from_payload([item], detail_fetcher=lambda _slug: detail)

        self.assertEqual(event["status"], "cancelled")
        self.assertIn("abgesagt", event["description"].casefold())

    def test_same_title_and_date_with_different_start_times_remain_distinct(self):
        afternoon = {
            **LISTING_ITEM,
            "beginning": "2026-09-12T12:00:00+00:00",
            "end": "2026-09-12T15:00:00+00:00",
            "title": "Pedelec-Kurs",
            "cSlug": "180283-pedeleckurs",
        }
        morning = {
            **afternoon,
            "beginning": "2026-09-12T08:00:00+00:00",
            "end": "2026-09-12T11:00:00+00:00",
            "cSlug": "180284-pedeleckurs",
        }

        events = adfc_bonn.events_from_payload(
            [morning, afternoon],
            detail_fetcher=lambda slug: {
                **DETAIL_PAYLOAD,
                "eventItem": {
                    **DETAIL_PAYLOAD["eventItem"],
                    **(morning if slug == morning["cSlug"] else afternoon),
                },
            },
        )

        self.assertEqual(len(events), 2)
        self.assertEqual([event["time"] for event in events], ["10:00–13:00", "14:00–17:00"])
        deduplicated = deduplicate(events)
        self.assertEqual(len(deduplicated), 2)
        self.assertEqual(
            [event["time"] for event in deduplicated],
            ["10:00–13:00", "14:00–17:00"],
        )

    def test_generated_fallback_description_has_generated_provenance(self):
        listing = {**LISTING_ITEM, "cShortDescription": ""}
        detail = {
            **DETAIL_PAYLOAD,
            "eventItem": {**DETAIL_PAYLOAD["eventItem"], **listing, "description": ""},
            "itemTags": [],
        }

        [event] = adfc_bonn.events_from_payload([listing], detail_fetcher=lambda _slug: detail)

        self.assertTrue(event["description"])
        self.assertEqual(event["description_source"], "generated")

    def test_detail_failure_keeps_listing_details_and_location(self):
        with patch.object(adfc_bonn.common, "log_source_error") as log_error:
            [event] = adfc_bonn.events_from_payload(
                [LISTING_ITEM], detail_fetcher=lambda _slug: (_ for _ in ()).throw(TimeoutError()),
            )

        self.assertEqual(event["venue"], "Poppelsdorfer Schlossweiherbrücke")
        self.assertEqual(event["venue_address"], "53115 Bonn")
        self.assertIn("Eine Tour entlang des Rheins", event["description"])
        log_error.assert_called_once()

    def test_fetch_paginates_listing_and_enriches_every_item(self):
        second = {
            **LISTING_ITEM,
            "eventItemId": "second-guid",
            "title": "Zweite Tour",
            "cSlug": "205000-zweite-tour",
        }
        requested_details = []
        search_queries = []

        def fetch_json(url, **_kwargs):
            if "/search?" in url:
                query = parse_qs(urlparse(url).query)
                search_queries.append(query)
                offset = int(query["offset"][0])
                return {
                    "items": [LISTING_ITEM] if offset == 0 else [second],
                    "results": 2,
                }
            slug = url.rsplit("/", 1)[-1]
            requested_details.append(slug)
            source = LISTING_ITEM if slug == LISTING_ITEM["cSlug"] else second
            return {**DETAIL_PAYLOAD, "eventItem": {**DETAIL_PAYLOAD["eventItem"], **source}}

        with patch.object(adfc_bonn.common, "fetch_json", side_effect=fetch_json):
            events = adfc_bonn.fetch()

        self.assertEqual([event["title"] for event in events], ["Feierabendtour Bonn", "Zweite Tour"])
        self.assertEqual(requested_details, [LISTING_ITEM["cSlug"], second["cSlug"]])
        self.assertEqual([query["distance"] for query in search_queries], [["20"], ["20"]])
        self.assertEqual([query["lat"] for query in search_queries], [["50.73743"], ["50.73743"]])
        self.assertEqual([query["lng"] for query in search_queries], [["7.0982068"], ["7.0982068"]])

    def test_source_is_registered_with_stable_id(self):
        self.assertIs(SOURCES["ADFC Bonn/Rhein-Sieg"], adfc_bonn.fetch)
        self.assertEqual(SOURCE_IDS["ADFC Bonn/Rhein-Sieg"], "adfc-bonn")


if __name__ == "__main__":
    unittest.main()
