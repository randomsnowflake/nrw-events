# Pantheon / Springmaus source audit: 2026-11-08

## Result and decision

Both calendars intermittently fail during the TLS handshake from the importer
host. Their programme HTML still exists and the unchanged parsers read it.
There is no demonstrated parser defect, empty programme or permanent IPv4-only
failure to repair. Keep the adapters, bounded retries and outage retention as
implemented. This PR records the diagnosis; it does **not** restore ingestion.

An IPv6-first transport experiment was discarded: later probes failed on IPv6
too, and Python already resolved IPv6 first. Do not introduce a global resolver
change, disable certificate checks, increase all-source timeouts or silently add
a recurring paid proxy on the strength of one successful probe.

## Exact alert and import

The Mattermost alert at 04:43:40 CET on 8 November named
`haus-der-springmaus` and `pantheon-bonn`. Evidence is the frozen release
`20261108T043303-9de38516`, not a later import:

- Website revision: `2c2e598b313879817a38339e7ee7d73c7eaa7830`.
- Importer revision: `48b1e1e58e060b39bd3a5285b6e3441cc6867c80` (also this PR's base).
- Import run: `7f15f201d7ca4d72a8f84cb20249bcd2`, generated
  `2026-11-08T04:40:13+01:00`, window **8 November–5 December 2026** inclusive.
- Evidence: `content/original/nrw-events-meta.json` and
  `content/original/website-events.json` inside that prepared release; morning
  ledger `~/.local/state/veranstaltungen-bonn/morning/2026-11-08.json`.

| Child | Listing endpoint | Endpoint result | Retained records before final deduplication |
| --- | --- | --- | --- |
| `pantheon-bonn` | `https://www.pantheon.de/programm/` | 2 attempts; `URLError`, TLS handshake timed out | 17 |
| `haus-der-springmaus` | `https://www.springmaus-theater.de/events.html?s=all` | 2 attempts; `URLError`, TLS handshake timed out | 22 |

Both child counts became zero while Sankt Augustin and Brückenforum still
returned HTTP 200 and parsed 11 and 60 records respectively. The grouped
adapter therefore remained `degraded`, not entirely failed. The source warning
`request or source time budget exhausted before retry` is the terminal budget
message; the endpoint evidence identifies the earlier TLS failure. No HTML was
received for these endpoints during that import.

The retained-source ledger records last success **7 November 04:40:13 CET**,
first failure **8 November 04:39:50 CET** and `consecutive_failures: 1` for both
children. The generic alert introduction (“Seit mindestens zwei Läufen …”) is
not evidence of two consecutive failed imports here; two endpoint attempts are
not two import runs. The older October incident in #429 must not be treated as
an uninterrupted outage extending into November.

## Bounded public probes and offline replay

Read-only probes from the VPS at approximately **05:22–05:31 CET**:

1. Ordinary Python/urllib requests timed out in the TLS handshake. Both domains
   resolved to the same addresses: IPv4 `138.199.193.172` and IPv6
   `2a01:4f8:1c1b:bb63::1`.
2. IPv4 curl probes timed out. IPv6 curl then returned HTTP 200 for both full
   listings: Pantheon in 0.130 seconds, Springmaus in 1.074 seconds. The bodies
   were saved once and replayed locally with the unchanged owning parsers.
3. Later IPv6 curl and Python probes also timed out, including TLS 1.2 and
   ordinary TLS negotiation. Python's normal `getaddrinfo` result already put
   IPv6 first. The successful fetches therefore establish intermittent
   availability, **not** a reliable IPv6 repair or sustained source recovery.
4. Independent `web_extract` retrieval also returned current programmes for
   both sources. It does not establish that the importer's direct path works.

| Saved body | Bytes | Unchanged parser records | Records dated within the import window |
| --- | ---: | ---: | ---: |
| Pantheon | 780783 | 149 | 28 |
| Springmaus | 416338 | 141 | 28 |

These are raw parser records, not accepted or published event totals. No full
import, detail enrichment or publication was run. Pantheon's replay starts
with “Rosemie Warth - ...sonst nix”, 8 November 20:00
(`/programm/#t568671`); Springmaus starts with “Fischer & Jung - Die Herren der
Erschöpfung”, 8 November 19:00 (`events/fischer-jung-die-herren-der-erschoepfung-1714.html`).
The old empty Pantheon listing described in #429 was **not** the body observed
in this investigation.

Saved-body SHA-256 identifiers:

- Pantheon: `b8d950e12e4e7f6944aa4e0d15683f7edaf711cd9b64fced0776a8a34b340a5c`.
- Springmaus: `3ddc517c21a4f70f714d837105213d12ce74caa586d9963d5f045c516fa47d00`.

Bodies remain in the investigating profile's temporary scratch directory
`springmaus-pantheon-20261108/{pantheon,springmaus}.html`, not in Git or a
production cache. They are temporary evidence, not durable test fixtures.
Replay uses `http._decode_body(..., 'utf-8')` followed by
`requested_venues._events_from_pantheon` / `_events_from_springmaus`, with
`common.WINDOW_START = datetime(2026, 11, 8)` and
`common.WINDOW_END = datetime(2026, 12, 5)`. Count the inclusive date window
separately from the returned list length.

## Coverage, risk and next decision

The captured original importer feed still contains 20 Pantheon-venue records
(16 `pantheon-bonn`, 4 `bonn-de-events`) and 23 Springmaus-venue records
(21 `haus-der-springmaus`, 2 `bonn-de-events`). These are post-deduplication
feed counts, hence different from the retention ledger above. They describe
this captured generation, not a separate live-site verification. Retention
preserves known dates but cannot discover additions or reliably observe changes.

`retention_policy.py` expires outage-retained records after seven elapsed days
from the first failure (or earlier as their event dates pass). If this episode
continues without a successful import, that grace reaches its limit on
**15 November at 04:39:50 CET**. This is an expiry threshold, not a prediction
of when the next import will execute.

The precise remote cause remains unknown: shared-host trouble, routing or
source-IP filtering cannot be distinguished by these probes. The smallest next
step is to check the next scheduled import's exact child endpoint telemetry.
If failures persist, establish a consistently working first-party path or
separately approve a narrowly scoped proxy fallback and its recurring cost.
Do not close the incident merely because a one-off fetch succeeds.

## Scope

Documentation only. No parser, HTTP transport, registry, tests, exports,
configuration, website pin or production state changed. No import rerun, merge
or deploy; no credentials or extra dependency introduced.
