"""
Deduplication, ranking, and Markdown report rendering.

Pure presentation + post-processing. No network, no source-specific logic.
"""

import os
import re
from collections import defaultdict
from dataclasses import replace
from datetime import date, datetime
from difflib import SequenceMatcher
from urllib import parse as urlparse

from . import common
from .models import CanonicalEvent
from .normalization import comparison_text


# Kept separate from ``score``: score includes distance and topical relevance,
# while authority decides which publisher owns the canonical record.
_AGGREGATOR_SOURCE_MARKERS = (
    "bonn.jetzt", "eventbrite", "meetup", "radio bonn", "ruhr-guide",
    "kinderflohmarkt.com",
)
# Third-party market directories relist the same occurrences that the market
# organizers publish themselves, and they keep serving records the organizer has
# already cancelled. They must never win a dedup contest against the organizer,
# so they are ranked with the aggregators rather than defaulting to the
# direct-publisher tier.
#
# ``krencky24``, ``meine-flohmarkt-termine`` and ``meine-kunsthandwerker-termine``
# are one operator (Kampagne Spezial GmbH) serving one database from a single
# host, so they also relist each other. Prefer integrating a single frontend.
_MARKET_DIRECTORY_SOURCE_MARKERS = (
    "marktcom", "krencky24", "meine-flohmarkt-termine",
    "meine-kunsthandwerker-termine", "flohmarkt-termine", "flohmap",
)
_CIVIC_AGGREGATOR_SOURCE_MARKERS = (
    "bonn.de events", "bonn.de sports", "bonn district festivals",
)
_SEARCH_SOURCE_MARKERS = ("exa search", "grok search")
_REUSED_OVERVIEW_LINK_THRESHOLD = 5
_CITYWIDE_VENUE_ALIAS_FAMILIES = {
    "street-food-festival": (
        frozenset({
            "theaterplatz",
            "bad godesberger innenstadt",
            "innenstadt bad godesberg",
        }),
    ),
}
_VENUE_LOCATION_FIELDS = (
    "venue_id",
    "venue_address",
    "venue_district",
    "venue_type",
    "venue_latitude",
    "venue_longitude",
    "distance_km",
    "location_confidence",
    "location_source",
)


def source_authority(source: str) -> int:
    """Rank direct/local publishers above aggregators and search discovery."""
    normalized = " ".join((source or "").casefold().split())
    if any(marker in normalized for marker in _SEARCH_SOURCE_MARKERS):
        return 0
    if any(marker in normalized for marker in _AGGREGATOR_SOURCE_MARKERS):
        return 1
    if any(marker in normalized for marker in _MARKET_DIRECTORY_SOURCE_MARKERS):
        return 1
    if any(marker in normalized for marker in _CIVIC_AGGREGATOR_SOURCE_MARKERS):
        return 2
    return 3


# ── Dedup ───────────────────────────────────────────────────────────

def normalize_title(title: str) -> str:
    """Aggressively normalize a title for near-duplicate comparison."""
    t = (title or "").casefold().strip()
    t = re.sub(
        r"^\s*[-–—:()]*\s*(?:abgesagt|entfällt|entfaellt|fällt\s+aus|"
        r"faellt\s+aus)\s*[-–—:()]*\s*",
        "",
        t,
    )
    t = re.sub(r"^(ausstellung[:\s]*|exhibition[:\s]*|konzert[:\s]*|concert[:\s]*|kostenloser\s+eintritt[:\s]*|eintritt\s+frei[:\s]*|tickets?\s+für\s+)", "", t)
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


def _link_identity_counts(events: list) -> dict[str, int]:
    """Count distinct event identities per URL, not dates or syndicated records."""
    identities_by_link = defaultdict(set)
    for event in events:
        link = event.get("link", "")
        if not link:
            continue
        identity = (
            normalize_title(event.get("title", "")),
            normalize_title(event.get("venue", "")),
        )
        identities_by_link[_normalized_link_key(link)].add(identity)
    return {link: len(identities) for link, identities in identities_by_link.items()}


def _dedup_key(ev: dict) -> str:
    """Occurrence key: recurring appointments on different dates must survive."""
    norm = normalize_title(ev.get("title", ""))
    city = _normalized_city(ev.get("city", ""))
    start_date = ev.get("start_date") or (ev.get("date", "") or "").split("–", 1)[0]
    return "|".join((norm, city, str(start_date)))


