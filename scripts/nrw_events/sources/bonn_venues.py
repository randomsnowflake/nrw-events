"""High-value Bonn venue calendars requested by users."""

import json
import re
from datetime import datetime

from .. import common
from . import regional_common as rc

_KULT41_URL = "https://www.kult41.de/veranstaltungen/programm"
_REPAIR_CAFES_URL = "https://www.repaircafesbonn.de/termine/"
_BROTFABRIK_URL = "https://brotfabrik-bonn.de/"
_BROTFABRIK_EVENTS_API = "https://brotfabrik-bonn.de/wp-content/themes/totochildelementor/alleVer.php"
_VOLKSSTERNWARTE_ICAL = "https://www.volkssternwarte-bonn.de/wordpress/kalender/liste/?ical=1"
_BOTGART_URL = "https://www.botgart.uni-bonn.de/de/ihr-besuch/veranstaltungen"
_VOX_BONA_ICAL = "https://vox-bona.de/kalender/?ical=1"
_BONNER_MUENSTER_URL = "https://www.bonner-muenster.de/musik/"


def fetch() -> list:
    events = []
    for month, year in _months_in_window():
        events.extend(rc.fetch_html_events(
            "KULT41",
            f"{_KULT41_URL}?mo={month}&yr={year}",
            events_from_kult41,
        ))
        events.extend(rc.fetch_html_events(
            "Repair Cafés Bonn",
            f"{_REPAIR_CAFES_URL}?time=month&yr={year}&month={month}",
            events_from_repair_cafes,
        ))
    events.extend(_fetch_brotfabrik())
    events.extend(_fetch_volkssternwarte())
    events.extend(rc.fetch_html_events(
        "Botanische Gärten Bonn",
        _BOTGART_URL,
        lambda html: events_from_botgart(
            html,
            detail_fetcher=lambda url: common.fetch_detail_url(
                url, cache_namespace="botanische-gaerten-bonn", timeout=20),
        ),
    ))
    events.extend(_fetch_vox_bona())
    events.extend(_fetch_bonner_muenster())
    return rc.dedupe(events)


def events_from_kult41(html: str) -> list:
    events = []
    for block in re.findall(r'<div class="em-event em-item .*?(?=<div class="em-event em-item |\Z)', html, re.S | re.I):
        title = re.search(r'<h3 class="em-item-title">\s*<a href="([^"]+)">(.*?)</a>\s*</h3>', block, re.S | re.I)
        date = re.search(r'class="[^"]*em-event-date[^"]*".*?</span>\s*([^<]+)</div>', block, re.S | re.I)
        time = re.search(r'class="[^"]*em-event-time[^"]*".*?em-icon-clock.*?</span>\s*([^<]+)</div>', block, re.S | re.I)
        desc = re.search(r'<div class="em-item-desc">\s*(.*?)\s*</div>', block, re.S | re.I)
        cats = " ".join(rc.clean(cat) for cat in re.findall(r'events/categories/[^"]+">(.*?)</a>', block, re.S | re.I))
        price = _match_clean(r'em-icon-ticket.*?</span>\s*([^<]+)</div>', block)
        if not (title and date):
            continue
        title_text = rc.clean(title.group(2)).strip(' "“”')
        if re.search(r"offenes büro|veranstalter\*?in werden", title_text, re.I):
            continue
        start = rc.with_time(_parse_short_date(date.group(1)), time.group(1) if time else "")
        end = _with_end_time(start, time.group(1) if time else "")
        ev = common.make_event(
            title_text,
            start,
            end or start,
            "KULT41",
            "Bonn",
            rc.clean(desc.group(1) if desc else ""),
            title.group(1),
            "KULT41",
            f"kult41 {cats} konzert theater kunst kultur community spiele",
            0.92,
        )
        if ev:
            ev["price"] = _clean_price(price)
            events.append(ev)
    return events


