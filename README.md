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

## Live-Website

Die aufbereiteten Termine werden unter https://www.veranstaltungen-bonn.de/ veröffentlicht; jeder Eintrag verweist zur Originalquelle.

> **Unabhängig und nicht verbunden.** Dieses Repository ist nicht mit Bonn.de,
> Köln Open Data, Bundeskunsthalle, Meetup, Exa, xAI oder irgendeiner
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
unter `~/.local/state/nrw-events/nrw-events-latest.json` gespeichert (oder unter
`$XDG_STATE_HOME/nrw-events`). Duplikate werden feldweise
angereichert; offizielle und direkte lokale Quellen haben dabei Vorrang vor
Aggregatoren und Suchtreffern. Zusätzlich schreibt der
Metadaten-Export daneben die stabile
Kategorieliste (`categories`) und je Event die kanonischen Felder
`category_key`/`category_label`; das rohe Quellenfeld `category` bleibt für
Debugging und Rückwärtskompatibilität erhalten.

Zusätzlich zu den kompatiblen Anzeige-Feldern `date` und `time` enthält jedes
Event kanonische Zeitfelder: `start_date`, `end_date`, `start_at`, `end_at`,
`all_day` und `timezone`. Ort und Datenqualität sind als
`location_confidence`, `location_source` und `status` verfügbar. Abgesagte
Events bleiben bis zu ihrem ursprünglichen Termin mit `status: "cancelled"`,
`cancelled_at` und `cancellation_source` veröffentlicht; Verschiebungen können
zusätzlich `replacement_start_date` tragen. Sie erhalten Score 0 und werden
damit nie als normaler Tipp gerankt. Unvollständige oder ungültige Quellrecords
werden mit einem Grund pro Quelle in `source_results` gezählt.

Events mit nicht auflösbarem Ort bleiben mit `distance_km: null` und
`location_confidence: "unresolved"` erhalten. Nur belegte Distanzen außerhalb
des konfigurierten Radius werden verworfen; diese Entscheidung erscheint als
`filter:radius` pro Quelle in `source_results.rejection_reasons`. Dadurch misst
das Quality-Gate die ungeklärten Orte vor einer möglichen Consumer-Entscheidung
und nicht erst nach einem stillen Verlust.

Stabile Veranstaltungsorte werden deterministisch über das statische
`VENUE_REGISTRY` aufgelöst. Es ordnet geprüfte Aliasse einer `venue_id` sowie,
falls belegt, Anzeigename, Adresse, Stadtteil, Ortstyp und Koordinaten zu. Die
zugehörigen Ausgabefelder heißen `venue`, `venue_id`, `venue_address`,
`venue_district`, `venue_type`, `venue_latitude` und `venue_longitude`.
Unbekannte Orte bleiben unverändert und erhalten keine erfundene ID; eine im
Quelltext enthaltene Adresse wird lediglich vom Namen getrennt. Die ersten
Bonner Einträge stammen aus den bereits verwendeten städtischen
Open-Data-Layern OD=4490 und OD=4489. Registry-Treffer und Adressabdeckung sind
in `quality_metrics` als `registered_venue_count` und `venue_address_count`
messbar.

Jedes veröffentlichte Event trägt eine stabile `event_id`. Sie identifiziert
genau eine Veranstaltungs-Occurrence und ist damit als dauerhafte URL
verwendbar. Die ID wird nach der Deduplizierung und vor der Sortierung vergeben
und leitet sich ausschließlich aus normalisiertem Titel, `start_date`,
Startzeit, Venue-Identität (`venue_id`, sonst Venue-Name) und Ort ab. Aus
`time` geht dabei ausschließlich die führende Startzeit ein: das Feld trägt
häufig eine Spanne (`08:00–14:00`), und eine Endzeit, die eine Quelle ergänzt
oder wegfallen lässt, darf keine veröffentlichte URL bewegen. Feed-
Reihenfolge, Score, Quelle, Preis, Beschreibung und Link fließen bewusst nicht
ein — Anreicherung darf keine bereits veröffentlichte URL bewegen. `source_id`
benennt eine Quelle, kein Event, und ist nie Teil der Identität. Verschiedene
Termine und Uhrzeiten einer Serie bleiben verschieden. Kollisionen werden
erkannt und in inhaltsbasierter, reihenfolgeunabhängiger Ordnung mit einem
Suffix aufgelöst; siehe `scripts/nrw_events/identity.py`.

Die Website implementiert dieselbe Regel in TypeScript für Events, die nie
durch diesen Importer laufen (Formular-Einreichungen). Beide Implementierungen
sind auf die Golden Vectors in `tests/data/event_id_vectors.json` festgenagelt.
Ändert sich `identity.py`, müssen Vektoren und Website-Implementierung im
selben Review nachgezogen werden. `tests/test_public_event_contract.py` hält
zusätzlich die Feldliste und die Menge der `venue_id`-Werte fest, auf die die
Website ihre kanonischen Veranstaltungsorte abbildet.

Snapshot-Schema 4 ergänzt `first_seen_at` und `content_hash`. Die erste Angabe
bleibt über Läufe stabil; der Hash ändert sich bei einer inhaltlichen Änderung,
nicht aber durch Feed-Reihenfolge oder das Hashfeld selbst. Retention vergleicht
zuerst `event_id` und nutzt den bisherigen Fuzzy-Vergleich nur für alte
Snapshots ohne kompatible ID.

Die redaktionellen Signale `flea_market`, `ahr_wine`, `local_festival`,
`antique_market` und `bonn_local` werden pro Event als `ranking_features`
exportiert. `priority_bonus` ist deren Summe. Der erklärbare Basisscore
(`distance × category × trust`) bleibt davon unverändert.

## Serien und Highlights

