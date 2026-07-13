"""
Much — Bergisches Land village in the eastern Rhein-Sieg-Kreis (~30 km from Bonn).

Reads:  much.de event listing (TYPO3 tx_news). No feed, but server-rendered with
        machine-readable <time datetime="…"> tags paired to title links.
Yields: rural gems — Bergische Gartentour, Trödelmarkt, village live music.

HTML scraping: fails soft (returns []) if the markup changes.
"""

import re
from html.parser import HTMLParser

from .. import common

_URL = "https://www.much.de/willkommen/veranstaltungen"
_BASE = "https://www.much.de"


class _DetailDescriptionParser(HTMLParser):
    """Collect the editorial description blocks from a Much detail page."""

    _VOID_TAGS = {
        "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[list[str]] = []
        self._part: list[str] | None = None
        self._depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        is_description = (
            attributes.get("itemprop") == "description" and "teaser-text" in classes
        ) or (
            attributes.get("itemprop") == "articleBody" and "news-text-wrap" in classes
        )

        if self._part is None and is_description:
            self._part = []
            self.parts.append(self._part)
            self._depth = 1
        elif self._part is not None and tag not in self._VOID_TAGS:
            self._depth += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # Images and other void elements carry no useful description text.
        return

    def handle_endtag(self, tag: str) -> None:
        if self._part is None:
            return
        self._depth -= 1
        if self._depth == 0:
            self._part = None

    def handle_data(self, data: str) -> None:
        if self._part is not None:
            self._part.append(data)


def _comparable_text(value: str) -> str:
    return re.sub(r"\W+", "", value.casefold())


def _parse_detail_description(html: str, title: str = "") -> str:
    structured_candidates = [
        item.get("description", "")
        for item in common.jsonld_event_items(html or "")
        if item.get("description")
    ]

    parser = _DetailDescriptionParser()
    parser.feed(html or "")

    title_key = _comparable_text(title)
    candidates = []
    seen = set()
    raw_candidates = structured_candidates or [" ".join(part) for part in parser.parts]
    for candidate in raw_candidates:
        text = common.clean_html(candidate)
        key = text.casefold()
        if not text or key in seen or (title_key and _comparable_text(text) == title_key):
            continue
        seen.add(key)
        candidates.append(text)

    # Prefer the structured copy: unlike the visible TYPO3 markup, it contains
    # clean email addresses without anti-scraping text inserted between spans.
    # The longest fallback block avoids repeating Much's title/date teaser.
    return common.concise_description(max(candidates, key=len, default=""))


def _structured_detail_context(html: str, title: str) -> dict[str, str]:
    items = common.jsonld_event_items(html or "")
    item = next((candidate for candidate in items
                 if _comparable_text(candidate.get("name", "")) == _comparable_text(title)),
                items[0] if items else {})
    location = item.get("location") if isinstance(item.get("location"), dict) else {}
    address = location.get("address") if isinstance(location.get("address"), dict) else {}
    venue_parts = [
        location.get("name", ""),
        address.get("streetAddress", ""),
        " ".join(filter(None, [address.get("postalCode", ""),
                                address.get("addressLocality", "")])),
    ]
    venue = ", ".join(part for part in venue_parts if part)
    description = _parse_detail_description(html, title)
    if not description and item:
        start = common.parse_iso_date(item.get("startDate", ""))
        end = common.parse_iso_date(item.get("endDate", ""))
        schedule = f" am {start:%d.%m.%Y}" if start else ""
        if start and start.time().strftime("%H:%M") != "00:00":
            if end and end.date() == start.date() and end > start:
                schedule += f" von {start:%H:%M} bis {end:%H:%M} Uhr"
            else:
                schedule += f" um {start:%H:%M} Uhr"
        place = f" am Veranstaltungsort „{venue}“" if venue else " in Much"
        description = f"„{title}“ findet{schedule}{place} statt."
    return {
        "description": common.concise_description(description),
        "venue": common.normalize_venue_name(venue),
    }


def _fallback_description(event: dict) -> str:
    start = common.parse_iso_date(event.get("start_date") or "")
    schedule = f" am {start:%d.%m.%Y}" if start else ""
    if event.get("time"):
        schedule += f" von {event['time'].replace('–', ' bis ')} Uhr"
    place = f" am Veranstaltungsort „{event['venue']}“" if event.get("venue") else " in Much"
    return f"„{event.get('title', '')}“ findet{schedule}{place} statt."


def _enrich_missing_descriptions(events: list, source: str) -> list:
    contexts_by_link: dict[str, dict[str, str]] = {}
    failed_links = set()

    for event in events:
        if event.get("description"):
            continue
        link = (event.get("link") or "").strip()
        if not link:
            continue
        if link not in contexts_by_link and link not in failed_links:
            try:
                detail_html = common.fetch_detail_url(
                    link, cache_namespace="much", timeout=20)
                contexts_by_link[link] = _structured_detail_context(
                    detail_html, event.get("title") or "")
            except Exception as exc:
                failed_links.add(link)
                common.log_source_error(source, exc)
        context = contexts_by_link.get(link, {})
        event["description"] = context.get("description") or _fallback_description(event)
        if not event.get("venue") and context.get("venue"):
            event["venue"] = context["venue"]
    return events


def fetch() -> list:
    source = "Much"
    try:
        html = common.fetch_url(_URL, timeout=20)
        events = common.events_from_time_listing(
            html, source, "Much", "lokal markt kultur outdoor konzert", 0.9, _BASE)
        return _enrich_missing_descriptions(events, source)
    except Exception as e:
        common.log_source_error(source, e)
        return []
