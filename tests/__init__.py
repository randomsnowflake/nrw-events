"""NRW Events offline test suite."""

import os
import socket
import tempfile


_TEST_CACHE_DIR = tempfile.mkdtemp(prefix="nrw-events-tests-")
os.environ.setdefault("NRW_EVENTS_CACHE_DIR", _TEST_CACHE_DIR)


def _block_network(*_args, **_kwargs):
    raise AssertionError("network access in offline test suite")


socket.socket.connect = _block_network
socket.socket.connect_ex = _block_network
