"""KrümelBühne of the Brotfabrik Bühne Bonn: children's theatre played away from the Brotfabrik.

Plays in the Brotfabrik itself already arrive through the Brotfabrik calendar API
(``brotfabrik-bonn``); its touring dates ("KrümelBühne unterwegs!", e.g. the Bonner
Puppentheater-Festival in Dransdorf or matinees in the LVR-Landesmuseum) appear only as
posts on brotfabrik-theater.de.
"""

import json
import re

from .. import dates, event_builder, http, run_state, text
from . import regional_common as rc

_SOURCE = "KrümelBühne Bonn"
_SOURCE_ID = "kruemelbuehne-bonn"
_SITE = "https://www.brotfabrik-theater.de/"
# Tag 84 = "Krümelbühne" on the WordPress site.
_API = f"{_SITE}wp-json/wp/v2/posts?tags=84&per_page=50&_fields=link,title,content"
_CATEGORY = "kindertheater figurentheater puppentheater kinder familie"
_DATE = re.compile(r"<h3[^>]*>\s*(\d{1,2}\.\s*[A-Za-zÄÖÜäöü]+\s+20\d{2})\s*<br\s*/?>(.*?)</h3>", re.I | re.S)
_TOURING = re.compile(r"Krümelbühne\s+unterwegs!?\s*[–-]\s*(?:im|in der|in)\s+(.+)", re.I)


_PRICE = re.compile(r"(?:Eintritt\s+)?(?:auf Spendenbasis|[\d,\- /€]+)", re.I)


def _touring_venue(teaser: str) -> str:
    for block in re.findall(r"<p[^>]*>(.*?)</p>", teaser, re.I | re.S):
        if match := _TOURING.search(rc.clean(block)):
            return match.group(1).strip()
    return ""


def _paragraphs(html: str) -> list[tuple[str, str]]:
    return [(block, rc.clean(block)) for block in re.findall(r"<p[^>]*>(.*?)</p>", html, re.I | re.S)]


def events_from_json(raw: str) -> list[dict]:
    events = []
    for post in json.loads(raw or "[]"):
        content = (post.get("content") or {}).get("rendered") or ""
        teaser, _, body = content.partition("<!--more-->")
        date_match = _DATE.search(teaser)
        venue = _touring_venue(teaser)
        # Overview posts carry no single date; plays in the Brotfabrik come from its own calendar.
        if not date_match or not venue:
            continue
        day = dates.parse_date(rc.clean(date_match.group(1)))
        times = re.findall(r"(\d{1,2})[.:](\d{2})\s*Uhr", rc.clean(date_match.group(2)))
        if not day or not times:
            continue
        address, price, prose = "", "", []
        for block, para in _paragraphs(body):
            lines = [line for part in re.split(r"<br\s*/?>", block) if (line := rc.clean(part).strip(" ,"))]
            if not address and lines and lines[0].casefold() == venue.casefold():
                # Venue block: name, street lines, sometimes the admission on its last line.
                if lines and _PRICE.fullmatch(lines[-1]):
                    price = price or lines.pop()
                address = ", ".join(lines[1:])
            elif not price and _PRICE.fullmatch(para.strip()):
                price = para.strip()
            elif para:
                prose.append(para)
        title = rc.clean((post.get("title") or {}).get("rendered") or "")
        ensemble = rc.clean(m.group(1)) if (m := re.search(r"<h2[^>]*>(.*?)</h2>", teaser, re.I | re.S)) else ""
        is_film = "film" in f"{ensemble} {title}".casefold()
        link = post.get("link") or _SITE
        age = re.search(r"\bab\s+(\d{1,2})\s+Jahren\b", rc.clean(content), re.I)
        for hour, minute in times:
            start = day.replace(hour=int(hour), minute=int(minute))
            description = text.concise_description(" ".join(prose))
            event = event_builder.make_event(
                title, start, None, venue, "Bonn",
                description or text.factual_event_description(title, date_value=start, time_text=f"{hour}:{minute}", venue=venue, city="Bonn"),
                link, _SOURCE, _CATEGORY, 1.0, f"{int(hour):02d}:{minute}",
                source_id=_SOURCE_ID,
                description_source="scraped" if description else "generated",
                default_category_key="cinema" if is_film else "stage",
                category_locked=True,
            )
            if not event:
                continue
            if address:
                event["venue_address"] = address
            if price:
                event["price"] = price
            if age:
                event["details"] = {"age": f"ab {age.group(1)} Jahren"}
            events.append(event)
    return rc.dedupe_occurrences(events)


def fetch() -> list[dict]:
    try:
        raw = http.fetch_url(_API, timeout=25, accept="application/json,*/*;q=0.8")
        payload = json.loads(raw)
        if not isinstance(payload, list):
            raise ValueError("KrümelBühne REST response is not a post list")
        events = [event for event in events_from_json(raw) if event_builder.window_contains(dates.parse_iso_date(event.get("start_date") or ""))]
        # Every post may lie in the Brotfabrik or outside the window: an empty result is healthy.
        http._record_endpoint(_API, parser_type="wordpress-rest", parsed_event_count=len(events), parser_empty=False)
        return events
    except Exception as exc:
        run_state.log_source_error(_SOURCE, exc)
        return []
