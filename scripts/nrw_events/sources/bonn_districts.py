"""Local event calendars for Bonn districts and neighbourhood associations."""

import json
import re
import urllib.parse
from datetime import datetime
from html.parser import HTMLParser

from .. import common
from . import regional_common


VILICH_MUELDORF_ICAL = "https://www.bv-vilich-mueldorf.de/events/?ical=1"
BEUEL_URL = "https://beuel.net/events/"
BAD_GODESBERG_URL = "https://bad-godesberg.info/veranstaltungen"
BAD_GODESBERG_DETAILS_API = (
    "https://bad-godesberg.info/wp-json/wp/v2/veranstaltungen_st"
    "?per_page=100&_fields=link,title,content"
)
HARDTBERG_API = "https://www.hardtbergkultur.de/wp-json/wp/v2/posts"
ROLEBER_ICAL = "https://bsvroleber.de/events/?ical=1"
_ROLEBER_SCORE_FLOOR = 0.45


def _ensure_descriptions(events: list) -> list:
    for event in events:
        if event.get("description"):
            continue
        start = common.parse_iso_date(event.get("start_date") or "")
        event["description"] = common.factual_event_description(
            event.get("title", ""),
            date_value=start,
            time_text=event.get("time", ""),
            venue=event.get("venue", ""),
            city=event.get("city", ""),
        )
    return events


def fetch_vilich_mueldorf() -> list:
    source = "Bürgerverein Vilich-Müldorf"
    try:
        events = common.fetch_ical(
            VILICH_MUELDORF_ICAL,
            source,
            "Bonn-Vilich-Müldorf",
            "stadtteil nachbarschaft kultur familie markt",
            1.0,
        )
        return _ensure_descriptions(events)
    except Exception as exc:
        common.log_source_error(source, exc)
        return []


class _BeuelParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cards: list[dict] = []
        self._card: dict | None = None
        self._depth = 0
        self._title_depth = 0
        self._date_depth = 0
        self._link = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        if tag == "div" and "yel" in classes and self._card is None:
            self._card = {"title": [], "date": [], "texts": [], "venue": "", "links": []}
            self._depth = 1
            return
        if self._card is None:
            return
        if tag == "div":
            self._depth += 1
        if tag == "span" and "title" in classes:
            self._title_depth = 1
        elif self._title_depth and tag not in {"br", "img", "input"}:
            self._title_depth += 1
        if tag == "b":
            self._date_depth = 1
        elif self._date_depth and tag not in {"br", "img", "input"}:
            self._date_depth += 1
        if tag == "a":
            href = attributes.get("href") or ""
            absolute = urllib.parse.urljoin(BEUEL_URL, href)
            self._link = absolute
            if "/map/" in href:
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(absolute).query)
                self._card["venue"] = (query.get("q") or [""])[0]
            elif absolute.startswith("http"):
                self._card["links"].append(absolute)

    def handle_endtag(self, tag: str) -> None:
        if self._card is None:
            return
        if self._title_depth:
            self._title_depth -= 1
        if self._date_depth:
            self._date_depth -= 1
        if tag == "a":
            self._link = ""
        if tag == "div":
            self._depth -= 1
            if self._depth == 0:
                self.cards.append(self._card)
                self._card = None

    def handle_data(self, data: str) -> None:
        if self._card is None:
            return
        text = common.clean_html(data)
        if not text:
            return
        self._card["texts"].append(text)
        if "/map/" in self._link:
            self._card["venue"] = text
        if self._title_depth:
            self._card["title"].append(text)
        if self._date_depth:
            self._card["date"].append(text)


def _beuel_dates(value: str) -> tuple[datetime | None, datetime | None, str]:
    text = regional_common.clean(value)
    matches = list(re.finditer(
        r"(\d{1,2})\.(\d{1,2})\.(?:(20\d{2}))?(?:\s+(\d{1,2}):(\d{2}))?",
        text,
    ))
    if not matches:
        return None, None, ""

    def resolve(match, *, start: datetime | None = None) -> datetime | None:
        day, month, year, hour, minute = match.groups()
        if year:
            result = datetime(int(year), int(month), int(day))
        elif start:
            result = datetime(start.year, int(month), int(day))
            if result.date() < start.date():
                result = result.replace(year=result.year + 1)
        else:
            result = regional_common.date_for_window(int(day), int(month))
        if result and hour:
            result = result.replace(hour=int(hour), minute=int(minute))
        return result

    start = resolve(matches[0])
    end = resolve(matches[1], start=start) if len(matches) > 1 else start
    start_time = ":".join(matches[0].groups()[3:5]) if matches[0].group(4) else ""
    end_time = ":".join(matches[1].groups()[3:5]) if len(matches) > 1 and matches[1].group(4) else ""
    time_text = "–".join(value for value in (start_time, end_time) if value) if start_time else ""
    return start, end, time_text


def _beuel_city(venue: str) -> str:
    return common.refine_city_from_text("Bonn-Beuel", venue)


