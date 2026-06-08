"""
Runner — wires the pieces together: load env, set the window, fan out the
registered sources in parallel, filter + dedup, render the report, dump JSON.

This is the only place that knows about all sources at once. Per-source logic
lives in ``sources/``; shared machinery lives in ``common`` and ``report``.
"""

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import common, report
from .sources import SOURCES

JSON_OUT = "/tmp/nrw-events-latest.json"


def _load_env_file() -> None:
    """Load a .env so the script works when run directly. Real env vars win.

    Lookup order: NRW_EVENTS_ENV_FILE → repo-root .env → CWD .env.
    """
    repo_root = Path(__file__).resolve().parents[2]
    for env_path in (
        os.environ.get("NRW_EVENTS_ENV_FILE", ""),
        str(repo_root / ".env"),
        str(Path.cwd() / ".env"),
    ):
        if not env_path or not os.path.exists(env_path):
            continue
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, _, value = line.partition("=")
                    key, value = key.strip(), value.strip().strip("'\"")
                    if key and key not in os.environ:
                        os.environ[key] = value
        break


def main() -> None:
    days_ahead = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    common.set_window(days_ahead)
    _load_env_file()

    print(f"Fetching events for {common.TODAY.strftime('%d %b')} → "
          f"{common.END_DATE.strftime('%d %b %Y')}...", file=sys.stderr)
    print(f"Radius: {common.MAX_RADIUS_KM}km from Bonn", file=sys.stderr)

    all_events = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(fn): name for name, fn in SOURCES.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                result = future.result()
                print(f"  ✓ {name}: {len(result)} events", file=sys.stderr)
                all_events.extend(result)
            except Exception as e:
                print(f"  ✗ {name}: {e}", file=sys.stderr)

    score_floor = float(os.environ.get("NRW_EVENTS_SCORE_FLOOR", "0.4"))
    filtered = [e for e in all_events if e["score"] >= score_floor and not common.is_junk_event(e)]
    print(f"\nPre-dedup: {len(filtered)} events "
          f"(filtered {len(all_events) - len(filtered)} low-score/junk)", file=sys.stderr)

    deduped = report.deduplicate(filtered)
    print(f"Post-dedup: {len(deduped)} events", file=sys.stderr)

    print(report.format_report(deduped))

    with open(JSON_OUT, "w") as f:
        json.dump(sorted(deduped, key=lambda x: -x["score"]), f, ensure_ascii=False, indent=2)
    print(f"\nJSON saved: {JSON_OUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
