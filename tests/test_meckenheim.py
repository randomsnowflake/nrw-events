import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

from nrw_events import common
from nrw_events.sources import meckenheim
from tests.helpers import patch_window


DETAIL_LINK = (
    "https://www.meckenheim.de/Leben-in-Meckenheim/Veranstaltungen/"
    "Feierabend-Radtour.php?object=tx,3947.4.1&ModID=11&FID=3947.636.1"
)


DETAIL_HTML = """
<meta name="description" content="Wir fahren gemütlich rund um Meckenheim. Nach der Tour wird gemeinsam eingekehrt.">
<h1 class="page-title">Feierabend-Radtour</h1>
<dt class="object-data_field">Uhrzeit:</dt>
<dd class="object-data_value">18:00 bis 21:00&nbsp;Uhr</dd>
<dt class="object-data_field">Veranstaltungsort:</dt>
<dd class="object-data_value"><p>Rathaus Meckenheim</p></dd>
<dt class="object-data_field">Ortschaft:</dt>
<dd class="object-data_value">Meckenheim</dd>
<dt class="object-data_field">Preis:</dt>
<dd class="object-data_value">kostenlos</dd>
"""


class MeckenheimDetailTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 7, 13), datetime(2026, 7, 27))
        meckenheim._reset_detail_context_cache()

    def tearDown(self):
        meckenheim._reset_detail_context_cache()

    def test_fetch_enriches_listing_event_from_detail_page(self):
        listing = f"""
<li class="result-list_item">
  <h3 class="result-list_object-title"><a href="{DETAIL_LINK}">Feierabend-Radtour</a></h3>
  <time datetime="2026-07-13 18:00:00">13.07.2026</time>
</li>
"""

        with tempfile.TemporaryDirectory() as cache_dir, patch.dict(
            "os.environ", {"NRW_EVENTS_CACHE_DIR": cache_dir}
        ), patch.object(
            common,
            "fetch_url",
            side_effect=lambda url, **kwargs: listing if url == meckenheim._URL else DETAIL_HTML,
        ):
            events = meckenheim.fetch()

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["description"], "Wir fahren gemütlich rund um Meckenheim. Nach der Tour wird gemeinsam eingekehrt.")
        self.assertEqual(events[0]["venue"], "Rathaus Meckenheim")
        self.assertEqual(events[0]["time"], "18:00 bis 21:00")
        self.assertEqual(events[0]["end_at"], "2026-07-13T21:00+02:00")
        self.assertEqual(events[0]["price"], "kostenlos")

    def test_persistent_cache_avoids_reloading_detail_page(self):
        with tempfile.TemporaryDirectory() as cache_dir, patch.dict(
            "os.environ",
            {
                "NRW_EVENTS_CACHE_DIR": cache_dir,
                "NRW_EVENTS_DETAIL_CACHE_TTL_HOURS": "24",
            },
        ):
            with patch.object(common, "fetch_url", return_value=DETAIL_HTML) as fetch_url:
                first = meckenheim._fetch_detail_context(DETAIL_LINK)
            self.assertEqual(fetch_url.call_count, 1)

            meckenheim._reset_detail_context_cache()
            with patch.object(common, "fetch_url", side_effect=AssertionError("cache miss")) as fetch_url:
                second = meckenheim._fetch_detail_context(DETAIL_LINK)

        self.assertEqual(first, second)
        fetch_url.assert_not_called()


if __name__ == "__main__":
    unittest.main()
