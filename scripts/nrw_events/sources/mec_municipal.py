"""Municipal Modern Events Calendar (MEC) sites: Hennef and Sankt Augustin.

Hennef publishes its whole calendar as one feed (``/?mec-ical-feed=1``), so
``fetch_hennef`` reads every event from there; the public calendar page only
renders the next twelve.

Sankt Augustin is read through the market long tail. MEC exposes two useful
endpoints:

* ``/wp-json/wp/v2/mec-events?mec_category=<id>`` lists a whole topical category,
  reaching far beyond the handful of entries the public calendar page renders.
* ``/?method=ical&id=<post id>`` returns a valid single-event VCALENDAR with an
  authoritative ``DTSTART``.

That combination reaches the markets that sit months out, beyond the handful of
entries the public calendar page renders.

Two deliberate constraints:

* **The REST payload carries no event date.** Its ``date`` field is the post's
  publish date. The listing prose usually names a day and month but omits the year
  in about three quarters of entries, and guessing a year would silently move an
  archived market into the future. The per-event calendar is therefore the only
  date source used.
* **One calendar request per event is only acceptable if it is cached.** Candidates
  are narrowed to market formats by title first, and every calendar read goes
  through the persistent TTL cache, so a repeat run inside the TTL costs nothing.
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any

from .. import common
from ..ical import _ical_parse_dt
from . import regional_common as rc


@dataclass(frozen=True, slots=True)
class MecSite:
    source: str
    source_id: str
    base_url: str
    city: str
    category_ids: tuple[int, ...]
    trust: float = 0.95


HENNEF_FEED_URL = "https://www.hennef.de/?mec-ical-feed=1"

# Category ids verified against each site's own taxonomy.
SITES = (
    MecSite(
        "Sankt Augustin Märkte",
        "sankt-augustin-maerkte",
        "https://www.sankt-augustin.de",
        "Sankt Augustin",
        (308,),
    ),
)

_CACHE_NAMESPACE = "mec-municipal"
_PER_PAGE = 100
# Blind pagination guard; reaching it is logged rather than silently truncated.
_MAX_PAGES = 3

# MEC fills these with placeholder hours (08–18) that contradict the opening
# times in their own description, so only their days are used.
_CHRISTMAS_MARKET_TITLE = re.compile(
    r"weihnachtsmarkt|adventsmarkt|nikolausmarkt|hüttenzauber|huettenzauber", re.I
)
# Second-hand and Christmas market formats. Produce markets share the same
# municipal categories and are excluded.
_MARKET_TITLE = re.compile(
    r"floh|trödel|troedel|garagen|hofmarkt|hof-|antik|basar|börse|boerse|"
    r"kram|second.?hand|" + _CHRISTMAS_MARKET_TITLE.pattern,
    re.I,
)
# Checked before the include pattern. "Börse" alone is too broad: a
# Pflanzentauschbörse or Samenbörse is a produce swap, not a second-hand market.
_EXCLUDED_TITLE = re.compile(
    r"wochenmarkt|frischemarkt|bauernmarkt|biomarkt|"
    r"pflanzen|samenbörse|samenboerse|saatgut|tierbörse|tierboerse|"
    r"jobbörse|jobboerse|helferbörse|helferboerse|berufsstarter",
    re.I,
)


def _listing_url(site: MecSite, category_id: int, page: int) -> str:
    return (f"{site.base_url}/wp-json/wp/v2/mec-events"
            f"?mec_category={category_id}&per_page={_PER_PAGE}&page={page}")


def ical_url(site: MecSite, post_id: int) -> str:
    return f"{site.base_url}/?method=ical&id={post_id}"


def _rendered(item: dict, field: str) -> str:
    value = item.get(field)
    if isinstance(value, dict):
        return rc.clean(value.get("rendered", ""))
    return rc.clean(str(value or ""))


def market_candidates(items: list) -> list:
    """Return (post id, title) for entries whose title names a wanted format."""
    candidates = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = _rendered(item, "title")
        post_id = item.get("id")
        if not title or not isinstance(post_id, int):
            continue
        if _EXCLUDED_TITLE.search(title) or not _MARKET_TITLE.search(title):
            continue
        candidates.append((post_id, title))
    return candidates


_VEVENT = re.compile(r"BEGIN:VEVENT.*?END:VEVENT", re.S)


def _whole_days(match: re.Match[str]) -> str:
    block = match.group(0)
    summary = re.search(r"^SUMMARY:(.*)$", block, re.M)
    start = re.search(r"^(DTSTART[^:\r\n]*):(\S+)", block, re.M)
    end = re.search(r"^(DTEND[^:\r\n]*):(\S+)", block, re.M)
    if not (summary and start and end):
        return block
    first = _ical_parse_dt(start.group(2), start.group(1))
    last = _ical_parse_dt(end.group(2), end.group(1))
    if first is None or last is None or last <= first:
        return block
    # MEC also encodes whole days as local midnight timestamps.
    if not (first.time() == last.time() == time.min or _CHRISTMAS_MARKET_TITLE.search(summary.group(1))):
        return block
    # DATE values take an exclusive DTEND; a midnight end already is one.
    after = last.date() if last.time() == time.min else last.date() + timedelta(days=1)
    block = block.replace(start.group(0), f"DTSTART;VALUE=DATE:{first:%Y%m%d}", 1)
    return block.replace(end.group(0), f"DTEND;VALUE=DATE:{after:%Y%m%d}", 1)


def _normalize_calendar(calendar: str) -> str:
    return _VEVENT.sub(_whole_days, calendar)


def _cached_calendar_fetcher(url: str, **kwargs: Any) -> str:
    kwargs.pop("timeout", None)
    return _normalize_calendar(
        common.fetch_detail_url(url, cache_namespace=_CACHE_NAMESPACE, timeout=20, **kwargs)
    )


def _hennef_feed_fetcher(url: str, **kwargs: Any) -> str:
    kwargs.pop("timeout", None)
    return _normalize_calendar(common.fetch_url(url, timeout=40, **kwargs))


def _city_resolver(city: str) -> Callable[[str], str]:
    def resolve(location: str) -> str:
        return rc.city_from_text(location or "", city) or city
    return resolve


def _list_category(site: MecSite, category_id: int) -> list:
    items = []
    for page in range(1, _MAX_PAGES + 1):
        url = _listing_url(site, category_id, page)
        payload = json.loads(common.fetch_url(url, timeout=25))
        if not isinstance(payload, list) or not payload:
            break
        items.extend(payload)
        if len(payload) < _PER_PAGE:
            break
    else:
        common.log_source_error(
            site.source,
            RuntimeError(
                f"category {category_id}: stopped at the {_MAX_PAGES}-page listing "
                "limit; later entries in this category were not read"
            ),
            source_id=site.source_id,
        )
    return items


def events_for_site(site: MecSite) -> list:
    events = []
    for category_id in site.category_ids:
        try:
            items = _list_category(site, category_id)
        except Exception as exc:
            common.log_source_error(
                f"{site.source} (category {category_id})",
                exc,
                source_id=site.source_id,
            )
            continue
        for post_id, title in market_candidates(items):
            url = ical_url(site, post_id)
            try:
                parsed = common.fetch_ical(
                    url,
                    site.source,
                    site.city,
                    trust=site.trust,
                    source_id=site.source_id,
                    city_resolver=_city_resolver(site.city),
                    fetcher=_cached_calendar_fetcher,
                )
            except Exception as exc:
                common.log_source_error(
                    f"{site.source} ({title[:40]})",
                    exc,
                    source_id=site.source_id,
                )
                continue
            events.extend(parsed)
    return rc.dedupe(events)


# A one-off entry running for months is a data-entry slip (an ADFC tour dated
# 27.02.2026–27.02.2027); Hennef's longest real run is its six-week Hüttenzauber.
_MAX_ONE_OFF_DAYS = 90


def _plausible_span(props: dict[str, str], start: datetime, end: datetime) -> bool:
    return bool(props.get("RRULE")) or (end - start).days <= _MAX_ONE_OFF_DAYS


def fetch_hennef() -> list:
    """Every event of Hennef's municipal calendar from its full MEC feed."""
    return common.fetch_ical(
        HENNEF_FEED_URL,
        "Hennef",
        "Hennef",
        "lokal veranstaltung markt kultur outdoor",
        trust=0.95,
        source_id="hennef",
        event_filter=_plausible_span,
        city_resolver=_city_resolver("Hennef"),
        fetcher=_hennef_feed_fetcher,
    )


def fetch() -> list:
    events = []
    for site in SITES:
        try:
            events.extend(events_for_site(site))
        except Exception as exc:  # noqa: PERF203 - municipality sites must fail independently
            common.log_source_error(
                site.source,
                exc,
                source_id=site.source_id,
            )
    return events
