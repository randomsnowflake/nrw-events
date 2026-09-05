"""Independent Bonn sport teaser contract and bounded detail enrichment."""
import re
from datetime import datetime

from .. import common, detail_enrichment
from . import regional_common as rc
from .bonn_policy import _active_reviewed_map, _clean_event_href

_SPORTS_URL = "https://www.bonn.de/bonn-erleben/aktiv-und-unterwegs/sportveranstaltungen.php"

def _parse_sport_time(text: str) -> str:
    m = re.search(r"(\d{1,2}):(\d{2})", text or "")
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else ""


def events_from_sport_teasers(html: str) -> list:
    """Parse the Bonn.de Sportveranstaltungen teaser list into dated events."""
    source = "Bonn.de Sports"
    events, seen = [], set()
    for body in rc.class_tag_blocks(html, "article", "SP-Teaser"):
        href = rc.attribute_from_class_tag(body, "a", "SP-Teaser__inner", "href")
        title_m = re.search(r'<h1[^>]+class="[^"]*SP-Teaser__headline[^"]*"[^>]*>(.*?)</h1>', body, re.S | re.I)
        cat_m = re.search(r'<span[^>]+class="[^"]*SP-Kicker__text[^"]*"[^>]*>(.*?)</span>', body, re.S | re.I)
        if not (href and title_m):
            continue
        title = common.clean_html(title_m.group(1))
        category = common.clean_html(cat_m.group(1) if cat_m else "Sport") or "Sport"
        link = common.urllib.parse.urljoin(
            "https://www.bonn.de", _clean_event_href(href),
        )
        for date_text, time_raw in re.findall(
            r'<span[^>]+class="[^"]*SP-Scheduling__date[^"]*"[^>]*>\s*(\d{2}\.\d{2}\.\d{4})\s*</span>'
            r'(?:\s*<span[^>]+class="[^"]*SP-Scheduling__time[^"]*"[^>]*>\s*([^<]*?)\s*</span>)?',
            body, re.S | re.I,
        ):
            start = common.parse_date(date_text)
            time_text = _parse_sport_time(common.clean_html(time_raw))
            if start and time_text:
                hour, minute = map(int, time_text.split(":"))
                start = start.replace(hour=hour, minute=minute)
            key = (title.lower(), start.strftime("%Y-%m-%d") if start else date_text, time_text)
            if key in seen:
                continue
            seen.add(key)
            ev = common.make_event(
                title, start, start, "", "Bonn",
                common.factual_event_description(
                    title, date_value=start, time_text=time_text, city="Bonn"),
                link, source, category, trust=0.8, time_text=time_text,
            )
            if ev:
                events.append(ev)
    return events


def _apply_reviewed_sport_occurrence_corrections(events: list[dict]) -> list[dict]:
    """Collapse BonnFest teaser rows into the official 25–27 September range."""
    corrected: list[dict] = []
    seen: set[tuple[str, str, str, str]] = set()
    primary_urls = _active_reviewed_map("bonn_press_primary_urls")
    overrides = _active_reviewed_map("bonn_press_overrides")
    bonnfest_key = next(
        (key for key in primary_urls if key[0].casefold().startswith("bonnfest")),
        None,
    )
    bonnfest_start = datetime.fromisoformat(bonnfest_key[1]) if bonnfest_key else None
    bonnfest_end = datetime.fromisoformat(bonnfest_key[2]) if bonnfest_key else None
    for event in events:
        candidate = dict(event)
        event_date = common.parse_iso_date(str(candidate.get("start_date") or ""))
        detail_path = common.urllib.parse.urlparse(str(candidate.get("link") or "")).path
        if (
            bonnfest_key is not None
            and common.clean_html(str(candidate.get("title") or "")) == bonnfest_key[0]
            and event_date
            and bonnfest_start
            and bonnfest_end
            and bonnfest_start.date() <= event_date.date() <= bonnfest_end.date()
            and detail_path == common.urllib.parse.urlparse(
                str(primary_urls[bonnfest_key])
            ).path
        ):
            candidate.update({
                "date": bonnfest_key[1],
                "start_date": bonnfest_key[1],
                "end_date": bonnfest_key[2],
                "time": "",
                "start_at": "",
                "end_at": "",
                "all_day": True,
                "description_html": "",
                "description_source": "generated",
            })
            candidate.update(overrides.get(bonnfest_key, {}))
        key = (
            common.clean_html(str(candidate.get("title") or "")).casefold(),
            str(candidate.get("start_date") or ""),
            str(candidate.get("end_date") or ""),
            str(candidate.get("link") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        corrected.append(candidate)
    return corrected


def _enrich_sport_details(events: list[dict], detail_fetcher) -> list[dict]:
    """Recover official Bonn.de Place data omitted from sport teaser cards."""
    enriched = []
    for event in events:
        event_date = common.parse_iso_date(str(event.get("start_date") or ""))
        if (
            event.get("venue")
            or not event.get("link")
            or not common.window_contains(event_date)
        ):
            enriched.append(event)
            continue
        locked = dict(event)
        locked["identity_venue"] = ""
        locked["identity_venue_locked"] = True
        try:
            document = detail_fetcher(str(event["link"]))
            context = detail_enrichment.extract_detail_context(document, locked)
            locked = detail_enrichment.apply_detail_context(locked, context)
        except Exception as exc:
            common.log_source_error("Bonn.de Sports detail", exc)
        enriched.append(locked)
    return enriched


def fetch_sports() -> list:
    source = "Bonn.de Sports"
    try:
        events = events_from_sport_teasers(common.fetch_url(_SPORTS_URL, timeout=20))
        return _apply_reviewed_sport_occurrence_corrections(
            _enrich_sport_details(
                events,
                lambda url: common.fetch_detail_url(
                    url,
                    cache_namespace="bonn-sports-detail",
                    timeout=15,
                    retry_attempts=1,
                ),
            ),
        )
    except Exception as e:
        common.log_source_error(source, e)
        return []

