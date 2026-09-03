"""Fact-only early listing for Pützchens Markt from Tourismus NRW.

The public page is backed by the openly licensed Data Hub NRW. We deliberately
read only structured master data and publish neither its editorial prose nor
its images. The description is generated locally from the extracted facts.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from html import unescape

from .. import common
from ..early_publication import PUETZCHENS_MARKT_SOURCE_ID, PUETZCHENS_MARKT_URL

_OBJECT_ID = 11030
_SOURCE = "Tourismus NRW"
_DATE_RANGE = re.compile(
    r'<caption[^>]*>\s*Laufzeit\s*</caption>.*?'
    r'<td[^>]*>\s*(\d{2}\.\d{2}\.\d{4})\s*[-–—]\s*'
    r'(\d{2}\.\d{2}\.\d{4})\s*</td>',
    re.IGNORECASE | re.DOTALL,
)


def _event_nodes(html: str) -> list[dict]:
    nodes: list[dict] = []
    for raw in re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        try:
            value = json.loads(unescape(raw).strip())
        except (json.JSONDecodeError, TypeError):
            continue
        queue = [value]
        while queue:
            item = queue.pop()
            if isinstance(item, list):
                queue.extend(item)
            elif isinstance(item, dict):
                if item.get("@type") == "Event":
                    nodes.append(item)
                queue.extend(v for v in item.values() if isinstance(v, dict | list))
    return nodes


def _organizer_name(event: dict) -> str:
    organizer = event.get("organizer") or event.get("contributor") or {}
    if isinstance(organizer, list):
        organizer = next((item for item in organizer if isinstance(item, dict)), {})
    if not isinstance(organizer, dict):
        return ""
    return str(organizer.get("legalName") or organizer.get("name") or "").strip()


def events_from_html(html: str) -> list[dict]:
    event = next(
        (
            node for node in _event_nodes(html)
            if str(node.get("identifier") or "") == str(_OBJECT_ID)
            and str(node.get("name") or "").strip() == "Pützchens Markt"
        ),
        None,
    )
    if not event or _organizer_name(event) != "Bundesstadt Bonn":
        return []

    date_range = _DATE_RANGE.search(html)
    if not date_range:
        return []
    start = datetime.strptime(date_range.group(1), "%d.%m.%Y")
    end = datetime.strptime(date_range.group(2), "%d.%m.%Y")
    if end < start:
        return []

    is_free = str(event.get("isAccessibleForFree") or "").rsplit("/", 1)[-1].casefold() == "true"
    date_label = (
        f"vom {start.strftime('%d.%m.%Y')} bis {end.strftime('%d.%m.%Y')}"
        if end != start else f"am {start.strftime('%d.%m.%Y')}"
    )
    description = f"Pützchens Markt ist für den Zeitraum {date_label} in Bonn-Pützchen angekündigt."
    description += " Veranstalterin ist die Bundesstadt Bonn."
    if is_free:
        description += " Der Eintritt ist laut Tourismus NRW kostenlos."

    result = common.make_event(
        "Pützchens Markt", start, end, "Marktwiesen in Pützchen", "Bonn-Pützchen",
        description, PUETZCHENS_MARKT_URL, _SOURCE,
        "Stadtfest Kirmes Jahrmarkt Brauchtum", trust=0.95,
        source_id=PUETZCHENS_MARKT_SOURCE_ID, description_source="generated",
        default_category_key="festival", category_locked=True, link_kind="detail",
    )
    if not result:
        return []
    result["organizer"] = "Bundesstadt Bonn"
    result["early_publication"] = True
    if is_free:
        result["price"] = "kostenlos"
        result["admission_basis"] = "explicit"
    return [result]


def fetch() -> list[dict]:
    return events_from_html(common.fetch_url(PUETZCHENS_MARKT_URL, timeout=20))