def _normalized_city(value: str) -> str:
    city = comparison_text(re.sub(r"\s*\([^)]*\)\s*$", "", value or ""))
    if city.startswith("bonn ") or city in {"bad godesberg", "rheinaue", "poppelsdorf"}:
        return "bonn"
    if city.startswith("koeln "):
        return "koeln"
    return city


def _citywide_title_family(title: str) -> str:
    """Identify event formats whose source calendars commonly name an area differently."""
    words = comparison_text(title)
    if "street food festival" in words:
        return "street-food-festival"
    return ""


def _citywide_venue_alias_family(event: dict, title_family: str) -> int | None:
    """Return the reviewed area-alias group for a citywide event format."""
    venue = _venue_comparison_text(event)
    for index, aliases in enumerate(
        _CITYWIDE_VENUE_ALIAS_FAMILIES.get(title_family, ())
    ):
        if venue in aliases:
            return index
    return None


def _locations_compatible(left: dict, right: dict) -> bool:
    left_venue_text = _venue_comparison_text(left)
    right_venue_text = _venue_comparison_text(right)
    left_venue = comparison_text(left_venue_text, separator="")
    right_venue = comparison_text(right_venue_text, separator="")
    left_venue_tokens = set(left_venue_text.split())
    right_venue_tokens = set(right_venue_text.split())
    cities_match = (
        _normalized_city(left.get("city", ""))
        == _normalized_city(right.get("city", ""))
    )
    if cities_match:
        if left.get("source") and left.get("source") == right.get("source"):
            return True
        left_title = normalize_title(left.get("title", ""))
        right_title = normalize_title(right.get("title", ""))
        if (
            left_title == right_title
            and any(
                marker in left_title
                for marker in ("flohmarkt", "trödelmarkt", "antikmarkt")
            )
        ):
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
        if not left_venue or not right_venue:
            return True
        if (
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
        ):
            return True
        # A production detail URL can be reused for performances at multiple
        # venues, so it is not enough to override a concrete venue conflict.
        return False
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


def _venue_comparison_text(event: dict) -> str:
    """Normalize a venue while ignoring a redundant leading city label."""
    venue = comparison_text(event.get("venue", ""))
    if len(comparison_text(venue, separator="")) < 2:
        return ""
    city = _normalized_city(event.get("city", ""))
    if city and venue.startswith(f"{city} "):
        return venue[len(city) + 1:]
    return venue


def _date_bounds(ev: dict) -> tuple[date, date] | None:
    """Return the inclusive date interval represented by an event."""
    start_value = ev.get("start_date") or (ev.get("date", "") or "").split("–", 1)[0]
    end_value = ev.get("end_date") or start_value
    try:
        start = date.fromisoformat(str(start_value))
        end = date.fromisoformat(str(end_value))
    except ValueError:
        return None
    return (start, max(start, end))


