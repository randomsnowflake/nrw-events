"""
Troisdorf — official town calendar iCal export (~10 km from Bonn).

The visible calendar is an IONAS TVM widget; its page exposes a stable iCal
export, which is less fragile than scraping the widget's generated HTML.
"""

from .. import common

_ICS_URL = "https://www.troisdorf.de/de/kalender/startseite/event.ics?weekends=false&tagMode=ANY"


def fetch() -> list:
    source = "Troisdorf"
    try:
        return common.fetch_ical(_ICS_URL, source, "Troisdorf", "troisdorf lokal kultur markt", 0.95)
    except Exception as e:
        common.log_source_error(source, e)
        return []
