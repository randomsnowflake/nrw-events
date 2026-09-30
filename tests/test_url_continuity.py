"""Published event URLs survive title cleanup and cross-source merges."""

import unittest

from nrw_events.identity import assign_event_ids
from nrw_events.identity_reconciliation import _reconcile_published_ids


def _event(title, source, **updates):
    event = {
        "title": title, "source": source, "source_id": source.casefold().replace(" ", "-"),
        "start_date": "2026-09-26", "end_date": "2026-09-26", "date": "2026-09-26",
        "start_at": "2026-09-26T11:00+02:00", "time": "11:00", "city": "Siegburg",
        "venue": "Abtei Michaelsberg", "link": "https://example.test/fuehrung", "link_kind": "detail",
        "status": "scheduled", "score": 1.0, "description": "", "price": "",
    }
    event.update(updates)
    return event


def _published(events, previous):
    return assign_event_ids(_reconcile_published_ids(events, {"events": previous}))


class UrlContinuityTests(unittest.TestCase):
    def test_cleaned_cancelled_title_keeps_its_published_id(self):
        prior = _event("ABGESAGT - Öffentliche Führung KSI und Abtei", "Siegburg",
                       status="cancelled", event_id="abgesagt-oeffentliche-fuehrung-ksi-und-abtei-f4acec7b47")
        [current] = _published([_event("Öffentliche Führung KSI und Abtei", "Siegburg", status="cancelled")], [prior])
        self.assertEqual(current["event_id"], prior["event_id"])

    def test_title_without_its_repeated_date_keeps_its_published_id(self):
        prior = _event("Quintetto Encuentro am 26.09.2026 im Löhrerhof", "SiteKit Hürth", event_id="quintetto-encuentro-old")
        [current] = _published([_event("Quintetto Encuentro im Löhrerhof", "SiteKit Hürth")], [prior])
        self.assertEqual(current["event_id"], "quintetto-encuentro-old")

    def test_merged_away_record_redirects_to_its_successor(self):
        winner_prior = _event("Rachel Joyce liest aus »Sommerhaus«", "Parkbuchhandlung", event_id="rachel-joyce-liest-winner",
                              venue="Rheinhotel Dreesen", link="https://example.test/park")
        folded_prior = _event("Rachel Joyce »Sommerhaus«", "Bonn.de Events", event_id="rachel-joyce-sommerhaus-folded",
                              venue="Rheinhotel Dreesen", link="https://example.test/bonn")
        winner = {key: value for key, value in winner_prior.items() if key != "event_id"}

        [current] = _published([winner], [winner_prior, folded_prior])

        self.assertEqual(current["event_id"], "rachel-joyce-liest-winner")
        self.assertIn("rachel-joyce-sommerhaus-folded", current["previous_event_ids"])

    def test_disappeared_record_is_not_attached_to_an_unrelated_event(self):
        gone = _event("Hauptausschuss", "SiteKit Hürth", event_id="hauptausschuss-old", venue="Rathaus")
        [current] = _published([_event("Orgelkonzert", "SiteKit Hürth", venue="Rathaus")], [gone])
        self.assertNotIn("hauptausschuss-old", current["previous_event_ids"])


if __name__ == "__main__":
    unittest.main()
