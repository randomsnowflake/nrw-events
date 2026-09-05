"""Owning implementation of event builder; core is a compatibility facade."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from html import unescape
from zoneinfo import ZoneInfo

from . import category_taxonomy, performance, richtext
from . import run_state as _impl_run_state
from . import text as _impl_text
from .dates import parse_date, parse_iso_date
from .junk_rules import legacy_junk_decision
from .location import (
    canonicalize_city,
    guess_city_from_text,
    haversine,
    refine_bonn_location,
    resolve_location,
)
from .models import AdmissionDefault, EventDraft, RawEvent, normalize_source_id
from .normalization import VenueResolution, resolve_venue
from .quality import QualityDecision, evaluate_event_quality
from .scoring import category_score, distance_score
from .title_normalization import normalize_event_title


def keep_only_event_master_data(event: RawEvent) -> RawEvent:
    """Replace publisher prose with a sentence generated only from event facts.

    This is for discovery platforms and directories whose descriptive copy must
    not be republished. Classification, admission and status extraction happen
    before this helper is called; the public description then contains only the
    allowed title, date, time and place fields already present on the record.
    """
    start = parse_iso_date(event.get("start_date") or event.get("date") or "")
    end = parse_iso_date(event.get("end_date") or "")
    description = _impl_text.factual_event_description(
        event.get("title", ""),
        date_value=start,
        end_date_value=end,
        time_text=event.get("time", ""),
        venue=event.get("venue", ""),
        city=event.get("city", ""),
    )
    event["description"] = description
    event["description_html"] = richtext.from_plain_text(description)
    event["description_source"] = "generated"
    return event


_CANCELLED_STATUS_WORDS = (
    r"abgesagt(?:\s+(?:werden|wird|wurde))?|entfällt|entfaellt|"
    r"fällt\s+(?:leider\s+)?aus|faellt\s+(?:leider\s+)?aus|"
    r"findet\s+(?:leider\s+)?nicht\s+statt|verschoben"
)


_CANCELLED_STATUS_SUBJECTS = (
    r"veranstaltung|termin|event|konzert|lesung|theaterabend|show|kurs|workshop|"
    r"führung|fuehrung|rundgang|programm|kabarettprogramm"
)


_CANCELLED_TITLE_PATTERN = re.compile(
    rf"^\s*[-–—:()]*\s*(?:{_CANCELLED_STATUS_WORDS})\b"
    rf"|\b(?:{_CANCELLED_STATUS_WORDS})\b\s*[-–—:()]*$",
    re.IGNORECASE,
)


_CANCELLED_CONTEXT_PATTERN = re.compile(
    rf"\b(?:{_CANCELLED_STATUS_SUBJECTS})\b[^\n.!?]{{0,80}}\b(?:{_CANCELLED_STATUS_WORDS})\b"
    rf"|\b(?:{_CANCELLED_STATUS_WORDS})\b[^\n.!?]{{0,80}}\b(?:krankheitsbedingt|neuer\s+termin|nachgeholt)\b",
    re.IGNORECASE,
)


_POSTPONED_VERLEGT_TITLE_PATTERN = re.compile(
    r"^\s*[-–—:()]*\s*verlegt\b|\bverlegt\s*[-–—:()]*$",
    re.IGNORECASE,
)


_POSTPONED_VERLEGT_CONTEXT_PATTERN = re.compile(
    rf"\b(?:{_CANCELLED_STATUS_SUBJECTS})\b\s+(?:wurde|wird|ist)\b"
    r"[^\n.!?]{0,80}\bverlegt\b"
    r"|\bverlegt\b[^\n.!?]{0,80}\b(?:vom|auf|neuer\s+termin|neues\s+datum)\b",
    re.IGNORECASE,
)


def has_cancelled_status(title: str, description: str) -> bool:
    """True when text marks this event as cancelled/postponed."""
    combined = " ".join([title or "", description or ""])
    return bool(
        _CANCELLED_TITLE_PATTERN.search(title or "")
        or _CANCELLED_CONTEXT_PATTERN.search(combined)
        or _POSTPONED_VERLEGT_TITLE_PATTERN.search(title or "")
        or _POSTPONED_VERLEGT_CONTEXT_PATTERN.search(combined)
    )


def event_status(title: str, description: str) -> str:
    """Return a normalized source-independent schedule status."""
    text = " ".join([title or "", description or ""])
    if has_cancelled_status(title, description):
        return (
            "postponed"
            if re.search(r"\b(?:verschoben|verlegt)\b|neuer\s+termin", text, re.IGNORECASE)
            else "cancelled"
        )
    return "scheduled"


def extract_dates(text: str) -> list:
    """Extract parseable dates from free text (for search-result filtering)."""
    text = text or ""
    dates = []
    patterns = [
        r"20\d{2}-\d{2}-\d{2}",
        r"\d{1,2}\.\d{1,2}\.20\d{2}",
        r"\d{1,2}\.\d{1,2}\.\d{2}\b",
        r"\d{1,2}\.\s*(?:Januar|Jan|Februar|Feb|März|Maerz|Mär|Mae|April|Apr|Mai|Juni|Jun|Juli|Jul|August|Aug|September|Sep|Oktober|Okt|November|Nov|Dezember|Dez)\s*20\d{2}",
    ]
    for pat in patterns:
        for m in re.finditer(pat, text, re.I):
            dt = parse_date(m.group(0))
            if dt:
                dates.append((m.start(), dt))
    return [parsed for _position, parsed in sorted(dates, key=lambda item: item[0])]


def date_range_overlaps(dates: list) -> bool:
    """True if any extracted date is inside the window; empty list = unknown = include."""
    if not dates:
        return True
    return any(window_contains(dt) for dt in dates)


def in_date_range(date_str: str) -> bool:
    """True if a date string is in-window, or unparseable (include-when-unknown)."""
    dt = parse_date(date_str)
    if dt is None:
        return True
    return window_contains(dt)


def window_contains(start_dt: datetime | None, end_dt: datetime | None = None) -> bool:
    """Return whether a dated event overlaps the inclusive report window."""
    if start_dt is None:
        return False
    window_end = _impl_run_state.runtime_window().end.replace(hour=23, minute=59, second=59, microsecond=999999)
    effective_end = end_dt or start_dt
    return effective_end >= _impl_run_state.runtime_window().start and start_dt <= window_end


def event_in_window(event: dict) -> bool:
    """Return whether a parsed event overlaps the inclusive report window."""
    start = parse_iso_date(event.get("start_date", ""))
    end = parse_iso_date(event.get("end_date", "")) or start
    if not start:
        date_text = event.get("date", "")
        if "–" in date_text:
            start_text, end_text = date_text.split("–", 1)
            start, end = parse_date(start_text), parse_date(end_text)
        else:
            start = parse_date(date_text)
            end = start
    return True if not start else window_contains(start, end)


def event_in_window_and_radius(
    start_dt: datetime | None, end_dt: datetime | None, city: str,
    coords: tuple | None = None,
) -> bool:
    """Cheap preflight for detail-page fan-out before full event construction."""
    if not window_contains(start_dt, end_dt):
        return False
    resolved_coords, _, _ = resolve_location(city, coords)
    if not resolved_coords:
        return True
    return haversine(_impl_run_state.BONN_LAT, _impl_run_state.BONN_LON, *resolved_coords) <= _impl_run_state.runtime_radius_km()


_FREE_ADMISSION_PATTERNS = (
    r"\b(?:kosten|preis|teilnahmegebühr|teilnahmegebuehr)\s*:\s*(?:frei|kostenlos|kostenfrei)\b",
    r"\beinlass\s*:?\s*(?:gratis|frei|kostenlos|kostenfrei)\b",
    r"\beintritt\s*:?\s*(?:frei|kostenlos|kostenfrei)\b",
    r"\beintritt\s+(?:ist|bleibt)\s+(?:(?:nach\s+wie\s+vor|weiterhin|auch|natürlich|natuerlich|"
    r"wieder|wie\s+immer|(?:für|fuer|zu)\s+alle(?:n)?\s+(?:veranstaltungen|angebote|termine))\s+)*"
    r"(?:frei|kostenlos|kostenfrei)\b",
    r"\beintirtt\s+(?:ist|bleibt)\s+(?:frei|kostenlos|kostenfrei)\b",
    r"\beintritt\s+(?:(?:natürlich|natuerlich|weiterhin|wieder|nach\s+wie\s+vor|"
    r"wie\s+immer)\s+)+(?:frei|kostenlos|kostenfrei)\b",
    r"\beintritt\s+(?:auch\s+)?(?:zu|für|fuer)\s+[^.]{1,60}\s+"
    r"(?:ist|bleibt)\s+(?:frei|kostenlos|kostenfrei)\b",
    r"\bfreier\s+eintritt\b",
    r"\b(?:kostenloser|kostenfreier)\s+eintritt\b",
    r"\b(?:bei|mit)\s+frei(?:em|en)\s+eintritt\b",
    r"\b(?:teilnahme|veranstaltung|ausstellung|ferienprogramm|performance|workshop|angebote?|programm|sportangebot|termin|event)"
    r"\s+.{0,90}\b(?:ist|sind)\s+(?:kostenlos|kostenfrei)\b",
    r"\b(?:kostenlos(?:e[rsn]?|em|en|es)?|kostenfrei(?:e[rsn]?|em|en|es)?)[,\s–-]+"
    r"(?:[a-zäöüß-]+[,\s]+){0,2}(?:teilnahme|veranstaltung|angebot|programm|sportangebot|"
    r"[a-zäöüß-]*(?:workshop|kurs|konzert|führung|fuehrung|tour|training)|termin|event|"
    r"filmvorführung|filmvorfuehrung)\b",
    r"\b(?:workshop|veranstaltung|sonder-veranstaltung|führung|fuehrung|offene werkstatt)"
    r"[^.]{0,80}\b(?:kostenlos|kostenfrei)\b",
    r"\b(?:kostenlos|kostenfrei)\s*(?:[-–]\s*)?(?:und\s+)?"
    r"(?:keine anmeldung|anmeldung erforderlich|ohne anmeldung)\b",
    r"\b(?:kostenlos|kostenfrei)\s+und\s+(?:draußen|draussen)\s*[-–,]?\s*"
    r"(?:keine anmeldung|ohne anmeldung)\b",
    r"(?:^|[.!?]\s*)kostenlos\s+und\s+unverbindlich\b",
    r"\b(?:kostenlos|kostenfrei)\s+ab\s+\d+\b",
    r"\b(?:du\s+kannst|kannst\s+du|ihr\s+(?:könnt|koennt)|(?:könnt|koennt)\s+ihr|"
    r"sie\s+(?:können|koennen)|(?:können|koennen)\s+sie|man\s+kann)\b"
    r"[^.]{0,140}\b(?:kostenlos|kostenfrei)\b"
    r"[^.]{0,60}\b(?:anhören|anhoeren|besuchen|teilnehmen|mitmachen)\b",
)


_FREE_TITLE_PATTERN = re.compile(r"^\s*(?:kostenlos|kostenfrei)\s+", re.IGNORECASE)


_FREE_DESCRIPTION_BLOCK_PATTERN = re.compile(
    r"(?im)^\s*(?:"
    r"(?:kostenlos|kostenfrei)(?:\s+natürlich|\s+natuerlich)?\s*"
    r"(?:[.!][ \t]*(?=\n|$)|$)"
    r"|frei\s*(?:[.!]?\s*$|,\s*(?:es\s+geht\s+der\s+hut\s+rum|hutspenden?\b|spenden?\b).*$)"
    r")",
)


_FREE_PRICE_PATTERN = re.compile(
    r"^(?:(?:eintritt|kosten|preis|teilnahmegebühr|teilnahmegebuehr)\s*:?\s*)?"
    r"(?:(?:frei|kostenlos|kostenfrei|free)"
    # Calendar templates append their currency unconditionally, so a free event
    # arrives as "Eintritt: frei€" or "Eintritt: frei 0 €". Without this the
    # whole string fails the match, the price is treated as a real amount and
    # the event is published as paid. The trailing group stays anchored so
    # "Eintritt: freitags 10 €" is still not free.
    r"(?:\s*[,;/(-].*|(?:\s*0(?:[,.]00)?)?\s*(?:€|eur|euro))?"
    r"|0(?:[,.]00)?\s*(?:€|eur|euro))$",
    re.IGNORECASE,
)


_MUSEUM_VISITOR_ACCESS_PATTERN = (
    r"(?:museumseintritt|eintritt\s+(?:ins|in\s+das)\s+museum)"
)


_INFLECTED_MUSEUM_VISITOR_ACCESS_PATTERN = (
    r"(?:museumseintritts?|eintritt\s+(?:ins|in\s+das)\s+museum)"
)


_PAID_MUSEUM_PREDICATE_PATTERN = (
    r"(?:zu\s+(?:zahlen|bezahlen|entrichten)|"
    r"muss\s+(?:bezahlt|entrichtet|gezahlt)\s+werden|"
    r"wird\s+(?:erhoben|berechnet)|kostenpflichtig|erforderlich|"
    r"fällt\s+zusätzlich\s+an)"
)


_PAID_VISITOR_ACCESS_WITHOUT_AMOUNT = re.compile(
    rf"\b(?:"
    rf"zu\s+zahlen\s+ist\s+der\s+(?:reguläre\s+)?{_MUSEUM_VISITOR_ACCESS_PATTERN}|"
    rf"es\s+gilt\s+(?:der\s+reguläre\s+)?{_MUSEUM_VISITOR_ACCESS_PATTERN}|"
    rf"(?:zuzüglich(?:\s+ist)?|zzgl\.?)\s+"
    rf"(?:(?:des|dem|der)\s+)?(?:regulär(?:e|en|er|es|em)\s+)?"
    rf"{_INFLECTED_MUSEUM_VISITOR_ACCESS_PATTERN}|"
    rf"{_MUSEUM_VISITOR_ACCESS_PATTERN}[^.!?;]{{0,20}}"
    rf"\bnicht\s+(?:kostenlos|kostenfrei|frei)|"
    rf"{_MUSEUM_VISITOR_ACCESS_PATTERN}[^.!?;]{{0,50}}"
    rf"{_PAID_MUSEUM_PREDICATE_PATTERN})\b",
    re.IGNORECASE,
)


_NEGATED_PAID_VISITOR_ACCESS = re.compile(
    rf"\b(?:nicht(?:\s+mehr)?|gar\s+nicht|ausdrücklich\s+nicht|"
    rf"keinesfalls|keineswegs|nie|unter\s+keinen\s+umständen|"
    rf"auf\s+keinen\s+fall)"
    rf"(?:\s+(?:extra|zusätzlich|gesondert|separat))*\s+"
    rf"zu\s+zahlen\s+ist\s+(?:der\s+)?(?:reguläre\s+)?"
    rf"{_MUSEUM_VISITOR_ACCESS_PATTERN}|"
    rf"\bkein(?:e|en|er|es)?(?:\s+[\w-]+){{0,8}}\s+"
    rf"{_MUSEUM_VISITOR_ACCESS_PATTERN}[^.!?;]{{0,40}}"
    rf"{_PAID_MUSEUM_PREDICATE_PATTERN}|"
    rf"\b{_MUSEUM_VISITOR_ACCESS_PATTERN}[^.!?;]{{0,40}}\b"
    rf"(?:nicht(?:\s+mehr)?|gar\s+nicht|keineswegs|keinesfalls|"
    rf"ausdrücklich\s+nicht|nie|unter\s+keinen\s+umständen|"
    rf"auf\s+keinen\s+fall|weder[^.!?;]{{0,30}}noch)\b"
    rf"[^.!?;]{{0,20}}{_PAID_MUSEUM_PREDICATE_PATTERN}|"
    rf"\b(?:weder|nie|unter\s+keinen\s+umständen)\b[^.!?;]{{0,40}}"
    rf"{_MUSEUM_VISITOR_ACCESS_PATTERN}[^.!?;]{{0,50}}"
    rf"{_PAID_MUSEUM_PREDICATE_PATTERN}",
    re.IGNORECASE,
)


def has_paid_visitor_access(text: str) -> bool:
    """Recognize positive museum charges without treating negations as paid."""
    negated = list(_NEGATED_PAID_VISITOR_ACCESS.finditer(text or ""))
    for match in _PAID_VISITOR_ACCESS_WITHOUT_AMOUNT.finditer(text or ""):
        if not any(
            negation.start() <= match.start() and match.end() <= negation.end()
            for negation in negated
        ):
            return True
    return False


_has_paid_visitor_access_without_amount = has_paid_visitor_access


_LIMITED_FREE_WITH_PAID_PATTERN = re.compile(
    r"\b(?:kosten|preise?|eintritt|teilnahme|gebühr|gebuehr|führungen?|fuehrungen?|"
    r"erwachsene|ermäßigt|ermaessigt)\b[^.]{0,100}\b\d+[,.]?\d*\s*(?:€|eur|euro)(?!\w)",
    re.IGNORECASE,
)


_LIMITED_FREE_CONTEXT_PATTERNS = (
    r"\beintritt\s+in\s+(?:den|die|das)\s+[^.]{0,50}\s+ist\s+frei\b",
    r"\bkinder(?:n)?\s+bis\s+\d+[^.]{0,40}\s+(?:kostenlos|frei)\b",
    r"\b(?:kostenlos|frei)[^.]{0,40}\bkinder(?:n)?\s+bis\s+\d+\b",
)


_LIMITED_FREE_TRIAL_PATTERN = re.compile(
    r"\b(?:erste|ersten|erstes|erstmalige|einmalige)\b[^.]{0,80}"
    r"\b(?:kostenlos|kostenfrei)(?:e[rsn]?|em|en|es)?\s+probe(?:stunde|training|termin)\b",
    re.IGNORECASE,
)


_CONDITIONAL_FREE_VISITOR_GROUP_PATTERN = (
    r"(?:kind(?:er(?:n)?)?|jugendlich(?:e|en|er|es)|person(?:en)?|mensch(?:en)?|"
    r"mitglied(?:er(?:n)?)?|begleitperson(?:en)?)"
)


_CONDITIONAL_FREE_ADMISSION_PATTERN = re.compile(
    r"\b(?:freier|kostenloser|kostenfreier)\s+eintritt\b[^.!?]{0,100}"
    r"(?:\bam\s+eröffnungsabend\b|\ban\s+(?:jedem\s+)?(?:ersten\s+)?sonntag\b|"
    rf"\bnur\b|\bfür\s+(?!alle\b){_CONDITIONAL_FREE_VISITOR_GROUP_PATTERN}\b)"
    rf"|\b{_CONDITIONAL_FREE_VISITOR_GROUP_PATTERN}\b[^.!?]{{0,100}}"
    r"\b(?:freien\s+eintritt|eintritt\s+(?:ist\s+)?frei|"
    r"kostenlos(?:e(?:n|r|m|s)?\s+eintritt)?)\b"
    r"|\b(?:am\s+eröffnungsabend|an\s+(?:jedem\s+)?(?:ersten\s+)?sonntag)\b"
    r"[^.!?]{0,100}\b(?:eintritt\b[^.!?]{0,30}\bfrei|freier\s+eintritt)\b",
    re.IGNORECASE,
)


def has_conditional_free_admission(value: str) -> bool:
    """Return whether free access is limited to a date or visitor group."""
    return bool(_CONDITIONAL_FREE_ADMISSION_PATTERN.search(_impl_text.clean_html(value or "")))


_EXPLICIT_ADMISSION_SOURCE_IDS = frozenset({
    "adfc-bonn",
    "haus-der-geschichte",
    "literaturhaus-bonn",
    "naturregion-sieg",
    "troisdorf",
})


def source_preserves_explicit_admission(source: str, source_id: str) -> bool:
    """Return whether this audited first-party adapter owns maintained copy."""
    normalized = normalize_source_id(source_id or source)
    return (
        normalized in _EXPLICIT_ADMISSION_SOURCE_IDS
        or normalized.startswith("sitekit-")
        or normalized.startswith("ionas4-")
    )


def source_requires_pre_truncation_admission(source: str, source_id: str) -> bool:
    """Return whether the adapter shortens maintained logistics after parsing."""
    normalized = normalize_source_id(source_id or source)
    return normalized in {"haus-der-geschichte", "literaturhaus-bonn"}


_DIRECT_EXPLICIT_FREE = re.compile(
    r"\b(?:eintritt|teilnahme|einlass)\b[^.!?]{0,50}"
    r"\b(?:frei|gratis|kostenlos|kostenfrei)\b"
    r"|\b(?:frei(?:er|em)|kostenloser|kostenfreier)\s+(?:eintritt|einlass)\b"
    r"|\b(?:veranstaltung|event|unser\s+angebot)\b[^.!?]{0,80}"
    r"\b(?:frei|kostenlos|kostenfrei)\b",
    re.IGNORECASE,
)


_ACTIVITY_EXPLICIT_FREE = re.compile(
    r"\b(?P<activity>workshop|führung|fuehrung|tour|training|konzert|programm)\b"
    r"[^.!?]{0,60}\b(?:ist|sind)\s+(?:frei|kostenlos|kostenfrei)\b"
    r"|\b(?:kostenlose|kostenloser|kostenlosen|kostenfreie|kostenfreier|kostenfreien)"
    r"(?:[,\s–-]+[a-zäöüß-]+){0,2}[,\s–-]+(?P<prefixed>workshop|führung|fuehrung|[a-zäöüß-]*tour|training|konzert|programm)\b",
    re.IGNORECASE,
)


def has_explicit_free_admission_wording(title: str, description: str) -> bool:
    """Recognize event-scoped free wording, excluding qualified offers."""
    text = _impl_text.clean_html(description or "")
    if not text or has_conditional_free_admission(text):
        return False
    if _has_paid_visitor_access_without_amount(text):
        return False
    if _LIMITED_FREE_TRIAL_PATTERN.search(text):
        return False
    normalized = re.sub(r"\s+", " ", text).strip().casefold()
    if _VISITOR_ADMISSION_AMOUNT_PATTERN.search(normalized):
        return False
    if _FREE_DESCRIPTION_BLOCK_PATTERN.search(_impl_text.clean_html_blocks(description or "")):
        return True
    if _DIRECT_EXPLICIT_FREE.search(normalized):
        return True
    title_text = _impl_text.clean_html(title or "").casefold()
    activity_suffixes = ("workshop", "führung", "fuehrung", "tour", "training", "konzert", "programm")
    for match in _ACTIVITY_EXPLICIT_FREE.finditer(normalized):
        activity = (match.group("activity") or match.group("prefixed") or "").casefold()
        if activity in title_text:
            return True
        if any(activity.endswith(suffix) and suffix in title_text for suffix in activity_suffixes):
            return True
    return False


_IMPLICIT_FREE_TITLE_PATTERN = re.compile(
    r"\b(?:flohmarkt|trödelmarkt|troedelmarkt|hofflohmarkt|hausflohmarkt|"
    r"straßenflohmarkt|strassenflohmarkt|stadtflohmarkt|büchermarkt|buechermarkt|"
    r"stadtteilfest|straßenfest|strassenfest|veedelsfest|dorffest|"
    r"nachbarschaftsfest|tag\s+der\s+offenen\s+tür|tag\s+der\s+offenen\s+tuer|"
    r"repair[-\s]?caf[ée]|reparaturcaf[ée])\b",
    re.IGNORECASE,
)


_IMPLICIT_FREE_EXCLUSION_PATTERN = re.compile(
    r"\b(?:nachtflohmarkt|indoor[-\s]?(?:floh|trödel|troedel)?markt|messe|"
    r"stadthalle|eventhalle|ticket(?:s|preis)?|besucher(?:eintritt|preis))\b",
    re.IGNORECASE,
)


_VISITOR_ADMISSION_AMOUNT_PATTERN = re.compile(
    r"\b(?:(?:eintritt|besucher(?:preis|eintritt)|ticket(?:preis)?|"
    r"teilnahme(?:gebühr|gebuehr|kosten)|teilnehmergebühr|teilnehmergebuehr|"
    r"kostenbeitrag|kursgebühr|kursgebuehr|workshopgebühr|workshopgebuehr)\b[^.]{0,60}|"
    r"(?:gäste|gaeste|erwachsene)\s+(?:zahlen|bezahlen|kosten)\s*)"
    # `\b` after `€` would never match at end of string: use a word-char guard so
    # the common German notation ("Eintritt: 4,50 €") is recognised.
    r"\b\d+[,.]?\d*\s*(?:€|eur|euro)(?!\w)",
    re.IGNORECASE,
)


_SELLER_FEE_PATTERN = re.compile(
    r"\b(?:standgebühr|standgebuehr|standpreis|standfläche\s+kostet|"
    r"standflaeche\s+kostet|lfdm|laufend(?:e|er|en)?\s+(?:front)?meter|"
    r"reinigungskaution|verkäufergebühr|verkaeufergebuehr|händlergebühr|"
    r"haendlergebuehr)\b",
    re.IGNORECASE,
)


def has_seller_fee(value: str) -> bool:
    """Return whether copy names a vendor charge rather than visitor admission."""
    return bool(_SELLER_FEE_PATTERN.search(_impl_text.clean_html(value or "")))


def infer_admission(
    title: str,
    description: str,
    price: str = "",
    *,
    admission: AdmissionDefault | None = None,
    admission_basis: str = "",
) -> tuple[str, str]:
    """Infer admission from event copy, price, or a declared source default."""
    # Transport metadata is not admission evidence. A venue or URL containing
    # "Eintritt frei" must not silently turn a paid event into a free one.
    raw = " ".join([title or "", description or "", price or ""])
    text = _impl_text.clean_html(raw).lower()
    # Some upstream WordPress copy glues adjacent logistics labels together
    # ("16:30 UhrEintritt frei"). Repair only this explicit boundary rather
    # than inserting spaces inside arbitrary camel-cased words.
    text = re.sub(r"\buhr(?=eintritt\b)", "uhr ", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(kostenfrei|kostenlos)(?=ab\s+\d)", r"\1 ", text)
    text = re.sub(r"\s+", " ", text)
    price_text = _impl_text.clean_html(price or "").lower().strip()

    # A broad calendar tag such as "Kostenlos" can coexist with prose that
    # limits free access to an opening night, a monthly museum day, children or
    # members.  The conditional prose is the stronger fact; do not publish the
    # occurrence as free for every visitor.  A separate, unqualified sentence
    # ("Der Eintritt ist frei.") still wins because it is explicit whole-event
    # evidence rather than the same qualified offer.
    description_text = _impl_text.clean_html(description or "")
    conditional_free = has_conditional_free_admission(description_text)
    unconditional_description = _CONDITIONAL_FREE_ADMISSION_PATTERN.sub(
        " ", description_text,
    )
    unconditional_free = (
        bool(_FREE_DESCRIPTION_BLOCK_PATTERN.search(_impl_text.clean_html_blocks(unconditional_description)))
        or any(
            re.search(pattern, unconditional_description, re.IGNORECASE)
            for pattern in _FREE_ADMISSION_PATTERNS
        )
    )
    if _has_paid_visitor_access_without_amount(description_text):
        return "kostenpflichtig", "explicit"
    if conditional_free and not unconditional_free:
        return "", ""

    visitor_charge = bool(_VISITOR_ADMISSION_AMOUNT_PATTERN.search(text))
    seller_fee = has_seller_fee(text)
    price_has_amount = bool(re.search(
        r"(?<!\d)\d+(?:[.,]\d{1,2})?\s*(?:€|eur\b|euro\b)",
        price_text,
        re.IGNORECASE,
    ))
    price_states_whole_event_is_free = (
        bool(_FREE_DESCRIPTION_BLOCK_PATTERN.search(price_text))
        or any(
            re.search(pattern, price_text, re.IGNORECASE)
            for pattern in _FREE_ADMISSION_PATTERNS
        )
    )
    if admission_basis == "implicit" and (visitor_charge or seller_fee):
        return "", ""
    if _FREE_PRICE_PATTERN.fullmatch(price_text):
        return "kostenlos", admission_basis or "explicit"
    # Structured municipal calendars frequently expose a complete sentence in
    # their price field (for example, "Die Teilnahme ist kostenlos.").  Treat
    # that as explicit whole-event evidence only when the same field contains
    # no monetary amount; conditional free tiers and paid add-ons must remain
    # paid.
    if price_states_whole_event_is_free and not price_has_amount:
        return "kostenlos", admission_basis or "explicit"
    if price_text:
        return "", "explicit"
    if visitor_charge:
        return "", ""
    if _LIMITED_FREE_WITH_PAID_PATTERN.search(text) and any(re.search(pattern, text, re.IGNORECASE) for pattern in _LIMITED_FREE_CONTEXT_PATTERNS):
        return "", ""
    if _LIMITED_FREE_TRIAL_PATTERN.search(_impl_text.clean_html(description or "")):
        return "", ""
    if admission == AdmissionDefault.SOURCE_CONFIRMED_FREE:
        return "kostenlos", "explicit"
    if _FREE_TITLE_PATTERN.search(_impl_text.clean_html(title or "")):
        return "kostenlos", "inferred"
    if _FREE_DESCRIPTION_BLOCK_PATTERN.search(_impl_text.clean_html_blocks(description or "")):
        return "kostenlos", admission_basis or "inferred"
    if any(re.search(pattern, text, re.IGNORECASE) for pattern in _FREE_ADMISSION_PATTERNS):
        return "kostenlos", admission_basis or "inferred"
    clean_title = _impl_text.clean_html(title or "")
    if (
        _IMPLICIT_FREE_TITLE_PATTERN.search(clean_title)
        and not price_text
        and not _IMPLICIT_FREE_EXCLUSION_PATTERN.search(text)
        and not visitor_charge
        and not seller_fee
    ):
        return "kostenlos", "implicit"
    if admission == AdmissionDefault.FREE_BY_NATURE and not visitor_charge:
        return "kostenlos", "implicit"
    return "", ""


def infer_free_admission_price(
    title: str,
    description: str,
    price: str = "",
    *,
    admission: AdmissionDefault | None = None,
) -> str:
    """Return a normalized free-admission label from explicit or safe implicit evidence."""
    return infer_admission(
        title, description, price, admission=admission,
    )[0]


def _event_time_fields(
    start: datetime | None,
    end: datetime | None,
    time_text: str,
    time_note: str,
    all_day: bool | None,
) -> tuple[str, str, bool]:
    if not time_text and start and (start.hour or start.minute):
        time_text = start.strftime("%H:%M")
        if end and (end.hour or end.minute):
            time_text += "–" + end.strftime("%H:%M")
    canonical_time, inferred_note = _impl_text.normalize_time_fields(time_text)
    if not canonical_time and start and (start.hour or start.minute):
        derived = start.strftime("%H:%M")
        if end and (end.hour or end.minute):
            derived += "–" + end.strftime("%H:%M")
        canonical_time, _ = _impl_text.normalize_time_fields(derived)
    combined_note = _impl_text.combine_time_notes(time_note, inferred_note)
    if all_day is None:
        all_day = not canonical_time and not combined_note and not (
            start and (start.hour or start.minute)
        )
    return canonical_time, combined_note, all_day


_CANONICAL_TIME_PATTERN = re.compile(
    r"^(?P<start_hour>\d{2}):(?P<start_minute>\d{2})"
    r"(?:–(?P<end_hour>\d{2}):(?P<end_minute>\d{2}))?$"
)


def _structured_event_times(
    start: datetime | None,
    end: datetime | None,
    canonical_time: str,
    all_day: bool,
) -> tuple[datetime | None, datetime | None]:
    """Apply one explicit clock or range without inventing an end time."""
    if start is None or all_day:
        return None, None
    match = _CANONICAL_TIME_PATTERN.fullmatch(canonical_time)
    if not match:
        # Complex notes can contain several slots. A date-only midnight is not
        # one of those slots and must not become a structured occurrence.
        structured_start = start if start.hour or start.minute else None
        structured_end = (
            end
            if structured_start and end and end > structured_start
            else None
        )
        return structured_start, structured_end

    structured_start = start.replace(
        hour=int(match.group("start_hour")),
        minute=int(match.group("start_minute")),
        second=0,
        microsecond=0,
    )
    if match.group("end_hour") is None:
        # A repeated start/start supplied by legacy adapters means that the end
        # is unknown. Preserve a genuinely distinct structured end if one exists.
        structured_end = (
            end
            if end and end > structured_start
            else None
        )
        return structured_start, structured_end

    end_day = end if end and end.date() > start.date() else start
    structured_end = end_day.replace(
        hour=int(match.group("end_hour")),
        minute=int(match.group("end_minute")),
        second=0,
        microsecond=0,
    )
    if structured_end <= structured_start:
        structured_end += timedelta(days=1)
    return structured_start, structured_end


def _event_location(
    city: str, venue: str, coords: tuple | None,
) -> tuple[VenueResolution, float | None, str, str]:
    canonical_venue = resolve_venue(venue, city)
    registry_coords = (
        (canonical_venue.venue_latitude, canonical_venue.venue_longitude)
        if canonical_venue.venue_latitude is not None
        and canonical_venue.venue_longitude is not None
        else None
    )
    resolved, confidence, source = resolve_location(
        city, coords if coords is not None else registry_coords,
    )
    if coords is None and registry_coords is not None:
        source = "venue_registry"
    distance = haversine(_impl_run_state.BONN_LAT, _impl_run_state.BONN_LON, *resolved) if resolved else None
    return canonical_venue, distance, confidence, source


@dataclass(frozen=True)
class _QualityPreparation:
    title: str
    city: str
    location: tuple[VenueResolution, float | None, str, str]
    description: str
    link: str
    status: str
    outside_window: bool
    decision: QualityDecision


_ICalQualityCache = dict[tuple[tuple[str, str], ...], QualityDecision]


_ICAL_QUALITY_CACHE_SIZE = 2048


@performance.measured("ical.quality_preparation")
def _prepare_ical_quality(draft: EventDraft, cache: _ICalQualityCache | None = None) -> _QualityPreparation:
    """Build the exact shared quality inputs without taxonomy, admission, or markup."""
    title = normalize_event_title(draft.title, start=draft.start, end=draft.end, source=draft.source)
    city = canonicalize_city(draft.city)
    city = refine_bonn_location(city, f"{draft.venue} {city}")
    location = _event_location(city, draft.venue, draft.coords)
    description = _impl_text.concise_description(draft.description)
    link = _impl_text.normalize_url(draft.link)
    if _impl_text.is_raw_api_url(link):
        link = ""
    status = event_status(title, draft.description)
    quality_input = {
        "title": _impl_text.clean_html(title), "description": description,
        "venue": location[0].venue, "link": link, "category": draft.category,
        "source": draft.source, "source_id": draft.source_id, "status": status,
    }
    key = tuple(quality_input.items())
    decision = cache.get(key) if cache is not None else None
    if decision is None:
        performance.count("ical_quality_cache_misses")
        decision = evaluate_event_quality(quality_input)
        if cache is not None:
            if len(cache) >= _ICAL_QUALITY_CACHE_SIZE:
                performance.count("ical_quality_cache_evictions", len(cache))
                cache.clear()
            cache[key] = decision
    else:
        performance.count("ical_quality_cache_hits")
    return _QualityPreparation(
        title, city, location, description, link, status,
        bool(draft.start is not None and not window_contains(draft.start, draft.end)), decision,
    )


@performance.measured("canonicalization.build_event")
def build_event(draft: EventDraft, *, _prepared: _QualityPreparation | None = None) -> RawEvent | None:
    """Normalize one bundled event draft and apply radius and quality checks.

    ``coords`` optionally pins the event to an explicit (lat, lon) — e.g. a venue
    point — instead of deriving it from ``city`` via :func:`coords_for_city`.
    """
    title, start_dt, end_dt = draft.title, draft.start, draft.end
    venue, city, description = draft.venue, draft.city, draft.description
    link, source, category, trust = draft.link, draft.source, draft.category, draft.trust
    time_text, coords, all_day = draft.time_text, draft.coords, draft.all_day
    timezone_name, source_id = draft.timezone_name, draft.source_id
    source_role, discovered_via, link_kind = (
        draft.source_role, draft.discovered_via, draft.link_kind,
    )
    description_source, admission = draft.description_source, draft.admission
    time_note = draft.time_note
    default_category_key, category_locked = draft.default_category_key, draft.category_locked
    if not title or (start_dt is None and end_dt is not None):
        return None
    title = _prepared.title if _prepared else normalize_event_title(title, start=start_dt, end=end_dt, source=source)
    # Most sources only ever report "Bonn". Resolve the district centrally from
    # the venue so every source benefits instead of each repeating the lookup.
    if _prepared:
        city = _prepared.city
    else:
        city = canonicalize_city(city)
        city = refine_bonn_location(city, f"{venue} {city}")
    outside_window = _prepared.outside_window if _prepared else bool(start_dt is not None and not window_contains(start_dt, end_dt))
    _impl_run_state._record_parser_candidate(out_of_window=outside_window)
    canonical_venue, km, location_confidence, location_source = _prepared.location if _prepared else _event_location(city, venue, coords)
    date_text = start_dt.strftime("%Y-%m-%d") if start_dt else ""
    ongoing = bool(start_dt and end_dt and start_dt < _impl_run_state.runtime_window().start <= end_dt)
    time_text, time_note, all_day = _event_time_fields(
        start_dt, end_dt, time_text, time_note, all_day,
    )
    structured_start, structured_end = _structured_event_times(
        start_dt, end_dt, time_text, all_day,
    )
    full_text = f"{title} {venue} {city} {description} {category}"
    # URLs encode venue slugs and other implementation detail (for example
    # ``alte-vhs`` in an aggregator concert URL). They are not event content and
    # must not affect the display category.
    canonical_category = category_taxonomy.categorize_event(
        category,
        title,
        description,
        venue=venue,
        source=source,
        source_id=source_id,
        default_category_key=default_category_key,
        category_locked=category_locked,
    )
    event_link = _prepared.link if _prepared else _impl_text.normalize_url(link)
    if _impl_text.is_raw_api_url(event_link):
        event_link = ""
    status = _prepared.status if _prepared else event_status(title, description)
    start_date = start_dt.strftime("%Y-%m-%d") if start_dt else ""
    final_end = (
        end_dt or start_dt
        if all_day
        else structured_end if "–" in time_text else (end_dt or start_dt)
    )
    end_date = final_end.strftime("%Y-%m-%d") if final_end else ""
    local_zone = ZoneInfo(timezone_name)
    start_at = "" if not structured_start else structured_start.replace(tzinfo=local_zone).isoformat(timespec="minutes")
    end_at = "" if not structured_end else structured_end.replace(tzinfo=local_zone).isoformat(timespec="minutes")
    price, admission_basis = infer_admission(title, description, admission=admission)
    if (
        admission_basis == "inferred"
        and source_requires_pre_truncation_admission(source, source_id)
        and has_explicit_free_admission_wording(title, description)
    ):
        admission_basis = "explicit"
    concise = _prepared.description if _prepared else _impl_text.concise_description(description)
    ev: RawEvent = {
        "title": _impl_text.clean_html(title),
        "date": date_text,
        "time": time_text,
        "time_note": time_note,
        "venue": canonical_venue.venue,
        "venue_id": canonical_venue.venue_id,
        "venue_address": canonical_venue.venue_address,
        "venue_district": canonical_venue.venue_district,
        "venue_type": canonical_venue.venue_type,
        "venue_latitude": canonical_venue.venue_latitude,
        "venue_longitude": canonical_venue.venue_longitude,
        "city": _impl_text.clean_html(city).title(),
        "description": concise,
        # Every event carries renderable markup. A source that kept the raw
        # HTML overwrites this with the real headings and lists afterwards.
        "description_html": richtext.from_plain_text(concise),
        "description_source": description_source or _impl_text.description_source_for(description),
        "price": price,
        "admission_basis": admission_basis,
        "link": event_link,
        "distance_km": round(km, 1) if km is not None else None,
        "location_confidence": location_confidence,
        "location_source": location_source,
        "score": round(distance_score(km, _impl_run_state.runtime_radius_km()) * category_score(full_text) * trust, 2) if km is not None
                 else round(0.3 * category_score(full_text) * trust, 2),
        "source": source,
        "source_id": source_id,
        "source_role": source_role,
        "discovered_via": list(discovered_via),
        "link_kind": link_kind,
        "status": status,
        "start_at": start_at,
        "end_at": end_at,
        "start_date": start_date,
        "end_date": end_date,
        "all_day": all_day,
        "ongoing": ongoing,
        "timezone": timezone_name,
        "category": category,
        "category_key": canonical_category["key"],
        "category_label": canonical_category["label"],
        "category_confidence": canonical_category.get("confidence", 0),
        "category_reason": canonical_category.get("reason", ""),
    }
    if status == "postponed":
        replacement_dates = [
            candidate for candidate in extract_dates(f"{title} {description}")
            if not start_dt or candidate.date() != start_dt.date()
        ]
        if replacement_dates:
            ev["replacement_start_date"] = replacement_dates[0].strftime("%Y-%m-%d")
    if status in {"cancelled", "postponed"}:
        # Preserve schedule changes as first-class candidates. The runner binds
        # them to the scheduled occurrence after source-authority deduplication.
        result = getattr(_impl_run_state._SOURCE_CONTEXT, "result", None)
        if result is not None:
            result.cancelled_events.append(ev)
    decision = _prepared.decision if _prepared else evaluate_event_quality(ev)
    if decision.should_drop:
        if not outside_window:
            _impl_run_state.log_source_quality_skip(source, decision.rule_id)
        return None
    return ev


def make_event(title: str, start_dt: datetime | None, end_dt: datetime | None,
               venue: str, city: str, description: str, link: str, source: str,
               category: str, trust: float = 1.0, time_text: str = "",
               coords: tuple | None = None, all_day: bool | None = None,
               timezone_name: str = "Europe/Berlin", source_id: str = "",
               description_source: str = "",
               admission: AdmissionDefault | None = None,
               time_note: str = "",
               default_category_key: str = "",
               category_locked: bool = False,
               source_role: str = "primary",
               discovered_via: tuple[str, ...] = (),
               link_kind: str = "", _early_quality: bool = False,
               _quality_cache: _ICalQualityCache | None = None) -> RawEvent | None:
    """Compatibility adapter for source modules migrating to :class:`EventDraft`."""
    draft = EventDraft(
        title=title, start=start_dt, end=end_dt, venue=venue, city=city,
        description=description, link=link, source=source, category=category,
        trust=trust, time_text=time_text, coords=coords, all_day=all_day,
        timezone_name=timezone_name, source_id=source_id,
        description_source=description_source, admission=admission,
        time_note=time_note, default_category_key=default_category_key,
        category_locked=category_locked, source_role=source_role,
        discovered_via=discovered_via, link_kind=link_kind,
    )
    if (
        _early_quality and title and start_dt is not None
        and (not default_category_key or default_category_key in category_taxonomy.CATEGORY_BY_KEY)
        and (not category_locked or default_category_key)
    ):
        prepared = _prepare_ical_quality(draft, _quality_cache)
        # Schedule changes must reach the complete cancellation/tombstone path,
        # even if the shared quality policy would otherwise reject the record.
        if prepared.status == "scheduled":
            if prepared.decision.should_drop:
                _impl_run_state._record_parser_candidate(out_of_window=prepared.outside_window)
                if not prepared.outside_window:
                    _impl_run_state.log_source_quality_skip(source, prepared.decision.rule_id)
                performance.count("ical_pruned_quality_candidates")
                return None
            return build_event(draft, _prepared=prepared)
    return build_event(draft)


def _legacy_is_junk_event(ev: dict) -> bool:
    """Compatibility boolean for callers that have not migrated to decisions."""
    return legacy_junk_decision(ev) is not None


def is_junk_event(ev: dict) -> bool:
    """Compatibility wrapper for callers that only need the boolean policy."""
    return evaluate_event_quality(ev).should_drop


def search_result_event(
    title: str,
    link: str,
    desc: str,
    source: str,
    trust: float,
    *,
    explicit_date: datetime | None = None,
) -> RawEvent | None:
    """Convert a search result through the same canonical draft pipeline as adapters."""
    full_text = f"{title} {desc} {link}"
    extracted_dates = [explicit_date] if explicit_date else extract_dates(full_text)
    if not extracted_dates:
        return None
    if not date_range_overlaps(extracted_dates):
        return None
    city_guess = guess_city_from_text(full_text)
    if not city_guess:
        return None
    start = extracted_dates[0]
    return build_event(EventDraft(
        title=unescape(_impl_text.clean_html(title)),
        start=start,
        end=start,
        venue="",
        city=city_guess,
        description=_impl_text.clean_html(desc),
        link=link,
        source=source,
        category="search fallback",
        trust=trust,
        all_day=True,
    ))
