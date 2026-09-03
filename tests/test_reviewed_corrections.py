import unittest
from datetime import date

from nrw_events import reviewed_corrections


class ReviewedCorrectionsTests(unittest.TestCase):
    def test_reviewed_entries_expire_instead_of_reaching_future_editions(self):
        self.assertTrue(
            reviewed_corrections.active_entries(
                "bonn_press_primary_urls", date(2026, 9, 1),
            )
        )
        self.assertEqual(
            reviewed_corrections.active_entries(
                "bonn_press_primary_urls", date(2027, 1, 1),
            ),
            (),
        )

    def test_unknown_group_is_empty(self):
        self.assertEqual(
            reviewed_corrections.active_entries("unknown", date(2026, 1, 1)),
            (),
        )


if __name__ == "__main__":
    unittest.main()
