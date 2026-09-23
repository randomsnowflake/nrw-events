"""Conservative publication guard for malformed place fields, without guessed places."""

from __future__ import annotations

import re
from html import escape

_URL = re.compile(r'https?://[^\s<>]+', re.I)
_PLACEHOLDER = re.compile(
    r'^(?:wird\s+(?:(?:noch|später)\s+|auf\s+der\s+(?:Homepage|Website)\s+)?'
    r'bekannt\s*gegeben|noch\s+nicht\s+bekannt|Ort\s+folgt|siehe\s+(?:Homepage|Website)'
    r'|(?:Der\s+)?Veranstaltungsort\s+ist\s+(?:der\s+)?(?:Start|Ausgangs|Treff)punkt(?:\s+(?:der|des)\s+\w+)?)\.?$', re.I,
)


def invalid_venue_reason(value: str) -> str:
    value = value.strip()
    if not value:
        return ''
    if _URL.search(value):
        return 'url'
    if len(value) == 1:
        return 'fragment'
    if re.search(r'(?:…|\.{3})(?:\s*\d+)?$', value):
        return 'truncated'
    if _PLACEHOLDER.fullmatch(value):
        return 'placeholder'
    if re.match(r'^Gemeinsame\s+Anfahrt\b', value, re.I):
        return 'journey'
    return ''


def source_venue_value(value: str) -> str:
    """Return a publishable place name, or empty when the source string is malformed."""
    value = (value or '').strip()
    return '' if invalid_venue_reason(value) else value


def retain_omitted_source_place(event: dict, value: str) -> None:
    """Keep identity and visitor notes after omitting a malformed source place."""
    value = str(value or '').strip()
    reason = invalid_venue_reason(value)
    if not reason:
        return
    if not event.get('identity_venue_locked'):
        event['identity_venue'] = value
        event['identity_venue_locked'] = True
    _append_place_notes(event, reason, value, list(dict.fromkeys(_URL.findall(value))))


def _append_place_notes(event: dict, reason: str, value: str, urls: list[str]) -> None:
    notes = [f'Karte / Ortsinformation: {url}' for url in urls]
    if reason in {'placeholder', 'journey'}:
        notes.append(('Ortsangabe: ' if reason == 'placeholder' else 'Anreise: ') + value)
    for note in notes:
        description = str(event.get('description') or '')
        if note not in description.split('\n\n'):
            event['description'] = '\n\n'.join(filter(None, (description, note)))
            if event.get('description_html'):
                event['description_html'] += '<p>' + escape(note) + '</p>'


def sanitize_venue_fields(event: dict) -> None:
    """Omit malformed names; retain useful source notes and the old URL identity."""
    value = str(event.get('venue') or '').strip()
    reason = invalid_venue_reason(value)
    address = str(event.get('venue_address') or '')
    urls = list(dict.fromkeys(_URL.findall(value + ' ' + address)))
    if not reason and not urls:
        return
    if reason:
        retain_omitted_source_place(event, value)
        event['venue'] = ''
        for key in ('venue_id', 'venue_district', 'venue_type'):
            event[key] = ''
        for key in ('venue_latitude', 'venue_longitude'):
            event[key] = None
    if urls:
        event['venue_address'] = _URL.sub('', address).strip(' ,')
    _append_place_notes(event, reason, value, urls)
    warnings = list(event.get('quality_warnings') or [])
    warning = {
        'rule_id': 'publication.invalid-venue',
        'field': 'venue' if reason else 'venue_address',
        'resolution': 'unknown' if reason else 'url_removed',
        'message': f'Malformed location ({reason or "url"}): {value or address}',
    }
    if warning not in warnings:
        warnings.append(warning)
    event['quality_warnings'] = warnings
