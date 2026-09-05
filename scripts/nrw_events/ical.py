"""Owning implementation of ical; core is a compatibility facade."""

from __future__ import annotations

import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import event_builder as _impl_event_builder
from . import http as _impl_http
from . import performance, richtext
from . import run_state as _impl_run_state
from . import text as _impl_text
from .dates import parse_iso_date
from .models import AdmissionDefault, RawEvent


def _ical_unfold(text: str) -> str:
    """RFC 5545 line unfolding: CRLF + space/tab continues the previous line."""
    return re.sub(r"\r?\n[ \t]", "", text)


def _ical_unescape(text: str, *, preserve_breaks: bool = False) -> str:
    """Decode RFC 5545 escapes.

    ``\\n`` is the only way an iCal feed can express a paragraph, so DESCRIPTION
    keeps it; a SUMMARY or URL stays on one line.
    """
    break_replacement = "\n" if preserve_breaks else " "
    replacements = {
        "n": break_replacement, "N": break_replacement, '"': '"',
        ",": ",", ";": ";", "\\": "\\",
    }
    return re.sub(
        r'\\([\\;,nN"])',
        lambda match: replacements[match.group(1)],
        text,
    ).strip()


def _ical_content_line(line: str) -> tuple:
    """Split an iCal content line at the first colon outside quoted params."""
    in_quote = False
    for idx, char in enumerate(line):
        if char == '"':
            in_quote = not in_quote
        elif char == ":" and not in_quote:
            return line[:idx], line[idx + 1:]
    return line, ""


def _ical_parse_dt(value: str, property_key: str = "") -> datetime | None:
    v = (value or "").strip()
    is_utc = v.endswith("Z")
    if re.match(r"^\d{8}T\d{6}Z?$", v):
        parsed = datetime.strptime(v[:15], "%Y%m%dT%H%M%S")
    elif re.match(r"^\d{8}T\d{4}Z?$", v):
        parsed = datetime.strptime(v[:13], "%Y%m%dT%H%M")
    else:
        parsed = None
    if parsed is not None:
        if is_utc:
            return parsed.replace(tzinfo=timezone.utc).astimezone(_impl_run_state.LOCAL_TIMEZONE).replace(tzinfo=None)
        tzid = re.search(r"(?:^|;)TZID=([^;:]+)", property_key, re.IGNORECASE)
        if tzid:
            try:
                timezone_name = tzid.group(1).strip().strip('"')
                return parsed.replace(tzinfo=ZoneInfo(timezone_name)).astimezone(_impl_run_state.LOCAL_TIMEZONE).replace(tzinfo=None)
            except (ValueError, ZoneInfoNotFoundError) as exc:
                _impl_run_state.log_source_error("iCal timezone", exc)
        return parsed
    if re.match(r"^\d{8}$", v):
        return datetime.strptime(v, "%Y%m%d")
    return parse_iso_date(v)


def _ical_attach_event_page(value: str) -> str:
    """Return a human event-detail page derived from an iCal ATTACH URL.

    Some municipal IONAS feeds put the organizer homepage in ``URL`` but include
    an image attachment whose path lives under the real event detail page, e.g.
    ``.../2026-06-12-jazzig-in-die-ferne-swingen/poster.jpg?cid=...``. The image
    itself is a bad event link; its parent directory is the readable event page.
    """
    raw = _ical_unescape(value or "").strip()
    if not raw.startswith(("http://", "https://")):
        return ""
    parsed = urllib.parse.urlparse(raw)
    path = parsed.path or ""
    if "/kalender/" not in path:
        return ""
    if path.rstrip("/").split("/")[-1].lower().endswith((
        ".jpg", ".jpeg", ".png", ".gif", ".webp", ".pdf", ".ics",
    )):
        path = path.rsplit("/", 1)[0] + "/"
    elif not path.endswith("/"):
        path += "/"
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


def _ical_feed_page(url: str) -> str:
    """Convert an iCal export URL to its human calendar page fallback."""
    parsed = urllib.parse.urlparse(url or "")
    path = parsed.path or ""
    if path.endswith("/event.ics"):
        path = path.rsplit("/", 1)[0] + "/"
        return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))
    return url


