"""
Harmonie Bonn — concert hall + club nights.

Reads:  harmonie-bonn.de/?post_type=tribe_events&ical=1  (Tribe Events iCal)
Yields: concerts and club nights. Note: the venue takes a summer break, so this
        source legitimately returns nothing in mid-summer windows.
"""

from .. import common


def fetch() -> list:
    return common.fetch_ical(
        "https://www.harmonie-bonn.de/?post_type=tribe_events&ical=1",
        "Harmonie Bonn", "Bonn", "concert", 1.0,
    )
