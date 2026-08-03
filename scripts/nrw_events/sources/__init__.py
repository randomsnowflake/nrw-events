"""Schema-validated, region-aware source registry."""

from pathlib import Path

from ..source_specs import AdapterType, adapter_for, load_source_specs

SOURCE_SPECS = load_source_specs(Path(__file__).with_name("registry.json"))
STANDARD_SOURCES = {
    spec.display_name: adapter_for(spec) for spec in SOURCE_SPECS if spec.adapter is not AdapterType.PYTHON
}
CUSTOM_SOURCES = {spec.display_name: adapter_for(spec) for spec in SOURCE_SPECS if spec.adapter is AdapterType.PYTHON}
SOURCES = {spec.display_name: adapter_for(spec) for spec in SOURCE_SPECS}
SOURCE_IDS = {spec.display_name: spec.id for spec in SOURCE_SPECS}
SOURCE_REGIONS = {spec.display_name: spec.region for spec in SOURCE_SPECS}
