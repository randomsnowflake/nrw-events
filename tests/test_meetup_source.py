import unittest
from datetime import datetime
from unittest.mock import patch

from nrw_events import common, detail_enrichment
from nrw_events.sources import SOURCES, SOURCE_SPECS, meetup


DETAIL_HTML = """
<script type="application/ld+json">{
  "@context": "https://schema.org",
  "@type": "Event",
  "name": "GitHub Copilot App - Das Cockpit für alle Agents",
  "startDate": "2026-09-01T18:00:00+02:00",
  "endDate": "2026-09-01T20:30:00+02:00",
  "eventStatus": "https://schema.org/EventScheduled",
  "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
  "image": ["https://images.example/meetup.jpg"],
  "location": {
    "@type": "Place",
    "name": "adesso SE Geschäftsstelle Bonn",
    "address": {
      "@type": "PostalAddress",
      "streetAddress": "Joseph-Schumpeter-Allee 1",
      "postalCode": "53227",
      "addressLocality": "Bonn"
    }
  },
  "organizer": {
    "@type": "Organization",
    "name": "Azure Bonn Meetup",
    "url": "https://www.meetup.com/azure-bonn-meetup/"
  }
}</script>
"""


def raw_event(**overrides):
    event = {
        "title": "GitHub Copilot App - Das Cockpit für alle Agents",
        "source": "Meetup",
        "start_date": "2026-09-01",
        "end_date": "2026-09-01",
        "date": "2026-09-01",
        "time": "18:00–20:30",
        "venue": "",
        "city": "Bonn",
        "link": "https://www.meetup.com/azure-bonn-meetup/events/315979799/",
        "category": "cloud tech meetup",
        "description": "Publisher-authored platform copy that must not be republished.",
        "description_html": "<p>Publisher-authored platform copy that must not be republished.</p>",
        "description_source": "scraped",
        "score": 0.9,
    }
    event.update(overrides)
    return event


