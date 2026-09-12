"""Pure semantic HTML and JSON-LD extraction shared by detail adapters."""
from __future__ import annotations

import os
import re
import unicodedata
from datetime import datetime
from html import escape
from html.parser import HTMLParser
from typing import TypedDict
from zoneinfo import ZoneInfo

from . import common, richtext
from .models import RawEvent

_CONTENT_TOKENS = {
    "article-content", "content-detail", "detail-content", "entry-content",
    "event-content", "event-description", "event-details", "event-text",
    "eventdetail", "eventdescription", "events_page_detail",
    "rich-text", "shapehub-detail-description", "tx-gbevents-pi1", "va-content",
    "veranstaltungsbeschreibung", "veranstaltungsdetails",
}


_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}

def _attributes(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
    return {name.casefold(): value or "" for name, value in attrs}


def _attribute_tokens(attrs: dict[str, str]) -> set[str]:
    return {
        token.casefold()
        for value in (attrs.get("class", ""), attrs.get("id", ""))
        for token in re.split(r"[^a-zA-Z0-9_-]+", value)
        if token
    }


def _is_event_type(value: str) -> bool:
    return bool(re.search(r"(?:schema.org/)?[A-Za-z]*Event\b", value or "", re.I))


class _Capture(TypedDict):
    tag: str
    depth: int
    score: int
    parts: list[str]


class _SemanticHTML(HTMLParser):
    """Collect high-confidence event fragments and machine-readable values."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.captures: list[_Capture] = []
        self.active: list[_Capture] = []
        self.meta: dict[str, str] = {}
        self.item_values: dict[str, list[str]] = {}
        self._item_stack: list[tuple[str, list[str]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = _attributes(attrs)
        tokens = _attribute_tokens(attr)
        itemprop = attr.get("itemprop", "").casefold()
        for capture in self.active:
            capture["parts"].append(self.get_starttag_text() or f"<{tag}>")
            if tag not in _VOID_TAGS:
                capture["depth"] = int(capture["depth"]) + 1

        # HTMLParser does not emit an end tag for HTML void elements.  Treating
        # them like containers makes a description meta tag or image swallow
        # the rest of the document and also corrupts every outer depth count.
        if tag in _VOID_TAGS:
            if itemprop and attr.get("content"):
                self.item_values.setdefault(itemprop, []).append(attr["content"])
            if tag == "meta":
                key = (attr.get("property") or attr.get("name") or "").casefold()
                if key and attr.get("content"):
                    self.meta[key] = attr["content"]
            return

        score = 0
        if itemprop in {"description", "articlebody"}:
            score = 100
        elif tokens & _CONTENT_TOKENS:
            score = 80
        elif _is_event_type(attr.get("itemtype", "")) and tag in {"main", "article", "section", "div"}:
            score = 70
        if score:
            new_capture: _Capture = {"tag": tag, "depth": 1, "score": score, "parts": []}
            self.captures.append(new_capture)
            self.active.append(new_capture)
            new_capture["parts"].append(self.get_starttag_text() or f"<{tag}>")

        if itemprop:
            content = attr.get("content", "")
            if content:
                self.item_values.setdefault(itemprop, []).append(content)
            self._item_stack.append((itemprop, []))
        else:
            self._item_stack.append(("", []))

        if tag == "meta":
            key = (attr.get("property") or attr.get("name") or "").casefold()
            if key and attr.get("content"):
                self.meta[key] = attr["content"]

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        for capture in self.active:
            capture["parts"].append(escape(data, quote=False))
        for itemprop, parts in self._item_stack:
            if itemprop:
                parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        for capture in list(self.active):
            capture["parts"].append(f"</{tag}>")
            capture["depth"] = int(capture["depth"]) - 1
            if capture["depth"] == 0:
                self.active.remove(capture)
        if self._item_stack:
            itemprop, parts = self._item_stack.pop()
            if itemprop:
                value = common.clean_html(" ".join(parts))
                if value:
                    self.item_values.setdefault(itemprop, []).append(value)


def _jsonld_candidates(document: str, title: str) -> list[dict]:
    candidates = common.jsonld_event_items(document)
    if not candidates:
        return []
    title_key = re.sub(r"[^a-z0-9]+", "", title.casefold())

    def similarity(item: dict) -> tuple[int, int]:
        item_key = re.sub(r"[^a-z0-9]+", "", common.clean_html(str(item.get("name") or "")).casefold())
        shared = os.path.commonprefix([title_key, item_key])
        return (len(shared), len(str(item.get("description") or "")))

    return sorted((item for item in candidates if isinstance(item, dict)), key=similarity, reverse=True)


def _exact_title_key(value: object) -> str:
    normalized = unicodedata.normalize(
        "NFC", common.clean_html(str(value or "")),
    ).lower()
    normalized = unicodedata.normalize("NFC", normalized)
    # Casefolding and fuzzy transliteration collapse distinct letters such as
    # ß/ss and í/i, which is unsafe when selecting authoritative event copy.
    # Lowercasing can expand a letter into a base plus a combining mark, as for
    # İ. Keep attached marks so distinct Unicode titles cannot collide.
    key: list[str] = []
    accepts_mark = False
    for character in normalized:
        if character.isalnum():
            key.append(character)
            accepts_mark = True
        elif accepts_mark and unicodedata.category(character).startswith("M"):
            key.append(character)
        else:
            accepts_mark = False
    return "".join(key)


def _exact_jsonld_description(document: str, event: RawEvent) -> tuple[str, str]:
    """Return copy only when structured title and occurrence date match exactly."""

    expected_title_key = _exact_title_key(event.get("title"))
    event_date = str(event.get("start_date") or event.get("date") or "")[:10]
    if not expected_title_key or not event_date:
        return "", ""
    for item in common.jsonld_event_items(document or ""):
        if (
            _exact_title_key(item.get("name")) != expected_title_key
            or str(item.get("startDate") or "")[:10] != event_date
        ):
            continue
        raw_description = item.get("description")
        if not isinstance(raw_description, str) or not raw_description.strip():
            continue
        description_html = richtext.sanitize_rich_text(raw_description)
        description = richtext.to_plain_text(description_html)
        if description:
            return description, description_html
    return "", ""


_PROSE_TIME_RANGE = re.compile(
    r"\b(?:von\s+)?([01]?\d|2[0-3]):([0-5]\d)\s*(?:uhr\s*)?"
    r"(?:bis|[-–])\s*([01]?\d|2[0-3]):([0-5]\d)\s*(?:uhr)?\b",
    re.IGNORECASE,
)


def _single_prose_time_range(value: str) -> tuple[str, str] | None:
    """Read one unambiguous visible clock range from first-party event copy."""
    ranges = {
        (f"{int(match.group(1)):02d}:{match.group(2)}", f"{int(match.group(3)):02d}:{match.group(4)}")
        for match in _PROSE_TIME_RANGE.finditer(common.clean_html(value or ""))
    }
    return next(iter(ranges)) if len(ranges) == 1 else None


def _timestamp_with_timezone(value: str, timezone_name: str) -> str:
    """Attach the event's declared zone when a source emits a local ISO timestamp."""
    cleaned = re.sub(r"\[[^]]+\]$", "", value.strip())
    if not cleaned:
        return ""
    try:
        parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except ValueError:
        return cleaned
    if parsed.tzinfo is not None:
        return cleaned
    return parsed.replace(tzinfo=ZoneInfo(timezone_name)).isoformat()


def _timestamp_with_clock(
    value: str,
    date_value: str,
    clock: str,
    timezone_name: str,
) -> str:
    """Replace a structured clock and resolve its offset from the event zone."""
    if value:
        replaced = re.sub(
            r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}",
            f"{date_value}T{clock}",
            value,
        )
        cleaned = re.sub(r"\[[^]]+\]$", "", replaced.strip())
        try:
            parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
        except ValueError:
            return _timestamp_with_timezone(replaced, timezone_name)
        zone = ZoneInfo(timezone_name)
        wall_time = parsed.replace(tzinfo=None)
        candidates = (
            wall_time.replace(tzinfo=zone, fold=0),
            wall_time.replace(tzinfo=zone, fold=1),
        )
        if parsed.tzinfo is not None:
            for candidate in candidates:
                if candidate.utcoffset() == parsed.utcoffset():
                    return candidate.isoformat()
        return candidates[0].isoformat()
    if not date_value:
        return ""
    return datetime.fromisoformat(f"{date_value}T{clock}").replace(
        tzinfo=ZoneInfo(timezone_name),
    ).isoformat()


