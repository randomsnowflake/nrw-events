"""First-party event dates from KG Quer durch de Waat's public website."""

from __future__ import annotations

from datetime import datetime
import html as html_lib
import json
import re

from .. import common


SOURCE = "KG Quer durch de Waat"
SOURCE_ID = "qddw"
POSTS_URL = (
    "https://quer-durch-de-waat.de/wp-json/wp/v2/posts"
    "?search=Frohes%20Neues%20Jahr&per_page=10&_fields=title,link,date"
)

_MONTHS = {
    "januar": 1,
    "februar": 2,
    "märz": 3,
    "maerz": 3,
    "april": 4,
    "mai": 5,
    "juni": 6,
    "juli": 7,
    "august": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "dezember": 12,
}
_NEW_YEAR_TITLE = re.compile(r"\bFrohes\s+Neues\s+Jahr\s+(20\d{2})\b", re.I)
_WARTHER_KIRMES = re.compile(
    r"\bWarther\s+Kirmes\s+(\d{1,2})\.\s*[-–]\s*(\d{1,2})\.\s*"
    r"(Januar|Februar|März|Maerz|April|Mai|Juni|Juli|August|September|"
    r"Oktober|November|Dezember)\b",
    re.I,
)


def _rendered_title(post: dict) -> str:
    title = post.get("title", "")
    if isinstance(title, dict):
        title = title.get("rendered", "")
    return common.clean_html(str(title or ""))


def _current_annual_page(posts: object) -> tuple[int, str] | None:
    if not isinstance(posts, list):
        return None
    current_year = common.TODAY.year
    for post in posts:
        if not isinstance(post, dict):
            continue
        match = _NEW_YEAR_TITLE.search(_rendered_title(post))
        link = str(post.get("link") or "").strip()
        if match and int(match.group(1)) == current_year and link.startswith("https://quer-durch-de-waat.de/"):
            return current_year, link
    return None


def events_from_page(page_html: str, *, year: int, source_url: str) -> list:
    text = re.sub(r"<br\s*/?>", "\n", page_html, flags=re.I)
    text = html_lib.unescape(common.clean_html(text))
    match = _WARTHER_KIRMES.search(text)
    if not match:
        return []

    start_day, end_day = int(match.group(1)), int(match.group(2))
    month_name = match.group(3).casefold()
    month = _MONTHS[month_name]
    try:
        start = datetime(year, month, start_day)
        end = datetime(year, month, end_day)
    except ValueError:
        return []
    if end < start:
        return []

    description = (
        f"Die Warther Kirmes findet vom {start_day}. bis {end_day}. "
        f"{match.group(3)} {year} auf dem Kirmesplatz in Hennef-Warth statt."
    )
    event = common.make_event(
        "Warther Kirmes",
        start,
        end,
        "Kirmesplatz Warth",
        "Hennef",
        description,
        source_url,
        SOURCE,
        "Kirmes Volksfest",
        trust=1.0,
        all_day=True,
        source_id=SOURCE_ID,
        description_source="generated",
    )
    return [event] if event else []


def fetch() -> list:
    payload = json.loads(common.fetch_url(POSTS_URL, timeout=20))
    annual_page = _current_annual_page(payload)
    if annual_page is None:
        return []
    year, source_url = annual_page
    page_html = common.fetch_url(source_url, timeout=20)
    return events_from_page(page_html, year=year, source_url=source_url)
