"""Occurrence-safe location enrichment for recurring Marktcom markets."""
import json
import unittest
from datetime import datetime
from unittest.mock import patch

from nrw_events import detail_enrichment
from nrw_events.identity import event_id
from nrw_events.sources import marktcom
from nrw_events.validation import validate_event

from tests.helpers import patch_window
from tests.test_marktcom import _event_block, _listing

LINK = 'https://www.marktcom.de/veranstaltung/troedelmarkt-in-51149-koeln-porz'


def document(**changes):
    item = {
        '@type': 'Event', 'name': 'Trödelmarkt', 'url': LINK,
        'startDate': '2026-10-18T11:00', 'endDate': '2026-10-18T17:00',
        'description': 'Nur der Oktobertermin beginnt um 11 Uhr.',
        'location': {'@type': 'Place', 'address': {
            'streetAddress': 'Oberstraße 96', 'postalCode': '51149',
            'addressLocality': 'Köln, Porz'}},
        'organizer': {'name': 'Bürgerzentrum Engelshof e.V.'},
    }
    item.update(changes)
    return '<script type="application/ld+json">' + json.dumps(item) + '</script>'


class MarktcomDetailTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 9, 21), datetime(2026, 11, 30))

    def events(self, name='Trödelmarkt'):
        return marktcom.events_from_listing(_listing(*[
            _event_block(LINK.rsplit('/', 1)[1], name, '51149', 'Köln',
                         'Bürgerzentrum Engelshof e.V.', date, 35)
            for date in ('18.10.2026', '15.11.2026')]), 35)

    def test_generic_title_is_not_a_venue_but_identity_is_preserved(self):
        for event in self.events():
            self.assertEqual(event['venue'], '')
            self.assertEqual(event['identity_venue'], 'Trödelmarkt')
            self.assertTrue(event['identity_venue_locked'])
            self.assertEqual(event['title'], 'Trödelmarkt Köln')
            original = {**event, 'venue': 'Trödelmarkt'}
            original.pop('identity_venue')
            original.pop('identity_venue_locked')
            self.assertEqual(event_id(event), event_id(original))

    def test_named_places_are_preserved(self):
        for name in ('Hit-Markt', 'Bürgerzentrum Engelshof', 'Trödelfabrik'):
            with self.subTest(name=name):
                self.assertEqual(self.events(name)[0]['venue'], name)

    def test_repeated_url_shares_only_location_and_fetches_once(self):
        events = self.events()
        events[1]['time'] = '13:00–18:00'
        with patch('nrw_events.common.fetch_detail_url', return_value=document()) as fetch:
            enriched = detail_enrichment.enrich_events(events)
        self.assertEqual(fetch.call_count, 1)
        for before, after in zip(events, enriched, strict=True):
            self.assertEqual(after['venue_address'], 'Oberstraße 96 51149 Köln, Porz')
            self.assertEqual(after['venue'], '')  # Organizer is not a location name.
            published = validate_event(after).to_dict()
            self.assertEqual(event_id(published), event_id(validate_event(before).to_dict()))
            self.assertEqual(published['venue_address'], 'Oberstraße 96, 51149 Köln, Porz')
            for key in ('event_id', 'start_date', 'end_date', 'time', 'start_at',
                        'end_at', 'description', 'price', 'identity_venue'):
                self.assertEqual(after.get(key), before.get(key), key)

    def test_explicit_place_name_is_used(self):
        page = document(location={'name': 'Bürgerzentrum Engelshof', 'address': {
            'streetAddress': 'Oberstraße 96', 'postalCode': '51149',
            'addressLocality': 'Köln, Porz'}})
        context = detail_enrichment.extract_detail_context(page, self.events()[0])
        self.assertEqual(context['venue'], 'Bürgerzentrum Engelshof')

    def test_unrelated_or_ambiguous_location_does_not_fall_back_to_generic(self):
        for page in (document(url=LINK + '-other'), document(name='Anderer Markt'),
                     document(location={'address': {'streetAddress': 'Anderswo 1'}}),
                     document() + document(location={'address': {
                         'streetAddress': 'Andere Straße 1', 'postalCode': '51149',
                         'addressLocality': 'Köln'}}), '<html>keine Daten</html>'):
            with self.subTest(page=page):
                context = detail_enrichment.extract_detail_context(page, self.events()[0])
                self.assertFalse(context.get('venue_address'))
                self.assertFalse(context.get('time'))
                self.assertFalse(context.get('description'))
