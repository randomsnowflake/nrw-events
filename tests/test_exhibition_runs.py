import unittest

from nrw_events.exhibition_runs import merge_exhibition_opening_days
from nrw_events.identity import assign_event_ids, event_id
from nrw_events.validation import validate_event


def _day(day: str, *, time: str = "14:00–18:00", title: str = "Ausstellung 100 Jahre Bad Godesberg",
         status: str = "scheduled", city: str = "Bonn-Bad Godesberg", **extra):
    return validate_event({
        **extra,
        "status": status,
        "title": title,
        "source": "Bonn.de Events",
        "source_id": "bonn-de-events",
        "start_date": day,
        "time": time,
        "venue": "Haus an der Redoute",
        "city": city,
        "category_key": "exhibition",
        "link": "https://www.bonn.de/ausstellung.php",
        "link_kind": "detail",
        "score": 1.0,
    })


def _offer(day: str, *, time: str = "", **extra):
    """A self-guided tour that bonn.de repeats daily, with a start address but no venue name."""
    return validate_event({
        **extra,
        "status": "scheduled",
        "title": 'Spotlight Secrets Rätseltour "Zurück ins Mittelalter"',
        "source": "Bonn.de Events",
        "source_id": "bonn-de-events",
        "start_date": day,
        "time": time,
        "venue": "",
        "venue_address": "Markt 4, 53111 Bonn",
        "city": "Bonn",
        "category_key": "outdoor",
        "link": "https://www.bonn.de/rätseltour.php",
        "link_kind": "detail",
        "score": 1.0,
    })


def _show(day: str, *, time: str = "19:30–21:00", title: str = "Berühmt - von Markus Barth", source_id: str = "kleines-theater", **extra):
    """One performance of a play; the source lists every evening separately."""
    return validate_event({
        **extra,
        "status": "scheduled",
        "title": title,
        "source": "kleines theater",
        "source_id": source_id,
        "start_date": day,
        "time": time,
        "venue": "Kleines Theater Bad Godesberg",
        "city": "Bonn-Bad Godesberg",
        "category": "theater",
        "category_key": "stage",
        "link": f"https://kleinestheater.eu/event/beruehmt/{day}",
        "link_kind": "detail",
        "score": 1.0,
    })


