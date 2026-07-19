"""University of Bonn — official public event calendar.

Reads the calendar's first-party iCal export for complete scheduling and
classification data, then enriches in-window events from their detail pages.
The Plone iCal feed deliberately omits locations, while the detail pages expose
venue, room, and price fields. Detail responses use the shared persistent TTL
cache and failures degrade to the still-useful iCal record.
"""

from datetime import timedelta
from html.parser import HTMLParser
import re

from .. import category_taxonomy, common
from . import regional_common as rc

_ICAL_URL = "https://www.uni-bonn.de/de/veranstaltungen?ical_download=1"
_SOURCE = "Universität Bonn"
_SOURCE_ID = "uni-bonn"
_CACHE_NAMESPACE = "uni-bonn-detail"
_MAX_DURATION = timedelta(days=366 * 5)
_ORDINARY_EVENT_MAX_DURATION = timedelta(days=31)
_LONG_RUNNING_RE = re.compile(r"\b(?:ausstellung|exhibition|museum|kunstkammer)\b", re.I)
_VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
})


class _ContentItemParser(HTMLParser):
    """Collect label/value pairs from Plone's event ``content-item`` blocks."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.fields: dict[str, str] = {}
        self._item_depth = 0
        self._capture = ""
        self._capture_depth = 0
        self._parts = {"label": [], "value": []}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = set((dict(attrs).get("class") or "").split())
        if tag == "div" and "content-item" in classes and not self._item_depth:
            self._item_depth = 1
            self._capture = ""
            self._parts = {"label": [], "value": []}
            return
        if not self._item_depth:
            return
        if tag not in _VOID_TAGS:
            self._item_depth += 1
        if tag == "div" and "item-title" in classes:
            self._capture = "label"
            self._capture_depth = self._item_depth
        elif tag == "div" and "item-value" in classes:
            self._capture = "value"
            self._capture_depth = self._item_depth
        elif tag == "br" and self._capture:
            self._parts[self._capture].append(" ")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "br" and self._capture:
            self._parts[self._capture].append(" ")

    def handle_endtag(self, tag: str) -> None:
        if not self._item_depth or tag in _VOID_TAGS:
            return
        if self._capture and tag == "div" and self._item_depth == self._capture_depth:
            self._capture = ""
            self._capture_depth = 0
        self._item_depth -= 1
        if self._item_depth:
            return
        label = common.clean_html(" ".join(self._parts["label"])).rstrip(":").casefold()
        value = common.clean_html(" ".join(self._parts["value"]))
        if label and value:
            self.fields[label] = value

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._parts[self._capture].append(data)


def _parse_detail_context(html: str, _event: dict | None = None) -> dict:
    parser = _ContentItemParser()
    parser.feed(html or "")
    venue_parts = []
    for part in (parser.fields.get("ort", ""), parser.fields.get("raum", "")):
        key = re.sub(r"\s*\([^)]*\)", "", part).strip(" ,").casefold()
        existing_keys = {
            re.sub(r"\s*\([^)]*\)", "", value).strip(" ,").casefold()
            for value in venue_parts
        }
        if part and key not in existing_keys:
            venue_parts.append(part)
    return {
        "venue": ", ".join(part for part in venue_parts if part),
        "price": parser.fields.get("eintrittspreis", "") or parser.fields.get("preis", ""),
    }


def _valid_duration(props: dict, start, end) -> bool:
    """Reject visibly corrupt centuries-long Plone end dates without hiding exhibitions."""
    if not start <= end <= start + _MAX_DURATION:
        return False
    if end <= start + _ORDINARY_EVENT_MAX_DURATION:
        return True
    text = f"{props.get('SUMMARY', '')} {props.get('DESCRIPTION', '')}"
    return bool(_LONG_RUNNING_RE.search(text))


def _merge_context(event: dict, context: dict) -> dict:
    enriched = dict(event)
    if context.get("venue"):
        enriched["venue"] = context["venue"]
    if context.get("price"):
        raw_price = context["price"][:160]
        inferred_price = common.infer_free_admission_price(
            enriched.get("title", ""), enriched.get("description", ""), context["price"],
        )
        paid_amounts = [
            float(value.replace(",", "."))
            for value in re.findall(r"(\d+(?:[,.]\d+)?)\s*(?:€|Euro\b)", raw_price, re.I)
        ]
        enriched["price"] = (
            raw_price if inferred_price == "kostenlos" and any(amount > 0 for amount in paid_amounts)
            else inferred_price or raw_price
        )
    return enriched


def _enrich_details(events: list, detail_fetcher=None) -> list:
    return rc.enrich_descriptions(
        events,
        source=f"{_SOURCE} detail",
        cache_namespace=_CACHE_NAMESPACE,
        extract_context=_parse_detail_context,
        fallback=lambda event: event.get("description", ""),
        timeout=15,
        detail_fetcher=detail_fetcher,
        needs_enrichment=lambda event: not event.get("venue"),
        merge_context=_merge_context,
    )


def _correct_categories(events: list) -> list:
    for event in events:
        category_text = event.get("category", "").casefold()
        event_text = f"{event.get('title', '')} {event.get('description', '')}"
        key = ""
        if "welcome days" in category_text:
            key = "talk"
        elif re.search(r"\b(?:ausstellung|exhibition)\b", event_text, re.I):
            key = "exhibition"
        if key:
            category = category_taxonomy.CATEGORY_BY_KEY[key]
            event["category_key"] = category["key"]
            event["category_label"] = category["label"]
            event["category_confidence"] = 1.0
            event["category_reason"] = f"source:{key}"
    return events


def fetch() -> list:
    try:
        events = common.fetch_ical(
            _ICAL_URL,
            _SOURCE,
            "Bonn",
            trust=1.0,
            source_id=_SOURCE_ID,
            event_filter=_valid_duration,
        )
        return _correct_categories(_enrich_details(events))
    except Exception as exc:
        common.log_source_error(_SOURCE, exc, source_id=_SOURCE_ID)
        return []
