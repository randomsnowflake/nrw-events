"""Official Tribe Events calendar of the Frauenmuseum Bonn."""

from .. import ical, run_state

_SOURCE = "Frauenmuseum Bonn"
_SOURCE_ID = "frauenmuseum-bonn"
_FEED = "https://frauenmuseum.de/?post_type=tribe_events&ical=1"
_VENUE = "Frauenmuseum"


def _with_house_venue(events: list) -> list:
    # shortcut: an empty LOCATION means the house, as every such event text says; a future offsite event would need its own check.
    for event in events:
        if not event.get("venue"):
            event["venue"] = _VENUE
    return events


def fetch() -> list:
    try:
        return _with_house_venue(ical.fetch_ical(
            _FEED, _SOURCE, "Bonn", "museum ausstellung kultur", 1.0, source_id=_SOURCE_ID,
        ))
    except Exception as exc:
        run_state.log_source_error(_SOURCE, exc, source_id=_SOURCE_ID)
        return []
