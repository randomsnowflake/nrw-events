"""Standalone, data-backed compatibility rules for editorial junk filtering."""

from __future__ import annotations

from dataclasses import dataclass
from html import unescape
import json
from pathlib import Path
import re
from typing import Any, Callable, Mapping

from .event_vocabulary import ROUTINE_MARKET_DROP_TERMS


Decision = tuple[str, str, tuple[str, ...]]
Predicate = Callable[["EventText"], tuple[str, ...] | None]


def _load_terms() -> dict[str, frozenset[str]]:
    path = Path(__file__).with_name("junk_rules_data.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {name: frozenset(values) for name, values in payload.items()}


_TERMS = _load_terms()
JUNK_TITLE_TERMS = _TERMS["junk_title"]
JUNK_LINK_TERMS = _TERMS["junk_link"]
PRIVATE_EVENT_TERMS = _TERMS["private_event"]
STATIC_ATTRACTION_TERMS = _TERMS["static_attraction"]
GOVERNANCE_TERMS = _TERMS["governance"]
ROUTINE_PHRASE_TERMS = _TERMS["routine_phrase"]
CULTURAL_EVENT_TERMS = _TERMS["cultural_event"]
RECURRING_DESTINATION_TERMS = _TERMS["recurring_destination"]
ROUTINE_COURSE_TERMS = _TERMS["routine_course"]
RECURRING_COURSE_MARKERS = _TERMS["recurring_course_marker"]
COURSE_CONTEXT_TERMS = _TERMS["course_context"]
SEARCH_STRONG_SIGNALS = _TERMS["search_strong_signal"]
EXPLICIT_LOCAL_EVENT_TERMS = _TERMS["explicit_local_event"]
SEARCH_STATIC_PAGE_TERMS = _TERMS["search_static_page"]

_WEAK_RECURRENCE_TERMS = frozenset({
    "regelmäßig", "regelmaessig", "wöchentlich", "woechentlich", "wiederkehrend",
})
_STRONG_ROUTINE_PHRASE_TERMS = ROUTINE_PHRASE_TERMS - _WEAK_RECURRENCE_TERMS
_WEAK_COURSE_TERMS = frozenset({"beratung", "fortgeschrittene"})
_STRONG_ROUTINE_COURSE_TERMS = ROUTINE_COURSE_TERMS - _WEAK_COURSE_TERMS
_ROUTINE_MEETUP_CONTEXT = re.compile(
    r"\b(?:[a-zäöüß]+treff(?:en)?|"
    r"treff(?:en)?|treffpunkt|stammtisch|(?:senioren|frauen)kreis|gruppe|"
    r"selbsthilfegruppe|gesprächsrunde|gespraechsrunde|clubabend|spiele[-\s]nachmittag)\b"
)
_EVENT_RECURRENCE_CONTEXT = re.compile(
    r"\b(?:regelmäßig(?:e[rsn]?)?|regelmaessig(?:e[rsn]?)?|"
    r"wöchentlich(?:e[rsn]?)?|woechentlich(?:e[rsn]?)?|wiederkehrend(?:e[rsn]?)?|"
    r"monatlich(?:e[rsn]?)?|montags|dienstags|mittwochs|donnerstags|freitags|samstags|sonntags|"
    r"jeden\s+(?:(?:ersten|zweiten|dritten|vierten)\s+)?"
    r"(?:montag|dienstag|mittwoch|donnerstag|freitag|samstag|sonntag)|"
    r"am\s+(?:ersten|zweiten|dritten|vierten)\s+\w+\s+im\s+monat|"
    r"alle\s+(?:zwei|drei|vier|\d+)\s+(?:wochen|tage)|"
    r"(?:zwei|drei|vier|fünf|fuenf|sechs|sieben|acht|neun|zehn|\d+)\s+termine|"
    r"einmal\s+im\s+monat)\b"
)
_ADVICE_SERVICE_CONTEXT = re.compile(
    r"\b(?:für\s+mitglieder|fuer\s+mitglieder|beratungstermin|beratungszentrum|"
    r"schuldnerberatung|insolvenzberatung)\b"
)

_DESTINATION_MARKET_PATTERN = re.compile(
    r"\b(?:abendflohmarkt|antikmarkt|feierabendmarkt|flohmarkt|garagenflohmarkt|hausflohmarkt|"
    r"hofflohmarkt|hof[-\s]?flohmarkt|jahrmarkt|kunstmarkt|nachbarschaftsmarkt|"
    r"nachtflohmarkt|spezialmarkt|stadtflohmarkt|stadtteilmarkt|straßenflohmarkt|strassenflohmarkt|"
    r"trödelmarkt|troedelmarkt|krammarkt|viehmarkt|weihnachtsmarkt)\b",
    re.IGNORECASE,
)
_DESTINATION_MARKET_EVENT_TERMS = frozenset({
    "festival", "kirmes", "stadtteilfest", "strassenfest", "straßenfest", "street food",
})
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
_POSTPONED_PATTERN = re.compile(
    r"\b(?:verschoben|verlegt)\b|\bneuer\s+termin\b",
    re.IGNORECASE,
)
_LANGUAGE_NAME = re.compile(r"\b(?:italienisch|französisch)\b")
_LANGUAGE_COURSE_CONTEXT = re.compile(
    r"\b(?:anfänger|anfaenger|fortgeschrittene|kurs|lernen|sprachunterricht|unterricht|[abc][12])\b"
)
_SEARCH_DATE_SIGNAL = re.compile(
    r"\b(20\d{2}|\d{1,2}\.\d{1,2}\.|\d{1,2}\s*(?:jan|feb|mär|mae|apr|mai|jun|jul|aug|sep|okt|nov|dez)|"
    r"montag|dienstag|mittwoch|donnerstag|freitag|samstag|sonntag|wochenende|heute|morgen|am\s+\d)",
    re.IGNORECASE,
)


def _first(terms: frozenset[str], text: str) -> str:
    return next((term for term in terms if term in text), "")


def _plain_text(value: str) -> str:
    value = unescape(value or "")
    value = re.sub(r"<!--.*?-->", " ", value, flags=re.S)
    value = re.sub(r"<script.*?</script>|<style.*?</style>", " ", value, flags=re.S | re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", value).strip().lower()


def _is_destination_market(text: str) -> bool:
    normalized = _plain_text(text)
    return bool(_DESTINATION_MARKET_PATTERN.search(normalized)) or any(
        term in normalized for term in _DESTINATION_MARKET_EVENT_TERMS
    )


@dataclass(frozen=True, slots=True)
class EventText:
    event: Mapping[str, Any]
    title: str
    description: str
    venue: str
    link: str
    category: str
    text: str
    content: str
    title_description: str
    destination_market: bool

    @classmethod
    def from_event(cls, event: Mapping[str, Any]) -> "EventText":
        title = str(event.get("title") or "").lower()
        description = str(event.get("description") or "").lower()
        venue = str(event.get("venue") or "").lower()
        link = str(event.get("link") or "").lower()
        category = str(event.get("category") or "").lower()
        content = f"{title} {description} {category}"
        return cls(
            event, title, description, venue, link, category,
            f"{title} {description} {venue} {link}", content,
            f"{title} {description}", _is_destination_market(content),
        )


def _contains(terms: frozenset[str], field: str) -> Predicate:
    def predicate(context: EventText) -> tuple[str, ...] | None:
        matched = _first(terms, getattr(context, field))
        return (matched,) if matched else None
    return predicate


def _partisan(context: EventText) -> tuple[str, ...] | None:
    for term in ("grüne jugend", "gruene jugend"):
        if term in context.text:
            return (term,)
    return None


def _cancelled(context: EventText) -> tuple[str, ...] | None:
    combined = f"{context.title} {context.description}"
    if _POSTPONED_PATTERN.search(combined):
        return None
    is_cancelled = bool(
        _CANCELLED_TITLE_PATTERN.search(context.title)
        or _CANCELLED_CONTEXT_PATTERN.search(combined)
    )
    if is_cancelled and context.event.get("status") not in {"cancelled", "postponed"}:
        return ()
    return None


def _governance(context: EventText) -> tuple[str, ...] | None:
    governance_text = f"{context.title} {context.category} {context.venue} {context.link}"
    matched = _first(GOVERNANCE_TERMS, governance_text)
    cultural = _first(CULTURAL_EVENT_TERMS, context.title_description)
    if "cinema-special" not in context.category and matched and not context.destination_market and not cultural:
        return (matched,)
    return None


def _routine_meetup(context: EventText) -> tuple[str, ...] | None:
    # Generic recurrence prose is weak evidence: it often comes from a venue's
    # navigation, biography, or series boilerplate rather than this occurrence.
    matched = _first(_STRONG_ROUTINE_PHRASE_TERMS, context.title)
    if not matched:
        recurrence = _EVENT_RECURRENCE_CONTEXT.search(context.title_description)
        recurring = recurrence.group(0) if recurrence else ""
        recurring = recurring or (
            "recurring source path" if "/wiederkehrende-termine/" in context.link else ""
        )
        # The meetup shape must belong to the occurrence title. Generic prose
        # such as "der Verein trifft sich regelmäßig" must not turn an
        # otherwise one-off public action into a routine service.
        routine_shape = _ROUTINE_MEETUP_CONTEXT.search(context.title)
        broad_recurring_listing = (
            "/wiederkehrende-termine/" in context.link
            and context.category == "begegnung"
        )
        if recurring and (routine_shape or broad_recurring_listing):
            matched = recurring
    if (
        "cinema-special" not in context.category
        and matched
        and not context.destination_market
        and not _first(RECURRING_DESTINATION_TERMS, context.title)
        and not _first(CULTURAL_EVENT_TERMS, context.title_description)
    ):
        return (matched,)
    return None


def _routine_market(context: EventText) -> tuple[str, ...] | None:
    matched = _first(ROUTINE_MARKET_DROP_TERMS, context.content)
    if not matched:
        recurrence = _EVENT_RECURRENCE_CONTEXT.search(context.title_description)
        if recurrence and re.search(
            r"\bmarkt(?:-shop)?\b|\böffnungszeiten\b|\boeffnungszeiten\b",
            context.title_description,
        ):
            matched = recurrence.group(0)
    return (matched,) if matched and not context.destination_market else None


def _routine_course(context: EventText) -> tuple[str, ...] | None:
    matched = _first(_STRONG_ROUTINE_COURSE_TERMS, context.title)
    if matched:
        return (matched,)

    # "Beratung" and "Fortgeschrittene" describe many public events without
    # making them standing services or courses. Require the weak word in the
    # event title plus separate, event-scoped service or recurrence evidence.
    recurrence = _EVENT_RECURRENCE_CONTEXT.search(context.title_description)
    advice_service = _ADVICE_SERVICE_CONTEXT.search(context.title_description)
    if "beratung" in context.title and (recurrence or advice_service):
        evidence = recurrence or advice_service
        return "beratung", evidence.group(0)

    title_course = _first(COURSE_CONTEXT_TERMS, context.title)
    if title_course and recurrence:
        if "fortgeschrittene" in context.title:
            return "fortgeschrittene", title_course, recurrence.group(0)
        return title_course, recurrence.group(0)
    return None


def _language_course(context: EventText) -> tuple[str, ...] | None:
    language_title = _LANGUAGE_NAME.search(context.title)
    course_title = _LANGUAGE_COURSE_CONTEXT.search(context.title)
    language = language_title or _LANGUAGE_NAME.search(context.description)
    course = course_title or _LANGUAGE_COURSE_CONTEXT.search(context.description)
    if language and course and (language_title or course_title):
        return language.group(0), course.group(0)
    return None


def _recurring_course(context: EventText) -> tuple[str, ...] | None:
    recurring = _first(RECURRING_COURSE_MARKERS, context.title_description)
    course = _first(COURSE_CONTEXT_TERMS, context.title)
    if recurring and course and not _first(CULTURAL_EVENT_TERMS, context.content):
        return recurring, course
    return None


def _search_static(context: EventText) -> tuple[str, ...] | None:
    if context.event.get("source") not in {"Exa Search", "Grok Search"}:
        return None
    matched = _first(SEARCH_STATIC_PAGE_TERMS, context.text)
    explicit = _first(EXPLICIT_LOCAL_EVENT_TERMS, context.text)
    return (matched,) if matched and not explicit else None


def _search_evidence(context: EventText) -> tuple[str, ...] | None:
    if context.event.get("source") not in {"Exa Search", "Grok Search"}:
        return None
    explicit = bool(_first(EXPLICIT_LOCAL_EVENT_TERMS, context.text))
    strong = context.destination_market or bool(_first(SEARCH_STRONG_SIGNALS, context.text))
    date_signal = bool(_SEARCH_DATE_SIGNAL.search(context.text))
    return () if not strong or (not date_signal and not explicit) else None


@dataclass(frozen=True, slots=True)
class Rule:
    rule_id: str
    reason: str
    predicate: Predicate


RULES = (
    Rule("metadata.navigation-page", "navigation or legal page is not an event", _contains(JUNK_TITLE_TERMS, "title")),
    Rule("metadata.directory-link", "link points to navigation, a directory, or a generic listing", _contains(JUNK_LINK_TERMS, "link")),
    Rule("civic.partisan-organization", "partisan organizational activity is outside the editorial scope", _partisan),
    Rule("editorial.private-graduation", "private graduation celebration is not a public destination event", _contains(PRIVATE_EVENT_TERMS, "text")),
    Rule("schedule.cancelled", "cancelled occurrence must not be published as scheduled", _cancelled),
    Rule("editorial.static-attraction", "static attraction page is not a dated destination event", _contains(STATIC_ATTRACTION_TERMS, "text")),
    Rule("civic.governance", "routine political or administrative meeting is outside the editorial scope", _governance),
    Rule("civic.routine-meetup", "recurring low-signal meetup is not a destination event", _routine_meetup),
    Rule("civic.routine-market", "routine produce market is civic infrastructure, not a special market event", _routine_market),
    Rule("civic.course", "routine course or support offer is not a destination event", _routine_course),
    Rule("civic.language-course", "recurring language instruction is not a destination event", _language_course),
    Rule("civic.recurring-course", "recurring course series is not a destination event", _recurring_course),
    Rule("search.static-page", "search result describes a static page rather than a dated event", _search_static),
    Rule("search.insufficient-event-evidence", "search result lacks enough topical and dated event evidence", _search_evidence),
)


def legacy_junk_decision(event: Mapping[str, Any]) -> Decision | None:
    """Evaluate the ordered compatibility policy without importing core or quality."""
    context = EventText.from_event(event)
    for rule in RULES:
        if (matched_terms := rule.predicate(context)) is not None:
            return rule.rule_id, rule.reason, matched_terms
    return None