Wiederkehrende Veranstaltungen erhalten bei mindestens zwei belegten
Occurrences eine stabile `series_id`, `series_title` und eine `run_id`. Die
Metadaten enthalten die dreistufige Struktur Serie → Run → Occurrence,
Kadenzen (`weekly`, `biweekly`, `monthly`, sonst konservativ `irregular`) sowie
die Zustände `active`, `dormant_seasonal`, `dormant_unknown` und `concluded`.
Ein persistentes `series-ledger.json` sammelt bestätigte und außerhalb des
Publikationsfensters angekündigte Termine. Saisonvertrauen braucht mindestens
zwei beobachtete Jahre; davor bleibt der ehrliche Cold-Start-Zustand
`dormant_unknown`. Geschätzte Folgetermine stehen ausschließlich in
`next_occurrence_estimated`, nie in `next_occurrence` oder `events.json`.

Neben Events und Metadaten wird atomar ein `highlights.json` mit derselben
`run_id` veröffentlicht. Die Auswahl ist offline reproduzierbar und kombiniert
`score`, `ranking_features` und generische Diversitätsgrenzen pro
`venue_id`/Kategorie. Ein LLM kann dieses gültige Basisergebnis später
verfeinern, ist aber keine Voraussetzung. Das Manifest verweist auf alle drei
Artefakte; ein inkonsistentes Highlight-Dokument degradiert den Lauf sichtbar.

Redaktionelle Ausschlüsse erscheinen dort als stabile, maschinenlesbare
`quality:<rule_id>`-Gründe, zum Beispiel `quality:civic.course`. Der
Metadaten-Export enthält außerdem `quality_metrics` und warnende, aber nicht
löschende `quality_warnings`. Ab mindestens zehn Events warnt der Lauf bei mehr
als 6 % `other` insgesamt sowie je Quelle bei mehr als 50 % niedriger
Kategorie-Konfidenz, 25 % ungeklärten Orten, 25 % fehlenden Ortsnamen oder 50 %
redaktionell verworfenen Kandidaten. Dieselben Einträge stehen aus
Kompatibilitätsgründen auch in `source_warnings`.

Jeder Lauf veröffentlicht außerdem atomisch eine Manifest-Datei neben den
beiden JSON-Dateien. Sie enthält die gemeinsame
`run_id`, den Laufstatus und die zugehörigen Artefaktpfade; Hintergrund-Consumer
sollten nur Snapshots mit einem aktuellen Manifest lesen.

Direkter Python-Aufruf:

```bash
python3 scripts/nrw-events.py 5
```

## Anforderungen

- **Python 3.10+**
- Nur Standardbibliothek: `urllib`, `xml.etree`, `concurrent.futures`, usw.
- Kein `pip install`, keine Drittanbieter-Pakete.

## Tests

```bash
bash scripts/test.sh
```

Der Qualitätslauf bleibt vollständig offline und behandelt `ResourceWarning`
als Fehler, damit nicht geschlossene HTTP-Antworten reproduzierbar fehlschlagen.

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

Der Paketinhalt und alle Quellenmodule werden aus dem Dateisystem generiert;
die Schichtung und Verantwortlichkeiten beschreibt
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

<!-- BEGIN GENERATED MODULES -->
```text
scripts/nrw_events/
  __init__.py
  category_taxonomy.py
  common.py
  config.py
  core.py
  dates.py
  detail_enrichment.py
  event_builder.py
  event_vocabulary.py
  health.py
  highlights.py
  http.py
  ical.py
  identity.py
  jsonld.py
  junk_rules.py
  location.py
  models.py
  normalization.py
  observability.py
  quality.py
  report.py
  richtext.py
  runner.py
  runtime.py
  scoring.py
  series.py
  source_specs.py
  source_types.py
  text.py
  title_normalization.py
  validation.py
  sources/
    registry.json
    __init__.py
    afterjobparty.py
    bonn.py
    bonn_districts.py
    bonn_food.py
    bonn_literature.py
    bonn_venues.py
    bonner_weihnachtsmarkt.py
    bonnjetzt.py
    bundeskunsthalle.py
    cinema_specials.py
    coelln_antik_design.py
    coelln_konzept.py
    deutsches_museum_bonn.py
    fixed_markets.py
    flohmarkt.py
    geide.py
    grote_hiller.py
    harmonie.py
    haus_der_geschichte.py
    hoffloh_bonn.py
    hofflohmaerkte.py
    junges_theater_bonn.py
    katharinenhof.py
    kinderflohmarkt.py
    kleines_theater.py
    koeln.py
    koenigswinter.py
    krewelshof.py
    lampert.py
    marktcom.py
    max7.py
    mec_municipal.py
    meckenheim.py
    meetup.py
    melan.py
    much.py
    museum_koenig.py
    naturregion_sieg.py
    okken.py
    radiobonn.py
    regional_common.py
    regional_feeds.py
    regional_html.py
    regional_ionas4.py
    regional_sitekit.py
    regional_tourism.py
    regional_venues.py
    requested_venues.py
    rhein_antik.py
    rheinbach_flohmarkt.py
    rheinevents.py
    ruhrguide.py
    salsainbonn.py
    search.py
    siebengebirge.py
    siegburg.py
    theater_bonn.py
    theater_im_ballsaal.py
    theater_marabu.py
    tik_bonn.py
    uni_bonn.py
```
<!-- END GENERATED MODULES -->

Bei iCal-Quellen hat das eventbezogene `CATEGORIES`-Feld Vorrang vor dem
statischen `category_hint` des `SourceSpec`; der Hint bleibt der Fallback für
Events ohne eigene Kategorien. Die Signale werden bewusst nicht zusammengeklebt:
Das könnte eine künstlich breite Kategorie-Tüte erzeugen, die die Taxonomie als
unzuverlässig verwirft.

Alle 81 Registry-Einträge liegen schema-validiert in
`sources/registry.json` und tragen ein `region`-Feld. Standard-iCal-, JSON-LD-
und einfache selektorbasierte HTML-Quellen benötigen normalerweise nur einen
neuen Registry-Eintrag plus Fixture-/Vertragstest. `SourceSpec` unterstützt
mehrere Endpoints/Paginierungs-URLs und gecachte Detailseiten. Proprietäre
Parser bleiben als explizit referenzierter Python-Adapter erhalten. Jedes
Quellenmodul stellt eine Funktion `fetch() -> list[dict]`
bereit. Fehler in
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

