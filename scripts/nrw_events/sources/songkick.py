"""
Songkick — concerts for the Bonn metro area.

Reads:  songkick.com/metro-areas/28447-germany-bonn/<year>
Yields: concerts via schema.org MusicEvent JSON-LD, with an HTML link fallback.
"""

import json
import re

from .. import common


def fetch() -> list:
    source = "Songkick"
    try:
        url = f"https://www.songkick.com/metro-areas/28447-germany-bonn/{common.TODAY.year}"
        html = common.fetch_url(url)
        events = []
        seen = set()

        for ld in re.findall(r'application/ld\+json">(.*?)</script>', html, re.DOTALL):
            try:
                data = json.loads(ld)
            except (json.JSONDecodeError, AttributeError):
                continue
            for item in (data if isinstance(data, list) else [data]):
                if item.get("@type") != "MusicEvent":
                    continue
                name = item.get("name", "")
                start = item.get("startDate", "")
                event_date = common.parse_date(start[:10]) if start else None
                if event_date and not (common.TODAY <= event_date <= common.END_DATE):
                    continue
                if name in seen:
                    continue
                seen.add(name)

                venue_name = (item.get("location", {}) or {}).get("name", "")
                city_guess = common.guess_city_from_text(venue_name) or "Bonn"

                display = name
                if venue_name and f"@ {venue_name}" not in name and venue_name not in name:
                    display = f"{name} @ {venue_name}"
                event = common.make_event(
                    display, event_date, event_date, venue_name, city_guess, "", item.get("url", ""),
                    source, "concert", 1.0, time_text=start[11:16] if len(start) > 11 else "",
                )
                if event:
                    events.append(event)
        return events
    except Exception as e:
        common.log_source_error(source, e)
        return []
