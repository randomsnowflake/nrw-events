"""Deterministic detail-adapter selection and shared-URL policy."""
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

from ..detail_types import DetailContext
from ..models import RawEvent
from . import sites

Extractor = Callable[[str, RawEvent], DetailContext | None]

@dataclass(frozen=True)
class DetailSpec:
    host: str
    extract: Extractor | None = None
    repeated: bool = False
    block_generic: bool = False
    blocked_path: str | None = None
    price: Callable[[str, RawEvent], str] | None = None

# Order matches the original dispatch. Extractors retain their precise path,
# source-id and occurrence checks; host matching only narrows dispatch.
SPECS = (
    DetailSpec("touren-termine.adfc.de", sites._adfc_detail_context),
    DetailSpec("bildungswerk-brotfabrik.de", sites._bildungswerk_brotfabrik_context),
    DetailSpec("klimaviertel-beuel.de", sites._klimaviertel_overview_context, repeated=True),
    DetailSpec("pantheon.de", sites._pantheon_detail_context, repeated=True, block_generic=True),
    DetailSpec("wir-fuer-rheinbach.de", sites._rheinbach_sommerkino_context, repeated=True),
    DetailSpec("rhein.info", sites._unkel_detail_context, repeated=True, block_generic=True, blocked_path="/unkel"),
    DetailSpec("rathausmusik.com", sites._rathausmusik_detail_context, repeated=True),
    DetailSpec("eitorf.de", sites._eitorf_detail_context),
    DetailSpec("froscon.org", sites._froscon_detail_context),
    DetailSpec("bundeskunsthalle.de", sites._bundeskunsthalle_detail_context),
    DetailSpec("dein-phonzimmer.de", sites._dein_phonzimmer_detail_context, repeated=True),
    DetailSpec("theater-marabu.de", repeated=True),
    DetailSpec("arpmuseum.org", price=sites._arp_museum_subline_price),
)

def matching_specs(event: RawEvent) -> tuple[DetailSpec, ...]:
    host = sites._event_hostname(event)
    return tuple(spec for spec in SPECS if host in {spec.host, "www." + spec.host})

def extract_source_context(document: str, event: RawEvent) -> DetailContext | None:
    for spec in matching_specs(event):
        context = spec.extract(document, event) if spec.extract else None
        if context is not None:
            return context
        path = urlsplit(str(event.get("link") or "")).path.casefold().rstrip("/")
        if spec.block_generic and (spec.blocked_path is None or path == spec.blocked_path):
            return {"description": "", "description_html": "", "price": "", "venue": "", "venue_address": ""}
    return None

def supports_repeated_detail(event: RawEvent) -> bool:
    return any(spec.repeated for spec in matching_specs(event))

def source_price(document: str, event: RawEvent) -> str:
    return next((value for spec in matching_specs(event) if spec.price and (value := spec.price(document, event))), "")

# This template is identified by its HTML marker, including proxy documents.
template_price = sites._template_price
