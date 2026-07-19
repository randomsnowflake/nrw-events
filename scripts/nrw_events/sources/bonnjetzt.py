"""
Bonn.jetzt — Bonn's digital/community scene (server-rendered event cards).

Reads:  bonn.jetzt homepage <article itemtype="schema.org/Event"> cards
Yields: local community / digital / weekend events the big feeds miss.
"""

import re

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
            title = common.clean_html(title_match.group(1)) if title_match else ""
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
            start_text = common.clean_html(start_match.group(2)) if start_match else ""
            end_raw = end_match.group(1).strip() if end_match else ''

            venue = common.clean_html(venue_match.group(1)) if venue_match else ""
            address = common.clean_html(addr_match.group(1)) if addr_match else ""
            city = common.guess_city_from_text(address or venue or title) or 'bonn'

            tag_text = " ".join(common.clean_html(tag) for tag in tags)
            start_dt = common.parse_date(start_raw)
            end_dt = common.parse_date(end_raw) or start_dt
            event = common.make_event(
                title, start_dt, end_dt, venue, city, tag_text,
                common.urllib.parse.urljoin("https://bonn.jetzt/", link_match.group(1))
                if link_match else "https://bonn.jetzt/",
                source, ", ".join(common.clean_html(tag) for tag in tags[:3]),
                time_text=start_text,
            )
            if event:
                events.append(event)
        return events
    except Exception as e:
        common.log_source_error(source, e)
        return []
