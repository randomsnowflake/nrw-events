"""Offline boundary records shared with consumers of the published snapshot."""
from datetime import datetime
from typing import Any

from .config import RuntimeConfig
from .identity import event_id
from .import_contracts import ImportResult
from .observability import configure_logging
from .runtime import EventWindow, RunContext
from .snapshot_publication import build_snapshot
from .validation import validate_event


def semantic_contract() -> dict[str, Any]:
    cases: list[tuple[str, dict[str, Any]]] = [
        ("all-day", {"all_day": True, "time": ""}),
        ("timed", {"all_day": False, "time": "20:00", "start_at": "2026-10-24T20:00:00+02:00", "end_at": "2026-10-24T22:00:00+02:00"}),
        ("multiday-dst", {"end_date": "2026-10-26", "all_day": False, "time": "20:00", "start_at": "2026-10-24T20:00:00+02:00", "end_at": "2026-10-26T22:00:00+01:00"}),
        ("cancelled", {"status": "cancelled", "cancelled_at": "2026-10-20", "cancellation_source": "https://example.test/cancelled"}),
        ("postponed", {"status": "postponed", "replacement_start_date": "2026-11-01"}),
        ("early-announcement", {"early_publication": True, "time": "", "all_day": True}),
        ("free", {"price": "Eintritt frei", "admission": {"isFree": True, "amount": 0, "currency": "EUR", "basis": "structured", "note": "", "donationSuggested": False}}),
        ("paid", {"price": "12 €", "admission": {"isFree": False, "amount": 12, "currency": "EUR", "basis": "structured", "note": "", "donationSuggested": False}}),
        ("unknown-admission", {"price": ""}),
        ("alias", {"previous_event_ids": ["earlier-announcement-2026-10-24-1234567890"]}),
        ("restricted-copy", {"source": "marktcom", "source_id": "marktcom", "source_role": "discovery", "discovered_via": ["marktcom"], "description": "RESTRICTED_SENTINEL", "description_html": "<p>RESTRICTED_SENTINEL</p>"}),
        ("retained-legacy", {"start_date": "2026-10-01", "end_date": "2026-10-01", "first_seen_at": "2026-09-01", "source_links": ["https://example.test/primary"], "discovered_via": ["legacy-calendar"]}),
    ]
    events = tuple(validate_event({
        "title": f"Contract {name}", "source": "Test", "source_id": "test",
        "start_date": "2026-10-24", "end_date": "2026-10-24", "city": "Bonn",
        "venue": "Kulturzentrum Brotfabrik", "description": "Producer fixture.",
        "link": f"https://example.test/{name}", "score": 2.0,
        **values,
    }) for name, values in cases)
    context = RunContext(RuntimeConfig(), EventWindow(datetime(2026, 10, 1), datetime(2026, 11, 2)),
                         "semantic-contract", configure_logging("semantic-contract", "ERROR", "", ""),
                         clock=lambda: datetime(2026, 10, 20, 12))
    snapshot = build_snapshot(ImportResult(events, {}, len(events), "healthy"), context)
    return {"producer": {**snapshot.metadata, "events": snapshot.events},
            "expected_ids": [event_id(event) for event in events],
            "cases": [name for name, _ in cases],
            "accepted_schemas": [None, 1, 2, 3, 4, 5, 6, 7], "rejected_schemas": [0, 8, 999]}
