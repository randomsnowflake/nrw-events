"""Share only unambiguous place facts across a Marktcom recurring detail URL."""
import re
from urllib.parse import urlsplit

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
    """Do not export rolling-page dates, hours, prices or occurrence-specific prose."""
    link = str(event.get('link') or '')
    host, path = _url_key(link)
    if event.get('source_id') != 'marktcom' or host != 'marktcom.de' or not path.startswith('/veranstaltung/'):
        return {}
    title = str(event.get('title') or '').casefold()
    city = str(event.get('city') or '').strip()
    locations: set[tuple[str, str]] = set()
    for item in jsonld_event_items(document):
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
    return {'venue': venue, 'venue_address': address}
