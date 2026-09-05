"""Separate decoded JSON documents must never share object-address history."""
import builtins
import json
import unittest
from unittest.mock import patch

from nrw_events import jsonld


class JsonLdBlockIdentityTests(unittest.TestCase):
    def test_reused_root_address_does_not_drop_later_script_blocks(self):
        blocks = [
            {'@context': 'https://schema.org', '@graph': [
                {'@type': 'Event', 'name': f'Contract market {index}'},
                {'@type': 'BreadcrumbList', 'itemListElement': []},
            ]}
            for index in range(14)
        ]
        document = ''.join(f'<script type="application/ld+json">{json.dumps(block)}</script>' for block in blocks)
        # Decoded wrapper roots are released after walk() returns. CPython may
        # reuse their address in the next json.loads(), while Event children
        # survive in the returned items. Reproduce that allocator behavior.
        def reused_address(value):
            return 1 if '@graph' in value else builtins.id(value)
        with patch.object(jsonld, 'id', reused_address, create=True):
            events = jsonld.jsonld_event_items(document)
        self.assertEqual([event['name'] for event in events], [f'Contract market {i}' for i in range(14)])
