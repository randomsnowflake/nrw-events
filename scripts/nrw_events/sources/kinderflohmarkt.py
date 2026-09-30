"""Children's flea markets around Bonn from Kinderflohmarkt.com JSON-LD."""

import re

from .. import common
from . import regional_common as rc

_URL = "https://kinderflohmarkt.com/de/bonn/"
_CITY_ALIASES = {
    "plittersdorf": "Bonn-Plittersdorf",
}


_LONG_TEXT = re.compile(r'<span class="infos long">(.*?)</span>', re.S)


def _full_descriptions(html: str) -> dict[str, str]:
    """Map listing anchors (``t21409``) to the untruncated ``infos long`` copy.

    The page's JSON-LD cuts each description after ~100 characters with "...";
    the same page carries the complete text inside every ``termin`` item.
    """
    descriptions = {}
    for chunk in html.split('<li class="termin" id="')[1:]:
        anchor = chunk.split('"', 1)[0]
        match = _LONG_TEXT.search(chunk)
        text = common.clean_html(match.group(1)) if match else ""
        if anchor and text:
            descriptions[anchor] = text
    return descriptions


def fetch() -> list:
    source = "Kinderflohmarkt.com"
    try:
        html = common.fetch_url(_URL, timeout=20)
        events = common.events_from_jsonld(
            html,
            source,
            "Bonn",
            "kinderflohmarkt flohmarkt second hand markt",
            0.82,
            _URL,
        )
        full_descriptions = _full_descriptions(html)
        for event in events:
            full = full_descriptions.get((event.get("link") or "").rpartition("#")[2])
            if full and len(full) > len(event.get("description") or ""):
                event["description"] = full
                event["description_source"] = common.description_source_for(full)
                event.pop("description_html", None)
            city = (event.get("city") or "").casefold()
            event["city"] = _CITY_ALIASES.get(city, event.get("city") or "Bonn")
            if not event.get("description"):
                event["description"] = common.factual_event_description(
                    event.get("title", ""),
                    date_value=common.parse_iso_date(event.get("start_date", "")),
                    time_text=event.get("time", ""),
                    venue=event.get("venue", ""),
                    city=event["city"],
                )
                event["description_source"] = "generated"
        common._record_endpoint(
            _URL,
            parser_type="json-ld",
            parsed_event_count=len(events),
            parser_empty=not bool(events),
        )
        return rc.dedupe(events)
    except Exception as exc:
        common.log_source_error(source, exc)
        return []
