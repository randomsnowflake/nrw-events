import unittest

from nrw_events import report


def _event(**updates):
    event = {
        "end_at": "",
        "city": "Bonn",
        "venue_id": "",
        "description": "",
        "price": "",
        "score": 1.0,
        "status": "scheduled",
    }
    event.update(updates)
    return event


def _day(date, start_at, end_at="", **updates):
    return _event(
        date=date, start_date=date, end_date=date,
        start_at=start_at, end_at=end_at, time=start_at[11:16], **updates,
    )


def _theater_werther(**updates):
    return _day(
        "2026-10-25", "2026-10-25T18:00+01:00",
        title="Die Leiden des jungen Werther",
        category_key="stage",
        source="Theater Bonn",
        source_id="theater-bonn",
        venue="Schauspielhaus Bad Godesberg",
        venue_id="schauspielhaus-bad-godesberg",
        venue_address="Am Michaelshof 9, 53177 Bonn",
        link="https://www.theater-bonn.de/de/programm/die-leiden-des-jungen-werther/235666",
        score=0.91,
    ) | updates


def _bonn_werther(**updates):
    return _day(
        "2026-10-25", "2026-10-25T19:30+01:00",
        title="Die Leiden des jungen Werther",
        category_key="stage",
        source="Bonn.de Events",
        source_id="bonn-de-events",
        venue="Schauspiel",
        venue_address="Theaterplatz 5, 53177 Bonn",
        link=(
            "https://www.bonn.de/veranstaltungskalender/veranstaltungen/"
            "hauptkalender/theater-bonn/die-leiden-des-jungen-werther.php"
        ),
    ) | updates


def _repair_cafe_workshop(**updates):
    return _day(
        "2026-11-06", "2026-11-06T17:30+01:00", "2026-11-06T20:00+01:00",
        title="Fahrrad-Selbsthilfe-Werkstatt Lannesdorf",
        category_key="workshop",
        source="Repair Cafés Bonn",
        source_id="repair-cafes-bonn",
        venue="Fahrrad-Selbsthilfe-Werkstatt Lannesdorf",
        venue_address="Ellesdorfer Str. 40 53179 Bonn",
        link=(
            "https://www.repaircafesbonn.de/mc-events/"
            "fahrrad-selbsthilfe-werkstatt-lannesdorf/?mc_id=3096"
        ),
        score=0.79,
    ) | updates


def _adfc_workshop(**updates):
    return _day(
        "2026-11-06", "2026-11-06T17:30+01:00", "2026-11-06T20:00+01:00",
        title="Fahrrad-Selbsthilfe-Werkstatt Lannesdorf/Mehlem",
        category_key="workshop",
        source="ADFC Bonn/Rhein-Sieg",
        source_id="adfc-bonn",
        venue="Ellesdorfer",
        venue_address="Ellesdorfer Straße 50, 53179 Bonn",
        link=(
            "https://touren-termine.adfc.de/radveranstaltung/"
            "183997-fahrradselbsthilfewerkstatt-lannesdorfmehlem"
        ),
        score=0.97,
    ) | updates


def _institut_fotowalk(**updates):
    return _day(
        "2026-10-30", "2026-10-30T15:00+01:00", "2026-10-30T19:00+01:00",
        title="Fotowalk französisches Bonn",
        category_key="workshop",
        source="Institut français Bonn",
        source_id="institut-francais-bonn",
        venue="LVR Landesmuseum bis Mediathek des Institut français Bonn",
        venue_address="Colmantstraße 14-16, 53115 Bonn",
        link=(
            "https://www.ifb.uni-bonn.de/kultur/veranstaltungen/"
            "copy_of_fotowalk-franzoesisches-bonn"
        ),
        score=0.98,
    ) | updates


def _bonn_fotowalk(**updates):
    return _day(
        "2026-10-30", "2026-10-30T15:00+01:00", "2026-10-30T19:00+01:00",
        title="Fotowalk",
        category_key="workshop",
        source="Bonn.de Events",
        source_id="bonn-de-events",
        venue="LVR-LandesMuseum Bonn",
        venue_id="lvr-landesmuseum-bonn",
        venue_address="Colmantstraße 14-16, Bonn",
        link=(
            "https://www.bonn.de/veranstaltungskalender/veranstaltungen/"
            "hauptkalender/extern/Fotowalk.php"
        ),
        score=0.91,
    ) | updates


