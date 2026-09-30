"""Canonical-boundary repairs for defects seen in the 2026-09-09 snapshot."""

import unittest

from nrw_events.validation import canonicalize_event


def _canonical(**updates):
    event = {
        "title": "Konzert im Park",
        "source": "Fixture",
        "source_id": "fixture",
        "start_date": "2026-09-12",
        "end_date": "2026-09-12",
        "city": "Bonn",
        "venue": "Stadtpark",
        "link": "https://example.test/event",
        "description": "Ein Abend mit Musik unter freiem Himmel.",
        "time": "19:00",
        "score": 1.0,
    }
    event.update(updates)
    return canonicalize_event(event).to_dict()


def _rules(event):
    return {warning["rule_id"] for warning in event["quality_warnings"]}


class ClockSentinelTests(unittest.TestCase):
    def test_lone_midnight_is_all_day(self):
        event = _canonical(time="00:00", start_at="2026-09-12T00:00+02:00", title="Scheunenkirmes")
        self.assertEqual((event["time"], event["start_at"], event["all_day"]), ("", "", True))
        self.assertIn("publication.clock-sentinel", _rules(event))

    def test_business_day_wrap_is_all_day(self):
        event = _canonical(
            time="05:00–04:59", end_date="2026-09-15",
            start_at="2026-09-12T05:00:00+02:00", end_at="2026-09-15T04:59:59+02:00",
            title="Großkirmes in Alfter",
        )
        self.assertEqual((event["time"], event["end_at"], event["all_day"]), ("", "", True))

    def test_midnight_party_keeps_its_start(self):
        event = _canonical(time="00:00", category_key="nightlife", category_label="Party & Nachtleben", title="Afterhour")
        self.assertEqual(event["time"], "00:00")

    def test_real_evening_range_is_kept(self):
        self.assertEqual(_canonical(time="18:00–23:00")["time"], "18:00–23:00")


class AdmissionPlausibilityTests(unittest.TestCase):
    def test_price_without_decimal_comma_is_omitted(self):
        event = _canonical(price="2200 EUR", admission_basis="explicit")
        self.assertEqual((event["price"], event["admission"]["amount"]), ("", None))
        self.assertIn("publication.admission-implausible", _rules(event))

    def test_ordinary_and_formatted_prices_are_kept(self):
        self.assertEqual(_canonical(price="22,00 €", admission_basis="explicit")["admission"]["amount"], 22)
        self.assertEqual(_canonical(price="1.200,00 EUR", admission_basis="explicit")["admission"]["amount"], 1200)


class TitleStatusTests(unittest.TestCase):
    def test_cancelled_title_loses_its_marker(self):
        event = _canonical(title="ABGESAGT - Öffentliche Führung KSI und Abtei", status="cancelled")
        self.assertEqual((event["title"], event["status"]), ("Öffentliche Führung KSI und Abtei", "cancelled"))

    def test_scheduled_title_is_untouched(self):
        self.assertEqual(_canonical(title="Kunst gegen Bares")["title"], "Kunst gegen Bares")

    def test_repeated_event_date_is_removed_from_the_title(self):
        event = _canonical(title="Quintetto Encuentro am 12.09.2026 im Löhrerhof")
        self.assertEqual(event["title"], "Quintetto Encuentro im Löhrerhof")

    def test_title_without_a_repeated_date_keeps_its_exact_text(self):
        title = "Lesung: Shakespeare „Die Sonette“ -"
        self.assertEqual(_canonical(title=title)["title"], title)

    def test_other_dates_stay_in_the_title(self):
        self.assertEqual(
            _canonical(title="Nachholtermin vom 03.05.2026")["title"], "Nachholtermin vom 03.05.2026",
        )


class VisitorCopyTests(unittest.TestCase):
    def test_listing_teaser_is_dropped(self):
        event = _canonical(
            title="Marielle & Freunde - Ein Tag voller Genuss", venue="Café & Restaurant Marielle",
            description="12.09.2026 Marielle & Freunde - Ein Tag voller Genuss Café & Restaurant Marielle › weiterlesen",
        )
        self.assertEqual(event["description"], "")
        self.assertIn("publication.description-teaser", _rules(event))

    def test_link_label_is_removed_from_real_copy(self):
        event = _canonical(description="Ein Abend mit Musik unter freiem Himmel und Picknick. Mehr erfahren ›")
        self.assertEqual(event["description"], "Ein Abend mit Musik unter freiem Himmel und Picknick.")

    def test_invitation_to_learn_more_is_prose(self):
        copy = "Alle die mehr erfahren möchten, sind herzlich zu dieser Wanderung eingeladen."
        self.assertEqual(_canonical(description=copy)["description"], copy)

    def test_truncated_copy_is_flagged(self):
        event = _canonical(description="Geboten wird alles rund um Kinder...")
        self.assertIn("publication.description-truncated", _rules(event))


class SummaryPlaceholderTests(unittest.TestCase):
    def test_placeholder_end_sentence_is_removed(self):
        event = _canonical(ai_summary=(
            "Das Straßenfest findet in der Clemens-August-Straße statt. "
            "Das Fest beginnt um 11:00 Uhr und endet um 23:59 Uhr. Es gibt Musik."
        ))
        self.assertEqual(
            event["ai_summary"],
            "Das Straßenfest findet in der Clemens-August-Straße statt. Es gibt Musik.",
        )


if __name__ == "__main__":
    unittest.main()
