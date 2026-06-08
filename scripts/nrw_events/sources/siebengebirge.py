"""
Siebengebirge — outdoor / nature / guided-hike source near Bonn.

fetch_vvs() — Verschönerungsverein Siebengebirge guided hikes & nature days
              (vv-siebengebirge.de, schema.org JSON-LD Events).

(A second source, Tourismus Siebengebirge / siebengebirge.com, was dropped: its
"Veranstaltungen aktuell" page only ever exposed a stale past-season list and
yielded nothing forward-looking.)
"""

from .. import common


def fetch_vvs() -> list:
    source = "VVS Siebengebirge"
    url = "https://www.vv-siebengebirge.de/veranstaltungen/"
    try:
        html = common.fetch_url(url, timeout=25)
        return common.events_from_jsonld(
            html, source, "Königswinter",
            "siebengebirge wanderung natur outdoor führung", 1.1, url)
    except Exception as e:
        common.log_source_error(source, e)
        return []
