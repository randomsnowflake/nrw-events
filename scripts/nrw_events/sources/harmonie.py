"""
Harmonie Bonn — concert hall + club nights.

Reads:  harmonie-bonn.de/?post_type=tribe_events&ical=1  (Tribe Events iCal)
Yields: concerts and club nights. Note: the venue takes a summer break, so this
        source legitimately returns nothing in mid-summer windows.
"""

from .. import common
from ..health import SourceFetchResult


def _fill_calendar_venues(events: list) -> list:
    """Use the venue calendar's explicit identity without replacing LOCATION."""
    for event in events:
        if not event.get("venue"):
            # The feed declares X-WR-CALNAME: Harmonie Bonn. It is the venue's
            # programme, while external occurrences carry their own LOCATION.
            event["venue"] = "Harmonie Bonn"
            event["identity_venue"] = ""
            event["identity_venue_locked"] = True
    return events


def fetch() -> SourceFetchResult:
    events = common.fetch_ical(
        "https://www.harmonie-bonn.de/?post_type=tribe_events&ical=1",
        "Harmonie Bonn", "Bonn", "concert", 1.0,
    )
    return SourceFetchResult.success(_fill_calendar_venues(events))
