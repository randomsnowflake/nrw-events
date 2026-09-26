import unittest

from nrw_events.exhibition_runs import merge_exhibition_opening_days
from nrw_events.identity import assign_event_ids, event_id
from nrw_events.validation import validate_event


def _day(day: str, *, time: str = "14:00–18:00", title: str = "Ausstellung 100 Jahre Bad Godesberg",
         status: str = "scheduled"):
    return validate_event({
        "status": status,
        "title": title,
        "source": "Bonn.de Events",
        "source_id": "bonn-de-events",
        "start_date": day,
        "time": time,
        "venue": "Haus an der Redoute",
        "city": "Bonn-Bad Godesberg",
        "category_key": "exhibition",
        "link": "https://www.bonn.de/ausstellung.php",
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

        # Next day: the first date has passed and left the window. The run
        # keeps its start, so the public ID does not move.
        published = assign_event_ids([merged])[0]
        [next_day] = merge_exhibition_opening_days(days[1:], {"events": [published]})
        self.assertEqual(next_day.start_date, "2026-10-01")
        self.assertEqual(event_id(next_day), published["event_id"])

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


if __name__ == "__main__":
    unittest.main()
