"""Municipal HTML calendars in Bonn/Rhein-Sieg."""

import re
import urllib.parse
from datetime import datetime

from .. import common, detail_enrichment
from . import regional_common as rc

_LOHMAR_BASE_URL = "https://www.lohmar.de/"
_LOHMAR_CALENDAR_URL = urllib.parse.urljoin(
    _LOHMAR_BASE_URL,
    "erlebnisfaktoren-natur-und-sport-freizeit-und-tourismus/veranstaltungen/",
)
_ALFTER_EMPTY_NOTICE = "Derzeit sind keine Einträge (unter dieser Rubrik) verfügbar."


def fetch() -> list:
    events = []
    events.extend(_fetch_alfter())
    events.extend(rc.fetch_html_events(
        "Lohmar",
        _LOHMAR_CALENDAR_URL,
        lambda html: _events_from_lohmar(
            html,
            detail_fetcher=lambda link: common.fetch_detail_url(
                link, cache_namespace="lohmar", timeout=15),
        ),
     source_id="lohmar-events"))
    events.extend(rc.fetch_html_events(
        "Bornheim",
        "https://www.bornheim.de/veranstaltungskalender",
        _events_from_bornheim,
     source_id="bornheim-events"))
    events.extend(rc.fetch_html_events(
        "Eitorf",
        "https://www.eitorf.de/veranstaltungen/",
        lambda html: _events_from_eitorf_cards(
            html,
            "https://www.eitorf.de",
            detail_fetcher=lambda link: common.fetch_detail_url(
                link, cache_namespace="eitorf-events", timeout=20,
            ),
        ),
     source_id="eitorf-events"))
    events.extend(rc.fetch_html_events(
        "Bröltal / Ruppichteroth",
        "https://www.broeltal.de/aktuelles/termine.html",
        lambda html: _events_from_broeltal(
            html,
            "https://www.broeltal.de",
            detail_fetcher=lambda link: common.fetch_detail_url(
                link, cache_namespace="broeltal-events", timeout=20,
            ),
        ),
     source_id="broeltal-ruppichteroth-events"))
    return rc.dedupe(events)


def _fetch_alfter() -> list:
    url = "https://www.alfter.de/schnellzugriff/veranstaltungen/"

    def parse(html: str) -> list:
        base = urllib.parse.urlsplit(url)._replace(path="").geturl()
        return common.events_from_time_listing(
            html,
            "Alfter",
            "Alfter",
            "alfter lokal kultur markt fest",
            0.84,
            base,
            min_title=3,
            max_chars=1800,
            anchor_pattern=r'<h3>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>\s*</h3>',
        )

    return rc.fetch_html_events(
        "Alfter",
        url,
        parse,
        source_id="alfter-events",
        empty_is_healthy=_alfter_calendar_is_expected_empty,
    )


def _alfter_calendar_is_expected_empty(html: str) -> bool:
    """Recognize Alfter's explicit, structurally valid empty-calendar notice."""
    return _ALFTER_EMPTY_NOTICE in rc.clean(html)


