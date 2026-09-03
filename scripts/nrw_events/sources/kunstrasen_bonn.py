"""First-party KUNST!RASEN Bonn dates from the public ticket shop."""

from __future__ import annotations

import json
import re
import urllib.parse

from .. import common
from . import regional_common as rc

URL = "https://tickets.kunstrasen-bonn.de/"
SOURCE = "KUNST!RASEN Bonn"


def _page_data(html: str) -> dict:
    match = re.search(r"wlec\.pageData\s*=\s*(\{.*?\})\s*;\s*</script>", html or "", re.S)
    if not match:
        return {}
    try:
        return json.loads(match.group(1))
    except ValueError:
        return {}


def _tour_items(payload: dict) -> list[dict]:
    groups = payload.get("tourTeasers") or []
    for group in groups if isinstance(groups, list) else []:
        if not isinstance(group, dict):
            continue
        if "aktuelle veranstaltungen" in common.clean_html(str(group.get("name") or "")).casefold():
            return [item for item in (group.get("tours") or []) if isinstance(item, dict)]
    return []


def _detail_time(html: str, date_value: str) -> str:
    anchors = re.findall(r'<a[^>]+data-eventdate=["\']' + re.escape(date_value) + r'["\'][^>]*>', html or "", re.I)
    for anchor in anchors:
        label_match = re.search(r'aria-label=["\']([^"\']+)["\']', anchor, re.I)
        time_match = re.search(r"\b([0-2]\d:[0-5]\d)\s*Uhr\b", label_match.group(1) if label_match else "", re.I)
        if time_match:
            return time_match.group(1)
    return ""


def _events_from_listing(html: str, *, detail_fetcher=None) -> list:
    detail_fetcher = detail_fetcher or (lambda link: common.fetch_detail_url(link, cache_namespace="kunstrasen-bonn", timeout=15))
    events = []
    for item in _tour_items(_page_data(html)):
        href = str(item.get("url") or "").strip()
        if not href or urllib.parse.urlsplit(href).netloc:
            continue
        artist_html = str(item.get("artist") or "")
        artist = common.clean_html(re.split(r"</?br\s*/?>", artist_html, maxsplit=1, flags=re.I)[0])
        title = artist or common.clean_html(str(item.get("title") or ""))
        date_match = re.search(r"\b(\d{2}\.\d{2}\.20\d{2})\b", common.clean_html(artist_html))
        start = common.parse_date(date_match.group(1)) if date_match else None
        if not title or not start:
            continue
        link = urllib.parse.urljoin(URL, href)
        time_text = ""
        if common.event_in_window_and_radius(start, start, "Bonn"):
            try:
                time_text = _detail_time(detail_fetcher(link), start.strftime("%Y-%m-%d"))
            except Exception as exc:
                common.log_source_error(f"{SOURCE} detail", exc)
        if time_text:
            hour, minute = map(int, time_text.split(":"))
            start = start.replace(hour=hour, minute=minute)
        tour_title = common.clean_html(str(item.get("title") or ""))
        description = common.concise_description(tour_title) or common.factual_event_description(title, date_value=start, time_text=time_text, venue="KUNST!RASEN Bonn", city="Bonn")
        default_category = "festival" if "festival" in title.casefold() else "concert"
        event = common.make_event(title, start, start, "KUNST!RASEN Bonn", "Bonn", description, link, SOURCE, "open air concert festival live music", 1.0, source_id="kunstrasen-bonn", description_source="scraped" if tour_title else "generated", default_category_key=default_category, category_locked=True)
        if not event:
            continue
        price = item.get("minprice")
        if price not in (None, ""):
            event["price"] = f"ab {common.parse_float(price):g} €"
            event["admission_basis"] = "explicit"
        events.append(event)
    return rc.dedupe(events)


def fetch() -> list:
    try:
        html = common.fetch_url(URL, timeout=25)
        with common.capture_parser_metrics() as metrics:
            events = _events_from_listing(html)
        parser_empty = not events and metrics["out_of_window_count"] == 0
        common._record_endpoint(URL, parser_type="page-data-json", candidate_count=metrics["candidate_count"], out_of_window_count=metrics["out_of_window_count"], parsed_event_count=len(events), parser_empty=parser_empty)
        if parser_empty:
            common.log_source_error(SOURCE, rc.ParserEmptyError("parser returned no event records"))
        return events
    except Exception as exc:
        common.log_source_error(SOURCE, exc)
        return []
