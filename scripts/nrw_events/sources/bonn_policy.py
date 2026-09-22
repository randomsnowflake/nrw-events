"""Shared official Bonn URL and reviewed occurrence rules."""
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .. import reviewed_corrections
from ..run_state import runtime_window
from ..runtime import RunContext


def _active_reviewed_map(group: str, context: RunContext | None = None) -> dict[tuple[str, ...], object]:
    return {
        tuple(str(value) for value in entry["match"]): entry["value"]
        for entry in reviewed_corrections.active_entries(group, (context.window if context else runtime_window()).start)
    }


def _clean_event_href(href: str) -> str:
    """Remove Bonn's transient signature while preserving functional queries."""
    parsed = urlsplit(href)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    query = [
        (key, value)
        for key, value in query
        if not (key == "p" and value.casefold().startswith("sig:"))
    ]
    return urlunsplit(
        (*parsed[:3], urlencode(query), parsed.fragment)
    )

