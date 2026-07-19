"""Compatibility facade for source adapters.

New code should import the focused modules (``http``, ``text``,
``event_builder``, ``jsonld``, and ``ical``). The module alias preserves the
historical mutable window/runtime state for adapters that still assign
``common.TODAY`` in tests or embedding code.
"""

from __future__ import annotations

import sys

from . import core as _core


sys.modules[__name__] = _core
