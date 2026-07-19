"""Official RSS event feed for TiK – Theater im Keller."""

import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from .. import common
from . import regional_common as rc


_SOURCE = "TiK Theater im Keller"
_FEED = "https://tik-bonn.de/feed/"
_CATEGORY = "theater bühne komödie schauspiel"
_TRUST = 1.0
_CONTENT = "{http://purl.org/rss/1.0/modules/content/}encoded"


def events_from_rss(raw: str) -> list[dict]:
    root = ET.fromstring(raw)
    events = []
    for item in root.findall(".//item"):
        title = rc.clean(item.findtext("title", ""))
        link = rc.clean(item.findtext("link", ""))
        body = item.findtext(_CONTENT, "") or item.findtext("description", "")
        description = common.concise_description(body)
        pub_date = rc.clean(item.findtext("pubDate", ""))
        try:
            date_value = parsedate_to_datetime(pub_date).replace(tzinfo=None)
        except (TypeError, ValueError, OverflowError):
            date_value = common.parse_date(pub_date)
        time_match = re.search(r'(\d{1,2}:\d{2})\s*Uhr', rc.clean(body), re.I)
        time_text = time_match.group(1) if time_match else ""
        start_dt = rc.with_time(date_value, time_text)
        if description:
            description = common.concise_description(
                f"Theateraufführung auf der Bühne. {description}"
            )
        else:
            description = common.factual_event_description(
                title, date_value=start_dt, time_text=time_text,
                venue="TiK – Theater im Keller", city="Bonn",
            )
        event = common.make_event(
            title, start_dt, None, "TiK – Theater im Keller", "Bonn", description,
            link, _SOURCE, _CATEGORY, _TRUST, time_text,
            source_id="tik-theater-im-keller",
        )
        if event:
            events.append(event)
    return rc.dedupe_occurrences(events)


def fetch() -> list[dict]:
    try:
        return events_from_rss(common.fetch_url(_FEED, timeout=25, accept="application/rss+xml, application/xml"))
    except Exception as exc:
        common.log_source_error(_SOURCE, exc)
        return []
