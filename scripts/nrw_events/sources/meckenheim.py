"""
Meckenheim — Voreifel town calendar (~13 km SW of Bonn).

Reads:  meckenheim.de event listing and first-party detail pages (Govconnect
        municipal CMS). The listing provides titles and dates; detail pages add
        descriptions, time ranges, venues, localities, and prices.
Yields: small local happenings — weekly market, guided hikes, book flea markets,
        nature days, town tours — the kind of thing aggregators never see.

Detail responses use a persistent TTL cache so frequent importer runs do not
re-request unchanged pages. Fails soft (returns listing-only events) if the
detail markup or an individual request fails.
"""

import re
from datetime import timedelta
from html import unescape

from .. import common

_URL = "https://www.meckenheim.de/Leben-in-Meckenheim/Veranstaltungen/"
_BASE = "https://www.meckenheim.de"
_TITLE = r'result-list_object-title[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>'
_CATEGORY = "lokal veranstaltung markt kultur outdoor"
_TRUST = 0.9
def _reset_detail_context_cache() -> None:
    """Compatibility hook for isolated tests using the shared raw-page cache."""
    common._reset_detail_page_cache("meckenheim-detail")


def _field_values(html: str) -> dict:
    values = {}
    for label_html, value_html in re.findall(
        r'<dt[^>]+class="[^"]*object-data_field[^"]*"[^>]*>(.*?)</dt>\s*'
        r'<dd[^>]+class="[^"]*object-data_value[^"]*"[^>]*>(.*?)</dd>',
        html or "", re.S | re.I,
    ):
        label = common.clean_html(label_html).rstrip(":").casefold()
        values[label] = value_html
    return values


def _detail_description(html: str) -> str:
    meta = re.search(
        r'<meta\s+name="description"\s+content="([^"]*)"', html or "", re.I,
    )
    if meta and common.clean_html(unescape(meta.group(1))):
        return common.concise_description(unescape(meta.group(1)))

    after_data = (html or "").split("</dl>", 1)
    if len(after_data) < 2:
        return ""
    main_tail = after_data[1].split('<div class="randspalte', 1)[0]
    paragraphs = []
    for raw in re.findall(r"<p\b[^>]*>(.*?)</p>", main_tail, re.S | re.I):
        text = common.clean_html(raw)
        if len(text) >= 40 and not re.search(r"\b(?:Karte anzeigen|Termin exportieren|Kontakt:)\b", text, re.I):
            paragraphs.append(text)
        if len(paragraphs) >= 2:
            break
    return common.concise_description(" ".join(paragraphs))


def _detail_venue(raw: str) -> str:
    heading = re.search(r'result-list_object-title[^>]*>\s*<a[^>]*>(.*?)</a>', raw or "", re.S | re.I)
    value = heading.group(1) if heading else raw
    value = re.sub(r'<span[^>]+class="[^"]*sr-only[^"]*"[^>]*>.*?</span>', " ", value, flags=re.S | re.I)
    return common.clean_html(value)


def _parse_detail_context(html: str) -> dict:
    fields = _field_values(html)
    return {
        "description": _detail_description(html),
        "venue": _detail_venue(fields.get("veranstaltungsort", "")),
        "city": common.clean_html(fields.get("ortschaft", "")),
        "time": common.sanitize_time_text(common.clean_html(fields.get("uhrzeit", ""))),
        "price": common.clean_html(fields.get("preis", ""))[:160],
    }


def _fetch_detail_context(link: str) -> dict:
    try:
        return _parse_detail_context(common.fetch_detail_url(
            link, cache_namespace="meckenheim-detail", timeout=20,
        ))
    except Exception as exc:
        common.log_source_error("Meckenheim detail", exc)
        return {}


def _event_datetimes(event: dict, time_text: str) -> tuple:
    start = common.parse_iso_date(event.get("start_date", ""))
    if not start:
        return None, None
    times = re.findall(r"\b(\d{1,2}):(\d{2})\b", time_text or event.get("time", ""))
    if times:
        start = start.replace(hour=int(times[0][0]), minute=int(times[0][1]))
    end = start
    if len(times) >= 2:
        end = start.replace(hour=int(times[1][0]), minute=int(times[1][1]))
        if end < start:
            end += timedelta(days=1)
    return start, end


def _enrich_event(event: dict) -> dict:
    if not common.event_in_window(event):
        return event
    context = _fetch_detail_context(event.get("link", ""))
    if not context:
        return event
    time_text = context.get("time", "") or event.get("time", "")
    start, end = _event_datetimes(event, time_text)
    enriched = common.make_event(
        event.get("title", ""), start, end,
        context.get("venue", "") or event.get("venue", ""),
        context.get("city", "") or event.get("city", "Meckenheim"),
        context.get("description", "") or event.get("description", ""),
        event.get("link", ""), "Meckenheim", _CATEGORY, _TRUST,
        time_text=time_text,
    )
    if not enriched:
        return event
    price = context.get("price", "")
    if price:
        enriched["price"] = common.infer_free_admission_price(
            enriched["title"], enriched["description"], price,
        ) or price
    return enriched


def fetch() -> list:
    source = "Meckenheim"
    try:
        html = common.fetch_url(_URL, timeout=20)
        events = common.events_from_time_listing(
            html, source, "Meckenheim", _CATEGORY, _TRUST,
            _BASE, min_title=3, max_chars=1500, anchor_pattern=_TITLE)
        return [_enrich_event(event) for event in events]
    except Exception as e:
        common.log_source_error(source, e)
        return []
