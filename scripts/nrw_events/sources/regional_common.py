"""Shared helpers for regional Bonn/Rhein-Sieg source scrapers."""

import os
import re
import time
import urllib.parse
from collections.abc import Callable, Iterable
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
from .. import common
from ..dates import MONTH_ALL, resolve_yearless_date
from ..source_types import TextParser


class ParserEmptyError(RuntimeError):
    """A source responded, but its parser produced no trustworthy records."""


VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
})


BLOCK_TAGS = frozenset({
    "p", "div", "section", "article", "header", "footer", "ul", "ol", "dl",
    "table", "blockquote", "figure", "figcaption", "pre", "hr",
    "h1", "h2", "h3", "h4", "h5", "h6",
})
LINE_TAGS = frozenset({"br", "li", "dt", "dd", "tr"})


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
        self._mark_break(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._mark_break(tag)

    def handle_endtag(self, tag: str) -> None:
        if not self._target or tag in VOID_TAGS:
            return
        self._depth -= 1
        if self._depth == 0:
            self._target = ""
            return
        # Only a closing block ends a thought. Marking ``</li>`` too would put a
        # blank line between every bullet, turning a list into one-line
        # paragraphs, because the following ``<li>`` breaks as well.
        if tag in BLOCK_TAGS:
            self._mark_break(tag)

    def _mark_break(self, tag: str) -> None:
        """Record where the author ended a thought, before the tags are gone.

        The collected text nodes are joined afterwards; without these markers a
        paragraph boundary is indistinguishable from a space and the whole
        description arrives as one block.
        """
        if not self._target:
            return
        if tag in BLOCK_TAGS:
            self.parts[self._target].append("\n\n")
        elif tag in LINE_TAGS:
            self.parts[self._target].append("\n")

    def handle_data(self, data: str) -> None:
        if self._target:
            self.parts[self._target].append(data)

    def text(self, target: str) -> str:
        """Flattened to a single line — the historical contract for short fields."""
        return common.clean_html(" ".join(self.parts.get(target, [])))

    def block_text(self, target: str) -> str:
        """Prose with the source's paragraph breaks preserved."""
        return common.normalize_block_text("".join(self.parts.get(target, [])))


_MONTH = MONTH_ALL


def abs_url(base: str, href: str) -> str:
    return urllib.parse.urljoin(base, unescape(href or "").strip())


def clean(text: str) -> str:
    return common.clean_html(text or "")


def clean_blocks(text: str) -> str:
    """``clean`` for prose: keeps the paragraph breaks the source authored."""
    return common.clean_html_blocks(text or "")


def first_group(pattern: str, text: str, *, flags: int = re.S | re.I) -> str:
    match = re.search(pattern, text or "", flags)
    return match.group(1).strip() if match else ""


def first_group_clean(pattern: str, text: str, *, flags: int = re.S | re.I) -> str:
    return clean(first_group(pattern, text, flags=flags))


def html_attribute(tag: str, name: str) -> str:
    """Return one quoted HTML attribute without depending on its position."""
    match = re.search(rf"(?:^|\s){re.escape(name)}\s*=\s*([\"'])(.*?)\1", tag or "", re.I | re.S)
    return unescape(match.group(2)).strip() if match else ""


def tag_has_class(tag: str, class_name: str) -> bool:
    return class_name in html_attribute(tag, "class").split()


def class_tag_blocks(html: str, tag_name: str, class_name: str) -> list[str]:
    """Find complete non-nested tag blocks by class, regardless of attribute order."""
    pattern = re.compile(
        rf"<{re.escape(tag_name)}\b[^>]*>.*?</{re.escape(tag_name)}\s*>",
        re.I | re.S,
    )
    return [match.group(0) for match in pattern.finditer(html or "") if tag_has_class(match.group(0).split(">", 1)[0], class_name)]


def attribute_from_class_tag(html: str, tag_name: str, class_name: str, attribute: str) -> str:
    """Return an attribute from the first opening tag carrying ``class_name``."""
    for match in re.finditer(rf"<{re.escape(tag_name)}\b[^>]*>", html or "", re.I | re.S):
        tag = match.group(0)
        if tag_has_class(tag, class_name):
            return html_attribute(tag, attribute)
    return ""


def factual_fallback(default_city: str = "", calendar_name=""):
    """Return a shared factual-description builder for sparse calendar rows."""
    def build(event: dict) -> str:
        start = common.parse_iso_date(event.get("start_date") or "")
        resolved_calendar = calendar_name(event) if callable(calendar_name) else calendar_name
        return common.factual_event_description(
            event.get("title", ""), date_value=start or event.get("date", ""),
            time_text=event.get("time", ""), venue=event.get("venue", ""),
            city=event.get("city") or default_city, calendar_name=resolved_calendar,
        )
    return build


def meta_description(html: str, *, fallback_pattern: str = "") -> str:
    """Extract description metadata regardless of HTML attribute order."""
    for pattern in (
        r'<meta[^>]+(?:property|name)=["\'](?:og:description|description)["\'][^>]+content=(["\'])(.*?)\1',
        r'<meta[^>]+content=(["\'])(.*?)\1[^>]+(?:property|name)=["\'](?:og:description|description)["\']',
    ):
        match = re.search(pattern, html or "", re.I)
        if match:
            return common.concise_description(match.group(2))
    fallback = re.search(fallback_pattern, html or "", re.I | re.S) if fallback_pattern else None
    return common.concise_description(fallback.group(1)) if fallback else ""


def meta_detail_description(
    url: str,
    *,
    namespace: str,
    source: str,
    timeout: int = 15,
    fallback_pattern: str = "",
) -> str:
    """Fetch a cached detail page and return its metadata description safely."""
    try:
        html = common.fetch_detail_url(url, cache_namespace=namespace, timeout=timeout)
        return meta_description(html, fallback_pattern=fallback_pattern)
    except Exception as exc:
        common.log_source_error(f"{source} detail", exc)
        return ""


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
    batch_timeout = float(os.environ.get("NRW_EVENTS_DETAIL_BATCH_TIMEOUT_SECONDS", "45"))
    deadline = time.monotonic() + max(batch_timeout, 0.0)
    html_by_link = {}
    failed_links = set()
    needs_enrichment = needs_enrichment or (lambda event: not event.get("description"))
    for index, event in enumerate(events):
        if not common.event_in_window(event):
            continue
        if not needs_enrichment(event):
            continue
        link = (event.get("link") or "").strip()
        remaining = deadline - time.monotonic()
        if link and link not in html_by_link and link not in failed_links and remaining >= 3.0:
            try:
                request_timeout = (
                    float(timeout) if remaining >= float(timeout) * 2
                    else max(1.0, remaining / 3.0)
                )
                html_by_link[link] = detail_fetcher(link) if detail_fetcher else common.fetch_detail_url(
                    link,
                    cache_namespace=cache_namespace,
                    timeout=request_timeout,
                )
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
            event["description_source"] = common.description_source_for(replacement)
        if context.get("description_html"):
            event["description_html"] = context["description_html"]
        if not event.get("venue") and context.get("venue"):
            event["venue"] = context["venue"]
        if not event.get("venue_address") and context.get("venue_address"):
            event["venue_address"] = context["venue_address"]
    return events


def parse_dt(text: str):
    return common.parse_date(clean(text))


def with_time(dt, text: str):
    if not dt:
        return None
    m = re.search(r"(\d{1,2}):(\d{2})", text or "")
    return dt.replace(hour=int(m.group(1)), minute=int(m.group(2))) if m else dt


def date_for_window(day: int, month: int):
    """Resolve a yearless date through the shared rollover/grace policy."""
    resolution = resolve_yearless_date(day, month, common.TODAY)
    return resolution.value if resolution else None


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


def dedupe_occurrences(events: list) -> list:
    """Remove repeated records without collapsing distinct same-day shows."""
    seen, out = set(), []
    for event in events:
        key = (
            event.get("source", ""),
            event.get("title", "").casefold(),
            event.get("start_at") or (event.get("date", ""), event.get("time", "")),
            event.get("venue", "").casefold(),
            event.get("city", "").casefold(),
        )
        if key not in seen:
            seen.add(key)
            out.append(event)
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
                      *, source_id: str,
                      empty_is_healthy: bool | Callable[[str], bool] = False,
                      fetcher=None,
                      page_urls: Iterable[str] | Callable[[str, int], str] | None = None,
                      stop_when: Callable[[str, int], bool] | None = None,
                      max_pages: int = 30) -> list:
    """Fetch one or more HTML pages with uniform metrics and source attribution."""
    if callable(page_urls):
        endpoints = (page_urls(url, page) for page in range(1, max_pages + 1))
    elif page_urls is not None:
        endpoints = iter(page_urls)
    else:
        endpoints = iter((url,))

    all_events = []
    for page, endpoint in enumerate(endpoints, 1):
        try:
            html = (fetcher or common.fetch_url)(endpoint, timeout=timeout)
            with common.capture_parser_metrics() as metrics:
                events = parser(html)
            expected_empty = (
                empty_is_healthy(html)
                if callable(empty_is_healthy)
                else empty_is_healthy
            )
            parser_empty = (
                not events
                and metrics["out_of_window_count"] == 0
                and not expected_empty
            )
            common._record_endpoint(
                endpoint,
                parser_type="html",
                candidate_count=metrics["candidate_count"],
                out_of_window_count=metrics["out_of_window_count"],
                parsed_event_count=len(events),
                parser_empty=parser_empty,
            )
            for event in events:
                if isinstance(event, dict) and not event.get("source_id"):
                    event["source_id"] = source_id
            if parser_empty:
                common.log_source_error(
                    name,
                    ParserEmptyError("parser returned no event records"),
                    source_id=source_id,
                )
            all_events.extend(events)
            if stop_when and stop_when(html, page):
                break
        except Exception as exc:
            common.log_source_error(
                name if page == 1 else f"{name} page {page}",
                exc,
                source_id=source_id,
            )
            if page == 1:
                break
    return all_events
