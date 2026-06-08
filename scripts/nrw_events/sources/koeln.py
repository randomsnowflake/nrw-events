"""
Köln Open Data — official events API (structured JSON).

Reads:  stadt-koeln.de/externe-dienste/open-data/events-od.php (JSON)
Yields: city-wide Köln events with real coordinates. The workhorse source.
"""

import json
import re
from html import unescape

from .. import common


def fetch() -> list:
    source = "Köln Open Data"
    try:
        url = ("http://www.stadt-koeln.de/externe-dienste/open-data/events-od.php"
               f"?out=json&ndays={common.DAYS_AHEAD}")
        data = json.loads(common.fetch_url(url, timeout=20))
        events = []
        for item in data.get("items", []):
            title = (item.get("title") or "").strip()
            if not title:
                continue

            begin = item.get("beginndatum", "")
            end = item.get("endedatum", "")
            begin_dt = common.parse_date(begin) if begin else None
            end_dt = common.parse_date(end) if end else begin_dt
            if begin_dt and end_dt:
                if end_dt < common.TODAY or begin_dt > common.END_DATE:
                    continue
            elif begin_dt and begin_dt > common.END_DATE:
                continue

            lat = float(item.get("latitude", 0) or 0)
            lon = float(item.get("longitude", 0) or 0)
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
                price = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", price)).strip()
            time_str = item.get("uhrzeit", "")
            district = item.get("stadtteil", "")

            full_text = f"{title} {desc} {venue}"
            events.append({
                "title": unescape(title),
                "date": begin,
                "time": unescape(re.sub(r"<[^>]+>", "", time_str).strip()) if time_str else "",
                "venue": unescape(venue),
                "city": "Köln" + (f" ({district})" if district else ""),
                "description": unescape(desc),
                "price": unescape(price) if price else "",
                "link": item.get("link", ""),
                "distance_km": round(km, 1),
                "score": round(common.distance_score(km) * common.category_score(full_text), 2),
                "source": source,
                "category": "",
            })
        return events
    except Exception as e:
        common.log_source_error(source, e)
        return []
