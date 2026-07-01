# NRW Events

**NRW Events** ist ein kostenloses Open-Source-Tool zur Event-Recherche für
**Bonn und die Umgebung**. Der Schwerpunkt liegt bewusst auf Bonn: Innenstadt,
Poppelsdorf, Endenich, Beuel, Bad Godesberg, Ippendorf, Dransdorf, Rheinaue,
Kottenforst, Siebengebirge/Königswinter und praktikablen Tageszielen im Umkreis
von ca. 75 km, inklusive Köln, Siegburg, Troisdorf, Düsseldorf, Aachen und dem
nahen Ahrtal.

Das Projekt sammelt öffentlich verfügbare Eventdaten aus verschiedenen Quellen,
entdoppelt die Treffer, bewertet sie nach **Nähe zu Bonn + Kategorie +
Quellenqualität** und gibt einen Markdown-Bericht nach Kategorien aus. Zusätzlich
wird eine JSON-Datei für die Weiterverarbeitung geschrieben.

Keine Frameworks, keine Paketinstallation, keine externen Python-Abhängigkeiten:
nur Python 3 Standardbibliothek.

> **Unabhängig und nicht verbunden.** Dieses Repository ist nicht mit Bonn.de,
> Köln Open Data, Bundeskunsthalle, Songkick, Meetup, Exa, xAI oder irgendeiner
> anderen Quelle verbunden, gesponsert oder offiziell autorisiert. Es ist nur ein
> freies Open-Source-Werkzeug, das öffentlich erreichbare Informationen bündelt.

> **Keine hart codierten Events.** Events werden live zur Laufzeit aus den Quellen
> gelesen. Im Code liegen keine festen Eventnamen oder Eventdaten, sondern nur
> Quell-URLs, Geodaten und Bewertungs-/Kategorie-Logik.

## Tags

`bonn`, `nrw`, `veranstaltungen`, `events`, `freizeit`, `wochenende`,
`konzerte`, `ausstellungen`, `märkte`, `siebengebirge`, `ahrtal`, `open-source`,
`python`, `markdown`, `json`

## Warum?

Eventkalender in der Region Bonn sind fragmentiert: Open-Data-APIs, RSS-Feeds,
iCal-Feeds, JSON-LD-Seiten, HTML-Kalender und viele kleine lokale Veranstaltungen
wie Stadtteilfeste, Dorffeste, Kirmes, Flohmärkte, Führungen oder Wanderungen.
Große Aggregatoren übersehen gerade diese lokalen Dinge oft.

Dieses Tool fragt mehrere Quellen parallel ab und führt die Ergebnisse in einer
Liste zusammen. Der Bericht ist **Bonn-zentriert**: nähere und für Bonn praktisch
erreichbare Veranstaltungen werden höher bewertet.

## Schnellstart

```bash
# Nächste 3 Tage, z.B. ein Wochenende
bash scripts/nrw-events.sh

# Kommende 7 Tage
bash scripts/nrw-events.sh 7

# Nur heute
bash scripts/nrw-events.sh 1
```

Die Ausgabe erscheint als Markdown auf stdout. Eine vollständige JSON-Kopie wird
unter `/tmp/nrw-events-latest.json` gespeichert. Zusätzlich schreibt der
Metadaten-Export unter `/tmp/nrw-events-latest-meta.json` die stabile
Kategorieliste (`categories`) und je Event die kanonischen Felder
`category_key`/`category_label`; das rohe Quellenfeld `category` bleibt für
Debugging und Rückwärtskompatibilität erhalten.

Direkter Python-Aufruf:

```bash
python3 scripts/nrw-events.py 5
```

## Anforderungen

- **Python 3.9+**
- Nur Standardbibliothek: `urllib`, `xml.etree`, `concurrent.futures`, usw.
- Kein `pip install`, keine Drittanbieter-Pakete.

## Fokusgebiet

Der Mittelpunkt ist Bonn. Die Standard-Suche nutzt einen Radius von ca. 75 km um
Bonn und bevorzugt Treffer, die für Menschen in Bonn praktisch interessant sind.
Typische Zielgebiete:

- Bonn: Innenstadt, Poppelsdorf, Endenich, Beuel, Bad Godesberg, Ippendorf,
  Dransdorf, Rheinaue
- Natur/Outdoor: Kottenforst, Siebengebirge, Königswinter, Drachenfels,
  Petersberg, Bad Honnef
- Nahbereich: Siegburg, Troisdorf, Bornheim, Meckenheim, Rheinbach
- Größere Städte: Köln, Düsseldorf, Aachen
- Nahe Ausflugsregionen: Ahrweiler, Bad Neuenahr-Ahrweiler, Dernau, Mayschoss,
  Ahrtal

Der Name „NRW Events“ ist also etwas breiter, aber der praktische Fokus ist:
**Was lohnt sich für jemanden in oder bei Bonn?**

## Projektstruktur

Der Code ist bewusst klein und modular: ein Bereich pro Datei, eine Quelle pro
Datei.