def _same_occurrence(left: dict, right: dict) -> bool:
    """Return whether two records describe the same city/date occurrence."""
    # A first-party calendar may offer the same programme several times on one
    # day.  Those are separate bookable occurrences, not duplicate metadata.
    if (
        left.get("source") == right.get("source")
        and left.get("start_at")
        and right.get("start_at")
        and left["start_at"] != right["start_at"]
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
        else:
            dates_match = (left_bounds[0] <= right_bounds[1]
                           and right_bounds[0] <= left_bounds[1])
    else:
        dates_match = (_dedup_key(left).rsplit("|", 1)[-1]
                       == _dedup_key(right).rsplit("|", 1)[-1])
    return dates_match and _locations_compatible(left, right)


def _duration_days(ev: dict) -> int:
    bounds = _date_bounds(ev)
    return (bounds[1] - bounds[0]).days if bounds else 0


def _titles_match(left: dict, right: dict) -> bool:
    """Match exact titles and very close cross-source title variants."""
    if _series_tokens(left.get("title", "")) != _series_tokens(right.get("title", "")):
        return False
    left_title = normalize_title(left.get("title", ""))
    right_title = normalize_title(right.get("title", ""))
    if left_title == right_title:
        return True
    if min(len(left_title), len(right_title)) >= 12 and (
        left_title in right_title or right_title in left_title
    ):
        return True
    return SequenceMatcher(None, left_title, right_title).ratio() >= 0.88


def _aggregator_title_variant_matches(left: dict, right: dict) -> bool:
    """Match a concise aggregator title to a fuller authoritative title."""
    left_authority = source_authority(left.get("source", ""))
    right_authority = source_authority(right.get("source", ""))
    if min(left_authority, right_authority) > 1 or max(left_authority, right_authority) < 2:
        return False
    left_start = left.get("start_at")
    right_start = right.get("start_at")
    if not left_start or left_start != right_start:
        return False
    left_words = set(comparison_text(left.get("title", "")).split())
    right_words = set(comparison_text(right.get("title", "")).split())
    return (
        min(len(left_words), len(right_words)) >= 3
        and (left_words <= right_words or right_words <= left_words)
    )


def _series_tokens(title: str) -> tuple[str, ...]:
    """Return numeric and explicit Roman-numeral episode markers in a title."""
    words = comparison_text(title)
    numbers = re.findall(r"\b\d+\b", words)
    roman_episodes = re.findall(
        r"\b(?:teil|folge|part|episode|band|kapitel)\s+([ivxlcdm]+)\b",
        words,
    )
    return tuple(numbers + [f"roman:{token}" for token in roman_episodes])


def _same_registered_venue_occurrence(left: dict, right: dict) -> bool:
    """Match cross-source records by canonical venue, date, and category."""
    if not left.get("source") or left.get("source") == right.get("source"):
        return False
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
    if not same_identity:
        return False
    if left_category != "market":
        return left_bounds == right_bounds
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


def _has_separate_admission_charge(event) -> bool:
    text = " ".join((event.get("description", ""), event.get("price", ""))).casefold()
    return bool(re.search(
        r"museumseintritt\s+(?:fällt|faellt)\s+zusätzlich\s+an|"
        r"regulärer\s+museumseintritt\s+ist\s+erforderlich|"
        r"kostenlos\s+zzgl\.?\s+eintritt|"
        r"kostenlos[^.]{0,80}(?:zuzüglich|zuzueglich)\s+(?:museum)?eintritt",
        text,
    ))


def _adopted_description(source: dict) -> dict:
    """Take a duplicate's copy as a unit.

    The markup renders one particular description. Adopting the text without it
    left the winner's own markup behind, so the page showed a generated
    "findet am … statt" line above the real write-up it was supposed to render.
    """
    return {
        "description": source["description"],
        "description_source": source.get("description_source", "scraped"),
        "description_html": source.get("description_html", ""),
    }


def _merge_duplicate_metadata(winner, duplicate, *, link_identity_counts=None):
    """Keep the authoritative record and enrich it field by field."""
    updates = {}
    winner_start = winner.get("start_at")
    winner_end = winner.get("end_at")
    duplicate_start = duplicate.get("start_at")
    duplicate_end = duplicate.get("end_at")
    if (
        winner_start
        and winner_end == winner_start
        and duplicate_start == winner_start
        and duplicate_end
        and duplicate_end > winner_end
    ):
        updates["end_at"] = duplicate_end
        if duplicate.get("time"):
            updates["time"] = duplicate["time"]
    separate_admission_charge = (
        _has_separate_admission_charge(winner)
        or _has_separate_admission_charge(duplicate)
    )
    if separate_admission_charge:
        updates["price"] = ""
        updates["admission_basis"] = ""
        updates["admission"] = {
            "isFree": None,
            "amount": None,
            "currency": "EUR",
            "basis": "",
            "note": "",
            "donationSuggested": False,
        }
    for field in ("price", "venue", "time", "time_note", "start_at", "end_at"):
        if field == "price" and separate_admission_charge:
            continue
        winner_value_is_missing = not winner.get(field)
        winner_venue_is_implausible = field == "venue" and not _venue_comparison_text(winner)
        duplicate_value_is_usable = bool(duplicate.get(field)) and (
            field != "venue" or bool(_venue_comparison_text(duplicate))
        )
        if (winner_value_is_missing or winner_venue_is_implausible) and duplicate_value_is_usable:
            updates[field] = duplicate[field]
            if field == "price":
                updates["admission_basis"] = duplicate.get("admission_basis", "")
                updates["admission"] = duplicate.get("admission")
            elif winner_venue_is_implausible:
                for location_field in _VENUE_LOCATION_FIELDS:
                    updates[location_field] = duplicate.get(location_field)

    if (
        winner.get("price")
        and not winner.get("admission_basis")
        and duplicate.get("price") == winner.get("price")
        and duplicate.get("admission_basis")
    ):
        updates["admission_basis"] = duplicate["admission_basis"]
        updates["admission"] = duplicate.get("admission")

    winner_link = winner.get("link", "")
    duplicate_link = duplicate.get("link", "")
    link_identity_counts = link_identity_counts or {}
    winner_link_is_reused = (
        winner_link
        and link_identity_counts.get(_normalized_link_key(winner_link), 0)
        >= _REUSED_OVERVIEW_LINK_THRESHOLD
    )
    duplicate_link_is_not_reused = (
        duplicate_link
        and link_identity_counts.get(_normalized_link_key(duplicate_link), 0)
        < _REUSED_OVERVIEW_LINK_THRESHOLD
    )
    if (not winner_link and duplicate_link) or (
        _is_radio_aggregation_link(winner_link)
        and duplicate_link
        and not _is_radio_aggregation_link(duplicate_link)
    ) or (
        winner_link_is_reused
        and duplicate_link_is_not_reused
        and _link_route_depth(duplicate_link) > _link_route_depth(winner_link)
    ):
        updates["link"] = duplicate_link

    duplicate_has_charge = _has_separate_admission_charge(duplicate)
    winner_has_charge = _has_separate_admission_charge(winner)
    if duplicate_has_charge and not winner_has_charge:
        updates.update(_adopted_description(duplicate))
    elif (
        len(duplicate.get("description", "").strip()) > len(winner.get("description", "").strip())
        and not (winner_has_charge and not duplicate_has_charge)
    ):
        updates.update(_adopted_description(duplicate))

    # Classification is derived data, but a broad aggregator label must not
    # override a usable classification from the canonical publisher. Peers may
    # still improve one another, and any source may fill an uncategorized record.
    winner_category = winner.get("category_key")
    category_authority_is_sufficient = (
        winner_category in {None, "", "other"}
        or source_authority(duplicate.get("source", ""))
        >= source_authority(winner.get("source", ""))
    )
    if (duplicate.get("category_key")
            and category_authority_is_sufficient
            and duplicate.get("category_confidence", 0) > winner.get("category_confidence", 0)):
        for field in ("category", "category_key", "category_label", "category_confidence", "category_reason"):
            if duplicate.get(field):
                updates[field] = duplicate[field]

    if isinstance(winner, CanonicalEvent):
        return replace(winner, **updates)
    return {**winner, **updates}


def _is_radio_aggregation_link(link: str) -> bool:
    parsed = urlparse.urlsplit(link or "")
    hostname = (parsed.hostname or "").casefold().removeprefix("www.")
    return (
        hostname == "radiobonn.de"
        and parsed.path.rstrip("/")
        == "/artikel/was-geht-unsere-veranstaltungstipps-2674962"
    )


def events_are_duplicates(left, right) -> bool:
    """Return whether two canonical records represent the same occurrence."""
    if _series_tokens(left.get("title", "")) != _series_tokens(right.get("title", "")):
        return False
    return (
        _same_registered_venue_occurrence(left, right)
        or (
            _same_occurrence(left, right)
            and (
                _titles_match(left, right)
                or _aggregator_title_variant_matches(left, right)
            )
        )
    )


def deduplicate(
    events: list[CanonicalEvent],
    *,
    cancellations: list[dict] | None = None,
) -> list[CanonicalEvent]:
    """Collapse duplicates and apply authoritative cancellation tombstones."""
    link_identity_counts = _link_identity_counts(events)

    def merge_preferred(current, candidate):
        current_rank = (
            source_authority(current.get("source", "")),
            current["score"],
            _duration_days(current),
        )
        candidate_rank = (
            source_authority(candidate.get("source", "")),
            candidate["score"],
            _duration_days(candidate),
        )
        return (
            _merge_duplicate_metadata(
                candidate,
                current,
                link_identity_counts=link_identity_counts,
            )
            if candidate_rank > current_rank
            else _merge_duplicate_metadata(
                current,
                candidate,
                link_identity_counts=link_identity_counts,
            )
        )

    authoritative_cancellations = [
        event
        for event in (cancellations or [])
        if event.get("status") in {"cancelled", "postponed"}
        and source_authority(event.get("source", "")) >= 2
    ]
    result: list = []
    for ev in events:
        if ev.get("status") in {"cancelled", "postponed"}:
            continue
        match_index = next(
            (
                index
                for index in range(len(result))
                if events_are_duplicates(result[index], ev)
            ),
            None,
        )
        if match_index is None:
            result.append(ev)
            continue

        current = result[match_index]
        result[match_index] = merge_preferred(current, ev)

    # Replace the scheduled record with its authoritative schedule change. By
    # keeping the scheduled record's identity fields, the public event ID stays
    # stable when an occurrence changes from scheduled to cancelled.
    for cancellation in authoritative_cancellations:
        match_index = next(
            (
                index for index, scheduled in enumerate(result)
                if source_authority(cancellation.get("source", ""))
                >= source_authority(scheduled.get("source", ""))
                and events_are_duplicates(cancellation, scheduled)
            ),
            None,
        )
        updates = {
            "status": cancellation.get("status", "cancelled"),
            "cancellation_source": cancellation.get("source", ""),
            "replacement_start_date": cancellation.get("replacement_start_date", ""),
            "score": 0.0,
        }
        if match_index is None:
            # A tombstone only means something for an occurrence a visitor could
            # still be looking at. ``cancelled_events`` is filled inside
            # ``make_event``, before the report window is applied, so a source
            # that keeps a months-old "verschoben" entry in its calendar would
            # otherwise publish that past date as a standalone event — listed
            # everywhere, with no detail page, because the site builds pages for
            # current events only.
            if not common.event_in_window(cancellation):
                continue
            if isinstance(cancellation, CanonicalEvent):
                result.append(replace(cancellation, **updates))
            else:
                result.append({**cancellation, **updates})
        elif isinstance(result[match_index], CanonicalEvent):
            result[match_index] = replace(result[match_index], **updates)
        else:
            result[match_index] = {**result[match_index], **updates}

    # Metadata enrichment can make a winner comparable to an earlier result
    # that neither of its inputs matched on its own. Collapse those transitive
    # pairs until the exported set is closed under ``events_are_duplicates``.
    while True:
        duplicate_pair = next(
            (
                (left_index, right_index)
                for right_index in range(1, len(result))
                for left_index in range(right_index)
                if events_are_duplicates(result[left_index], result[right_index])
            ),
            None,
        )
        if duplicate_pair is None:
            break
        left_index, right_index = duplicate_pair
        result[left_index] = merge_preferred(
            result[left_index],
            result[right_index],
        )
        del result[right_index]

    # A recurring series is not a duplicate: each date is a separately usable
    # occurrence. Cross-source authority is therefore resolved only inside the
    # same overlapping date interval by the loop above.
    classified = []
    for event in result:
        link = event.get("link", "")
        link_kind = ""
        if link:
            identity_count = link_identity_counts.get(_normalized_link_key(link), 0)
            link_kind = (
                "overview"
                if identity_count >= _REUSED_OVERVIEW_LINK_THRESHOLD
                else "detail"
            )
        if isinstance(event, CanonicalEvent):
            classified.append(replace(event, link_kind=link_kind))
        else:
            classified.append({**event, "link_kind": link_kind})
    return classified


# ── Report rendering ────────────────────────────────────────────────

CATEGORY_SECTIONS = {
    "nightlife": "Nightlife & Electronic",
    "concert": "Concerts & Live Music",
    "exhibition": "Exhibitions & Museums",
    "stage": "Talks, Community & Culture", "cinema": "Talks, Community & Culture",
    "talk": "Talks, Community & Culture", "workshop": "Talks, Community & Culture",
    "kids": "Talks, Community & Culture", "sports": "Talks, Community & Culture",
    "activities": "Talks, Community & Culture",
    "festival": "Walks, Markets & Outdoor", "market": "Walks, Markets & Outdoor",
    "food": "Walks, Markets & Outdoor", "outdoor": "Walks, Markets & Outdoor",
    "other": "Other",
}


def _bucket(ev: dict) -> str:
    """Map the already-canonical category to exactly one report section."""
    return CATEGORY_SECTIONS[ev.get("category_key", "other")]


def ranking_features(ev: dict) -> dict[str, float]:
    """Return named editorial ranking features without presentation side effects."""
    text = (ev.get("title", "") + " " + ev.get("category", "") + " " + ev.get("description", "")).lower()
    features = {}
    if "flohmarkt" in text:
        features["flea_market"] = 0.5
    if any(k in text for k in ["ahrweinwalk", "weinwanderung", "ahrtal", "ahrweiler"]):
        features["ahr_wine"] = 0.55
    if any(k in text for k in ["stadtteilfest", "straßenfest", "strassenfest", "dorffest",
                               "poppelsdorf", "weinmeile", "genussmeile"]):
        features["local_festival"] = 0.45
    if "antikmarkt" in text:
        features["antique_market"] = 0.3
    if ev.get("city") == "Bonn":
        features["bonn_local"] = 0.1
    return features


def _priority_bonus(ev: dict) -> float:
    return sum(ranking_features(ev).values())


PREFERRED_ORDER = [
    ("Nightlife & Electronic", "🌙"),
    ("Concerts & Live Music", "🎵"),
    ("Exhibitions & Museums", "🏛️"),
    ("Talks, Community & Culture", "🧠"),
    ("Walks, Markets & Outdoor", "🚶"),
    ("Other", "📌"),
]


def format_report(events: list, *, window_start: datetime | None = None,
                  window_end: datetime | None = None, max_per_section: int | None = None) -> str:
    """Render the deduplicated, scored event list into a grouped Markdown report."""
    start = window_start or common.TODAY
    end = window_end or common.END_DATE
    lines = [
        "# 🗓 Weekend Event Report",
        f"**{start.strftime('%A %d %b')} → {end.strftime('%A %d %b %Y')}**",
        f"**Radius:** {common.MAX_RADIUS_KM}km from Bonn",
        f"**Sources:** {len(set(e['source'] for e in events))} active",
        f"**Relevant events after cleanup:** {len(events)}",
        "",
    ]

    grouped = {name: [] for name, _ in PREFERRED_ORDER}
    for ev in sorted(events, key=lambda x: (-(x["score"] + _priority_bonus(x)),
                                            x.get("distance_km") if x.get("distance_km") is not None else 999,
                                            x.get("title", ""))):
        grouped[_bucket(ev)].append(ev)

    if max_per_section is None:
        try:
            max_per_section = int(os.environ.get("NRW_EVENTS_MAX_PER_SECTION", "0"))
        except ValueError:
            max_per_section = 0

    def format_when(ev: dict) -> str:
        parts = []
        if ev.get("date"):
            parts.append(ev["date"])
        if ev.get("time"):
            parts.append(ev["time"])
        return " ".join(parts).strip()

    def format_section(title: str, emoji: str, items: list):
        if not items:
            return
        shown = items if max_per_section <= 0 else items[:max_per_section]
        count_note = f" ({len(items)})" if len(shown) == len(items) else f" ({len(shown)} of {len(items)})"
        lines.append(f"## {emoji} {title}{count_note}")
        lines.append("")
        for ev in shown:
            when = format_when(ev)
            distance = ev.get("distance_km")
            dist_tag = f"{distance}km" if distance and distance > 0 else (
                "Bonn" if distance == 0 else "Ort nicht aufgelöst"
            )
            score_bar = "★" * max(1, min(5, int(round(ev["score"] * 3))))
            meta = []
            if when:
                meta.append(when)
            if ev.get("venue"):
                meta.append(ev["venue"])
            if ev.get("city"):
                meta.append(ev["city"])
            meta.append(dist_tag)
            meta.append(score_bar)
            lines.append(f"- **{ev['title']}**")
            lines.append(f"  {' · '.join(meta)}")
            if ev.get("description"):
                # One markdown list item per event: a real break here would end
                # the emphasis run and split the bullet.
                flat = " ".join(ev["description"].split())
                lines.append(f"  _{flat}_")
            if ev.get("link"):
                lines.append(f"  🔗 {ev['link']}")
            lines.append("")

    for name, emoji in PREFERRED_ORDER:
        format_section(name, emoji, grouped[name])

    lines.append("---")
    lines.append("### Source Status")
    source_counts = {}
    for e in events:
        source_counts[e["source"]] = source_counts.get(e["source"], 0) + 1
    for src, count in sorted(source_counts.items(), key=lambda x: (-x[1], x[0])):
        lines.append(f"- {src}: {count} events")

    return "\n".join(lines)
