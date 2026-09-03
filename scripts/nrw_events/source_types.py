"""Source/parser callable contracts used by adapter helpers."""

from typing import Protocol

from .health import SourceFetchResult
from .models import EventRecord


class SourceFetcher(Protocol):
    def __call__(self) -> SourceFetchResult: ...


class TextParser(Protocol):
    def __call__(self, document: str) -> list[EventRecord]: ...
