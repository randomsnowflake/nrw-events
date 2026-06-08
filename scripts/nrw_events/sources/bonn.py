"""
Bonn.de — the city's official event channels.

Three fetchers, all reading bonn.de:
  fetch_html()             — Veranstaltungskalender HTML listing (event links)
  fetch_rss()              — the same calendar as an RSS feed
  fetch_press_festivals()  — the annual "Veranstaltungsjahr" press release, which
                             lists district festivals / markets / Kirmes as <li>
                             items. This is the *live* replacement for the old
                             hardcoded district-festival table — no baked dates.
"""

import re
from datetime import datetime
from html import unescape

from .. import common

_HTML_URL = "https://www.bonn.de/bonn-erleben/ausgehen-und-erleben/veranstaltungskalender.php"
_RSS_URL = (_HTML_URL + "?sp%3Aout=rss&sp%3Acmp=search-1-0-searchResult&action=submit")
# Annual press release. The slug embeds the year; we build it dynamically so the
# source keeps working in future years with no code change (no dates hardcoded).
_PRESS_URL_TEMPLATE = (
    "https://www.bonn.de/pressemitteilungen/dezember/"
    "abwechslungsreiches-veranstaltungsjahr-{year}-in-bonn.php"
)


def fetch_html() -> list:
    source = "Bonn.de"
    try:
        html = common.fetch_url(_HTML_URL)
        events = []
        pattern = r'<a[^>]*href="(/veranstaltungskalender/[^"]+?)"[^>]*>(.*?)</a>'
        for href, text in re.findall(pattern, html, re.DOTALL):
            clean = unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip())
            if "speichern" in clean.lower() or len(clean) < 10:
                continue

            cat_match = re.match(r"^([\w/|]+(?:\s*\|\s*[\w/]+)*)\s*", clean)
            category = cat_match.group(1) if cat_match else ""
            dates = re.findall(r"(\d{2}\.\d{2}\.\d{4})", clean)

            title_part = clean[len(category):].strip() if category else clean
            title_part = re.sub(r"\d{2}\.\d{2}\.\d{4}\s*\d{0,2}:?\d{0,2}\s*(?:Uhr)?\s*,?\s*", "", title_part)
            title_part = re.sub(r"[\xa0 ]", " ", title_part)
            title_part = re.sub(r"^[\s.,;]*\.{3}\s*", "", title_part)
            title_part = re.sub(r"^\.\.\.\s*", "", title_part).strip()

            for sep in [" Bei der ", " Die ", " Spannende ", " Im Rahmen ", " Informieren ",
                        " Auf dieser ", " Eine ", " Das ", " Monatlicher "]:
                if sep in title_part and len(title_part.split(sep)[0]) > 10:
                    title_part = title_part.split(sep)[0]
                    break
            if len(title_part) > 80:
                for brk in [" - ", " – ", " | ", ". "]:
                    if brk in title_part[:80]:
                        break
                else:
                    title_part = title_part[:80]
            if not title_part or len(title_part) < 3:
                continue

            if dates and not any(common.in_date_range(d) for d in dates):
                continue

            full_text = f"{category} {title_part}"
            events.append({
                "title": title_part[:120],
                "date": dates[0] if dates else "",
                "time": "", "venue": "", "city": "Bonn", "description": "", "price": "",
                "link": f"https://www.bonn.de{href}",
                "distance_km": 0,
                "score": round(common.distance_score(0) * common.category_score(full_text), 2),
                "source": source,
                "category": category,
            })
        return events
    except Exception as e:
        common.log_source_error(source, e)
        return []


def fetch_rss() -> list:
    import xml.etree.ElementTree as ET
    source = "Bonn.de RSS"
    try:
        root = ET.fromstring(common.fetch_url(_RSS_URL))
        events = []
        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            if not title:
                continue
            pub_date = (item.findtext("pubDate") or "").strip()
            if pub_date and not common.in_date_range(pub_date):
                continue
            desc = (item.findtext("description") or "").strip()
            full_text = f"{title} {desc}"
            events.append({
                "title": unescape(title),
                "date": pub_date[:16] if pub_date else "",
                "time": "", "venue": "", "city": "Bonn",
                "description": unescape(re.sub(r"<[^>]+>", "", desc)) if desc else "",
                "price": "",
                "link": (item.findtext("link") or "").strip(),
                "distance_km": 0,
                "score": round(common.distance_score(0) * common.category_score(full_text), 2),
                "source": source,
                "category": "",
            })
        return events
    except Exception as e:
        common.log_source_error(source, e)
        return []


def fetch_press_festivals() -> list:
    """Parse the annual Bonn 'Veranstaltungsjahr' press release for district festivals.

    Each <li> looks like: "<name>, <venue...>, <date>, <date>, … <year>".
    We extract the name + every in-window date and emit one event per date. This
    surfaces Stadtteilfeste / Kirmes / markets that never reach the clean APIs —
    fully live, no event names or dates hardcoded in the script.
    """
    source = "Bonn district festivals"
    # Try this year; from October onward also try next year's edition (published early).
    years = [common.TODAY.year]
    if common.TODAY.month >= 10:
        years.append(common.TODAY.year + 1)

    events = []
    for year in years:
        url = _PRESS_URL_TEMPLATE.format(year=year)
        try:
            html = common.fetch_url(url, timeout=20)
        except Exception:
            continue  # edition not published / slug changed → just skip
        for li in re.findall(r"<li>(.*?)</li>", html, re.S):
            text = common.clean_html(li)
            if len(text) < 6:
                continue
            dates = []
            for m in re.finditer(
                r"(\d{1,2})\.\s*(Januar|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember)\s*(20\d{2})?",
                text,
            ):
                day, mon, yr = m.groups()
                try:
                    dates.append(datetime(int(yr or year), common.MONTH_DE[mon.lower()], int(day)))
                except (ValueError, KeyError):
                    continue
            in_window = [d for d in dates if common.TODAY <= d <= common.END_DATE]
            if not in_window:
                continue
            title = re.split(r",", text)[0].strip()
            if len(title) < 3:
                continue
            city = common.guess_city_from_text(text) or "Bonn"
            for d in sorted(set(in_window)):
                ev = common.make_event(
                    title, d, d, "", city, text[:240], url, source,
                    "stadtteilfest market kirmes outdoor local", 1.0,
                )
                if ev:
                    events.append(ev)
    return events
