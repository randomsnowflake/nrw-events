import unittest
from datetime import datetime
from unittest.mock import patch

from nrw_events import event_builder, http
from nrw_events.deduplication import deduplicate
from nrw_events.normalization import resolve_venue
from nrw_events.sources import SOURCES, frauenmuseum, rpz_bonn
from nrw_events.validation import validate_event

from tests.helpers import patch_window

_RPZ_LIST = """
<div class="qodef-el-item qodef-item-space"><div class="qodef-eli-content-inner">
<h5 itemprop="name" class="qodef-eli-title"><a itemprop="url" href="https://rpz-bonn.de/events/amplified-bonn/">06.11.2026 &#8211; AMPLIFIED BONN, VOL. 2: CHAMBER OF ELEPHANTS // DONBASS</a></h5>
<div class="qodef-eli-info-time"><span class="qodef-eli-info-label">Einlass: 18:30 Uhr // Beginn: 19:00 Uhr</span></div>
<div class="qodef-eli-info-author"><span class="qodef-eli-info-label">Eintritt: Ak. 10,00€</span></div>
</div></div>
<div class="qodef-el-item qodef-item-space"><div class="qodef-eli-content-inner">
<h5 itemprop="name" class="qodef-eli-title"><a itemprop="url" href="https://rpz-bonn.de/events/undated/">Bald: Ohne Datum</a></h5>
</div></div>
"""

_FRAUENMUSEUM_ICS = """BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART;TZID=Europe/Berlin:20261114T150000
DTEND;TZID=Europe/Berlin:20261114T160000
SUMMARY:Kuratorinnenführung
DESCRIPTION:Führung durch die Ausstellung im Frauenmuseum.
URL:https://frauenmuseum.de/event/kuratorinnenfuehrung/
END:VEVENT
BEGIN:VEVENT
DTSTART;TZID=Europe/Berlin:20261105T153000
DTEND;TZID=Europe/Berlin:20261105T173000
SUMMARY:Zeichen-Treff am Donnerstag
DESCRIPTION:Der Zeichentreff findet im Café statt und jeder kann mitmachen.
LOCATION:Café Feminista\\, Im Krausfeld 10\\, Bonn\\, 53111\\, Germany
URL:https://frauenmuseum.de/event/zeichen-treff/
END:VEVENT
END:VCALENDAR
"""


def _bonn_copy(title, venue, address, start, end):
    return validate_event(event_builder.make_event(
        title, start, end, venue, "Bonn", "Kopie im Stadtkalender.",
        "https://www.bonn.de/veranstaltungskalender/veranstaltungen/hauptkalender/extern/copy.php",
        "Bonn.de Events", "kultur", 1.0, f"{start:%H:%M}", source_id="bonn-de-events",
    ) | {"venue_address": address})


class GroupOneVenueSourceTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 10, 31), datetime(2027, 12, 31))

    def test_sources_are_registered(self):
        self.assertIs(SOURCES["Frauenmuseum Bonn"], frauenmuseum.fetch)
        self.assertIs(SOURCES["RPZ Bonn"], rpz_bonn.fetch)

    def test_rpz_list_item_keeps_date_start_and_price(self):
        [event] = rpz_bonn.events_from_html(_RPZ_LIST)
        canonical = validate_event(event)
        self.assertEqual(canonical.start_date, "2026-11-06")
        self.assertEqual(canonical.time, "19:00")
        self.assertEqual(canonical.price, "Ak. 10,00€")
        self.assertEqual((canonical.venue_id, canonical.venue_address), ("rpz-bonn", "Moltkestraße 41, 53173 Bonn"))
        self.assertTrue(canonical.title.startswith("Amplified Bonn, Vol. 2"))

    def test_rpz_description_drops_the_leading_facts(self):
        html = (
            '<div class="qodef-ttevent-single-content"><p>Wann: 20.11.2026 Einlass: 18:30 Uhr // Beginn: 19:00 Uhr '
            "VK: 12,00 Euro / AK: 15,00 Euro</p><p>Drei Bands. Drei Spielarten Metal. Ein Abend mitten in Bonn.</p></div>"
        )
        self.assertTrue(rpz_bonn._detail_description(html, {})["description"].startswith("Drei Bands"))

    def test_rpz_copy_on_the_city_calendar_merges_into_the_venue_record(self):
        [event] = rpz_bonn.events_from_html(_RPZ_LIST)
        primary = validate_event(event)
        copy = _bonn_copy(
            "Amplified Bonn, Vol. 2: Chamber Of Elephants // Donbass", "RPZ Bonn",
            "Moltkestraße 41, 53173 Bonn", datetime(2026, 11, 6, 19), None,
        )
        [merged] = deduplicate([copy, primary])
        self.assertEqual(merged.source, "RPZ Bonn")
        self.assertEqual(merged.link, primary.link)

    def test_frauenmuseum_empty_location_means_the_house(self):
        with patch.object(http, "fetch_url", return_value=_FRAUENMUSEUM_ICS):
            events = [validate_event(event) for event in frauenmuseum.fetch()]
        self.assertEqual(len(events), 2)
        self.assertEqual({event.venue_id for event in events}, {"frauenmuseum-bonn"})
        self.assertEqual({event.venue_address for event in events}, {"Im Krausfeld 10, 53111 Bonn"})

    def test_frauenmuseum_short_title_on_the_city_calendar_merges(self):
        with patch.object(http, "fetch_url", return_value=_FRAUENMUSEUM_ICS):
            primary = next(validate_event(event) for event in frauenmuseum.fetch() if "Zeichen" in event["title"])
        copy = _bonn_copy(
            "Zeichen-Treff", "Cafe Feminista des Frauenmuseums Bonn", "Im Krausfeld 10, 53111 Bonn",
            datetime(2026, 11, 5, 15, 30), datetime(2026, 11, 5, 17, 30),
        )
        [merged] = deduplicate([copy, primary])
        self.assertEqual(merged.source, "Frauenmuseum Bonn")

    def test_vhs_names_reach_the_house_only_with_its_address(self):
        here = "Mülheimer Platz 1, 53111 Bonn"
        self.assertEqual(resolve_venue(f"Volkshochschule Bonn, {here}", "Bonn").venue_id, "haus-der-bildung-bonn")
        self.assertEqual(resolve_venue(f"VHS Bonn, {here}", "Bonn").venue_id, "haus-der-bildung-bonn")
        self.assertEqual(resolve_venue("VHS Bonn, Am Michaelshof 2, 53177 Bonn", "Bonn").venue_id, "")
        self.assertEqual(resolve_venue("Stadtbibliothek Bonn, Warschauer Straße 66, 53117 Bonn", "Bonn").venue_id, "")
        self.assertEqual(resolve_venue("Saal im Haus der Bildung", "Bonn").venue_id, "haus-der-bildung-bonn")


if __name__ == "__main__":
    unittest.main()
