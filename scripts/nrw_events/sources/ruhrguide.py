"""
Ruhr-Guide — WP Event Manager event listing for Ruhrgebiet / NRW.

Most Ruhrgebiet cities are outside the Bonn-centered 75 km radius, so the parser
keeps only entries whose location resolves to a known in-radius town.
"""

import re

from .. import common

_URL = "https://www.ruhr-guide.de/events/"


def fetch() -> list:
    source = "Ruhr-Guide"
    try:
        html = common.fetch_url(_URL, timeout=25)
        events = common.events_from_wp_event_manager_listing(
            html, source, "ruhr-guide nrw ruhrgebiet event konzert kultur ausstellung", 0.65)
        return _enrich_missing_descriptions(
            events,
            detail_fetcher=lambda url: common.fetch_detail_url(
                url, cache_namespace="ruhrguide", timeout=20),
        )
    except Exception as e:
        common.log_source_error(source, e)
        return []


def _detail_description(html: str) -> str:
    descriptions = [
        common.clean_html(item.get("description") or "")
        for item in common.jsonld_event_items(html or "")
        if item.get("description")
    ]
    if descriptions:
        return common.concise_description(max(descriptions, key=len), max_chars=360)
    metadata = re.search(
        r'<meta[^>]+property=["\']og:description["\'][^>]+'
        r'content=["\']([^"\']+)',
        html or "",
        re.S | re.I,
    )
    return common.concise_description(
        common.clean_html(metadata.group(1) if metadata else ""), max_chars=360)


def _fallback_description(event: dict) -> str:
    start = common.parse_iso_date(event.get("start_date") or "")
    schedule = f" am {start:%d.%m.%Y}" if start else ""
    time_text = event.get("time") or ""
    times = re.findall(r"\d{1,2}:\d{2}", time_text)
    if len(times) >= 2:
        schedule += f" von {times[0]} bis {times[1]} Uhr"
    elif times:
        schedule += f" um {times[0]} Uhr"
    place = f" am Veranstaltungsort „{event['venue']}“" if event.get("venue") else ""
    city = f" in {event['city']}" if event.get("city") and not place else ""
    return f"„{event.get('title', '')}“ findet{schedule}{place}{city} statt."


def _enrich_missing_descriptions(events: list, detail_fetcher) -> list:
    descriptions_by_link: dict[str, str] = {}
    failed_links = set()
    for event in events:
        if event.get("description"):
            continue
        link = (event.get("link") or "").strip()
        if link and link not in descriptions_by_link and link not in failed_links:
            try:
                descriptions_by_link[link] = _detail_description(detail_fetcher(link))
            except Exception as exc:
                failed_links.add(link)
                common.log_source_error("Ruhr-Guide detail", exc)
        event["description"] = descriptions_by_link.get(link) or _fallback_description(event)
    return events