def _ical_best_link(props: dict, feed_url: str) -> str:
    """Choose the most useful human URL for an iCal event."""
    attach_page = _ical_attach_event_page(props.get("ATTACH", ""))
    if attach_page:
        return attach_page
    return (props.get("URL", "") or _ical_feed_page(feed_url)).strip()


_ICAL_WEEKDAYS = {name: index for index, name in enumerate(
    ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
)}


_SUPPORTED_RRULE_PARTS = {"FREQ", "INTERVAL", "BYDAY", "UNTIL", "COUNT"}


def _ical_date_list(values: list[tuple[str, str]]) -> list[datetime]:
    parsed = []
    for property_key, raw in values:
        for value in raw.split(","):
            dt = _ical_parse_dt(value, property_key)
            if dt is not None:
                parsed.append(dt)
    return parsed


def _ical_date_only_days(values: list[tuple[str, str]]) -> set[date]:
    """Calendar days named by date-only values (e.g. ``EXDATE;VALUE=DATE``).

    A date-only exclusion carries no clock time, so it must exclude the whole
    day; comparing its midnight parse against timed occurrences never matches.
    """
    return {
        datetime.strptime(value, "%Y%m%d").date()
        for _property_key, raw in values
        for value in (part.strip() for part in raw.split(","))
        if re.match(r"^\d{8}$", value)
    }


def _ical_duration(value: str) -> timedelta | None:
    """Parse the bounded RFC 5545 duration subset used by event feeds."""
    match = re.fullmatch(
        r"P(?:(?P<weeks>\d+)W)?(?:(?P<days>\d+)D)?"
        r"(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?",
        (value or "").strip().upper(),
    )
    if not match or not any(match.groupdict().values()):
        return None
    return timedelta(
        weeks=int(match.group("weeks") or 0),
        days=int(match.group("days") or 0),
        hours=int(match.group("hours") or 0),
        minutes=int(match.group("minutes") or 0),
        seconds=int(match.group("seconds") or 0),
    )


def _ical_recurrence_starts(
    start: datetime,
    rrule: str,
    rdates: list[datetime],
    exdates: list[datetime],
    exdate_days: set[date] | None = None,
) -> tuple[list[datetime], str]:
    """Expand a bounded stdlib-only subset of RFC 5545 recurrence rules."""
    excluded_days = exdate_days or set()
    if not rrule:
        starts = [start, *rdates]
        excluded = set(exdates)
        return sorted({
            value for value in starts
            if value not in excluded and value.date() not in excluded_days
        }), ""

    parts = {}
    for raw_part in rrule.split(";"):
        if "=" not in raw_part:
            return [start], f"unsupported RRULE fragment {raw_part!r}"
        key, value = raw_part.split("=", 1)
        parts[key.upper()] = value.upper()
    unsupported = set(parts) - _SUPPORTED_RRULE_PARTS
    if unsupported:
        return [start], f"unsupported RRULE parts: {', '.join(sorted(unsupported))}"

    freq = parts.get("FREQ", "")
    if freq not in {"DAILY", "WEEKLY", "MONTHLY"}:
        return [start], f"unsupported RRULE frequency: {freq or 'missing'}"
    try:
        interval = max(int(parts.get("INTERVAL", "1")), 1)
        count = int(parts["COUNT"]) if "COUNT" in parts else None
    except ValueError:
        return [start], "invalid RRULE INTERVAL or COUNT"
    until_value = parts.get("UNTIL", "")
    until = _ical_parse_dt(until_value) if until_value else None
    if until is not None and re.fullmatch(r"\d{8}", until_value):
        until = until.replace(hour=23, minute=59, second=59)
    byday_tokens = [token for token in parts.get("BYDAY", "").split(",") if token]
    if any(token not in _ICAL_WEEKDAYS for token in byday_tokens):
        return [start], "unsupported ordinal or invalid RRULE BYDAY"
    weekdays = {_ICAL_WEEKDAYS[token] for token in byday_tokens}

    window_end = _impl_run_state.runtime_window().end.replace(hour=23, minute=59, second=59, microsecond=999999)
    hard_end = min(window_end, until) if until else window_end
    cursor = start
    starts = []
    generated = 0
    iterations = 0
    start_week = start.date() - timedelta(days=start.weekday())
    while cursor <= hard_end and iterations < 100_000:
        iterations += 1
        days_since = (cursor.date() - start.date()).days
        include = False
        if freq == "DAILY":
            include = days_since % interval == 0
        elif freq == "WEEKLY":
            week = (cursor.date() - start_week).days // 7
            allowed_days = weekdays or {start.weekday()}
            include = week % interval == 0 and cursor.weekday() in allowed_days
        else:
            months_since = (cursor.year - start.year) * 12 + cursor.month - start.month
            if months_since % interval == 0:
                include = cursor.weekday() in weekdays if weekdays else cursor.day == start.day
        if include:
            generated += 1
            if cursor >= start and (count is None or generated <= count):
                starts.append(cursor)
            if count is not None and generated >= count:
                break
        cursor += timedelta(days=1)

    starts.extend(rdates)
    excluded = set(exdates)
    return sorted({
        value for value in starts
        if value not in excluded and value.date() not in excluded_days
    }), ""


