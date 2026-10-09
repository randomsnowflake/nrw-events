"""Regression contracts from the September 2026 public-page audit."""
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from nrw_events import common, identity
from nrw_events.sources import bonn, bonnkirmes, regional_sitekit, regional_venues
from nrw_events.validation import canonicalize_event


class SeptemberLandingContentTests(unittest.TestCase):
    def test_sitekit_contact_ui_is_not_a_postal_address(self):
        html = '''<section aria-labelledby="veranstaltungsort">
        <h2>Veranstaltungsort</h2><h3>Rheintreppe Ruttmanns Wiese</h3>
        <h4>Ort</h4><p>Ort</p><p>Rheintreppe Ruttmanns Wiese</p>
        <p>Kölner Straße / Ufer Straße</p><p>50389 Wesseling</p>
        <a>Karte öffnen (Google Maps) <span>(Öffnet in einem neuen Tab)</span></a>
        </section>'''
        context = regional_sitekit._detail_context(html)
        self.assertEqual(context['venue'], 'Rheintreppe Ruttmanns Wiese')
        self.assertEqual(context['venue_address'], 'Kölner Straße / Ufer Straße, 50389 Wesseling')

    def test_lvr_search_index_hits_keep_publisher_title_berlin_time_and_ticket_link(self):
        payload = (Path(__file__).parent / 'fixtures' / 'lvr_landesmuseum_dates_20261009.json').read_text()
        with patch.object(common, 'TODAY', datetime(2026, 10, 1)), patch.object(common, 'END_DATE', datetime(2026, 10, 31)):
            events = regional_venues._events_from_lvr(payload)
        self.assertEqual(
            [(e['title'], e['date'], e['time']) for e in events],
            [
                ('Kino im Landesmuseum', '2026-10-09', '19:30'),
                ('Foto-Walk: Fotografische Spurensuche: Das französische Bonn', '2026-10-16', '15:00'),
                ('Fluchtpunkt Paris', '2026-10-18', '11:30'),
            ],
        )
        self.assertEqual(
            events[1]['link'],
            'https://www.shop.landesmuseum-bonn.lvr.de/#/product/event/16427?date=2026-10-16&date_id=90623',
        )
        self.assertTrue(events[1]['description'].endswith('zu einem individuellen …'))

    def test_duisdorf_gets_specific_organizer_link_not_other_fairs(self):
        html = '''<h3>Herbstkirmes Duisdorf</h3><p>Auf dem Europaplatz findet die traditionelle Kirmes vom 04.09.2026 bis zum 07.09.2026 statt.</p>'''
        with patch.object(common, 'TODAY', datetime(2026, 9, 1)), patch.object(common, 'END_DATE', datetime(2026, 9, 30)):
            event = bonnkirmes.events_from_html(html)[0]
        self.assertEqual(event['link'], 'https://www.ofa-duisdorf.de/kirmes')
        self.assertEqual(event['source_id'], 'bonnkirmes')
        self.assertEqual(event['start_date'], '2026-09-04')

    def test_muffenale_reviewed_pdf_copy_is_occurrence_scoped(self):
        html = '<li>Muffenale, Muffendorfer Hauptstraße, 6. September 2026, Verein Straßenfest in Muffendorf</li>'
        with patch.object(common, 'TODAY', datetime(2026, 9, 1)), patch.object(common, 'END_DATE', datetime(2026, 9, 30)), patch.object(common, 'fetch_url', return_value=html):
            event = bonn.fetch_press_festivals()[0]
        self.assertIn('10:30', event['description'])
        self.assertIn('2025', event['description'])
        self.assertIn('Seite 90', event['description'])
        self.assertIn('Seite 62', event['description'])
        self.assertTrue(event['link'].endswith('.pdf#page=90'))
        self.assertEqual(event['source'], 'Bonn district festivals')
        canonical = canonicalize_event(event)
        self.assertFalse(canonical['all_day'])
        self.assertEqual(canonical['time'], '10:30')
        self.assertEqual(canonical['start_at'], '2026-09-06T10:30:00+02:00')
        self.assertIn('Seite 62', canonical['description_html'])
        previous = {**dict(canonical), 'time': '', 'start_at': '', 'all_day': True}
        self.assertEqual(identity.identity_tuple(canonical), identity.identity_tuple(previous))

    def test_muffenale_pdf_is_not_reused_for_another_year(self):
        html = '<li>Muffenale, Muffendorfer Hauptstraße, 5. September 2027, Verein Straßenfest in Muffendorf</li>'
        with patch.object(common, 'TODAY', datetime(2027, 9, 1)), patch.object(common, 'END_DATE', datetime(2027, 9, 30)), patch.object(common, 'fetch_url', return_value=html):
            event = bonn.fetch_press_festivals()[0]
        self.assertNotIn('2025/2026', event['description'])
        self.assertNotIn('.pdf', event['link'])
