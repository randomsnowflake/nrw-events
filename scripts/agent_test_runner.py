#!/usr/bin/env python3
"""Keep the canonical shell test gate intact, retaining logs behind a short summary."""
import json
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(command, directory):
    directory.mkdir(parents=True, exist_ok=True)
    log = directory / 'tests.log'
    started = time.monotonic()
    with log.open('w') as stream:
        child = subprocess.Popen(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
        try:
            while True:
                try:
                    code = child.wait(timeout=30)
                    break
                except subprocess.TimeoutExpired:
                    print(f'[tests] running {time.monotonic() - started:.0f}s; log: {log}', flush=True)
        except KeyboardInterrupt:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
            code = 130
    content = log.read_text(errors='replace')
    counts = re.findall(r'Ran (\d+) tests?', content)
    result = {'exit_code': code, 'tests': int(counts[-1]) if counts else None,
              'seconds': round(time.monotonic() - started, 3), 'log': str(log),
              'log_bytes': log.stat().st_size, 'command': command}
    (directory / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2), flush=True)
    if code:
        print('\n'.join(content[-16000:].splitlines()[-60:]), file=sys.stderr)
        print(f'Full failure diagnostics: {log}', file=sys.stderr)
    return code


if __name__ == '__main__':
    folder = ROOT / '.cache/agent-tests' / (time.strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:8])
    sys.exit(run(['bash', str(ROOT / 'scripts/test.sh'), *sys.argv[1:]], folder))
