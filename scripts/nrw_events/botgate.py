"""kdvz Rhein-Erft-Rur botgate: polite access to municipal calendars.

Since September 2026 the kdvz hosting cloud (bonn.de and the SiteKit town
portals) answers first visits with a small SHA-256 proof-of-work page. A browser
solves it once, receives a ``botgate`` cookie valid for several days and is then
served normally.

This module does the same for the importer, but deliberately conservatively:

* the cookie is persisted and reused across runs until shortly before it
  expires, so a host is normally solved about once a week;
* each host bucket is solved at most once per process. A challenge that
  survives a fresh cookie is a hard source failure, never a retry loop;
* difficulty is capped, so a raised gate turns into a visible source failure
  instead of unbounded CPU use;
* successful responses from gated hosts are cached for most of a day (see
  ``GatedResponseCache``), so repeated or resumed runs do not re-request pages
  that were already read today.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import suppress
from dataclasses import dataclass
from http.cookies import CookieError, SimpleCookie
from pathlib import Path

# Registrable domains served through the kdvz botgate. Subdomains match; a
# hostname like ``www.uni-bonn.de`` does not end in ``.bonn.de`` and is excluded.
GATED_HOST_SUFFIXES: tuple[str, ...] = (
    "bonn.de",
    "bruehl.de",
    "wesseling.de",
    "stadt-frechen.de",
    "huerth.de",
    "erftstadt.de",
    "zuelpich.de",
)

_CHALLENGE_MARKER = "/.well-known/botgate/"
_CHALLENGE_PATTERN = re.compile(
    r'var\s+token\s*=\s*"(?P<token>[A-Za-z0-9._\-]+)"\s*,\s*'
    r"difficulty\s*=\s*(?P<difficulty>\d+)\s*,\s*"
    r'verifyURL\s*=\s*"(?P<verify>/[^"]*)"\s*,\s*'
    r'redirect\s*=\s*"(?P<redirect>[^"]*)"'
)
_COOKIE_NAME = "botgate"
# Difficulty is hex digits (4 bits each). 5 (20 bits) takes ~1-2 s in CPython;
# 6 (24 bits) ~30 s. Anything above is treated as an intentional block.
_MAX_BITS = 24
# Renew a stored cookie this long before its announced expiry.
_COOKIE_RENEW_MARGIN_SECONDS = 6 * 60 * 60
# Without an explicit expiry assume the page's "einige Tage" conservatively.
_DEFAULT_COOKIE_LIFETIME_SECONDS = 2 * 24 * 60 * 60
_COOKIE_STORE_VERSION = 1


class BotgateError(RuntimeError):
    """The gate could not be passed politely; the source should fail visibly."""


@dataclass(frozen=True)
class Challenge:
    token: str
    difficulty: int
    verify_path: str
    redirect: str

    @property
    def bits(self) -> int:
        return self.difficulty * 4


def gated_bucket(url: str) -> str | None:
    """Return the gated registrable domain for ``url``, if any."""
    hostname = (urllib.parse.urlsplit(url).hostname or "").lower()
    for suffix in GATED_HOST_SUFFIXES:
        if hostname == suffix or hostname.endswith(f".{suffix}"):
            return suffix
    return None


def is_challenge(body: str) -> bool:
    return _CHALLENGE_MARKER in body and _CHALLENGE_PATTERN.search(body) is not None


def parse_challenge(body: str) -> Challenge:
    match = _CHALLENGE_PATTERN.search(body)
    if match is None:
        raise BotgateError("botgate challenge page has an unknown format")
    return Challenge(
        token=match.group("token"),
        difficulty=int(match.group("difficulty")),
        verify_path=match.group("verify"),
        redirect=match.group("redirect"),
    )


def solve(token: str, bits: int, *, max_bits: int = _MAX_BITS) -> int:
    """Find n with ``sha256(token + ":" + n)`` starting with ``bits`` zero bits.

    Mirrors the gate's browser worker, which only inspects the first 32-bit word.
    """
    if bits < 1 or bits > min(max_bits, 32):
        raise BotgateError(f"botgate difficulty of {bits} bits exceeds the polite limit")
    prefix = f"{token}:".encode()
    shift = 32 - bits
    n = 0
    while True:
        digest = hashlib.sha256(prefix + str(n).encode()).digest()
        if int.from_bytes(digest[:4], "big") >> shift == 0:
            return n
        n += 1


def _cache_root() -> Path:
    configured = os.environ.get("NRW_EVENTS_CACHE_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    xdg_cache = os.environ.get("XDG_CACHE_HOME", "").strip()
    base = Path(xdg_cache).expanduser() if xdg_cache else Path.home() / ".cache"
    return base / "nrw-events"


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        temporary.write_bytes(data)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


class BotgateSession:
    """Process-wide cookie store and once-per-run solver for gated hosts."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._bucket_locks: dict[str, threading.Lock] = {}
        self._solved_this_process: set[str] = set()
        self._cookies: dict[str, dict[str, object]] | None = None

    # -- cookie persistence -------------------------------------------------
    def _path(self) -> Path:
        return _cache_root() / "botgate-cookies-v1.json"

    def _load(self) -> dict[str, dict[str, object]]:
        if self._cookies is not None:
            return self._cookies
        try:
            payload = json.loads(self._path().read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError):
            payload = {}
        cookies = payload.get("cookies") if isinstance(payload, dict) else None
        valid: dict[str, dict[str, object]] = {}
        if isinstance(payload, dict) and payload.get("version") == _COOKIE_STORE_VERSION and isinstance(cookies, dict):
            for bucket, entry in cookies.items():
                if (isinstance(entry, dict) and isinstance(entry.get("value"), str)
                        and isinstance(entry.get("expires_at"), int | float)):
                    valid[str(bucket)] = entry
        self._cookies = valid
        return valid

    def _save(self) -> None:
        cookies = self._cookies or {}
        data = json.dumps({"version": _COOKIE_STORE_VERSION, "cookies": cookies}, sort_keys=True)
        # A lost cookie only costs one extra solve next run.
        with suppress(OSError):
            _atomic_write(self._path(), data.encode("utf-8"))

    def cookie_header(self, bucket: str) -> str | None:
        with self._lock:
            entry = self._load().get(bucket)
        if not entry:
            return None
        expires_at = float(entry["expires_at"])  # type: ignore[arg-type]
        if expires_at - _COOKIE_RENEW_MARGIN_SECONDS <= time.time():
            return None
        return f"{_COOKIE_NAME}={entry['value']}"

    def forget(self, bucket: str) -> None:
        with self._lock:
            if self._load().pop(bucket, None) is not None:
                self._save()

    # -- solving --------------------------------------------------------------
    def _bucket_lock(self, bucket: str) -> threading.Lock:
        with self._lock:
            return self._bucket_locks.setdefault(bucket, threading.Lock())

    def pass_challenge(self, url: str, body: str, *, timeout: float, headers: dict[str, str],
                       stale_cookie: str | None) -> None:
        """Solve the gate for ``url``'s bucket once per process and store the cookie.

        Concurrent callers for one bucket wait for the first solver. If another
        thread already renewed the cookie, return without solving again.
        """
        bucket = gated_bucket(url)
        if bucket is None:
            raise BotgateError(f"unexpected botgate challenge from {url}")
        with self._bucket_lock(bucket):
            current = self.cookie_header(bucket)
            if current is not None and current != stale_cookie:
                return  # Renewed concurrently.
            if bucket in self._solved_this_process:
                raise BotgateError(
                    f"botgate challenge for {bucket} persisted after a fresh solve; not retrying"
                )
            self._solved_this_process.add(bucket)
            challenge = parse_challenge(body)
            nonce = solve(challenge.token, challenge.bits)
            query = urllib.parse.urlencode({"t": challenge.token, "n": nonce, "r": challenge.redirect})
            verify_url = urllib.parse.urljoin(url, challenge.verify_path) + "?" + query
            request_headers = {k: v for k, v in headers.items() if k.lower() != "cookie"}
            opener = urllib.request.build_opener(_NoRedirect)
            try:
                response = opener.open(urllib.request.Request(verify_url, headers=request_headers), timeout=timeout)
                set_cookies = response.headers.get_all("Set-Cookie") or []
                response.close()
            except urllib.error.HTTPError as exc:
                if exc.code not in (301, 302, 303, 307, 308):
                    exc.close()
                    raise BotgateError(f"botgate verification for {bucket} returned HTTP {exc.code}") from exc
                set_cookies = exc.headers.get_all("Set-Cookie") or []
                exc.close()
            value, expires_at = _extract_cookie(set_cookies)
            if value is None:
                raise BotgateError(f"botgate verification for {bucket} did not issue a cookie")
            with self._lock:
                self._load()[bucket] = {"value": value, "expires_at": expires_at, "solved_at": time.time()}
                self._save()


