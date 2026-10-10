import unittest

from nrw_events.event_details import canonical_details
from nrw_events.identity import content_hash
from nrw_events.validation import canonicalize_event

from tests.test_public_event_contract import raw_event


class EventDetailsTests(unittest.TestCase):
    def test_canonical_details_keeps_only_known_clean_values(self):
        self.assertEqual(canonical_details({
            "performance": "premiere",
            "age": " ab <b>12</b> Jahren ",
            "language": "",
            "performers": ["Regie: A", "Regie: A", "", 3],
            "programme": "not a list",
            "video_url": "javascript:alert(1)",
            "unknown": "x",
        }), {"performance": "premiere", "age": "ab 12 Jahren", "performers": ["Regie: A"]})
        self.assertEqual(canonical_details({"performance": "gala", "video_url": "http://insecure.test"}), {})
        self.assertEqual(canonical_details(["not", "a", "dict"]), {})

    def test_malformed_optional_details_are_dropped_not_raised(self):
        self.assertEqual(canonical_details({"video_url": "https://[invalid", "age": "ab 6 Jahren"}), {"age": "ab 6 Jahren"})
        self.assertEqual(canonical_details({"performance": ["premiere"]}), {})
        self.assertEqual(canonical_details({"performance": {"premiere": 1}}), {})
        event = canonicalize_event(raw_event(details={"video_url": "https://[invalid", "performance": ["premiere"]}))
        self.assertEqual(event.details, {})

    def test_details_reach_the_canonical_event_but_not_the_content_hash(self):
        plain = canonicalize_event(raw_event())
        enriched = canonicalize_event(raw_event(details={"age": "ab 6 Jahren", "video_url": "https://vimeo.com/1"}))
        self.assertEqual(plain.details, {})
        self.assertEqual(enriched.details, {"age": "ab 6 Jahren", "video_url": "https://vimeo.com/1"})
        self.assertEqual(content_hash(plain.to_dict()), content_hash(enriched.to_dict()))


if __name__ == "__main__":
    unittest.main()
