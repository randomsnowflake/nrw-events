"""
Songkick — concerts for the Bonn metro area.

Reads:  songkick.com/metro-areas/28447-germany-bonn/<year>
Yields: concerts via schema.org MusicEvent JSON-LD, with an HTML link fallback.
"""

import json
import re
from html import unescape

from .. import common


def fetch() -> list:
    source = "Songkick"
    try:
        url = f"https://www.songkick.com/metro-areas/28447-germany-bonn/{common.TODAY.year}"
        html = common.fetch_url(url)
        events = []
        seen = set()

        for ld in re.findall(r'application/ld\+json">(.*?)</script>', html, re.DOTALL):
            try:
                data = json.loads(ld)
            except (json.JSONDecodeError, AttributeError):
                continue
            for item in (data if isinstance(data, list) else [data]):
                if item.get("@type") != "MusicEvent":
                    continue
                name = item.get("name", "")
                start = item.get("startDate", "")
                event_date = common.parse_date(start[:10]) if start else None
                if event_date and not (common.TODAY <= event_date <= common.END_DATE):
                    continue
                if name in seen:
                    continue
                seen.add(name)

                venue_name = (item.get("location", {}) or {}).get("name", "")
                city_guess = common.guess_city_from_text(venue_name) or "bonn"
                km = common.haversine(common.BONN_LAT, common.BONN_LON, *common.coords_for_city(city_guess))
                if km > common.MAX_RADIUS_KM:
                    continue

                display = name
                if venue_name and f"@ {venue_name}" not in name and venue_name not in name:
                    display = f"{name} @ {venue_name}"
                events.append({
                    "title": display,
                    "date": start[:10] if start else "",
                    "time": start[11:16] if len(start) > 11 else "",
                    "venue": venue_name, "city": city_guess.title(),
                    "description": "", "price": "",
                    "link": item.get("url", ""),
                    "distance_km": round(km, 1),
                    "score": round(common.distance_score(km) * 1.5, 2),
                    "source": source, "category": "concert",
                })

        if len(events) < 3:
            for href, text in re.findall(r'href="(/concerts/[^"]+)"[^>]*>(.*?)</a>', html, re.DOTALL):
                clean = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()
                if not clean or clean in seen:
                    continue
                seen.add(clean)
                events.append({
                    "title": unescape(clean), "date": "", "time": "", "venue": "",
                    "city": "Bonn area", "description": "", "price": "",
                    "link": f"https://www.songkick.com{href}",
                    "distance_km": 0,
                    "score": round(common.distance_score(0) * 1.5, 2),
                    "source": source, "category": "concert",
                })
        return events
    except Exception as e:
        common.log_source_error(source, e)
        return []
