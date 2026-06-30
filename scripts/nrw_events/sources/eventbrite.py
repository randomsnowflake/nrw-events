"""Eventbrite public party listing for Bonn and nearby NRW nightlife."""

import urllib.error

from .. import common


URL = "https://www.eventbrite.de/d/germany--bonn/party/"


def fetch() -> list:
    try:
        html = common.fetch_url(URL, timeout=25)
    except urllib.error.HTTPError as e:
        if e.code == 405:
            # Eventbrite serves a bot-check/405 to some server networks. Treat it
            # as an opportunistic miss; Rausgegangen Party is the stable source.
            return []
        common.log_source_error("Eventbrite Party", e)
        return []
    except Exception as e:
        common.log_source_error("Eventbrite Party", e)
        return []

    # Eventbrite exposes its search results as schema.org JSON-LD. Keep trust
    # modest: it is broad and ticketing-oriented, but it fills nightlife gaps.
    events = common.events_from_jsonld(
        html,
        "Eventbrite Party",
        "Bonn",
        "party nightlife dj club",
        0.72,
        URL,
    )
    return [_with_nightlife_category(event) for event in events]


def _with_nightlife_category(event: dict) -> dict:
    return {
        **event,
        "category_key": "nightlife",
        "category_label": "Nachtleben & Party",
        "category_confidence": max(event.get("category_confidence", 0), 0.8),
        "category_reason": f"source:Eventbrite Party; {event.get('category_reason', '')}".strip(),
    }
