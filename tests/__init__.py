"""NRW Events offline test suite."""

import atexit
import os
import shutil
import socket
import tempfile


_TEST_CACHE_DIR = tempfile.mkdtemp(prefix="nrw-events-tests-")
atexit.register(shutil.rmtree, _TEST_CACHE_DIR, ignore_errors=True)
os.environ.setdefault("NRW_EVENTS_CACHE_DIR", _TEST_CACHE_DIR)
os.environ.setdefault("NRW_EVENTS_LOG_LEVEL", "CRITICAL")
os.environ.setdefault("NRW_EVENTS_AI_ENRICHMENT", "0")


def _block_network(*_args, **_kwargs):
    raise AssertionError("network access in offline test suite")


socket.socket.connect = _block_network
socket.socket.connect_ex = _block_network
