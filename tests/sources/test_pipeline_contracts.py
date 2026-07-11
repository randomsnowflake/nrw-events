from .parser_cases import case_class


PIPELINE_MARKERS = ("make_event", "search_fallback", "date_for_window", "ical_")
BONN_MARKERS = (
    "bonn", "brotfabrik", "pantheon", "kult41", "kunstmuseum",
    "bundeskunsthalle", "botanical", "springmaus", "repair_cafes",
    "brueckenforum", "vox_bona",
)

PipelineContractTests = case_class(
    "PipelineContractTests",
    lambda name: (any(marker in name for marker in PIPELINE_MARKERS)
                  and not any(marker in name for marker in BONN_MARKERS)),
)
