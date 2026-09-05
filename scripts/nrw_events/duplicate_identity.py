"""Owning implementation of duplicate identity; core is a compatibility facade."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from typing import Any
from urllib import parse as urlparse

from . import common, performance
from . import dedup_rules as _impl_dedup_rules
from .normalization import comparison_text


def normalize_title(title: str) -> str:
    """Aggressively normalize a title for near-duplicate comparison."""
    t = (title or "").casefold().strip()
    t = re.sub(
        r"^\s*[-–—:()]*\s*(?:abgesagt|entfällt|entfaellt|fällt\s+aus|"
        r"faellt\s+aus)\s*[-–—:()]*\s*",
        "",
        t,
    )
    t = re.sub(
        r"^(?:(?:ausstellung|exhibition|konzert|concert)\b[:\s]*|"
        r"kostenloser\s+eintritt\b[:\s]*|eintritt\s+frei\b[:\s]*|tickets?\s+für\s+)",
        "",
        t,
    )
    t = re.sub(
        r"\bfloh\s*[-/&]?\s*und\s+trödelmarkt\s+am\b",
        "flohmarkt ",
        t,
    )
    return comparison_text(t, separator="")


def _normalized_link_key(link: str) -> str:
    """Normalize equivalent web URLs for reuse counting, preserving app routes."""
    parsed = urlparse.urlsplit(link or "")
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        return link
    netloc = parsed.netloc.casefold()
    default_port = ":80" if parsed.scheme.casefold() == "http" else ":443"
    if netloc.endswith(default_port):
        netloc = netloc.rsplit(":", 1)[0]
    path = parsed.path.rstrip("/") or "/"
    # Fragments are significant: ticket shops commonly put event routes after '#'.
    return urlparse.urlunsplit(("https", netloc, path, parsed.query, parsed.fragment))


def _link_route_depth(link: str) -> int:
    """Count concrete path/query components without guessing from URL length."""
    parsed = urlparse.urlsplit(link or "")
    path_parts = [part for part in parsed.path.split("/") if part]
    query_parts = urlparse.parse_qsl(parsed.query, keep_blank_values=True)
    fragment = urlparse.urlsplit(parsed.fragment)
    fragment_parts = [part for part in fragment.path.split("/") if part]
    fragment_query_parts = urlparse.parse_qsl(
        fragment.query, keep_blank_values=True,
    )
    return len(path_parts) + len(query_parts) + len(fragment_parts) + len(fragment_query_parts)


def _dedup_key(ev: Mapping[str, Any]) -> str:
    """Occurrence key: recurring appointments on different dates must survive."""
    norm = normalize_title(ev.get("title", ""))
    city = _normalized_city(ev.get("city", ""))
    start_date = ev.get("start_date") or (ev.get("date", "") or "").split("–", 1)[0]
    return "|".join((norm, city, str(start_date)))


def _occurrence_date_keys(event: Mapping[str, Any]) -> tuple[str, ...]:
    """Return bounded daily/coarse keys so overlapping runs share a bucket."""
    bounds = _date_bounds(event)
    if not bounds:
        raw = event.get("start_date") or (event.get("date", "") or "").split("–", 1)[0]
        return (str(raw),)
    start, end = bounds
    span_days = (end - start).days + 1
    # Every interval gets coarse century buckets. They let a short occurrence
    # enter the candidate set of a pathological multi-year range without
    # materializing every covered calendar day. The exact duplicate predicate
    # still decides whether the coarse candidate really overlaps.
    coarse = tuple(
        f"century:{century}"
        for century in range((start.year - 1) // 100, (end.year - 1) // 100 + 1)
    )
    if span_days > 92:
        return coarse
    daily = tuple(
        (start + timedelta(days=offset)).isoformat()
        for offset in range(span_days)
    )
    return (*daily, *coarse)


def _normalized_city(value: str) -> str:
    city = comparison_text(re.sub(r"\s*\([^)]*\)\s*$", "", value or ""))
    if city.startswith("bonn ") or city in {"bad godesberg", "rheinaue", "poppelsdorf"}:
        return "bonn"
    if city.startswith("koeln "):
        return "koeln"
    return city


def _same_explicit_start(left: str | None, right: str | None) -> bool:
    """Compare equivalent ISO timestamps independent of seconds formatting."""
    if left == right:
        return True
    if left is None or right is None:
        return False
    try:
        return datetime.fromisoformat(left.replace("Z", "+00:00")) == datetime.fromisoformat(
            right.replace("Z", "+00:00")
        )
    except ValueError:
        return False


def _reviewed_occurrence_alias_family(event: Mapping[str, Any]) -> str:
    """Return a source-backed identity for a reviewed civic calendar mismatch."""
    if _normalized_city(event.get("city", "")) != "bonn":
        return ""
    source = " ".join(str(event.get("source") or "").casefold().split())
    source_id = str(event.get("source_id") or "").strip()
    title = normalize_title(event.get("title", ""))
    venue = _venue_comparison_text(event)
    place_copy = comparison_text(
        f"{event.get('venue', '')} {event.get('description', '')}"
    )
    if "rigal" in place_copy and "flohmarkt" in comparison_text(event.get("title", "")):
        return "bonn-rigalsche-wiese-flohmarkt"
    source_title_alias = _impl_dedup_rules._REVIEWED_OCCURRENCE_SOURCE_TITLE_ALIASES.get(
        (source_id, title), ""
    )
    if source_title_alias:
        return source_title_alias
    if (
        source == "bonn district festivals"
        and title == "weinfest"
        and venue == "muensterplatz"
    ):
        return "bonn-muensterplatz-weinfest"
    if (
        source == "bonn.de events"
        and title == "weinfestaufdembonnermuensterplatz"
        and venue in {"muensterplatz", "winzergemeinschaft bonn"}
    ):
        return "bonn-muensterplatz-weinfest"
    if (
        source == "bonn.de events"
        and title == "mirecourtplatzkonzert"
        and venue == "mirecourtplatz bonn beuel"
    ):
        return "bonn-mirecourtplatz-mitsingkonzert"
    if (
        source == "dein-phonzimmer.de"
        and title == "mitsingkonzertfranzoesischundkoelsch"
        and venue == "mirecourtplatz"
    ):
        return "bonn-mirecourtplatz-mitsingkonzert"
    return ""


def _reviewed_occurrence_alias_matches(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    left_start = str(left.get("start_at") or "")
    right_start = str(right.get("start_at") or "")
    if left_start and right_start and not _same_explicit_start(left_start, right_start):
        return False
    left_family = _reviewed_occurrence_alias_family(left)
    return bool(left_family and left_family == _reviewed_occurrence_alias_family(right))


def _citywide_title_family(title: str) -> str:
    """Identify event formats whose source calendars commonly name an area differently."""
    words = comparison_text(title)
    if "street food festival" in words:
        return "street-food-festival"
    return ""


def _citywide_venue_alias_family(event: Mapping[str, Any], title_family: str) -> int | None:
    """Return the reviewed area-alias group for a citywide event format."""
    venue = _venue_comparison_text(event)
    for index, aliases in enumerate(
        _impl_dedup_rules._CITYWIDE_VENUE_ALIAS_FAMILIES.get(title_family, ())
    ):
        if venue in aliases:
            return index
    return None


def _concrete_numeric_units(value: str) -> set[str]:
    value = re.sub(r"[\u2010-\u2015\u2212]", "-", value.casefold())
    return {
        re.sub(r"\s+", "", match)
        for match in re.findall(
            r"(?<!\d)\d{1,4}(?:(?:\s*[a-z])|(?:\s*[/-]\s*\d{1,4}\s*[a-z]?))?(?!\d)",
            value,
        )
    }


def _concrete_venue_units(event: Mapping[str, Any]) -> set[str]:
    # A structured address is stronger place evidence than numbers embedded in
    # the venue name (for example "Halle 1"). Only fall back to the name when
    # the address carries no house or unit number.
    address_units = _concrete_numeric_units(str(event.get("venue_address", "")))
    return address_units or _concrete_numeric_units(str(event.get("venue", "")))


def _locations_compatible(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    left_venue_text = _venue_comparison_text(left)
    right_venue_text = _venue_comparison_text(right)
    left_venue = comparison_text(left_venue_text, separator="")
    right_venue = comparison_text(right_venue_text, separator="")
    left_venue_tokens = set(left_venue_text.split())
    right_venue_tokens = set(right_venue_text.split())
    # Ignore five-digit postcodes but retain house/unit numbers, including
    # forms such as 10a. Shared postcodes must not hide a 10-vs-100 conflict.
    left_venue_numbers = _concrete_venue_units(left)
    right_venue_numbers = _concrete_venue_units(right)
    cities_match = (
        _normalized_city(left.get("city", ""))
        == _normalized_city(right.get("city", ""))
    )
    if cities_match:
        # Concrete address evidence outranks source ownership and fuzzy venue
        # aliases. This prevents same-source records at house numbers 10 and
        # 100 (or units 10a and 10b) from collapsing into one occurrence.
        if (
            left_venue_numbers
            and right_venue_numbers
            and left_venue_numbers.isdisjoint(right_venue_numbers)
        ):
            return False
        # A cultural complex may expose the building and an in-house theatre as
        # separate registered venues.  Matching full structured addresses with
        # a concrete unit is stronger place evidence than those differing labels.
        left_address_raw = str(left.get("venue_address") or "")
        right_address_raw = str(right.get("venue_address") or "")
        left_address = comparison_text(left_address_raw)
        right_address = comparison_text(right_address_raw)
        if (
            left_address
            and left_address == right_address
            and _concrete_numeric_units(left_address_raw)
            and _concrete_numeric_units(right_address_raw)
        ):
            return True
        # A stable detail URL is stronger continuity evidence than a changed
        # venue label on a retained record from the same publisher.
        left_link = _normalized_link_key(left.get("link", ""))
        right_link = _normalized_link_key(right.get("link", ""))
        if (
            left_link
            and left_link == right_link
            and left.get("source") == right.get("source")
        ):
            return True
        if _reviewed_occurrence_alias_matches(left, right):
            return True
        left_title = normalize_title(left.get("title", ""))
        right_title = normalize_title(right.get("title", ""))
        left_place_copy = comparison_text(
            f"{left.get('venue', '')} {left.get('description', '')}"
        )
        right_place_copy = comparison_text(
            f"{right.get('venue', '')} {right.get('description', '')}"
        )
        if (
            "rigal" in left_place_copy
            and "rigal" in right_place_copy
            and "flohmarkt" in comparison_text(left.get("title", ""))
            and "flohmarkt" in comparison_text(right.get("title", ""))
        ):
            return True
        if (
            left_title == right_title
            and left.get("source") != right.get("source")
            and re.search(r"(?:floh|troedel|trödel|antik|kunst|abend)?markt", left_venue_text + " " + str(left.get("title", "")), re.I)
        ):
            return True
        if left_title == right_title and (not left_venue or not right_venue):
            return True
        left_citywide_family = _citywide_title_family(left.get("title", ""))
        right_citywide_family = _citywide_title_family(right.get("title", ""))
        if left_title == right_title and left_citywide_family == right_citywide_family:
            left_alias_family = _citywide_venue_alias_family(
                left, left_citywide_family
            )
            right_alias_family = _citywide_venue_alias_family(
                right, right_citywide_family
            )
            if left_alias_family is not None and left_alias_family == right_alias_family:
                return True
        if any(
            left_venue_text in aliases and right_venue_text in aliases
            for aliases in _impl_dedup_rules._REVIEWED_VENUE_ALIAS_FAMILIES
        ):
            return True
        if not left_venue or not right_venue:
            return True
        return bool(
            left_venue == right_venue
            or left_venue in right_venue
            or right_venue in left_venue
            or SequenceMatcher(None, left_venue, right_venue).ratio() >= 0.82
            or (
                min(len(left_venue_tokens), len(right_venue_tokens)) >= 2
                and (
                    left_venue_tokens <= right_venue_tokens
                    or right_venue_tokens <= left_venue_tokens
                )
            )
        )
    if left_venue and left_venue == right_venue:
        return True
    left_title = normalize_title(left.get("title", ""))
    right_title = normalize_title(right.get("title", ""))
    return (
        not left_venue
        and not right_venue
        and min(len(left_title), len(right_title)) >= 24
        and SequenceMatcher(None, left_title, right_title).ratio() >= 0.92
    )


def _venue_comparison_text(event: Mapping[str, Any]) -> str:
    """Normalize a venue while ignoring a redundant leading city label."""
    venue = comparison_text(event.get("venue", ""))
    venue = re.sub(r"^treffpunkt\s+", "", venue)
    if len(comparison_text(venue, separator="")) < 2:
        return ""
    city = _normalized_city(event.get("city", ""))
    if city and venue.startswith(f"{city} "):
        return venue[len(city) + 1:]
    return venue


def _date_bounds(ev: Mapping[str, Any]) -> tuple[date, date] | None:
    """Return the inclusive date interval represented by an event."""
    start_value = ev.get("start_date") or (ev.get("date", "") or "").split("–", 1)[0]
    end_value = ev.get("end_date") or start_value
    try:
        start = date.fromisoformat(str(start_value))
        end = date.fromisoformat(str(end_value))
    except ValueError:
        return None
    return (start, max(start, end))


def _same_occurrence(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    """Return whether two records describe the same city/date occurrence."""
    # A first-party calendar may offer the same programme several times on one
    # day.  Those are separate bookable occurrences, not duplicate metadata.
    if (
        str(left.get("start_at") or "")[:10]
        == str(right.get("start_at") or "")[:10]
        and left.get("start_at")
        and right.get("start_at")
        and not _same_explicit_start(left["start_at"], right["start_at"])
    ):
        return False
    left_bounds = _date_bounds(left)
    right_bounds = _date_bounds(right)
    if left_bounds and right_bounds:
        if left.get("source") != right.get("source"):
            # Independent calendars frequently disagree by one day about when
            # a multi-day festival ends (for example Sunday versus Monday).
            # Matching the same start date with only that narrow discrepancy is
            # conservative enough to fold the duplicate without absorbing a
            # separately scheduled occurrence later inside a longer run.
            dates_match = (
                left_bounds == right_bounds
                or (
                    left_bounds[0] == right_bounds[0]
                    and abs((left_bounds[1] - right_bounds[1]).days) <= 1
                )
            )
            if not dates_match:
                left_single_day = left_bounds[0] == left_bounds[1]
                right_single_day = right_bounds[0] == right_bounds[1]
                syndicated_daily_occurrence = (
                    (
                        normalize_title(left.get("title", ""))
                        == normalize_title(right.get("title", ""))
                        or _reviewed_occurrence_alias_matches(left, right)
                    )
                    and min(
                        _impl_dedup_rules.source_authority(left.get("source", "")),
                        _impl_dedup_rules.source_authority(right.get("source", "")),
                    ) <= 2
                    and (
                        (left_single_day and right_bounds[0] <= left_bounds[0] <= right_bounds[1])
                        or (right_single_day and left_bounds[0] <= right_bounds[0] <= left_bounds[1])
                    )
                )
                dates_match = syndicated_daily_occurrence
        else:
            dates_match = (left_bounds[0] <= right_bounds[1]
                           and right_bounds[0] <= left_bounds[1])
    else:
        dates_match = (_dedup_key(left).rsplit("|", 1)[-1]
                       == _dedup_key(right).rsplit("|", 1)[-1])
    return dates_match and _locations_compatible(left, right)


def _duration_days(ev: Mapping[str, Any]) -> int:
    bounds = _date_bounds(ev)
    return (bounds[1] - bounds[0]).days if bounds else 0


def _titles_match(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    """Match exact titles and very close cross-source title variants."""
    left_title = normalize_title(left.get("title", ""))
    right_title = normalize_title(right.get("title", ""))
    if left_title == right_title:
        return True
    if _reviewed_occurrence_alias_matches(left, right):
        return True
    # Fair calendars inconsistently add a locative "in" and the edition year.
    # The funfair taxonomy plus the independent date/place guards make this a
    # narrow event-family identity rule rather than a global stop-word rewrite.
    left_funfair = _funfair_title_identity(left)
    right_funfair = _funfair_title_identity(right)
    if left_funfair and left_funfair == right_funfair:
        return True
    left_without_year = tuple(
        word for word in comparison_text(left.get("title", "")).split()
        if not re.fullmatch(r"(?:19|20)\d{2}", word)
    )
    right_without_year = tuple(
        word for word in comparison_text(right.get("title", "")).split()
        if not re.fullmatch(r"(?:19|20)\d{2}", word)
    )
    recurring_title = " ".join(left_without_year)
    if (
        left_without_year
        and left_without_year == right_without_year
        and re.search(r"(?:sommer|stadtteil|strassen|straßen|dorf|wein|kunst|abend)fest$", recurring_title)
    ):
        return True
    left_words = tuple(comparison_text(left.get("title", "")).split())
    right_words = tuple(comparison_text(right.get("title", "")).split())
    shorter, longer = (
        (left_words, right_words) if len(left_words) <= len(right_words)
        else (right_words, left_words)
    )
    word_containment = bool(shorter) and any(
        longer[index:index + len(shorter)] == shorter
        for index in range(len(longer) - len(shorter) + 1)
    )
    if not word_containment:
        shorter_flat = "".join(shorter)
        longer_flat = "".join(longer)
        start = longer_flat.find(shorter_flat)
        boundaries = {0}
        total = 0
        for word in longer:
            total += len(word)
            boundaries.add(total)
        word_containment = (
            start >= 0
            and start in boundaries
            and start + len(shorter_flat) in boundaries
        )
    if min(len(left_title), len(right_title)) >= 12 and word_containment:
        return True
    return SequenceMatcher(None, left_title, right_title).ratio() >= 0.88


def _funfair_title_identity(event: Mapping[str, Any]) -> tuple[str, ...]:
    if "funfair" not in (event.get("event_types") or ()):
        return ()
    identity = tuple(
        token
        for token in comparison_text(event.get("title", "")).split()
        if token != "in" and not re.fullmatch(r"20\d{2}", token)
    )
    # "Kirmes" alone is not an identity: two neighbourhood fairs can share a
    # date while an aggregator omits both exact places. Require the place/name
    # token that makes the reviewed Röttgen variant safe to compare.
    return identity if len(identity) >= 2 else ()


def _same_funfair_title_identity(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    left_identity = _funfair_title_identity(left)
    return bool(left_identity and left_identity == _funfair_title_identity(right))


def _title_words_without_venue_suffix(event: Mapping[str, Any]) -> tuple[str, ...]:
    """Drop an exact venue suffix added by directory-style event titles."""
    words = tuple(comparison_text(event.get("title", "")).split())
    venue_words = tuple(_venue_comparison_text(event).split())
    if venue_words and len(words) > len(venue_words) and words[-len(venue_words):] == venue_words:
        return words[:-len(venue_words)]
    return words


def _aggregator_title_variant_matches(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    """Match a concise aggregator title to a fuller authoritative title."""
    left_authority = _impl_dedup_rules.source_authority(left.get("source", ""))
    right_authority = _impl_dedup_rules.source_authority(right.get("source", ""))
    if min(left_authority, right_authority) > 1 or max(left_authority, right_authority) < 2:
        return False
    source_ids = {left.get("source_id", ""), right.get("source_id", "")}
    left_start = left.get("start_at")
    right_start = right.get("start_at")
    if left_start and right_start and not _same_explicit_start(left_start, right_start):
        return False
    if "kunstrasen-bonn" not in source_ids and (not left_start or not right_start):
        return False
    left_words = set(_title_words_without_venue_suffix(left))
    right_words = set(_title_words_without_venue_suffix(right))
    # KUNST!RASEN's first-party ticket shop deliberately uses the headliner as
    # its concise title, while Bonn.jetzt appends guests or tour copy.  The
    # shared exact start, venue, date, category and authority guards above keep
    # this exception occurrence-specific instead of turning artist names into
    # global aliases.
    if "kunstrasen-bonn" in source_ids:
        shorter, longer = sorted((left_words, right_words), key=len)
        if shorter <= longer and len(shorter) >= 1 and len("".join(shorter)) >= 3:
            return True
    return (
        min(len(left_words), len(right_words)) >= 3
        and (left_words <= right_words or right_words <= left_words)
    )


def _venue_qualified_aggregator_title_matches(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    """Match a directory clone whose title appends its exact venue.

    The venue, city, category and occurrence checks remain independent guards.
    Removing only an exact suffix avoids treating a genuinely named programme
    point at the same fair as another spelling of the fair itself.
    """
    left_authority = _impl_dedup_rules.source_authority(left.get("source", ""))
    right_authority = _impl_dedup_rules.source_authority(right.get("source", ""))
    if min(left_authority, right_authority) > 1 or max(left_authority, right_authority) < 2:
        return False
    if not left.get("category_key") or left.get("category_key") != right.get("category_key"):
        return False
    if _normalized_city(left.get("city", "")) != _normalized_city(right.get("city", "")):
        return False
    left_venue = _venue_comparison_text(left)
    right_venue = _venue_comparison_text(right)
    if not left_venue or left_venue != right_venue:
        return False
    left_words = _title_words_without_venue_suffix(left)
    right_words = _title_words_without_venue_suffix(right)
    if min(len(left_words), len(right_words)) < 2:
        return False
    left_title = "".join(left_words)
    right_title = "".join(right_words)
    return (
        min(len(left_title), len(right_title)) >= 12
        and SequenceMatcher(None, left_title, right_title).ratio() >= 0.92
    )


def _series_tokens(title: str) -> tuple[str, ...]:
    """Return numeric and explicit Roman-numeral episode markers in a title."""
    words = comparison_text(title)
    numbers = [
        token for token in re.findall(r"\b\d+\b", words)
        if not re.fullmatch(r"(?:19|20)\d{2}", token)
    ]
    roman_episodes = re.findall(
        r"\b(?:teil|folge|part|episode|band|kapitel)\s+([ivxlcdm]+)\b",
        words,
    )
    return tuple(numbers + [f"roman:{token}" for token in roman_episodes])


def _same_registered_venue_occurrence(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    """Match cross-source records by canonical venue, date, and category."""
    if not left.get("source") or left.get("source") == right.get("source"):
        return False
    # A museum or theatre can host several events in the same category on one
    # day. Distinct explicit starts normally prove separate occurrences. Market
    # sources are the exception only when their normalized names share specific
    # evidence beyond the venue and broad market format.
    left_start = left.get("start_at")
    right_start = right.get("start_at")
    start_times_conflict = bool(
        left_start
        and right_start
        and not _same_explicit_start(left_start, right_start)
    )
    left_venue_id = left.get("venue_id")
    left_category = left.get("category_key")
    left_bounds = _date_bounds(left)
    right_bounds = _date_bounds(right)
    same_identity = bool(
        left_venue_id
        and left_venue_id == right.get("venue_id")
        and left_category
        and left_category == right.get("category_key")
        and left_bounds is not None
        and right_bounds is not None
    )
    if not same_identity or left_bounds is None or right_bounds is None:
        return False
    if left_category != "market":
        return not start_times_conflict and left_bounds == right_bounds
    # A registered market area may host different market formats on one day.
    # Require the same narrow family before accepting exact or overlapping
    # source ranges (for example a city listing with only the first day).
    left_family = _market_title_family(left.get("title", ""))
    right_family = _market_title_family(right.get("title", ""))
    return bool(
        left_family
        and left_family == right_family
        and left_bounds[0] <= right_bounds[1]
        and right_bounds[0] <= left_bounds[1]
        and (
            not start_times_conflict
            or _market_title_evidence_matches(left, right)
        )
    )


def _market_title_family(title: str) -> str:
    """Return a conservative market format shared across title variants."""
    words = comparison_text(title)
    if "antik" in words and "markt" in words:
        return "antik"
    if any(marker in words for marker in ("floh", "troedel")):
        return "secondhand"
    if "wochenmarkt" in words:
        return "weekly"
    if any(marker in words for marker in ("kunsthandwerkermarkt", "kunstmarkt")):
        return "craft"
    return ""


_MARKET_TITLE_GENERIC_WORDS = frozenset({
    "am", "an", "auf", "aus", "bei", "bonn", "das", "der", "die", "ein",
    "eine", "ferien", "flohmarkt", "im", "in", "markt", "mit", "und", "vom",
    "von", "zur", "zum", "troedelmarkt",
})


def _market_title_evidence_tokens(event: Mapping[str, Any]) -> set[str]:
    """Keep the title words that identify one market beyond venue and format."""
    title_words = set(comparison_text(event.get("title", "")).split())
    venue_words = set(_venue_comparison_text(event).split())
    return title_words - venue_words - _MARKET_TITLE_GENERIC_WORDS


def _market_title_evidence_matches(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    """Require shared normalized naming evidence when source times disagree."""
    left_tokens = _market_title_evidence_tokens(left)
    right_tokens = _market_title_evidence_tokens(right)
    return bool(left_tokens and right_tokens and left_tokens & right_tokens)


def _has_separate_admission_charge(event: Mapping[str, Any]) -> bool:
    text = " ".join((event.get("description", ""), event.get("price", ""))).casefold()
    return common.has_paid_visitor_access(text)


def _paid_admission_identity(event: Mapping[str, Any]) -> tuple[object, str] | None:
    admission = event.get("admission")
    if not isinstance(admission, dict) or admission.get("isFree") is not False:
        return None
    amount = admission.get("amount")
    if amount is None or isinstance(amount, bool):
        return None
    currency = str(admission.get("currency") or "").strip().upper()
    return amount, currency


def _price_has_currency(value: object) -> bool:
    return bool(re.search(r"(?:€|\bEUR\b|\bEuro\b)", str(value or ""), re.I))


def _is_radio_aggregation_link(link: str) -> bool:
    parsed = urlparse.urlsplit(link or "")
    hostname = (parsed.hostname or "").casefold().removeprefix("www.")
    return (
        hostname == "radiobonn.de"
        and parsed.path.rstrip("/")
        == "/artikel/was-geht-unsere-veranstaltungstipps-2674962"
    )


def events_are_duplicates(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    """Return whether two canonical records represent the same occurrence."""
    performance.count("dedup_comparisons")
    left_years = set(re.findall(r"\b(?:19|20)\d{2}\b", str(left.get("title", ""))))
    right_years = set(re.findall(r"\b(?:19|20)\d{2}\b", str(right.get("title", ""))))
    left_without_year = tuple(
        token for token in comparison_text(left.get("title", "")).split()
        if not re.fullmatch(r"(?:19|20)\d{2}", token)
    )
    right_without_year = tuple(
        token for token in comparison_text(right.get("title", "")).split()
        if not re.fullmatch(r"(?:19|20)\d{2}", token)
    )
    same_yearless_title = bool(
        left_without_year and left_without_year == right_without_year
    )
    recurring_edition = bool(
        same_yearless_title
        and re.search(
            r"(?:sommer|stadtteil|strassen|straßen|dorf|wein|kunst|abend)fest$",
            " ".join(left_without_year),
        )
    )
    if (
        same_yearless_title
        and left_years != right_years
        and not recurring_edition
        and not _same_funfair_title_identity(left, right)
    ):
        return False
    if (
        _series_tokens(left.get("title", ""))
        != _series_tokens(right.get("title", ""))
        and not _same_funfair_title_identity(left, right)
    ):
        return False
    left_link = _normalized_link_key(left.get("link", ""))
    right_link = _normalized_link_key(right.get("link", ""))
    same_detail_occurrence = bool(
        left_link
        and left_link == right_link
        and left.get("source") != right.get("source")
        and _titles_match(left, right)
        and _date_bounds(left) == _date_bounds(right)
        and (
            not left.get("start_at")
            or not right.get("start_at")
            or _same_explicit_start(left.get("start_at"), right.get("start_at"))
        )
    )
    return (
        same_detail_occurrence
        or _same_registered_venue_occurrence(left, right)
        or (
            _same_occurrence(left, right)
            and (
                _titles_match(left, right)
                or _aggregator_title_variant_matches(left, right)
                or _venue_qualified_aggregator_title_matches(left, right)
            )
        )
    )
