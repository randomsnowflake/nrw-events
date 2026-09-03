import unittest

from nrw_events import report


class RathaustreppeDedupTests(unittest.TestCase):
    def _event(self, **updates):
        event = {
            "date": "2026-09-10",
            "start_date": "2026-09-10",
            "end_date": "2026-09-10",
            "start_at": "2026-09-10T18:00+02:00",
            "end_at": "2026-09-10T20:00+02:00",
            "time": "18:00–20:00",
            "city": "Bonn-Beuel",
            "venue": "Beueler Rathausplatz",
            "category_key": "concert",
            "price": "kostenlos",
            "status": "scheduled",
        }
        event.update(updates)
        return event

    def _primary(self, **updates):
        event = self._event(
            title="Musik auf der Rathaustreppe: The Roots",
            source="Musik auf der Rathaustreppe",
            source_id="rathausmusik",
            link="http://www.rathausmusik.com/",
            description="Ausführlicher Originaltext über das Programm von The Roots.",
            description_source="scraped",
            score=1.48,
        )
        event.update(updates)
        return event

    def _discovery(self, **updates):
        event = self._event(
            title="Musik auf der Rathaustreppe: The Cotties (Beat/R&B)",
            venue="Möhneplatz",
            source="beuelhats.de",
            source_id="beuel-net",
            link="https://beuelhats.de/",
            description="Veralteter Sekundärtermin.",
            ai_summary="Bei Musik auf der Rathaustreppe spielen The Cotties.",
            score=1.4,
        )
        event.update(updates)
        return event

    def test_primary_schedule_replaces_conflicting_beuel_discovery_row(self):
        for events in (
            [self._primary(), self._discovery()],
            [self._discovery(), self._primary()],
        ):
            with self.subTest(first_source=events[0]["source_id"]):
                [event] = report.deduplicate(events)

                self.assertEqual(event["title"], self._primary()["title"])
                self.assertEqual(event["source_id"], "rathausmusik")
                self.assertEqual(event["link"], "http://www.rathausmusik.com/")
                self.assertEqual(event["description"], self._primary()["description"])
                self.assertNotIn("Cotties", event.get("ai_summary", ""))

    def test_conflicting_start_times_stay_separate(self):
        discovery = self._discovery(
            start_at="2026-09-10T19:00+02:00",
            end_at="2026-09-10T21:00+02:00",
            time="19:00–21:00",
        )

        self.assertEqual(len(report.deduplicate([self._primary(), discovery])), 2)

    def test_conflicting_concrete_addresses_stay_separate(self):
        discovery = self._discovery(
            venue="Ein anderer Platz",
            venue_address="Siegburger Straße 42, 53229 Bonn",
        )
        primary = self._primary(
            venue_address="Friedrich-Breuer-Straße 65, 53225 Bonn",
        )

        self.assertEqual(len(report.deduplicate([primary, discovery])), 2)

    def test_other_rathaustreppe_programmes_are_not_absorbed(self):
        discovery = self._discovery(
            title="Musik auf der Rathaustreppe: Eine andere Band",
        )

        self.assertEqual(len(report.deduplicate([self._primary(), discovery])), 2)


if __name__ == "__main__":
    unittest.main()
