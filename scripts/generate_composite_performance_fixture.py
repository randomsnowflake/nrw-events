"""Exercise the real SiteKit and IONAS4 adapters with synthetic response bodies."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import urljoin

from nrw_events.sources import regional_ionas4, regional_sitekit


def generate(directory: Path, per_calendar: int = 10) -> Path:
    if per_calendar < 1:
        raise ValueError("per-calendar count must be positive")
    if directory.exists() and any(directory.iterdir()):
        raise ValueError("composite fixture directory must be empty")
    directory.mkdir(parents=True, exist_ok=True)
    responses: dict[str, dict] = {}
    seeds = []

    def response(url: str, body: str, content_type: str) -> str:
        filename = f"response-{len(responses):04d}.txt"
        (directory / filename).write_text(body, encoding="utf-8")
        responses[url] = {"file": filename, "content_type": content_type}
        return filename

    def detail(title: str, city: str, start: str) -> str:
        description = (
            "Ein synthetisches Jazzkonzert für den Leistungstest. "
            "Das Quartett spielt Musik und beantwortet anschließend Fragen aus dem Publikum. "
            "Der Eintritt ist kostenlos."
        )
        schema = json.dumps({
            "@context": "https://schema.org", "@type": "Event", "name": title,
            "startDate": start, "description": description,
            "location": {"@type": "Place", "name": f"Kulturhaus {city}",
                         "address": {"@type": "PostalAddress", "addressLocality": city}},
        }, ensure_ascii=False)
        return (
            f'<script type="application/ld+json">{schema}</script>'
            f'<div class="SP-Paragraph"><p>{description}</p></div>'
            f'<div class="tvm-event--description">{description}</div>'
            f'<div class="tvm-event--location">Kulturhaus {city}</div>'
        )

    for city, _source_id, url, _trust in regional_sitekit._CALENDARS:
        cards = []
        for index in range(per_calendar):
            day = 5 + index % 20
            title = f"Synthetisches Jazzkonzert {city} {index}"
            link = urljoin(url, f"/synthetic-performance-event-{index}.php")
            cards.append(
                '<article class="SP-Teaser">'
                f'<a class="SP-Teaser__inner" href="{link}">'
                f'<span class="SP-Scheduling__date">{day:02d}.09.2026</span>'
                '<span class="SP-Scheduling__time">18:00 Uhr</span>'
                f'<h4 class="SP-Teaser__headline">{title}</h4></a></article>'
            )
            filename = response(link, detail(title, city, f"2026-09-{day:02d}T18:00:00"), "text/html; charset=utf-8")
            if index % 2 == 0:
                seeds.append({"namespace": "regional-sitekit-detail", "url": link, "file": filename})
        response(url, "".join(cards), "text/html; charset=utf-8")

    for city, url, calendar_url, _trust in regional_ionas4._CALENDARS:
        items = []
        for index in range(per_calendar):
            day = 5 + index % 20
            title = f"Synthetisches Jazzkonzert {city} {index}"
            item = {"id": f"synthetic-{index}", "title": title,
                    "start": f"2026-09-{day:02d}T18:00:00", "end": f"2026-09-{day:02d}T20:00:00",
                    "allDay": False, "location": {"name": f"Kulturhaus {city}"},
                    "category": {"name": "Musik"}}
            link = regional_ionas4._detail_url(calendar_url, item)
            item["website"] = link
            items.append(item)
            filename = response(link, detail(title, city, item["start"]), "text/html; charset=utf-8")
            if index % 2 == 0:
                seeds.append({"namespace": f"ionas4-{city}", "url": link, "file": filename})
        response(url, json.dumps(items, ensure_ascii=False), "application/json; charset=utf-8")
    manifest = {
        "date": "2026-09-03T12:00:00", "days_ahead": 90,
        "sources": ["SiteKit regional", "ionas4 regional"],
        "responses": responses, "detail_cache_seed": seeds,
    }
    path = directory / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--per-calendar", type=int, default=10)
    args = parser.parse_args()
    print(generate(args.directory, args.per_calendar))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
