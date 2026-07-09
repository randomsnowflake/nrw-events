"""Source/parser callable contracts used by adapter helpers."""

from typing import List, Protocol

from .models import EventRecord


class SourceFetcher(Protocol):
    def __call__(self) -> List[EventRecord]: ...


class TextParser(Protocol):
    def __call__(self, document: str) -> List[EventRecord]: ...
