"""
Siegburg — Kreisstadt event calendar (Rhein-Sieg-Kreis, ~10 km from Bonn).

Reads:  siegburg.de combined calendar iCal export (RFC 5545 .ics).
Yields: exhibitions, museum events, town markets, readings and local happenings.
        The feed also carries recurring/historical anniversary entries; those fall
        outside the window and are dropped by the shared make_event guard.
"""

from html.parser import HTMLParser

from .. import common

_ICS_URL = ("https://siegburg.de/kalender/kombinierter-kalender/"
            "event.ics?weekends=false&tagMode=ANY")


class _DetailDescriptionParser(HTMLParser):
    """Collect the event subtitle and body from a Siegburg detail page."""

    _TARGETS = ("subtitle", "description")
    _VOID_TAGS = {
        "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts = {target: [] for target in self._TARGETS}
        self._target = ""
        self._depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        target = ""
        if attributes.get("id") == "event_subtitle_wrapper":
            target = "subtitle"
        elif "dwa_event_description_text" in classes:
            target = "description"

        if not self._target and target:
            self._target = target
            self._depth = 1
        elif self._target and tag not in self._VOID_TAGS:
            self._depth += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # Images and other void elements carry no useful description text.
        return

    def handle_endtag(self, tag: str) -> None:
        if not self._target:
            return
        self._depth -= 1
        if self._depth == 0:
            self._target = ""

    def handle_data(self, data: str) -> None:
        if self._target:
            self.parts[self._target].append(data)


def _parse_detail_description(html: str) -> str:
    parser = _DetailDescriptionParser()
    parser.feed(html or "")

    description_parts = []
    normalized_parts = set()
    for target in parser._TARGETS:
        text = common.clean_html(" ".join(parser.parts[target]))
        normalized = text.casefold()
        if text and normalized not in normalized_parts:
            description_parts.append(text)
            normalized_parts.add(normalized)
    return common.concise_description(" ".join(description_parts))


def _fallback_description(event: dict) -> str:
    start = common.parse_iso_date(event.get("start_date") or "")
    schedule = f" am {start:%d.%m.%Y}" if start else ""
    if event.get("time"):
        schedule += f" von {event['time'].replace('–', ' bis ')} Uhr"
    venue = f" im {event['venue']}" if event.get("venue") else " in Siegburg"
    return f"„{event.get('title', '')}“ findet{schedule}{venue} statt."


def _enrich_missing_descriptions(events: list, source: str) -> list:
    descriptions_by_link: dict[str, str] = {}
    failed_links = set()

    for event in events:
        if event.get("description"):
            continue
        link = (event.get("link") or "").strip()
        if not link:
            continue
        if link not in descriptions_by_link and link not in failed_links:
            try:
                descriptions_by_link[link] = _parse_detail_description(
                    common.fetch_detail_url(link, cache_namespace="siegburg")
                )
            except Exception as exc:
                failed_links.add(link)
                common.log_source_error(source, exc)
        description = descriptions_by_link.get(link, "")
        if description:
            event["description"] = description
        else:
            event["description"] = _fallback_description(event)
    return events


def fetch() -> list:
    source = "Siegburg"
    try:
        events = common.fetch_ical(_ICS_URL, source, "Siegburg", "", 1.0)
        return _enrich_missing_descriptions(events, source)
    except Exception as e:
        common.log_source_error(source, e)
        return []
