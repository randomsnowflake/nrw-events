"""
Ruhr-Guide — WP Event Manager event listing for Ruhrgebiet / NRW.

Most Ruhrgebiet cities are outside the Bonn-centered 75 km radius, so the parser
keeps only entries whose location resolves to a known in-radius town.
"""

import re

from .. import common
from . import regional_common as rc

_URL = "https://www.ruhr-guide.de/events/"


def fetch() -> list:
    source = "Ruhr-Guide"
    try:
        html = common.fetch_url(_URL, timeout=25)
        events = common.events_from_wp_event_manager_listing(
            html, source, "ruhr-guide nrw ruhrgebiet event konzert kultur ausstellung", 0.65)
        return _expand_tour_ranges(
            events,
            detail_fetcher=lambda url: common.fetch_detail_url(
                url, cache_namespace="ruhr-guide-tour-dates", timeout=20,
            ),
        )
    except Exception as e:
        common.log_source_error(source, e)
        return []


def _keep_only_master_data(events: list) -> list:
    """Keep Ruhr-Guide dates while discarding all editorial description copy."""
    return [common.keep_only_event_master_data(event) for event in events]


def _is_tour_range(event: dict) -> bool:
    start = common.parse_iso_date(str(event.get("start_date") or ""))
    end = common.parse_iso_date(str(event.get("end_date") or ""))
    return bool(start and end and (end.date() - start.date()).days > 31)


def _expand_tour_ranges(events: list[dict], detail_fetcher) -> list[dict]:
    """Replace publisher-wide tour spans with explicit local occurrences.

    Ruhr-Guide sometimes models a multi-city tour as one event from the first
    show to the last. Publishing that range makes a months-long event appear to
    run continuously at the first venue. The detail page contains factual
    date/venue lines, which are safe master data even though editorial prose is
    deliberately discarded.
    """
    expanded: list[dict] = []
    for event in events:
        if not _is_tour_range(event):
            expanded.append(common.keep_only_event_master_data(event))
            continue
        try:
            document = detail_fetcher(str(event.get("link") or ""))
            occurrences = _tour_occurrences(event, document)
        except Exception as exc:
            common.log_source_error("Ruhr-Guide tour detail", exc, source_id="ruhr-guide")
            occurrences = []
        expanded.extend(occurrences)
    return expanded


def _tour_occurrences(event: dict, document: str) -> list[dict]:
    body_match = re.search(
        r'class=["\'][^"\']*\bwpem-single-event-body-content\b[^"\']*["\'][^>]*>(.*?)'
        r'(?=<!--\s*Event description section end|<div\b[^>]+class=["\'][^"\']*\bwpem-additional-info)',
        document or "", re.I | re.S,
    )
    body = body_match.group(1) if body_match else ""
    occurrences: list[dict] = []
    for paragraph in re.findall(r"<p\b[^>]*>(.*?)</p>", body, re.I | re.S):
        text = common.clean_html(paragraph)
        match = re.search(
            r"\b(\d{1,2}\.\d{1,2}\.(?:20)?\d{2})\s*[–-]\s*(.*?)"
            r"\s*\((?:Vorstellungen?\s+um\s+)?([^)]+)\)",
            text, re.I,
        )
        if not match:
            continue
        start = common.parse_date(match.group(1))
        if not start or not common.window_contains(start):
            continue
        place = common.clean_html(match.group(2))
        venue, separator, city = place.rpartition(",")
        if not separator:
            venue, city = place, str(event.get("city") or "")
        venue = venue.strip()
        city = rc.city_from_text(city, str(event.get("city") or "")).strip()
        times = [
            f"{int(hour):02d}:{minute}"
            for hour, minute in re.findall(r"(\d{1,2})[.:](\d{2})", match.group(3))
        ]
        time_text = " / ".join(times)
        start_with_time = rc.with_time(start, times[0] if times else "")
        occurrence = common.make_event(
            str(event.get("title") or ""),
            start_with_time,
            start_with_time,
            venue,
            city,
            "",
            str(event.get("link") or ""),
            "Ruhr-Guide",
            str(event.get("category") or "ruhr-guide nrw veranstaltung"),
            float(event.get("score") or 0.65),
            time_text,
            source_id="ruhr-guide",
        )
        if occurrence:
            if len(times) > 1:
                occurrence["time_note"] = (
                    "Weitere Vorstellungen: " + ", ".join(times[1:]) + " Uhr"
                )
            occurrences.append(common.keep_only_event_master_data(occurrence))
    return occurrences
