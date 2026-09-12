"""Shared occurrence-clock normalization; kept byte-identical by contract generation.

The consumer carries a standalone copy so it never imports a submodule at runtime.
"""
import re


def occurrence_clock(time: object, start_at: object = "") -> str:
    if match := re.match(r"\s*(\d{1,2}):(\d{2})", str(time or "")):
        return f"{int(match.group(1)):02d}:{match.group(2)}"
    value = str(start_at or "")
    return value[11:16] if len(value) >= 16 else ""