def events_from_repair_cafes(html: str) -> list:
    events = []
    for article in re.findall(r"<article[^>]+calendar-event\b.*?</article>", html, re.S | re.I):
        title = re.search(r"<h3[^>]*class=['\"]event-title summary['\"][^>]*>.*?<button[^>]*>(.*?)</button>", article, re.S | re.I)
        if not title:
            title = re.search(r"<h3[^>]*class=['\"]event-title summary['\"][^>]*>(.*?)</h3>", article, re.S | re.I)
        if not title:
            continue
        title_text = rc.clean(
            _match_text(r"data-modal-title=['\"]([^'\"]+)['\"]", article)
            or _match_text(r"aria-label=['\"]Weiterlesen:\s*([^'\"]+)['\"]", article)
            or title.group(1)
        )
        if re.search(r"\bfirmen\b|monats-?treffen|arbeitsgemeinschaft", title_text, re.I):
            continue
        start_raw = _match_text(r"class=['\"]value-title['\"][^>]+datetime=['\"]([^'\"]+)['\"]", article)
        end_raw = _match_text(r"class=['\"]end-time dtend['\"][\s\S]*?datetime=['\"]([^'\"]+)['\"]", article)
        venue = _match_clean(r"class=['\"]location-label['\"][\s\S]*?<span[^>]*></span>\s*(.*?)</a>", article)
        if not venue:
            venue = _match_clean(r"Karte<span[^>]*>\s*(.*?)</span>", article)
        desc = _match_clean(r"<div class=['\"]longdesc description['\"]>(.*?)</div>\s*<div class=['\"]mc-location", article)
        link = _match_text(r"class=['\"]mc-details['\"]><a[^>]+href=['\"]([^'\"]+)['\"]", article) or _REPAIR_CAFES_URL
        coords = _coords_from_google_maps(article)
        ev = common.make_event(
            title_text,
            common.parse_iso_date(start_raw),
            common.parse_iso_date(end_raw),
            venue,
            "Bonn",
            desc,
            link,
            "Repair Cafés Bonn",
            "repair café reparatur offene werkstatt nachhaltigkeit fahrrad nähen",
            0.9,
            coords=coords,
        )
        if ev:
            events.append(ev)
    return events


def events_from_brotfabrik(html: str) -> list:
    text = rc.clean(html)
    events = []
    pattern = re.compile(
        r"(?P<prefix>[A-ZÄÖÜa-zäöüß0-9][^|]{5,160}?)\s*\|\s*"
        r"(?P<time>\d{1,2}:\d{2})\s*Uhr\s*"
        r"(?P<date>\d{1,2}\.\d{1,2}\.20\d{2})"
    )
    for match in pattern.finditer(text):
        title, category = _split_brotfabrik_prefix(match.group("prefix"))
        if re.search(r"^(heute|aktuelles programm|kontaktimprovisation /)$", title, re.I):
            continue
        start = rc.with_time(common.parse_date(match.group("date")), match.group("time"))
        ev = common.make_event(
            title,
            start,
            start,
            "Brotfabrik Bonn",
            "Bonn",
            category,
            _BROTFABRIK_URL + "#programm",
            "Brotfabrik Bonn",
            f"brotfabrik {category} theater kultur konzert",
            0.82,
        )
        if ev:
            events.append(ev)
    return events


def events_from_brotfabrik_items(items: list) -> list:
    events = []
    for item in items if isinstance(items, list) else []:
        title = (item.get("Titel") or "").strip()
        start = common.parse_iso_date(item.get("Datum") or "")
        start = rc.with_time(start, item.get("Uhrzeit") or "")
        end = None
        if item.get("Datumbis") and item.get("Datumbis") != "0000-00-00":
            end = common.parse_iso_date(item.get("Datumbis"))
        end = rc.with_time(end, item.get("Uhrzeit") or "") if end else start
        gewerk = (item.get("Gewerk") or "Programm").strip()
        ev = common.make_event(
            title,
            start,
            end,
            item.get("Ort") or "Brotfabrik Bonn",
            "Bonn",
            item.get("Beschreibung") or "",
            item.get("Url") or _BROTFABRIK_URL + "#programm",
            "Brotfabrik Bonn",
            f"brotfabrik {gewerk} theater kino tanz konzert workshop kultur",
            0.86,
        )
        if ev:
            events.append(ev)
    return events


def _botgart_detail_description(html: str) -> str:
    description = re.search(
        r'<div[^>]+id=["\']event-description["\'][^>]*>(.*?)</div>',
        html or "",
        re.S | re.I,
    )
    if description:
        return common.concise_description(rc.clean(description.group(1)), max_chars=360)
    metadata = re.search(
        r'<meta[^>]+(?:name|property)=["\'](?:description|og:description)["\'][^>]+'
        r'content=["\']([^"\']+)',
        html or "",
        re.S | re.I,
    )
    return common.concise_description(
        rc.clean(metadata.group(1) if metadata else ""), max_chars=360)


