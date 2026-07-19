"""Shared helpers for regional Bonn/Rhein-Sieg source scrapers."""

import re
import urllib.parse
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
from .. import common
from ..dates import MONTH_DE, MONTH_EN
from ..source_types import TextParser


class ParserEmptyError(RuntimeError):
    """A source responded, but its parser produced no trustworthy records."""


VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
})


class ClassScopedTextParser(HTMLParser):
    """Collect text inside elements selected by attribute matcher callables."""

    def __init__(self, targets: dict[str, object]) -> None:
        super().__init__(convert_charrefs=True)
        self.targets = targets
        self.parts = {name: [] for name in targets}
        self._target = ""
        self._depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if not self._target:
            self._target = next((
                name for name, matcher in self.targets.items()
                if matcher(tag, attributes)
            ), "")
            if self._target:
                self._depth = 1
        elif tag not in VOID_TAGS:
            self._depth += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        return

    def handle_endtag(self, tag: str) -> None:
        if not self._target or tag in VOID_TAGS:
            return
        self._depth -= 1
        if self._depth == 0:
            self._target = ""

    def handle_data(self, data: str) -> None:
        if self._target:
            self.parts[self._target].append(data)

    def text(self, target: str) -> str:
        return common.clean_html(" ".join(self.parts.get(target, [])))


_MONTH = {
    **MONTH_DE,
    **MONTH_EN,
    "mar": 3, "mär": 3, "sept": 9, "oct": 10, "dec": 12,
}


def abs_url(base: str, href: str) -> str:
    return urllib.parse.urljoin(base, unescape(href or "").strip())


def clean(text: str) -> str:
    return common.clean_html(text or "")


def first_group(pattern: str, text: str, *, flags: int = re.S | re.I) -> str:
    match = re.search(pattern, text or "", flags)
    return match.group(1).strip() if match else ""


def first_group_clean(pattern: str, text: str, *, flags: int = re.S | re.I) -> str:
    return clean(first_group(pattern, text, flags=flags))


def enrich_descriptions(
    events: list,
    *,
    source: str,
    cache_namespace: str,
    extract_context,
    fallback,
    timeout: int = 15,
    detail_fetcher=None,
    needs_enrichment=None,
    merge_context=None,
) -> list:
    """Memoize shared detail fetches and fill missing event descriptions."""
    html_by_link = {}
    failed_links = set()
    needs_enrichment = needs_enrichment or (lambda event: not event.get("description"))
    for index, event in enumerate(events):
        if not needs_enrichment(event):
            continue
        link = (event.get("link") or "").strip()
        if link and link not in html_by_link and link not in failed_links:
            try:
                html_by_link[link] = detail_fetcher(link) if detail_fetcher else common.fetch_detail_url(
                    link, cache_namespace=cache_namespace, timeout=timeout)
            except Exception as exc:
                failed_links.add(link)
                common.log_source_error(source, exc)
        context = extract_context(html_by_link.get(link, ""), event) if link in html_by_link else {}
        if isinstance(context, str):
            context = {"description": context}
        if merge_context and context:
            event = merge_context(event, context)
            events[index] = event
        replacement = context.get("description") or fallback(event)
        if len(replacement) > len(event.get("description") or ""):
            event["description"] = replacement
        if not event.get("venue") and context.get("venue"):
            event["venue"] = context["venue"]
    return events


def parse_dt(text: str):
    text = clean(text)
    dt = common.parse_date(text)
    if dt:
        return dt
    m = re.search(r"(\d{1,2})\.\s*([A-Za-zäöüÄÖÜ.]+)\s*(20\d{2})", text)
    if not m:
        return None
    day, month, year = m.groups()
    mon = _MONTH.get(month.lower().rstrip("."))
    return datetime(int(year), mon, int(day)) if mon else None


def with_time(dt, text: str):
    if not dt:
        return None
    m = re.search(r"(\d{1,2}):(\d{2})", text or "")
    return dt.replace(hour=int(m.group(1)), minute=int(m.group(2))) if m else dt


def date_for_window(day: int, month: int):
    """Resolve a yearless day/month to the year that keeps it current.

    Listings that omit the year (e.g. the LVR calendar) otherwise default to
    ``TODAY.year``; during a late-December run a January date then lands in the
    year that is ending and gets dropped as stale. Pick the first of this year /
    next year that is not already past so the New-Year rollover is handled.
    """
    for year in (common.TODAY.year, common.TODAY.year + 1):
        try:
            dt = datetime(year, month, day)
        except ValueError:
            return None
        if dt >= common.TODAY:
            return dt
    return None


def time_text(text: str) -> str:
    times = re.findall(r"\d{1,2}:\d{2}", text or "")
    if len(times) >= 2:
        return f"{times[0]}–{times[1]}"
    return times[0] if times else ""


def city_from_text(text: str, default_city: str) -> str:
    return common.guess_city_from_text(text) or default_city


def dedupe(events: list) -> list:
    seen, out = set(), []
    for ev in events:
        key = (ev["source"], ev["title"].lower(), ev["date"], ev["city"].lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(ev)
    return out


def title_from_href(href: str) -> str:
    slug = urllib.parse.urlparse(unescape(href or "")).path.rstrip("/").split("/")[-1]
    slug = re.sub(r"\.(?:html|php)$", "", slug)
    slug = re.sub(r"[-_]+", " ", slug)
    return slug.strip().title()


def range_dates(text: str):
    text = clean(text)
    m = re.search(r"(\d{1,2})\.(\d{1,2})\.\s*[–-]\s*(\d{1,2})\.(\d{1,2})\.(20\d{2})", text)
    if m:
        start_day, start_month, end_day, end_month, year = (int(part) for part in m.groups())
        return datetime(year, start_month, start_day), datetime(year, end_month, end_day)
    dates = re.findall(r"\d{1,2}\.\d{1,2}\.20\d{2}", text)
    if dates:
        start = common.parse_date(dates[0])
        end = common.parse_date(dates[-1]) if len(dates) > 1 else start
        return start, end
    return parse_dt(text), None


def fetch_html_events(name: str, url: str, parser: TextParser, timeout: int = 25,
                      *, source_id: str = "", empty_is_healthy: bool = False) -> list:
    try:
        html = common.fetch_url(url, timeout=timeout)
        with common.capture_parser_metrics() as metrics:
            events = parser(html)
        parser_empty = (
            not events
            and metrics["out_of_window_count"] == 0
            and not empty_is_healthy
        )
        common._record_endpoint(
            url,
            parser_type="html",
            candidate_count=metrics["candidate_count"],
            out_of_window_count=metrics["out_of_window_count"],
            parsed_event_count=len(events),
            parser_empty=parser_empty,
        )
        if parser_empty:
            common.log_source_error(
                name,
                ParserEmptyError("parser returned no event records"),
                source_id=source_id,
            )
        return events
    except Exception as e:
        common.log_source_error(name, e, source_id=source_id)
        return []
