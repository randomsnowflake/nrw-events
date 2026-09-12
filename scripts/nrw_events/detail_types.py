"""Internal detail facts. Missing or empty facts never erase existing values.

An empty context is an explicit no-enrichment result from a matched adapter;
None at the extractor boundary means the generic parser may still be consulted.
"""
from typing import TypedDict


class DetailContext(TypedDict, total=False):
    description: str
    description_html: str
    exact_description: str
    exact_description_html: str
    price: str
    venue: str
    venue_address: str
    organizer: str
    time: str
    start_at: str
    end_at: str
