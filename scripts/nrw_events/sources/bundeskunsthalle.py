"""
Bundeskunsthalle — official events and current exhibitions (Bonn Museum Mile).

Reads:  bundeskunsthalle.de/en/exhibitions
Yields: current/upcoming exhibitions, scraped live from the page's heading pairs:
        a title heading followed by a date-range heading, e.g.
        "Peter Hujar Eyes Open in the Dark" / "27 February to 23 August 2026".

The page has no JSON-LD, so this parses the rendered headings. Exhibition titles
and dates are discovered live — nothing is hardcoded.
"""

import re
from datetime import datetime
from html import unescape
from html.parser import HTMLParser

from .. import common
from . import regional_common as rc

_URL = "https://www.bundeskunsthalle.de/en/exhibitions"
_EVENTS_URL = "https://www.bundeskunsthalle.de/veranstaltungen"

# "27 February to 23 August 2026", "11 October 2026 to 2 May 2027",
# "1 May to 1 November 2026", "until 23 August 2026"
_RANGE_RE = re.compile(
    r"^(?:(?P<sd>\d{1,2})\s+(?P<sm>[A-Za-z]+)(?:\s+(?P<sy>20\d{2}))?\s+to\s+)?"
    r"(?:until\s+)?(?P<ed>\d{1,2})\s+(?P<em>[A-Za-z]+)\s+(?P<ey>20\d{2})$",
    re.I,
)


def _exhibition_cards(html: str) -> list:
    """Return (title, date text, detail URL) cards from exhibition sections."""
    cards = []
    for section in re.findall(r"<section\b.*?</section>", html, re.S | re.I):
        h2 = re.search(r"<h2[^>]*>(.*?)</h2>", section, re.S | re.I)
        h3 = re.search(r"<h3[^>]*>(.*?)</h3>", section, re.S | re.I)
        if not (h2 and h3):
            continue
        href = ""
        # Prefer the readable detail page behind the explicit "More Information"
        # button. Ticket-shop and image links are nearby but not event details.
        more = re.search(
            r'<a[^>]+href="([^"]+)"[^>]*aria-label="[^"]*exhibition page with further information[^"]*"',
            section, re.S | re.I,
        )
        if more:
            href = more.group(1)
        else:
            first_internal = re.search(r'<a[^>]+href="(/en/[^"]+)"', section, re.S | re.I)
            if first_internal:
                href = first_internal.group(1)
        link = common.urllib.parse.urljoin(_URL, href) if href else _URL
        cards.append((common.clean_html(h2.group(1)), common.clean_html(h3.group(1)), link))
    return cards


def _parse_range(text: str):
    m = _RANGE_RE.match(text.strip())
    if not m:
        return None, None
    em = common.MONTH_EN.get(m.group("em").lower())
    if not em:
        return None, None
    ey = int(m.group("ey"))
    try:
        end = datetime(ey, em, int(m.group("ed")))
    except ValueError:
        return None, None
    if m.group("sd") and m.group("sm"):
        sm = common.MONTH_EN.get(m.group("sm").lower())
        if not sm:
            return None, end
        sy = int(m.group("sy") or ey)
        try:
            return datetime(sy, sm, int(m.group("sd"))), end
        except ValueError:
            return None, end
    return None, end  # "until <date>" → open start


def _tidy_title(t: str) -> str:
    # Headings concatenate title + subtitle spans with no space ("HujarEyes").
    return re.sub(r"([a-zäöüß])([A-ZÄÖÜ])", r"\1 \2", t).strip()


class _EventSearchFormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_form = False
        self.api_url = ""
        self.fields = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "form" and values.get("id") == "event-search-form":
            self.in_form = True
            self.api_url = values.get("data-api-search-url", "")
            return
        if not self.in_form or tag != "input" or not values.get("name"):
            return
        input_type = values.get("type", "text")
        if input_type in {"checkbox", "radio"} and "checked" not in values:
            return
        self.fields.append((values["name"], values.get("value", "")))

    def handle_endtag(self, tag):
        if tag == "form" and self.in_form:
            self.in_form = False


def _event_search_form(html: str) -> tuple[str, list]:
    parser = _EventSearchFormParser()
    parser.feed(html)
    return parser.api_url, parser.fields


def _card_date(date_text: str):
    match = re.search(r"(\d{1,2})\.\s*([A-Za-zäöüÄÖÜ]+)", date_text or "")
    if not match:
        return None
    month = common.MONTH_DE.get(match.group(2).casefold().rstrip("."))
    return rc.date_for_window(int(match.group(1)), month) if month else None