def _botgart_fallback_description(title: str, kind: str, start) -> str:
    schedule = f" am {start:%d.%m.%Y}" if start else ""
    if start and start.strftime("%H:%M") != "00:00":
        schedule += f" um {start:%H:%M} Uhr"
    category = f" aus dem Bereich „{kind}“" if kind else ""
    return (
        f"Die Veranstaltung „{title}“{category} findet{schedule} "
        "in den Botanischen Gärten Bonn statt."
    )


def events_from_botgart(html: str, detail_fetcher=None) -> list:
    events = []
    for href, body in re.findall(r'<a[^>]+href="([^"]+/de/ihr-besuch/veranstaltungen/[^"]+)"[^>]*>(.*?)</a>', html, re.S | re.I):
        text = rc.clean(body)
        match = re.search(
            r"^(?P<kind>[A-Za-zÄÖÜäöüß]+)\s+"
            r"(?:(?:Mo|Di|Mi|Do|Fr|Sa|So|Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag|Sonntag),?\s+)?"
            r"(?P<date>\d{1,2}\.\d{1,2}\.20\d{2})\s+"
            r"(?P<time>\d{1,2}:\d{2})\s+Uhr\s+"
            r"(?P<title>.+)$",
            text,
            re.I,
        )
        if not match:
            continue
        start = rc.with_time(common.parse_date(match.group("date")), match.group("time"))
        title = match.group("title")
        kind = match.group("kind")
        link = rc.abs_url("https://www.botgart.uni-bonn.de", href)
        fallback = _botgart_fallback_description(title, kind, start)
        if not common.event_in_window_and_radius(start, start, "Bonn"):
            continue
        description = ""
        if detail_fetcher:
            try:
                description = _botgart_detail_description(detail_fetcher(link))
            except Exception as exc:
                common.log_source_error("Botanische Gärten Bonn detail", exc)
        ev = common.make_event(
            title,
            start,
            start,
            "Botanische Gärten Bonn",
            "Bonn",
            description or fallback,
            link,
            "Botanische Gärten Bonn",
            f"botanische gärten bonn {kind} natur führung exkursion vortrag",
            0.9,
        )
        if ev:
            events.append(ev)
    return events


def events_from_bonner_muenster(html: str) -> list:
    events = []
    for block in re.findall(r'<li class="list-entry teaser-tile.*?</li>', html, re.S | re.I):
        href = _match_text(r'<a href="([^"]*/detail/[^"]+)"', block)
        title = _match_clean(r'<h[1-6][^>]*class="[^"]*headline[^"]*"[^>]*>(.*?)</h[1-6]>', block)
        if not title:
            title = _match_clean(r'<a[^>]+href="[^"]*/detail/[^"]+"[^>]*>([^<]*?:[^<]+)</a>', block)
        date_text = _match_clean(
            r'((?:Mo|Di|Mi|Do|Fr|Sa|So)\.\s+\d{1,2}\.\s+[A-Za-zÄÖÜäöüß]+\.?\s+20\d{2}\s+\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2})',
            block,
        )
        if not (title and date_text):
            continue
        venue, title_text = _split_muenster_title(title)
        start, end = _parse_muenster_datetime(date_text)
        if not start:
            continue
        ev = common.make_event(
            title_text,
            start,
            end or start,
            venue or "Bonner Münster",
            "Bonn",
            date_text,
            rc.abs_url("https://www.bonner-muenster.de", href),
            "Bonner Münster Musik",
            "bonner münster kirchenmusik orgel konzert chor klassik",
            0.9,
        )
        if ev:
            events.append(ev)
    return events


def _fetch_volkssternwarte() -> list:
    try:
        return common.fetch_ical(
            _VOLKSSTERNWARTE_ICAL,
            "Volkssternwarte Bonn",
            "Bonn",
            "volkssternwarte astronomie vortrag sternenhimmel wissenschaft",
            0.92,
        )
    except Exception as e:
        common.log_source_error("Volkssternwarte Bonn", e)
        return []


def _fetch_brotfabrik() -> list:
    try:
        items = json.loads(common.fetch_url(
            _BROTFABRIK_EVENTS_API,
            timeout=20,
            accept="application/json,*/*;q=0.8",
            sec_fetch_mode="cors",
            sec_fetch_dest="empty",
            headers={"Referer": _BROTFABRIK_URL},
        ))
        events = events_from_brotfabrik_items(items)
        if events:
            return events
    except Exception as e:
        common.log_source_error("Brotfabrik Bonn API", e)
    return rc.fetch_html_events("Brotfabrik Bonn", _BROTFABRIK_URL, events_from_brotfabrik)


