"""Local event calendars for Bonn districts and neighbourhood associations."""

import calendar
import json
import re
import urllib.parse
from datetime import datetime
from html.parser import HTMLParser

from .. import common, normalization, reviewed_corrections, richtext
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
HOLZLAR_URL = "https://bv-holzlar.de/veranstaltungen"
BRUESER_BERG_URL = "https://brueser-berg-puls.base44.app/"
_NBB_CALENDAR_URL = "https://www.nachbarschaftszentrum.info/termine/"
_BRUESER_BERG_SOURCE = "Veranstaltungen Brüser Berg"
_BRUESER_BERG_SOURCE_ID = "veranstaltungen-brueser-berg"
_BRUESER_BERG_LOCAL_VENUES = (
    "brüser berg",
    "brueser berg",
    "atelier der stadtteilkultur",
    "begegnungsort auf der wiese in der fußgängerzone",
    "begegnungsort auf der wiese in der fussgängerzone",
    "hardtberghalle",
    "telekom dome",
)
_JMJ_HOST = "jmj-online.de"


def _active_reviewed_map(group: str) -> dict[tuple[str, ...], object]:
    return {
        tuple(str(value) for value in entry["match"]): entry["value"]
        for entry in reviewed_corrections.active_entries(group, common.TODAY)
    }


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
        event["description_source"] = "generated"
    return events


def _brueser_berg_link(row: dict) -> str:
    for key in ("link", "contribution_link", "pdf_url"):
        candidate = str(row.get(key) or "").strip()
        parsed = urllib.parse.urlsplit(candidate)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            return candidate
    return BRUESER_BERG_URL


def _is_brueser_berg_row(row: dict) -> bool:
    venue = regional_common.clean(str(row.get("location") or "")).casefold()
    return any(regional_common.clean(marker).casefold() in venue for marker in _BRUESER_BERG_LOCAL_VENUES)


def _brueser_berg_venue(title: str, venue: str) -> str:
    """Keep the reviewed street while allowing later exact source updates."""
    normalized_title = title.casefold()
    generic_venues = {
        regional_common.clean(candidate).casefold()
        for candidate in (
            "",
            "Brüser Berg Zentrum",
            "Fußgängerzone Brüser Berg",
        )
    }
    if (
        "familienfest" in normalized_title
        and "flohmarkt" in normalized_title
        and regional_common.clean(venue).casefold() in generic_venues
    ):
        return "Borsigallee"
    return venue


def events_from_brueser_berg_json(raw: str) -> list:
    try:
        rows = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise regional_common.ParserEmptyError("invalid events JSON") from exc
    if not isinstance(rows, list) or not rows:
        raise regional_common.ParserEmptyError("events API returned no rows")

    local_rows = sorted(
        (row for row in rows if isinstance(row, dict) and _is_brueser_berg_row(row)),
        key=lambda row: (
            str(row.get("date") or row.get("event_date") or ""),
            str(row.get("time") or row.get("event_time") or ""),
            str(row.get("title") or "").casefold(),
        ),
    )
    if not local_rows:
        raise regional_common.ParserEmptyError("events API returned no Brüser Berg rows")

    events = []
    for row in local_rows:
        title = common.clean_html(str(row.get("title") or ""))
        date_text = str(row.get("date") or row.get("event_date") or "").strip()
        time_text = common.sanitize_time_text(
            str(row.get("time") or row.get("event_time") or "")
        )
        try:
            start = datetime.fromisoformat(f"{date_text}T{time_text or '00:00'}")
        except ValueError:
            continue
        end = None
        end_time_text = common.sanitize_time_text(str(row.get("end_time") or ""))
        if end_time_text:
            try:
                candidate = datetime.fromisoformat(f"{date_text}T{end_time_text}")
                if candidate > start:
                    end = candidate
            except ValueError:
                pass
        venue = _brueser_berg_venue(
            title,
            common.clean_html(str(row.get("location") or "")),
        )
        description = common.concise_description(str(row.get("description") or ""))
        if not description:
            description = common.factual_event_description(
                title, date_value=start, time_text=time_text, venue=venue, city="Bonn-Brüser Berg",
            )
        event = common.make_event(
            title, start, end, venue, "Bonn-Brüser Berg", description,
            _brueser_berg_link(row), _BRUESER_BERG_SOURCE,
            "stadtteil nachbarschaft kultur bildung beratung spiele workshop",
            0.8, time_text=time_text, all_day=not bool(time_text),
            source_id=_BRUESER_BERG_SOURCE_ID,
        )
        if event:
            events.append(event)
    return regional_common.dedupe(events)


