"""Generate synthetic iCal inputs for repeatable scale and memory experiments."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path


def generate(directory: Path, count: int) -> Path:
    if count < 1:
        raise ValueError("record count must be positive")
    directory.mkdir(parents=True, exist_ok=True)
    manifests = (directory / "manifest.json", directory / "manifest-cache-off.json")
    calendars = (directory / "a.ics", directory / "b.ics")
    if any(path.exists() for path in (*manifests, *calendars)):
        raise ValueError("fixture output files already exist")
    blocks: list[list[str]] = [[], []]
    base = datetime(2026, 9, 3, 18)
    formats = (
        ("Jazzkonzert", "Live-Musik mit einem Jazzquartett und anschließender Diskussion."),
        ("Workshop Malerei", "Gemeinsam gestalten die Teilnehmenden Bilder und lernen praktische Techniken."),
        ("Vortrag Stadtgeschichte", "Eine öffentliche Einführung in die Geschichte der Region."),
        ("Kunstausstellung", "Die Ausstellung zeigt Malerei und Skulpturen zeitgenössischer Kunst."),
    )
    for index in range(count):
        # Four fifths lie outside the public window but still exercise the
        # existing announcement and source-health paths. Dates are deterministic.
        start = base + timedelta(days=(index % 365) - 300)
        end = start + timedelta(hours=2)
        title, description = formats[index % len(formats)]
        extra = ""
        if index % 97 == 0:
            extra = "RRULE:FREQ=WEEKLY;COUNT=8\n"
        if index % 101 == 0:
            extra += "STATUS:CANCELLED\n"
        if index % 103 == 0:
            extra += f"RDATE:{(start + timedelta(days=2)):%Y%m%dT%H%M%S}\n"
        if index % 107 == 0:
            extra += f"EXDATE:{start:%Y%m%dT%H%M%S}\n"
        blocks[index % 2].append(
            f"BEGIN:VEVENT\nUID:synthetic-{index}\n"
            f"DTSTART:{start:%Y%m%dT%H%M%S}\nDTEND:{end:%Y%m%dT%H%M%S}\n"
            f"SUMMARY:{title} Reihe {index % 200}\nLOCATION:Brotfabrik Bonn\n"
            f"DESCRIPTION:{description} Dieser Datensatz dient ausschließlich dem synthetischen Leistungstest.\n"
            f"CATEGORIES:Kultur\nURL:https://calendar-{index % 2}.example.test/event/{index}\n"
            f"{extra}END:VEVENT\n"
        )
    for path, events in zip(calendars, blocks, strict=True):
        path.write_text("BEGIN:VCALENDAR\nVERSION:2.0\n" + "".join(events) + "END:VCALENDAR\n", encoding="utf-8")
    manifest = {
        "date": "2026-09-03T12:00:00", "days_ahead": 90,
        "sources": [
            {"name": f"Synthetic Calendar {index}", "url": f"https://calendar-{index}.example.test/events.ics", "city": "Bonn"}
            for index in range(2)
        ],
        "responses": {
            f"https://calendar-{index}.example.test/events.ics": {"file": path.name, "content_type": "text/calendar; charset=utf-8"}
            for index, path in enumerate(calendars)
        },
    }
    manifests[0].write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    manifests[1].write_text(json.dumps({**manifest, "taxonomy_cache": False}, indent=2) + "\n", encoding="utf-8")
    return manifests[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--records", type=int, default=10_000)
    args = parser.parse_args()
    print(generate(args.directory, args.records))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