def _events_from_lohmar(html: str, detail_fetcher=None) -> list:
    """Parse Lohmar's event cards, including their teaser, time, and venue.

    The generic time-listing parser only retains the date/title/link tuple.
    Lohmar renders richer fields in each server-side card, so use those directly
    and request the detail page only when a teaser is genuinely missing.
    """
    events = []
    blocks = re.split(
        r'(?=<div[^>]+class="[^"]*\barticle\b[^"]*"[^>]*>)',
        html or "",
        flags=re.I,
    )
    for block in blocks:
        time_match = re.search(
            r'<time[^>]+datetime="([^"]+)"[^>]*>(.*?)</time>',
            block,
            re.S | re.I,
        )
        title_match = re.search(
            r'<h3[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>\s*</h3>',
            block,
            re.S | re.I,
        )
        if not (time_match and title_match):
            continue

        title = rc.clean(title_match.group(2))
        link = rc.abs_url(_LOHMAR_BASE_URL, title_match.group(1))
        time_text = rc.time_text(rc.clean(time_match.group(2)))
        venue_match = re.search(
            r'Veranstaltungsort:\s*(.*?)(?:</div>|<br\s*/?>)',
            block,
            re.S | re.I,
        )
        venue = rc.clean(venue_match.group(1) if venue_match else "")

        teaser_match = re.search(
            r'<div[^>]+class="[^"]*\bteaser-text\b[^"]*"[^>]*>(.*?)</div>',
            block,
            re.S | re.I,
        )
        teaser_html = teaser_match.group(1) if teaser_match else ""
        teaser_html = re.sub(
            r'<a\b[^>]*>\s*(?:mehr|details?)\s*</a>',
            "",
            teaser_html,
            flags=re.S | re.I,
        )
        description = common.concise_description(rc.clean(teaser_html))
        teaser_is_title = (
            description.casefold().strip(" .") == title.casefold().strip(" .")
        )
        start = common.parse_iso_date(time_match.group(1))
        if (not description or teaser_is_title) and common.window_contains(start):
            description = _lohmar_detail_description(link, detail_fetcher)
        description = description or _lohmar_fallback_description(title, time_text, venue)

        source_categories = " ".join(
            rc.clean(value)
            for value in re.findall(
                r'class="[^"]*\beventcategory\b[^"]*"[^>]*title="([^"]+)"',
                block,
                re.S | re.I,
            )
        )
        event = common.make_event(
            title,
            start,
            None,
            venue,
            "Lohmar",
            description,
            link,
            "Lohmar",
            f"lohmar lokal natur kultur markt {source_categories}",
            0.84,
            time_text,
        )
        if event:
            events.append(event)
    return events


def _lohmar_detail_description(link: str, detail_fetcher) -> str:
    if not (link and detail_fetcher):
        return ""
    try:
        html = detail_fetcher(link)
    except Exception as exc:
        common.log_source_error("Lohmar detail", exc)
        return ""
    body = re.search(
        r'<div[^>]+class="[^"]*\bnews-text-wrap\b[^"]*"[^>]*>(.*?)</div>',
        html or "",
        re.S | re.I,
    )
    return common.concise_description(rc.clean(body.group(1) if body else ""))


def _lohmar_fallback_description(title: str, time_text: str, venue: str) -> str:
    details = ""
    if time_text:
        details += f" um {time_text} Uhr"
    if venue:
        details += f" am Veranstaltungsort „{venue}“"
    return f"„{title}“ ist im Lohmarer Veranstaltungskalender{details} angekündigt."


def _events_from_bornheim(html: str) -> list:
    events = []
    for part in re.split(r'(?=<article class="event-teaser")', html):
        if 'class="event-teaser"' not in part:
            continue
        dates = re.findall(r'date-card-btn-date">([^<]+)', part, re.S | re.I)
        title = re.search(r'<p>([^<]{4,160})</p>', part, re.S | re.I)
        href = re.search(r'<a[^>]+href="([^"]*/veranstaltung/veranstaltung/[^"]+)"', part, re.S | re.I)
        cat = " ".join(rc.clean(x) for x in re.findall(r'<span class="eventcategory">(.*?)</span>', part, re.S | re.I))
        if not dates:
            continue
        title_text = rc.clean(title.group(1)) if title else rc.title_from_href(href.group(1) if href else "")
        if not title_text:
            continue
        for date_text in dates:
            start = rc.parse_dt(date_text)
            description = common.factual_event_description(
                title_text,
                date_value=start,
                city="Bornheim",
                calendar_name="Bornheim",
                categories=(cat,),
            )
            ev = common.make_event(
                title_text,
                start,
                None,
                "",
                "Bornheim",
                description,
                rc.abs_url("https://www.bornheim.de", href.group(1) if href else ""),
                "Bornheim",
                f"bornheim {cat} lokal markt kultur natur",
                0.86,
            )
            if ev:
                events.append(ev)
    return events


