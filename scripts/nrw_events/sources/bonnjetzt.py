"""
Bonn.jetzt — Bonn's digital/community scene (server-rendered event cards).

Reads:  bonn.jetzt homepage <article itemtype="schema.org/Event"> cards
Yields: local community / digital / weekend events the big feeds miss.
"""

import re
from html import unescape

from .. import common


def fetch() -> list:
    source = "Bonn.jetzt"
    try:
        html = common.fetch_url("https://bonn.jetzt/")
        events = []
        articles = re.findall(
            r'<article[^>]*itemtype="https://schema.org/Event".*?</article>', html, re.DOTALL)
        for article in articles:
            title_match = re.search(r'<h2[^>]*class="title p-name"[^>]*>(.*?)</h2>', article, re.DOTALL)
            title = unescape(re.sub(r'<[^>]+>', '', title_match.group(1)).strip()) if title_match else ''
            if not title:
                continue

            link_match = re.search(r'<a href="(/event/[^"]+)"[^>]*itemprop="url">', article)
            start_match = re.search(
                r'<time[^>]*datetime="([^"]+)"[^>]*itemprop="startDate"[^>]*>(.*?)</time>', article, re.DOTALL)
            end_match = re.search(r'<time[^>]*itemprop="endDate"[^>]*content="([^"]+)"', article)
            venue_match = re.search(r'<span itemprop="name">(.*?)</span>', article, re.DOTALL)
            addr_match = re.search(r'<div itemprop="address"[^>]*>(.*?)</div>', article, re.DOTALL)
            tags = re.findall(r'<span class="v-chip__content">(.*?)</span>', article, re.DOTALL)

            start_raw = start_match.group(1).strip() if start_match else ''
            start_text = unescape(re.sub(r'<[^>]+>', ' ', start_match.group(2)).strip()) if start_match else ''
            end_raw = end_match.group(1).strip() if end_match else ''
            if start_raw and not common.in_date_range(start_raw):
                if not (end_raw and common.in_date_range(end_raw)):
                    continue

            venue = unescape(re.sub(r'<[^>]+>', '', venue_match.group(1)).strip()) if venue_match else ''
            address = unescape(re.sub(r'<[^>]+>', '', addr_match.group(1)).strip()) if addr_match else ''
            city = common.guess_city_from_text(address or venue or title) or 'bonn'
            km = common.haversine(common.BONN_LAT, common.BONN_LON, *common.coords_for_city(city))
            if km > common.MAX_RADIUS_KM:
                continue

            tag_text = ' '.join(unescape(t).strip() for t in tags)
            full_text = f"{title} {venue} {address} {tag_text}"
            events.append({
                "title": title, "date": start_raw, "time": start_text,
                "venue": venue, "city": city.title() if city else "Bonn",
                "description": tag_text, "price": "",
                "link": f"https://bonn.jetzt{link_match.group(1)}" if link_match else "https://bonn.jetzt/",
                "distance_km": round(km, 1),
                "score": round(common.distance_score(km) * common.category_score(full_text), 2),
                "source": source, "category": ", ".join(tags[:3]),
            })
        return events
    except Exception as e:
        common.log_source_error(source, e)
        return []
