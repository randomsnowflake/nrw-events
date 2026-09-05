"""Owning implementation of ai policy; core is a compatibility facade."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from hashlib import sha256
from typing import Any, cast

from . import ai_contracts as _impl_ai_contracts
from . import category_taxonomy, common, richtext
from .core import keep_only_event_master_data
from .models import RawEvent, normalize_source_id


def is_target_event(event: Mapping[str, Any]) -> bool:
    return normalize_source_id(event.get("source_id") or event.get("source")) in _impl_ai_contracts.TARGET_SOURCE_IDS


def strip_restricted_copy(event: RawEvent) -> RawEvent:
    """Enforce the no-source-copy rule and keep failure paths useful.

    A successful AI summary remains the only prose shown for restricted
    sources. If AI is unavailable or rejects the record, publish a generated
    sentence made solely from the event's master data instead of leaving the
    detail page without any visitor-facing description.
    """
    summary_value: object = event.get("ai_summary")
    summary = summary_value if isinstance(summary_value, str) else ""
    event["description"] = ""
    event["description_html"] = ""
    event["description_source"] = "generated"
    event["ai_summary"] = summary
    if not summary.strip():
        keep_only_event_master_data(event)
    return event


def _source_material(event: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for value in (
        event.get("description"),
        richtext.to_plain_text(str(event.get("description_html") or "")),
    ):
        clean = re.sub(r"\s+", " ", str(value or "")).strip()
        if clean and not any(clean.casefold() == existing.casefold() for existing in parts):
            parts.append(clean)
    if parts:
        return "\n\n".join(parts)

    # Some legally restricted calendars expose reliable structured fields but
    # no reusable prose.  Those facts are still sufficient for a short,
    # strictly factual summary; refusing the request entirely would leave the
    # public event page blank.  Keep this label-bound and exclude URLs/source
    # names so the model cannot treat transport metadata as editorial facts.
    labels = (
        ("Titel", event.get("title")),
        ("Datum", event.get("start_date") or event.get("date")),
        ("Enddatum", event.get("end_date")),
        ("Uhrzeit", event.get("time")),
        ("Zeithinweis", event.get("time_note")),
        ("Ort", event.get("venue")),
        ("Adresse", event.get("venue_address")),
        ("Stadt", event.get("city")),
        ("Veranstalter", event.get("organizer")),
        ("Eintritt", event.get("price")),
        ("Kategorie", event.get("category") or event.get("category_key")),
        ("Reihe", event.get("series_title")),
    )
    return "\n".join(
        f"{label}: {clean}"
        for label, value in labels
        if (clean := re.sub(r"\s+", " ", str(value or "")).strip())
    )


def _input_payload(event: Mapping[str, Any], source_material: str) -> dict[str, Any]:
    return {
        "source_id": normalize_source_id(event.get("source_id") or event.get("source")),
        "title": str(event.get("title") or ""),
        "start_date": str(event.get("start_date") or event.get("date") or ""),
        "end_date": str(event.get("end_date") or ""),
        "time": str(event.get("time") or ""),
        "time_note": str(event.get("time_note") or ""),
        "venue": str(event.get("venue") or ""),
        "venue_address": str(event.get("venue_address") or ""),
        "city": str(event.get("city") or ""),
        "organizer": str(event.get("organizer") or ""),
        "price": str(event.get("price") or ""),
        "availability": str(event.get("availability") or ""),
        "category_key": str(event.get("category_key") or ""),
        "category": str(event.get("category") or ""),
        "series_title": str(event.get("series_title") or ""),
        "source_material": source_material,
    }


def _input_hash(payload: Mapping[str, Any]) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(serialized.encode("utf-8")).hexdigest()


def _normalized_words(value: str) -> list[str]:
    return re.findall(r"[a-z0-9äöüß]+", value.casefold())


def _mentions_date_outside_scope(summary: str, facts: Mapping[str, Any]) -> bool:
    try:
        start = datetime.fromisoformat(str(facts.get("_publication_start") or facts.get("start_date"))).date()
        end = datetime.fromisoformat(
            str(facts.get("_publication_end") or facts.get("end_date") or start.isoformat())
        ).date()
    except ValueError:
        return False
    allowed_dates = set()
    for match in _impl_ai_contracts._PROSE_DATE_PATTERN.finditer(str(facts.get("registration") or "")):
        year = int(match.group(3) or start.year)
        try:
            allowed_dates.add(
                datetime(year, _impl_ai_contracts._GERMAN_MONTHS[match.group(2).casefold()], int(match.group(1))).date()
            )
        except ValueError:
            continue
    for match in _impl_ai_contracts._PROSE_DATE_PATTERN.finditer(summary):
        year = int(match.group(3) or start.year)
        try:
            mentioned = datetime(year, _impl_ai_contracts._GERMAN_MONTHS[match.group(2).casefold()], int(match.group(1))).date()
        except ValueError:
            continue
        if not start <= mentioned <= end and mentioned not in allowed_dates:
            return True
    return False


def _mentions_weekday_outside_scope(value: str, payload: Mapping[str, Any]) -> bool:
    try:
        start = datetime.fromisoformat(str(payload.get("start_date") or "")).date()
        end = datetime.fromisoformat(str(payload.get("end_date") or payload.get("start_date") or "")).date()
    except ValueError:
        return False
    if end < start or (end - start).days > 31:
        return False
    allowed_weekdays = {(start + timedelta(days=offset)).weekday() for offset in range((end - start).days + 1)}
    mentioned = {_impl_ai_contracts._WEEKDAY_NUMBERS[match.group(1).casefold()] for match in _impl_ai_contracts._WEEKDAY_PATTERN.finditer(value)}
    return bool(mentioned - allowed_weekdays)


def _sanitize_extracted_facts(facts: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    cleaned = dict(facts)
    source_material = str(payload.get("source_material") or "")
    structured_price = str(payload.get("price") or "")
    evidence_text = f"{structured_price}\n{source_material}"

    def parsed_date(value: object) -> date | None:
        try:
            return datetime.fromisoformat(str(value or "")).date()
        except ValueError:
            return None

    publication_start = parsed_date(payload.get("start_date"))
    publication_end = parsed_date(payload.get("end_date")) or publication_start
    extracted_start = parsed_date(cleaned.get("start_date")) or parsed_date(cleaned.get("end_date"))
    extracted_end = parsed_date(cleaned.get("end_date")) or extracted_start
    if (
        publication_start
        and publication_end
        and extracted_start
        and extracted_end
        and (extracted_end < publication_start or extracted_start > publication_end)
    ):
        # Detail pages for calls, exhibitions and recurring calendars can discuss
        # a related occurrence while the selected listing represents another day.
        # Keep useful topical facts, but never let that related occurrence supply
        # the public identity or poison every summary retry with its date/place.
        for field in (
            "title", "start_date", "end_date", "time", "time_note", "venue", "venue_address", "city",
        ):
            cleaned[field] = payload.get(field) or None
        cleaned["end_date"] = payload.get("end_date") or payload.get("start_date") or None

    scope = {
        **cleaned,
        "_publication_start": payload.get("start_date"),
        "_publication_end": payload.get("end_date") or payload.get("start_date"),
    }

    def allowed_fact(value: object, *, sponsors: bool = True, weekdays: bool = True) -> bool:
        text = str(value or "").strip()
        if not text:
            return False
        blocked = (
            _impl_ai_contracts._VENDOR_FEE_PATTERN.search(text)
            or _impl_ai_contracts._MISSING_INFO_PATTERN.search(text)
            or _impl_ai_contracts._META_OR_SPECULATION_PATTERN.search(text)
            or _impl_ai_contracts._HEALTH_CLAIM_PATTERN.search(text)
            or _mentions_date_outside_scope(text, scope)
            or (weekdays and _mentions_weekday_outside_scope(text, payload))
        )
        if sponsors:
            blocked = bool(blocked or _impl_ai_contracts._SPONSOR_PATTERN.search(text))
        return not bool(blocked)

    for field in (
        "program", "participants", "target_group", "language", "accessibility",
        "requirements", "special_features", "neutral_facts",
    ):
        values = cleaned.get(field)
        if isinstance(values, list):
            cleaned[field] = [value for value in values if allowed_fact(value)]
    target_groups = cast(list, cleaned.get("target_group")) if isinstance(cleaned.get("target_group"), list) else []
    cleaned["target_group"] = [
        value for value in target_groups
        if not _impl_ai_contracts._GENERIC_TARGET_GROUP_PATTERN.fullmatch(str(value).strip())
    ]
    if not _impl_ai_contracts._LANGUAGE_EVIDENCE_PATTERN.search(source_material):
        cleaned["language"] = []

    for field in ("time_note", "registration", "age_information", "duration"):
        value = cleaned.get(field)
        if value and not allowed_fact(value, sponsors=False, weekdays=False):
            cleaned[field] = None
    registration = str(cleaned.get("registration") or "")
    if registration and (
        not _impl_ai_contracts._REGISTRATION_PATTERN.search(registration)
        or _impl_ai_contracts._NEGATIVE_REGISTRATION_PATTERN.search(registration)
    ):
        cleaned["registration"] = None
    if (
        not cleaned.get("registration")
        and _impl_ai_contracts._REGISTRATION_PATTERN.search(source_material)
        and not _impl_ai_contracts._NEGATIVE_REGISTRATION_PATTERN.search(source_material)
    ):
        neutral_facts = (
            cast(list, cleaned.get("neutral_facts"))
            if isinstance(cleaned.get("neutral_facts"), list)
            else []
        )
        for index, fact in enumerate(neutral_facts):
            text = str(fact).strip()
            if _impl_ai_contracts._REGISTRATION_PATTERN.search(text) and not _impl_ai_contracts._NEGATIVE_REGISTRATION_PATTERN.search(text):
                cleaned["registration"] = text
                cleaned["neutral_facts"] = [
                    *neutral_facts[:index],
                    *neutral_facts[index + 1:],
                ]
                break

    organizer = str(cleaned.get("organizer") or "").strip()
    structured_organizer = str(payload.get("organizer") or "").strip()
    explicit_organizer_label = bool(
        re.search(r"\b(?:veranstalter|organisiert\s+von)\b", source_material, re.IGNORECASE)
    )
    organizer_in_source = bool(organizer and organizer.casefold() in source_material.casefold())
    if organizer and not (
        organizer.casefold() == structured_organizer.casefold()
        or (explicit_organizer_label and organizer_in_source)
    ):
        cleaned["organizer"] = None

    # A model may normalize a locality spelling, but it must never replace an
    # explicit municipality from the source with another one. This also blocks
    # invented venues such as "Bonner Marktplatz" when the prose says Sieglar
    # or Troisdorf. Unknown remains preferable to a contradictory location.
    source_city = (
        str(payload.get("city") or "").strip()
        or common.guess_city_from_text(source_material)
        or ""
    )
    candidate_city = str(cleaned.get("city") or "").strip()
    if (
        source_city
        and candidate_city
        and category_taxonomy.comparison_text(source_city)
        != category_taxonomy.comparison_text(candidate_city)
    ):
        cleaned["city"] = None
    for field in ("venue", "venue_address"):
        candidate_location = str(cleaned.get(field) or "").strip()
        candidate_location_city = common.guess_city_from_text(candidate_location)
        if (
            source_city
            and candidate_location_city
            and category_taxonomy.comparison_text(source_city)
            != category_taxonomy.comparison_text(candidate_location_city)
        ):
            cleaned[field] = None
            continue
        structured_location = str(payload.get(field) or "").strip()
        normalized_candidate = " ".join(_normalized_words(candidate_location))
        normalized_material = " ".join(_normalized_words(source_material))
        candidate_words = normalized_candidate.split()
        material_words = normalized_material.split()
        fuzzy_supported = bool(candidate_words) and any(
            SequenceMatcher(
                None,
                normalized_candidate,
                " ".join(material_words[index:index + len(candidate_words)]),
            ).ratio() >= 0.88
            for index in range(max(len(material_words) - len(candidate_words) + 1, 0))
        )
        if (
            candidate_location
            and not structured_location
            and normalized_candidate not in normalized_material
            and not fuzzy_supported
        ):
            cleaned[field] = None

    admission = dict(cast(dict, cleaned.get("admission"))) if isinstance(cleaned.get("admission"), dict) else {}
    note = str(admission.get("note") or "").strip()
    if note and not allowed_fact(note, sponsors=False):
        note = ""
    conditional_free = common.has_conditional_free_admission(source_material)
    first_sunday_offer = bool(re.search(
        r"\b(?:an\s+)?(?:jedem\s+)?ersten\s+sonntag\b",
        source_material,
        re.IGNORECASE,
    ))
    selected_is_first_sunday = False
    try:
        selected_date = datetime.fromisoformat(str(payload.get("start_date") or "")).date()
        selected_is_first_sunday = selected_date.weekday() == 6 and selected_date.day <= 7
    except ValueError:
        pass
    explicit_free = bool(_impl_ai_contracts._VISITOR_FREE_PATTERN.search(evidence_text))
    selected_conditional_offer_applies = first_sunday_offer and selected_is_first_sunday
    if conditional_free and not selected_conditional_offer_applies:
        note = ""
        explicit_free = bool(_impl_ai_contracts._VISITOR_FREE_PATTERN.search(structured_price))
    amount = admission.get("amount")
    if not isinstance(amount, int | float) or isinstance(amount, bool):
        amount = None
    if amount is not None and amount > 0 and not _impl_ai_contracts._VISITOR_PAID_PATTERN.search(evidence_text):
        amount = None
    is_free = admission.get("is_free") if isinstance(admission.get("is_free"), bool) else None
    if is_free is True and not explicit_free:
        is_free = None
    if is_free is False and amount is None:
        is_free = None
    if amount == 0 and not explicit_free:
        amount = None
    if amount == 0 and is_free is False:
        amount = None
        is_free = None
    if amount == 0 and explicit_free:
        is_free = True
    if explicit_free:
        is_free = True
    admission.update({
        "is_free": is_free,
        "amount": amount,
        "currency": "EUR" if amount is not None else None,
        "note": note or None,
        "donation_suggested": (
            bool(admission.get("donation_suggested"))
            if re.search(r"\bspende\w*\b", evidence_text, re.IGNORECASE)
            else None
        ),
    })
    cleaned["admission"] = admission

    availability = cleaned.get("availability")
    pattern = _impl_ai_contracts._AVAILABILITY_PATTERNS.get(str(availability))
    if not pattern or not pattern.search(source_material):
        cleaned["availability"] = None
    if str(payload.get("source_id") or "") == "marktcom" and cleaned.get("time"):
        time_match = re.fullmatch(r"(\d{2}):(\d{2})", str(cleaned["time"]))
        time_pattern = rf"{time_match.group(1)}[:.]?{time_match.group(2)}" if time_match else r"(?!)"
        if re.search(rf"\bplatzvergabe\b[^.!?]{{0,80}}\b{time_pattern}", source_material, re.IGNORECASE):
            cleaned["time"] = None
    return cleaned


def _summary_quality(summary: object, source_material: str, facts: Mapping[str, Any]) -> str:
    if not isinstance(summary, str):
        return "summary is not text"
    clean = re.sub(r"\s+", " ", summary).strip()
    words = _normalized_words(clean)
    if len(words) < 10:
        return "summary is too short to be useful"
    if len(words) > 250:
        return "summary exceeds 250 words"
    if not re.search(r'[.!?…](?:["”»])?$', clean):
        return "summary ends mid-sentence"
    if clean.count("„") != clean.count("“") or clean.count('"') % 2:
        return "summary contains an unclosed quotation"
    if _impl_ai_contracts._MARKETING_PATTERN.search(clean):
        return "summary contains promotional language"
    if _impl_ai_contracts._MISSING_INFO_PATTERN.search(clean):
        return "summary talks about missing information"
    if _impl_ai_contracts._META_OR_SPECULATION_PATTERN.search(clean):
        return "summary contains speculation or meta commentary"
    if _impl_ai_contracts._VENDOR_FEE_PATTERN.search(clean):
        return "summary contains seller-facing information"
    if _impl_ai_contracts._HEALTH_CLAIM_PATTERN.search(clean):
        return "summary contains a health-effect claim"
    if _impl_ai_contracts._SPONSOR_PATTERN.search(clean):
        return "summary contains sponsor or cooperation copy"
    if re.search(r"(?i)https?://|www\.|\b\S+@\S+\b|\b0\d{2,4}[\s/-]?\d{3,}\b", clean):
        return "summary contains contact or outbound-link data"
    german_stopwords = {
        "aber",
        "auch",
        "am",
        "bei",
        "das",
        "dem",
        "den",
        "der",
        "die",
        "ein",
        "eine",
        "einer",
        "für",
        "im",
        "in",
        "ist",
        "mit",
        "nach",
        "sich",
        "und",
        "um",
        "von",
        "vor",
        "wird",
        "zu",
        "zum",
        "zur",
    }
    if sum(word in german_stopwords for word in words) < 3:
        return "summary is not recognizably German"
    fact_time = str(facts.get("time") or "")
    unsupported_times = {value for value in re.findall(r"\b\d{1,2}:\d{2}\b", clean) if value not in fact_time}
    if unsupported_times:
        return "summary contains a clock time absent from the facts"
    if _mentions_date_outside_scope(clean, facts):
        return "summary mentions a date outside the selected event"
    source_city = common.guess_city_from_text(source_material)
    summary_city = common.guess_city_from_text(clean)
    if (
        source_city
        and summary_city
        and category_taxonomy.comparison_text(source_city)
        != category_taxonomy.comparison_text(summary_city)
        and category_taxonomy.comparison_text(source_city)
        not in category_taxonomy.comparison_text(clean)
    ):
        return "summary contradicts the source location"
    if not facts.get("organizer") and re.search(
        r"\b(?:veranstalter\s+ist|veranstaltet\s+von|organisiert\s+von)\b", clean, re.IGNORECASE,
    ):
        return "summary invents an organizer"
    if not facts.get("registration") and _impl_ai_contracts._REGISTRATION_PATTERN.search(clean):
        return "summary invents registration information"
    admission = cast(dict, facts.get("admission")) if isinstance(facts.get("admission"), dict) else {}
    if admission.get("is_free") is not True and _impl_ai_contracts._VISITOR_FREE_PATTERN.search(clean):
        return "summary invents free admission"
    if not facts.get("target_group") and re.search(
        r"\b(?:richtet\s+sich\s+an|für\s+alle\s+interessierten)\b", clean, re.IGNORECASE,
    ):
        return "summary invents a target group"
    if not facts.get("language") and _impl_ai_contracts._LANGUAGE_EVIDENCE_PATTERN.search(clean):
        return "summary invents a language"
    source_words = _normalized_words(source_material)
    if len(source_words) >= 12 and len(words) >= 12:
        source_shingles = {tuple(source_words[index:index + 12]) for index in range(len(source_words) - 11)}
        if any(tuple(words[index:index + 12]) in source_shingles for index in range(len(words) - 11)):
            return "summary repeats a long source phrase"
    if source_material and len(clean) >= 120:
        similarity = SequenceMatcher(None, clean.casefold(), source_material.casefold()).ratio()
        if similarity >= 0.72:
            return "summary is too similar to source prose"
    return ""


def _without_sentences(value: str, pattern: re.Pattern[str]) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", value).strip())
    return " ".join(sentence for sentence in sentences if sentence and not pattern.search(sentence)).strip()


def _admission_conflicts(original: Mapping[str, Any], facts: Mapping[str, Any]) -> bool:
    existing = cast(dict, original.get("admission")) if isinstance(original.get("admission"), dict) else {}
    extracted = cast(dict, facts.get("admission")) if isinstance(facts.get("admission"), dict) else {}
    existing_free = existing.get("isFree")
    extracted_free = extracted.get("is_free")
    existing_amount = existing.get("amount")
    extracted_amount = extracted.get("amount")
    price = str(original.get("price") or "").casefold()
    amount_match = re.search(r"(?<!\d)(\d+(?:[.,]\d{1,2})?)\s*(?:€|eur\b|euro\b)", price)
    if existing_amount is None and amount_match:
        existing_amount = float(amount_match.group(1).replace(",", "."))
    if existing_free is None and price:
        if re.search(r"\b(?:kostenlos|kostenfrei|eintritt\s+frei|frei)\b", price):
            existing_free = True
        elif existing_amount is not None:
            existing_free = False
    if existing_free is not None and extracted_free is not None and existing_free != extracted_free:
        return True
    if existing_free is True and isinstance(extracted_amount, int | float) and extracted_amount > 0:
        return True
    if extracted_free is True and isinstance(existing_amount, int | float) and existing_amount > 0:
        return True
    return (
        isinstance(existing_amount, int | float)
        and isinstance(extracted_amount, int | float)
        and abs(float(existing_amount) - float(extracted_amount)) > 0.01
    )


def _clean_summary_result(
    result: Mapping[str, Any],
    *,
    admission_conflict: bool,
    facts: Mapping[str, Any],
) -> dict[str, Any]:
    cleaned = dict(result)
    summary = str(cleaned.get("ai_summary") or "")
    summary = _without_sentences(summary, _impl_ai_contracts._MISSING_INFO_PATTERN)
    summary = _without_sentences(summary, _impl_ai_contracts._META_OR_SPECULATION_PATTERN)
    summary = _without_sentences(summary, _impl_ai_contracts._VENDOR_FEE_PATTERN)
    if admission_conflict:
        summary = _without_sentences(summary, _impl_ai_contracts._ADMISSION_SENTENCE_PATTERN)
        cleaned["price"] = None
    for field in ("time", "time_note", "venue", "venue_address", "city", "organizer", "series_title"):
        cleaned[field] = facts.get(field) or None
    cleaned["availability"] = facts.get("availability") or None
    admission = cast(dict, facts.get("admission")) if isinstance(facts.get("admission"), dict) else {}
    if (
        admission_conflict
        or (
            admission.get("is_free") is None
            and admission.get("amount") is None
            and not admission.get("donation_suggested")
        )
    ):
        cleaned["price"] = None
    else:
        is_free = admission.get("is_free")
        amount = admission.get("amount")
        currency = str(admission.get("currency") or "EUR").upper()
        donation = bool(admission.get("donation_suggested"))
        if is_free is True:
            cleaned["price"] = "Eintritt frei"
        elif isinstance(amount, int | float) and not isinstance(amount, bool):
            rendered_amount = f"{float(amount):.2f}".rstrip("0").rstrip(".").replace(".", ",")
            symbol = "€" if currency == "EUR" else currency
            cleaned["price"] = f"{rendered_amount} {symbol}".strip()
        elif donation:
            cleaned["price"] = "Spende erbeten"
        else:
            cleaned["price"] = None
    cleaned["ai_summary"] = summary
    return cleaned


def _clean_nullable(value: object, limit: int = 500) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()[:limit]


def _confidence(value: object) -> float:
    try:
        return float(cast(Any, value) or 0)
    except (TypeError, ValueError):
        return 0


def _apply_result(event: RawEvent, result: Mapping[str, Any]) -> RawEvent:
    enriched = dict(event)
    summary = _clean_nullable(result.get("ai_summary"), 4000)
    enriched["ai_summary"] = summary
    if not enriched.get("time") and not enriched.get("identity_time_locked"):
        candidate = _clean_nullable(result.get("time"), 20)
        if re.fullmatch(r"\d{2}:\d{2}(?:–\d{2}:\d{2})?", candidate):
            enriched["time"] = candidate
            enriched["all_day"] = False
    if not enriched.get("time_note"):
        enriched["time_note"] = _clean_nullable(result.get("time_note"), 500)
    if not enriched.get("identity_venue_locked"):
        for field, limit in (("venue", 300), ("venue_address", 500), ("city", 160)):
            if not enriched.get(field):
                enriched[field] = _clean_nullable(result.get(field), limit)
    if not enriched.get("organizer"):
        enriched["organizer"] = _clean_nullable(result.get("organizer"), 500)
    admission = cast(dict, enriched.get("admission")) if isinstance(enriched.get("admission"), dict) else {}
    locked_admission = (
        enriched.get("admission_basis") == "explicit"
        or admission.get("basis") == "structured"
    )
    candidate_price = _clean_nullable(result.get("price"), 160)
    if not locked_admission and candidate_price and not _impl_ai_contracts._VENDOR_FEE_PATTERN.search(candidate_price):
        enriched["price"] = candidate_price
        enriched["admission_basis"] = "inferred"
    availability = result.get("availability")
    if (
        not enriched.get("availability") and isinstance(availability, str)
        and availability
        in {
        "InStock", "SoldOut", "LimitedAvailability", "PreOrder",
    }
    ):
        enriched["availability"] = availability
    current_key = str(enriched.get("category_key") or "other")
    confidence = _confidence(enriched.get("category_confidence"))
    category_key = result.get("category_key")
    if (
        (current_key == "other" or confidence < 0.75) and isinstance(category_key, str)
        and category_key in category_taxonomy.CATEGORY_BY_KEY
    ):
        category = category_taxonomy.CATEGORY_BY_KEY[category_key]
        enriched["category_key"] = category["key"]
        enriched["category_label"] = category["label"]
        enriched["category"] = category["label"]
        enriched["category_confidence"] = 0.8
        enriched["category_reason"] = "ai:extracted-facts"
    if not enriched.get("series_title"):
        enriched["series_title"] = _clean_nullable(result.get("series_title"), 500)
    return strip_restricted_copy(cast(RawEvent, enriched))


def _occurrence_start_time(value: object) -> str:
    """Return the explicit start time while ignoring a newly learned end time."""
    raw = str(value or "")
    match = re.match(
        r"^\s*(\d{2}:\d{2})(?:\s*[–-]\s*\d{2}:\d{2})?\s*$",
        raw,
    )
    return match.group(1) if match else category_taxonomy.comparison_text(raw)


def _event_occurrence_start_time(event: Mapping[str, Any]) -> str:
    explicit = _occurrence_start_time(event.get("time"))
    if explicit:
        return explicit
    match = re.search(
        r"[T ](\d{2}:\d{2})(?::\d{2})?(?:[+-]\d{2}:?\d{2}|Z)?$",
        str(event.get("start_at") or ""),
    )
    return match.group(1) if match else ""


def _cached_occurrence_matches(
    event: Mapping[str, Any],
    facts: Mapping[str, Any],
    result: Mapping[str, Any] | None = None,
) -> bool:
    """Conservatively match an accepted cache row after mutable identity fields changed."""
    def text(value: object) -> str:
        return category_taxonomy.comparison_text(str(value or ""))

    event_start = str(event.get("start_date") or event.get("date") or "")
    facts_start = str(facts.get("start_date") or "")
    event_end = str(event.get("end_date") or event_start)
    facts_end = str(facts.get("end_date") or facts_start)
    if (
        not text(event.get("title"))
        or text(event.get("title")) != text(facts.get("title"))
        or not event_start
        or event_start != facts_start
        or event_end != facts_end
        or _event_occurrence_start_time(event)
        != _occurrence_start_time(facts.get("time"))
    ):
        return False
    for field in ("city", "venue"):
        current = text(event.get(field))
        cached = text(facts.get(field) or (result or {}).get(field))
        if current and current != cached:
            return False
    return True


def _historical_event_key_identity(value: object) -> str:
    """Collapse legacy/current key formats that retain the same identity digest."""
    key = str(value or "")
    digest = re.search(r"(?:^|-)([0-9a-f]{10})$", key)
    return digest.group(1) if digest else key


def _calendar_occurrence_overrides_non_event(
    facts: Mapping[str, Any],
    original: Mapping[str, Any],
    source_id: str,
) -> bool:
    """Keep explicit calendar occurrences; only marktcom contains shop listings."""
    return (
        facts.get("is_concrete_event") is False
        and source_id != "marktcom"
        and bool(original.get("start_date") or original.get("date"))
    )