def _events_from_eitorf_cards(html: str, base: str, detail_fetcher=None) -> list:
    events = []
    for block in re.findall(r'<a[^>]+class="[^"]*card[^"]*"[^>]+data-date="[^"]+".*?</a>', html, re.S | re.I):
        href = re.search(r'href="([^"]+)"', block, re.I)
        date = re.search(r'data-date="([^"]+)"', block, re.I)
        title = re.search(r'<p class="title">(.*?)</p>', block, re.S | re.I)
        place = re.search(r'<p class="subtitle event-place">(.*?)</p>', block, re.S | re.I)
        subtitle = re.search(r'<p class="subtitle">\s*(.*?)\s*</p>', block, re.S | re.I)
        if not (date and title):
            continue
        block_text = rc.clean(block)
        start = rc.with_time(common.parse_iso_date(date.group(1)), rc.clean(subtitle.group(1) if subtitle else ""))
        ev = common.make_event(
            rc.clean(title.group(1)),
            start,
            start,
            _eitorf_venue(rc.clean(place.group(1) if place else ""), block_text),
            "Eitorf",
            block_text,
            rc.abs_url(base, href.group(1) if href else ""),
            "Eitorf",
            "lokal markt kultur outdoor fest",
            0.88,
            rc.time_text(block_text),
        )
        if ev:
            ev = _enrich_regional_detail(
                ev, detail_fetcher, "Eitorf detail",
            )
            events.append(ev)
    return events


def _eitorf_venue(place: str, text: str) -> str:
    """Recover an explicit place when the card's place starts with the city."""
    place = rc.clean(place)
    detail = re.sub(r"^Eitorf\s*,\s*", "", place, flags=re.I).strip()
    if detail and detail.casefold() != "eitorf":
        return detail
    meeting_point = re.search(
        r"\bTreffpunkt\s+ist\s+(?:(?:der|die|das|am|im)\s+)?([^,.;]+)",
        text or "",
        re.I,
    )
    return rc.clean(meeting_point.group(1)) if meeting_point else place


def _broeltal_named_address(text: str) -> dict[str, str]:
    """Extract a complete venue/address line from Bröltal event body copy."""
    match = re.search(
        r"(?:^|\n)\s*(?:Kostenfrei!\s*)?"
        r"([^,\n]{3,100}),\s*([^,\n]*\d[^,\n]*),\s*"
        r"(\d{5}\s+Ruppichteroth)\b",
        text or "",
        re.I,
    )
    if not match:
        return {}
    return {
        "venue": rc.clean(match.group(1)),
        "venue_address": f"{rc.clean(match.group(2))}, {rc.clean(match.group(3))}",
    }


def _broeltal_explicit_place(text: str) -> dict[str, str]:
    """Recover only the location phrases used in Bröltal event body copy."""
    prose = rc.clean_blocks(text or "")
    named_address = _broeltal_named_address(prose)
    if named_address:
        return named_address

    pfarrheim = re.search(
        r"\bim\s+(Pfarrheim\s+[^,.\n]{2,80}),\s*([^,\n]{2,80}?\d+[a-z]?)"
        r"(?=\s+(?:statt|$)|[.;\n])",
        prose,
        re.I,
    )
    if pfarrheim:
        return {
            "venue": rc.clean(pfarrheim.group(1)),
            "venue_address": rc.clean(pfarrheim.group(2)),
        }

    meeting_point = re.search(
        r"\bTreffpunkt\s+ist\s+(?:der|die|das)\s+([^.;\n]{4,160})",
        prose,
        re.I,
    )
    if meeting_point:
        venue = re.sub(
            r"\s+um\s+\d{1,2}(?::\d{2})?\s*Uhr\b.*$",
            "",
            rc.clean(meeting_point.group(1)),
            flags=re.I,
        )
        return {"venue": venue}

    event_place = re.search(
        r"\bOrt\s+der\s+Veranstaltung\s*:\s*([^.;\n]{4,160})",
        prose,
        re.I,
    )
    if event_place:
        return {"venue": rc.clean(event_place.group(1))}
    return {}


