"""Owning implementation of import cli; core is a compatibility facade."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn, cast

from . import (
    common,
    config,
    performance,
    report,
)
from . import import_contracts as _impl_import_contracts
from . import import_orchestration as _impl_import_orchestration
from . import retention_policy as _impl_retention_policy
from . import snapshot_publication as _impl_snapshot_publication
from . import source_execution as _impl_source_execution
from .category_taxonomy import CATEGORIES
from .health import (
    SourceFetchResult,
)
from .identity import assign_event_ids
from .models import CanonicalEvent, normalize_source_id
from .observability import configure_logging, log
from .runtime import EventWindow, RunContext
from .sources import SOURCE_FETCHERS, SOURCE_IDS

SOURCES = SOURCE_FETCHERS


VERBS = ("heute", "heute-abend", "wochenende")


_CATEGORY_ALIASES = {
    "aktivitaeten": "activities", "aktivitäten": "activities", "ausstellung": "exhibition",
    "familie": "kids", "festival": "festival", "food": "food", "fuehrung": "outdoor",
    "führung": "outdoor", "kino": "cinema", "konzert": "concert", "kurs": "workshop",
    "markt": "market", "nachtleben": "nightlife", "party": "nightlife", "sonstiges": "other",
    "sport": "sports", "theater": "stage", "treffen": "activities", "vortrag": "talk",
    "workshop": "workshop",
}


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise ValueError(message)


@dataclass(frozen=True, slots=True)
class CliQuery:
    verb: str = ""
    source_ids: tuple[str, ...] = ()


def _parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(
        prog="nrw-events",
        description="Import and query public NRW events.",
    )
    parser.add_argument("target", nargs="?", help="days_ahead or one of: " + ", ".join(VERBS))
    parser.add_argument("--days", type=int, help="number of days to import (1-90)")
    parser.add_argument("--json", action="store_true", help="write only the filtered event list as JSON to stdout")
    parser.add_argument("--umkreis", metavar="KM", help="maximum distance from Bonn, e.g. 15km")
    parser.add_argument("--kostenlos", action="store_true", help="return only events with explicit free admission")
    parser.add_argument("--kategorie", metavar="KEYS", help="comma-separated category keys or German names")
    parser.add_argument("--max-per-section", type=int, metavar="N", help="maximum events per report section")
    parser.add_argument("--max-chars", type=int, metavar="N", help="maximum Markdown report length")
    parser.add_argument(
        "--source",
        action="append",
        metavar="SOURCE_ID",
        help="refresh only this source id; repeat for multiple sources and retain all others from the previous snapshot",
    )
    return parser


def _parse_radius(value: str) -> float:
    normalized = value.strip().casefold().removesuffix("km").strip()
    try:
        radius = float(normalized.replace(",", "."))
    except ValueError as exc:
        raise ValueError("--umkreis must be a distance such as 15km") from exc
    if not 0.1 <= radius <= 500:
        raise ValueError("--umkreis must be between 0.1km and 500km")
    return radius


def _category_keys(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    known = {category["key"] for category in CATEGORIES}
    keys = []
    for raw in value.split(","):
        normalized = raw.strip().casefold()
        key = _CATEGORY_ALIASES.get(normalized, normalized)
        if key not in known:
            raise ValueError(f"unknown category {raw.strip()!r}; use one of: {', '.join(sorted(known))}")
        if key not in keys:
            keys.append(key)
    return tuple(keys)


def _weekend_bounds(today: datetime) -> tuple[datetime, datetime]:
    weekday = today.weekday()
    days_to_friday = 4 - weekday if weekday <= 4 else 0
    start = today + timedelta(days=days_to_friday)
    end = today + timedelta(days=(6 - weekday) % 7)
    return start, end


def _parse_cli(argv: list[str], now: datetime | None = None) -> tuple[int | None, CliQuery, dict[str, object]]:
    args = _parser().parse_args(argv[1:])
    target = args.target or ""
    verb = target if target in VERBS else ""
    positional_days: int | None = None
    if target and not verb:
        try:
            positional_days = int(target)
        except ValueError as exc:
            raise ValueError(f"unknown verb {target!r}; use one of: {', '.join(VERBS)}") from exc
    if positional_days is not None and args.days is not None:
        raise ValueError("days_ahead may be given either positionally or with --days, not both")
    if verb and args.days is not None:
        # A verb *is* the time window. Silently discarding --days would make an
        # agent believe it queried 14 days when it only ever saw one.
        raise ValueError(f"{verb} already defines its own window; drop --days")
    explicit_days = args.days if args.days is not None else positional_days
    current = (now or datetime.now(common.LOCAL_TIMEZONE)).replace(tzinfo=None)
    if verb in {"heute", "heute-abend"}:
        explicit_days = 1
    elif verb == "wochenende":
        _, weekend_end = _weekend_bounds(current.replace(hour=0, minute=0, second=0, microsecond=0))
        explicit_days = (weekend_end.date() - current.date()).days + 1
    overrides: dict[str, object] = {}
    if args.json:
        overrides["json_stdout"] = True
    if args.umkreis:
        overrides["radius_km"] = _parse_radius(args.umkreis)
    if args.kostenlos:
        overrides["free_only"] = True
    if args.kategorie:
        overrides["categories"] = _category_keys(args.kategorie)
    if args.max_per_section is not None:
        if args.max_per_section < 0:
            raise ValueError("--max-per-section must be zero or greater")
        overrides["max_per_section"] = args.max_per_section
    if args.max_chars is not None:
        if args.max_chars < 200:
            raise ValueError("--max-chars must be at least 200")
        overrides["report_max_chars"] = args.max_chars
    requested_source_ids: list[str] = []
    known_source_ids = set(SOURCE_IDS.values())
    for value in args.source or []:
        for raw_source_id in value.split(","):
            source_id = normalize_source_id(raw_source_id)
            if source_id not in known_source_ids:
                raise ValueError(
                    f"unknown source {raw_source_id.strip()!r}; use one of: "
                    + ", ".join(sorted(known_source_ids))
                )
            if source_id not in requested_source_ids:
                requested_source_ids.append(source_id)
    return explicit_days, CliQuery(verb, tuple(requested_source_ids)), overrides


def _targeted_sources(source_ids: tuple[str, ...]) -> Mapping[str, Callable[[], object]]:
    """Fetch selected sources while retaining every unselected source globally."""
    if not source_ids:
        return SOURCES
    selected = set(source_ids)

    def retained_source(source_id: str) -> Callable[[], object]:
        return lambda: SourceFetchResult.scheduled_skip(
            f"targeted refresh preserved {source_id} from the previous snapshot"
        )

    return {
        name: fetcher if SOURCE_IDS[name] in selected else retained_source(SOURCE_IDS[name])
        for name, fetcher in SOURCES.items()
    }


def _validate_targeted_refresh_snapshot(
    settings: config.RuntimeConfig,
    source_ids: tuple[str, ...],
) -> None:
    """Refuse a partial refresh when there is no snapshot to retain."""
    if not source_ids:
        return
    previous_path = settings.previous_meta_json or settings.meta_json_out
    previous = _impl_retention_policy._previous_snapshot(previous_path)
    if not isinstance(previous.get("events"), list):
        raise ValueError(
            "--source requires a readable previous snapshot with events at "
            f"{Path(previous_path).expanduser()}"
        )


def _event_overlaps(event: CanonicalEvent, start: datetime, end: datetime) -> bool:
    event_start = common.parse_iso_date(event.start_date)
    event_end = common.parse_iso_date(event.end_date) or event_start
    return bool(event_start and event_end and event_start.date() <= end.date() and event_end.date() >= start.date())


def _matches_query(event: CanonicalEvent, settings: config.RuntimeConfig, query: CliQuery, today: datetime) -> bool:
    if settings.categories and event.category_key not in settings.categories:
        return False
    if settings.free_only and (event.admission or {}).get("isFree") is not True:
        return False
    if event.distance_km is not None and event.distance_km > settings.radius_km:
        return False
    day = today.replace(hour=0, minute=0, second=0, microsecond=0)
    if query.verb == "wochenende":
        start, end = _weekend_bounds(day)
        if not _event_overlaps(event, start, end):
            return False
    elif query.verb in {"heute", "heute-abend"} and not _event_overlaps(event, day, day):
        return False
    if query.verb == "heute-abend":
        times = [int(hour) * 60 + int(minute) for hour, minute in re.findall(r"(\d{2}):(\d{2})", event.time)]
        if not times or times[0] < 17 * 60:
            return False
    return True


def filter_import_result(
    result: _impl_import_contracts.ImportResult,
    settings: config.RuntimeConfig,
    query: CliQuery,
    today: datetime,
) -> _impl_import_contracts.ImportResult:
    events = tuple(event for event in result.events if _matches_query(event, settings, query, today))
    return replace(result, events=events)


def cli(argv: list[str]) -> int:
    """Emit opt-in performance diagnostics on stderr, never in public data."""
    if os.environ.get("NRW_EVENTS_PERFORMANCE", "").strip() != "1":
        return _cli(argv)
    collector = performance.Collector()
    started = time.perf_counter()
    cpu_started = time.process_time()
    try:
        with performance.collect(collector):
            return _cli(argv)
    finally:
        diagnostics = collector.snapshot()
        diagnostics["event"] = "import_performance"
        diagnostics["wall_ms"] = (time.perf_counter() - started) * 1000
        diagnostics["process_cpu_ms"] = (time.process_time() - cpu_started) * 1000
        print(json.dumps(diagnostics, sort_keys=True), file=sys.stderr)


def _cli(argv: list[str]) -> int:
    """Translate argv/environment and service results into CLI effects."""
    try:
        config.load_env_file()
        days_ahead, query, overrides = _parse_cli(argv)
        import_settings = config.runtime_config(days_ahead)
        settings = replace(import_settings, **cast(dict[str, Any], overrides))
        settings = replace(settings, categories=_category_keys(",".join(settings.categories)))
        if not settings.json_stdout:
            _impl_snapshot_publication._validate_output_paths(settings)
        _validate_targeted_refresh_snapshot(import_settings, query.source_ids)
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return _impl_source_execution.EXIT_FAILED

    run_id = uuid.uuid4().hex
    logger = configure_logging(run_id, settings.log_level, settings.log_file, settings.json_log_file)
    context = RunContext(import_settings, EventWindow.from_days(import_settings.days_ahead), run_id, logger)
    try:
        import_result = _impl_import_orchestration.run_import(context, _targeted_sources(query.source_ids))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return _impl_source_execution.EXIT_FAILED
    snapshot = _impl_snapshot_publication.build_snapshot(import_result, context)
    presentation_result = filter_import_result(import_result, settings, query, context.window.start)
    if settings.json_stdout:
        presentation_object_ids = {id(event) for event in presentation_result.events}
        presentation_ids = {
            assigned["event_id"]
            for original, assigned in zip(
                import_result.events,
                assign_event_ids(event.to_dict() for event in import_result.events),
                strict=True,
            )
            if id(original) in presentation_object_ids
        }
        presentation_events = [
            event for event in snapshot.events if str(event.get("event_id") or "") in presentation_ids
        ]
        print(json.dumps(presentation_events, ensure_ascii=False, indent=2))
    else:
        report_options: dict[str, Any] = {"radius_km": settings.radius_km}
        if settings.max_per_section:
            report_options["max_per_section"] = settings.max_per_section
        if settings.report_max_chars:
            report_options["max_chars"] = settings.report_max_chars
        print(report.format_report(list(presentation_result.events), **report_options))
    for issue in snapshot.metadata["import_issues"]:
        log(logger, 30 if issue["severity"] == "warning" else 40,
            f"import issue: {issue['message']}", run_id=run_id, source=str(issue["source"]))
    run_status = str(snapshot.metadata["run_status"])
    if run_status == "failed":
        log(logger, 40, "import health gate failed; preserving last-known-good snapshot",
            run_id=run_id, source="runner")
    elif not settings.json_stdout:
        try:
            paths = _impl_snapshot_publication.publish_snapshot(snapshot, settings)
            log(logger, 20, f"published snapshot manifest at {paths['manifest']}", run_id=run_id, source="runner")
        except OSError as exc:
            log(logger, 40, f"snapshot publication failed: {exc}", run_id=run_id, source="runner",
                error_type=type(exc).__name__)
            return _impl_source_execution.EXIT_FAILED
    log(logger, 20 if run_status == "healthy" else 30, f"run finished: {run_status}",
        run_id=run_id, source="runner")
    return _impl_source_execution._exit_code(run_status)


def main() -> int:
    """Compatibility entry point for existing wrappers."""
    return cli(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
