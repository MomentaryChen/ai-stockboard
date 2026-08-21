"""Per-source-IP throttling for the credential endpoints.

This is one half of the login defence; `services/auth.py` owns the other.
The split is deliberate, and so is the storage each half uses:

  * **per account**, in the database. Consecutive failures against one account
    lock it for a while. Persistent because a lockout that a restart clears is
    a lockout the attacker can clear.
  * **per source IP**, here, in process memory. Failures from one address across
    *any* account. Memory is the right store for this one: an attacker who can
    rotate addresses defeats a shared table just as easily as a local dict, so
    paying for a write on every failed login would buy nothing, and a window
    that empties on restart costs an honest user a few minutes at worst.

Neither half is sufficient alone. The account lock stops one password list
being ground against one account, and does nothing about the same attacker
trying `admin`, `test`, `victor`... one guess each. The IP limit stops that,
and does nothing about a botnet with one guess per address. Together they make
both shapes expensive, which is all a login endpoint can honestly promise.

The known gap: several uvicorn workers each hold their own counters, so the
effective per-IP allowance multiplies by the worker count. `server/Dockerfile`
runs a single worker, and the per-account lock -- which is in the database and
therefore shared -- is the half that has to hold if that ever changes.
"""

import ipaddress
import logging
import threading
import time
from collections import defaultdict, deque

from fastapi import Request

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Nothing prunes idle addresses between requests, so an attacker cycling source
# addresses would grow the dict forever. Past this many tracked addresses the
# oldest windows are dropped wholesale -- losing a few counters is a far better
# failure than exhausting memory, and the account lock still applies.
_MAX_TRACKED_ADDRESSES = 20_000


class IpRateWindow:
    """Sliding window of event timestamps, keyed by client address.

    What counts as an event is the caller's choice, and the two users of this
    class choose differently: the login window records only *failures*, because
    signing in correctly a hundred times is not suspicious and must not consume
    anybody's budget, while the registration window records every account
    created, because creating them is the thing being limited.
    """

    def __init__(self, max_failures: int, window_seconds: float):
        self._max = max_failures
        self._window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _trim(self, key: str, now: float) -> deque[float]:
        hits = self._hits[key]
        while hits and now - hits[0] >= self._window:
            hits.popleft()
        return hits

    def retry_after(self, key: str) -> int:
        """Seconds until this address may try again, or 0 while it is welcome.

        Read-only: checking never counts against the budget, so a caller can
        ask before doing the expensive part of the request.
        """
        if self._max <= 0:
            return 0
        now = time.monotonic()
        with self._lock:
            hits = self._trim(key, now)
            if len(hits) < self._max:
                return 0
            return max(1, int(self._window - (now - hits[0])) + 1)

    def record(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            if len(self._hits) > _MAX_TRACKED_ADDRESSES:
                self._evict_locked(now)
            self._trim(key, now).append(now)

    def clear(self, key: str) -> None:
        """Called on success: one good sign-in forgives that address."""
        with self._lock:
            self._hits.pop(key, None)

    def _evict_locked(self, now: float) -> None:
        """Drop every window that has already expired; if that frees nothing,
        drop everything. Caller holds the lock."""
        stale = [key for key, hits in self._hits.items() if not hits or now - hits[-1] >= self._window]
        for key in stale:
            del self._hits[key]
        if len(self._hits) > _MAX_TRACKED_ADDRESSES:
            logger.warning(
                "Login guard is tracking %s live addresses -- clearing all counters",
                len(self._hits),
            )
            self._hits.clear()


login_failures = IpRateWindow(
    max_failures=settings.login_ip_max_failures,
    window_seconds=settings.login_ip_window_minutes * 60,
)

# Registrations are counted whether or not they succeed: the thing being
# limited is accounts created, not mistakes made.
registrations = IpRateWindow(
    max_failures=settings.register_ip_max_per_hour,
    window_seconds=3600,
)


_TRUSTED = settings.trusted_proxy_networks


def client_ip(request: Request) -> str:
    """The address to hold responsible for this request.

    `deployment/docker-compose.yml` puts nginx in front of the API *and*
    publishes the API's own port on the host, so `X-Real-IP` is only worth
    reading when the connection itself arrived from a proxy we trust -- anyone
    reaching :8000 directly can put any address they like in that header, and
    believing it would let a single attacker present as a fresh client on every
    request. When the peer is not trusted the header is ignored entirely.

    Returns "unknown" for a request with no peer address (ASGI transports that
    do not provide one, e.g. some test clients). Those all share one bucket,
    which is the conservative direction.
    """
    peer = request.client.host if request.client else None
    if peer is None:
        return "unknown"

    if not _is_trusted(peer):
        return peer

    # nginx.conf sets this from $remote_addr, overwriting whatever the client
    # sent. X-Forwarded-For is deliberately not used: it is client-supplied
    # with the proxy's view appended, so reading it correctly means counting
    # hops, and there is nothing here that header carries and this one does not.
    forwarded = request.headers.get("x-real-ip", "").strip()
    return forwarded or peer


def _is_trusted(peer: str) -> bool:
    if not _TRUSTED:
        return False
    try:
        address = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return any(address in network for network in _TRUSTED)
