"""Martinszüge from the city of Brühl's annual collective calendar entry.

The city publishes every procession on one page ("Die Umzüge im Überblick":
date, time span, organiser). Start points and routes exist only in the linked
flyer PDF, so they come from a reviewed copy of it. The page stays the authority
for which processions exist and when they run.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import NamedTuple

from ..event_builder import make_event
from ..text import clean_html
from . import regional_common as rc

URL = (
    "https://www.bruehl.de/veranstaltungskalender/veranstaltungen/hauptkalender/"
    "wiederholende-veranstaltungen/St.Martins-Umzuege-alle.php"
)
SOURCE = "Stadt Brühl Martinszüge"
SOURCE_ID = "bruehl-martinszuege"
_OVERVIEW_RE = re.compile(r'id="die-umzuege-im-ueberblick".*?</section>', re.I | re.S)
_YEAR_RE = re.compile(r'SP-Scheduling__date">\s*\d{1,2}\.\d{1,2}\.(20\d{2})')
_DAY_RE = re.compile(r"^\w+\.,\s*(\d{1,2})\.(\d{1,2})\.\s*:\s*(.+)$")
# The lookahead keeps "(Kita Auf der Pehle + Kita Lessingstraße)" in one item.
_ITEM_SPLIT_RE = re.compile(r"\s*\+\s*(?=\d{1,2}:\d{2})")
_ITEM_RE = re.compile(r"^(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*h,\s*(.+)$")


class _Procession(NamedTuple):
    needle: str
    title: str
    area: str
    organizer: str
    start: str
    route: str


# Flyer "Dä hellije Zinter Mätes", Stand Oktober 2026 (/veranstaltungen/2026_martinzuege_4-seiter-web.pdf).
# shortcut: reviewed by hand because the importer has no PDF reader; retype from the new flyer each autumn.
_FLYER_2026 = {
    "2026-11-03": (
        _Procession(
            "vochem", "Martinszug Vochem", "Brühl-Vochem", "Bürgergemeinschaft Vochem 1949 e. V.", "Regenbogenschule, Hinterausgang",
            "Regenbogenschule Hinterausgang - Zum Herrengarten - Fronhofweg - Zum Herrengarten - Hauptstraße - "
            "kleine Kierberger Straße - Durchgang Alexianer Seniorenstift - Königsberger Straße - "
            "Auflösung Merseburger Str. (Höhe Kita)",
        ),
    ),
    "2026-11-04": (
        _Procession(
            "innenstadt", "Martinszug Innenstadt", "der Brühler Innenstadt", "Stadt Brühl", "Franziskanerhof/Leamington-Spa-Platz",
            "Franziskanerhof/Leamington-Spa-Platz - Uhlstraße - Markt - Kölnstraße - Schützenstraße - "
            "An der Synagoge - Mühlenstraße - Steinweg - Uhlstraße - Franziskanerhof",
        ),
        _Procession(
            "st. heinrich", "Martinszug Brühl-West", "Brühl-West", "Martinsausschuss St. Heinrich / Brühl-West",
            "Zum Rodderbruch",
            "Zum Rodderbruch - Rolfsacker - Zum Donnerbach - Zur Gabjei - Zum Rodderbruch - Kirchenvorplatz St. Heinrich",
        ),
    ),
    "2026-11-05": (
        _Procession(
            "st. margareta", "Martinszug Familienzentrum St. Margareta", "Brühl", "SKF Familienzentrum St. Margareta",
            "Familienzentrum St. Margareta",
            "Familienzentrum - Josefstraße - Kentenichstraße - Mühlenstraße - An der Bleiche - Heinrich-Fetten-Platz - "
            "Wallstraße - Kempishofstraße - Mühlenstraße - Hermannstraße - Josefstraße - Außengelände Kita Mühlenstraße",
        ),
        _Procession(
            "clemens-august", "Martinszug Familienzentrum Clemens-August", "Brühl", "Familienzentrum Clemens-August",
            "Familienzentrum Clemens-August, In der Maar",
            "Tor am Außengelände der Einrichtung In der Maar - Richtung Clemens-August-Straße - Georg-Sandmann-Straße - "
            "Liblarer Straße - zurück zum Familienzentrum (nur Gehwege)",
        ),
    ),
    "2026-11-06": (
        _Procession(
            "badorf", "Martinszug Badorf-Eckdorf", "Brühl-Badorf und Eckdorf", "DG Badorf-Eckdorf e. V.", "Grundschule Badorf, Badorfer Straße",
            "Grundschule Badorf, Badorfer Straße - Wingertsberg - Steingasse - Pützgasse - Auf der Kehre - "
            "Auf dem Kamm - Auf dem Gallberg - Robertstraße - Metzenmacherweg",
        ),
    ),
    "2026-11-08": (
        _Procession(
            "schwadorf", "Martinszug Schwadorf", "Brühl-Schwadorf", "DG Schwadorf", "Weiherhofstraße (vor FC-Heim)",
            "Weiherhofstraße (vor FC-Heim) - An der Schallenburg - Spürckstraße - Mittelstraße - Oberstraße - "
            "Flechtenweg - Sechtemer Straße - Weiherhofstraße - Kunibertweg zum Sportheim",
        ),
    ),
    "2026-11-09": (
        _Procession(
            "brühl-heide", "Martinszug Brühl-Heide", "Brühl-Heide", "DG Brühl-Heide e. V.", "Grubenstraße",
            "Grubenstraße - Klosterstraße - Barbarastraße - Gruhlstraße - Grubenstraße - Barbarastraße - Villestraße - "
            "Hochstraße - Im Himmelreich - Siegesbach - Bergstraße - Barbarastraße - Marienstraße - Klosterstraße - "
            "Wiese Grubenstraße",
        ),
        _Procession(
            "st. marien", "Martinszug Kita St. Marien Brühl-Süd", "Brühl-Süd", "Ortsgemeinschaft Brühl-Süd / Kita St. Marien",
            "Kita St. Marien, Waldorfer Straße",
            "Kita Waldorfer Straße - In der Maar - Walberberger Straße - Hubert-Geuer-Straße - "
            "Roisdorfer Straße - In der Maar - zurück zur Kita Waldorfer Straße",
        ),
    ),
    "2026-11-10": (
        _Procession(
            "st. stephan", "Martinszug Kita St. Stephan", "Brühl", "KiTa St. Stephan", "Kita St. Stephan, Rheinstraße 67",
            "Kita Rheinstraße 67 - Helma-Meyers-Weg - Ricarda-Huch-Weg - Fußweg Bergerstraße - "
            "Fußweg Rheinstraße zurück zur Kita",
        ),
        _Procession(
            "st. servatius", "Martinszug Kita St. Servatius Kierberg", "Brühl-Kierberg", "Kita St. Servatius Kierberg",
            "Kita St. Servatius, Servatiusstraße",
            "Kita Servatiusstraße - Vochemer Straße - Margaretenstraße - Berrenrather Straße - "
            "zurück zur Kita Servatiusstraße",
        ),
        _Procession(
            "pingsdorf", "Martinszug Pingsdorf", "Brühl-Pingsdorf", "DG Pingsdorf e. V.", "Kirche St. Pantaleon, Badorfer Straße",
            "Kirche Badorfer Straße - Obermühle - Euskirchener Straße Querung bis zum - Hüllenweg - "
            "Maiglerstraße - Schützenplatz Hüllenweg - Kath. Grundschule Hüllenweg / Jugendheim",
        ),
        _Procession(
            "familienzentrum brühl-süd", "Martinszug Familienzentrum Brühl-Süd", "Brühl-Süd",
            "Familienzentrum Brühl-Süd (Kita Auf der Pehle + Kita Lessingstraße)", "Kita Auf der Pehle 27",
            "Kita Auf der Pehle 27 - Joseph-Hürten-Straße - Auf der Pehle zur Kita",
        ),
    ),
    "2026-11-12": (
        _Procession(
            "kierberg", "Martinszug Kierberg", "Brühl-Kierberg", "DG Kierberg", "Regenbogen- und Barbaraschule, Kaiserstraße",
            "Regenbogen- und Barbaraschule, Kaiserstraße - Schulstraße - Mühlenbach - Barbarastraße und Kita Mühlenbach "
            "gliedern ein - Lohmühle - Winterburg - Theismühle - Talstraße - Schulstraße - Barbaraschule und "
            "Kita Mühlenbach gliedern aus - weiter zur Regenbogenschule",
        ),
    ),
}


def _flyer_entry(day: str, name: str) -> _Procession | None:
    folded = name.casefold()
    return next((entry for entry in _FLYER_2026.get(day, ()) if entry.needle in folded), None)


def _description(entry: _Procession | None, name: str, start: datetime, end: datetime) -> str:
    organizer = f"Veranstalter: {entry.organizer if entry else name}".rstrip(".") + "."
    if not entry:
        return organizer
    # Paragraphs, not sentences: "St." and "e. V." must not end the page's meta description mid-name.
    span = f"{start:%H:%M} bis {end:%H:%M} Uhr"
    return f"Laternenumzug in {entry.area} von {span}.\n\nZugweg: {entry.route.replace(' - ', ' – ')}.\n\n{organizer}"


def events_from_page(html: str) -> list[dict]:
    overview, year = _OVERVIEW_RE.search(html or ""), _YEAR_RE.search(html or "")
    if not overview or not year:
        return []
    events = []
    for line in re.split(r"<br\s*/?>", overview.group(0), flags=re.I):
        day_match = _DAY_RE.match(clean_html(re.sub(r"^.*?<p[^>]*>", "", line, flags=re.S)))
        if not day_match:
            continue
        day, month, items = day_match.groups()
        for item in _ITEM_SPLIT_RE.split(items):
            item_match = _ITEM_RE.match(item.strip())
            if not item_match:
                continue
            start_hour, start_minute, end_hour, end_minute, name = item_match.groups()
            start = datetime(int(year.group(1)), int(month), int(day), int(start_hour), int(start_minute))
            end = start.replace(hour=int(end_hour), minute=int(end_minute))
            # A procession the flyer does not know still publishes, with the page's facts only.
            entry = _flyer_entry(start.date().isoformat(), name)
            description = _description(entry, name, start, end)
            event = make_event(
                entry.title if entry else f"Martinszug {name}", start, end, entry.start if entry else "", "Brühl",
                description, URL, SOURCE, "martinszug sankt martin laternen kinder familie", 0.9,
                time_text=start.strftime("%H:%M"), source_id=SOURCE_ID,
                # Span and route are the city's and flyer's facts; a bare organizer line is not copy.
                description_source="scraped" if entry else "generated", default_category_key="kids",
                category_locked=True, link_kind="overview",
            )
            if event:
                events.append(event)
    return rc.dedupe(events)


def _no_list_published(html: str) -> bool:
    # Outside the season the entry drops its overview; that page is healthy.
    return "die-umzuege-im-ueberblick" not in (html or "")


def fetch() -> list[dict]:
    return rc.fetch_html_events(
        SOURCE, URL, events_from_page, source_id=SOURCE_ID,
        empty_is_healthy=_no_list_published,
    )