def _broeltal_detail_context(document: str, event: dict) -> dict:
    """Add venue facts only from exact occurrence or bounded event copy."""
    context = detail_enrichment.extract_detail_context(document, event)
    exact_place: dict[str, str] = {}
    expected_title = detail_enrichment._exact_title_key(event.get("title"))
    expected_date = str(event.get("start_date") or event.get("date") or "")[:10]
    for item in common.jsonld_event_items(document or ""):
        if (
            detail_enrichment._exact_title_key(item.get("name")) != expected_title
            or str(item.get("startDate") or "")[:10] != expected_date
        ):
            continue
        location = item.get("location")
        if isinstance(location, list):
            location = next((value for value in location if isinstance(value, dict)), None)
        if isinstance(location, dict):
            exact_place["venue"] = common.clean_html(str(location.get("name") or ""))
            address = location.get("address")
            if isinstance(address, dict):
                exact_place["venue_address"] = " ".join(filter(None, (
                    common.clean_html(str(address.get("streetAddress") or "")),
                    common.clean_html(str(address.get("postalCode") or "")),
                    common.clean_html(str(address.get("addressLocality") or "")),
                )))
        break
    context["venue"] = exact_place.get("venue", "")
    context["venue_address"] = exact_place.get("venue_address", "")
    event_scoped_copy = context.get("exact_description")
    if not event_scoped_copy:
        visible = rc.clean_blocks(document or "")
        title = rc.clean(str(event.get("title") or ""))
        start_date = str(event.get("start_date") or event.get("date") or "")[:10]
        date_labels = {start_date}
        try:
            parsed_date = datetime.strptime(start_date, "%Y-%m-%d")
            date_labels.add(parsed_date.strftime("%d.%m.%Y"))
            date_labels.add(f"{parsed_date.day}.{parsed_date.month}.{parsed_date.year}")
        except ValueError:
            pass
        if title and title.casefold() in visible.casefold() and any(
            label and label in visible for label in date_labels
        ):
            visible_description = re.search(
                r'<(?:div|section)\b[^>]*class=["\'][^"\']*\bevent-description\b'
                r'[^"\']*["\'][^>]*>(.*?)</(?:div|section)>',
                document or "", re.I | re.S,
            )
            if visible_description:
                event_scoped_copy = visible_description.group(1)
    context.update(_broeltal_explicit_place(str(event_scoped_copy or "")))
    return context


def _events_from_broeltal(html: str, base: str, detail_fetcher=None) -> list:
    events = []
    blocks = re.findall(r'<a class="list-group-item list-group-item-action" href="([^"]+)">(.*?)</a>',
                        html, re.S | re.I)
    for href, body in blocks:
        text = rc.clean(body)
        start, end = rc.range_dates(text)
        title = re.search(r'<h[1-6][^>]*>(.*?)</h[1-6]>', body, re.S | re.I)
        title_text = rc.clean(title.group(1)) if title else re.sub(r"\d{1,2}\.\d{1,2}\..*", "", text).strip()
        if not (start and title_text):
            continue
        ev = common.make_event(
            title_text,
            rc.with_time(start, text),
            end,
            "",
            "Ruppichteroth",
            text[:500],
            rc.abs_url(base, href),
            "Bröltal / Ruppichteroth",
            "broeltal ruppichteroth lokal natur markt fest",
            0.86,
            rc.time_text(text),
        )
        if ev:
            ev["identity_venue"] = ""
            ev["identity_venue_locked"] = True
            ev.update(_broeltal_explicit_place(text))
            ev = _enrich_regional_detail(
                ev, detail_fetcher, "Bröltal / Ruppichteroth detail",
                context_extractor=_broeltal_detail_context,
            )
            events.append(ev)
    return events


def _enrich_regional_detail(
    event: dict,
    detail_fetcher,
    source: str,
    context_extractor=detail_enrichment.extract_detail_context,
) -> dict:
    if not detail_fetcher or not common.window_contains(
        common.parse_iso_date(str(event.get("start_date") or "")),
    ):
        return event
    try:
        document = detail_fetcher(str(event.get("link") or ""))
        context = context_extractor(document, event)
        return detail_enrichment.apply_detail_context(event, context)
    except Exception as exc:
        common.log_source_error(source, exc)
        return event
