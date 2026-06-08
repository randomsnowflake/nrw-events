"""
Meetup — curated Bonn-area groups via their public per-group iCal feeds.

Reads:  meetup.com/<slug>/events/ical/  (no auth) for each group in config.MEETUP_GROUPS
Yields: tech / outdoor / hobby meetups. Edit the group list in config.py.
"""

from .. import common, config


def fetch() -> list:
    events = []
    for slug, city, category, trust in config.MEETUP_GROUPS:
        try:
            events.extend(common.fetch_ical(
                f"https://www.meetup.com/{slug}/events/ical/",
                "Meetup", city, category, trust,
            ))
        except Exception as e:
            common.log_source_error(f"Meetup ({slug})", e)
    return events