def _beuel_description(card: dict, title: str, date_text: str, venue: str) -> str:
    ignored = {title.casefold(), date_text.casefold(), venue.casefold(), "externer link:"}
    parts = []
    for text in card["texts"]:
        normalized = text.casefold()
        normalized_label = normalized.strip(" |")
        if normalized in ignored or normalized_label in {"externer link", "externer link:"}:
            continue
        if re.fullmatch(r"(?:heute|in \d+ tagen?)", normalized_label):
            continue
        if re.search(r"\b\d{1,2}\.\d{1,2}\.", normalized_label):
            continue
        if venue and venue.casefold() in normalized_label:
            continue
        if normalized.startswith(("beuel.net/", "www.")) or re.fullmatch(r"[\w.-]+\.[a-z]{2,}/?…?", normalized):
            continue
        if text not in parts:
            parts.append(text)
    description = common.concise_description(" ".join(parts))
    if re.fullmatch(
        r"(?:große\s+)?(?:evangelische\s+|katholische\s+)?(?:kirche|halle|rathaus|stadion|platz|straße|str\.?|rheinufer).{0,50}",
        description,
        re.IGNORECASE,
    ):
        return ""
    if re.fullmatch(r"ticket(?:/einladung)? erforderlich", description, re.IGNORECASE):
        return ""
    return description


def events_from_beuel_html(html: str) -> list:
    parser = _BeuelParser()
    parser.feed(html or "")
    events = []
    for card in parser.cards:
        title = common.clean_html(" ".join(card["title"]))
        date_text = common.clean_html(" ".join(card["date"]))
        start, end, time_text = _beuel_dates(date_text)
        if not title or not start:
            continue
        venue = common.clean_html(card["venue"])
        links = [link for link in card["links"] if "/events/#" not in link and "/map/" not in link]
        is_beuel_rathaus_market = (
            title.casefold() == "flohmarkt"
            and "möhneplatz" in venue.casefold()
            and any("beuelhats.de" in link.casefold() for link in links)
        )
        if is_beuel_rathaus_market:
            title = "Floh- und Trödelmarkt Beueler Rathausplatz"
            venue = "Beueler Rathausplatz (Möhneplatz)"
        description = _beuel_description(card, title, date_text, venue)
        if not description:
            description = common.factual_event_description(
                title, date_value=start, time_text=time_text, venue=venue, city=_beuel_city(venue)
            )
        event = common.make_event(
            title, start, end, venue, _beuel_city(venue), description,
            links[-1] if links else BEUEL_URL, "Beuel.net",
            (
                "stadtteil kultur flohmarkt trödelmarkt markt familie"
                if is_beuel_rathaus_market
                else "stadtteil kultur markt familie"
            ),
            0.95, time_text=time_text,
            all_day=not bool(time_text),
        )
        if event:
            events.append(event)
    return regional_common.dedupe(events)


def fetch_beuel() -> list:
    return regional_common.fetch_html_events("Beuel.net", BEUEL_URL, events_from_beuel_html)


class _BadGodesbergCalendarParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.entries: list[dict] = []
        self._entry: dict | None = None
        self._depth = 0
        self._heading = ""
        self._link = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        if tag == "article" and "kalender" in classes and self._entry is None:
            self._entry = {"dates": [], "title": [], "link": ""}
            self._depth = 1
            return
        if self._entry is None:
            return
        if tag == "article":
            self._depth += 1
        if tag in {"h2", "h4"}:
            self._heading = tag
        if tag == "a" and self._heading == "h4":
            self._link = urllib.parse.urljoin(BAD_GODESBERG_URL, attributes.get("href") or "")
            self._entry["link"] = self._link

    def handle_endtag(self, tag: str) -> None:
        if self._entry is None:
            return
        if tag in {"h2", "h4"}:
            self._heading = ""
        if tag == "a":
            self._link = ""
        if tag == "article":
            self._depth -= 1
            if self._depth == 0:
                self.entries.append(self._entry)
                self._entry = None

    def handle_data(self, data: str) -> None:
        if self._entry is None or not self._heading:
            return
        text = common.clean_html(data)
        if not text:
            return
        if self._heading == "h2":
            self._entry["dates"].append(text.rstrip(" -"))
        elif self._heading == "h4":
            self._entry["title"].append(text)