@performance.measured("ical.fetch_parse_canonicalize")
def fetch_ical(url: str, source: str, default_city: str, category: str = "",
               trust: float = 1.0, source_id: str = "",
               event_filter: Callable[[dict[str, str], datetime, datetime], bool] | None = None,
               city_resolver: Callable[[str], str] | None = None,
               fetcher: Callable[..., str] | None = None,
               admission: AdmissionDefault | None = None,
               default_category_key: str = "",
               category_locked: bool = False,
               empty_calendar_is_valid: bool = False,
               description_max_chars: int | None = None) -> list[RawEvent]:
    """Generic RFC 5545 iCal/.ics fetcher (Tribe Events, webcal, Meetup feeds).

    ``fetcher`` optionally replaces the plain HTTP read with a ``(url, **kwargs) ->
    str`` callable. Sources that must request one small calendar *per event* use it
    to route through the persistent TTL cache, so a repeat run costs no requests.
    """
    read = fetcher or _impl_http.fetch_url
    raw = read(
        url,
        timeout=20,
        accept="text/calendar,application/calendar+json;q=0.9,*/*;q=0.8",
        sec_fetch_mode="no-cors",
        sec_fetch_dest="empty",
    )
    return parse_ical(
        raw, url, source, default_city, category, trust, source_id,
        event_filter=event_filter, city_resolver=city_resolver, admission=admission,
        default_category_key=default_category_key, category_locked=category_locked,
        empty_calendar_is_valid=empty_calendar_is_valid, description_max_chars=description_max_chars,
    )


