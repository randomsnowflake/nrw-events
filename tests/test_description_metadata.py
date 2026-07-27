import unittest
from datetime import timedelta

from nrw_events import common
from nrw_events.validation import EventValidationError, canonicalize_event


class DescriptionMetadataTests(unittest.TestCase):
    def test_concise_description_prefers_the_last_sentence_boundary(self):
        text = "Ein vollständiger erster Satz. Ein zweiter Satz, der deutlich zu lang für das Limit ist."

        self.assertEqual(
            common.concise_description(text, max_chars=45),
            "Ein vollständiger erster Satz.",
        )

    def test_sentence_boundary_keeps_a_closing_quote(self):
        text = "Er sagte: „Das ist vollständig!“ Danach folgt ein sehr langer zweiter Satz."

        self.assertEqual(
            common.concise_description(text, max_chars=50),
            "Er sagte: „Das ist vollständig!“",
        )

    def test_concise_description_falls_back_to_a_word_boundary(self):
        text = "Eine Beschreibung ohne passende Satzgrenze innerhalb des gesetzten Limits"

        shortened = common.concise_description(text, max_chars=35)

        self.assertEqual(shortened, "Eine Beschreibung ohne passende…")
        self.assertLessEqual(len(shortened), 35)

    def test_concise_description_removes_literal_newline_escapes(self):
        self.assertEqual(
            common.concise_description("Erster Satz.\\n\\n Zweiter Satz."),
            "Erster Satz. Zweiter Satz.",
        )

    def test_make_event_marks_generated_and_scraped_descriptions(self):
        start = common.TODAY + timedelta(days=1)
        generated = common.factual_event_description(
            "Testtermin", date_value=start, venue="Testhalle", city="Bonn"
        )

        generated_event = common.make_event(
            "Testtermin", start, None, "Testhalle", "Bonn", generated,
            "https://example.test/generated", "Test", "Kultur",
        )
        scraped_event = common.make_event(
            "Testtermin", start, None, "Testhalle", "Bonn", "Originaltext der Quelle.",
            "https://example.test/scraped", "Test", "Kultur",
        )

        self.assertEqual(generated_event["description_source"], "generated")
        self.assertEqual(scraped_event["description_source"], "scraped")

    def test_canonical_model_preserves_and_validates_description_source(self):
        start = common.TODAY + timedelta(days=1)
        event = common.make_event(
            "Testtermin", start, None, "Testhalle", "Bonn",
            common.factual_event_description("Testtermin", date_value=start),
            "https://example.test/event", "Test", "Kultur",
        )

        canonical = canonicalize_event(event)

        self.assertEqual(canonical.description_source, "generated")
        self.assertEqual(canonical.to_dict()["description_source"], "generated")
        invalid = dict(event, description_source="invented")
        with self.assertRaisesRegex(EventValidationError, "description_source_invalid"):
            canonicalize_event(invalid)


if __name__ == "__main__":
    unittest.main()
