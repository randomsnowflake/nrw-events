from .parser_cases import case_class


BONN_MARKERS = (
    "bonn", "brotfabrik", "pantheon", "kult41", "kunstmuseum",
    "bundeskunsthalle", "botanical", "springmaus", "repair_cafes",
    "brueckenforum", "vox_bona", "haus_der_geschichte",
)

BonnSourceTests = case_class(
    "BonnSourceTests", lambda name: any(marker in name for marker in BONN_MARKERS)
)
