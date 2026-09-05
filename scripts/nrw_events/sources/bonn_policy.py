"""Shared official Bonn URL and reviewed occurrence rules."""
from .. import common, reviewed_corrections


def _active_reviewed_map(group: str) -> dict[tuple[str, ...], object]:
    return {
        tuple(str(value) for value in entry["match"]): entry["value"]
        for entry in reviewed_corrections.active_entries(group, common.runtime_window().start)
    }


def _clean_event_href(href: str) -> str:
    """Remove Bonn's transient signature while preserving functional queries."""
    parsed = common.urllib.parse.urlsplit(href)
    query = common.urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query = [
        (key, value)
        for key, value in query
        if not (key == "p" and value.casefold().startswith("sig:"))
    ]
    return common.urllib.parse.urlunsplit(
        (*parsed[:3], common.urllib.parse.urlencode(query), parsed.fragment)
    )

