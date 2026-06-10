"""
Regional Bonn / Rhein-Sieg / nearby RLP sources from the import proposal.

The module groups small municipal calendars by CMS pattern instead of creating a
file per town. Every fetcher still returns plain make_event() dictionaries and
fails soft, matching the rest of the package.
"""

import json
import re
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from html import unescape

from .. import common


_MONTH = {
    **common.MONTH_DE,
    "jan": 1, "feb": 2, "mar": 3, "mär": 3, "apr": 4, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "sept": 9, "oct": 10, "okt": 10, "nov": 11,
    "dec": 12, "dez": 12,
}


def _abs(base: str, href: str) -> str:
    return urllib.parse.urljoin(base, unescape(href or "").strip())


def _clean(text: str) -> str:
    return common.clean_html(unescape(text or ""))


def _parse_dt(text: str):
    text = _clean(text)
    dt = common.parse_date(text)
    if dt:
        return dt
    m = re.search(r"(\d{1,2})\.\s*([A-Za-zäöüÄÖÜ.]+)\s*(20\d{2})", text)
    if m:
        day, month, year = m.groups()
        mon = _MONTH.get(month.lower().rstrip("."))
        if mon:
            return datetime(int(year), mon, int(day))
    return None


def _with_time(dt, text: str):
    if not dt:
        return None
    m = re.search(r"(\d{1,2}):(\d{2})", text or "")
    if m:
        return dt.replace(hour=int(m.group(1)), minute=int(m.group(2)))
    return dt


def _time_text(text: str) -> str:
    times = re.findall(r"\d{1,2}:\d{2}", text or "")
    if len(times) >= 2:
        return f"{times[0]}–{times[1]}"
    return times[0] if times else ""


def _city_from_text(text: str, default_city: str) -> str:
    return common.guess_city_from_text(text) or default_city


