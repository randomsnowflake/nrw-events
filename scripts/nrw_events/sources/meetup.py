"""
Meetup — curated Bonn-area groups via their public per-group iCal feeds.

Reads:  meetup.com/<slug>/events/ical/  (no auth) for each group in config.MEETUP_GROUPS
Yields: tech / outdoor / hobby meetups. Edit the group list in config.py.
"""

from .. import common, config
from ..models import normalize_source_id


def fetch() -> list:
    events = []
    for slug, city, category, trust in config.MEETUP_GROUPS:
        source_id = normalize_source_id(f"meetup-{slug}")
        try:
            group_events = common.fetch_ical(
                f"https://www.meetup.com/{slug}/events/ical/",
                "Meetup", city, category, trust,
            )
            for event in group_events:
                event["source_id"] = source_id
            events.extend(group_events)
        except Exception as e:
            common.log_source_error(f"Meetup ({slug})", e, source_id=source_id)
    return events