def _nbb_calendar_occurrences(html: str) -> dict[tuple[str, str], dict]:
    """Index NBB's public occurrence URLs without trusting its excerpt copy."""
    occurrences = {}
    for raw_json in re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html or "",
        re.I | re.S,
    ):
        try:
            # Decode JSON before HTML entities. Descriptions can legitimately
            # contain ``&quot;``; unescaping the entire script would turn those
            # entities into unescaped JSON delimiters.
            payload = json.loads(raw_json.strip())
        except (TypeError, json.JSONDecodeError):
            continue
        nodes = payload if isinstance(payload, list) else [payload]
        for node in nodes:
            if not isinstance(node, dict) or node.get("@type") != "Event":
                continue
            title = common.clean_html(str(node.get("name") or ""))
            start_at = str(node.get("startDate") or "")
            link = str(node.get("url") or "").strip()
            if not (title and len(start_at) >= 10 and link.startswith("https://")):
                continue
            occurrences[(title.casefold(), start_at[:10])] = {
                "link": link,
                "end_at": str(node.get("endDate") or ""),
            }
    return occurrences


def _nbb_detail_description(html: str) -> str:
    parser = regional_common.ClassScopedTextParser({
        "description": lambda _tag, attrs: (
            "tribe-events-single-event-description" in (attrs.get("class") or "").split()
        ),
    })
    parser.feed(html or "")
    description = common.concise_description(parser.block_text("description"), max_chars=0)
    return re.sub(r"!\s*\?", "!", description)


def _enrich_brueser_berg_details(events: list) -> list:
    candidates = [
        event for event in events
        if urllib.parse.urlsplit(event.get("link") or "").hostname
        == "www.nachbarschaftszentrum.info"
    ]
    if not candidates:
        return events
    try:
        calendar = common.fetch_detail_url(
            _NBB_CALENDAR_URL,
            cache_namespace="brueser-berg-nbb-calendar-v2",
            timeout=15,
        )
        occurrences = _nbb_calendar_occurrences(calendar)
    except Exception as exc:
        common.log_source_error(f"{_BRUESER_BERG_SOURCE} detail index", exc)
        return events

    for event in candidates:
        occurrence = occurrences.get((event.get("title", "").casefold(), event.get("start_date", "")))
        if not occurrence:
            continue
        detail_link = occurrence["link"]
        try:
            detail_html = common.fetch_detail_url(
                detail_link,
                cache_namespace="brueser-berg-nbb-detail-v2",
                timeout=15,
            )
            description = _nbb_detail_description(detail_html)
        except Exception as exc:
            common.log_source_error(f"{_BRUESER_BERG_SOURCE} detail", exc)
            continue
        if description:
            event["description"] = description
            event["description_html"] = richtext.from_plain_text(description)
            event["description_source"] = common.description_source_for(description)
            event["link"] = detail_link
            event["link_kind"] = "detail"
        end_at = common.parse_date(occurrence.get("end_at") or "")
        if end_at:
            event["end_date"] = end_at.strftime("%Y-%m-%d")
            event["end_at"] = end_at.isoformat()
            if event.get("time") and end_at.strftime("%H:%M") != "00:00":
                event["time"] = f"{event['time'].split('–', 1)[0]}–{end_at:%H:%M}"
    return events


def fetch_brueser_berg() -> list:
    try:
        html = common.fetch_url(BRUESER_BERG_URL, timeout=20)
        app_match = re.search(
            r'\bappId\s*=\s*["\']([a-f0-9]{24})["\']', html, re.IGNORECASE,
        )
        if not app_match:
            raise regional_common.ParserEmptyError("Base44 application id not found")
        app_id = app_match.group(1)
        query = urllib.parse.urlencode({"sort": "date", "limit": 500})
        api_url = urllib.parse.urljoin(
            BRUESER_BERG_URL,
            f"api/apps/{app_id}/entities/Event?{query}",
        )
        payload = common.fetch_url(
            api_url,
            timeout=20,
            headers={"X-App-Id": app_id},
            expected_content_types=("application/json",),
        )
        return _enrich_brueser_berg_details(events_from_brueser_berg_json(payload))
    except Exception as exc:
        common.log_source_error(_BRUESER_BERG_SOURCE, exc, source_id=_BRUESER_BERG_SOURCE_ID)
        return []


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