class ExhibitionRunTests(unittest.TestCase):
    def test_opening_days_become_one_ranged_event_with_stable_id(self):
        days = [_day("2026-10-01"), _day("2026-10-02"), _day("2026-10-04")]
        day_ids = [event_id(day) for day in days]

        published_days = {"events": [{"event_id": identifier} for identifier in day_ids[:2]]}
        [merged] = merge_exhibition_opening_days(days, published_days)

        self.assertEqual((merged.start_date, merged.end_date), ("2026-10-01", "2026-10-04"))
        self.assertEqual([slot["date"] for slot in merged.daily_schedule], ["2026-10-01", "2026-10-02", "2026-10-04"])
        self.assertEqual((merged.time, merged.start_at), ("", ""))
        self.assertEqual(merged.previous_event_ids, day_ids[:2])  # the unpublished day needs no alias
        self.assertFalse(merged.dated_sessions)  # opening hours, not dated sessions

        # Next day: the first date has passed and left the window. The run
        # keeps its start, so the public ID does not move.
        published = assign_event_ids([merged])[0]
        [next_day] = merge_exhibition_opening_days(days[1:], {"events": [published]})
        self.assertEqual(next_day.start_date, "2026-10-01")
        self.assertEqual(event_id(next_day), published["event_id"])

    def test_final_opening_day_keeps_published_run_identity(self):
        for time in ("14:00–18:00", ""):
            with self.subTest(time=time):
                days = [_day("2026-10-01", time=time), _day("2026-10-04", time=time)]
                [published] = assign_event_ids(merge_exhibition_opening_days(days, {}))
                [last_day] = merge_exhibition_opening_days(days[-1:], {"events": [published]})
                self.assertEqual((last_day.start_date, last_day.end_date), ("2026-10-01", "2026-10-04"))
                self.assertEqual(event_id(last_day), published["event_id"])
                self.assertEqual(last_day.all_day, not bool(time))

    def test_new_standalone_day_and_unrelated_old_run_stay_separate(self):
        day = _day("2026-11-01")
        [old] = assign_event_ids(merge_exhibition_opening_days([_day("2026-10-01"), _day("2026-10-02")], {}))
        for previous in ({}, {"events": [old]}):
            self.assertEqual(merge_exhibition_opening_days([day], previous), [day])

    def test_retained_run_absorbs_one_fresh_day(self):
        [run] = merge_exhibition_opening_days([_day("2026-10-01"), _day("2026-10-02")], {})
        [merged] = merge_exhibition_opening_days([run, _day("2026-10-03")], {})
        self.assertEqual((merged.start_date, merged.end_date), ("2026-10-01", "2026-10-03"))
        self.assertEqual(event_id(merged), event_id(run))

    def test_day_labelled_with_the_municipality_joins_the_district_run(self):
        [run] = merge_exhibition_opening_days([_day("2026-10-01"), _day("2026-10-02")], {})
        [merged] = merge_exhibition_opening_days([run, _day("2026-10-03", city="Bonn")], {})
        self.assertEqual((merged.start_date, merged.end_date, merged.city), ("2026-10-01", "2026-10-03", "Bonn-Bad Godesberg"))
        self.assertEqual(event_id(merged), event_id(run))

    def test_tours_and_same_day_sessions_keep_their_own_pages(self):
        tours = [_day("2026-10-01", time="15:00–16:00"), _day("2026-10-02", time="15:00–16:00")]
        self.assertEqual(merge_exhibition_opening_days(tours, {}), tours)
        sessions = [_day("2026-10-01"), _day("2026-10-01", time="18:00–22:00"), _day("2026-10-02")]
        self.assertEqual(merge_exhibition_opening_days(sessions, {}), sessions)

    def test_retained_run_absorbs_fresh_days_instead_of_colliding(self):
        [run] = merge_exhibition_opening_days([_day("2026-10-01"), _day("2026-10-02")], {})
        [merged] = merge_exhibition_opening_days([run, _day("2026-10-02"), _day("2026-10-03")], {})
        self.assertEqual((merged.start_date, merged.end_date), ("2026-10-01", "2026-10-03"))
        self.assertEqual(event_id(merged), event_id(run))

    def test_untimed_days_and_closing_days_fold_into_an_all_day_run(self):
        days = [_day("2026-10-01", time=""), _day("2026-10-03", time="")]
        closing_day = _day("2026-10-02", time="", status="cancelled")
        after_run = _day("2026-10-09", time="", status="cancelled")
        published = {"events": [{"event_id": event_id(day)} for day in (*days, closing_day)]}

        # A carried-over cancellation can be a plain snapshot record.
        merged, kept = merge_exhibition_opening_days([*days, closing_day.to_dict(), after_run.to_dict()], published)

        self.assertEqual((merged.start_date, merged.end_date, merged.all_day), ("2026-10-01", "2026-10-03", True))
        self.assertEqual(merged.daily_schedule, [])
        self.assertIn(event_id(closing_day), merged.previous_event_ids)
        self.assertEqual(kept, after_run.to_dict())

    def test_mixed_hours_publish_no_partial_schedule(self):
        [merged] = merge_exhibition_opening_days([_day("2026-10-01"), _day("2026-10-02", time="")], {})
        self.assertEqual((merged.daily_schedule, merged.all_day), ([], True))

    def test_day_that_only_names_its_opening_time_joins_the_run(self):
        [run] = merge_exhibition_opening_days([_day("2026-10-03", time=""), _day("2026-10-04", time="")], {})
        opens = _day("2026-10-11", time="12:00", time_note="Sonntag ab 12:00 Uhr")
        [merged] = merge_exhibition_opening_days([run, opens], {"events": [{"event_id": event_id(opens)}]})
        self.assertEqual((merged.start_date, merged.end_date, merged.all_day), ("2026-10-03", "2026-10-11", True))
        self.assertEqual(merged.time_note, "")  # one day's note does not describe the run
        self.assertIn(event_id(opens), merged.previous_event_ids)

        tour = _day("2026-10-11", time="12:00", time_note="Führung um 12:00 Uhr")
        self.assertEqual(merge_exhibition_opening_days([run, tour], {}), [run, tour])

    def test_labelled_final_date_extends_the_run(self):
        final = {"details": {"final_date": "2026-12-20"}}
        [merged] = merge_exhibition_opening_days([_day("2026-10-17", time="", **final), _day("2026-10-18", time="", **final)], {})
        self.assertEqual((merged.start_date, merged.end_date), ("2026-10-17", "2026-12-20"))
        [unchanged] = merge_exhibition_opening_days([_day("2026-10-17", time=""), _day("2026-10-18", time="")], {})
        self.assertEqual(unchanged.end_date, "2026-10-18")

    def test_run_keeps_the_weekly_opening_days(self):
        weekend = {"details": {"final_date": "2026-12-20", "open_weekdays": [5, 6]}}
        [merged] = merge_exhibition_opening_days([_day("2026-10-17", time="", **weekend), _day("2026-10-18", time="", **weekend)], {})
        self.assertEqual(merged.details, {"final_date": "2026-12-20", "open_weekdays": [5, 6]})

    def test_final_date_beyond_timed_days_publishes_an_untimed_range(self):
        # Slots for the listed days only would present every later opening day as closed.
        final = {"details": {"final_date": "2026-12-20"}}
        [merged] = merge_exhibition_opening_days([_day("2026-10-17", **final), _day("2026-10-18", **final)], {})
        self.assertEqual((merged.end_date, merged.daily_schedule, merged.all_day), ("2026-12-20", [], True))

    def test_weekly_hours_keep_a_cancelled_day_closed(self):
        note = "Öffnungszeiten: Mittwoch bis Freitag, 16 bis 19 Uhr"
        days = [_day(day, time="16:00–19:00", time_note=note) for day in ("2026-10-14", "2026-10-16")]
        cancelled = _day("2026-10-15", time="16:00–19:00", time_note=note, status="cancelled")

        [merged] = merge_exhibition_opening_days([*days, cancelled], {})

        self.assertEqual([slot["date"] for slot in merged.daily_schedule], ["2026-10-14", "2026-10-16"])

    def test_labelled_weekly_hours_beat_the_clock_copied_onto_every_repeat(self):
        note = "Öffnungszeiten: Mittwoch bis Freitag, 16 bis 19 Uhr; Samstag bis Sonntag, 14 bis 19 Uhr"
        days = [_day(f"2026-10-{day}", time="16:00–19:00", time_note=note) for day in range(10, 25)]

        [merged] = merge_exhibition_opening_days(days, {})

        hours = {slot["date"]: (slot["start_at"][11:16], slot["end_at"][11:16]) for slot in merged.daily_schedule}
        self.assertEqual((merged.start_date, merged.end_date, merged.time_note), ("2026-10-10", "2026-10-24", note))
        self.assertEqual(hours["2026-10-10"], ("14:00", "19:00"))  # Saturday
        self.assertEqual(hours["2026-10-14"], ("16:00", "19:00"))  # Wednesday
        self.assertNotIn("2026-10-12", hours)  # Monday is closed
        self.assertNotIn("2026-10-13", hours)  # Tuesday is closed
        self.assertEqual(len(hours), 11)

    def test_short_labelled_opening_hours_fold_but_short_tours_do_not(self):
        note = "Öffnungszeiten: Dienstag bis Freitag: 15.00-17.00 Uhr, Sonntag: 11.00-18.00 Uhr"
        days = [_day("2026-10-18", time="11:00–18:00", time_note=note),
                *(_day(f"2026-10-{day}", time="15:00–17:00", time_note=note) for day in range(20, 24))]

        [merged] = merge_exhibition_opening_days(days, {})

        self.assertEqual((merged.start_date, merged.end_date), ("2026-10-18", "2026-10-23"))
        self.assertEqual(len(merged.daily_schedule), 5)
        tours = [_day(day, time="15:00–16:00", time_note=note) for day in ("2026-10-20", "2026-10-21")]
        self.assertEqual(merge_exhibition_opening_days(tours, {}), tours)

    def test_daily_untimed_offer_becomes_one_run_and_redirects_its_day_pages(self):
        days = [_offer(f"2026-10-{day}", details={"final_date": "2027-12-31"}) for day in range(18, 30)]
        day_ids = [event_id(day) for day in days]

        [merged] = merge_exhibition_opening_days(days, {"events": [{"event_id": identifier} for identifier in day_ids]})

        self.assertEqual((merged.start_date, merged.end_date, merged.all_day), ("2026-10-18", "2027-12-31", True))
        self.assertEqual(event_id(merged), day_ids[0])  # the first day page becomes the run
        self.assertEqual(merged.previous_event_ids, day_ids)

        # Next import: the window moved one day and a new future day appeared.
        published = assign_event_ids([merged])[0]
        later = [_offer(f"2026-10-{day}", details={"final_date": "2027-12-31"}) for day in range(19, 31)]
        [next_day] = merge_exhibition_opening_days(later, {"events": [published]})
        self.assertEqual(event_id(next_day), published["event_id"])
        self.assertEqual(next_day.start_date, "2026-10-18")

        # The last listed days of a published run stay in it.
        [tail] = merge_exhibition_opening_days(later[-2:], {"events": [published]})
        self.assertEqual(event_id(tail), published["event_id"])

    def test_untimed_days_with_a_gap_or_too_few_keep_their_own_pages(self):
        # Without a clock time a slot cannot say which days the offer runs.
        cases = {
            "too few days": [_offer(f"2026-10-{day}") for day in range(18, 24)],
            "skipped day": [_offer(f"2026-10-{day}") for day in (*range(18, 24), 25, 26)],
        }
        for name, days in cases.items():
            with self.subTest(name):
                self.assertEqual(merge_exhibition_opening_days(days, {}), days)


    def test_stage_run_becomes_one_event_with_its_performances(self):
        shows = [_show("2026-11-02"), _show("2026-11-05", time="19:30", start_at="2026-11-05T19:30+01:00"), _show("2026-11-08", time="15:30–17:00")]
        show_ids = [event_id(show) for show in shows]

        [run] = merge_exhibition_opening_days(shows, {"events": [{"event_id": identifier} for identifier in show_ids]})

        self.assertEqual((run.start_date, run.end_date, run.all_day, run.time), ("2026-11-02", "2026-11-08", False, ""))
        self.assertEqual(
            [(slot["date"], slot["start_at"][11:16], slot["end_at"][11:16]) for slot in run.daily_schedule],
            [("2026-11-02", "19:30", "21:00"), ("2026-11-05", "19:30", ""), ("2026-11-08", "15:30", "17:00")],
        )
        self.assertEqual(run.previous_event_ids, show_ids)
        self.assertTrue(run.dated_sessions)

        # Played performances leave the schedule; the public ID stays.
        published = assign_event_ids([run])[0]
        [later] = merge_exhibition_opening_days([*shows[1:], _show("2026-11-09")], {"events": [published]})
        self.assertEqual(event_id(later), published["event_id"])
        self.assertEqual([slot["date"] for slot in later.daily_schedule], ["2026-11-05", "2026-11-08", "2026-11-09"])
        [retained] = merge_exhibition_opening_days([run, _show("2026-11-09")], {})
        self.assertEqual([slot["date"] for slot in retained.daily_schedule], ["2026-11-09"])

    def test_weekly_meetups_and_timed_daily_offers_become_session_runs(self):
        weekly = [_offer(day, time="16:00–19:00") for day in ("2026-10-23", "2026-10-30", "2026-11-06")]
        rally = [_offer(f"2026-10-{day}", time="08:00–20:00") for day in range(18, 30)]
        for name, rows, dates in (("weekly", weekly, 3), ("daily rally", rally, 12)):
            with self.subTest(name):
                [run] = merge_exhibition_opening_days(rows, {})
                self.assertEqual((run.start_date, run.end_date), (rows[0].start_date, rows[-1].start_date))
                self.assertEqual((len(run.daily_schedule), run.dated_sessions, run.all_day), (dates, True, False))
        # The flag describes a schedule; without one it is dropped.
        self.assertFalse(validate_event({**weekly[0].to_dict(), "dated_sessions": True}).dated_sessions)

    def test_exhibition_tours_become_session_runs_without_the_exhibition_end(self):
        final = {"details": {"final_date": "2026-12-20"}}
        tours = [_day(day, time="15:00–16:00", title="Führung durch die Ausstellung", **final) for day in ("2026-10-04", "2026-10-11", "2026-10-18")]
        [run] = merge_exhibition_opening_days(tours, {})
        self.assertEqual((run.end_date, len(run.daily_schedule), run.dated_sessions), ("2026-10-18", 3, True))

    def test_run_prefers_a_summary_that_names_no_date(self):
        rows = [
            _offer("2026-10-23", time="16:00–19:00", ai_summary="Am 23. Oktober öffnet das Repair-Café."),
            _offer("2026-10-30", time="16:00–19:00", ai_summary="Im Repair-Café reparieren Ehrenamtliche Geräte."),
            _offer("2026-11-06", time="16:00–19:00", ai_summary="Am 6. November öffnet das Repair-Café."),
        ]
        [run] = merge_exhibition_opening_days(rows, {})
        self.assertEqual(run.ai_summary, "Im Repair-Café reparieren Ehrenamtliche Geräte.")

    def test_curated_sparse_short_and_same_day_stage_dates_keep_their_pages(self):
        cases = {
            "two performances": [_show("2026-11-02"), _show("2026-11-03")],
            "gap over two weeks": [_show(day) for day in ("2026-11-02", "2026-11-03", "2026-11-20")],
            "curated per date": [_show(day, title="Die Zauberflöte", source_id="theater-bonn") for day in ("2026-11-02", "2026-11-03", "2026-11-05")],
            "two shows a day": [_show("2026-11-02"), _show("2026-11-02", time="15:00–16:30"), _show("2026-11-03")],
        }
        for name, shows in cases.items():
            with self.subTest(name):
                self.assertEqual(merge_exhibition_opening_days(shows, {}), shows)

if __name__ == "__main__":
    unittest.main()
