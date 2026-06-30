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
from datetime import datetime, timedelta
from pathlib import Path

from . import common, report
from .category_taxonomy import CATEGORIES, categorize_event
from .sources import SOURCES

JSON_OUT = "/tmp/nrw-events-latest.json"
META_JSON_OUT = "/tmp/nrw-events-latest-meta.json"


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


def _with_canonical_category(event: dict) -> dict:
    canonical = categorize_event(
        event.get("category", ""),
        event.get("title", ""),
        f"{event.get('description', '')} {event.get('link', '')}",
    )
    return {
        **event,
        "category_key": canonical["key"],
        "category_label": canonical["label"],
        "category_confidence": canonical.get("confidence", 0),
        "category_reason": canonical.get("reason", ""),
    }


def main() -> None:
    days_ahead = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    common.set_window(days_ahead)
    common.reset_source_warnings()
    _load_env_file()

    print(f"Fetching events for {common.TODAY.strftime('%d %b')} → "
          f"{common.END_DATE.strftime('%d %b %Y')}...", file=sys.stderr)
    print(f"Radius: {common.MAX_RADIUS_KM}km from Bonn", file=sys.stderr)

    all_events = []
    source_counts_raw: dict = {}
    source_errors: dict = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(fn): name for name, fn in SOURCES.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                result = future.result()
                source_counts_raw[name] = len(result)
                print(f"  ✓ {name}: {len(result)} events", file=sys.stderr)
                all_events.extend(result)
            except Exception as e:
                source_counts_raw[name] = 0
                source_errors[name] = str(e)
                print(f"  ✗ {name}: {e}", file=sys.stderr)

    score_floor = float(os.environ.get("NRW_EVENTS_SCORE_FLOOR", "0.4"))
    filtered = [e for e in all_events if e["score"] >= score_floor and not common.is_junk_event(e)]
    print(f"\nPre-dedup: {len(filtered)} events "
          f"(filtered {len(all_events) - len(filtered)} low-score/junk)", file=sys.stderr)

    deduped = report.deduplicate(filtered)
    print(f"Post-dedup: {len(deduped)} events", file=sys.stderr)

    print(report.format_report(deduped))

    events_sorted = sorted((_with_canonical_category(event) for event in deduped), key=lambda x: -x["score"])

    # Rich JSON wrapper for callers that want generation metadata without
    # changing the documented top-level list contract of JSON_OUT.
    start = common.TODAY
    end = common.END_DATE
    has_weekend = any((start + timedelta(days=i)).weekday() >= 5
                      for i in range((end - start).days + 1))
    label = "this weekend" if has_weekend else "short term"
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "window": {"start": start.strftime("%Y-%m-%d"), "end": end.strftime("%Y-%m-%d"), "label": label},
        "radius_km_from_bonn": common.MAX_RADIUS_KM,
        "score_floor": score_floor,
        "source_counts_raw": source_counts_raw,
        "source_errors": source_errors,
        "source_warnings": common.get_source_warnings(),
        "categories": CATEGORIES,
        "pre_dedup_count": len(filtered),
        "event_count": len(deduped),
        "events": events_sorted,
    }
    out_path = os.environ.get("NRW_EVENTS_JSON_OUT", JSON_OUT)
    with open(out_path, "w") as f:
        json.dump(events_sorted, f, ensure_ascii=False, indent=2)
    print(f"\nJSON saved: {out_path}", file=sys.stderr)

    meta_out_path = os.environ.get("NRW_EVENTS_META_JSON_OUT", META_JSON_OUT)
    with open(meta_out_path, "w") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"Metadata JSON saved: {meta_out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