def _dedupe_beuel_combined_festival(events: list) -> list:
    """Collapse the two cards for the one Beuelfest/Promenadenfest programme."""
    audited_titles = {
        "beuelfest und promenadenfest",
        "promenadenfest und beuelfest",
    }
    combined = {}
    output = []
    for event in events:
        title = common.clean_html(event.get("title", "")).casefold()
        normalized_title = re.sub(r"[^\w]+", " ", title).strip()
        is_combined_festival = normalized_title in audited_titles
        key = (
            event.get("link", "").rstrip("/"),
            event.get("start_date", ""),
        )
        if not is_combined_festival or not key[0]:
            output.append(event)
            continue
        previous_index = combined.get(key)
        if previous_index is None:
            combined[key] = len(output)
            output.append(event)
            continue
        previous = output[previous_index]
        if event.get("end_date", "") > previous.get("end_date", ""):
            output[previous_index] = event
    return output


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
        # Beuel.net is discovery-only. Never carry its editorial card copy into
        # the event record; fetch_beuel confirms the linked primary page below.
        description = common.factual_event_description(
            title, date_value=start, end_date_value=end, time_text=time_text,
            venue=venue, city=_beuel_city(venue),
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
            events.append(common.keep_only_event_master_data(event))
    return regional_common.dedupe(_dedupe_beuel_combined_festival(events))


def _jmj_kirmes_description(html: str) -> str:
    """Extract the current organizer-written overview from the Kirmes page."""
    entry = re.search(
        r'<div\b[^>]*class=["\'][^"\']*\bentry-content\b[^"\']*["\'][^>]*>'
        r'(.*?)</div>\s*<!--\s*\.entry-content\s*-->',
        html or "", re.IGNORECASE | re.DOTALL,
    )
    if not entry:
        return ""
    body = re.sub(
        r"<(?:script|style|blockquote)\b.*?</(?:script|style|blockquote)>",
        "",
        entry.group(1),
        flags=re.IGNORECASE | re.DOTALL,
    )
    paragraphs = [
        common.clean_html(paragraph)
        for paragraph in re.findall(r"<p\b[^>]*>(.*?)</p>", body, re.IGNORECASE | re.DOTALL)
    ]
    overview = next((
        paragraph for paragraph in paragraphs
        if re.search(r"\bstartet\s+die\s+Kirmes\b", paragraph, re.IGNORECASE)
        and re.search(r"\bKirmes\s+endet\b", paragraph, re.IGNORECASE)
    ), "")
    visitor_copy = re.search(
        r"\bAb\s+20\d{2}\s+startet\s+die\s+Kirmes\b",
        overview,
        re.IGNORECASE,
    )
    return common.concise_description(
        overview[visitor_copy.start():] if visitor_copy else overview,
    )


def _primary_description(event: dict, html: str, source: str) -> str:
    is_kirmes = re.search(
        r"\bkirmes\b", str(event.get("title") or ""), re.IGNORECASE,
    )
    if source != _JMJ_HOST or not is_kirmes:
        return ""
    return _jmj_kirmes_description(html)


def _reviewed_beuel_primary_link(event: dict, link: str) -> str:
    normalized_link = link.rstrip("/")
    programme_entries = reviewed_corrections.active_entries(
        "beuel_mirecourtplatz_programme", common.TODAY,
    )
    programme = programme_entries[0] if programme_entries else None
    if (
        programme is not None
        and normalized_link == "https://dein-phonzimmer.de"
        and event.get("title") == "Mitsingkonzert Französisch und Kölsch"
        and event.get("venue") == "Mirecourtplatz"
        and event.get("start_date") in programme["match"]
    ):
        return str(programme["value"])
    return link


def _confirm_beuel_primary_sources(events: list, primary_fetcher) -> list:
    """Keep discovery records only when their linked first-party page is readable."""
    confirmed = []
    primary_pages: dict[str, str] = {}
    failed_primary_pages: set[str] = set()
    replacements = _active_reviewed_map("beuel_primary_url_replacements")
    civic_aggregator_urls = frozenset(str(value) for value in replacements.values())
    for event in events:
        link = str(event.get("link") or "")
        replacement_key = (
            link.rstrip("/"),
            event.get("start_date", ""),
            event.get("end_date", ""),
        )
        link = str(replacements.get(replacement_key, link))
        link = _reviewed_beuel_primary_link(event, link)
        event["link"] = link
        hostname = (urllib.parse.urlsplit(link).hostname or "").casefold()
        source = hostname.removeprefix("www.")
        if not source or source in {"beuel.net", "www.beuel.net"}:
            continue
        if link in failed_primary_pages:
            continue
        if link in primary_pages:
            primary_html = primary_pages[link]
        else:
            try:
                primary_html = str(primary_fetcher(link) or "")
                if not primary_html.strip():
                    raise regional_common.ParserEmptyError(
                        "primary event page returned no content")
            except Exception as exc:
                failed_primary_pages.add(link)
                common.log_source_error(
                    f"Beuel.net primary ({source})", exc, source_id="beuel-net",
                )
                continue
            primary_pages[link] = primary_html
        event["source"] = (
            "Bonn district festivals (Beuel.net discovery)"
            if link in civic_aggregator_urls
            else source
        )
        event["source_id"] = "beuel-net"
        event["source_role"] = "primary"
        event["discovered_via"] = ["beuel-net"]
        description = _primary_description(event, primary_html, source)
        if description:
            event["description"] = description
            event["description_html"] = ""
            event["description_source"] = "scraped"
            confirmed.append(event)
        else:
            confirmed.append(common.keep_only_event_master_data(event))
    return confirmed


def _fetch_beuel_primary(url: str) -> str:
    kwargs = {}
    burg_entries = reviewed_corrections.active_entries(
        "burg_lede_programme", common.TODAY,
    )
    burg_url = str(burg_entries[0]["value"]) if burg_entries else ""
    if burg_url and url.rstrip("/") == burg_url.rstrip("/"):
        kwargs = {
            "brightdata_fallback": True,
            "allowed_hosts": ("www.burglede.de",),
            "required_body_markers": ("Veranstaltungen 2026", "Burg Lede"),
            "fallback_statuses": (403,),
        }
    return common.fetch_detail_url(
        url, cache_namespace="beuel-primary", timeout=20, **kwargs,
    )


def fetch_beuel() -> list:
    discovered = regional_common.fetch_html_events(
        "Beuel.net", BEUEL_URL, events_from_beuel_html, source_id="beuel-net",
    )
    return _confirm_beuel_primary_sources(
        discovered,
        primary_fetcher=_fetch_beuel_primary,
    )


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
        except ValueError:  # noqa: PERF203 - try the maintained date formats in order
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


def _bad_godesberg_venue(title: str, description: str) -> str:
    """Use only explicit detail-page place evidence; the calendar has none."""
    text = normalization.comparison_text(f"{title} {description}", separator="")
    if "rigalschewiese" in text or "rigalschenwiese" in text:
        return "Rigal'sche Wiese"
    # The recurring antique market uses several streets and squares. A detail
    # page mentioning Theaterplatz among them must not collapse the whole route
    # onto one pin, while the Street Food page explicitly names only this square.
    if "theaterplatz" in text and not any(marker in text for marker in ("antik", "troedelmarkt")):
        return "Theaterplatz"
    return "Bad Godesberger Innenstadt"


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
        venue = _bad_godesberg_venue(title, description)
        event = common.make_event(
            title, start, end, venue, "Bonn-Bad Godesberg",
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
            title, start, None, "Hardtberger Kulturzentrum", "Bonn-Duisdorf",
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


def _roleber_detail_context(html: str) -> dict[str, str]:
    parser = regional_common.ClassScopedTextParser({
        "description": lambda _tag, attrs: "tribe-events-single-event-description" in (attrs.get("class") or "").split(),
    })
    parser.feed(html or "")
    description = common.concise_description(parser.text("description"))
    return {
        "description": description,
        **regional_common.explicit_place_context(description, "Bonn-Roleber"),
    }


def _enrich_roleber_descriptions(events: list) -> list:
    def fallback(event):
        start = common.parse_iso_date(event.get("start_date") or "")
        return common.factual_event_description(
            event.get("title", ""), date_value=start,
            time_text=event.get("time", ""), venue=event.get("venue", ""),
            city=event.get("city", "Bonn-Roleber"),
        )

    for event in events:
        if not event.get("venue"):
            event["identity_venue"] = ""
            event["identity_venue_locked"] = True

    events = regional_common.enrich_descriptions(
        events,
        source="BSV Roleber",
        cache_namespace="bsv-roleber",
        extract_context=lambda html, _event: _roleber_detail_context(html),
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


# The Holzlar association publishes one Elementor loop item per event. Each item
# carries the day ("13" or "14.-15."), the month and year as two separate icon
# list entries, the title as an <h2>, and the venue as the following icon list
# entry. The WordPress REST API exposes the same posts but without any event
# date — the listing markup is the only place the real date exists.
_HOLZLAR_ITEM_BOUNDARY = r'(?=class="[^"]*\bveranstaltung type-veranstaltung\b)'
_HOLZLAR_DAY = re.compile(r'<p class="elementor-heading-title[^"]*">([^<]+)</p>')
_HOLZLAR_TITLE = re.compile(r'<h2 class="elementor-heading-title[^"]*">([^<]+)</h2>')
_HOLZLAR_LIST_TEXT = re.compile(r'elementor-icon-list-text">([^<]*)</span>')
_HOLZLAR_LINK = re.compile(r'href="(https://bv-holzlar\.de/veranstaltung/[^"]+)"')


def _holzlar_dates(day_text: str, month: str, year: str) -> tuple:
    days = re.findall(r"\d{1,2}", day_text or "")
    if not days or not month or not year:
        return None, None
    start = common.parse_date(f"{days[0]}. {month} {year}")
    if start is None or len(days) == 1:
        return start, start
    end = common.parse_date(f"{days[-1]}. {month} {year}")
    if end is None:
        return start, start
    if end < start:
        # A range may straddle a month boundary ("30.-02. September"); only the
        # closing month is printed, so roll the start back one month.
        month_number = start.month - 1 or 12
        year_number = start.year - (1 if start.month == 1 else 0)
        try:
            start = start.replace(year=year_number, month=month_number)
        except ValueError:
            last_day = calendar.monthrange(year_number, month_number)[1]
            start = start.replace(
                year=year_number, month=month_number, day=last_day,
            )
    return start, end


def events_from_holzlar_html(html: str) -> list:
    events = []
    for block in re.split(_HOLZLAR_ITEM_BOUNDARY, html or "")[1:]:
        title = common.clean_html(regional_common.first_group(_HOLZLAR_TITLE.pattern, block))
        list_texts = [regional_common.clean(text) for text in _HOLZLAR_LIST_TEXT.findall(block)]
        list_texts = [text for text in list_texts if text]
        day_text = regional_common.clean(regional_common.first_group(_HOLZLAR_DAY.pattern, block))
        month = list_texts[0] if list_texts else ""
        year = list_texts[1] if len(list_texts) > 1 else ""
        venue = list_texts[2] if len(list_texts) > 2 else ""
        start, end = _holzlar_dates(day_text, month, year)
        if not title or start is None:
            continue
        link = regional_common.first_group(_HOLZLAR_LINK.pattern, block) or HOLZLAR_URL
        is_audited_wuppertal_destination = (
            "herbstfahrt" in title.casefold()
            and venue.casefold() == "wuppertal schwebodrohm"
        )
        if is_audited_wuppertal_destination:
            city = "Wuppertal"
        elif "wuppertal" in venue.casefold():
            city = "Bonn-Holzlar"
        else:
            city = common.refine_city_from_text("Bonn-Holzlar", venue)
        event = common.make_event(
            title, start, end, venue, city,
            common.factual_event_description(
                title, date_value=start, end_date_value=end, venue=venue, city=city,
            ),
            link, "BV Holzlar",
            "stadtteil verein gemeinschaft", 1.0,
            all_day=True,
        )
        if event:
            events.append(event)
    return regional_common.dedupe(events)


def fetch_holzlar() -> list:
    return regional_common.fetch_html_events("BV Holzlar", HOLZLAR_URL, events_from_holzlar_html, source_id="bv-holzlar")