def _brueckenforum_flohmarkt(**updates):
    return _day(
        "2026-10-25", "2026-10-25T11:00+01:00", "2026-10-25T17:00+01:00",
        title="Floh- und Trödelmarkt Beueler Rathausplatz",
        category_key="market",
        source="Brückenforum Bonn",
        source_id="brueckenforum-bonn",
        venue="Beueler Rathausplatz (Möhneplatz)",
        link=(
            "https://www.brueckenforum.de/events/"
            "ausser-haus-floh-und-troedelmarkt-auf-dem-rathausplatz-5/"
        ),
        score=0.95,
    ) | updates


def _beuel_net_flohmarkt(**updates):
    return _event(
        date="2026-10-25", start_date="2026-10-25", end_date="2026-10-25",
        start_at="", time="11:00–17:00",
        title="Flohmarkt am Beueler Rathaus",
        category_key="market",
        source="Beuel.net",
        source_id="beuel-net",
        city="Bonn-Beuel",
        venue="Möhneplatz",
        link="https://beuelhats.de/",
        score=0.9,
    ) | updates


class ReviewedSourceConflictDedupTests(unittest.TestCase):
    """Pairs from the 2026-10-19 release, reviewed against the publishers."""

    def assert_folds_into(self, confirmed, other, **expected):
        for events in ([confirmed, other], [other, confirmed]):
            with self.subTest(first_source=events[0]["source_id"]):
                [event] = report.deduplicate([dict(row) for row in events])
                for field, value in expected.items():
                    self.assertEqual(event[field], value)

    def test_bonn_calendar_werther_with_wrong_clock_folds_into_theater(self):
        self.assert_folds_into(
            _theater_werther(), _bonn_werther(),
            source_id="theater-bonn", start_at="2026-10-25T18:00+01:00",
        )

    def test_adfc_workshop_with_wrong_house_number_folds_into_repair_cafe(self):
        self.assert_folds_into(
            _repair_cafe_workshop(), _adfc_workshop(),
            source_id="repair-cafes-bonn",
            venue_address="Ellesdorfer Str. 40 53179 Bonn",
        )

    def test_generic_bonn_fotowalk_folds_into_institut_listing(self):
        self.assert_folds_into(
            _institut_fotowalk(), _bonn_fotowalk(),
            source_id="institut-francais-bonn",
        )

    def test_beuel_net_rathaus_flohmarkt_folds_into_brueckenforum(self):
        self.assert_folds_into(
            _brueckenforum_flohmarkt(), _beuel_net_flohmarkt(),
            source_id="brueckenforum-bonn",
        )

    def test_beuel_rathaus_flohmarkt_months_stay_apart(self):
        september = _beuel_net_flohmarkt(
            date="2026-09-27", start_date="2026-09-27", end_date="2026-09-27",
        )
        self.assertEqual(
            len(report.deduplicate([_brueckenforum_flohmarkt(), september])), 2
        )

    def test_reviewed_families_stay_apart_on_different_days(self):
        bonn = _bonn_werther(
            date="2026-11-16", start_date="2026-11-16", end_date="2026-11-16",
            start_at="2026-11-16T19:30+01:00",
        )
        self.assertEqual(len(report.deduplicate([_theater_werther(), bonn])), 2)

    def test_two_theater_performances_on_one_day_stay_apart(self):
        matinee = _theater_werther(
            start_at="2026-10-25T11:00+01:00", time="11:00",
            link=_theater_werther()["link"] + "#matinee",
        )
        self.assertEqual(len(report.deduplicate([_theater_werther(), matinee])), 2)

    def test_other_bonn_fotowalk_at_another_time_stays_apart(self):
        other = _bonn_fotowalk(start_at="2026-10-30T10:00+01:00", end_at="", time="10:00")
        self.assertEqual(len(report.deduplicate([_institut_fotowalk(), other])), 2)


if __name__ == "__main__":
    unittest.main()