def _english_date(value: str) -> datetime | None:
    cleaned = re.sub(r"\s+", " ", value.replace(",", " ")).strip()
    for pattern in ("%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(cleaned, pattern)
        except ValueError:
            pass
    return regional_common.parse_dt(cleaned)


def _bad_godesberg_descriptions(raw: str) -> dict[str, str]:
    descriptions = {}
    for item in json.loads(raw or "[]"):
        link = (item.get("link") or "").rstrip("/")
        content = common.concise_description(item.get("content", {}).get("rendered", ""))
        if link and content:
            descriptions[link] = content
    return descriptions


def events_from_bad_godesberg_html(html: str, descriptions: dict[str, str]) -> list:
    parser = _BadGodesbergCalendarParser()
    parser.feed(html or "")
    events = []
    for entry in parser.entries:
        title = common.clean_html(" ".join(entry["title"]))
        dates = [_english_date(value) for value in entry["dates"]]
        dates = [value for value in dates if value]
        if not title or not dates:
            continue
        start, end = dates[0], dates[-1]
        link = (entry["link"] or BAD_GODESBERG_URL).rstrip("/")
        description = descriptions.get(link) or common.factual_event_description(
            title, date_value=start, venue="Bad Godesberger Innenstadt", city="Bonn-Bad Godesberg"
        )
        event = common.make_event(
            title, start, end, "Bad Godesberger Innenstadt", "Bonn-Bad Godesberg",
            description, link, "Bad Godesberg Stadtmarketing",
            "stadtfest markt familie kultur", 1.0, all_day=True,
        )
        if event:
            events.append(event)
    return regional_common.dedupe(events)


def fetch_bad_godesberg() -> list:
    source = "Bad Godesberg Stadtmarketing"
    try:
        html = common.fetch_url(BAD_GODESBERG_URL, timeout=25)
        details = common.fetch_url(BAD_GODESBERG_DETAILS_API, timeout=25)
        events = events_from_bad_godesberg_html(html, _bad_godesberg_descriptions(details))
        common._record_endpoint(
            BAD_GODESBERG_URL, parser_type="html+wordpress-rest",
            parsed_event_count=len(events), parser_empty=not bool(events),
        )
        return events
    except Exception as exc:
        common.log_source_error(source, exc)
        return []


def events_from_hardtberg_json(raw: str) -> list:
    events = []
    for item in json.loads(raw or "[]"):
        start_text = item.get("date") or ""
        try:
            start = datetime.fromisoformat(start_text)
        except (TypeError, ValueError):
            continue
        title = common.clean_html(item.get("title", {}).get("rendered", ""))
        description = common.concise_description(
            item.get("excerpt", {}).get("rendered", "")
            or item.get("content", {}).get("rendered", "")
        )
        if not description:
            description = common.factual_event_description(
                title, date_value=start, time_text=start.strftime("%H:%M"),
                venue="Hardtberger Kulturzentrum", city="Bonn-Duisdorf",
            )
        event = common.make_event(
            title, start, start, "Hardtberger Kulturzentrum", "Bonn-Duisdorf",
            description, item.get("link") or "https://www.hardtbergkultur.de/",
            "Hardtberg Kultur", "kultur konzert ausstellung", 1.0,
            time_text=start.strftime("%H:%M"), all_day=False,
        )
        if event:
            events.append(event)
    return regional_common.dedupe(events)


def fetch_hardtberg() -> list:
    source = "Hardtberg Kultur"
    params = urllib.parse.urlencode({
        "per_page": 100,
        "after": common.TODAY.strftime("%Y-%m-%dT00:00:00"),
        "before": common.END_DATE.strftime("%Y-%m-%dT23:59:59"),
        "orderby": "date",
        "order": "asc",
        "_fields": "date,link,title,content,excerpt",
    })
    url = f"{HARDTBERG_API}?{params}"
    try:
        raw = common.fetch_url(url, timeout=25)
        payload = json.loads(raw)
        if not isinstance(payload, list):
            raise ValueError("Hardtberg REST response is not an event list")
        events = events_from_hardtberg_json(raw)
        # An authoritative empty REST collection is a genuine healthy-empty
        # result. Non-empty payloads that yield no records indicate parser or
        # filtering drift and must retain the previous snapshot.
        common._record_endpoint(
            url,
            parser_type="wordpress-rest",
            parsed_event_count=len(events),
            parser_empty=bool(payload) and not events,
        )
        return events
    except Exception as exc:
        common.log_source_error(source, exc)
        return []


def _roleber_detail_description(html: str) -> str:
    parser = regional_common.ClassScopedTextParser({
        "description": lambda _tag, attrs: "tribe-events-single-event-description" in (attrs.get("class") or "").split(),
    })
    parser.feed(html or "")
    return common.concise_description(parser.text("description"))


def _enrich_roleber_descriptions(events: list) -> list:
    def fallback(event):
        start = common.parse_iso_date(event.get("start_date") or "")
        return common.factual_event_description(
            event.get("title", ""), date_value=start,
            time_text=event.get("time", ""), venue=event.get("venue", ""),
            city=event.get("city", "Bonn-Roleber"),
        )

    events = regional_common.enrich_descriptions(
        events,
        source="BSV Roleber",
        cache_namespace="bsv-roleber",
        extract_context=lambda html, _event: _roleber_detail_description(html),
        fallback=fallback,
        needs_enrichment=lambda event: len(event.get("description") or "") < 120,
    )
    for event in events:
        # The global ranking deliberately downranks kids-only listings. This
        # requested, primary neighbourhood source should still clear the
        # publication threshold so Roleber is not left without its real camps.
        event["score"] = max(float(event.get("score") or 0), _ROLEBER_SCORE_FLOOR)
    return _ensure_descriptions(events)


def fetch_roleber() -> list:
    source = "BSV Roleber"
    try:
        events = common.fetch_ical(
            ROLEBER_ICAL, source, "Bonn-Roleber", "sport verein familie", 1.0
        )
        return _enrich_roleber_descriptions(events)
    except Exception as exc:
        common.log_source_error(source, exc)
        return []