def _extract_cookie(set_cookie_headers: list[str]) -> tuple[str | None, float]:
    for header in set_cookie_headers:
        parsed: SimpleCookie = SimpleCookie()
        try:
            parsed.load(header)
        except CookieError:
            continue
        morsel = parsed.get(_COOKIE_NAME)
        if morsel is None or not morsel.value:
            continue
        expires_at = time.time() + _DEFAULT_COOKIE_LIFETIME_SECONDS
        max_age = morsel.get("max-age")
        if max_age and str(max_age).isdigit():
            expires_at = time.time() + int(max_age)
        elif morsel.get("expires"):
            try:
                from email.utils import parsedate_to_datetime
                expires_at = parsedate_to_datetime(morsel["expires"]).timestamp()
            except (TypeError, ValueError):
                pass
        return morsel.value, expires_at
    return None, 0.0


class GatedResponseCache:
    """Day-scoped response cache for gated hosts.

    kdvz sends ``Cache-Control: no-store`` and neither ETag nor Last-Modified,
    so conditional requests are impossible. Instead a successful body is reused
    for ``NRW_EVENTS_GATED_RESPONSE_TTL_HOURS`` (default 20 h): the daily run
    reads each page once, while retries, resumes and manual reruns on the same
    day are served locally.
    """

    _PRUNE_AFTER_SECONDS = 3 * 24 * 60 * 60

    def __init__(self) -> None:
        self._pruned: set[str] = set()
        self._lock = threading.Lock()

    @staticmethod
    def ttl_seconds() -> float:
        try:
            hours = float(os.environ.get("NRW_EVENTS_GATED_RESPONSE_TTL_HOURS", "20"))
        except ValueError:
            hours = 20.0
        return max(hours, 0.0) * 60 * 60

    @staticmethod
    def _dir(bucket: str) -> Path:
        return _cache_root() / "gated-responses-v1" / bucket

    @staticmethod
    def _key(url: str, accept: str) -> str:
        return hashlib.sha256(f"{url}\n{accept}".encode()).hexdigest()

    def get(self, bucket: str, url: str, accept: str) -> str | None:
        ttl = self.ttl_seconds()
        if not ttl:
            return None
        path = self._dir(bucket) / f"{self._key(url, accept)}.json.gz"
        try:
            payload = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
        except (FileNotFoundError, OSError, ValueError, EOFError):
            return None
        if (not isinstance(payload, dict) or payload.get("url") != url
                or not isinstance(payload.get("body"), str)
                or not isinstance(payload.get("fetched_at"), int | float)
                or time.time() - payload["fetched_at"] > ttl):
            return None
        return str(payload["body"])

    def put(self, bucket: str, url: str, accept: str, body: str) -> None:
        if not self.ttl_seconds() or not body.strip() or is_challenge(body):
            return
        directory = self._dir(bucket)
        data = json.dumps({"url": url, "fetched_at": time.time(), "body": body}, ensure_ascii=False)
        try:
            _atomic_write(directory / f"{self._key(url, accept)}.json.gz",
                          gzip.compress(data.encode("utf-8"), compresslevel=6))
        except OSError:
            return
        self._prune_once(bucket, directory)

    def _prune_once(self, bucket: str, directory: Path) -> None:
        with self._lock:
            if bucket in self._pruned:
                return
            self._pruned.add(bucket)
        cutoff = time.time() - self._PRUNE_AFTER_SECONDS
        with suppress(OSError):
            stale = [entry for entry in directory.glob("*.json.gz") if _mtime(entry) < cutoff]
            for entry in stale:
                entry.unlink(missing_ok=True)


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return float("inf")


SESSION = BotgateSession()
RESPONSE_CACHE = GatedResponseCache()


def reset_for_tests() -> None:
    global SESSION, RESPONSE_CACHE
    SESSION = BotgateSession()
    RESPONSE_CACHE = GatedResponseCache()
