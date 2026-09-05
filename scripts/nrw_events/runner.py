"""Public import API; orchestration, policy and publication have separate owners."""
import sys
from importlib import import_module
from types import ModuleType
from typing import Any

_OWNERS = {'SOURCES': 'import_cli', 'EXIT_SUCCESS': 'source_execution', 'EXIT_DEGRADED': 'source_execution', 'EXIT_FAILED': 'source_execution', 'SNAPSHOT_GENERATIONS_KEPT': 'snapshot_publication', '_DISCOVERY_ONLY_SOURCE_IDS': 'retention_policy', '_BONN_FALLBACK_SOURCE_IDS': 'retention_policy', '_RADIO_RUNNER_SOURCE': 'retention_policy', '_LEGACY_DATED_RANGE_TITLE': 'retention_policy', '_RESEARCH_LEAD_MASTER_FIELDS': 'source_execution', 'VERBS': 'import_cli', '_CATEGORY_ALIASES': 'import_cli', '_DetachedThreadPoolExecutor': 'source_execution', 'ImportResult': 'import_contracts', 'SnapshotPayload': 'import_contracts', '_sanitize_research_lead': 'source_execution', '_run_source': 'source_execution', '_run_status': 'source_execution', '_exit_code': 'source_execution', '_endpoint_issues': 'source_execution', '_source_issue_message': 'source_execution', '_import_issues': 'source_execution', '_validate_output_paths': 'snapshot_publication', '_previous_snapshot': 'retention_policy', '_event_source_id': 'retention_policy', '_retained_event_counts_by_source': 'retention_policy', '_is_discovery_only_event': 'retention_policy', '_retention_labels': 'retention_policy', '_retain_previous_events': 'retention_policy', '_attach_baselines': 'retention_policy', '_source_result_for_identity': 'retention_policy', '_source_result_for_event': 'retention_policy', '_operational_source_result_for_event': 'retention_policy', '_publication_ai_input': 'import_orchestration', '_record_publication_ai_metrics': 'import_orchestration', '_publication_filter_reason': 'retention_policy', '_attach_cross_run_fields': 'retention_policy', '_cross_run_match_score': 'identity_reconciliation', '_uniquely_disambiguates_occurrence': 'identity_reconciliation', '_uniquely_matches_renamed_occurrence': 'identity_reconciliation', '_reconcile_published_ids': 'identity_reconciliation', '_atomic_json': 'snapshot_publication', '_publish_snapshots': 'snapshot_publication', '_ArgumentParser': 'import_cli', 'CliQuery': 'import_cli', '_parser': 'import_cli', '_parse_radius': 'import_cli', '_category_keys': 'import_cli', '_weekend_bounds': 'import_cli', '_parse_cli': 'import_cli', '_targeted_sources': 'import_cli', '_validate_targeted_refresh_snapshot': 'import_cli', '_event_overlaps': 'import_cli', '_matches_query': 'import_cli', 'filter_import_result': 'import_cli', 'run_import': 'import_orchestration', '_retained_events_without_fresh_duplicate': 'retention_policy', '_enrich_promoted_fallbacks': 'retention_policy', '_prefer_retained_primary_over_radio_fallback': 'retention_policy', '_prefer_retained_primary_over_bonn_fallback': 'retention_policy', '_enforce_restricted_publication_boundary': 'retention_policy', '_run_import_configured': 'import_orchestration', 'build_snapshot': 'snapshot_publication', 'publish_snapshot': 'snapshot_publication', 'cli': 'import_cli', '_cli': 'import_cli', 'main': 'import_cli'}
_EXTERNAL = {'argparse': ('argparse', None), 'atexit': ('atexit', None), 'fcntl': ('fcntl', None), 'json': ('json', None), 'os': ('os', None), 're': ('re', None), 'shutil': ('shutil', None), 'sys': ('sys', None), 'tempfile': ('tempfile', None), 'threading': ('threading', None), 'time': ('time', None), 'uuid': ('uuid', None), 'weakref': ('weakref', None), 'Counter': ('collections', 'Counter'), 'Callable': ('collections.abc', 'Callable'), 'Mapping': ('collections.abc', 'Mapping'), 'Sequence': ('collections.abc', 'Sequence'), 'FIRST_COMPLETED': ('concurrent.futures', 'FIRST_COMPLETED'), 'Future': ('concurrent.futures', 'Future'), 'ThreadPoolExecutor': ('concurrent.futures', 'ThreadPoolExecutor'), 'wait': ('concurrent.futures', 'wait'), 'futures_thread': ('concurrent.futures', 'thread'), 'copy_context': ('contextvars', 'copy_context'), 'dataclass': ('dataclasses', 'dataclass'), 'field': ('dataclasses', 'field'), 'replace': ('dataclasses', 'replace'), 'datetime': ('datetime', 'datetime'), 'timedelta': ('datetime', 'timedelta'), 'Path': ('pathlib', 'Path'), 'Any': ('typing', 'Any'), 'cast': ('typing', 'cast'), 'ai_enrichment': ('.', 'ai_enrichment'), 'common': ('.', 'common'), 'components': ('.', 'components'), 'config': ('.', 'config'), 'detail_enrichment': ('.', 'detail_enrichment'), 'early_publication': ('.', 'early_publication'), 'performance': ('.', 'performance'), 'radio_primary_resolution': ('.', 'radio_primary_resolution'), 'report': ('.', 'report'), 'reviewed_summaries': ('.', 'reviewed_summaries'), 'highlight_selection': ('.', 'highlights'), 'series_entities': ('.', 'series'), 'CATEGORIES': ('.category_taxonomy', 'CATEGORIES'), 'SourceFetchResult': ('.health', 'SourceFetchResult'), 'SourceResult': ('.health', 'SourceResult'), 'SourceStatus': ('.health', 'SourceStatus'), 'bounded_diagnostic_text': ('.health', 'bounded_diagnostic_text'), 'diagnostic_warning': ('.health', 'diagnostic_warning'), 'sanitized_warning': ('.health', 'sanitized_warning'), 'assign_event_ids': ('.identity', 'assign_event_ids'), 'content_hash': ('.identity', 'content_hash'), 'event_id': ('.identity', 'event_id'), 'partition_directory_fallbacks': ('.market_source_fallbacks', 'partition_directory_fallbacks'), 'MAX_DISCOVERY_PROVENANCE_SOURCES': ('.models', 'MAX_DISCOVERY_PROVENANCE_SOURCES'), 'CanonicalEvent': ('.models', 'CanonicalEvent'), 'normalize_source_id': ('.models', 'normalize_source_id'), 'comparison_text': ('.normalization', 'comparison_text'), 'configure_logging': ('.observability', 'configure_logging'), 'log': ('.observability', 'log'), 'redact': ('.observability', 'redact'), 'quality_gate_warnings': ('.quality', 'quality_gate_warnings'), 'summarize_event_quality': ('.quality', 'summarize_event_quality'), 'EventWindow': ('.runtime', 'EventWindow'), 'RunContext': ('.runtime', 'RunContext'), 'SOURCE_FETCHERS': ('.sources', 'SOURCE_FETCHERS'), 'SOURCE_IDS': ('.sources', 'SOURCE_IDS'), 'normalize_event_title': ('.title_normalization', 'normalize_event_title'), 'title_looks_truncated': ('.title_normalization', 'title_looks_truncated'), 'EventValidationError': ('.validation', 'EventValidationError'), 'validate_event': ('.validation', 'validate_event')}


def __getattr__(name: str) -> Any:
    if name in _OWNERS:
        return getattr(import_module('.' + _OWNERS[name], __package__), name)
    if name in _EXTERNAL:
        module, symbol = _EXTERNAL[name]
        if module == '.' and symbol:
            return import_module('.' + symbol, __package__)
        target = import_module(module, __package__)
        return getattr(target, symbol) if symbol else target
    raise AttributeError(name)


class _Facade(ModuleType):
    """Preserve legacy monkeypatch/injection seams during adapter migration."""
    def __setattr__(self, name: str, value: Any) -> None:
        if name in _OWNERS:
            setattr(import_module('.' + _OWNERS[name], __package__), name, value)
        else:
            super().__setattr__(name, value)

    def __delattr__(self, name: str) -> None:
        if name in _OWNERS:
            delattr(import_module('.' + _OWNERS[name], __package__), name)
        else:
            super().__delattr__(name)


sys.modules[__name__].__class__ = _Facade