def _card_times(date_text: str) -> tuple[int, int, int, int] | None:
    match = re.search(
        r",\s*(\d{1,2})(?::(\d{2}))?\s*(?:–|—|-)\s*(\d{1,2})(?::(\d{2}))?\s*Uhr",
        date_text or "",
        flags=re.I,
    )
    if not match:
        return None
    return (
        int(match.group(1)), int(match.group(2) or 0),
        int(match.group(3)), int(match.group(4) or 0),
    )


def _series_key(event: dict) -> tuple[str, str]:
    normalize = lambda value: re.sub(r"[^a-zäöüß0-9]", "", (value or "").casefold())
    return normalize(event.get("title", "")), normalize(event.get("description", ""))


def _events_from_search_results(content: str, source: str = "Bundeskunsthalle") -> list:
    events_by_series = {}
    for article in re.findall(r'<article class="card\b.*?</article>', content or "", re.S | re.I):
        heading = re.search(r'<h3[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>\s*</h3>', article, re.S | re.I)
        if not heading:
            continue
        date_match = re.search(
            r'<span[^>]+class="[^"]*events__card__date[^"]*"[^>]*>(.*?)</span>',
            heading.group(2), re.S | re.I,
        )
        if not date_match:
            continue
        date_text = common.clean_html(date_match.group(1))
        start = _card_date(date_text)
        if not common.window_contains(start):
            continue

        title = common.clean_html(re.sub(
            r'<span[^>]+class="[^"]*events__card__date[^"]*"[^>]*>.*?</span>',
            "", heading.group(2), flags=re.S | re.I,
        ))
        if not title:
            continue
        link = common.urllib.parse.urljoin(_EVENTS_URL, unescape(heading.group(1)))
        paragraphs = [
            common.clean_html(value) for value in re.findall(r"<p[^>]*>(.*?)</p>", article, re.S | re.I)
        ]
        description = max((value for value in paragraphs if value and "Zur Veranstaltung" not in value),
                          key=len, default="")

        end = start
        time_text = ""
        times = _card_times(date_text)
        if times:
            start_hour, start_minute, end_hour, end_minute = times
            start = start.replace(hour=start_hour, minute=start_minute)
            end = end.replace(hour=end_hour, minute=end_minute)
            time_text = f"{start_hour:02d}:{start_minute:02d}–{end_hour:02d}:{end_minute:02d}"

        event = common.make_event(
            title, start, end, "Bundeskunsthalle", "Bonn", description, link,
            source, f"Bundeskunsthalle Kultur Veranstaltung {title}", trust=1.0,
            time_text=time_text,
        )
        if not event:
            continue
        if "badge--free" in article or re.search(r"\bKostenlos\b", common.clean_html(article), re.I):
            event["price"] = "kostenlos"

        key = _series_key(event)
        current = events_by_series.get(key)
        if current is None or event["start_date"] < current["start_date"]:
            events_by_series[key] = event
    return sorted(events_by_series.values(), key=lambda event: (event["start_date"], event["title"]))


def _fetch_program_events(source: str) -> list:
    page = common.fetch_url(_EVENTS_URL, timeout=25)
    api_path, fields = _event_search_form(page)
    if not api_path or not fields:
        return []
    dated_fields = []
    for name, value in fields:
        if name.endswith("[startDate]"):
            value = common.TODAY.strftime("%Y-%m-%d")
        elif name.endswith("[endedBeforeDate]"):
            value = common.END_DATE.strftime("%Y-%m-%d")
        dated_fields.append((name, value))
    api_url = common.urllib.parse.urljoin(_EVENTS_URL, api_path)
    payload = common.post_form(api_url, dated_fields, timeout=25, headers={"Referer": _EVENTS_URL})
    content = ((payload.get("data") or {}).get("content") or "") if isinstance(payload, dict) else ""
    return _events_from_search_results(content, source)


def _fetch_exhibitions(source: str) -> list:
    try:
        html = common.fetch_url(_URL, timeout=25)
    except Exception as e:
        common.log_source_error(f"{source} exhibitions", e)
        return []

    events = []
    for title_raw, date_text, link in _exhibition_cards(html):
        start_dt, end_dt = _parse_range(date_text)
        if not end_dt:
            continue
        title = _tidy_title(title_raw)
        if len(title) < 3 or _RANGE_RE.match(title):
            continue
        # Treat as an exhibition spanning [start, end]; make_event keeps it if the
        # span overlaps the window. Open-start exhibitions use TODAY as start.
        ev = common.make_event(
            title, start_dt or common.TODAY, end_dt,
            "Bundeskunsthalle", "Bonn",
            "Museum Mile, Helmut-Kohl-Allee 4", link,
            source, "exhibition museum art", 1.0,
        )
        if ev:
            events.append(ev)
    return events


def fetch() -> list:
    source = "Bundeskunsthalle"
    try:
        program_events = _fetch_program_events(source)
    except Exception as e:
        common.log_source_error(f"{source} events", e)
        program_events = []
    return program_events + _fetch_exhibitions(source)
