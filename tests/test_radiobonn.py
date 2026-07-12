import unittest

from scripts.nrw_events.sources import radiobonn
from scripts.nrw_events.sources import SOURCES


class RadioBonnLocationTests(unittest.TestCase):
    def test_adapter_is_registered(self):
        self.assertIs(SOURCES["Radio Bonn/Rhein-Sieg"], radiobonn.fetch)

    def test_specific_city_wins_over_bonn_mentions(self):
        text = "Eitorf Live auf dem Marktplatz, empfohlen von Radio Bonn"
        self.assertEqual(radiobonn._city_for(text), "Eitorf")

    def test_meeting_point_wins_over_organizer_location(self):
        text = (
            "Führung der VHS Bornheim/Alfter. Treffpunkt ist am Legionslager "
            "in der Graurheindorfer Straße in Bonn."
        )
        self.assertEqual(radiobonn._city_for(text), "Bonn")

    def test_configured_meeting_point_city_wins_over_hinted_organizer_location(self):
        text = "Veranstaltet von der VHS Alfter. Treffpunkt ist am Rathaus in Bornheim."
        self.assertEqual(radiobonn._city_for(text), "bornheim")


if __name__ == "__main__":
    unittest.main()