def _dedupe(events: list) -> list:
    seen, out = set(), []
    for ev in events:
        key = (ev["source"], ev["title"].lower(), ev["date"], ev["city"].lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(ev)
    return out


def _title_from_href(href: str) -> str:
    slug = urllib.parse.urlparse(unescape(href or "")).path.rstrip("/").split("/")[-1]
    slug = re.sub(r"\.(?:html|php)$", "", slug)
    slug = re.sub(r"[-_]+", " ", slug)
    return slug.strip().title()


def _events_from_sitekit_list(html: str, source: str, base: str, city: str, trust: float) -> list:
    events = []
    for block in re.findall(r'<article class="SP-Teaser.*?</article>', html, re.S | re.I):
        href = re.search(r'<a[^>]+class="SP-Teaser__inner"[^>]+href="([^"]+)"', block, re.S | re.I)
        date = re.search(r'<span class="SP-Scheduling__date">([^<]+)', block, re.S | re.I)
        title = re.search(r'<h4 class="SP-Teaser__headline">(.*?)</h4>', block, re.S | re.I)
        desc = re.search(r'<div class="SP-Teaser__abstract">(.*?)</div>', block, re.S | re.I)
        if not (date and title):
            continue
        text = _clean(block)
        start = _with_time(_parse_dt(date.group(1)), text)
        ev = common.make_event(
            _clean(title.group(1)), start, start, city, city,
            _clean(desc.group(1) if desc else ""), _abs(base, href.group(1) if href else ""),
            source, "kommunal kultur markt ausstellung konzert führung", trust, _time_text(text),
        )
        if ev:
            events.append(ev)
    return _dedupe(events)


def _events_from_time_page(url: str, source: str, city: str, category: str, trust: float,
                           title_pattern: str = "") -> list:
    html = common.fetch_url(url, timeout=25)
    return common.events_from_time_listing(
        html, source, city, category, trust, urllib.parse.urlsplit(url)._replace(path="").geturl(),
        min_title=3, max_chars=1800, anchor_pattern=title_pattern or None)


def _events_from_eitorf_cards(html: str, source: str, base: str) -> list:
    events = []
    for block in re.findall(r'<a[^>]+class="[^"]*card[^"]*"[^>]+data-date="[^"]+".*?</a>', html, re.S | re.I):
        href = re.search(r'href="([^"]+)"', block, re.I)
        date = re.search(r'data-date="([^"]+)"', block, re.I)
        title = re.search(r'<p class="title">(.*?)</p>', block, re.S | re.I)
        place = re.search(r'<p class="subtitle event-place">(.*?)</p>', block, re.S | re.I)
        subtitle = re.search(r'<p class="subtitle">\s*(.*?)\s*</p>', block, re.S | re.I)
        if not (date and title):
            continue
        start = _with_time(common.parse_iso_date(date.group(1)), _clean(subtitle.group(1) if subtitle else ""))
        ev = common.make_event(
            _clean(title.group(1)), start, start, _clean(place.group(1) if place else ""),
            "Eitorf", _clean(block), _abs(base, href.group(1) if href else ""), source,
            "lokal markt kultur outdoor fest", 0.88, _time_text(_clean(block)),
        )
        if ev:
            events.append(ev)
    return _dedupe(events)


def _events_from_shapehub(html: str, source: str, base: str, default_city: str, category: str,
                          trust: float) -> list:
    events = []
    card_re = re.compile(r'<a href="(?P<href>[^"]+)" class="shapehub-card-link">(?P<body>.*?)</a>',
                         re.S | re.I)
    for m in card_re.finditer(html):
        body = m.group("body")
        date = re.search(r'shapehub-date-badge">\s*([^<]+)', body, re.S | re.I)
        title = re.search(r'shapehub-card-title">(.*?)</div>', body, re.S | re.I)
        if not (date and title):
            continue
        text = _clean(body)
        city = _city_from_text(text, default_city)
        ev = common.make_event(
            _clean(title.group(1)), _with_time(_parse_dt(date.group(1)), text), None,
            city, city, text[:500], _abs(base, m.group("href")), source, category, trust,
            _time_text(text),
        )
        if ev:
            events.append(ev)
    return _dedupe(events)


def _events_from_broeltal(html: str, source: str, base: str) -> list:
    events = []
    blocks = re.findall(r'<a class="list-group-item list-group-item-action" href="([^"]+)">(.*?)</a>',
                        html, re.S | re.I)
    for href, body in blocks:
        text = _clean(body)
        date_match = re.search(r"\d{1,2}\.\d{1,2}\.(?:\s*–\s*\d{1,2}\.\d{1,2}\.)?20\d{2}", text)
        title = re.search(r'<h[1-6][^>]*>(.*?)</h[1-6]>', body, re.S | re.I)
        title_text = _clean(title.group(1)) if title else re.sub(r"\d{1,2}\.\d{1,2}\..*", "", text).strip()
        if not (date_match and title_text):
            continue
        ev = common.make_event(
            title_text, _with_time(_parse_dt(date_match.group(0)), text), None, "",
            "Ruppichteroth", text[:500], _abs(base, href), source,
            "broeltal ruppichteroth lokal natur markt fest", 0.86, _time_text(text),
        )
        if ev:
            events.append(ev)
    return _dedupe(events)


def _events_from_swisttal(html: str) -> list:
    events = []
    for block in re.findall(r'<article class="event-template.*?</article>', html, re.S | re.I):
        date = re.search(r'<time[^>]+datetime="([^"]+)"', block, re.S | re.I)
        title = re.search(r'class="post-title"[^>]*>(.*?)</a>', block, re.S | re.I)
        href = re.search(r'class="post-title"[^>]+href="([^"]+)"', block, re.S | re.I)
        venue = re.search(r'class="host-location">\s*(.*?)\s*</div>', block, re.S | re.I)
        if not (date and title):
            continue
        ev = common.make_event(
            _clean(title.group(1)), common.parse_date(date.group(1)), None,
            _clean(venue.group(1) if venue else ""), "Swisttal", _clean(block),
            href.group(1) if href else "https://www.swisttal.de/veranstaltungen/",
            "Swisttal", "swisttal lokal markt kultur konzert fest", 0.86,
        )
        if ev:
            events.append(ev)
    return _dedupe(events)


def _events_from_bornheim(html: str) -> list:
    events = []
    for part in re.split(r'(?=<article class="event-teaser")', html):
        if 'class="event-teaser"' not in part:
            continue
        block = part.split('<article class="event-teaser"', 1)[0] if part.startswith('</article>') else part
        dates = re.findall(r'date-card-btn-date">([^<]+)', block, re.S | re.I)
        title = re.search(r'<p>([^<]{4,160})</p>', block, re.S | re.I)
        href = re.search(r'<a[^>]+href="([^"]*/veranstaltung/veranstaltung/[^"]+)"', block, re.S | re.I)
        cat = " ".join(_clean(x) for x in re.findall(r'<span class="eventcategory">(.*?)</span>', block, re.S | re.I))
        if not (dates and title):
            continue
        title_text = _clean(title.group(1)) or _title_from_href(href.group(1) if href else "")
        for date_text in dates:
            ev = common.make_event(
                title_text, _parse_dt(date_text), None, "", "Bornheim", _clean(block),
                _abs("https://www.bornheim.de", href.group(1) if href else ""),
                "Bornheim", f"bornheim {cat} lokal markt kultur natur", 0.86,
            )
            if ev:
                events.append(ev)
    return _dedupe(events)


def _events_from_linz(html: str) -> list:
    events = []
    pat = re.compile(
        r'<a href="(?P<href>/startseite/tourismus-freizeit/veranstaltungen/events/'
        r'(?P<iso>20\d{2}-\d{2}-\d{2})-[^"]+/event\.html)">'
        r'.{0,700}?<div class="h3">\s*<a[^>]+>(?P<title>.*?)</a>',
        re.S | re.I,
    )
    for m in pat.finditer(html):
        text = _clean(m.group(0))
        ev = common.make_event(
            _clean(m.group("title")), common.parse_iso_date(m.group("iso")), None,
            "Linz am Rhein", "Linz am Rhein", text[:500], _abs("https://www.linz.de", m.group("href")),
            "Linz am Rhein", "linz mittelrhein kultur markt fest führung", 0.84, _time_text(text),
        )
        if ev:
            events.append(ev)
    return _dedupe(events)


def _range_dates(text: str):
    text = _clean(text)
    dates = re.findall(r"\d{1,2}\.\d{1,2}\.20\d{2}", text)
    if dates:
        start = common.parse_date(dates[0])
        end = common.parse_date(dates[-1]) if len(dates) > 1 else start
        return start, end
    return _parse_dt(text), None


def _events_from_bad_muenstereifel(html: str) -> list:
    events = []
    for block in re.findall(r'<div class="veranst_singleItem clearfix">(.*?)</div>\s*</div>\s*</div>',
                            html, re.S | re.I):
        date = re.search(r'veranst_singleItem_Headline_Dateline">(.*?)</div>', block, re.S | re.I)
        title = re.search(r'veranst_singleItem_Headline">(.*?)</div>', block, re.S | re.I)
        venue = re.search(r'veranst_singleItem_Ort">\s*(.*?)\s*</div>', block, re.S | re.I)
        href = re.search(r'<a[^>]+href="([^"]+)"', block, re.S | re.I)
        if not (date and title):
            continue
        start, end = _range_dates(date.group(1))
        ev = common.make_event(
            _clean(title.group(1)), start, end, _clean(venue.group(1) if venue else ""),
            "Bad Münstereifel", _clean(block), _abs("https://www.bad-muenstereifel.de", href.group(1) if href else ""),
            "Bad Münstereifel", "bad münstereifel kultur markt natur", 0.78,
        )
        if ev:
            events.append(ev)
    return _dedupe(events)


def _events_from_euskirchen(html: str) -> list:
    events = []
    for block in re.findall(r'<div class="event-list-wrapper">(.*?)</div>\s*</div>\s*</div>',
                            html, re.S | re.I):
        day = re.search(r'event-list-item-date-day">([^<]+)', block, re.S | re.I)
        month = re.search(r'event-list-item-date-month">([^<]+)', block, re.S | re.I)
        year = re.search(r'event-list-item-date-year">([^<]+)', block, re.S | re.I)
        title = re.search(r'<h3>(.*?)</h3>', block, re.S | re.I)
        subtitle = re.search(r'<h4>(.*?)</h4>', block, re.S | re.I)
        venue = re.search(r'event-detail-item-date-location">(.*?)</span>', block, re.S | re.I)
        href = re.search(r'event-list-item-text-link" href="([^"]+)"', block, re.S | re.I)
        if not (day and month and year and title):
            continue
        title_text = _clean(title.group(1))
        if subtitle:
            sub = _clean(subtitle.group(1))
            if sub and sub.lower() not in title_text.lower():
                title_text = f"{title_text}: {sub}"
        ev = common.make_event(
            title_text, _parse_dt(f"{_clean(day.group(1))} {_clean(month.group(1))} {_clean(year.group(1))}"),
            None, _clean(venue.group(1) if venue else "Stadttheater Euskirchen"),
            "Euskirchen", _clean(block), _abs("https://www.kultur-euskirchen.de", href.group(1) if href else ""),
            "Kultur Euskirchen", "theater konzert kultur comedy", 0.82,
        )
        if ev:
            events.append(ev)
    return _dedupe(events)


def _events_from_rheinbach(html: str) -> list:
    events = []
    for block in re.findall(r'<div class="row event-item.*?(?=<div class="row event-item|<button class="event-more-button")',
                            html, re.S | re.I):
        date = re.search(r'<p class="date">\s*([^<]+)', block, re.S | re.I)
        href = re.search(r'<a[^>]+href="([^"]*/veranstaltungen/veranstaltung/[^"]+)"', block, re.S | re.I)
        teaser = re.search(r'<p class="teaser">(.*?)</p>', block, re.S | re.I)
        title = _clean(teaser.group(1)) if teaser else _title_from_href(href.group(1) if href else "")
        if not (date and title):
            continue
        ev = common.make_event(
            title, _parse_dt(date.group(1)), None, "", "Rheinbach", _clean(block),
            _abs("https://www.rheinbach.de", href.group(1) if href else ""),
            "Rheinbach", "rheinbach lokal kultur markt", 0.82,
        )
        if ev:
            events.append(ev)
    return _dedupe(events)


def _events_from_detail_like_cards(html: str, source: str, base: str, city: str, item_class: str,
                                   category: str, trust: float) -> list:
    events = []
    for block in re.findall(rf'<div class="{re.escape(item_class)}".*?</div>\s*</div>\s*</div>', html, re.S | re.I):
        text = _clean(block)
        date = _parse_dt(text)
        title = re.search(r"<h3[^>]*>(.*?)</h3>", block, re.S | re.I)
        subtitle = re.search(r"<h4[^>]*>(.*?)</h4>", block, re.S | re.I)
        href = re.search(r'<a[^>]+href="([^"]+)"[^>]*>(?:Alle Informationen|Mehr lesen|.*?)</a>', block, re.S | re.I)
        venue = re.search(r'(?:event-detail-item-date-location|host-location)[^>]*>(.*?)</span>', block, re.S | re.I)
        title_text = _clean(title.group(1) if title else "")
        if subtitle:
            sub = _clean(subtitle.group(1))
            if sub and sub.lower() not in title_text.lower():
                title_text = f"{title_text}: {sub}" if title_text else sub
        if not (date and title_text):
            continue
        ev = common.make_event(
            title_text, _with_time(date, text), None, _clean(venue.group(1) if venue else ""),
            city, text[:500], _abs(base, href.group(1) if href else ""), source, category, trust,
            _time_text(text),
        )
        if ev:
            events.append(ev)
    return _dedupe(events)


def _events_from_rsf(html: str) -> list:
    events = []
    for block in re.split(r'(?=<a class="listteaser-link")', html):
        if 'class="listteaser-link"' not in block:
            continue
        href = re.search(r'href="([^"]+)"', block, re.I)
        date = re.search(r'(\d{1,2}\.\s*[A-Za-zäöüÄÖÜ]+\s*20\d{2})', _clean(block), re.I)
        title = re.search(r'<h2[^>]*class="h200"[^>]*>(.*?)</h2>', block, re.S | re.I)
        title_text = _clean(title.group(1)) if title else ""
        if not title_text or re.match(r"^\d", title_text):
            alt = re.search(r'<img[^>]+alt="([^"]+)"', block, re.S | re.I)
            title_text = _clean(alt.group(1)) if alt else _title_from_href(href.group(1) if href else "")
        if not (date and title_text):
            continue
        ev = common.make_event(
            title_text, _parse_dt(date.group(1)), None, "Rhein Sieg Forum",
            "Siegburg", _clean(block), _abs("https://www.rhein-sieg-forum.de", href.group(1) if href else ""),
            "Rhein Sieg Forum", "show comedy konzert messe kultur", 0.9,
        )
        if ev:
            events.append(ev)
    return _dedupe(events)


def _events_from_lvr(html: str) -> list:
    events = []
    for body in re.split(r'(?=<div class="event filter-element)', html):
        if 'data-filter-list=' not in body:
            continue
        text = _clean(body)
        href = re.search(r'<a class="more"[^>]+href="([^"]+)"', body, re.S | re.I)
        data = re.search(r'data-filter-list="([^"]+)"', body, re.S | re.I)
        hay = _clean(data.group(1) if data else text)
        parts = [p.strip() for p in hay.split(",")]
        title = parts[1] if len(parts) > 1 else ""
        date = re.search(r"(\d{1,2}\.\d{1,2}\.)\s*(\d{1,2}:\d{2})", hay)
        if not (title and date):
            continue
        dt = common.parse_date(f"{date.group(1)}{common.TODAY.year}")
        ev = common.make_event(
            title, _with_time(dt, date.group(2)), None, "LVR-LandesMuseum Bonn",
            "Bonn", text[:500], unescape(href.group(1)).strip() if href else "", "LVR-LandesMuseum",
            "museum ausstellung führung kino vortrag", 0.92, date.group(2),
        )
        if ev:
            events.append(ev)
    return _dedupe(events)


def _events_from_clickaround(html: str, source: str, base: str) -> list:
    events, current_date = [], None
    chunks = re.split(r'(<div class="ui dividing header">[^<]+</div>)', html)
    for chunk in chunks:
        header = re.search(r'ui dividing header">\s*([^<]+)', chunk)
        if header:
            current_date = _parse_dt(header.group(1))
            continue
        if not current_date:
            continue
        for item in re.findall(r'<div class="item">(.*?)</div>\s*</div>', chunk, re.S | re.I):
            link = re.search(r'href="([^"]+)"[^>]+aria-label="Mehr Infos - ([^"]+)"', item, re.S | re.I)
            venue = re.search(r'<b>Veranstaltungsort:</b>\s*([^<]+)', item, re.S | re.I)
            if not link:
                continue
            ev = common.make_event(
                _clean(link.group(2)), current_date, None, _clean(venue.group(1) if venue else ""),
                "Andernach", _clean(item), _abs(base, link.group(1)), source,
                "andernach kultur konzert theater open air fest", 0.84,
            )
            if ev:
                events.append(ev)
    return _dedupe(events)


def fetch_ionas4() -> list:
    source = "ionas4 regional"
    calendars = [
        ("Bad Honnef", "https://meinbadhonnef.de/kalender/veranstaltungen/events.json", 0.98),
        ("Grafschaft", "https://www.gemeinde-grafschaft.de/kalender/kalendergrafschaft/events.json", 0.9),
        ("Sinzig", "https://tourismus.sinzig.de/kalender/events.json?weekends=false&tagMode=ALL", 0.82),
    ]
    events = []
    for city, url, trust in calendars:
        try:
            items = json.loads(common.fetch_url(url, timeout=25))
            for item in items if isinstance(items, list) else []:
                title = item.get("title") or ""
                loc = item.get("location") or {}
                cat = item.get("category") or {}
                tag_text = " ".join(t.get("name", "") for t in item.get("tags") or [] if isinstance(t, dict))
                category = " ".join([cat.get("name", "") if isinstance(cat, dict) else "", tag_text,
                                     city, "kommunal lokal markt kultur"])
                start = common.parse_iso_date(item.get("start", ""))
                end = common.parse_iso_date(item.get("end", "")) or start
                ev = common.make_event(
                    title, start, end, loc.get("name") or "", city, tag_text,
                    item.get("website") or url, source, category, trust,
                )
                if ev:
                    events.append(ev)
        except Exception as e:
            common.log_source_error(f"{source} ({city})", e)
    return _dedupe(events)


def fetch_sitekit() -> list:
    source = "SiteKit regional"
    events = []
    for city, url, trust in [
        ("Brühl", "https://www.bruehl.de/tksf/veranstaltungskalender/veranstaltungskalender.php", 0.9),
        ("Wesseling", "https://www.wesseling.de/kultur-sport/veranstaltungskalender.php", 0.86),
    ]:
        try:
            events.extend(_events_from_sitekit_list(common.fetch_url(url, timeout=25), source, url, city, trust))
        except Exception as e:
            common.log_source_error(f"{source} ({city})", e)
    return _dedupe(events)


def fetch_standard_feeds() -> list:
    events = []
    try:
        html = common.fetch_url("https://www.sankt-augustin.de/kultur-freizeit/veranstaltungsuebersicht/", timeout=25)
        events.extend(common.events_from_jsonld(
            html, "Sankt Augustin", "Sankt Augustin",
            "sankt augustin lokal kultur markt open air", 0.95,
            "https://www.sankt-augustin.de/kultur-freizeit/veranstaltungsuebersicht/"))
    except Exception as e:
        common.log_source_error("Sankt Augustin", e)
    try:
        events.extend(common.fetch_ical(
            "https://termine.wir-nkse.de/termine/liste/?ical=1",
            "Neunkirchen-Seelscheid", "Neunkirchen-Seelscheid",
            "neunkirchen-seelscheid lokal markt kultur", 0.9))
    except Exception as e:
        common.log_source_error("Neunkirchen-Seelscheid", e)
    try:
        events.extend(_fetch_unkel_rss())
    except Exception as e:
        common.log_source_error("VG Unkel", e)
    return _dedupe([e for e in events if "abgesagt" not in e["title"].lower()])


def _fetch_unkel_rss() -> list:
    url = "https://rhein.info/?post_type=event&feed=eventical"
    root = ET.fromstring(common.fetch_url(url, timeout=25))
    events = []
    for item in root.findall(".//item"):
        title = item.findtext("title") or ""
        link = item.findtext("link") or url
        desc = item.findtext("description") or ""
        text = _clean(desc)
        if not any(place in f"{title} {text} {link}".lower()
                   for place in ("unkel", "rheinbreitbach", "bruchhausen", "erpel")):
            continue
        start = _parse_dt(text)
        if not start and item.findtext("pubDate"):
            start = parsedate_to_datetime(item.findtext("pubDate")).replace(tzinfo=None)
        lines = [p.strip() for p in re.split(r"<br\s*/?>", desc) if _clean(p)]
        venue = _clean(lines[1]) if len(lines) > 1 else ""
        city = _city_from_text(text, "Unkel")
        ev = common.make_event(
            title, _with_time(start, text), None, venue, city, text, link, "VG Unkel",
            "unkel mittelrhein kultur konzert markt", 0.86, _time_text(text),
        )
        if ev:
            events.append(ev)
    return events


def fetch_naturregion_sieg() -> list:
    source = "Naturregion Sieg"
    url = "https://naturregion-sieg.de/service/veranstaltungskalender"
    try:
        html = common.fetch_url(url, timeout=25)
        return _events_from_site_like_anchor_text(html, source, "https://naturregion-sieg.de", "Windeck",
                                                  "naturregion sieg windeck hennef eitorf outdoor kultur", 0.9)
    except Exception as e:
        common.log_source_error(source, e)
        return []


def _events_from_site_like_anchor_text(html: str, source: str, base: str, city: str,
                                       category: str, trust: float) -> list:
    events = []
    for href, body in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.S | re.I):
        segments = [_clean(s) for s in re.split(r"<[^>]+>", body) if _clean(s)]
        text = _clean(body)
        if not re.search(r"\d{1,2}\.\d{1,2}\.20\d{2}", text):
            continue
        date = _parse_dt(text)
        date_idx = next((i for i, part in enumerate(segments)
                         if re.search(r"\d{1,2}\.\d{1,2}\.20\d{2}", part)), -1)
        title = segments[date_idx + 1] if date_idx >= 0 and date_idx + 1 < len(segments) else ""
        venue = segments[date_idx + 2] if date_idx >= 0 and date_idx + 2 < len(segments) else ""
        if not title:
            title = re.sub(r"^\d{1,2}\.\d{1,2}\.20\d{2}\s*", "", text).strip()
            title = re.split(r"\s{2,}| {8,}", title)[0].strip() or title[:100]
        if len(title) > 100 or title.lower() in {"mehr lesen", "details"}:
            title = _title_from_href(href)
        if len(title) < 4:
            continue
        event_city = _city_from_text(f"{venue} {text}", city)
        ev = common.make_event(
            title, _with_time(date, text), None, venue or event_city, event_city, text[:500],
            _abs(base, href), source, category, trust, _time_text(text),
        )
        if ev:
            events.append(ev)
    return _dedupe(events)


