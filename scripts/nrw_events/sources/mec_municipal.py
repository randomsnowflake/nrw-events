"""Market long tail from municipal Modern Events Calendar (MEC) sites.

Hennef and Sankt Augustin both run WordPress with Modern Events Calendar, which
exposes two useful endpoints:

* ``/wp-json/wp/v2/mec-events?mec_category=<id>`` lists a whole topical category,
  reaching far beyond the handful of entries the public calendar page renders.
* ``/?method=ical&id=<post id>`` returns a valid single-event VCALENDAR with an
  authoritative ``DTSTART``.

That combination is what makes this source worth having. The Hennef JSON-LD page
already registered as a ``SourceSpec`` yields only the current page window — twelve
events, whose sole market entry is a Wochenmarkt — while the category listing holds
the Hof-, Garagen-, Dorf- and Gassenflohmärkte that sit months out.

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
from dataclasses import dataclass

from .. import common
from . import regional_common as rc


@dataclass(frozen=True, slots=True)
class MecSite:
    source: str
    source_id: str
    base_url: str
    city: str
    category_ids: tuple[int, ...]
    trust: float = 0.95


# Category ids verified against each site's own taxonomy.
SITES = (
    MecSite(
        "Hennef Märkte",
        "hennef-maerkte",
        "https://www.hennef.de",
        "Hennef",
        (74, 240),
    ),
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

# Only second-hand market formats. Produce and Christmas markets share the same
# municipal categories and are excluded here as well as by the junk filter.
_MARKET_TITLE = re.compile(
    r"floh|trödel|troedel|garagen|hofmarkt|hof-|antik|basar|börse|boerse|"
    r"kram|second.?hand",
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
    return f"{site.base_url}/wp-json/wp/v2/mec-events?mec_category={category_id}&per_page={_PER_PAGE}&page={page}"


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


def _cached_calendar_fetcher(url: str, **kwargs) -> str:
    kwargs.pop("timeout", None)
    return common.fetch_detail_url(url, cache_namespace=_CACHE_NAMESPACE, timeout=20, **kwargs)


def _city_resolver(site: MecSite):
    def resolve(location: str) -> str:
        return rc.city_from_text(location or "", site.city) or site.city

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
                    city_resolver=_city_resolver(site),
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


def fetch() -> list:
    events = []
    for site in SITES:
        try:
            events.extend(events_for_site(site))
        except Exception as exc:
            common.log_source_error(
                site.source,
                exc,
                source_id=site.source_id,
            )
    return events
