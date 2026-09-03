"""Offline full-pipeline replay in fresh processes with isolated writable state."""

from __future__ import annotations

import argparse
import io
import json
import math
import os
import resource
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from contextlib import nullcontext, redirect_stdout
from datetime import datetime
from email.message import Message
from pathlib import Path
from typing import Any
from unittest.mock import patch

from . import performance
from .snapshot_compare import differences, snapshot_differences


class _Response(io.BytesIO):
    def __init__(self, body: bytes, content_type: str, status: int, url: str) -> None:
        super().__init__(body)
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        self.status = status
        self.url = url

    def geturl(self) -> str:
        return self.url


class ReplayTransport:
    def __init__(self, responses: dict[str, tuple[bytes, str, int]], *, latency_ms: float = 0) -> None:
        if not math.isfinite(latency_ms) or not 0 <= latency_ms <= 1000:
            raise ValueError("replay latency must be between 0 and 1000 milliseconds")
        self.responses = responses
        self.latency_ms = latency_ms
        self.misses: list[str] = []
        self._lock = threading.Lock()

    def open(self, request: Any, *args: Any, **kwargs: Any) -> _Response:
        url = request.full_url if isinstance(request, urllib.request.Request) else str(request)
        if url not in self.responses:
            with self._lock:
                self.misses.append(url)
            raise OSError("missing replay response")
        if self.latency_ms:
            time.sleep(self.latency_ms / 1000)
        response = _Response(*self.responses[url], url)
        if response.status >= 400:
            raise urllib.error.HTTPError(url, response.status, "replayed HTTP error", response.headers, response)
        return response

    def deny_socket(self, *args: Any, **kwargs: Any) -> None:
        with self._lock:
            self.misses.append("unrecorded socket access")
        raise OSError("network is disabled during offline replay")


def summarize(values: list[float]) -> dict[str, float]:
    if not values:
        raise ValueError("at least one sample is required")
    ordered = sorted(values)
    return {
        "min": ordered[0], "median": statistics.median(ordered),
        "p95": ordered[math.ceil(len(ordered) * 0.95) - 1],
    }


def replay_differences(left: dict, right: dict) -> list[str]:
    """Compare public metadata and durable artifacts with no ledger exclusions."""
    return [
        *snapshot_differences(left["snapshot"], right["snapshot"]),
        *differences({"artifacts": left["artifacts"]}, {"artifacts": right["artifacts"]}),
    ]


