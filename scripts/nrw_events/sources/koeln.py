"""
Köln Open Data — official events API (structured JSON).

Reads:  stadt-koeln.de/externe-dienste/open-data/events-od.php (JSON)
Yields: city-wide Köln events with real coordinates. The workhorse source.
"""

import json
import re
from html import unescape

from .. import common


def _clean_price(value: str) -> str:
    price = common.clean_html(value)
    if len(price) > 160:
        return price[:159].rstrip() + "…"
    return price


def fetch() -> list:
    source = "Köln Open Data"
    try:
        url = ("https://www.stadt-koeln.de/externe-dienste/open-data/events-od.php"
               f"?out=json&ndays={common.DAYS_AHEAD}")
        data = json.loads(common.fetch_url(
            url,
            timeout=20,
            accept="application/json,*/*;q=0.8",
            sec_fetch_mode="cors",
            sec_fetch_dest="empty",
            expected_content_types=("application/json", "text/json"),
        ))
        events = []
        for item in data.get("items", []):
            title = (item.get("title") or "").strip()
            if not title:
                continue

            begin = item.get("beginndatum", "")
            end = item.get("endedatum", "")
            begin_dt = common.parse_date(begin) if begin else None
            end_dt = common.parse_date(end) if end else begin_dt
            if not common.window_contains(begin_dt, end_dt):
                continue

            lat = common.parse_float(item.get("latitude"))
            lon = common.parse_float(item.get("longitude"))
            if lat and lon:
                km = common.haversine(common.BONN_LAT, common.BONN_LON, lat, lon)
            else:
                km = common.haversine(common.BONN_LAT, common.BONN_LON, *common.coords_for_city("köln"))
            if km > common.MAX_RADIUS_KM:
                continue

            venue = item.get("veranstaltungsort", "")
            desc = item.get("description", "")
            price = item.get("preis", "")
            if price:
                price = _clean_price(price)
            time_str = item.get("uhrzeit", "")
            district = item.get("stadtteil", "")

            event = common.make_event(
                unescape(title), begin_dt, end_dt, unescape(venue), "Köln", unescape(desc),
                item.get("link", ""), source, "", time_text=(
                    unescape(re.sub(r"<[^>]+>", "", time_str).strip())[:80] if time_str else ""
                ), coords=(lat, lon) if lat and lon else None,
            )
            if event:
                event["city"] = "Köln" + (f" ({district})" if district else "")
                event["price"] = unescape(price) if price else ""
                events.append(event)
        return events
    except Exception as e:
        common.log_source_error(source, e)
        return []
