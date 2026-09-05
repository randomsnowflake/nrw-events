"""Public import API; orchestration, policy and publication have separate owners."""
import sys
from importlib import import_module
from types import ModuleType
from typing import Any

_OWNERS = {'TARGET_SOURCE_IDS': 'ai_contracts', '_BONN_CACHE_CONTINUITY_SOURCE_IDS': 'ai_contracts', 'PIPELINE_VERSION': 'ai_contracts', 'OPENROUTER_PIPELINE_VERSION': 'ai_contracts', 'FACTS_PIPELINE_VERSION': 'ai_contracts', '_LEGACY_FACTS_PIPELINE_VERSION': 'ai_contracts', '_LEGACY_OPENAI_COMBINED_PIPELINE_VERSION': 'ai_contracts', '_LEGACY_OPENROUTER_COMBINED_PIPELINE_VERSION': 'ai_contracts', 'DEFAULT_MODEL': 'ai_contracts', 'DEFAULT_OPENROUTER_MODEL': 'ai_contracts', 'FACTS_OUTPUT_TOKEN_LIMIT': 'ai_contracts', 'SUMMARY_OUTPUT_TOKEN_LIMIT': 'ai_contracts', '_OPENAI_API_URL': 'ai_contracts', '_OPENROUTER_API_URL': 'ai_contracts', '_MAX_RESPONSE_BYTES': 'ai_contracts', '_RESPONSE_CHUNK_BYTES': 'ai_contracts', '_TRANSIENT_FAILURE_CACHE_HOURS': 'ai_contracts', '_CATEGORY_KEYS': 'ai_contracts', 'category': 'ai_contracts', '_EVENT_LOCKS_GUARD': 'ai_cache', '_EVENT_LOCKS': 'ai_cache', '_DATABASE_SCHEMA_GUARD': 'ai_cache', '_INITIALIZED_DATABASES': 'ai_cache', 'AIEnrichmentError': 'ai_contracts', 'AICacheMissBudgetExceeded': 'ai_contracts', '_retry_after_seconds': 'ai_contracts', '_sleep_before_ai_retry': 'ai_transport', '_read_bounded_response': 'ai_transport', 'AISettings': 'ai_transport', 'Usage': 'ai_contracts', 'StructuredClient': 'ai_contracts', '_isolated_http_worker': 'ai_transport', '_read_response_isolated': 'ai_transport', '_read_http_response': 'ai_transport', '_env_bool': 'ai_settings', '_env_reasoning_effort': 'ai_settings', 'settings_from_env': 'ai_settings', 'is_target_event': 'ai_policy', 'strip_restricted_copy': 'ai_policy', '_source_material': 'ai_policy', '_input_payload': 'ai_policy', '_input_hash': 'ai_policy', '_nullable_string': 'ai_contracts', '_ADMISSION_SCHEMA': 'ai_contracts', '_FACT_SCHEMA': 'ai_contracts', '_SUMMARY_SCHEMA': 'ai_contracts', '_validate_types': 'ai_contracts', '_EXTRACT_PROMPT': 'ai_contracts', '_SUMMARY_PROMPT': 'ai_contracts', 'ResponsesClient': 'ai_transport', 'OpenRouterClient': 'ai_transport', '_openrouter_usage': 'ai_transport', '_utc_now': 'ai_cache', '_timestamp': 'ai_cache', '_parse_timestamp': 'ai_cache', '_event_lock': 'ai_cache', '_locked_database': 'ai_cache', 'cache_pipeline_version': 'ai_cache', 'facts_cache_version': 'ai_cache', '_legacy_facts_pipeline_pattern': 'ai_cache', '_ensure_row': 'ai_cache', '_reuse_compatible_facts': 'ai_cache', '_record_success': 'ai_cache', '_record_failure': 'ai_cache', '_reset_expired_failure_window': 'ai_cache', '_MARKETING_PATTERN': 'ai_contracts', '_MISSING_INFO_PATTERN': 'ai_contracts', '_META_OR_SPECULATION_PATTERN': 'ai_contracts', '_VENDOR_FEE_PATTERN': 'ai_contracts', '_SPONSOR_PATTERN': 'ai_contracts', '_HEALTH_CLAIM_PATTERN': 'ai_contracts', '_VISITOR_FREE_PATTERN': 'ai_contracts', '_VISITOR_PAID_PATTERN': 'ai_contracts', '_REGISTRATION_PATTERN': 'ai_contracts', '_NEGATIVE_REGISTRATION_PATTERN': 'ai_contracts', '_GENERIC_TARGET_GROUP_PATTERN': 'ai_contracts', '_LANGUAGE_EVIDENCE_PATTERN': 'ai_contracts', '_AVAILABILITY_PATTERNS': 'ai_contracts', '_GERMAN_MONTHS': 'ai_contracts', '_PROSE_DATE_PATTERN': 'ai_contracts', '_WEEKDAY_PATTERN': 'ai_contracts', '_WEEKDAY_NUMBERS': 'ai_contracts', '_normalized_words': 'ai_policy', '_mentions_date_outside_scope': 'ai_policy', '_mentions_weekday_outside_scope': 'ai_policy', '_sanitize_extracted_facts': 'ai_policy', '_summary_quality': 'ai_policy', '_without_sentences': 'ai_policy', '_ADMISSION_SENTENCE_PATTERN': 'ai_contracts', '_admission_conflicts': 'ai_policy', '_clean_summary_result': 'ai_policy', '_clean_nullable': 'ai_policy', '_confidence': 'ai_policy', '_apply_result': 'ai_policy', '_occurrence_start_time': 'ai_policy', '_event_occurrence_start_time': 'ai_policy', '_cached_occurrence_matches': 'ai_policy', '_historical_event_key_identity': 'ai_policy', '_reuse_cached_success': 'ai_cache', '_calendar_occurrence_overrides_non_event': 'ai_policy', 'enrich_event': 'ai_orchestration', 'enrich_events': 'ai_orchestration'}
_EXTERNAL = {'fcntl': ('fcntl', None), 'json': ('json', None), 'math': ('math', None), 'multiprocessing': ('multiprocessing', None), 'os': ('os', None), 're': ('re', None), 'sqlite3': ('sqlite3', None), 'threading': ('threading', None), 'time': ('time', None), 'urllib': ('urllib', None), 'weakref': ('weakref', None), 'Callable': ('collections.abc', 'Callable'), 'Iterator': ('collections.abc', 'Iterator'), 'Mapping': ('collections.abc', 'Mapping'), 'ThreadPoolExecutor': ('concurrent.futures', 'ThreadPoolExecutor'), 'closing': ('contextlib', 'closing'), 'contextmanager': ('contextlib', 'contextmanager'), 'dataclass': ('dataclasses', 'dataclass'), 'replace': ('dataclasses', 'replace'), 'date': ('datetime', 'date'), 'datetime': ('datetime', 'datetime'), 'timedelta': ('datetime', 'timedelta'), 'timezone': ('datetime', 'timezone'), 'SequenceMatcher': ('difflib', 'SequenceMatcher'), 'Message': ('email.message', 'Message'), 'sha256': ('hashlib', 'sha256'), 'Path': ('pathlib', 'Path'), 'Any': ('typing', 'Any'), 'Protocol': ('typing', 'Protocol'), 'cast': ('typing', 'cast'), 'category_taxonomy': ('.', 'category_taxonomy'), 'common': ('.', 'common'), 'config': ('.', 'config'), 'richtext': ('.', 'richtext'), 'keep_only_event_master_data': ('.core', 'keep_only_event_master_data'), 'event_id': ('.identity', 'event_id'), 'RawEvent': ('.models', 'RawEvent'), 'normalize_source_id': ('.models', 'normalize_source_id')}


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
