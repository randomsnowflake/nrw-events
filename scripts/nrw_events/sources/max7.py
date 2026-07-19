"""Public dance-party dates from Tanzschule Max7's official calendar."""

import re
import urllib.parse

from .. import common
from . import regional_common as rc


URL = "https://www.max7.de/tanzkurse-bonn?view=kurse&tab=2&task=display&day=0"
SOURCE = "Tanzschule Max7"

_GROUP_RE = re.compile(
    r"<div[^>]+class=['\"]workshop-group-header[^'\"]*['\"][^>]*>(?P<date>.*?)</div>\s*"
    r"<div[^>]+class=['\"]workshop-group-header-2[^'\"]*['\"][^>]*>(?P<venue>.*?)</div>\s*"
    r"(?P<body>.*?)(?=<div[^>]+class=['\"]workshop-group-header[^'\"]*['\"]|\Z)",
    re.S | re.I,
)
_ITEM_RE = re.compile(
    r"<div[^>]+class=['\"](?P<classes>workshop_or_party[^'\"]*)['\"][^>]*>(?P<body>.*?)"
    r"(?=<div[^>]+class=['\"]workshop_or_party|\Z)",
    re.S | re.I,
)


def _nightlife(event: dict) -> dict:
    return {
        **event,
        "category_key": "nightlife",
        "category_label": "Nachtleben & Party",
        "category_confidence": 0.99,
        "category_reason": "source:Tanzschule Max7; party-only listing record",
    }


def _detail_fields(html: str) -> tuple[str, str]:
    body = re.search(
        r"<h2[^>]*>\s*Beschreibung\s*</h2>(.*?)(?=<div class=['\"]col-lg-6['\"]|</main>)",
        html or "",
        re.S | re.I,
    )
    description = common.concise_description(body.group(1)) if body else ""
    price_match = re.search(
        r"Partyeintritt\s*(\d+(?:[,.]\d+)?)\s*€.*?(\d+(?:[,.]\d+)?)\s*€\s*Verzehr",
        common.clean_html(body.group(1) if body else ""),
        re.I,
    )
    price = ""
    if price_match:
        admission, consumption = (value.replace(",", ".") for value in price_match.groups())
        price = f"{admission.rstrip('0').rstrip('.')} € + {consumption.rstrip('0').rstrip('.')} € Verzehr"
    return description, price


def _browser_safe_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((
        parts.scheme,
        parts.netloc,
        urllib.parse.quote(parts.path, safe="/,:-"),
        parts.query,
        parts.fragment,
    ))


def _events_from_listing(html: str, detail_loader=None) -> list:
    events = []
    details: dict[str, tuple[str, str]] = {}
    for group in _GROUP_RE.finditer(html or ""):
        for match in _ITEM_RE.finditer(group.group("body")):
            if "party" not in match.group("classes").casefold().split():
                continue
            body = match.group("body")
            href_match = re.search(r"<a[^>]+href=['\"]([^'\"]+)['\"]", body, re.I)
            title_match = re.search(r"class=['\"][^'\"]*\bline3\b[^'\"]*['\"][^>]*>(.*?)</div>", body, re.S | re.I)
            if not (href_match and title_match):
                continue

            start = common.parse_date(rc.clean(group.group("date")))
            party_line = rc.clean(title_match.group(1))
            time_match = re.search(r"\b(\d{1,2}:\d{2})\b", party_line)
            time_text = time_match.group(1) if time_match else ""
            if start and time_match:
                hour, minute = (int(value) for value in time_text.split(":"))
                start = start.replace(hour=hour, minute=minute)
            title = re.sub(r"^\s*(?:ab\s*)?\d{1,2}:\d{2}\s*", "", party_line, flags=re.I).strip()
            venue = rc.clean(group.group("venue"))
            link = _browser_safe_url(rc.abs_url(URL, href_match.group(1)))
            detail_url = urllib.parse.urlunsplit((*urllib.parse.urlsplit(link)[:3], "", ""))
            if detail_loader and common.window_contains(start) and detail_url not in details:
                details[detail_url] = _detail_fields(detail_loader(detail_url))
            description, price = details.get(detail_url, ("", ""))
            description = description or common.factual_event_description(
                title, date_value=start, time_text=time_text, venue=venue, city="Bonn"
            )
            event = common.make_event(
                title, start, None, venue, "Bonn", description, link, SOURCE,
                "party nightlife salsa bachata discofox", 0.98, time_text,
                source_id="max7",
            )
            if event:
                if price:
                    event["price"] = price
                events.append(_nightlife(event))
    return rc.dedupe(events)


def fetch() -> list:
    try:
        html = common.fetch_url(URL, timeout=25)

        def load_detail(url: str) -> str:
            try:
                return common.fetch_detail_url(url, cache_namespace="max7", timeout=20)
            except Exception as exc:
                common.log_source_error(f"{SOURCE} detail", exc)
                return ""

        with common.capture_parser_metrics() as metrics:
            events = _events_from_listing(html, load_detail)
        parser_empty = not events and metrics["out_of_window_count"] == 0
        common._record_endpoint(
            URL, parser_type="html", candidate_count=metrics["candidate_count"],
            out_of_window_count=metrics["out_of_window_count"],
            parsed_event_count=len(events), parser_empty=parser_empty,
        )
        if parser_empty:
            common.log_source_error(SOURCE, rc.ParserEmptyError("parser returned no party records"))
        return events
    except Exception as exc:
        common.log_source_error(SOURCE, exc)
        return []
