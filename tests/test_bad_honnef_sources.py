"""Bad Honnef's local calendar can complement the municipal feed without shifting dates."""

from datetime import datetime
from unittest import TestCase
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from nrw_events.config import runtime_config
from nrw_events.deduplication import deduplicate
from nrw_events.duplicate_identity import events_are_duplicates
from nrw_events.health import SourceStatus
from nrw_events.runtime import EventWindow
from nrw_events.sources import bad_honnef
from nrw_events.validation import validate_event

_PRESS = """
<p><strong>Kaminzimmer – ein winterlicher Treffpunkt</strong></p>
<p>Der Verein lädt auf den Marktplatz ein.</p>
<ul><li>Kaminzimmer – Marktplatz</li></ul>
<p>15. November 2026 bis 6. Januar 2027, täglich von 16 bis 22 Uhr</p>
<ul><li>Winterkino – Dreiecksplatz am unteren Markt</li></ul>
<p>4. bis 6. Dezember 2026, Freitag bis Sonntag von 17 bis 22 Uhr</p>
"""
_CENTRUM = """
<h1>Kaminzimmer</h1><span>Beginn: 15.11.26</span>
<span>Ende: 06.01.27</span><span>Ort: Innenstadt</span>
"""


def _entry(title, start, end, prose, venue=None):
    return {
        "title": title,
        "url": "https://www.stadtjournal.online/event/test/",
        "start_date": start,
        "end_date": end,
        "description": f"<p>{prose}</p>",
        "venue": venue or [],
    }


class BadHonnefSourceTests(TestCase):
    def test_winterkino_is_three_evenings_and_keeps_the_city_provenance(self):
        item = _entry("Winterkino", "2026-12-04 17:00", "2026-12-06 22:00", "Bad Honnef. Das Winterkino findet am unteren Markt statt.")
        event = validate_event(bad_honnef._journal_event(item, _PRESS))
        self.assertEqual(event.venue, "Dreiecksplatz am unteren Markt")
        self.assertEqual(event.category_key, "cinema")
        self.assertEqual(event.time, "")
        self.assertEqual(
            [(slot["start_at"], slot["end_at"]) for slot in event.daily_schedule],
            [(f"2026-12-0{day}T17:00:00+01:00", f"2026-12-0{day}T22:00:00+01:00") for day in (4, 5, 6)],
        )
        self.assertIn(bad_honnef._CITY_ADVENT_URL, event.source_links)
        stale = validate_event(bad_honnef._journal_event(item, _PRESS.replace("2026", "2025")))
        self.assertEqual(stale.daily_schedule, [])
        self.assertEqual(stale.time_note, "")

    def test_kaminzimmer_uses_organizer_dates_and_matching_city_hours(self):
        event = validate_event(bad_honnef._centrum_event(_CENTRUM, _PRESS))
        self.assertEqual((event.start_date, event.end_date), ("2026-11-15", "2027-01-06"))
        self.assertEqual((event.venue, event.time_note), ("Marktplatz", "Täglich von 16 bis 22 Uhr"))
        self.assertEqual(event.daily_schedule, [])
        stale = validate_event(bad_honnef._centrum_event(_CENTRUM, _PRESS.replace("2026", "2025")))
        self.assertEqual((stale.venue, stale.time_note), ("Innenstadt", ""))
        with self.assertRaisesRegex(ValueError, "date range"):
            bad_honnef._centrum_event("<h1>Kaminzimmer</h1>", _PRESS)

    def test_selhofer_event_preserves_each_confirmed_start(self):
        prose = ("Bad Honnef/Selhof. Veranstaltung am Kelterplatz. Mittwoch 9. Dez. 2026, Uhr ab 18:00 Uhr. "
                 "Donnerstag 10. Dez. 2026, Uhr ab 18:00 Uhr. Freitag 11. Dez. 2026, Uhr ab 17:00 Uhr. "
                 "Samstag 12. Dez. 2026, Uhr ab 13:00 Uhr.")
        event = validate_event(bad_honnef._journal_event(
            _entry("Selhofer Adventsalm", "2026-12-09 18:00", "2026-12-12 18:00", prose), "",
        ))
        self.assertEqual(event.venue, "Kelterplatz")
        self.assertGreaterEqual(event.score, runtime_config().score_floor)
        self.assertEqual([slot["start_at"][11:16] for slot in event.daily_schedule], ["18:00", "18:00", "17:00", "13:00"])
        self.assertTrue(all(not slot["end_at"] for slot in event.daily_schedule))

    def test_public_primary_market_and_the_local_listing_match(self):
        item = _entry("Weihnachtsmarkt Rhöndorf", "2026-12-13 12:00", "2026-12-13 20:00", "Rhöndorf. Treffpunkt ist der Ziepchensplatz.")
        journal = validate_event(bad_honnef._journal_event(item, "")).to_dict()
        primary = {**journal, "title": "Rhöndorfer Weihnachtsmarkt", "source_id": "rhoendorf",
                   "source": "Bürger- und Ortsverein Rhöndorf",
                   "link": "https://www.rhoendorf.de/bewerbung-rhoendorfer-weihnachtsmarkt-2026/"}
        self.assertTrue(events_are_duplicates(journal, primary))
        merged = deduplicate([validate_event(journal), validate_event(primary)])
        self.assertEqual([event.link for event in merged], [primary["link"]])
        self.assertEqual(journal["event_types"], ["christmas-market"])
        item["venue"] = {"city": "Königswinter", "venue": "Marktplatz"}
        self.assertIsNone(bad_honnef._journal_event(item, ""))

    def test_stadtjournal_pages_are_bounded_and_partial_fetch_retains_records(self):
        window = EventWindow(datetime(2026, 11, 27), datetime(2026, 12, 24))
        item = _entry("Advent im Dorf", "2026-11-29 11:00", "2026-11-29 20:00", "Aegidienberg. Die Vereine laden zum Weihnachtsmarkt ein.")
        with (
            patch.object(bad_honnef.run_state, "runtime_window", return_value=window),
            patch.object(bad_honnef.http, "fetch_json", side_effect=[{"events": [item], "total_pages": 2}, OSError("unavailable")]) as fetch,
        ):
            result = bad_honnef.fetch_stadtjournal()
        self.assertEqual(result.status, SourceStatus.DEGRADED)
        self.assertEqual(len(result.events), 1)
        self.assertEqual(result.events[0]["venue"], "Platz vor St. Aegidius")
        query = parse_qs(urlsplit(fetch.call_args_list[0].args[0]).query)
        self.assertEqual(query["tags"], ["bad-honnef"])
        self.assertEqual(query["start_date"], ["2026-10-30"])
        self.assertEqual(fetch.call_count, 2)
