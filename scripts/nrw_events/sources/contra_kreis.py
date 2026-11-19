"""Official performance calendar embedded in Contra-Kreis-Theater's /termine/ page."""

import json
import re
from datetime import datetime

from .. import event_builder, run_state, text
from ..models import RawEvent
from . import regional_common as rc

_SOURCE = "Contra-Kreis-Theater"
_SOURCE_ID = "contra-kreis-theater"
_URL = "https://www.contra-kreis-theater.de/termine/"
_VENUE = "Contra-Kreis-Theater, Am Hof 3-5, 53113 Bonn"
_CALENDAR = re.compile(r'<script\b[^>]*\bdata-ckt-cal-data\s*>(.*?)</script>', re.S)
_CLOCK = re.compile(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]')


def events_from_calendar(document: str) -> list[RawEvent]:
    match = _CALENDAR.search(document)
    if not match:
        raise ValueError("Contra-Kreis calendar payload missing")
    shows = json.loads(match[1])["shows"]
    if not isinstance(shows, dict) or not shows:
        raise ValueError("Contra-Kreis calendar shows missing")
    window = run_state.runtime_window()
    first, last = f"{window.start:%Y-%m}", f"{window.end:%Y-%m}"
    events = []
    for month, days in shows.items():
        if not first <= month <= last or days == []:  # Unannounced months use empty arrays.
            continue
        for day, performances in days.items():
            date = datetime.strptime(day, "%Y-%m-%d")
            if date.strftime("%Y-%m") != month or not isinstance(performances, list):
                raise ValueError("Invalid Contra-Kreis calendar day")
            for show in performances:
                status = show["status"]
                if status not in ("normal", "ausverkauft", "faellt_aus"):
                    raise ValueError("Unknown Contra-Kreis show status")
                title, link = rc.clean(show["title"]), show["url"]
                if not title or not link.startswith("https://www.contra-kreis-theater.de/"):
                    raise ValueError("Invalid Contra-Kreis show identity")
                times = show.get("times") or [show["time"]]
                if not isinstance(times, list) or any(not _CLOCK.fullmatch(clock) for clock in times):
                    raise ValueError("Invalid Contra-Kreis show time")
                synopsis, cast = rc.clean(show.get("excerpt", "")), rc.clean(show.get("cast", ""))
                description = " ".join(part for part in (
                    "Der Termin fällt aus." if status == "faellt_aus" else "",
                    "Premiere." if show.get("is_premiere") else "Letzte Vorstellung." if show.get("is_finale") else "",
                    synopsis if re.search(r"\w", synopsis) else "",
                    f"Besetzung: {cast}." if cast else "",
                ) if part)
                for clock in dict.fromkeys(times):
                    event = event_builder.make_event(
                        title, rc.with_time(date, clock), None, _VENUE, "Bonn",
                        text.concise_description(description), link, _SOURCE, "theater bühne komödie schauspiel",
                        1.0, clock, source_id=_SOURCE_ID, default_category_key="stage", category_locked=True,
                    )
                    if event:
                        # Plot mentions of children must not demote this first-party stage programme.
                        event["score"] = max(float(event.get("score") or 0), 0.45)
                        if status == "ausverkauft":
                            event["availability"] = "SoldOut"
                        events.append(event)
    return rc.dedupe_occurrences(events)


def fetch() -> list[dict]:
    return rc.fetch_html_events(_SOURCE, _URL, events_from_calendar, source_id=_SOURCE_ID)
