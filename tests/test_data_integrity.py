import unittest
from datetime import datetime
from unittest import mock

from nrw_events import common, report
from nrw_events.validation import EventValidationError, validate_event
from tests.helpers import patch_window


class DataIntegrityTests(unittest.TestCase):
    def test_validation_moves_complex_time_copy_to_note(self):
        event = validate_event({
            "title": "Ausstellung mit Öffnungszeiten",
            "source": "Test",
            "date": "2026-06-12",
            "score": 1.0,
            "city": "Bonn",
            "time": "Dienstag bis Freitag, 13 bis 19 Uhr; Samstag 11 bis 18 Uhr",
        })

        self.assertEqual(event.time, "")
        self.assertEqual(
            event.time_note,
            "Dienstag bis Freitag, 13 bis 19 Uhr; Samstag 11 bis 18 Uhr",
        )
        self.assertFalse(event.all_day)

    def test_validation_canonicalizes_hour_only_ranges(self):
        event = validate_event({
            "title": "Führung",
            "source": "Test",
            "date": "2026-06-12",
            "score": 1.0,
            "city": "Bonn",
            "time": "15 bis 16 Uhr",
        })

        self.assertEqual(event.time, "15:00–16:00")
        self.assertEqual(event.time_note, "")

    def test_validation_preserves_invalid_clock_as_note(self):
        event = validate_event({
            "title": "Spätprogramm",
            "source": "Test",
            "date": "2026-06-12",
            "score": 1.0,
            "city": "Bonn",
            "time": "ab 25 Uhr",
        })

        self.assertEqual(event.time, "")
        self.assertEqual(event.time_note, "ab 25 Uhr")

    def test_validation_does_not_classify_from_url_implementation_details(self):
        event = validate_event({
            "title": "Unklare Veranstaltung",
            "source": "Test",
            "date": "2026-06-12",
            "score": 1.0,
            "city": "Bonn",
            "link": "https://example.test/museum/event",
        })
        self.assertEqual(event.category_key, "other")

    def test_validation_upgrades_a_weak_teaser_category_from_richer_copy(self):
        event = validate_event({
            "title": "Klassik am Rinderstall",
            "source": "Naturregion Sieg",
            "date": "2026-06-12",
            "score": 1.0,
            "city": "Wissen",
            "category": "Outdoor",
            "category_key": "outdoor",
            "category_label": "Führungen & Outdoor",
            "category_confidence": 0.6,
            "category_reason": "outdoor:source_category=outdoor",
            "description": "Ein Benefizkonzert mit Kammermusik und international renommierten Musikern.",
        })

        self.assertEqual(event.category_key, "concert")

    def test_validation_preserves_an_explicitly_locked_category(self):
        event = validate_event({
            "title": "Klassik am Rinderstall",
            "source": "Test",
            "date": "2026-06-12",
            "score": 1.0,
            "city": "Bonn",
            "category": "Outdoor",
            "category_key": "outdoor",
            "category_label": "Führungen & Outdoor",
            "category_confidence": 1.0,
            "category_reason": "source:locked-default:outdoor",
            "description": "Ein Benefizkonzert mit Kammermusik.",
        })

        self.assertEqual(event.category_key, "outdoor")

    def test_validation_backfills_reason_for_matching_canonical_category(self):
        event = validate_event({
            "title": "Straßenfest im Agnesviertel",
            "source": "Kölner Straßenfeste",
            "date": "2026-06-12",
            "score": 1.0,
            "city": "Köln",
            "category": "Fest",
            "category_key": "festival",
            "category_label": "Feste & Stadtleben",
            "category_confidence": 0.0,
            "category_reason": "",
        })

        self.assertEqual(event.category_key, "festival")
        self.assertEqual(event.category_reason, "source:canonical:festival")

    def test_validation_preserves_canonical_category_when_backfilling_reason(self):
        event = validate_event({
            "title": "Sekt and the City - Frisch geföhnt und flach gelegt",
            "source": "Haus der Springmaus",
            "date": "2026-06-12",
            "score": 1.0,
            "city": "Bonn",
            "category": "Event",
            "category_key": "stage",
            "category_label": "Theater & Bühne",
            "category_confidence": 0.0,
            "category_reason": "",
        })

        self.assertEqual(event.category_key, "stage")
        self.assertEqual(event.category_reason, "source:canonical:stage")

    def test_validation_does_not_publish_inferred_free_access(self):
        event = validate_event({
            "title": "Hofflohmarkt Rondorf",
            "source": "Hofflohmärkte Köln",
            "date": "2026-06-12",
            "score": 1.0,
            "city": "Köln",
            "description": "Hausanwohner verkaufen in ihren Höfen.",
        })
        self.assertEqual(event.price, "")
        self.assertIsNone(event.admission["isFree"])
        self.assertEqual(event.quality_warnings[0]["rule_id"], "publication.admission-not-explicit")

    def test_validation_repairs_equal_structured_end_from_explicit_range(self):
        event = validate_event({
            "title": "14. Garagenflohmarkt Berzdorf",
            "source": "Stadt Wesseling",
            "date": "2026-08-30",
            "score": 1.0,
            "city": "Wesseling",
            "time": "10:00–17:00",
            "start_at": "2026-08-30T10:00:00+02:00",
            "end_at": "2026-08-30T10:00:00+02:00",
        })

        self.assertEqual(event.start_at, "2026-08-30T10:00:00+02:00")
        self.assertEqual(event.end_at, "2026-08-30T17:00:00+02:00")
        self.assertEqual(event.quality_warnings[0]["resolution"], "repaired_from_time")

    def test_validation_keeps_per_day_schedule_without_flattening(self):
        event = validate_event({
            "title": "Street Food Festival",
            "source": "Bad Godesberg Stadtmarketing",
            "date": "2026-08-28",
            "end_date": "2026-08-30",
            "score": 1.0,
            "city": "Bonn-Bad Godesberg",
            "time": "15:00–22:00",
            "start_at": "2026-08-28T15:00:00+02:00",
            "end_at": "2026-08-28T22:00:00+02:00",
            "all_day": True,
            "daily_schedule": [
                {"date": "2026-08-28", "start_at": "2026-08-28T15:00:00+02:00", "end_at": "2026-08-28T22:00:00+02:00"},
                {"date": "2026-08-29", "start_at": "2026-08-29T12:00:00+02:00", "end_at": "2026-08-29T22:00:00+02:00"},
                {"date": "2026-08-30", "start_at": "2026-08-30T12:00:00+02:00", "end_at": "2026-08-30T20:00:00+02:00"},
            ],
        })

        self.assertEqual(len(event.daily_schedule), 3)
        self.assertEqual(event.time, "")
        self.assertEqual(event.start_at, "")
        self.assertEqual(event.end_at, "")
        self.assertFalse(event.all_day)

    def test_validation_keeps_conflicted_schedule_date_unknown(self):
        event = validate_event({
            "title": "Festival with conflicting hours",
            "source": "Test",
            "date": "2026-08-28",
            "score": 1.0,
            "city": "Bonn",
            "daily_schedule": [
                {"date": "2026-08-28", "start_at": "2026-08-28T12:00:00+02:00", "end_at": "2026-08-28T20:00:00+02:00"},
                {"date": "2026-08-28", "start_at": "2026-08-28T14:00:00+02:00", "end_at": "2026-08-28T22:00:00+02:00"},
                {"date": "2026-08-28", "start_at": "2026-08-28T12:00:00+02:00", "end_at": "2026-08-28T20:00:00+02:00"},
            ],
        })

        self.assertEqual(event.daily_schedule, [])
        self.assertEqual(event.quality_warnings[0]["rule_id"], "publication.schedule-conflict")
        self.assertEqual(event.quality_warnings[0]["resolution"], "unknown")

    def test_validation_extracts_street_food_daily_hours_from_primary_copy(self):
        event = validate_event({
            "title": "Street Food Festival",
            "source": "Bad Godesberg Stadtmarketing",
            "date": "2026-08-28",
            "end_date": "2026-08-30",
            "score": 1.0,
            "city": "Bonn-Bad Godesberg",
            "description": "Freitag von 15:00 – 22:00 Uhr, Samstag von 12:00 – 22:00 Uhr und Sonntag von 12:00 – 20:00 Uhr.",
            "all_day": True,
        })

        self.assertEqual(
            [(slot["date"], slot["start_at"][11:16], slot["end_at"][11:16]) for slot in event.daily_schedule],
            [
                ("2026-08-28", "15:00", "22:00"),
                ("2026-08-29", "12:00", "22:00"),
                ("2026-08-30", "12:00", "20:00"),
            ],
        )
        self.assertFalse(event.all_day)

    def test_validation_preserves_explicit_paid_price_for_implicit_free_event_type(self):
        event = validate_event({
            "title": "Flohmarkt Spezial",
            "source": "Test",
            "date": "2026-06-12",
            "score": 1.0,
            "city": "Bonn",
            "price": "4 Euro",
        })
        self.assertEqual(event.price, "4 Euro")

    def test_validation_marks_shifted_direct_dict_event_as_postponed(self):
        event = validate_event({
            "title": "Theaterabend verlegt",
            "source": "Test",
            "date": "2026-06-12",
            "score": 1.0,
            "city": "Bonn",
            "description": "Neuer Termin folgt.",
        })

        self.assertEqual(event.status, "postponed")

    def setUp(self):
        patch_window(self, datetime(2026, 6, 8), datetime(2026, 6, 30))

    def test_unknown_location_is_not_scored_as_bonn(self):
        event = common.make_event("Regional event", datetime(2026, 6, 12), None, "", "Unknown region", "",
                                  "https://example.test", "Test", "concert")
        self.assertIsNotNone(event)
        assert event is not None
        self.assertIsNone(event["distance_km"])
        self.assertEqual(event["location_confidence"], "unresolved")

    def test_cancelled_phrase_is_published_with_status(self):
        event = common.make_event(
            "Kabarettprogramm muss leider kurzfristig abgesagt werden!", datetime(2026, 6, 12), None,
            "Venue", "Bonn", "", "https://example.test", "Test", "stage",
        )
        self.assertIsNotNone(event)
        self.assertEqual(event and event["status"], "cancelled")

    def test_ical_utc_time_converts_to_berlin_time(self):
        ical = """BEGIN:VCALENDAR
BEGIN:VEVENT
SUMMARY:UTC concert
DTSTART:20260612T180000Z
DTEND:20260612T200000Z
URL:https://example.test/event
END:VEVENT
END:VCALENDAR"""
        with mock.patch("nrw_events.common.fetch_url", return_value=ical):
            events = common.fetch_ical("https://example.test/events.ics", "Test", "Bonn", "concert")
        self.assertEqual(events[0]["time"], "20:00–22:00")
        self.assertEqual(events[0]["start_at"], "2026-06-12T20:00+02:00")

    def test_all_day_ical_end_date_is_exclusive(self):
        ical = """BEGIN:VCALENDAR
BEGIN:VEVENT
SUMMARY:Weekend exhibition
DTSTART;VALUE=DATE:20260612
DTEND;VALUE=DATE:20260615
END:VEVENT
END:VCALENDAR"""
        with mock.patch("nrw_events.common.fetch_url", return_value=ical):
            events = common.fetch_ical("https://example.test/events.ics", "Test", "Bonn")
        self.assertEqual(events[0]["end_date"], "2026-06-14")
        self.assertTrue(events[0]["all_day"])

    def test_valid_empty_ical_can_be_healthy_for_inactive_calendars(self):
        ical = """BEGIN:VCALENDAR
VERSION:2.0
NAME:Inactive Meetup group
END:VCALENDAR"""
        with mock.patch("nrw_events.common.fetch_url", return_value=ical), mock.patch(
            "nrw_events.common._record_endpoint"
        ) as record_endpoint:
            events = common.fetch_ical(
                "https://example.test/events.ics",
                "Test",
                "Bonn",
                empty_calendar_is_valid=True,
            )

        self.assertEqual(events, [])
        self.assertFalse(record_endpoint.call_args.kwargs["parser_empty"])

    def test_empty_calendar_opt_out_does_not_hide_non_ical_parser_drift(self):
        with mock.patch("nrw_events.common.fetch_url", return_value="<html>changed layout</html>"), mock.patch(
            "nrw_events.common._record_endpoint"
        ) as record_endpoint:
            common.fetch_ical(
                "https://example.test/events.ics",
                "Test",
                "Bonn",
                empty_calendar_is_valid=True,
            )

        self.assertTrue(record_endpoint.call_args.kwargs["parser_empty"])

    def test_empty_calendar_opt_out_does_not_hide_a_truncated_vevent(self):
        ical = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
