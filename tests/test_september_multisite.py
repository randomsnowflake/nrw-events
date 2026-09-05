"""Exact reviewed occurrences must not become general locality aliases."""
import unittest
from datetime import datetime
from unittest.mock import patch

from nrw_events import common, identity
from nrw_events.sources import bonn, regional_ionas4
from nrw_events.validation import canonicalize_event


class SeptemberMultisiteTests(unittest.TestCase):
    def setUp(self):
        for name, value in [('TODAY', datetime(2026, 9, 1)), ('END_DATE', datetime(2026, 9, 30))]:
            p = patch.object(common, name, value)
            p.start()
            self.addCleanup(p.stop)

    def ionas(self, title, city, day, venue, source_id, link, end=None):
        item = {'id': '1:0', 'title': title, 'start': day + 'T11:00:00',
                'end': (end or day) + 'T18:00:00', 'allDay': False,
                'location': {'name': venue}, 'website': link}
        return canonicalize_event(regional_ionas4._events_from_items(
            [item], city, 'https://example.org/kalender/', 0.95, source_id=source_id)[0])

    def test_beuel_component_and_copy_survive_canonicalization(self):
        html = '<li>Beuel-Fest, Beuel-Zentrum, Rheinufer Beuel, 5. und 6. September 2026 Gewerbe-Gemeinschaft Beuel</li>'
        with patch.object(common, 'fetch_url', return_value=html):
            event = canonicalize_event(bonn.fetch_press_festivals()[0])
        self.assertEqual(event['venue'], 'Möhneplatz')
        self.assertEqual(event['city'], 'Bonn-Beuel')
        self.assertEqual(event['venue_address'], 'Rathausstraße, 53225 Bonn')
        self.assertEqual(event['venue_latitude'], 50.7393914)
        self.assertEqual(event['venue_longitude'], 7.1198711)
        self.assertIn('Die Karte markiert den Möhneplatz; zum gemeinsamen Fest gehört außerdem das Beueler Rheinufer.', event['description'])
        self.assertIn('Die Karte markiert', event['description_html'])
        self.assertEqual(event['source_id'], 'bonn-district-festivals')
        self.assertEqual(identity.event_id(event), 'beuel-fest-2026-09-05-6622f26bb3')
        with patch.object(common, 'fetch_url', return_value=html.replace('5. und 6.', '12. und 13.')):
            sibling = canonicalize_event(bonn.fetch_press_festivals()[0])
        self.assertNotEqual(sibling['venue'], 'Möhneplatz')

    def test_roesrath_component_is_exact_occurrence_only(self):
        link = 'https://www.roesrath.de/kalender/2026/q3/september/2026-09-06-stadtfest-und-schuetzenfest-in-roesrath/17422:0'
        event = self.ionas('Stadtfest und Schützenfest in Rösrath', 'Rösrath', '2026-09-06', 'Rösrath-Mitte', 'ionas4-r-srath', link)
        self.assertEqual(event['venue'], 'Schützenplatz')
        self.assertEqual(event['venue_latitude'], 50.8943563)
        self.assertEqual(event['venue_longitude'], 7.1828993)
        for text in ['Die Karte markiert den Schützenplatz', 'Innenstadt', 'Scharrenbroicher Straße']:
            self.assertIn(text, event['description'])
            self.assertIn(text, event['description_html'])
        self.assertEqual(identity.event_id(event), 'stadtfest-und-schuetzenfest-in-roesrath-2026-09-06-510269c3be')
        for title, day, source in [('Anderes Fest', '2026-09-06', 'ionas4-r-srath'), ('Stadtfest und Schützenfest in Rösrath', '2026-09-07', 'ionas4-r-srath'), ('Stadtfest und Schützenfest in Rösrath', '2026-09-06', 'other')]:
            sibling = self.ionas(title, 'Rösrath', day, 'Rösrath-Mitte', source, link)
            self.assertNotEqual(sibling['venue'], 'Schützenplatz')

    def test_roettgen_correction_preserves_scraped_detail_copy(self):
        description = 'Röttgen Rockt: Das offizielle Programm mit Live-Musik und Informationen für alle Besucher.'
        item = {'id': '30460:0', 'title': 'Röttgen Rockt',
                'start': '2026-09-04T11:00:00', 'end': '2026-09-05T18:00:00',
                'allDay': False, 'location': {'name': 'Festzelt Bruchhausen Röttgen'}}
        with patch.object(regional_ionas4, '_detail_context', return_value={'description': description}):
            event = canonicalize_event(regional_ionas4._events_from_items(
                [item], 'Ruppichteroth', 'https://example.org/kalender/', 0.95,
                detail_fetcher=lambda url: '', source_id='ionas4-ruppichteroth')[0])
        self.assertEqual(event['city'], 'Much')
        self.assertEqual(event['description'], description)
        self.assertEqual(event['description_source'], 'scraped')

    def test_roettgen_city_and_original_public_id(self):
        link = 'https://www.ruppichteroth.de/kalender/meldungen/2026-09-04-roettgen-rockt/30460:0'
        event = self.ionas('Röttgen Rockt', 'Ruppichteroth', '2026-09-04', 'Festzelt Bruchhausen Röttgen, Bruchhausen, 53804 Much', 'ionas4-ruppichteroth', link, end='2026-09-05')
        self.assertEqual(event['city'], 'Much')
        self.assertEqual(event['description_source'], 'generated')
        self.assertIn('Veranstaltungsort: Festzelt Bruchhausen Röttgen, Much.', event['description'])
        self.assertNotIn('Ruppichteroth', event['description'])
        self.assertNotIn('Ruppichteroth', event['description_html'])
        self.assertIn('53804 Much', event['venue_address'])
        self.assertEqual(event['link'], link)
        self.assertEqual(identity.event_id(event), 'roettgen-rockt-2026-09-04-1ae4157a7d')
        sibling = self.ionas('Röttgen Rockt', 'Ruppichteroth', '2026-09-11', 'Festzelt Bruchhausen Röttgen', 'ionas4-ruppichteroth', link, end='2026-09-12')
        self.assertNotEqual(identity.event_id(sibling), identity.event_id(event))
