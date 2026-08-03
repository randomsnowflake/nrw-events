"""Source/parser callable contracts used by adapter helpers."""

from typing import Protocol

from .models import EventRecord


class SourceFetcher(Protocol):
    def __call__(self) -> list[EventRecord]: ...


class TextParser(Protocol):
    def __call__(self, document: str) -> list[EventRecord]: ...
