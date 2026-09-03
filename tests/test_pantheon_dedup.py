import unittest

from nrw_events import report


class PantheonDedupTests(unittest.TestCase):
    def _event(self, **updates):
        event = {
            "date": "2026-09-03",
            "start_date": "2026-09-03",
            "end_date": "2026-09-03",
            "start_at": "2026-09-03T20:00+02:00",
            "end_at": "",
            "time": "20:00",
            "city": "Bonn",
            "venue": "Pantheon Theater",
            "venue_id": "pantheon-theater",
            "venue_address": "Siegburger Straße 42, 53229 Bonn",
            "description": "",
            "price": "",
            "score": 1.0,
            "status": "scheduled",
        }
        event.update(updates)
        return event

    def _pantheon_event(self, **updates):
        event = self._event(
            title=(
                "Die Geschwister Pfister präsentieren: · Ursli Pfister - "
                "Peggy March, Frau Huggenberger und ich"
            ),
            category_key="stage",
            source="Pantheon Bonn",
            source_id="pantheon-bonn",
            link="https://www.pantheon.de/programm/#t593871",
            score=1.34,
        )
        event.update(updates)
        return event

    def _bonn_event(self, **updates):
        event = self._event(
            title=(
                "Ursli Pfister & Jo Roloff Band - Peggy March, "
                "Frau Huggenberger und ich - Musik-Show"
            ),
            category_key="concert",
            source="Bonn.de Events",
            source_id="bonn-de-events",
            link=(
                "https://www.bonn.de/veranstaltungskalender/veranstaltungen/"
                "hauptkalender/extern/Ursli-Pfister-Jo-Roloff-Band-Peggy-March-"
                "Frau-Huggenberger-und-ich-Musik-Show.php"
            ),
            end_at="2026-09-03T22:15+02:00",
            score=1.29,
        )
        event.update(updates)
        return event

    def test_pantheon_primary_record_replaces_bonn_calendar_copy(self):
        for events in (
            [self._bonn_event(), self._pantheon_event()],
            [self._pantheon_event(), self._bonn_event()],
        ):
            with self.subTest(first_source=events[0]["source"]):
                [event] = report.deduplicate(events)

                self.assertEqual(event["source"], "Pantheon Bonn")
                self.assertEqual(event["source_id"], "pantheon-bonn")
                self.assertEqual(
                    event["link"], "https://www.pantheon.de/programm/#t593871"
                )
                self.assertEqual(event["title"], self._pantheon_event()["title"])
                self.assertEqual(event["category_key"], "stage")
                self.assertEqual(event["end_at"], "2026-09-03T22:15+02:00")

    def test_same_alias_titles_at_different_times_stay_distinct(self):
        bonn = self._bonn_event(
            start_at="2026-09-03T18:00+02:00",
            end_at="2026-09-03T20:15+02:00",
            time="18:00",
        )

        self.assertEqual(len(report.deduplicate([self._pantheon_event(), bonn])), 2)

    def test_same_alias_titles_at_conflicting_addresses_stay_distinct(self):
        bonn = self._bonn_event(
            venue="Pantheon Außenbühne",
            venue_id="",
            venue_address="Siegburger Straße 100, 53229 Bonn",
        )

        self.assertEqual(len(report.deduplicate([self._pantheon_event(), bonn])), 2)

    def test_other_pantheon_programme_is_not_absorbed(self):
        other = self._bonn_event(
            title="Ursli Pfister & Jo Roloff Band - Ein anderes Programm",
        )

        self.assertEqual(len(report.deduplicate([self._pantheon_event(), other])), 2)


if __name__ == "__main__":
    unittest.main()
