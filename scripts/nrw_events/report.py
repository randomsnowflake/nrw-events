"""Public import API; orchestration, policy and publication have separate owners."""
import sys
from importlib import import_module
from types import ModuleType
from typing import Any

_OWNERS = {'_AGGREGATOR_SOURCE_MARKERS': 'dedup_rules', '_MARKET_DIRECTORY_SOURCE_MARKERS': 'dedup_rules', '_CIVIC_AGGREGATOR_SOURCE_MARKERS': 'dedup_rules', '_CIVIC_AGGREGATOR_SOURCE_EXACT': 'dedup_rules', '_RESTRICTED_FALLBACK_SOURCE_IDS': 'dedup_rules', '_REVIEWED_OCCURRENCE_SOURCE_TITLE_ALIASES': 'dedup_rules', '_SEARCH_SOURCE_MARKERS': 'dedup_rules', '_REUSED_OVERVIEW_LINK_THRESHOLD': 'dedup_rules', '_CITYWIDE_VENUE_ALIAS_FAMILIES': 'dedup_rules', '_REVIEWED_VENUE_ALIAS_FAMILIES': 'dedup_rules', '_VENUE_LOCATION_FIELDS': 'dedup_rules', 'source_authority': 'dedup_rules', '_series_place_key': 'deduplication', 'suppress_redundant_series_umbrellas': 'deduplication', 'normalize_title': 'duplicate_identity', '_normalized_link_key': 'duplicate_identity', '_link_route_depth': 'duplicate_identity', '_link_identity_counts': 'dedup_index', '_dedup_key': 'duplicate_identity', '_occurrence_date_keys': 'duplicate_identity', '_dedup_blocking_keys': 'dedup_index', '_blocking_candidates': 'dedup_index', '_index_blocking_keys': 'dedup_index', '_normalized_city': 'duplicate_identity', '_same_explicit_start': 'duplicate_identity', '_reviewed_occurrence_alias_family': 'duplicate_identity', '_reviewed_occurrence_alias_matches': 'duplicate_identity', '_citywide_title_family': 'duplicate_identity', '_citywide_venue_alias_family': 'duplicate_identity', '_concrete_numeric_units': 'duplicate_identity', '_concrete_venue_units': 'duplicate_identity', '_locations_compatible': 'duplicate_identity', '_venue_comparison_text': 'duplicate_identity', '_date_bounds': 'duplicate_identity', '_same_occurrence': 'duplicate_identity', '_duration_days': 'duplicate_identity', '_titles_match': 'duplicate_identity', '_funfair_title_identity': 'duplicate_identity', '_same_funfair_title_identity': 'duplicate_identity', '_title_words_without_venue_suffix': 'duplicate_identity', '_aggregator_title_variant_matches': 'duplicate_identity', '_venue_qualified_aggregator_title_matches': 'duplicate_identity', '_series_tokens': 'duplicate_identity', '_same_registered_venue_occurrence': 'duplicate_identity', '_market_title_family': 'duplicate_identity', '_MARKET_TITLE_GENERIC_WORDS': 'duplicate_identity', '_market_title_evidence_tokens': 'duplicate_identity', '_market_title_evidence_matches': 'duplicate_identity', '_has_separate_admission_charge': 'duplicate_identity', '_adopted_description': 'dedup_merge', '_merged_exhibitor_information': 'dedup_merge', '_paid_admission_identity': 'duplicate_identity', '_price_has_currency': 'duplicate_identity', '_merge_duplicate_metadata': 'dedup_merge', '_is_radio_aggregation_link': 'duplicate_identity', 'events_are_duplicates': 'duplicate_identity', 'deduplicate': 'deduplication', 'CATEGORY_SECTIONS': 'markdown_report', '_bucket': 'markdown_report', 'ranking_features': 'ranking', '_priority_bonus': 'ranking', 'PREFERRED_ORDER': 'ranking', '_escape_markdown': 'markdown_report', '_bounded_report': 'markdown_report', 'format_report': 'markdown_report'}
_EXTERNAL = {'re': ('re', None), 'Counter': ('collections', 'Counter'), 'defaultdict': ('collections', 'defaultdict'), 'replace': ('dataclasses', 'replace'), 'date': ('datetime', 'date'), 'datetime': ('datetime', 'datetime'), 'timedelta': ('datetime', 'timedelta'), 'SequenceMatcher': ('difflib', 'SequenceMatcher'), 'urlparse': ('urllib', 'parse'), 'common': ('.', 'common'), 'performance': ('.', 'performance'), 'event_id': ('.identity', 'event_id'), 'MAX_DISCOVERY_PROVENANCE_SOURCES': ('.models', 'MAX_DISCOVERY_PROVENANCE_SOURCES'), 'CanonicalEvent': ('.models', 'CanonicalEvent'), 'comparison_text': ('.normalization', 'comparison_text'), 'EventValidationError': ('.validation', 'EventValidationError')}


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
