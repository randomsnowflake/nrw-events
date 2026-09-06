#!/usr/bin/env python3
"""Offline, bounded repository context and recorded event inspection."""
from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
MAP = ROOT / 'docs/task-map.json'
FIELDS = ('event_id', 'title', 'start_date', 'date', 'end_date', 'time', 'venue', 'city',
          'status', 'source_id', 'source', 'link', 'source_links', 'discovered_via',
          'previous_event_ids', 'description_source', 'category_key', 'price', 'admission',
          'archived_first_seen', 'archived_last_seen', 'archived_transition_at')


def read_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def topics(root: Path = ROOT) -> dict:
    return read_json(root / 'docs/task-map.json')


def validate_map(root: Path = ROOT) -> list[str]:
    errors = []
    for name, item in topics(root).items():
        for field in ('files', 'tests', 'docs', 'fixtures'):
            for value in item.get(field, []):
                path = root / value
                if not path.exists() or not path.resolve().is_relative_to(root.resolve()):
                    errors.append(f'{name}.{field}: invalid path {value}')
    return errors


def source_context(source_id: str, root: Path = ROOT) -> dict:
    registry = read_json(root / 'scripts/nrw_events/sources/registry.json')['sources']
    source = next((row for row in registry if row['id'] == source_id), None)
    if source is None:
        raise ValueError(f'Unknown source ID: {source_id}; see docs/sources.md')
    owner = source.get('callable', '').split(':')[0]
    files = ['scripts/nrw_events/sources/registry.json', 'scripts/nrw_events/source_specs.py']
    if owner:
        files.append('scripts/' + owner.replace('.', '/') + '.py')
    else:
        adapter = source['adapter']
        if adapter in ('ical', 'json_ld'):
            files.append('scripts/nrw_events/' + ('ical.py' if adapter == 'ical' else 'jsonld.py'))
    missing = [p for p in files if not (root / p).is_file()]
    if missing:
        raise ValueError(f'Stale source owner paths: {missing}')
    # References are discovery evidence, not a claim of complete test coverage.
    needles = {source_id, source['display_name']}
    if owner:
        needles.update((owner, 'sources.' + owner.rsplit('.', 1)[-1]))
    references = []
    for path in sorted((root / 'tests').rglob('*.py')):
        lines = [index for index, line in enumerate(path.read_text().splitlines(), 1)
                 if any(needle in line for needle in needles)]
        if lines:
            references.append({'path': str(path.relative_to(root)), 'lines': lines[:6]})
    manifest = read_json(root / 'tests/fixtures/manifest.json').get('sources', {})
    fixtures = manifest.get(source_id, [])
    test_files = [item['path'] for item in references if Path(item['path']).name.startswith('test_')]
    modules = [path[:-3].replace('/', '.') for path in test_files]
    return {'source': source, 'files': files, 'test_references': references[:12], 'test_reference_count': len(references), 'omitted_test_references': max(0, len(references) - 12),
            'coverage_note': 'Text references are candidates; shared pipeline tests may also apply. Inline fixtures live in these tests.',
            'fixtures': [{'url': f['url'], 'path': 'tests/fixtures/' + f['path']} for f in fixtures],
            'commands': ['bash scripts/test.sh --agent ' + ' '.join(map(shlex.quote, modules[:12]))] if modules else [],
            'docs': ['docs/ARCHITECTURE.md', 'docs/performance.md']}


def query_key(query: str) -> str:
    path = urlsplit(query).path if '://' in query or query.startswith('/') else query
    return unquote(path).rstrip('/').rsplit('/', 1)[-1]


def inspect_snapshot(query: str, snapshot: Path, full: bool = False, limit: int = 10) -> dict:
    payload = read_json(snapshot)
    if not isinstance(payload, dict) or not isinstance(payload.get('events'), list):
        raise ValueError(f'{snapshot}: expected a snapshot object with an events list')
    key = query_key(query)
    matches = []
    for section in ('events', 'early_announcements'):
        for record in payload.get(section, []):
            ids = [record.get('event_id'), record.get('id'), *record.get('previous_event_ids', [])]
            urls = [record.get('link'), *record.get('source_links', [])]
            if key in ids or query in urls:
                matches.append({'section': section, 'record': record if full else {k: record[k] for k in FIELDS if k in record}})
    return {'snapshot': str(snapshot.resolve()), 'generated_at': payload.get('generated_at', payload.get('updated_at')),
            'run_id': payload.get('run_id'), 'match_count': len(matches), 'matches': matches[:limit],
            'omitted': max(0, len(matches) - limit)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    context = sub.add_parser('context')
    context.add_argument('topic', nargs='?')
    context.add_argument('source_id', nargs='?')
    sub.add_parser('check')
    inspect = sub.add_parser('inspect')
    inspect.add_argument('query')
    inspect.add_argument('--snapshot', required=True, type=Path)
    inspect.add_argument('--stage', action='append', type=Path, default=[])
    inspect.add_argument('--full', action='store_true')
    inspect.add_argument('--limit', type=int, default=10)
    args = parser.parse_args()
    try:
        if args.command == 'check':
            errors = validate_map()
            if errors:
                raise ValueError('\n'.join(errors))
            output = {'ok': True, 'topics': list(topics())}
        elif args.command == 'context':
            if args.topic == 'source':
                if not args.source_id:
                    raise ValueError('context source requires an exact registry SOURCE_ID')
                output = source_context(args.source_id)
            elif args.topic is None:
                output = {'topics': list(topics()), 'source': 'context source SOURCE_ID'}
            else:
                output = topics().get(args.topic)
                if output is None:
                    raise ValueError(f'Unknown topic: {args.topic}')
        else:
            if not 1 <= args.limit <= 100:
                raise ValueError('--limit must be between 1 and 100')
            stages = [inspect_snapshot(args.query, path, args.full, args.limit) for path in [args.snapshot, *args.stage]]
            output = {'query': args.query, 'stages': stages, 'note': 'Only supplied recorded snapshots were read; no stages were reconstructed or fetched.'}
            sources = sorted({match['record'].get('source_id', '') for stage in stages for match in stage['matches']} - {''})
            output['source_context_commands'] = [f'python3 scripts/agent_tools.py context source {shlex.quote(source)}' for source in sources]
            if not any(stage['match_count'] for stage in stages):
                print(json.dumps(output, ensure_ascii=False, indent=2))
                return 1
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.exit(2, f'{error}\n')


if __name__ == '__main__':
    sys.exit(main())