def _best_description(document: str, parser: _SemanticHTML, title: str) -> tuple[str, str]:
    choices: list[tuple[int, int, str]] = []
    for capture in parser.captures:
        fragment = "".join(capture["parts"])
        if re.search(r'class=["\'][^"\']*\bva-content\b', fragment, re.I):
            # Arp Museum nests its share/calendar controls inside va-content.
            # They are page furniture, while the preceding paragraphs are the
            # complete editorial event copy.
            fragment = re.split(
                r'<div[^>]+class=["\'][^"\']*\bva-content-cta\b',
                fragment,
                maxsplit=1,
                flags=re.I,
            )[0]
            fragment = re.sub(r"<figure\b.*?</figure>", "", fragment, flags=re.I | re.S)
        sanitized = richtext.sanitize_rich_text(fragment)
        plain = richtext.to_plain_text(sanitized)
        if plain and common.clean_html(title).casefold() != plain.casefold():
            choices.append((int(capture["score"]), len(plain), sanitized))
    for item in _jsonld_candidates(document, title):
        description = item.get("description")
        if isinstance(description, str) and description.strip():
            sanitized = richtext.sanitize_rich_text(description)
            choices.append((65, richtext.text_length(sanitized), sanitized))
    meta_description = parser.meta.get("og:description") or parser.meta.get("description") or ""
    if meta_description:
        sanitized = richtext.from_plain_text(common.clean_html(meta_description))
        choices.append((25, richtext.text_length(sanitized), sanitized))
    if not choices:
        return "", ""
    # Confidence wins before length: a huge event-root container must not beat
    # an explicit itemprop=description merely by including page furniture.
    _, _, html = max(choices, key=lambda choice: (choice[0], choice[1]))
    html = _append_supplemental_details(document, html)
    return richtext.to_plain_text(html), html