Ladereihenfolge: echte Env Vars → `NRW_EVENTS_ENV_FILE` → `.env` im Repo.
Eine `.env` im aktuellen Arbeitsverzeichnis wird aus Sicherheitsgründen nicht
geladen. Echte Umgebungsvariablen gewinnen immer.
**`.env` ist gitignored.**

## Konfiguration über Umgebungsvariablen

Die vollständig kommentierte [`.env.example`](.env.example) ist die kanonische
Liste aller Einstellungen; `python3 scripts/check_env_docs.py` prüft sie lokal
gegen die Zugriffe im Python-Code.

Die Standardwerte bevorzugen **Vollständigkeit vor Kürze**. Ohne explizite
Begrenzung werden alle gefundenen, deduplizierten und relevanten Events gezeigt.
Die zuletzt beobachtete Zahl `99` war nur das Ergebnis eines konkreten Testlaufs,
kein Limit.

Die CLI kann den bestehenden Tageswert weiterhin positional lesen und bietet
zusätzlich maschinenlesbare, eng gefilterte Abfragen:

```bash
bash scripts/nrw-events.sh 7
bash scripts/nrw-events.sh heute --json
bash scripts/nrw-events.sh wochenende --umkreis 15km --kostenlos
bash scripts/nrw-events.sh heute-abend --kategorie markt,festival
bash scripts/nrw-events.sh --days 7 --json
```

Ein Verb (`heute`, `heute-abend`, `wochenende`) legt sein Zeitfenster selbst
fest; die Kombination mit `--days` wird abgelehnt statt still überschrieben.

`--json` schreibt ausschließlich die gefilterte Eventliste nach stdout und
verändert die Snapshot-Dateien nicht. Logs bleiben auf stderr. CLI-Flags
überschreiben die entsprechenden Umgebungsvariablen.

