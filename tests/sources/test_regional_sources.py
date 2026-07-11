from .parser_cases import case_class

BONN_MARKERS = (
    "bonn", "brotfabrik", "pantheon", "kult41", "kunstmuseum",
    "bundeskunsthalle", "botanical", "springmaus", "repair_cafes",
    "brueckenforum", "vox_bona",
)
PIPELINE_MARKERS = ("make_event", "search_fallback", "date_for_window", "ical_")


RegionalSourceTests = case_class(
    "RegionalSourceTests",
    lambda name: not any(marker in name for marker in BONN_MARKERS + PIPELINE_MARKERS),
)
