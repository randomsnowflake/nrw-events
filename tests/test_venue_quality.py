import unittest

from nrw_events.validation import canonicalize_event
from nrw_events.venue_quality import sanitize_venue_fields

from tests.test_venue_registry import event


class VenueQualityTests(unittest.TestCase):
    def test_invalid_fields_are_omitted_with_diagnostic_and_stable_identity(self):
        for value in ['g', 'Parkplatz an der… 20', 'wird auf der Homepage bekannt gegeben']:
            with self.subTest(value=value):
                result = canonicalize_event(event(venue=value))
                self.assertEqual(result.venue, '')
                self.assertEqual(result.identity_venue, value)
                self.assertTrue(result.identity_venue_locked)
                self.assertTrue(any(w['rule_id'] == 'publication.invalid-venue' for w in result.quality_warnings))

    def test_map_url_is_preserved_as_note_not_name_or_street(self):
        url = 'https://www.google.com/maps/d/u/0/viewer?mid=abc&z=15'
        result = canonicalize_event(event(venue=url, venue_address=url + ' 53343 Niederbachem'))
        self.assertEqual(result.venue, '')
        self.assertEqual(result.venue_address, '53343 Niederbachem')
        self.assertIn(url, result.description)
        self.assertEqual(canonicalize_event(result.to_dict()), result)

    def test_journey_is_retained_without_inventing_a_single_meeting_point(self):
        value = 'Gemeinsame Anfahrt mit der RB25 (Abfahrten: 14:03 Uhr in Stümpen; 14:08 Uhr in Rösrath)'
        result = canonicalize_event(event(venue=value))
        self.assertEqual(result.venue, '')
        self.assertIn(value, result.description)

    def test_valid_unregistered_places_and_addresses_survive(self):
        for value in ['H7', 'B 9', 'Studio 5', 'Sülz & Klettenberg', 'Verschiedene Veranstaltungsorte in Köln', 'Parkplatz an der Grundschule']:
            with self.subTest(value=value):
                row = event(venue=value, venue_address='Schulstraße 20, 53489 Bad Bodendorf')
                sanitize_venue_fields(row)
                self.assertEqual(row['venue'], value)
                self.assertEqual(row['venue_address'], 'Schulstraße 20, 53489 Bad Bodendorf')
                self.assertNotIn('quality_warnings', row)

    def test_cleaning_does_not_mutate_shared_warning_list(self):
        warnings = []
        row = event(venue='g', quality_warnings=warnings)
        sanitize_venue_fields(row)
        self.assertEqual(warnings, [])

class SourceVenueRecoveryTests(unittest.TestCase):
    def test_brotfabrik_exact_api_fixture_recovers_only_explicit_places(self):
        import json
        from datetime import datetime
        from pathlib import Path

        from nrw_events.sources.bonn_venues import events_from_brotfabrik_items

        from tests.sources.parser_cases import patch_window
        patch_window(self, datetime(2026, 9, 1), datetime(2026, 10, 31))
        items = json.loads((Path(__file__).parent / 'data/brotfabrik-venue-fields.json').read_text())
        rows = {e['title']: e for e in events_from_brotfabrik_items(items)}
        self.assertEqual(rows['Wasser-Fahrradtour | Bike Tour on Water']['venue'], 'Brotfabrik Innenhof')
        self.assertIn('Kreuzstraße 16', rows['Wasser-Fahrradtour | Bike Tour on Water']['venue_address'])
        self.assertEqual(rows['World Life Balance']['venue'], 'Abenteuer Lernen e. V.')
        self.assertIn('Siebenmorgenweg 22', rows['World Life Balance']['venue_address'])
        self.assertEqual(rows['Ein Garten für Beuel - Feierliche Einweihung']['venue'], '')
        for row in rows.values():
            self.assertEqual(row['identity_venue'], 'g')
            self.assertTrue(row['identity_venue_locked'])

    def test_grote_hiller_named_places_keep_rooms_and_ignore_unlabelled_titles(self):
        from nrw_events.sources.grote_hiller import _named_market_place
        for title, expected in [
            ('Bonn, Mädelsflohmarkt im Telekom Dome', 'Telekom Dome'),
            ('Lohmar, Mädelsflohmarkt in der Jabachhalle', 'Jabachhalle'),
            ('Hennef, Mehrzweckhalle "Meiersheide" Mädelsmarkt', 'Mehrzweckhalle "Meiersheide"'),
            ('Gummersbach, Riesen Stadtflohmarkt in der Fußgängerzone. Nur 2x im Jahr!', 'Fußgängerzone'),
            ('Bonn, Flohmarkt', ''),
            ('Bonn, Flohmarkt im September', ''),
            ('Bonn, Flohmarkt in der kalten Jahreszeit', ''),
        ]:
            self.assertEqual(_named_market_place(title), expected)

    def test_reviewed_alias_is_city_scoped(self):
        from nrw_events.normalization import resolve_venue
        one = resolve_venue('Herrenhaus Burg Altendorf', 'Meckenheim')
        two = resolve_venue('Herrenhaus der Burg Altendorf', 'Meckenheim')
        self.assertEqual(one, two)
        self.assertEqual(one.venue_address, 'Burgstraße 5, 53340 Meckenheim')
        self.assertEqual(resolve_venue('Herrenhaus der Burg Altendorf', 'Essen').venue_id, '')

    def test_ionas_meeting_points_remove_departure_times_and_route_copy(self):
        from nrw_events.sources.regional_ionas4 import _detail_context
        context = _detail_context('<div class="tvm-event--description">Treffpunkt: 12:45 Uhr Bahnhof Sinzig (offen für jedermann) Fahrgemeinschaft nach Bad Breisig Fähre Wanderstrecke: Bad Breisig</div>')
        self.assertEqual(context['venue'], 'Bahnhof Sinzig')
        contradiction = _detail_context('<div class="tvm-event--location">Rathaus</div><div class="tvm-event--description">Treffpunkt: Bahnhof Sinzig</div>')
        self.assertEqual(contradiction['venue'], 'Rathaus')

    def test_truncated_detail_can_be_replaced_without_overwriting_valid_place(self):
        from nrw_events.detail_enrichment import apply_detail_context
        row = event(venue='Parkplatz an der… 20')
        context = {'venue': 'Parkplatz an der Grundschule in Schönenberg'}
        self.assertEqual(apply_detail_context(row, context)['venue'], context['venue'])
        row['venue'] = 'Anderer Parkplatz'
        self.assertEqual(apply_detail_context(row, context)['venue'], 'Anderer Parkplatz')

    def test_marktcom_programme_label_is_not_a_venue(self):
        from datetime import datetime

        from nrw_events.sources.marktcom import events_from_listing

        from tests.helpers import patch_window
        from tests.test_marktcom import _event_block, _listing
        patch_window(self, datetime(2026, 9, 1), datetime(2026, 9, 30))
        label = 'Info- und Tauschtag für Ansichtskarten, Briefmarken, Münzen'
        rows = events_from_listing(_listing(_event_block('info', label, '53489', 'Sinzig', 'Briefmarkenfreunde', '20.09.2026', 13)), 13)
        self.assertEqual(rows[0]['venue'], '')
        self.assertEqual(rows[0]['identity_venue'], label)
