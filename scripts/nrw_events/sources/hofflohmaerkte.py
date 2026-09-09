"""Neighborhood courtyard flea markets published by Hofflohmärkte Köln."""

import re
import html as html_lib
import unicodedata
from urllib.parse import urlsplit

from .. import common, http
from ..dates import MONTH_DE
from . import regional_common as rc

_URL = "https://www.hofflohmaerkte.de/pages/hofflohmarkte-koln"
_DATE_PATTERN = re.compile(
    r"(?:Sa|So)\.\s*(\d{1,2})\.\s*([A-Za-zäöüÄÖÜ]+)\s+(20\d{2})\s*"
    r"·\s*(\d{1,2})\s*-\s*(\d{1,2})\s*Uhr\s*·\s*<strong>(.*?)(?:<br\s*/?>|</strong>)",
    re.S | re.I,
)


def _neighborhood_key(value: str) -> str:
    value = value.casefold().replace("ß", "ss").replace("ü", "ue").replace("ö", "oe").replace("ä", "ae")
    value = "".join(char for char in unicodedata.normalize("NFKD", value) if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]", "", value.replace(" & ", "").replace(" / ", ""))


def _tour_plan(html: str, neighborhood: str, start) -> str:
    # Only this neighbourhood AND occurrence date may select a PDF. In
    # particular, last year's tour plan must never become the current source.
    date_token = start.strftime("%d%m%y")
    key = _neighborhood_key(neighborhood)
    for href in re.findall(r'href=["\']([^"\']+)["\']', html, re.I):
        url = html_lib.unescape(href)
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.hostname != "cdn.shopify.com":
            continue
        filename = parts.path.rsplit("/", 1)[-1]
        match = re.fullmatch(r"hofflohmaerkte-(.+)-(\d{6})\.pdf", filename, re.I)
        if match and match[2] == date_token and _neighborhood_key(match[1]) == key:
            return url
    return ""


def _visitor_context(html: str) -> list[str]:
    # Read the actual visitor introduction, not the seller checkout, consent
    # text, navigation, or the list of dates for other neighbourhoods.
    paragraphs = [rc.clean(value) for value in re.findall(r"<p\b[^>]*>(.*?)</p>", html, re.S | re.I)]
    introduction = next((p for p in paragraphs if "Hausanwohner" in p and "Hof oder Garten" in p), "")
    introduction = introduction.split("Auf dieser Seite", 1)[0].strip()
    tour_notice = next((p for p in paragraphs if "Tourpläne" in p and "veröffentlichen" in p), "")
    return [p for p in (introduction, tour_notice) if p]


def _events_from_page(html: str) -> list:
    events = []
    context = _visitor_context(html)
    for match in _DATE_PATTERN.finditer(html or ""):
        day, month_name, year, start_hour, end_hour, neighborhood_html = match.groups()
        month = MONTH_DE.get(month_name.casefold().rstrip("."))
        if not month:
            continue
        start = common.parse_date(f"{int(day):02d}.{month:02d}.{year}")
        neighborhood = rc.clean(neighborhood_html)
        if not (start and neighborhood):
            continue
        city = "Frechen" if "frechen" in neighborhood.casefold() else "Köln"
        time_text = f"{int(start_hour):02d}:00–{int(end_hour):02d}:00"
        title = f"Hofflohmarkt {neighborhood}"
        description = (
            f"Beim Hofflohmarkt in {neighborhood} verkaufen Hausanwohnerinnen und "
            "Hausanwohner auf ihren eigenen Höfen und in ihren Gärten."
        )
        tour_plan = _tour_plan(html, neighborhood, start)
        if context:
            description = f"Hofflohmarkt in {neighborhood}.\n\n" + "\n\n".join(context[:1])
        if tour_plan:
            description += "\n\nDer Tourplan für diesen Termin ist veröffentlicht. Er zeigt die teilnehmenden Höfe und hilft bei der Planung des Rundgangs durch das Viertel. Den Plan erreichst du über die Originalquelle."
        elif len(context) > 1:
            description += "\n\n" + context[1]
        event = common.make_event(
            title,
            start,
            None,
            neighborhood,
            city,
            description,
            tour_plan or _URL,
            "Hofflohmärkte Köln",
            "hofflohmarkt flohmarkt nachbarschaft markt",
            0.94,
            time_text,
        )
        if event:
            event["link_kind"] = "detail" if tour_plan else "overview"
            event["source_links"] = list(dict.fromkeys([_URL, tour_plan or _URL]))
            events.append(event)
    return rc.dedupe(events)


def _fetch_page(url: str, timeout: int = 20) -> str:
    return http.fetch_url_with_brightdata_fallback(
        url,
        timeout=timeout,
        allowed_hosts=("www.hofflohmaerkte.de",),
        required_body_markers=("Hofflohmärkte Köln",),
        fallback_on_timeout=True,
    )


def fetch() -> list:
    return rc.fetch_html_events(
        "Hofflohmärkte Köln",
        _URL,
        _events_from_page,
        timeout=20,
        fetcher=_fetch_page,
 source_id="hofflohm-rkte-k-ln")
