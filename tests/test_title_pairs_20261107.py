"""Same-title duplicates from the 2026-11-06 production feed, reviewed 2026-11-07."""

import unittest

from nrw_events.deduplication import deduplicate
from nrw_events.duplicate_identity import events_are_duplicates
from nrw_events.validation import validate_event


def _event(**fields):
    event = {
        "description": "", "price": "", "score": 1.0, "status": "scheduled", "category_key": "workshop",
        "start_at": "", "end_at": "", "all_day": False, "venue_id": "", "venue_address": "", "venue": "",
    }
    event.update(fields)
    event.setdefault("date", event["start_date"])
    event.setdefault("end_date", event["start_date"])
    return event


class TitlePairTests(unittest.TestCase):
    def test_museum_library_is_the_lvr_landesmuseum(self):
        museum = _event(title="Objektbegutachtung", venue="LVR-LandesMuseum Bonn", city="Bonn",
                        source="LVR-LandesMuseum", source_id="lvr-landesmuseum-bonn", start_date="2026-12-02",
                        start_at="2026-12-02T14:00+01:00", category_key="exhibition",
                        link="https://landesmuseum-bonn.lvr.de/objektbegutachtung")
        bonn_de = _event(title="Objektbegutachtung", venue="Bibliothek des LVR-Landesmusuems Bonn", city="Bonn",
                         venue_address="Colmantstraße 18, 53115 Bonn", source="Bonn.de Events",
                         source_id="bonn-de-events", start_date="2026-12-02", start_at="2026-12-02T14:00+01:00",
                         end_at="2026-12-02T16:00+01:00", category_key="activities",
                         link="https://www.bonn.de/objektbegutachtung.php")
        self.assertTrue(events_are_duplicates(museum, bonn_de))
        self.assertTrue(events_are_duplicates(bonn_de, museum))

    def test_siegburg_project_room_has_two_names(self):
        siegburg = _event(title="Flatland", venue="Künstlerischer Projektraum", city="Siegburg", source="Siegburg",
                          source_id="siegburg", start_date="2026-11-07", start_at="2026-11-07T14:00+01:00",
                          link="https://events.siegburg.de/Veranstaltungen/FLATLAND.html")
        bonn_de = _event(title="Flatland", venue="Projektraum für zeitgenössische Kunst", city="Siegburg",
                         source="Bonn.de Events", source_id="bonn-de-events", start_date="2026-11-07",
                         start_at="2026-11-07T14:00+01:00", link="https://www.bonn.de/flatland.php")
        self.assertTrue(events_are_duplicates(siegburg, bonn_de))

    def test_two_venueless_listings_a_quarter_hour_apart_fold(self):
        def listing(source, source_id, start_at):
            return validate_event(_event(title="Döörper Repair Café", city="Ruppichteroth", source=source,
                                         source_id=source_id, start_date="2026-11-21", start_at=start_at,
                                         link=f"https://example.org/{source_id}"))
        rows = [listing("Bröltal / Ruppichteroth", "broeltal-ruppichteroth-events", "2026-11-21T10:00+01:00"),
                listing("ionas4 regional", "ionas4-ruppichteroth", "2026-11-21T10:15+01:00")]
        self.assertEqual(len(deduplicate(rows)), 1)
        # A venue on only one side keeps them apart: the empty venue is part of the key.
        rows[1] = validate_event({**rows[1].to_dict(), "venue": "Bürgerhaus"})
        self.assertEqual(len(deduplicate(rows)), 2)


if __name__ == "__main__":
    unittest.main()
