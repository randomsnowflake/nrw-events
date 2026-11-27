# Market source suspensions — 2026-11-15

Update 2026-11-27: LuPe now uses the public Tribe REST calendar. Both iCal
URLs still return zero bytes. REST returned the 2026 Ippendorf and Endenich
records and a valid empty response for 2027. The adapter preserves LuPe's
identity and interprets its 05:00 all-day boundary as a calendar cutoff.
Rhein Antik remains suspended.

Weekly report action 3 reported 162 failures for `rhein-antik` (HTTP 503)
and 28 for `lupe-events` (empty parser). Both identities and their existing
parsers remain registered. A nonempty `disabled_reason` now makes the production
`SOURCE_FETCHERS` entry return the existing typed `disabled` outcome before
constructing or invoking an adapter. This is not a healthy empty calendar.
The legacy `SOURCES` map and direct parser functions remain available for offline
revalidation; operational callers must use `SOURCE_FETCHERS` (as the CLI does).

## Primary evidence

Bounded, read-only HTTPS GETs on 2026-11-15, approximately 04:16–04:17 UTC,
with a 20-second timeout and a 1,500,000-byte response cap per request. No
all-source import, paid service, or guessed alternate hostname/path was used.
The following seven requests completed without reaching the body cap:

| Requested primary URL | Observed result |
|---|---|
| <https://rhein-antik.de/termine/> | HTTP 503, 2,557 bytes, HTML title and heading `Maintenance`; message `Please check back soon.` |
| <https://rhein-antik.de/> | HTTP 503, same maintenance body; no replacement schedule link |
| <https://lupe-events.de/events?ical=1> | HTTP 200, `text/html; charset=UTF-8`, zero bytes; not a VCALENDAR |
| <https://lupe-events.de/> | HTTP 200, 196,159 bytes; advertises the calendar and subscription URLs below; says `0 Veranstaltungen gefunden.` and `Es sind keine anstehenden Veranstaltungen vorhanden.` |
| <https://lupe-events.de/events/?ical=1> | Advertised on homepage; redirects to the existing `/events?ical=1` endpoint, HTTP 200, zero bytes |
| <https://lupe-events.de/?post_type=tribe_events&ical=1&eventDisplay=list> | Advertised homepage webcal subscription, read over HTTPS; HTTP 200, zero bytes |
| <https://lupe-events.de/veranstaltungskalender> | Linked from homepage; HTTP 200, 162,013 bytes; also explicitly lists no upcoming events |

## Decision and reactivation

Suspend both sources explicitly until their upstream contracts can be revalidated.
Rhein Antik's response supports maintenance, **not permanent closure**. The repeated
failures and absence of any replacement link do not justify a hostname change or
parser rewrite. Keep its existing organizer URL and parser intact.

LuPe's public website is available, but its advertised exports are not valid iCal.
The current no-upcoming-events notice explains the lack of visible records; it
does not establish that an empty HTTP body is always a valid calendar. Do not
weaken shared empty-calendar validation, synthesize a VCALENDAR, or infer future
events. No alternate adapter is justified by the currently empty listings.

To reactivate, verify a restored primary endpoint with a bounded request, add an
exact response fixture with successful identity/provenance and error-path tests,
then remove that source's `disabled_reason` and regenerate the inventories. If the
restored feed legitimately has no events, it must still be a valid calendar.
Changing a source URL requires primary evidence. No automatic retry date is set.

## Rollout limits

This change is an importer-only local commit. It has no production effect until
reviewed, published, and explicitly pinned/activated by the website release
workflow. Publish the importer commit before changing a consumer pin. Do not
repeat a production import after a prepared package activates.

Disabled sources do not fetch upstream, do not produce new records, and do not
qualify for failure-based previous-event retention. Thus a later activated import
can drop records previously retained solely for these failing sources. Other
sources and their records remain subject to the existing publication policy.
No ledger, cron, consumer, shared deduplication code, or production state is changed.
