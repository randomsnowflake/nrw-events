"""Traditional Bonn district fairs from their first-party organizer page."""

from __future__ import annotations

import re
from datetime import datetime
from html.parser import HTMLParser
from typing import TypedDict

from .. import common
from . import regional_common as rc


_SOURCE = "Kirmes in Bonn"
_SOURCE_ID = "bonnkirmes"
_URL = "https://www.bonnkirmes.com/"

_DATE_RANGE_RE = re.compile(
    r"(?<!\d)(\d{1,2})\.(\d{1,2})\.(20\d{2})?\s*"
    r"(?:[–—-]|bis(?:\s+zum)?)\s*"
    r"(\d{1,2})\.(\d{1,2})\.(20\d{2})(?!\d)",
    re.IGNORECASE,
)
_CITY_MARKERS = (
    ("bad godesberg", "Bonn-Bad Godesberg"),
    ("duisdorf", "Bonn-Duisdorf"),
    ("beuel", "Bonn-Beuel"),
)
_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}


class _Section(TypedDict):
    title: str
    paragraphs: list[str]


class _SectionParser(HTMLParser):
    """Collect paragraphs under each h3 without depending on Wix CSS classes."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.sections: list[_Section] = []
        self._section: _Section | None = None
        self._capture = ""
        self._depth = 0
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if self._capture:
            if tag not in _VOID_TAGS:
                self._depth += 1
            return
        if tag in {"h3", "p"}:
            self._capture = tag
            self._depth = 1
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if not self._capture:
            return
        self._depth -= 1
        if self._depth:
            return
        text = common.clean_html(" ".join(self._text))
        captured = self._capture
        self._capture = ""
        self._text = []
        if captured == "h3":
            self._section = _Section(title=text, paragraphs=[])
            self.sections.append(self._section)
        elif captured == "p" and self._section is not None and text:
            self._section["paragraphs"].append(text)


def _date_range(text: str) -> tuple[datetime, datetime] | None:
    match = _DATE_RANGE_RE.search(text)
    if not match:
        return None
    start_day, start_month, start_year, end_day, end_month, end_year = match.groups()
    year = int(start_year or end_year)
    try:
        return (
            datetime(year, int(start_month), int(start_day)),
            datetime(int(end_year), int(end_month), int(end_day)),
        )
    except ValueError:
        return None


def _city(title: str) -> str:
    normalized = title.casefold()
    for marker, city in _CITY_MARKERS:
        if marker in normalized:
            return city
    return "Bonn"


def _venue(text: str, city: str) -> str:
    matches = list(re.finditer(
        r"\b(?:am|auf\s+der|auf\s+dem)\s+(.+?)"
        r"(?:\s+in\s+Bad\s+Godesberg)?\s+"
        r"(?:findet\b|vom\b|die\s+traditionelle\b)",
        text,
        re.IGNORECASE,
    ))
    if matches:
        venue = common.clean_html(matches[-1].group(1)).strip(" .,;:")
        return re.sub(r"'schen\s+Wiese$", "'sche Wiese", venue, flags=re.IGNORECASE)
    return city


def _description(paragraphs: list[str], start: datetime) -> str:
    selected = paragraphs[:1]
    selected.extend(
        paragraph
        for paragraph in paragraphs[1:]
        if re.search(r"\b(?:Öffnungszeiten|Zum Schutz)\b", paragraph, re.IGNORECASE)
    )
    description = " ".join(selected)
    # The organizer sometimes updates the actual date range without replacing
    # the prose year immediately before it. The parsed range is the event-scoped
    # fact; reconcile only this narrow sentence instead of rewriting other years.
    start_date_pattern = start.strftime("%d.%m.%Y").replace(".", r"\.")
    description = re.sub(
        rf"\bIm Jahr \d{{3,4}}(?=\s+findet\s+die\s+Kirmes\b[^.!?<>\n]{{0,160}}\bvom\s+{start_date_pattern}\b)",
        f"Im Jahr {start.year}",
        description,
        flags=re.IGNORECASE,
    )
    return common.concise_description(description)


def events_from_html(html: str, *, strict: bool = False) -> list:
    parser = _SectionParser()
    parser.feed(html)
    fair_sections = [
        section for section in parser.sections
        if "kirmes" in str(section["title"]).casefold()
    ]
    dated_sections = 0
    events = []
    for section in fair_sections:
        title = str(section["title"])
        paragraphs = [str(value) for value in section["paragraphs"]]
        prose = " ".join(paragraphs)
        bounds = _date_range(prose)
        if not bounds:
            continue
        dated_sections += 1
        start, end = bounds
        city = _city(title)
        event = common.make_event(
            title,
            start,
            end,
            _venue(prose, city),
            city,
            _description(paragraphs, start),
            _URL,
            _SOURCE,
            "kirmes volksfest stadtteilfest fahrgeschäfte",
            1.0,
            all_day=True,
            source_id=_SOURCE_ID,
            description_source="scraped",
            default_category_key="festival",
            category_locked=True,
        )
        if event:
            events.append(event)
    if strict and not dated_sections:
        raise rc.ParserEmptyError("Kirmes in Bonn page contained no dated fair sections")
    return rc.dedupe(events)


def fetch() -> list:
    try:
        html = common.fetch_url(_URL, timeout=25)
        with common.capture_parser_metrics() as metrics:
            events = events_from_html(html, strict=True)
        parser_empty = not events and metrics["out_of_window_count"] == 0
        common._record_endpoint(
            _URL,
            parser_type="html",
            candidate_count=metrics["candidate_count"],
            out_of_window_count=metrics["out_of_window_count"],
            parsed_event_count=len(events),
            parser_empty=parser_empty,
        )
        if parser_empty:
            raise rc.ParserEmptyError("Kirmes in Bonn parser returned no events")
        return events
    except Exception as exc:
        common.log_source_error(_SOURCE, exc, source_id=_SOURCE_ID)
        return []
