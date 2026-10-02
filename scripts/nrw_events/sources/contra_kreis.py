"""Official performance calendar of the Contra-Kreis-Theater Bonn.

The ``/termine/`` page loads each month through WordPress ``admin-ajax.php``
(``action=cal``, ``data=YYYY-MM``); the fragment lists every day with play,
start time, cast and a short synopsis.
"""

import re
from datetime import datetime

from .. import event_builder, http, run_state, text
from . import regional_common as rc

_SOURCE = "Contra-Kreis-Theater"
_SOURCE_ID = "contra-kreis-theater"
_AJAX_URL = "https://www.contra-kreis-theater.de/wp-admin/admin-ajax.php"
_CATEGORY = "theater bühne komödie schauspiel"
_TRUST = 1.0
_VENUE = "Contra-Kreis-Theater, Am Hof 3-5, 53113 Bonn"


def _months() -> list[datetime]:
    window = run_state.runtime_window()
    current = window.start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    months = []
    while current <= window.end:
        months.append(current)
        current = current.replace(year=current.year + current.month // 12, month=current.month % 12 + 1)
    return months


def events_from_month(html: str) -> list[dict]:
    events = []
    for day in html.split('<div class="tag_wrap">')[1:]:
        date_value = rc.parse_dt(rc.first_group(r'<span class="datum">(.*?)</span>', day))
        link = rc.first_group(r'<div class="termine-meta-stueck[^"]*"[^>]*>.*?<a href="([^"]+)"', day)
        if not date_value or not link:
            continue
        title = rc.first_group_clean(r'<div class="termine-meta-stueck[^"]*"[^>]*>.*?<a [^>]*>(.*?)</a>', day)
        time_text = rc.time_text(rc.first_group_clean(r'<span class="vorstellungstart">(.*?)</span>', day))
        badge = rc.first_group_clean(r'<div class="cal_typ_[a-z_]+">(.*?)</div>', day)
        synopsis = rc.first_group_clean(r'<div class="tm-beschreibung[^"]*">.*?<div class="span7">(.*?)</div>', day)
        if not re.search(r"\w", synopsis):  # unannounced plays carry a bare "..."
            synopsis = ""
        cast = rc.first_group_clean(r'<div class="span8 tm-items">(.*?)</div>', day)
        description = " ".join(part for part in (
            f"{badge}." if badge else "",
            synopsis,
            f"Besetzung: {cast}." if cast else "",
        ) if part)
        event = event_builder.make_event(
            title, rc.with_time(date_value, time_text), None, _VENUE, "Bonn",
            text.concise_description(description), link, _SOURCE, _CATEGORY, _TRUST, time_text,
            source_id=_SOURCE_ID,
            default_category_key="stage",
            category_locked=True,
        )
        if event:
            events.append(event)
    return rc.dedupe_occurrences(events)


def fetch() -> list[dict]:
    events: list[dict] = []
    for month in _months():
        try:
            html = http.post_form_text(_AJAX_URL, {"action": "cal", "data": f"{month:%Y-%m}"}, timeout=25)
            events.extend(events_from_month(html))
        except Exception as exc:  # noqa: PERF203 - one failed month must not drop the others
            run_state.log_source_error(f"{_SOURCE} {month:%Y-%m}", exc, source_id=_SOURCE_ID)
    return events