SUMMARY:Truncated by the feed
END:VCALENDAR"""
        with mock.patch("nrw_events.common.fetch_url", return_value=ical), mock.patch(
            "nrw_events.common._record_endpoint"
        ) as record_endpoint:
            common.fetch_ical(
                "https://example.test/events.ics",
                "Test",
                "Bonn",
                empty_calendar_is_valid=True,
            )

        self.assertTrue(record_endpoint.call_args.kwargs["parser_empty"])

    def test_deduplication_keeps_same_title_on_different_dates(self):
        events = [
            {"title": "Weekly concert", "city": "Bonn", "date": "2026-06-12", "score": 1.0},
            {"title": "Weekly concert", "city": "Bonn", "date": "2026-06-19", "score": 1.0},
        ]
        self.assertEqual(len(report.deduplicate(events)), 2)

    def test_validation_rejects_bad_link_and_nonfinite_score(self):
        base = {"title": "Event", "date": "2026-06-12", "source": "Test", "score": 1.0}
        with self.assertRaisesRegex(EventValidationError, "link_invalid"):
            validate_event({**base, "link": "javascript:alert(1)"})
        with self.assertRaisesRegex(EventValidationError, "score_invalid"):
            validate_event({**base, "score": float("nan")})

    def test_validation_neutralizes_legacy_bonn_fallback_for_unknown_city(self):
        event = validate_event({
            "title": "Unknown city event", "date": "2026-06-12", "source": "Legacy", "score": 1.5,
            "city": "Naturregion Sieg", "distance_km": 0,
        })
        self.assertIsNone(event["distance_km"])
        self.assertEqual(event["location_confidence"], "unresolved")
        self.assertLessEqual(event["score"], 0.3)