def fetch_html_sources() -> list:
    events = []
    source_urls = [
        ("Swisttal", "https://www.swisttal.de/veranstaltungen/", "Swisttal",
         "swisttal lokal markt kultur konzert fest", 0.86, ""),
        ("Alfter", "https://www.alfter.de/schnellzugriff/veranstaltungen/", "Alfter",
         "alfter lokal kultur markt fest", 0.84,
         r'<h3>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>\s*</h3>'),
        ("Lohmar", "https://www.lohmar.de/erlebnisfaktoren-natur-und-sport-freizeit-und-tourismus/veranstaltungen/",
         "Lohmar", "lohmar lokal natur kultur markt", 0.84,
         r'<h3>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>\s*</h3>'),
    ]
    for name, url, city, category, trust, pattern in source_urls:
        try:
            if name == "Swisttal":
                events.extend(_events_from_swisttal(common.fetch_url(url, timeout=25)))
            else:
                events.extend(_events_from_time_page(url, name, city, category, trust, pattern))
        except Exception as e:
            common.log_source_error(name, e)
    try:
        events.extend(_events_from_bornheim(
            common.fetch_url("https://www.bornheim.de/veranstaltungskalender", timeout=25)))
    except Exception as e:
        common.log_source_error("Bornheim", e)
    try:
        events.extend(_events_from_eitorf_cards(
            common.fetch_url("https://www.eitorf.de/veranstaltungen/", timeout=25),
            "Eitorf", "https://www.eitorf.de"))
    except Exception as e:
        common.log_source_error("Eitorf", e)
    try:
        events.extend(_events_from_broeltal(
            common.fetch_url("https://www.broeltal.de/aktuelles/termine.html", timeout=25),
            "Bröltal / Ruppichteroth", "https://www.broeltal.de"))
    except Exception as e:
        common.log_source_error("Bröltal / Ruppichteroth", e)
    return _dedupe(events)


