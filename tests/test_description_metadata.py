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

    def test_abbreviations_are_not_treated_as_sentence_boundaries(self):
        for text in (
            "Dr. Müller erläutert die Ausstellung ausführlich ohne einen Satzabschluss im Limit",
            "Die Führung ist z. B. für Familien gedacht und enthält viele weitere Informationen",
            "Der Termin findet am 12. August im Museum statt und hat noch zusätzliche Hinweise",
        ):
            with self.subTest(text=text):
                shortened = common.concise_description(text, max_chars=55)
                self.assertTrue(shortened.endswith("…"))
                self.assertGreater(len(shortened), 40)

    def test_concise_description_falls_back_to_a_word_boundary(self):
        text = "Eine Beschreibung ohne passende Satzgrenze innerhalb des gesetzten Limits"

        shortened = common.concise_description(text, max_chars=35)

        self.assertEqual(shortened, "Eine Beschreibung ohne passende…")
        self.assertLessEqual(len(shortened), 35)

    def test_concise_description_turns_literal_newline_escapes_into_breaks(self):
        """A feed that serialized its copy keeps the paragraphs it wrote.

        The escape is the source's own break, not noise: flattening it produced
        the same unreadable wall of text as dropping a ``<p>`` tag would.
        """
        self.assertEqual(
            common.concise_description("Erster Satz.\\n\\n Zweiter Satz."),
            "Erster Satz.\n\nZweiter Satz.",
        )
        self.assertEqual(
            common.concise_description("Erster Satz.\\nZweiter Satz."),
            "Erster Satz.\nZweiter Satz.",
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



class DescriptionParagraphTests(unittest.TestCase):
    """Event copy is prose: the breaks the source authored are content.

    Flattening them produced one unreadable block on the detail page. Only
    description text keeps them — a title, venue or price stays single-line.
    """

    def test_block_tags_become_paragraphs_and_inline_tags_do_not(self):
        html = (
            "<div><p>Gemütliches Beisammensein mit Spielen.</p>"
            "<p>Gäste sind <strong>herzlich</strong> <a href='#'>willkommen</a>.</p>"
            "<ul><li>Dienstags, 17 Uhr</li><li>Donnerstags, 11 Uhr</li></ul>"
            "<p>Die Teilnahme ist kostenfrei.<br>Einstieg jederzeit.</p></div>"
        )

        self.assertEqual(
            common.clean_html_blocks(html),
            "Gemütliches Beisammensein mit Spielen.\n\n"
            "Gäste sind herzlich willkommen.\n\n"
            "Dienstags, 17 Uhr\nDonnerstags, 11 Uhr\n\n"
            "Die Teilnahme ist kostenfrei.\nEinstieg jederzeit.",
        )

    def test_clean_html_still_flattens_single_value_fields(self):
        self.assertEqual(
            common.clean_html("<p>Haus der<br>Springmaus</p>"),
            "Haus der Springmaus",
        )

    def test_layout_padding_never_becomes_a_third_separator(self):
        self.assertEqual(
            common.clean_html_blocks("<p>Eins</p><br><br><br><p>Zwei</p>"),
            "Eins\n\nZwei",
        )

    def test_truncation_does_not_glue_two_paragraphs_together(self):
        long_word = "x" * 40
        value = f"{long_word} {long_word}\n\n{long_word} {long_word}"

        shortened = common.concise_description(value, max_chars=100)

        self.assertNotIn(f"{long_word}{long_word}", shortened)
        self.assertTrue(shortened.endswith("…"))

    def test_ical_description_keeps_its_breaks_but_summary_does_not(self):
        self.assertEqual(
            common._ical_unescape("Erster Satz.\\n\\nZweiter Satz.", preserve_breaks=True),
            "Erster Satz.\n\nZweiter Satz.",
        )
        self.assertEqual(
            common._ical_unescape("Konzert\\nim Park"),
            "Konzert im Park",
        )


if __name__ == "__main__":
    unittest.main()
