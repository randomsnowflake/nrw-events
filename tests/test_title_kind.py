"""Opaque titles gain a short, source-grounded kind; everything else stays untouched."""
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from nrw_events import publication_enrichment, title_kind
from nrw_events.validation import validate_event

NAHT_TEXT = "Am 10. Oktober eröffnet im Dialograum die Gruppenausstellung \"NAHT\" mit zehn künstlerischen Praktiken."


class FakeClient:
    def __init__(self, kind):
        self.kind = kind
        self.calls = 0

    def structured(self, **_kwargs):
        self.calls += 1
        return {"kind": self.kind}, None


class TitleKindTests(unittest.TestCase):
    def test_only_short_titles_without_a_type_word_need_a_kind(self):
        for title in ("NAHT", "Echos", "Die Zauberflöte", "Tumult 61", "Harris & Ford", "1984",
                      "Das Elmar-Abenteuer", "Touch/ing Transformation", "Telekom Starkick 2026"):
            self.assertTrue(title_kind.needs_kind(title), title)
        for title in ("Laternenbasteln", "Jamsession", "Klavierrecital", "Kunstspaziergang", "Open Mic",
                      "Flohmarkt", "Rheinaue parkrun", "Martinszug Beuel", "Spielenachmittag", "Kreativ-Tag",
                      "Offene Kinderwerkstatt", "Ein Abend mit Musik und Gästen", ""):
            self.assertFalse(title_kind.needs_kind(title), title)

    def test_kind_must_be_grounded_short_and_free_of_title_place_and_numbers(self):
        def clean(kind, material=NAHT_TEXT):
            return title_kind.clean_kind(kind, title="NAHT", venue="Dialograum Kreuzung", city="Bonn", material=material)

        self.assertEqual("Gruppenausstellung", clean("Gruppenausstellung."))
        self.assertEqual("Stadtrundgang", clean("Stadtrundgang", "Der Rundgang führt durch die Nordstadt."))
        self.assertEqual("Jazzkonzert", clean("Jazzkonzert", "Kammermusik im Umfeld des Third Stream Jazz."))
        for rejected in (None, "", "Ausstellung NAHT", "Ausstellung in Bonn", "Theater ab 6 Jahren",
                         "gruppenausstellung", "Exklusivführung", "Einzigartig spektakuläre Gruppenausstellung",
                         "Eine sehr lange Bezeichnung mit vielen Wörtern hier"):
            self.assertEqual("", clean(rejected), rejected)
        # World knowledge without support in the source text is dropped.
        self.assertEqual("", clean("European Outdoor Film Tour", "Out of the box"))

    def test_answers_are_cached_by_title_and_text(self):
        client = FakeClient("Gruppenausstellung")
        with closing(sqlite3.connect(":memory:")) as connection:
            args = {"model": "m", "title": "NAHT", "category": "Ausstellung", "venue": "Dialograum", "city": "Bonn",
                    "material": NAHT_TEXT}
            self.assertEqual("Gruppenausstellung", title_kind.resolve(connection, client, **args))
            self.assertEqual("Gruppenausstellung", title_kind.resolve(connection, client, **args))
            self.assertEqual(1, client.calls)
            client.kind = None
            self.assertEqual("", title_kind.resolve(connection, client, **{**args, "material": "Anderer Text."}))
            self.assertEqual(2, client.calls)

    def test_publication_sets_the_kind_only_for_opaque_titles_with_prose(self):
        events = [
            validate_event({"title": "NAHT", "source": "Bonn.de", "source_id": "bonn-de-events", "date": "2026-10-10",
                            "city": "Bonn", "score": 1.0, "ai_summary": NAHT_TEXT}),
            validate_event({"title": "Flohmarkt", "source": "Bonn.de", "source_id": "bonn-de-events",
                            "date": "2026-10-10", "city": "Bonn", "score": 1.0, "ai_summary": "Trödel am Rhein."}),
            validate_event({"title": "Echos", "source": "Bonn.de", "source_id": "bonn-de-events", "date": "2026-10-10",
                            "city": "Bonn", "score": 1.0}),
        ]
        client = FakeClient("Gruppenausstellung")
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(publication_enrichment.ai_enrichment, "settings_from_env") as settings, \
                patch.object(publication_enrichment.ai_transport, "ResponsesClient", return_value=client):
            settings.return_value.enabled = True
            settings.return_value.api_key = "key"
            settings.return_value.provider = "openai"
            settings.return_value.model = "m"
            settings.return_value.cache_db = Path(directory) / "ai.sqlite"
            outcomes = publication_enrichment._resolve_title_kinds(events, {})
        self.assertEqual({"named": 1, "no_text": 1}, dict(outcomes))
        self.assertEqual(["Gruppenausstellung", "", ""], [event.title_kind for event in events])
        self.assertEqual(1, client.calls)


if __name__ == "__main__":
    unittest.main()
