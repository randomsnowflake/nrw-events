"""Regression for the two published Marktplatz Gute Geschaefte records."""
import unittest

from nrw_events import report
from nrw_events.identity import event_id


class BonnJetztDedupTests(unittest.TestCase):
    def setUp(self):
        self.primary = {
            'title': '15. Marktplatz Gute Geschäfte', 'city': 'Bonn',
            'venue': 'Altes Rathaus Bonn', 'date': '2026-09-18', 'start_date': '2026-09-18',
            'end_date': '2026-09-18', 'start_at': '2026-09-18T15:00+02:00',
            'end_at': '2026-09-18T17:00+02:00', 'time': '15:00–17:00',
            'source': 'Bonn.de Events', 'source_id': 'bonn-de-events', 'score': 1,
            'category_key': 'market', 'description': '', 'price': '',
            'link': 'https://www.bonn.de/veranstaltungskalender/veranstaltungen/hauptkalender/extern/15.Marktplatz-Gute-Geschaefte.php',
        }
        self.secondary = {**self.primary, 'title': '15. Marktplatz "Gute Geschäfte"',
            'venue': 'Bonn, Altes Rathaus', 'start_at': '2026-09-18T14:30+02:00',
            'time': '14:30–17:00', 'source': 'Bonn.jetzt', 'source_id': 'bonn-jetzt',
            'category_key': 'lecture', 'score': 100,
            'link': 'https://bonn.jetzt/event/15-marktplatz-gute-geschafte'}

    def test_primary_wins_in_both_orders_and_old_identity_survives(self):
        for rows in ([self.primary, self.secondary], [self.secondary, self.primary]):
            with self.subTest(order=rows[0]['source']):
                [winner] = report.deduplicate(rows)
                for field in ('source', 'link', 'start_at', 'end_at', 'time', 'category_key'):
                    self.assertEqual(winner[field], self.primary[field])
                self.assertEqual(event_id(winner), event_id(self.primary))
                self.assertIn(event_id(self.secondary), winner['previous_event_ids'])

    def test_different_occurrences_survive(self):
        for patch in (
            {'start_at': '2026-09-18T14:29+02:00'},
            {'end_at': '2026-09-18T18:00+02:00'},
            {'end_at': ''},
            {'city': 'Köln'}, {'venue': ''}, {'venue': 'Rheinaue'},
            {'title': '16. Marktplatz Gute Geschäfte'},
            {'start_date': '2026-09-19', 'end_date': '2026-09-19'},
            {'source': 'Other organizer'},
            {'venue_address': 'Markt 100'},
        ):
            with self.subTest(patch=patch):
                self.assertEqual(len(report.deduplicate([
                    {**self.primary, 'venue_address': 'Markt 2'},
                    {**self.secondary, **patch},
                ])), 2)

    def test_unique_secondary_event_survives(self):
        self.assertEqual(len(report.deduplicate([self.secondary])), 1)

    def test_other_aggregator_wins_even_with_lower_score(self):
        primary = {**self.primary, 'source': 'Meetup', 'start_at': self.secondary['start_at']}
        for rows in ([primary, self.secondary], [self.secondary, primary]):
            [winner] = report.deduplicate(rows)
            self.assertEqual(winner['source'], 'Meetup')