def fetch_deskline_and_venues() -> list:
    events = []
    try:
        events.extend(_events_from_shapehub(
            common.fetch_url("https://www.ahrtal.com/de/events", timeout=25),
            "Ahrtal", "https://www.ahrtal.com", "Ahrweiler",
            "ahrtal wein wanderung führung kultur ausstellung", 0.86))
    except Exception as e:
        common.log_source_error("Ahrtal", e)
    try:
        events.extend(_events_from_linz(
            common.fetch_url("https://www.linz.de/startseite/tourismus-freizeit/veranstaltungen", timeout=25)))
    except Exception as e:
        common.log_source_error("Linz am Rhein", e)
    try:
        events.extend(_events_from_bad_muenstereifel(
            common.fetch_url("https://www.bad-muenstereifel.de/tourismus-freizeit/veranstaltungskalender", timeout=25)))
    except Exception as e:
        common.log_source_error("Bad Münstereifel", e)
    try:
        events.extend(_events_from_euskirchen(
            common.fetch_url("https://www.kultur-euskirchen.de/stadttheater/veranstaltungen", timeout=25)))
    except Exception as e:
        common.log_source_error("Kultur Euskirchen", e)
    return _dedupe(events)


def fetch_more_venues() -> list:
    events = []
    try:
        events.extend(_events_from_rsf(common.fetch_url("https://www.rhein-sieg-forum.de/de/programm", timeout=25)))
    except Exception as e:
        common.log_source_error("Rhein Sieg Forum", e)
    try:
        events.extend(_events_from_rheinbach(
            common.fetch_url("https://www.rheinbach.de/veranstaltungen", timeout=25)))
    except Exception as e:
        common.log_source_error("Rheinbach", e)
    try:
        events.extend(_events_from_arp(
            common.fetch_url("https://arpmuseum.org/veranstaltungen.html", timeout=25)))
    except Exception as e:
        common.log_source_error("Arp Museum", e)
    try:
        url = "https://events.click-around.systems/core/19b47bb1-7fba-40a0-a4a8-8d35589b4fce/events/standard/de"
        events.extend(_events_from_clickaround(common.fetch_url(url, timeout=25), "Andernach", url))
    except Exception as e:
        common.log_source_error("Andernach", e)
    try:
        events.extend(_events_from_lvr(
            common.fetch_url("https://landesmuseum-bonn.lvr.de/de/veranstaltungen/veranstaltungen_2/alleveranstaltungen.html",
                             timeout=25)))
    except Exception as e:
        common.log_source_error("LVR-LandesMuseum", e)
    return _dedupe(events)


def _events_from_arp(html: str) -> list:
    events = []
    for block in re.findall(r'<a href="([^"]*/veranstaltungen/detail/[^"]+)">(.*?)(?=<a href="[^"]*/veranstaltungen/detail/|</ul>|</section>)',
                            html, re.S | re.I):
        href, body = block
        date = re.search(r'va-date-block"><span>(\d{1,2})\s+([A-Za-z]+)</span>\s*(20\d{2})', body, re.S | re.I)
        title = re.search(r'<h3 class="va-title">(.*?)</h3>', body, re.S | re.I)
        typ = re.search(r'<p class="va-type">(.*?)</p>', body, re.S | re.I)
        if not (date and title):
            continue
        dt = _parse_dt(f"{date.group(1)} {date.group(2)} {date.group(3)}")
        ev = common.make_event(
            _clean(title.group(1)), dt, None, "Arp Museum Bahnhof Rolandseck",
            "Remagen", _clean(typ.group(1) if typ else ""), _abs("https://arpmuseum.org", href),
            "Arp Museum", "museum ausstellung führung workshop kultur", 0.9,
        )
        if ev:
            events.append(ev)
    return _dedupe(events)