```text
scripts/
  nrw-events.py            # dünner Einstiegspunkt
  nrw-events.sh            # Shell-Wrapper, lädt .env und startet Python
  nrw_events/
    category_taxonomy.py   # stabile Kategorie-Keys, Labels und Keyword-Klassifizierung
    config.py              # Geodaten, Kategoriegewichte, Venue-Koordinaten, Gruppenlisten
    common.py              # HTTP, HTML/JSON-LD/iCal, Datumslogik, Scoring, Event-Erzeugung
    report.py              # Entdoppelung + Markdown-Ausgabe
    runner.py              # Orchestrierung: Quellen parallel abfragen, filtern, schreiben
    sources/
      __init__.py          # SOURCES-Registry: Anzeigename -> fetch-Funktion
      koeln.py  bonn.py  songkick.py  meetup.py  harmonie.py
      koenigswinter.py  siebengebirge.py  bonnjetzt.py
      flohmarkt.py  bundeskunsthalle.py  naturregion_sieg.py
      troisdorf.py  ruhrguide.py  search.py
```

Jedes Quellenmodul stellt eine Funktion `fetch() -> list[dict]` bereit. Fehler in
einer Quelle brechen den Gesamtlauf nicht ab; die Quelle liefert dann einfach
keine Treffer.

## API-Schlüssel, optional

Das Tool läuft ohne API-Schlüssel. Die deterministischen Quellen erledigen den
wichtigsten Teil. Zwei optionale Schlüssel aktivieren zusätzliche Such-Fallbacks:

| Schlüssel     | Dienst                 | Aktiviert                                           |
|---------------|------------------------|-----------------------------------------------------|
| `EXA_API_KEY` | [Exa](https://exa.ai)  | Websuche für schwer auffindbare lokale Eventseiten  |
| `XAI_API_KEY` | [xAI Grok](https://x.ai) | optionale agentische Suche, standardmäßig aus      |

Schlüssel können als echte Umgebungsvariablen gesetzt werden oder über eine
lokale `.env`:

```bash
cp .env.example .env
$EDITOR .env
```

Ladereihenfolge: echte Env Vars → `NRW_EVENTS_ENV_FILE` → `.env` im Repo → `.env`
im aktuellen Arbeitsverzeichnis. Echte Umgebungsvariablen gewinnen immer.
**`.env` ist gitignored.**

## Konfiguration über Umgebungsvariablen

Die Standardwerte bevorzugen **Vollständigkeit vor Kürze**. Ohne explizite
Begrenzung werden alle gefundenen, deduplizierten und relevanten Events gezeigt.
Die zuletzt beobachtete Zahl `99` war nur das Ergebnis eines konkreten Testlaufs,
kein Limit.

| Variable                      | Standard | Wirkung |
|-------------------------------|----------|---------|
| `NRW_EVENTS_MAX_PER_SECTION`  | `0`      | Optionale Begrenzung pro Kategorie. `0`/nicht gesetzt = alle Events anzeigen. |
| `NRW_EVENTS_SCORE_FLOOR`      | `0.4`    | Mindestscore. Niedriger = mehr Treffer und mehr Rauschen. |
| `NRW_EVENTS_EXA_QUERIES`      | `10`     | Anzahl der Exa-Suchanfragen, jeweils ca. 5 Ergebnisse. |
| `NRW_EVENTS_ENABLE_GROK`      | nicht gesetzt | Auf `1` setzen, um die langsame/kostspielige Grok-Suche zu aktivieren. |
| `NRW_EVENTS_USER_AGENT`       | moderner Chrome UA | Optionaler Override für HTTP-Requests an öffentliche Quellen. |
| `NRW_EVENTS_HTTP_RETRY_ATTEMPTS` | `5` | Maximale Versuche für temporäre HTTP-/Netzwerkfehler (`429`, `5xx`, Timeouts). |
| `NRW_EVENTS_HTTP_RETRY_BASE_SECONDS` | `1.0` | Basis für exponentielles Retry-Backoff mit Jitter. |
| `NRW_EVENTS_BONN_DE_DELAY_SECONDS` | `2.0` | Mindestabstand zwischen Requests an `bonn.de`, um MyraCDN/Backend-503s bei Parallelimporten zu reduzieren. |
| `NRW_EVENTS_ENV_FILE`         | nicht gesetzt | Expliziter Pfad zu einer `.env`-Datei. |

Beispiel für eine absichtlich kurze, strenge Liste:

```bash
NRW_EVENTS_SCORE_FLOOR=0.7 NRW_EVENTS_MAX_PER_SECTION=5 bash scripts/nrw-events.sh
```

Für den normalen vollständigen Bericht nichts begrenzen:

```bash
unset NRW_EVENTS_MAX_PER_SECTION
bash scripts/nrw-events.sh 7
```

## Scoring

Jedes Event erhält grob:

```text
Distanzscore × Kategoriegewicht × Quellenvertrauen
```

- **Distanz:** Bonn-Zentrum = 1.0, linear fallend bis zum Rand des 75-km-Radius.
- **Kategorie:** Gewichtungen in `config.CATEGORY_WEIGHT`, z.B. elektronische Musik,
  Architektur, Weinwanderungen, Konzerte, Ausstellungen, Märkte und Outdoor.
- **Quellenvertrauen:** strukturierte APIs und iCal/JSON-LD sind höher gewichtet
  als fragile HTML-Scrapes oder Suchtreffer.

Events mit Familien-/Kinder-Nebenangebot werden nicht pauschal abgewertet. Nur
wirklich reine Kinderveranstaltungen werden niedriger bewertet.

## Quellen

Strukturierte Quellen werden bevorzugt, danach HTML-Scraping, danach Suche. Alle
Treffer werden live ermittelt. HTTP-Requests verwenden standardmäßig einen
konsistenten, browserähnlichen Header-Satz mit deutscher `Accept-Language` statt
des auffälligen Python-Standard-User-Agents; Quellmodule können Header bei Bedarf
weiterhin gezielt überschreiben.

- **Open-Data-API:** Köln Open Data Events (`koeln.py`)
- **RSS / HTML:** Bonn.de Veranstaltungskalender und Sportveranstaltungen (`bonn.py`)
- **Jährliche Pressemitteilung:** Bonner Stadtteilfeste, Kirmes, Märkte und lokale
  Termine aus dem städtischen „Veranstaltungsjahr“ (`bonn.py`)
- **JSON-LD / schema.org:** Rheinauen-Flohmarkt, VVS Siebengebirge, Songkick
- **iCal / RFC 5545:** Harmonie Bonn, Troisdorf, Siegburg und kuratierte
  Meetup-Gruppen
- **Strukturiertes HTML:** Königswinter, Naturregion Sieg, Ruhr-Guide,
  Bundeskunsthalle, Bonn.jetzt
- **Websuche als Fallback:** Exa standardmäßig, Grok optional (`search.py`)

Das Ahrtal, z.B. Ahrweiler, Bad Neuenahr, Dernau und Mayschoss, ist trotz des
NRW-Namens im praktischen Suchraum, weil es von Bonn gut erreichbar ist und für
Wein, Wandern und Wochenendausflüge relevant sein kann.

## Eine neue Quelle hinzufügen

Viele deutsche Veranstaltungsseiten nutzen WordPress mit „The Events Calendar“.
Oft gibt es einen iCal-Feed unter `?post_type=tribe_events&ical=1`. iCal oder
JSON-LD ist stabiler als HTML-Scraping.

1. Quelle prüfen:
   ```bash
   curl -sL '<url>' | grep -c 'BEGIN:VEVENT'         # iCal
   curl -sL '<url>' | grep -c 'application/ld+json'  # JSON-LD
   ```
2. `scripts/nrw_events/sources/<name>.py` mit `fetch()` anlegen.
3. Bestehende Helfer verwenden: `common.fetch_ical(...)` oder
   `common.events_from_jsonld(...)`.
4. Quelle in `sources/__init__.py` registrieren.
5. Neue Orte in `config.VENUE_COORDS` ergänzen, damit die Distanzwertung stimmt.

Für Meetup-Gruppen: `config.MEETUP_GROUPS` bearbeiten. Öffentliche iCal-Feeds
liegen unter `https://www.meetup.com/<slug>/events/ical/`.

## Ausgabe

- **Markdown auf stdout:** Kategorien, Eventname, Datum/Zeit, Ort, Distanz,
  Bewertung, Beschreibung und Link.
- **JSON unter `/tmp/nrw-events-latest.json`:** vollständige deduplizierte und
  bewertete Eventliste.
- **Metadaten-JSON unter `/tmp/nrw-events-latest-meta.json`:** Zeitfenster,
  Radius, Quellenstatistiken, Fehler und die vollständige Eventliste.

Standardmäßig wird die vollständige Liste ausgegeben. Gekürzt wird nur, wenn
`NRW_EVENTS_MAX_PER_SECTION` explizit gesetzt wird.

## Anpassung

Häufige Anpassungen:

- `config.CATEGORY_WEIGHT` — Ranking an eigene Interessen anpassen.
- `config.BONN_LAT`, `config.BONN_LON`, `MAX_RADIUS_KM` — Suchmittelpunkt/Radius ändern.
- `config.VENUE_COORDS` — Orte für genauere Distanzwerte ergänzen.
- `sources/__init__.py` — Quellen hinzufügen oder entfernen.

## Lizenz

MIT — siehe [LICENSE](LICENSE).

## Disclaimer

Dieses Tool aggregiert öffentlich verfügbare Eventinformationen von Drittseiten.
Bitte Datum, Uhrzeit, Ort, Preis und Tickets immer auf der offiziellen Eventseite
prüfen, bevor du losgehst. Respektiere die Nutzungsbedingungen und Rate Limits der
jeweiligen Quellen.

Dieses Projekt ist unabhängig von den genannten Datenquellen. Es ist keine
offizielle Eventdatenbank und keine Zusicherung, dass eine Veranstaltung wirklich
stattfindet, vollständig beschrieben oder noch verfügbar ist.