def _append_supplemental_details(document: str, description_html: str) -> str:
    """Keep event facts that municipal templates place beside the prose.

    Köln's official detail pages are the first concrete contract: registration
    and age are siblings of ``itemprop=description``, not children of it.  The
    patterns are intentionally label-bound and therefore cannot absorb generic
    navigation or contact furniture.
    """
    additions: list[str] = []
    for heading, pattern in (
        ("Hinweis", r'<span[^>]+itemprop=["\']age["\'][^>]*>(.*?)</span>'),
        ("Anmeldung", r'<strong>\s*Anmeldung:\s*</strong>.*?<span[^>]*>(.*?)</span>'),
    ):
        match = re.search(pattern, document or "", re.I | re.S)
        value = common.clean_html(match.group(1)) if match else ""
        if value and value.casefold() not in richtext.to_plain_text(description_html).casefold():
            additions.append(f"<h3>{heading}</h3><p>{escape(value, quote=False)}</p>")
    return description_html + "".join(additions)


def _first(values: dict[str, list[str]], *names: str) -> str:
    for name in names:
        for value in values.get(name.casefold(), []):
            cleaned = common.clean_html(value)
            if cleaned:
                return cleaned
    return ""


def _visible_labeled_value(document: str, *labels: str) -> str:
    """Read a short value following an explicit, visible field label.

    Several otherwise well-structured calendars omit schema.org admission and
    address fields.  Label-bound extraction keeps this conservative: arbitrary
    currency-like page text (for example vendor fees or related events) is not
    promoted.
    """
    label_pattern = "|".join(re.escape(label) for label in labels)
    match = re.search(
        rf"<(?:b|strong)[^>]*>\s*(?:{label_pattern})\s*:?\s*</(?:b|strong)>"
        rf"\s*(?:<br\s*/?>\s*)?(.*?)(?=<br\s*/?>|</p>|</div>|</li>|</td>)",
        document or "",
        re.I | re.S,
    )
    return common.clean_html(match.group(1)).lstrip(" :–-")[:240] if match else ""


def _tribe_price(document: str) -> str:
    """Extract The Events Calendar's visitor-facing event cost."""
    tribe_cost = re.search(
        r'<(?P<tag>[a-z0-9]+)[^>]+class=["\'][^"\']*'
        r'(?:tribe-events-cost|tribe-events-event-cost)(?=\s|["\'])'
        r'[^"\']*["\'][^>]*>(?P<value>.*?)</(?P=tag)>',
        document or "",
        re.I | re.S,
    )
    if tribe_cost:
        price = common.clean_html(tribe_cost.group("value"))
        if price:
            return price[:240]
    return ""


def _product_meta_price(parser: _SemanticHTML) -> str:
    """Keep the currency paired with Open Graph product price metadata."""
    amount = common.clean_html(parser.meta.get("product:price:amount", "")).strip()
    currency = common.clean_html(parser.meta.get("product:price:currency", "")).strip()
    if not amount or not currency:
        return ""
    return f"{amount} {currency}"[:240]