| Variable                      | Standard | Wirkung |
|-------------------------------|----------|---------|
| `NRW_EVENTS_MAX_PER_SECTION`  | `0`      | Optionale Begrenzung pro Kategorie. `0`/nicht gesetzt = alle Events anzeigen. |
| `NRW_EVENTS_DAYS_AHEAD`       | `3`      | Standard-Zeitfenster, wenn kein CLI-Argument gesetzt ist (1–90). |
| `NRW_EVENTS_SCORE_FLOOR`      | `0.4`    | Mindestscore. Niedriger = mehr Treffer und mehr Rauschen. |
| `NRW_EVENTS_RADIUS_KM`        | `75`     | Maximaler Umkreis ab Bonn; entspricht `--umkreis`. |
| `NRW_EVENTS_DESCRIPTION_MAX_CHARS` | `700` | Maximale Länge der normalisierten öffentlichen Kurzbeschreibung; `0` deaktiviert die Kürzung. |
| `NRW_EVENTS_CATEGORIES`       | nicht gesetzt | Kommagetrennte Kategorie-Keys; entspricht `--kategorie`. |
| `NRW_EVENTS_FREE_ONLY`        | `0`      | Nur explizit kostenlose Events; entspricht `--kostenlos`. |
| `NRW_EVENTS_JSON_STDOUT`      | `0`      | Eventliste als reines JSON auf stdout, ohne Snapshot-Publikation; entspricht `--json`. |
| `NRW_EVENTS_DESCRIPTION_MAX_CHARS` | `700` | Allgemeine Obergrenze für normalisierte Beschreibungstexte. |
| `NRW_EVENTS_HIGHLIGHTS_JSON_OUT` | State-Verzeichnis/`highlights.json` | Deterministisches Highlight-Artefakt derselben Snapshot-Generation. |
| `NRW_EVENTS_SERIES_LEDGER_JSON` | State-Verzeichnis/`series-ledger.json` | Dauerhafte Occurrence-Historie für Serien, Runs und Saisonalität. |
| `NRW_EVENTS_CATEGORY_FALLBACK_CACHE` | nicht gesetzt | Optionaler geprüfter Cache für unklare Serien (`source_id` + normalisierter Titel). Es erfolgt kein LLM- oder Netzwerkaufruf. |
| `NRW_EVENTS_EXA_QUERIES`      | `10`     | Anzahl der Exa-Suchanfragen, jeweils ca. 5 Ergebnisse. |
| `NRW_EVENTS_ENABLE_GROK`      | nicht gesetzt | Auf `1` setzen, um die langsame/kostspielige Grok-Suche zu aktivieren. |
| `NRW_EVENTS_USER_AGENT`       | moderner Chrome UA | Optionaler Override für HTTP-Requests an öffentliche Quellen. |
| `NRW_EVENTS_HTTP_RETRY_ATTEMPTS` | `5` | Maximale Versuche für temporäre HTTP-/Netzwerkfehler (`429`, `5xx`, Timeouts). |
| `NRW_EVENTS_HTTP_RETRY_BASE_SECONDS` | `1.0` | Basis für exponentielles Retry-Backoff mit Jitter. |
| `NRW_EVENTS_HTTP_REQUEST_BUDGET_SECONDS` | `45.0` | Gemeinsames Zeitbudget für Request, Wiederholungen und Backoff; Socket-Timeouts werden an die Restzeit angepasst. |
| `NRW_EVENTS_HTTP_RETRY_MAX_DELAY_SECONDS` | `60.0` | Obergrenze für einzelne Retry-Wartezeiten. |
| `NRW_EVENTS_HTTP_MAX_RESPONSE_BYTES` | `5000000` | Harte Antwortgrößen-Grenze pro HTTP-Request. |
| `NRW_EVENTS_SOURCE_WORKERS` | `12` | Maximale parallele Quellen. Requests an denselben Host werden serialisiert; verschiedene Hosts laufen parallel. |
| `NRW_EVENTS_SOURCE_TIMEOUT_SECONDS` | `180.0` | Inaktivitätsbudget einer Quelle. Jeder erfolgreiche Endpunkt erneuert es; nachfolgende Requests und Retries werden auf die Restzeit begrenzt. |
| `NRW_EVENTS_SOURCE_BASELINE_MIN_COUNT` | `10` | Ab dieser vorherigen Trefferzahl wird ein neuer Nullstand als Telemetrie-Anomalie markiert. |
| `NRW_EVENTS_BONN_DE_DELAY_SECONDS` | `2.0` | Mindestabstand zwischen Requests an `bonn.de`, um MyraCDN/Backend-503s bei Parallelimporten zu reduzieren. |
| `NRW_EVENTS_BONN_CALENDAR_MAX_PAGES` | `30` | Sicherheitsgrenze für paginierte Bonn.de-Kalenderseiten. |
| `BRIGHT_DATA_API_KEY` / `BRIGHT_DATA_ZONE` | nicht gesetzt | Bright-Data-Web-Unlocker-Zugang. vomFASS wird montags ausschließlich darüber aktualisiert; Hofflohmärkte Köln nutzt ihn nach direkten HTTP-429- oder Timeout-Fehlern als Fallback. Die fünf IONAS4-Regionalkalender nutzen ihn nur nach direkten Timeouts oder transienten HTTP-Fehlern. Alle Fallbacks bleiben auf die jeweils fest konfigurierten Quellhosts beschränkt. |
| `NRW_EVENTS_CACHE_DIR` | `~/.cache/nrw-events` | Persistenter Cache für sparsame Detail-Abfragen. |
| `NRW_EVENTS_DETAIL_CACHE_TTL_HOURS` | `24` | TTL für HTML-Detailseiten-Abrufe. Bonn.de nutzt diese Seiten für strukturierte Veranstaltungsorte und Adressen aller aktuellen Kalendereinträge; wiederkehrende Termine und parallele Bonn-Listen teilen denselben persistenten Cache. Um das feste Abruflimit einzuhalten, werden bei Bonn.de auch fehlgeschlagene Versuche bis zum TTL-Ablauf negativ gecacht. Weitere Nutzer sind unter anderem Siegburg, Much, Königswinter, Naturregion Sieg, Linz, IONAS-Kommunen und einzelne Veranstaltungsorte. `0` deaktiviert Speicher- und Platten-Cache. Listen, APIs und Feeds bleiben ungecacht und werden bei jedem Import frisch geladen. |
| `NRW_EVENTS_DETAIL_ENRICHMENT` | `1` | Gemeinsame, gecachte Anreicherung aus primären Detailseiten; `0` deaktiviert sie. |
| `XDG_CACHE_HOME` | `~/.cache` | Standardbasis für persistente Detail-Caches, wenn `NRW_EVENTS_CACHE_DIR` fehlt. |
| `NRW_EVENTS_BONN_DETAIL_DESCRIPTION_MAX_CHARS` | `0` | Optionale Obergrenze für den aus einer Bonn.de-Detailseite übernommenen Text. Standardmäßig werden alle erklärenden Absätze und Aufzählungen vollständig übernommen; Logistikblöcke werden weiterhin übersprungen. Ein positiver Wert aktiviert eine satz- bzw. wortnahe Kürzung. |
| `NRW_EVENTS_JSON_OUT`         | Benutzer-State-Verzeichnis | Zielpfad für die Eventliste als JSON-Array. |
| `NRW_EVENTS_META_JSON_OUT`    | Benutzer-State-Verzeichnis | Zielpfad für Metadaten, Quellenstatistik und Warnungen. |
| `NRW_EVENTS_LOG_LEVEL`        | `INFO` | Log-Level (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |
| `NRW_EVENTS_LOG_FILE`         | nicht gesetzt | Optionaler persistenter Text-Logpfad. |
| `NRW_EVENTS_JSON_LOG_FILE`    | nicht gesetzt | Optionaler JSON-Lines-Logpfad für Monitoring. |
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
  Architektur, Weinwanderungen, Konzerte, Ausstellungen, Märkte und Outdoor. Wenn
  mehrere Begriffe passen, werden die stärkste Aufwertung und die stärkste
  Abwertung multipliziert; damit wirken auch Gewichte unter 0,8 tatsächlich.
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

Die folgende vollständige Liste wird direkt aus `sources/registry.json`
generiert. Die nachfolgenden Absätze erläutern nur besondere Adapter- und
Redaktionsentscheidungen.

<!-- BEGIN GENERATED SOURCES -->
| Region | Quelle | ID | Adapter |
|---|---|---|---|
| bonn-region | AfterJobParty Bonn | `afterjobparty-bonn` | `python` |
| bonn-region | Bad Godesberg Stadtmarketing | `bad-godesberg-stadtmarketing` | `python` |
| bonn-region | Beuel.net | `beuel-net` | `python` |
| bonn-region | BFF Bonner Schifffahrt | `bff-bonner-schifffahrt` | `python` |
| bonn-region | Biertasting Bonn | `biertasting-bonn` | `python` |
| bonn-region | Bonn district festivals | `bonn-district-festivals` | `python` |
| bonn-region | Bonn venue calendars | `bonn-venue-calendars` | `python` |
| bonn-region | Bonn.de Events | `bonn-de-events` | `python` |
| bonn-region | Bonn.de Sports | `bonn-de-sports` | `python` |
| bonn-region | Bonn.jetzt | `bonn-jetzt` | `python` |
| bonn-region | Bonner Weihnachtsmarkt | `bonner-weihnachtsmarkt` | `python` |
| bonn-region | BSV Roleber | `bsv-roleber` | `python` |
| bonn-region | Bundeskunsthalle | `bundeskunsthalle` | `python` |
| bonn-region | BV Holzlar | `bv-holzlar` | `python` |
| bonn-region | Bürgerverein Vilich-Müldorf | `b-rgerverein-vilich-m-ldorf` | `python` |
| bonn-region | Choco Dealer | `choco-dealer` | `python` |
| bonn-region | Craftquelle Bonn | `craftquelle-bonn` | `python` |
| bonn-region | Curated cinema specials | `curated-cinema-specials` | `python` |
| bonn-region | Cölln Antik&Design | `c-lln-antik-design` | `python` |
| bonn-region | Cölln Konzept | `c-lln-konzept` | `python` |
| bonn-region | Deskline regional | `deskline-regional` | `python` |
| bonn-region | Deutsches Museum Bonn | `deutsches-museum-bonn` | `python` |
| bonn-region | Exa Search | `exa-search` | `python` |
| bonn-region | Geide Märkte | `geide-m-rkte` | `python` |
| bonn-region | Grok Search | `grok-search` | `python` |
| bonn-region | Grote & Hiller | `grote-hiller` | `python` |
| bonn-region | Hardtberg Kultur | `hardtberg-kultur` | `python` |
| bonn-region | Harmonie Bonn | `harmonie-bonn` | `python` |
| bonn-region | Haus der Geschichte | `haus-der-geschichte` | `python` |
| bonn-region | Haus der Geschichte Begleitungen | `haus-der-geschichte-begleitungen` | `python` |
| bonn-region | Hennef | `hennef` | `json_ld` |
| bonn-region | HofFloh Bonn | `hoffloh-bonn` | `python` |
| bonn-region | Hofflohmärkte Köln | `hofflohm-rkte-k-ln` | `python` |
| bonn-region | ionas4 regional | `ionas4-regional` | `python` |
| bonn-region | Junges Theater Bonn | `junges-theater-bonn` | `python` |
| bonn-region | Katharinenhof Flohmarkt | `katharinenhof-flohmarkt` | `python` |
| bonn-region | Kinderflohmarkt.com | `kinderflohmarkt-com` | `python` |
| bonn-region | Kleines Theater Bad Godesberg | `kleines-theater-bad-godesberg` | `python` |
| bonn-region | Krewelshof Kindersachen-Flohmarkt | `krewelshof-kindersachen-flohmarkt` | `python` |
| bonn-region | Köln Open Data | `k-ln-open-data` | `python` |
| bonn-region | Königswinter | `k-nigswinter` | `python` |
| bonn-region | Lampert Märkte | `lampert-m-rkte` | `python` |
| bonn-region | Literaturhaus Bonn | `literaturhaus-bonn` | `python` |
| bonn-region | Ludwig's Bonn | `ludwig-s-bonn` | `python` |
| bonn-region | marktcom | `marktcom` | `python` |
| bonn-region | Meckenheim | `meckenheim` | `python` |
| bonn-region | Meetup | `meetup` | `python` |
| bonn-region | Melan Märkte | `melan-m-rkte` | `python` |
| bonn-region | Much | `much` | `python` |
| bonn-region | Municipal MEC markets | `municipal-mec-markets` | `python` |
| bonn-region | Museum Koenig Bonn | `museum-koenig-bonn` | `python` |
| bonn-region | Naturregion Sieg | `naturregion-sieg` | `python` |
| bonn-region | Okken Märkte | `okken-m-rkte` | `python` |
| bonn-region | Parkbuchhandlung | `parkbuchhandlung` | `python` |
| bonn-region | Radio Bonn/Rhein-Sieg | `radio-bonn-rhein-sieg` | `python` |
| bonn-region | Redüttchen | `red-ttchen` | `python` |
| bonn-region | Regional HTML calendars | `regional-html-calendars` | `python` |
| bonn-region | Regional venues | `regional-venues` | `python` |
| bonn-region | Requested venue calendars | `requested-venue-calendars` | `python` |
| bonn-region | Rhein Antik | `rhein-antik` | `python` |
| bonn-region | Rheinauen-Flohmarkt | `rheinauen-flohmarkt` | `python` |
| bonn-region | Rheinbach Flohmarkt | `rheinbach-flohmarkt` | `python` |
| bonn-region | RheinEvents | `rheinevents` | `python` |
| bonn-region | Ruhr-Guide | `ruhr-guide` | `python` |
| bonn-region | Salsa in Bonn | `salsa-in-bonn` | `python` |
| bonn-region | Siegburg | `siegburg` | `python` |
| bonn-region | SiteKit regional | `sitekit-regional` | `python` |
| bonn-region | Standard regional feeds | `standard-regional-feeds` | `python` |
| bonn-region | Street Food Bonn | `street-food-bonn` | `python` |
| bonn-region | Street Food Festival Original | `street-food-festival-original` | `python` |
| bonn-region | Swisttal | `swisttal` | `html` |
| bonn-region | Tanzschule Max7 | `tanzschule-max7` | `python` |
| bonn-region | Theater Bonn | `theater-bonn` | `python` |
| bonn-region | Theater im Ballsaal | `theater-im-ballsaal` | `python` |
| bonn-region | Theater Marabu | `theater-marabu` | `python` |
| bonn-region | TiK Theater im Keller | `tik-theater-im-keller` | `python` |
| bonn-region | Troisdorf | `troisdorf` | `ical` |
| bonn-region | Universität Bonn | `uni-bonn` | `python` |
| bonn-region | vomFASS Bonn | `vomfass-bonn` | `python` |
| bonn-region | VVS Siebengebirge | `vvs-siebengebirge` | `python` |
| bonn-region | Wachtberg | `wachtberg` | `ical` |
| bonn-region | Waldbröl | `waldbroel` | `ical` |
<!-- END GENERATED SOURCES -->

- **Offizielle strukturierte Daten:** Köln Open Data (`koeln.py`), der primäre
  Bonn.de-Kalender (`bonn.py`) und der Veranstaltungskalender der Universität
  Bonn mit iCal-Feed und gecachten Detailseiten (`uni_bonn.py`).
- **Bonn.de-Ergänzungen:** Sportveranstaltungen sowie das jährliche
  „Veranstaltungsjahr“ mit Stadtteilfesten, Kirmes, Märkten und lokalen Terminen
  (`bonn.py`).
- **iCal / RFC 5545:** Harmonie Bonn, Siegburg, Troisdorf, Wachtberg und kuratierte
  Bonn-area Meetup-Gruppen.
- **JSON-LD / schema.org:** Rheinauen-Flohmarkt, Kinderflohmarkt.com,
  VVS Siebengebirge, Hennef und
  weitere seitennahe Eventdaten, wenn Quellen strukturierte Eventobjekte anbieten.
- **Direkte Marktveranstalter:** Grote & Hiller, Hofflohmärkte Köln und Cölln
  Konzept liefern Termine, Uhrzeiten, Orte und direkte Veranstaltungsseiten.
  **Rhein Antik** (`rhein_antik.py`) ist der Veranstalter der Antik-, Kunst- und
  Designmärkte auf dem Bonner Friedensplatz sowie in Siegburg, Königswinter und
  Bad Honnef; diese Termine kamen vorher nur zweiter Hand über einen Kölner
  Marktbetreiber und den Bonner Pressekalender herein.
  **Cölln Antik&Design** (`coelln_antik_design.py`) ist ein anderer Betreiber als
  das bereits integrierte Cölln Konzept und bespielt andere Orte: Kölner Flora,
  Gürzenich, Neumarkt, Maternusplatz Rodenkirchen und Rheinauhafen. Die Seite ist
  handgepflegt, entsprechend locker sind die Datumsangaben (Feiertagsnamen,
  mehrere Tage per `+` oder `und`, Monat nur einmal am Ende). Unplausible
  Jahreszahlen aus Tippfehlern werden als erwarteter Qualitätsausschluss
  verworfen, nicht repariert und degradieren keinen ansonsten gesunden Lauf.
- **Kuratierte Kino-Sonderformate:** Bonner Kinemathek,
  Rex-Lichtspieltheater/Neue Filmbühne, Internationale Stummfilmtage, Filmhaus Köln,
  Kurzfilmwanderung Bonn und das saisonale Open-Air-Kino im Rüngsdorfer
  Kulturbad. Breite Kinoprogramme werden nur bei expliziten Festival-, Open-Air-,
  Preview-, Gesprächs-, Workshop- oder vergleichbaren Event-Markierungen
  übernommen (`cinema_specials.py`); reguläre Vorstellungen bleiben draußen.
- **Food & Genuss in Bonn:** Craftquelle, BFF Bonner Schifffahrt, vomFASS,
  Biertasting Bonn, Ludwig's, Redüttchen, Street Food Bonn, Street Food Festival
  „Das Original“ und Choco Dealer liefern kuratierte
  Primärtermine mit Detailseiten-Anreicherung (`bonn_food.py`).
  **Street Food Bonn** liest beide Landingpages desselben Veranstalters
  (WEvent UG): `street-food-bonn.de` und `streetfood-siegburg.de`. Beide bewerben
  teils dieselben Festivals in leicht abweichender Datumsschreibweise; ein
  gemeinsamer Parser plus die bestehende Entdopplung führt sie zusammen.
  **Street Food Festival „Das Original“** (`street-food-festival.de/bonn`) ist ein
  anderer Veranstalter. Seine `schema.org/FoodEvent`-Auszeichnung wird gepflegt
  nicht mehr: `startDate` zeigt auf eine vergangene Ausgabe und ist kein gültiges
  ISO 8601. Aus dem JSON-LD werden deshalb nur Name, Beschreibung und Ort
  übernommen; **das Datum stammt ausschließlich aus dem sichtbaren HTML**. Ohne
  lesbares Datum im Seitentext wird nichts veröffentlicht und die Quelle meldet
  einen leeren Parse.
  **Choco Dealer** (Bad Godesberg) betreibt eine eigene Buchungsplattform ohne
  JSON-LD; die Terminkarten der Events-Kategorie werden direkt aus dem HTML
  gelesen. Wenn der direkte Buchungspfad eine Tasting-Art ausweist, die
  Marketing-Überschrift aber nicht, ergänzt der Adapter diese sachliche
  Bezeichnung. So kann die allgemeine Titelähnlichkeitsprüfung beispielsweise
  ein parallel bei Radio Bonn/Rhein-Sieg gelistetes „Schokoladentasting“
  zusammenführen, ohne Eventnamen oder Termine hart zu codieren.
- **Kommunale und regionale Kalender:** Königswinter, Meckenheim, Much,
  Naturregion Sieg, IONAS4-Quellen, SiteKit-Kalender, Standard-Feeds,
  regionale HTML-Kalender, Tourismus-/Deskline-Kalender, regionale Venue-Kalender
  und explizit angefragte Bonn/Rhein-Sieg-Spielstätten.
- **Kabarett & Comedy:** Kabarett, Comedy, Impro, Poetry Slam und Kleinkunst
  bleiben wegen der derzeit kleinen Terminmenge Teil von „Theater & Bühne",
  statt eine dünn besetzte eigene Kategorie zu bilden. Pantheon und Haus der
  Springmaus geben passende Genre-Hinweise mit; der Brotfabrik-Feed nutzt sein
  eigenes `Gewerk`-Feld je Termin.
- **Bonner Stadtbezirke:** `make_event` löst ein bloßes „Bonn" zentral über die
  Postleitzahl im Veranstaltungsort auf (Bad Godesberg, Beuel, Hardtberg). Die
  zentralen Postleitzahlen bleiben „Bonn", weil der zentrale Stadtbezirk selbst
  so heißt.
- **Kultur, Nachtleben und NRW-weite Ergänzungen:** Bundeskunsthalle, Bonn.jetzt,
  Tanzschule Max7, AfterJobParty Bonn, RheinEvents, Salsa in Bonn und Ruhr-Guide.
- **Stadtteilfeste und Bonner Großveranstaltungen:** Bürgerverein Vilich-Müldorf,
  Beuel.net, Bad Godesberg Stadtmarketing, Hardtberg Kultur, BSV Roleber und
  BV Holzlar (`bonn_districts.py`) decken die Vereins- und Ortsfeste ab; der
  Bonner Termin von Rhein in Flammen kommt über `bonn_venues.py`.
- **Literatur in Bonn:** Literaturhaus Bonn (iCal) und Parkbuchhandlung Bad
  Godesberg liefern Autorenlesungen, Buchpremieren und Literaturgespräche
  (`bonn_literature.py`). Stehende Lesekreise und Literaturkreise werden
  bewusst nicht veröffentlicht — sie sind feste Gruppentreffen, keine
  besuchbaren Termine. Kuratierte Reihen, die das besprochene Werk im Titel
  nennen, bleiben erhalten.
- **Theater und Bühne:** Theater Bonn, Junges Theater Bonn, Kleines Theater Bad
  Godesberg, Theater Marabu, Theater im Ballsaal und TiK Theater im Keller.
- **Kommunale MEC-Kalender (Marktausläufer):** Hennef und Sankt Augustin fahren
  WordPress mit Modern Events Calendar (`mec_municipal.py`). Die
  `mec-events`-REST-Kategorie erreicht den kompletten Marktausläufer, den die
  öffentliche Kalenderseite nicht rendert — bei Hennef die Hof-, Garagen-, Dorf-
  und Gassenflohmärkte, die Monate voraus liegen. Der REST-Payload enthält **kein**
  Eventdatum, deshalb kommt das Datum aus dem autoritativen Ein-Event-iCal
  (`?method=ical&id=`). Weil das einen Abruf pro Event bedeutet, werden Kandidaten
  zuerst per Titel auf Second-Hand-Formate eingegrenzt und jeder Kalenderabruf
  läuft durch den persistenten TTL-Cache.
- **Marktverzeichnis nach Format:** marktcom (`marktcom.py`) ist das einzige
  Verzeichnis, dessen Suche gleichzeitig über einen Radius um Koordinaten *und*
  über das Marktformat adressierbar ist. Angefragt werden nur Second-Hand-Formate
  (`WANTED_CATEGORIES`); `Wochenmarkt`, `Garten-/Pflanzenmarkt` und
  `Tiere und Zubehör` werden nie abgefragt, statt sie hinterher wieder
  herauszufiltern. Es werden ausschließlich Listenseiten geladen, keine
  Detailseiten, und die Paginierung endet bei der ersten Seite jenseits des
  Berichtsfensters — ein kurzes Fenster kostet also einen Request pro Format.
  Datensätze von Veranstaltern, die wir bereits direkt lesen, werden verworfen.
- **Websuche als Fallback:** Exa standardmäßig, Grok nur mit
  `NRW_EVENTS_ENABLE_GROK=1` (`search.py`).

### Quellenautorität bei Duplikaten

`report.source_authority` entscheidet, welcher Publisher den kanonischen Datensatz
besitzt, wenn zwei Quellen dieselbe Veranstaltung melden. Die Stufen sind:

| Stufe | Quellenart |
|-------|------------|
| `3`   | Direkte Veranstalter und kommunale Kalender (Standard) |
| `2`   | Civic-Aggregatoren (`bonn.de`) |
| `1`   | Aggregatoren und **fremde Marktverzeichnisse** |
| `0`   | Websuche (Exa, Grok) |

Marktverzeichnisse listen die Termine der Veranstalter erneut und servieren dabei
auch Termine weiter, die der Veranstalter bereits abgesagt hat. Sie stehen deshalb
bewusst auf Stufe `1` und dürfen ein Dedup-Duell gegen den Veranstalter nie
gewinnen — unabhängig vom Score. Metadaten des Verzeichnisses ergänzen den
Gewinner-Datensatz trotzdem feldweise.

`krencky24.de`, `meine-flohmarkt-termine.de` und `meine-kunsthandwerker-termine.de`
gehören einem Betreiber (Kampagne Spezial GmbH) und liefern eine gemeinsame
Datenbank von einem Host. Sie listen sich gegenseitig; sinnvoll ist genau ein
Frontend als Quelle.

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
2. Standard-iCal/JSON-LD: einen `SourceSpec` in `sources/__init__.py` und einen
   Vertragstest in `tests/sources/parser_cases.py` ergänzen.
3. Nur für proprietäre Formate ein Modul mit `fetch()` schreiben und es in
   `CUSTOM_SOURCES` registrieren; dabei die gemeinsamen Parser verwenden.
4. Neue Orte in `config.VENUE_COORDS` ergänzen, damit die Distanzwertung stimmt.

Für Meetup-Gruppen: `config.MEETUP_GROUPS` bearbeiten. Öffentliche iCal-Feeds
liegen unter `https://www.meetup.com/<slug>/events/ical/`.

## Ausgabe

- **Markdown auf stdout:** Kategorien, Eventname, Datum/Zeit, Ort, Distanz,
  Bewertung, Beschreibung und Link.
- **JSON im Benutzer-State-Verzeichnis:** vollständige deduplizierte und
  bewertete Eventliste als Top-Level-Array. Dieser Vertrag bleibt stabil für
  einfache Weiterverarbeitung.
- **Metadaten-JSON daneben:** Zeitfenster,
  Radius, Score-Schwelle, Roh-Zählungen je Quelle, hart fehlgeschlagene Quellen,
  weiche Quellenwarnungen, eine kompakte analysierbare Problemliste
  (`import_issues`), den detaillierten Status jeder Quelle (`source_results`),
  stabile Kategorie-Taxonomie und einen `events_path` auf die Eventliste. Der Laufstatus ist
  `healthy`, `degraded` oder `failed`; einzelne fehlgeschlagene/degradierte
  Quellen werden als `degraded` veröffentlicht und beenden den Prozess mit Exit 0,
  solange der Lauf weiterhin Events erzeugt. Wenn `NRW_EVENTS_PREVIOUS_META_JSON`
  auf einen dauerhaften vorherigen Metadaten-Snapshot zeigt, behält ein degradierter
  Lauf außerdem nicht abgelaufene Events vorübergehend unerreichbarer Quellen bei.
  Planmäßig nur wöchentlich aktualisierte Quellen werden an den übrigen Tagen als
  `scheduled_skip` geführt und behalten ebenfalls nur ihre noch nicht abgelaufenen Events.
  Frische Quelldaten gewinnen bei der Deduplizierung; abgelaufene Cache-Events werden
  entfernt. `fresh_event_count`, `retained_event_count`,
  `expired_retained_event_count` und `retained_sources` dokumentieren die Entscheidung.
  Erfolgreiche leere Quellen ersetzen ihren bisherigen Snapshot; nur Fehler,
  Parser-Leerstände, planmäßige Auslassungen und auffällige Nullergebnisse lösen
  die Aufbewahrung aus.
  `failed` bleibt für Läufe ohne veröffentlichbare Events oder
  Infrastruktur-/Konfigurationsfehler reserviert.

Der lokale Canary ruft die öffentlichen Quellen live ab und vergleicht ihre
Rohzählungen mit dem letzten gesunden Metadaten-Snapshot. Er schreibt bei
`degraded`, `failed`, `parser_empty` oder einer Baseline-Anomalie einen
Markdown-Bericht und beendet sich mit Status 1. GitHub Actions sind für dieses
Repository deaktiviert; der Canary wird daher bewusst lokal oder über einen
eigenen Scheduler ausgeführt:

```bash
bash scripts/run_canary.sh
# optionales persistentes Zustandsverzeichnis
bash scripts/run_canary.sh /pfad/zum/canary-state
```

Parser-Fixtures lassen sich ausschließlich für die in
`tests/fixtures/manifest.json` allowlisteten URLs aktualisieren:

```bash
python scripts/refresh_fixtures.py --source uni-bonn
python scripts/refresh_fixtures.py --source uni-bonn --dry-run
```

Standardmäßig wird die vollständige Liste ausgegeben. Gekürzt wird nur, wenn
`NRW_EVENTS_MAX_PER_SECTION` explizit gesetzt wird.

## Anpassung

Häufige Anpassungen:

- `config.CATEGORY_WEIGHT` — Ranking an eigene Interessen anpassen.
- `config.BONN_LAT`, `config.BONN_LON`, `MAX_RADIUS_KM` — Suchmittelpunkt/Radius ändern.
- `config.VENUE_COORDS` — Orte für genauere Distanzwerte ergänzen.
- `sources/__init__.py` — Quellen hinzufügen oder entfernen.

## Entwicklung und Qualitätssicherung

Die Laufzeit selbst braucht keine Drittanbieter-Pakete. Der kanonische lokale
Testlauf ist:

```bash
bash scripts/test.sh

# einzelnes Modul
bash scripts/test.sh tests.test_report
```

Die optionalen Entwicklungswerkzeuge werden gemeinsam installiert und ebenfalls
lokal ausgeführt; GitHub Actions sind deaktiviert:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/ruff check .
.venv/bin/mypy
PATH="$PWD/.venv/bin:$PATH" NRW_EVENTS_COVERAGE=1 bash scripts/test.sh
.venv/bin/python scripts/check_env_docs.py
.venv/bin/python scripts/generate_docs.py --check
```

Schneller Smoke-Test ohne echte Ausgabedateien im Repo:

```bash
tmpdir=$(mktemp -d)
NRW_EVENTS_JSON_OUT="$tmpdir/events.json" \
NRW_EVENTS_META_JSON_OUT="$tmpdir/meta.json" \
python3 scripts/nrw-events.py 3 >/tmp/nrw-events-smoke.md
python3 - "$tmpdir/meta.json" <<'PY'
import json, sys
meta = json.load(open(sys.argv[1]))
print(meta["event_count"])
print(meta["source_counts_raw"])
print(meta.get("source_warnings", []))
print(meta.get("import_issues", []))
PY
```

Ein erfolgreicher Lauf ist nicht nur ein Exit-Code: Prüfe auch `event_count`,
wichtige Quellenzählungen, `source_warnings` und `import_issues`, weil einzelne
öffentliche Seiten degradiert sein können, ohne den Gesamtlauf zu stoppen.

### Geprüfte Venue-Koordinaten

Der Venue-Geocoding-Pfad ist ein reproduzierbarer, redaktionell geprüfter
Offline-Workflow; während eines Imports werden keine Geocoder aufgerufen:

```bash
# 1. Noch nicht aufgelöste Venue-Gruppen aus einem Feed-Snapshot erfassen
make venue-audit VENUE_FEED=/path/to/events-with-metadata.json \
  VENUE_AUDIT=/tmp/venue-audit.json

# 2. Kandidaten mit persistenten Caches recherchieren
python3 scripts/research_venue_geocoding.py /tmp/venue-audit.json \
  --cache /path/to/nominatim-cache.json \
  --photon-cache /path/to/photon-cache.json \
  --output /tmp/venue-proposals.json

# 3. Reviewte Vorschläge bauen; manuelle/rejected Entscheidungen stehen in
#    scripts/venue_geocoding_decisions.json
python3 scripts/build_verified_venue_locations.py /tmp/venue-proposals.json \
  --registry scripts/nrw_events/verified_venue_locations.json \
  --decisions /tmp/venue-decisions.json

# CI-Reproduzierbarkeitsprüfung gegen den versionierten Vorschlagsstand
make venue-registry-check
```

`checkedAt` bleibt für unveränderte Einträge stabil und ändert sich nur, wenn
sich Koordinaten, Adresse, Aliase oder Evidenz ändern.

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
