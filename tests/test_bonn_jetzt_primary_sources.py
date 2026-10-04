"""First-party replacements for events previously published only via Bonn.jetzt."""

import unittest
from datetime import datetime

from nrw_events.bonn_jetzt_fallbacks import partition_bonn_jetzt_fallbacks
from nrw_events.sources import SOURCES, bonn_community, regional_common
from nrw_events.validation import canonicalize_event

from tests.helpers import patch_window

_STW_LISTING = """
<li aria-labelledby="news-headline-259" class="news__item"><a title="Spieleabend"
 href="/internationales-kultur/veranstaltungen/veranstaltung/spieleabend-jeden-1-montag-im-monat">
<time itemprop="datePublished" datetime="05.10.2026"> 05.10.2026 </time>
<h3 id="news-headline-259"><span itemprop="headline">Spieleabend - jeden 1. Montag im Monat</span></h3>
<div itemprop="description" id="news-teaser-259"><p>Jeden 1. Montag im Monat findet in der CAMPO der große Spieleabend statt.</p></div>
</a></li>
"""
_STW_DETAIL = """
<div class="news-text-wrap" itemprop="articleBody"><p>Das Studierendenwerk Bonn veranstaltet eine Spieleabend-Reihe.</p>
<p><strong>Wann: </strong>jeden 1. Montag im Monat, 17 - 22 Uhr</p>
<p><strong>Wo: </strong>CAMPO Campusmensa Poppelsdorf, Endenicher Allee 19, 53115 Bonn</p>
<p><strong>Eintritt:</strong> frei</p></div>
"""
_KB = """
<article class="card"><div class="h3 mb-0"> 27. Sept. </div><div class="kb-event-time">10:30 Uhr</div>
<h3 class="mb-0">Titus Dittmann liest</h3></article>
<article class="card"><div class="h3 mb-0"> 11. Okt. </div><h3 class="mb-0">Abschlussfest</h3></article>
"""
_DATENBURG = """<h3>Offener Burgabend</h3><p>Die Datenburg öffnet jeden Dienstag ab 18 Uhr ihre
Tore in der Bornheimer Straße 25 in der Bonner Altstadt und alle interessierten Wesen sind
herzlich eingeladen vorbeizuschauen. Keinerlei Vorwissen benötigt, nur Neugier!</p>"""
_VHS_ROW = """<tr data-href="//www.vhs-bonn.de/x?courseId=484-C-{cid}&rowIndex=1" class="">
<td class="stateIcon"><i class="coursestate {state}"></i></td>
<td class="title"><a href="#" class="title"> Decolonial AI <span class="subtitle">An Indigenous African Perspective</span> </a></td>
<td class="startDate "> Mo., 05.10.2026 <span class="subtitle">18:00 Uhr</span> </td></tr>"""
_VHS_ICAL = """BEGIN:VCALENDAR
BEGIN:VTIMEZONE
DTSTART:19961027T030000
END:VTIMEZONE
BEGIN:VEVENT
DTSTART;TZID=Europe/Berlin:20261005T180000
DTEND;TZID=Europe/Berlin:20261005T193000
SUMMARY:Decolonial AI
LOCATION:VHS\\, Mülheimer Platz 1\\, Raum 3.49\\, Mülheimer Platz 1\\, 53111 Bonn
END:VEVENT
END:VCALENDAR"""


def _bonn_jetzt(**overrides):
    return canonicalize_event({
        "title": "Regelmäßiger Spieleabend", "source": "Bonn.jetzt", "source_id": "bonn-jetzt",
        "start_date": "2026-10-05", "end_date": "2026-10-05",
        "start_at": "2026-10-05T17:00+02:00", "end_at": "2026-10-05T22:00+02:00",
        "time": "17:00–22:00", "venue": "CAMPO Campusmensa Poppelsdorf", "city": "Bonn",
        "link": "https://bonn.jetzt/event/regelmassiger-spieleabend-13", "score": 5.0,
        **overrides,
    })


class BonnJetztPrimarySourceTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 9, 27), datetime(2026, 10, 20))

    def test_sources_are_registered(self):
        for name in ("Studierendenwerk Bonn", "Käpt’n Book Lesefest", "Datenburg", "VHS Bonn", "bitcircus101"):
            self.assertIn(name, SOURCES)

    def test_studierendenwerk_reads_listing_date_and_detail_facts(self):
        [event] = bonn_community.events_from_studierendenwerk(_STW_LISTING, lambda _url: _STW_DETAIL)
        self.assertEqual(event["title"], "Spieleabend")
        self.assertEqual(event["start_at"], "2026-10-05T17:00+02:00")
        self.assertEqual(event["time"], "17:00–22:00")
        self.assertEqual(event["venue"], "CAMPO Campusmensa Poppelsdorf")
        self.assertEqual(event["price"], "kostenlos")
        self.assertIn("studierendenwerk-bonn.de", event["link"])

    def test_kaeptn_book_is_one_festival_spanning_its_programme(self):
        [event] = bonn_community.events_from_kaeptn_book(_KB)
        self.assertEqual(event["title"], "Käpt’n Book Lesefest 2026")
        self.assertEqual((event["start_date"], event["end_date"]), ("2026-09-27", "2026-10-11"))

    def test_datenburg_expands_published_weekly_opening(self):
        events = bonn_community.events_from_datenburg(_DATENBURG)
        self.assertEqual([e["date"] for e in events], ["2026-09-29", "2026-10-06", "2026-10-13", "2026-10-20"])
        self.assertTrue(all(e["time"] == "18:00" for e in events))
        with self.assertRaises(regional_common.ParserEmptyError):
            bonn_community.events_from_datenburg("<p>geschlossen</p>")

    def test_vhs_keeps_single_session_in_person_courses_only(self):
        rows = "".join((
            _VHS_ROW.format(cid="N1538", state="Course_Bookable_OK"),
            _VHS_ROW.format(cid="N1538ON", state="Course_Bookable_OK"),
            _VHS_ROW.format(cid="N1539", state="Course_NotBookable_CourseCancelled"),
            _VHS_ROW.format(cid="N1540", state="Course_Bookable_OK"),
        ))
        calendars = {"N1538": _VHS_ICAL, "N1540": _VHS_ICAL.replace("END:VCALENDAR", "BEGIN:VEVENT\nEND:VEVENT")}
        events = bonn_community.events_from_vhs(rows, lambda url: calendars[url.split("484-C-")[1].split("/")[0]])
        [event] = events
        self.assertEqual(event["start_at"], "2026-10-05T18:00+02:00")
        self.assertEqual(event["venue"], "VHS Bonn")
        self.assertEqual(event["venue_address"], "Mülheimer Platz 1, Raum 3.49, 53111 Bonn")
        self.assertEqual(event["link"], "https://www.vhs-bonn.de/kurs/N1538")

    def test_bitcircus_keeps_only_own_space(self):
        props = {"LOCATION": "Dorotheenstraße 101, 53111 Bonn"}
        self.assertTrue(bonn_community._at_bitcircus(props, datetime.now(), datetime.now()))
        self.assertFalse(bonn_community._at_bitcircus({"LOCATION": "Bornheimer Straße 25"}, datetime.now(), datetime.now()))

    def test_primary_replaces_bonn_jetzt_and_inherits_its_id(self):
        [primary] = bonn_community.events_from_studierendenwerk(_STW_LISTING, lambda _url: _STW_DETAIL)
        primary = canonicalize_event(primary)
        fallback = _bonn_jetzt()
        kept, replaced = partition_bonn_jetzt_fallbacks([fallback, primary])
        self.assertEqual([e.source_id for e in kept], ["studierendenwerk-bonn"])
        self.assertEqual(replaced, [fallback])
        self.assertTrue(kept[0].previous_event_ids)

    def test_bonn_jetzt_stays_without_matching_primary(self):
        [primary] = bonn_community.events_from_studierendenwerk(_STW_LISTING, lambda _url: _STW_DETAIL)
        primary = canonicalize_event(primary)
        for fallback in (
            _bonn_jetzt(title="Blinde Kuh"),
            _bonn_jetzt(start_date="2026-10-06", end_date="2026-10-06",
                        start_at="2026-10-06T17:00+02:00", end_at="2026-10-06T22:00+02:00"),
            _bonn_jetzt(start_at="2026-10-05T19:30+02:00"),
            _bonn_jetzt(venue="Fabrik45"),
            _bonn_jetzt(city="Köln"),
        ):
            with self.subTest(fallback=fallback.title):
                kept, replaced = partition_bonn_jetzt_fallbacks([fallback, primary])
                self.assertEqual(replaced, [])
                self.assertEqual(len(kept), 2)

    def test_aggregator_never_replaces_bonn_jetzt(self):
        other = canonicalize_event({**_bonn_jetzt().to_dict(), "source": "Meetup", "source_id": "meetup"})
        _kept, replaced = partition_bonn_jetzt_fallbacks([_bonn_jetzt(), other])
        self.assertEqual(replaced, [])


if __name__ == "__main__":
    unittest.main()
