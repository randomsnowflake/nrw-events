"""Extract place facts and occurrence-specific hours from a Marktcom detail URL."""
import re
from datetime import date, datetime
from urllib.parse import parse_qs, urljoin, urlsplit
from zoneinfo import ZoneInfo

from ..detail_types import DetailContext
from ..jsonld import jsonld_event_items
from ..models import RawEvent
from ..text import clean_html

_GENERIC_MARKET = re.compile(
    r'(?:Trödelmarkt|Troedelmarkt|Flohmarkt|Antikmarkt|Antik[- ] und Trödelmarkt|'
    r'Antik[- ]Trödelmarkt|Floh[- /] und Trödelmarkt|Floh[- /]Trödelmarkt)', re.I,
)


def generic_market_name(value: str, city: str = '') -> bool:
    """Recognize bare formats, optionally followed by the listing municipality."""
    value = value.strip()
    if city and value.casefold().endswith(' ' + city.casefold()):
        value = value[:-len(city)].strip()
    return bool(_GENERIC_MARKET.fullmatch(value))


def _url_key(value: str) -> tuple[str, str]:
    parsed = urlsplit(value)
    return (parsed.hostname or '').removeprefix('www.'), parsed.path.rstrip('/')


def location_context(document: str, event: RawEvent) -> DetailContext:
    """Use programme rows for hours because the page header follows the selected occurrence."""
    link = str(event.get('link') or '')
    host, path = _url_key(link)
    if event.get('source_id') != 'marktcom' or host != 'marktcom.de' or not path.startswith('/veranstaltung/'):
        return {}
    title = str(event.get('title') or '').casefold()
    city = str(event.get('city') or '').strip()
    locations: set[tuple[str, str]] = set()
    for item in jsonld_event_items(document, optional=True):
        if not isinstance(item.get('url'), str) or _url_key(item['url']) != (host, path):
            continue
        name = clean_html(str(item.get('name') or '')).strip().casefold()
        if not name or not (title == name or title.startswith(name + ' ')):
            continue
        location = item.get('location')
        if not isinstance(location, dict):
            return {}
        address = location.get('address')
        if not isinstance(address, dict):
            return {}
        street, postal, locality = (
            clean_html(str(address.get(key) or '')).strip()
            for key in ('streetAddress', 'postalCode', 'addressLocality')
        )
        # Require the same municipality and a complete street address, not an
        # organizer's contact address or a partially parsed Place.
        if not street or not re.fullmatch(r'\d{5}', postal) or not city or not re.match(
            re.escape(city) + r'(?:$|[\s,\-/])', locality, re.I,
        ):
            return {}
        venue = clean_html(str(location.get('name') or '')).strip()
        if generic_market_name(venue, city):
            venue = ''
        locations.add((venue, ' '.join((street, postal, locality))))
    if len(locations) != 1:
        return {}
    venue, address = locations.pop()
    context: DetailContext = {'venue': venue, 'venue_address': address}
    context.update(_dated_hours(document, link, event))
    return context


def _dated_hours(document: str, link: str, event: RawEvent) -> DetailContext:
    event_date = event.get('start_date') or event.get('date', '')
    try:
        day = date.fromisoformat(event_date)
    except ValueError:
        return {}
    date_label = day.strftime('%d.%m.%Y')
    term = parse_qs(urlsplit(link).query).get('term_id')
    times = set()
    for row in re.findall(r'<tr\b[^>]*>(.*?)</tr>', document, re.I | re.S):
        cells = re.findall(r'<td\b[^>]*>(.*?)</td>', row, re.I | re.S)
        if len(cells) != 2 or not clean_html(cells[0]).strip().endswith(date_label):
            continue
        href = re.search(r'<a\b[^>]*\bhref=["\']([^"\']+)["\']', cells[0], re.I)
        if not href:
            continue
        row_url = urljoin(link, href.group(1))
        row_term = parse_qs(urlsplit(row_url).query).get('term_id')
        if _url_key(row_url) != _url_key(link) or not row_term or (term and row_term != term):
            continue
        time_range = re.fullmatch(
            r'((?:[01]\d|2[0-3]):[0-5]\d)\s*[-–—]\s*((?:[01]\d|2[0-3]):[0-5]\d)\s*Uhr',
            clean_html(cells[1]).strip(), re.I,
        )
        if time_range and time_range[1] < time_range[2]:
            times.add((time_range[1], time_range[2]))
    if len(times) != 1:
        return {}
    start, end = times.pop()
    zone = ZoneInfo('Europe/Berlin')
    return {
        'time': f'{start}–{end}',
        'start_at': datetime.fromisoformat(f'{day}T{start}').replace(tzinfo=zone).isoformat(timespec='minutes'),
        'end_at': datetime.fromisoformat(f'{day}T{end}').replace(tzinfo=zone).isoformat(timespec='minutes'),
    }