def replay(manifest_path: Path, state: Path, *, telemetry: bool) -> dict:
    # Called only in an isolated worker process by the command below. Do not
    # load .env or inherit production credentials and cache paths.
    from . import common, config, runner
    from .observability import configure_logging
    from .runtime import EventWindow, RunContext

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    os.environ["NRW_EVENTS_TAXONOMY_CACHE"] = "1" if manifest.get("taxonomy_cache", True) else "0"
    os.environ["NRW_EVENTS_NORMALIZATION_CACHE"] = "1" if manifest.get("normalization_cache", True) else "0"
    os.environ["NRW_EVENTS_ICAL_PRUNE"] = "1" if manifest.get("ical_prune", True) else "0"
    os.environ["NRW_EVENTS_COMPONENT_WORKERS"] = str(manifest.get("component_workers", 3))
    root = manifest_path.parent
    os.environ["NRW_EVENTS_CACHE_DIR"] = str(state / "cache")
    os.environ["NRW_EVENTS_AI_ENRICHMENT"] = "0"
    if manifest.get("detail_cache_dir"):
        shutil.copytree(root / manifest["detail_cache_dir"], state / "cache", dirs_exist_ok=True)
    for seed in manifest.get("detail_cache_seed", []):
        cache = common._load_detail_page_cache(seed["namespace"], common._detail_page_cache_ttl_seconds())
        now = time.time() - float(seed.get("age_seconds", 60))
        cache["entries"][seed["url"]] = {
            "body": (root / seed["file"]).read_text(encoding="utf-8"),
            "fetched_at": now, "accessed_at": now,
        }
        cache["dirty"] = True
        warning = common._persist_detail_page_cache(cache)
        if warning:
            raise OSError("cannot prepare isolated replay cache")
    common._reset_detail_page_cache()
    transport = ReplayTransport({
        url: ((root / entry["file"]).read_bytes(), entry.get("content_type", "text/html; charset=utf-8"),
              entry.get("status", 200))
        for url, entry in manifest["responses"].items()
    }, latency_ms=float(manifest.get("network_latency_ms", 0)))
    today = datetime.fromisoformat(manifest["date"])
    window = EventWindow.from_days(manifest.get("days_ahead", 90), today)
    settings = config.RuntimeConfig(
        days_ahead=manifest.get("days_ahead", 90),
        source_workers=manifest.get("source_workers", 12),
        http_retry_attempts=1,
        previous_meta_json=str(root / manifest["previous_snapshot"]) if manifest.get("previous_snapshot") else "",
        series_ledger_json=str(root / manifest["series_ledger"]) if manifest.get("series_ledger") else str(state / "ledger.json"),
        json_out=str(state / "events.json"), meta_json_out=str(state / "metadata.json"),
        highlights_json_out=str(state / "highlights.json"),
    )
    context = RunContext(settings, window, "offline-replay", configure_logging("offline-replay", "CRITICAL", "", ""),
                         clock=lambda: today)
    sources = {}
    for spec in manifest["sources"]:
        if isinstance(spec, str):
            sources[spec] = runner.SOURCES[spec]
        else:
            def fetch(spec: dict[str, Any] = spec) -> list:
                return common.fetch_ical(spec["url"], spec["name"], spec.get("city", "Bonn"),
                                         category=spec.get("category", ""), source_id=spec.get("source_id", ""))
            sources[spec["name"]] = fetch
    collector = performance.Collector()
    started = time.perf_counter()
    cpu_started = time.process_time()
    with (
        patch.object(urllib.request, "urlopen", transport.open),
        patch.object(socket.socket, "connect", transport.deny_socket),
        patch.object(socket.socket, "connect_ex", transport.deny_socket),
        redirect_stdout(io.StringIO()),
        performance.collect(collector) if telemetry else nullcontext(),
    ):
        result = runner.run_import(context, sources)
        snapshot = runner.build_snapshot(result, context)
        published = {**snapshot.metadata, "events": snapshot.events}
        # Serialize in isolated state to include the real encoding/fsync cost.
        runner._atomic_json(state / "snapshot.json", published)
    elapsed = time.perf_counter() - started
    cpu = time.process_time() - cpu_started
    if transport.misses:
        raise ValueError(f"incomplete replay: {len(transport.misses)} unrecorded requests; first: {transport.misses[0]}")
    peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_rss_mib = peak_rss / (1024 * 1024 if sys.platform == "darwin" else 1024)
    from . import category_taxonomy, normalization
    return {"wall_ms": elapsed * 1000, "process_cpu_ms": cpu * 1000, "peak_rss_mib": peak_rss_mib,
            "simulated_network_latency_ms": transport.latency_ms,
            "taxonomy_cache": category_taxonomy._cached_keyword_matches.cache_info()._asdict(),
            "normalization_cache": normalization._cached_comparison_text.cache_info()._asdict(),
            "telemetry": collector.snapshot(), "snapshot": published,
            "artifacts": {"series_ledger": snapshot.series_ledger, "highlights": snapshot.highlights}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--without-telemetry", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker-state", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker_state:
        print(json.dumps(replay(args.manifest, args.worker_state, telemetry=not args.without_telemetry)))
        return 0
    if args.repetitions < 1:
        parser.error("--repetitions must be positive")
    runs = []
    for _ in range(args.repetitions):
        with tempfile.TemporaryDirectory(prefix="nrw-events-replay-") as temporary:
            state = Path(temporary)
            env = {
                "PATH": os.environ.get("PATH", ""),
                "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
                "PYTHONHASHSEED": "0", "TZ": "Europe/Berlin",
                "XDG_STATE_HOME": str(state), "XDG_CACHE_HOME": str(state),
                "NRW_EVENTS_CACHE_DIR": str(state / "cache"),
                "NRW_EVENTS_AI_ENRICHMENT": "0",
            }
            command = [sys.executable, "-m", "nrw_events.benchmark", str(args.manifest.resolve()),
                       "--worker-state", str(state)]
            if args.without_telemetry:
                command.append("--without-telemetry")
            completed = subprocess.run(command, env=env, capture_output=True, text=True, check=True, timeout=900)
            runs.append(json.loads(completed.stdout))
    deltas = [replay_differences(runs[0], run) for run in runs[1:]]
    output = {
        "repetitions": len(runs), "mode": "offline replay; fresh process and isolated state per run; AI disabled",
        "wall_ms": summarize([run["wall_ms"] for run in runs]),
        "process_cpu_ms": summarize([run["process_cpu_ms"] for run in runs]),
        "semantic_differences": deltas, "runs": runs,
    }
    output["failed_runs"] = sum(run["snapshot"]["run_status"] == "failed" for run in runs)
    encoded = json.dumps(output, indent=2)
    if args.output:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    else:
        print(encoded)
    return 1 if any(deltas) or output["failed_runs"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
