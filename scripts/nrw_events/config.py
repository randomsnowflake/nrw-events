"""
Static configuration for the NRW event aggregator.

Everything here is *reference data* — geography, opinionated category weights,
and source lists. There are deliberately **no event names and no dates** in this
file. Tune these values to recenter the tool or change ranking to your taste.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class RuntimeConfig:
    days_ahead: int = 3
    score_floor: float = 0.4
    http_retry_attempts: int = 5
    http_retry_base_seconds: float = 1.0
    bonn_de_delay_seconds: float = 2.0
    json_out: str = "/tmp/nrw-events-latest.json"
    meta_json_out: str = "/tmp/nrw-events-latest-meta.json"
    log_level: str = "INFO"
    log_file: str = ""
    json_log_file: str = ""


def load_env_file() -> Optional[str]:
    """Load the first configured env file while preserving real environment values."""
    repo_root = Path(__file__).resolve().parents[2]
    for candidate in (os.environ.get("NRW_EVENTS_ENV_FILE", ""), repo_root / ".env", Path.cwd() / ".env"):
        path = Path(candidate).expanduser() if candidate else None
        if not path or not path.is_file():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].lstrip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = value
        return str(path)
    return None


def _int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}, got {value}")
    return value


def _float(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = os.environ.get(name, str(default))
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}, got {value}")
    return value


def runtime_config(days_ahead: Optional[int] = None) -> RuntimeConfig:
    """Return validated settings after :func:`load_env_file` has run."""
    configured_days = _int("NRW_EVENTS_DAYS_AHEAD", 3, 1, 90)
    if days_ahead is not None:
        if not 1 <= days_ahead <= 90:
            raise ValueError("days_ahead must be between 1 and 90")
        configured_days = days_ahead
    level = os.environ.get("NRW_EVENTS_LOG_LEVEL", "INFO").upper()
    if level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        raise ValueError("NRW_EVENTS_LOG_LEVEL must be DEBUG, INFO, WARNING, or ERROR")
    return RuntimeConfig(
        days_ahead=configured_days,
        score_floor=_float("NRW_EVENTS_SCORE_FLOOR", 0.4, 0.0, 10.0),
        http_retry_attempts=_int("NRW_EVENTS_HTTP_RETRY_ATTEMPTS", 5, 1, 10),
        http_retry_base_seconds=_float("NRW_EVENTS_HTTP_RETRY_BASE_SECONDS", 1.0, 0.0, 60.0),
        bonn_de_delay_seconds=_float("NRW_EVENTS_BONN_DE_DELAY_SECONDS", 2.0, 0.0, 60.0),
        json_out=os.environ.get("NRW_EVENTS_JSON_OUT", "/tmp/nrw-events-latest.json"),
        meta_json_out=os.environ.get("NRW_EVENTS_META_JSON_OUT", "/tmp/nrw-events-latest-meta.json"),
        log_level=level,
        log_file=os.environ.get("NRW_EVENTS_LOG_FILE", ""),
        json_log_file=os.environ.get("NRW_EVENTS_JSON_LOG_FILE", ""),
    )

# ── Geography ───────────────────────────────────────────────────────
# Center point + search radius. Recenter the whole tool by editing these.
BONN_LAT, BONN_LON = 50.7374, 7.0982
MAX_RADIUS_KM = 75

# ── Category preference weights ─────────────────────────────────────
# Keyword → ranking multiplier. Higher = surfaced more prominently.
# These are opinionated defaults (culture + outdoor + nightlife). Edit freely.
# NOTE: keys are matched as substrings against lowercased event text.
CATEGORY_WEIGHT = {
    # music / nightlife
    "concert": 1.5, "konzert": 1.5, "musik": 1.5, "music": 1.5, "live": 1.4,
    "electronic": 1.8, "techno": 1.8, "nightlife": 1.3, "party": 1.2, "club": 1.3,
    # art / culture
    "exhibition": 1.4, "ausstellung": 1.4, "museum": 1.3, "vernissage": 1.35,
    "architecture": 1.6, "art": 1.3, "kunst": 1.3, "film": 1.0, "theater": 1.0,
    "comedy": 1.0, "lecture": 1.1, "vortrag": 1.1, "lesung": 1.1,
    # outdoor / nature
    "outdoor": 1.35, "hiking": 1.35, "wandern": 1.35, "wanderung": 1.35,
    "geführte wanderung": 1.45, "führung": 1.25, "tour": 1.2, "rundgang": 1.2,
    "nature": 1.25, "natur": 1.35, "rhein": 1.15, "walk": 1.25,
    # wine / food
    "wein": 1.45, "wine": 1.45, "winzer": 1.4, "weinwanderung": 1.55,
    "weinfest": 1.45, "weinprobe": 1.4, "genuss": 1.25, "food": 1.2,
    "street food": 1.3, "kulinar": 1.25,
    # local festivals / markets
    "market": 1.1, "markt": 1.1, "flohmarkt": 1.2, "festival": 1.4,
    "stadtteilfest": 1.45, "straßenfest": 1.45, "strassenfest": 1.45,
    "dorffest": 1.35, "kirmes": 1.25, "meile": 1.25, "weinmeile": 1.55,
    "genussmeile": 1.45, "viertel": 1.2,
    # place affinity (near + scenic)
    "drachenfels": 1.4, "petersberg": 1.3, "ölberg": 1.3, "heisterbach": 1.25,
    "siebengebirge": 1.35, "kottenforst": 1.3, "königswinter": 1.3,
    "ahrtal": 1.35, "ahrweiler": 1.3, "dernau": 1.25, "mayschoss": 1.25,
    "poppelsdorf": 1.35, "endenich": 1.25, "beuel": 1.2, "bad godesberg": 1.2,
    # de-prioritized (kids-only / classes)
    "kids": 0.2, "kinder": 0.2, "family": 0.3, "familie": 0.3,
    "sport": 0.5, "workshop": 0.7, "reading": 0.4, "vorlesen": 0.3, "basteln": 0.2,
}

# ── Known venue / town coordinates ──────────────────────────────────
# Saves geocoding and keeps distance scoring accurate. Add a town here when you
# wire in a new regional source.
VENUE_COORDS = {
    "bonn": (50.7374, 7.0982),
    "köln": (50.9375, 6.9603), "koeln": (50.9375, 6.9603), "cologne": (50.9375, 6.9603),
    "siegburg": (50.7972, 7.2028), "troisdorf": (50.8157, 7.1554),
    "königswinter": (50.6741, 7.1844), "koenigswinter": (50.6741, 7.1844),
    "bad honnef": (50.6452, 7.2278), "sankt augustin": (50.7705, 7.1867),
    "remagen": (50.5741, 7.2290), "düsseldorf": (51.2277, 6.7735),
    "aachen": (50.7753, 6.0839), "leverkusen": (51.0459, 6.9844),
    "koblenz": (50.3569, 7.5890), "bornheim": (50.7577, 6.9987),
    "meckenheim": (50.6314, 7.0289), "rheinbach": (50.6255, 6.9499),
    "hennef": (50.7752, 7.2836), "lohmar": (50.8377, 7.2136),
    "much": (50.9025, 7.4021), "eitorf": (50.7696, 7.4524), "brühl": (50.8282, 6.9063),
    "bruehl": (50.8282, 6.9063), "wesseling": (50.8271, 6.9747),
    "neunkirchen-seelscheid": (50.8648, 7.3364), "grafschaft": (50.5728, 7.0876),
    "bad münstereifel": (50.5568, 6.7646), "bad muenstereifel": (50.5568, 6.7646),
    "euskirchen": (50.6606, 6.7872), "ruppichteroth": (50.8436, 7.4847),
    "windeck": (50.7978, 7.5726), "herchen": (50.7836, 7.5160),
    "rosbach": (50.8039, 7.6087), "wissen": (50.7792, 7.7342),
    "steinebach/sieg": (50.7390, 7.8152), "steinebach": (50.7390, 7.8152),
    "wachtberg": (50.6262, 7.1167), "alfter": (50.7339, 7.0011),
    "swisttal": (50.6300, 6.8870), "niederkassel": (50.8136, 7.0386),
    # Bonn districts
    "poppelsdorf": (50.7267, 7.0863), "endenich": (50.7272, 7.0650),
    "beuel": (50.7390, 7.1170), "bad godesberg": (50.6830, 7.1500),
    "ippendorf": (50.7065, 7.0780), "dransdorf": (50.7355, 7.0508),
    "oberkassel": (50.7158, 7.1667), "oberdollendorf": (50.6990, 7.1850),
    "venusberg": (50.7047, 7.0968), "lannesdorf": (50.6606, 7.1556),
    "rheinaue": (50.7106, 7.1283), "freizeitpark rheinaue": (50.7106, 7.1283),
    # Siebengebirge
    "siebengebirge": (50.6710, 7.2370), "kottenforst": (50.6670, 7.0400),
    "drachenfels": (50.6652, 7.2107), "petersberg": (50.6869, 7.2078),
    "margarethenhöhe": (50.6840, 7.2430), "margarethenhoehe": (50.6840, 7.2430),
    "heisterbach": (50.6966, 7.2093), "lohrberg": (50.6837, 7.2545),
    # Ahrtal
    "bad neuenahr": (50.5439, 7.1113), "bad neuenahr-ahrweiler": (50.5439, 7.1113),
    "ahrweiler": (50.5415, 7.0947), "ahrtal": (50.5420, 7.0950),
    "dernau": (50.5332, 7.0447), "mayschoss": (50.5238, 7.0186),
    "altenahr": (50.5161, 6.9922), "walporzheim": (50.5361, 7.0751),
    "sinzig": (50.5439, 7.2460),
    # Mittelrhein
    "andernach": (50.4406, 7.4019), "namedy": (50.4564, 7.3665),
    "linz": (50.5686, 7.2849), "linz am rhein": (50.5686, 7.2849),
    "unkel": (50.6003, 7.2162), "bad hönningen": (50.5169, 7.3122),
    "bad hoenningen": (50.5169, 7.3122), "rolandseck": (50.6324, 7.2079),
    "rheinbreitbach": (50.6166, 7.2320), "bruchhausen": (50.6020, 7.2533),
    "erpel": (50.5831, 7.2365),
    # NRW / Ruhr-Guide locations that can still fall inside the Bonn radius
    "wuppertal": (51.2562, 7.1508), "solingen": (51.1652, 7.0671),
    "neuss": (51.2042, 6.6879), "monheim": (51.0916, 6.8925),
    "monheim am rhein": (51.0916, 6.8925), "leichlingen": (51.1061, 7.0187),
}

# ── Curated Meetup groups ───────────────────────────────────────────
# Each exposes a public iCal feed at meetup.com/<slug>/events/ical/ (no auth).
# Re-probe periodically: a 404 = wrong slug; 200 with 0 VEVENT = inactive group.
# Tuple: (slug, default_city, category-hint, trust)
MEETUP_GROUPS = [
    ("bonner-ki-meetup", "Bonn", "ki tech meetup", 0.95),
    ("jug-bonn", "Bonn", "java tech meetup", 0.9),
    ("azure-bonn-meetup", "Bonn", "cloud tech meetup", 0.9),
    ("rudel-koeln", "Köln", "wanderung outdoor natur meetup", 0.85),
    ("board-games-in-bonn", "Bonn", "spiele meetup", 0.8),
    ("sprachcafe-bonn", "Bonn", "sprache meetup", 0.8),
]
