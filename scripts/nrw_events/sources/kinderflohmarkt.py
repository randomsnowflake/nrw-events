"""Children's flea markets around Bonn from Kinderflohmarkt.com JSON-LD."""

from .. import common
from . import regional_common as rc


_URL = "https://kinderflohmarkt.com/de/bonn/"
_CITY_ALIASES = {
    "plittersdorf": "Bonn-Plittersdorf",
}


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
        for event in events:
            city = (event.get("city") or "").casefold()
            event["city"] = _CITY_ALIASES.get(city, event.get("city") or "Bonn")
            if not event.get("description"):
                event["description"] = common.factual_event_description(
                    event.get("title", ""),
                    date_value=event.get("start_date", ""),
                    time_text=event.get("time", ""),
                    venue=event.get("venue", ""),
                    city=event["city"],
                )
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
