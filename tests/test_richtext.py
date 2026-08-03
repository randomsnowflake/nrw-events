import unittest

from nrw_events import richtext


class RichTextStructureTests(unittest.TestCase):
    def test_headings_lists_and_emphasis_survive(self):
        source = (
            "<div><p>Ein Escape Game im Museum.</p>"
            "<h2>Was erwartet euch?</h2>"
            "<p>Im Landesmuseum ist ein <b>Nachtwächter</b> <i>ausgefallen</i>.</p>"
            "<ul><li>Neugier</li><li>Taschenlampe</li></ul>"
            "<ol><li>Anmelden</li></ol></div>"
        )

        self.assertEqual(
            richtext.sanitize_rich_text(source),
            "<p>Ein Escape Game im Museum.</p>"
            "<h3>Was erwartet euch?</h3>"
            "<p>Im Landesmuseum ist ein <strong>Nachtwächter</strong> <em>ausgefallen</em>.</p>"
            "<ul><li>Neugier</li><li>Taschenlampe</li></ul>"
            "<ol><li>Anmelden</li></ol>",
        )

    def test_source_headings_never_compete_with_the_page_outline(self):
        """h1/h2 belong to the page; event copy starts at h3."""
        for level in ("h1", "h2", "h3"):
            with self.subTest(level=level):
                self.assertEqual(richtext.sanitize_rich_text(f"<{level}>Titel</{level}>"), "<h3>Titel</h3>")
        for level in ("h4", "h5", "h6"):
            with self.subTest(level=level):
                self.assertEqual(richtext.sanitize_rich_text(f"<{level}>Titel</{level}>"), "<h4>Titel</h4>")

    def test_bold_line_becomes_a_heading_but_a_bold_sentence_or_date_does_not(self):
        self.assertEqual(
            richtext.sanitize_rich_text("<p><strong>Was erwartet euch?</strong></p>"),
            "<h3>Was erwartet euch?</h3>",
        )
        self.assertEqual(
            richtext.sanitize_rich_text("<p><strong>30. Mai bis 30. August 2026</strong></p>"),
            "<p><strong>30. Mai bis 30. August 2026</strong></p>",
        )
        self.assertEqual(
            richtext.sanitize_rich_text("<p><strong>Bitte bringt eine Taschenlampe mit.</strong></p>"),
            "<p><strong>Bitte bringt eine Taschenlampe mit.</strong></p>",
        )

    def test_truncation_cuts_between_blocks_never_inside_one(self):
        source = "<p>" + "a" * 60 + "</p><ul><li>Eins</li><li>Zwei</li></ul><p>Ende</p>"

        shortened = richtext.sanitize_rich_text(source, max_chars=40)

        self.assertEqual(shortened, "<p>" + "a" * 60 + "</p>")

    def test_plain_text_round_trip_keeps_paragraphs(self):
        self.assertEqual(
            richtext.to_plain_text("<p>Eins</p><h3>Titel</h3><ul><li>A</li></ul>"),
            "Eins\n\nTitel\n\nA",
        )
        self.assertEqual(
            richtext.from_plain_text("Absatz eins.\n\nZeile A\nZeile B"),
            "<p>Absatz eins.</p><p>Zeile A<br>Zeile B</p>",
        )


class RichTextSafetyTests(unittest.TestCase):
    """The output is constructed from a fixed vocabulary, not filtered.

    Nothing from the source reaches the result as markup, so these cases are a
    guard on that property rather than a blocklist of known attacks.
    """

    def test_no_attribute_survives(self):
        for source in (
            '<p onclick="evil()">Text</p>',
            '<p class="x" style="color:red" data-x="1">Text</p>',
            '<a href="javascript:bad()">Text</a>',
            "<img src=x onerror=alert(1)>Text",
        ):
            with self.subTest(source=source):
                rendered = richtext.sanitize_rich_text(source)
                self.assertNotIn("=", rendered)
                self.assertIn("Text", rendered)

    def test_script_and_style_contents_are_dropped_entirely(self):
        rendered = richtext.sanitize_rich_text("<p>Vorher</p><script>alert(1)</script><style>p{}</style><p>Nachher</p>")

        self.assertEqual(rendered, "<p>Vorher</p><p>Nachher</p>")

    def test_text_that_looks_like_markup_is_escaped(self):
        self.assertEqual(
            richtext.sanitize_rich_text("<p>Preis &lt; 5 &amp; Rest</p>"),
            "<p>Preis &lt; 5 &amp; Rest</p>",
        )
        self.assertNotIn("<script", richtext.sanitize_rich_text("<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>"))

    def test_only_known_tags_are_emitted(self):
        import re

        messy = (
            "<section><table><tr><td>Zelle</td></tr></table>"
            "<form><input><button>Klick</button></form>"
            "<p>Text <span class='x'>im</span> <a href='#'>Fluss</a></p></section>"
        )

        emitted = set(re.findall(r"</?([a-z0-9]+)", richtext.sanitize_rich_text(messy)))

        self.assertTrue(emitted <= {"p", "h3", "h4", "ul", "ol", "li", "strong", "em", "br"}, emitted)

    def test_sanitizing_its_own_output_changes_nothing(self):
        once = richtext.sanitize_rich_text(
            "<div><h2>Titel</h2><p>Text <b>fett</b></p><ul><li>A</li><li>B</li></ul></div>"
        )

        self.assertEqual(richtext.sanitize_rich_text(once), once)


if __name__ == "__main__":
    unittest.main()
