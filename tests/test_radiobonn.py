import unittest

from scripts.nrw_events.sources import radiobonn
from scripts.nrw_events.sources import SOURCES


class RadioBonnLocationTests(unittest.TestCase):
    def test_adapter_is_registered(self):
        self.assertIs(SOURCES["Radio Bonn/Rhein-Sieg"], radiobonn.fetch)

    def test_specific_city_wins_over_bonn_mentions(self):
        text = "Eitorf Live auf dem Marktplatz, empfohlen von Radio Bonn"
        self.assertEqual(radiobonn._city_for(text), "Eitorf")


if __name__ == "__main__":
    unittest.main()
