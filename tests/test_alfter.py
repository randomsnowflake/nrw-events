import unittest

from nrw_events.sources import regional_html


class AlfterCalendarTests(unittest.TestCase):
    def test_official_empty_notice_is_expected(self):
        html = """
        <div class="news">
          <div class="alert">
            Derzeit sind keine Einträge (unter dieser Rubrik) verfügbar.
          </div>
        </div>
        """

        self.assertTrue(regional_html._alfter_calendar_is_expected_empty(html))

    def test_unrecognized_empty_page_still_counts_as_parser_drift(self):
        self.assertFalse(
            regional_html._alfter_calendar_is_expected_empty(
                "<html><main>Calendar layout changed</main></html>"
            )
        )


if __name__ == "__main__":
    unittest.main()