def _fetch_vox_bona() -> list:
    try:
        return common.fetch_ical(
            _VOX_BONA_ICAL,
            "Vox Bona",
            "",
            "vox bona chor kirchenmusik klassik konzert",
            0.9,
            city_resolver=_vox_bona_city,
        )
    except Exception as e:
        common.log_source_error("Vox Bona", e)
        return []


def _fetch_bonner_muenster() -> list:
    events = []
    urls = [_BONNER_MUENSTER_URL] + [
        f"https://www.bonner-muenster.de/musik/index.html?reloaded&sort=date_asc&page={page}"
        for page in range(2, 7)
    ]
    for url in urls:
        try:
            page_events = events_from_bonner_muenster(common.fetch_url(url, timeout=25))
        except Exception as e:
            common.log_source_error("Bonner Münster Musik", e)
            continue
        events.extend(page_events)
    return rc.dedupe(events)


def _parse_short_date(value: str):
    match = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{2,4})", value or "")
    if not match:
        return None
    day, month, year = (int(part) for part in match.groups())
    year = 2000 + year if year < 100 else year
    try:
        return datetime(year, month, day)
    except ValueError:
        return None


def _with_end_time(start, text: str):
    if not start:
        return None
    times = re.findall(r"(\d{1,2}):(\d{2})", text or "")
    if len(times) < 2:
        return start
    hour, minute = (int(part) for part in times[1])
    return start.replace(hour=hour, minute=minute)


def _split_brotfabrik_prefix(prefix: str) -> tuple[str, str]:
    prefix = rc.clean(prefix)
    categories = (
        "Hofkultur", "Theater", "Kino", "Bib", "Kulturkneipe", "Tanz", "Konzert",
        "Ausstellung", "Workshop", "Performance",
    )
    for category in categories:
        suffix = f" {category}"
        if prefix.endswith(suffix):
            return prefix[:-len(suffix)].strip(" -"), category
    parts = prefix.rsplit(" ", 1)
    if len(parts) == 2:
        return parts[0].strip(" -"), parts[1]
    return prefix, "Programm"


def _months_in_window() -> list[tuple[int, int]]:
    months = []
    cursor = datetime(common.TODAY.year, common.TODAY.month, 1)
    end = datetime(common.END_DATE.year, common.END_DATE.month, 1)
    while cursor <= end:
        months.append((cursor.month, cursor.year))
        year = cursor.year + (1 if cursor.month == 12 else 0)
        month = 1 if cursor.month == 12 else cursor.month + 1
        cursor = datetime(year, month, 1)
    return months


def _parse_muenster_datetime(text: str):
    match = re.search(
        r"(\d{1,2})\.\s*([A-Za-zÄÖÜäöüß]+)\.?\s*(20\d{2})\s+"
        r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})",
        text or "",
    )
    if not match:
        return None, None
    day, month_name, year, sh, sm, eh, em = match.groups()
    month = common.MONTH_DE.get(month_name.lower().rstrip(".")) or {"sept": 9}.get(month_name.lower().rstrip("."))
    if not month:
        return None, None
    start = datetime(int(year), month, int(day), int(sh), int(sm))
    end = datetime(int(year), month, int(day), int(eh), int(em))
    return start, end


def _split_muenster_title(text: str) -> tuple[str, str]:
    text = rc.clean(text)
    if " : " not in text:
        return "Bonner Münster", text
    venue, title = text.split(" : ", 1)
    return venue.strip(), title.strip()


def _vox_bona_city(location: str) -> str:
    text = (location or "").lower()
    if "bonn" in text:
        return "Bonn"
    if "köln" in text or "koeln" in text:
        return "Köln"
    return ""


def _coords_from_google_maps(text: str):
    match = re.search(r"daddr=([0-9.]+)N,([0-9.]+)E", text or "")
    if not match:
        return None
    return float(match.group(1)), float(match.group(2))


_match_text = rc.first_group
_match_clean = rc.first_group_clean


def _clean_price(text: str) -> str:
    text = rc.clean(text)
    return "" if text in {"Eintritt: €", "Eintritt:"} else text