@performance.measured("ical.parse_canonicalize")
def parse_ical(
    raw: str, url: str, source: str, default_city: str, category: str = "",
    trust: float = 1.0, source_id: str = "", *,
    event_filter: Callable[[dict[str, str], datetime, datetime], bool] | None = None,
    city_resolver: Callable[[str], str] | None = None,
    admission: AdmissionDefault | None = None,
    default_category_key: str = "", category_locked: bool = False,
    empty_calendar_is_valid: bool = False, description_max_chars: int | None = None,
) -> list[RawEvent]:
    """Parse an already fetched calendar with the same source/runtime policy."""
    early_quality = os.environ.get("NRW_EVENTS_ICAL_PRUNE", "1") != "0"
    quality_cache: _impl_event_builder._ICalQualityCache = {}
    raw = _ical_unfold(raw)
    raw = re.sub(r"BEGIN:VALARM.*?END:VALARM", "", raw, flags=re.S | re.I)
    events: list[RawEvent] = []
    blocks = re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", raw, re.S)
    recurrence_overrides: set[tuple[str, datetime]] = set()
    for block in blocks:
        uid = ""
        recurrence_id: datetime | None = None
        for line in re.split(r"\r?\n", block):
            key, val = _ical_content_line(line)
            name = key.split(";", 1)[0].strip().upper()
            if name == "UID":
                uid = _ical_unescape(val)
            elif name == "RECURRENCE-ID":
                recurrence_id = _ical_parse_dt(val, key)
        if uid and recurrence_id is not None:
            recurrence_overrides.add((uid, recurrence_id))
    for block in blocks:
        props: dict[str, str] = {}
        property_keys: dict[str, str] = {}
        multi_props: dict[str, list[tuple[str, str]]] = {}
        for line in re.split(r"\r?\n", block):
            if ":" not in line:
                continue
            key, val = _ical_content_line(line)
            if not val:
                continue
            name = key.split(";")[0].strip().upper()
            if name in (
                "SUMMARY", "DTSTART", "DTEND", "DESCRIPTION", "LOCATION", "URL",
                "CATEGORIES", "ATTACH", "RRULE", "RDATE", "EXDATE",
                "STATUS", "UID", "RECURRENCE-ID", "DURATION",
            ):
                props.setdefault(name, val)
                property_keys.setdefault(name, key)
                multi_props.setdefault(name, []).append((key, val))
        if not props.get("SUMMARY"):
            continue
        start_dt = _ical_parse_dt(props.get("DTSTART", ""), property_keys.get("DTSTART", ""))
        if start_dt is None:
            continue
        raw_end_dt = _ical_parse_dt(
            props.get("DTEND", ""), property_keys.get("DTEND", "")
        )
        if raw_end_dt is None:
            parsed_duration = _ical_duration(props.get("DURATION", ""))
            raw_end_dt = start_dt + parsed_duration if parsed_duration else start_dt
        all_day = bool(re.match(r"^\d{8}$", props.get("DTSTART", "").strip()))
        duration = (raw_end_dt - start_dt) if raw_end_dt else timedelta(0)
        starts, recurrence_warning = _ical_recurrence_starts(
            start_dt,
            props.get("RRULE", ""),
            _ical_date_list(multi_props.get("RDATE", [])),
            _ical_date_list(multi_props.get("EXDATE", [])),
            _ical_date_only_days(multi_props.get("EXDATE", [])),
        )
        if recurrence_warning:
            _impl_run_state.log_source_error(f"{source} recurrence", ValueError(recurrence_warning))
        # Keep feed-level and event-level signals separate. CATEGORIES describes
        # this VEVENT and is therefore preferred when present; the static hint is
        # only a fallback. Concatenating both can create an artificial broad bag
        # with more than two category intents, which the taxonomy deliberately
        # rejects as untrustworthy.
        event_categories = _ical_unescape(props.get("CATEGORIES", "")).strip()
        cat = event_categories or (category or "").strip()
        for occurrence_start in starts:
            if (
                not props.get("RECURRENCE-ID")
                and props.get("UID")
                and (props["UID"], occurrence_start) in recurrence_overrides
            ):
                continue
            occurrence_end = occurrence_start + duration
            # RFC 5545 all-day DTEND is exclusive. Present the inclusive last day.
            if all_day and duration > timedelta(0):
                occurrence_end -= timedelta(days=1)
            if event_filter and not event_filter(props, occurrence_start, occurrence_end):
                continue
            location = _ical_unescape(props.get("LOCATION", ""))
            city = city_resolver(location) if city_resolver else default_city
            if not city:
                continue
            full_description = _ical_unescape(
                props.get("DESCRIPTION", ""), preserve_breaks=True
            )
            ev = _impl_event_builder.make_event(
                _ical_unescape(props["SUMMARY"]),
                occurrence_start, occurrence_end,
                location,
                city,
                full_description,
                _ical_best_link(props, url),
                source, cat, trust,
                all_day=all_day,
                source_id=source_id,
                admission=admission,
                default_category_key=default_category_key,
                category_locked=category_locked,
                _early_quality=early_quality and props.get("STATUS", "").strip().upper() != "CANCELLED",
                _quality_cache=quality_cache,
            )
            if ev:
                if props.get("STATUS", "").strip().upper() == "CANCELLED":
                    ev["status"] = "cancelled"
                if description_max_chars is not None:
                    ev["description"] = _impl_text.concise_description(
                        full_description, max_chars=description_max_chars
                    )
                    ev["description_html"] = richtext.from_plain_text(ev["description"])
                events.append(ev)
    valid_empty_calendar = bool(
        empty_calendar_is_valid
        and re.search(r"(?mi)^BEGIN:VCALENDAR\s*$", raw)
        and re.search(r"(?mi)^END:VCALENDAR\s*$", raw)
        # A VEVENT marker that produced no block means the component is
        # truncated or unbalanced — that is drift, not an inactive group.
        and not re.search(r"(?mi)^BEGIN:VEVENT", raw)
    )
    _impl_http._record_endpoint(
        url,
        parser_type="ical",
        candidate_count=len(blocks),
        parsed_event_count=len(events),
        parser_empty=not bool(blocks) and not valid_empty_calendar,
    )
    return events