class MeetupSourceTests(unittest.TestCase):
    def setUp(self):
        self.today = patch.object(common, "TODAY", datetime(2026, 8, 30))
        self.end = patch.object(common, "END_DATE", datetime(2026, 9, 30))
        self.today.start()
        self.end.start()
        self.addCleanup(self.end.stop)
        self.addCleanup(self.today.stop)

    def test_source_and_group_health_ids_are_registered(self):
        self.assertIs(SOURCES["Meetup Bonn groups"], meetup.fetch)
        spec = next(spec for spec in SOURCE_SPECS if spec.id == "meetup-bonn-groups")
        self.assertEqual(
            set(spec.component_ids),
            {f"meetup-{group.slug}" for group in meetup.GROUPS},
        )

    def test_group_events_use_detail_master_data_and_strip_platform_copy(self):
        group = meetup.MeetupGroup("azure-bonn-meetup", "Bonn", "cloud tech meetup", 0.9)
        detail_calls = []

        with patch.object(meetup.common, "fetch_ical", return_value=[raw_event()]):
            events = meetup.events_for_group(
                group,
                detail_fetcher=lambda link, timeout: detail_calls.append((link, timeout)) or DETAIL_HTML,
            )

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["source_id"], "meetup-azure-bonn-meetup")
        self.assertEqual(event["venue"], "adesso SE Geschäftsstelle Bonn")
        self.assertEqual(event["venue_address"], "Joseph-Schumpeter-Allee 1, 53227 Bonn")
        self.assertEqual(event["city"], "Bonn")
        self.assertEqual(event["organizer"], "Azure Bonn Meetup")
        self.assertEqual(event["image"], "https://images.example/meetup.jpg")
        self.assertTrue(event["_detail_page_enriched"])
        self.assertEqual(event["description_source"], "generated")
        self.assertNotIn("Publisher-authored", event["description"])
        self.assertIn("adesso SE Geschäftsstelle Bonn", event["description"])
        self.assertEqual(detail_calls[0][0], event["link"])

        with patch.object(detail_enrichment.common, "fetch_detail_url") as shared_fetch:
            self.assertEqual(detail_enrichment.enrich_events(events), events)
        shared_fetch.assert_not_called()

    def test_valid_empty_group_calendar_is_not_parser_degradation(self):
        with patch.object(meetup.common, "fetch_ical", return_value=[]) as fetch_ical:
            self.assertEqual(meetup.events_for_group(meetup.GROUPS[0]), [])

        self.assertTrue(fetch_ical.call_args.kwargs["empty_calendar_is_valid"])
        self.assertEqual(fetch_ical.call_args.kwargs["source_id"], meetup.GROUPS[0].source_id)

    def test_changed_detail_occurrence_cannot_overwrite_listing_event(self):
        changed = DETAIL_HTML.replace("GitHub Copilot App - Das Cockpit für alle Agents", "Different event")
        with patch.object(meetup.common, "fetch_ical", return_value=[raw_event()]):
            events = meetup.events_for_group(
                meetup.GROUPS[0],
                detail_fetcher=lambda _link, _timeout: changed,
            )

        self.assertEqual(events, [])

    def test_repeated_trailing_city_is_normalized_in_structured_address(self):
        self.assertEqual(
            meetup._venue_address({
                "streetAddress": "Meckenheimer Allee 171, 53115 Bonn, Bonn",
                "addressLocality": "Bonn",
            }, "Bonn"),
            "Meckenheimer Allee 171, 53115 Bonn",
        )

    def test_online_only_event_is_not_published_as_a_bonn_event(self):
        online = DETAIL_HTML.replace("OfflineEventAttendanceMode", "OnlineEventAttendanceMode")
        with patch.object(meetup.common, "fetch_ical", return_value=[raw_event()]):
            events = meetup.events_for_group(
                meetup.GROUPS[0],
                detail_fetcher=lambda _link, _timeout: online,
            )

        self.assertEqual(events, [])

    def test_zero_detail_budget_drops_unlocated_ical_event_without_requests(self):
        detail_calls = []
        with patch.object(meetup.common, "fetch_ical", return_value=[raw_event()]):
            events = meetup.events_for_group(
                meetup.GROUPS[0],
                detail_fetcher=lambda link, _timeout: detail_calls.append(link) or DETAIL_HTML,
                detail_batch_timeout=0,
            )

        self.assertEqual(detail_calls, [])
        self.assertEqual(events, [])

    def test_zero_detail_budget_keeps_source_backed_location_and_resolves_city(self):
        located = raw_event(venue="Rheinhalle, Remagen")
        with patch.object(meetup.common, "fetch_ical", return_value=[located]):
            [event] = meetup.events_for_group(
                meetup.GROUPS[0],
                detail_batch_timeout=0,
            )

        self.assertEqual(event["venue"], "Rheinhalle, Remagen")
        self.assertEqual(event["city"], "Remagen")
        self.assertEqual(event["description_source"], "generated")
        self.assertNotIn("Publisher-authored", event["description"])

    def test_same_title_same_day_occurrences_with_different_times_survive(self):
        later = raw_event(
            time="20:00–22:30",
            venue="adesso SE Geschäftsstelle Bonn",
            link="https://www.meetup.com/azure-bonn-meetup/events/315979800/",
        )
        with patch.object(meetup.common, "fetch_ical", return_value=[
            raw_event(venue="adesso SE Geschäftsstelle Bonn"), later,
        ]):
            events = meetup.events_for_group(
                meetup.GROUPS[0],
                detail_batch_timeout=0,
            )

        self.assertEqual(len(events), 2)
        self.assertEqual({event["time"] for event in events}, {"18:00–20:30", "20:00–22:30"})

    def test_fetch_shares_one_detail_budget_across_groups(self):
        groups = meetup.GROUPS[:2]
        budgets = []

        def fake_group_fetch(_group, **kwargs):
            budgets.append(kwargs["detail_batch_timeout"])
            return []

        with patch.object(meetup, "GROUPS", groups), patch.object(
            meetup, "events_for_group", side_effect=fake_group_fetch,
        ), patch.object(meetup.time, "monotonic", side_effect=[100.0, 110.0, 130.0]):
            self.assertEqual(meetup.fetch(), [])

        self.assertEqual(budgets, [35.0, 15.0])

    def test_detail_transport_uses_one_attempt_inside_the_batch_budget(self):
        def invoke_detail(_group, **kwargs):
            kwargs["detail_fetcher"]("https://www.meetup.com/group/events/1/", 2.0)
            return []

        with patch.object(meetup, "GROUPS", meetup.GROUPS[:1]), patch.object(
            meetup, "events_for_group", side_effect=invoke_detail,
        ) as group_fetch, patch.object(meetup.common, "fetch_detail_url", return_value=DETAIL_HTML) as fetch_detail:
            meetup.fetch()

        self.assertEqual(fetch_detail.call_args.kwargs["timeout"], 2.0)
        self.assertEqual(fetch_detail.call_args.kwargs["retry_attempts"], 1)


if __name__ == "__main__":
    unittest.main()
