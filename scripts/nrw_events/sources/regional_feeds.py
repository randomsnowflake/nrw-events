"""Structured regional feeds that are not large enough for dedicated modules."""

import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from .. import common
from . import regional_common as rc

_SANKT_AUGUSTIN_URL = "https://www.sankt-augustin.de/kultur-freizeit/veranstaltungsuebersicht/"
_NEUNKIRCHEN_ICAL = "https://termine.wir-nkse.de/termine/liste/?ical=1"
_UNKEL_RSS = "https://rhein.info/?post_type=event&feed=eventical"
_UNKEL_EVENTS_URL = "https://rhein.info/unkel/"


def fetch() -> list:
    events = []
    events.extend(_fetch_sankt_augustin())
    events.extend(_fetch_neunkirchen_seelscheid())
    events.extend(_fetch_unkel_rss())
    return rc.dedupe([ev for ev in events if "abgesagt" not in ev["title"].lower()])


def _fetch_sankt_augustin() -> list:
    try:
        html = common.fetch_url(_SANKT_AUGUSTIN_URL, timeout=25)
        return common.events_from_jsonld(
            html,
            "Sankt Augustin",
            "Sankt Augustin",
            "sankt augustin lokal kultur markt open air",
            0.95,
            _SANKT_AUGUSTIN_URL,
        )
    except Exception as e:
        common.log_source_error("Sankt Augustin", e)
        return []


def _fetch_neunkirchen_seelscheid() -> list:
    try:
        return common.fetch_ical(
            _NEUNKIRCHEN_ICAL,
            "Neunkirchen-Seelscheid",
            "Neunkirchen-Seelscheid",
            "neunkirchen-seelscheid lokal markt kultur",
            0.9,
        )
    except Exception as e:
        common.log_source_error("Neunkirchen-Seelscheid", e)
        return []


def _fetch_unkel_rss() -> list:
    try:
        root = ET.fromstring(common.fetch_url(
            _UNKEL_RSS,
            timeout=25,
            accept="application/rss+xml,application/xml;q=0.9,*/*;q=0.8",
            sec_fetch_mode="no-cors",
            sec_fetch_dest="empty",
        ))
    except Exception as e:
        common.log_source_error("VG Unkel", e)
        return []

    events = []
    for item in root.findall(".//item"):
        ev = _event_from_unkel_item(item)
        if ev:
            events.append(ev)
    return events


def _event_from_unkel_item(item):
    title = item.findtext("title") or ""
    link = _UNKEL_EVENTS_URL
    desc = item.findtext("description") or ""
    text = rc.clean(desc)
    if not any(place in f"{title} {text} {link}".lower()
               for place in ("unkel", "rheinbreitbach", "bruchhausen", "erpel")):
        return None

    start = rc.parse_dt(text)
    if not start and item.findtext("pubDate"):
        start = parsedate_to_datetime(item.findtext("pubDate")).replace(tzinfo=None)
    lines = [part.strip() for part in re.split(r"<br\s*/?>", desc) if rc.clean(part)]
    venue = rc.clean(lines[1]) if len(lines) > 1 else ""
    time_text = rc.time_text(text)
    if time_text in {"0:00", "00:00"}:
        time_text = ""
    description = _unkel_description(title, start, lines, time_text)
    return common.make_event(
        title,
        rc.with_time(start, text),
        None,
        venue,
        rc.city_from_text(text, "Unkel"),
        description,
        link,
        "VG Unkel",
        "unkel mittelrhein kultur konzert markt",
        0.86,
        time_text,
    )


def _unkel_description(title: str, start, lines: list[str], time_text: str) -> str:
    date_label = start.strftime("%d.%m.%Y") if start else ""
    schedule = f" am {date_label}" if date_label else ""
    if time_text:
        times = re.findall(r"\d{1,2}:\d{2}", time_text)
        if len(times) >= 2:
            schedule += f" von {times[0]} bis {times[1]} Uhr"
        elif times:
            schedule += f" um {times[0]} Uhr"

    location_parts = []
    for raw_part in lines[1:]:
        part = rc.clean(raw_part)
        if part and part not in location_parts:
            location_parts.append(part)
    location = f" im {', '.join(location_parts)}" if location_parts else " in Unkel"
    return f"„{rc.clean(title)}“ findet{schedule}{location} statt."
