"""Keep-alive HTTPS pool for Abyss CDN segments.

Why this exists: the original client opened a brand-new TLS connection
per 2 MB segment (urllib.request.urlopen). On low-latency routes that is
merely wasteful; on high-latency routes (e.g. Gulf -> CDN PoP) the TLS +
slow-start handshake per segment dominates and total throughput collapses.

This pool keeps one persistent HTTPSConnection per worker thread per host
(HTTP/1.1 keep-alive) so segments 2..N on the same thread skip TCP+TLS
setup entirely. It also supports Range probes so auto-tuning can sample
the first 1 MB of a segment instead of downloading full 2 MB segments.
Standard library only: no new dependencies.
"""

from __future__ import annotations

import http.client
import ssl
import threading
import time
import urllib.parse

from .constants import DEFAULT_TIMEOUT, USER_AGENT, ABYSS_BASE

_local = threading.local()


def _headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = {
        "Referer": f"{ABYSS_BASE}/",
        "Origin": ABYSS_BASE,
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Accept-Encoding": "identity",
        "Connection": "keep-alive",
    }
    if extra:
        headers.update(extra)
    return headers


def _conn_for(host: str, timeout: int) -> http.client.HTTPSConnection:
    """Thread-local cached connection per host. Rebuilt if stale/closed."""
    cache: dict[str, http.client.HTTPSConnection] = getattr(_local, "conns", None)  # type: ignore[assignment]
    if cache is None:
        cache = {}
        _local.conns = cache  # type: ignore[attr-defined]
    conn = cache.get(host)
    # http.client has no public "is connected" flag; `sock is None` means closed.
    if conn is not None and getattr(conn, "sock", None) is None:
        try:
            conn.close()
        except Exception:
            pass
        conn = None
    if conn is None:
        ctx = ssl.create_default_context()
        conn = http.client.HTTPSConnection(host, 443, timeout=timeout, context=ctx)
        cache[host] = conn
    return conn


def _drop_conn(host: str) -> None:
    cache: dict[str, http.client.HTTPSConnection] | None = getattr(_local, "conns", None)  # type: ignore[assignment]
    if not cache:
        return
    conn = cache.pop(host, None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass


def fetch_range(
    url: str,
    extra_headers: dict[str, str] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    start: int = 0,
    end: int | None = None,
    chunk_size: int = 256 * 1024,
) -> tuple[bytes, int]:
    """GET a byte range, reusing the thread-local keep-alive connection.

    Returns (body, status). Retries once on a stale pooled connection.
    """
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    path = parts.path + ("?" + parts.query if parts.query else "")
    headers = _headers(extra_headers)
    if end is not None:
        headers["Range"] = f"bytes={start}-{end}"
    elif start:
        headers["Range"] = f"bytes={start}-"

    last_exc: Exception | None = None
    for attempt in range(2):
        conn = _conn_for(host, timeout)
        try:
            conn.request("GET", path or "/", headers=headers)
            resp = conn.getresponse()
            status = resp.status
            chunks: list[bytes] = []
            while True:
                data = resp.read(chunk_size)
                if not data:
                    break
                chunks.append(data)
            # Drain fully so the connection stays reusable.
            body = b"".join(chunks)
            # Cloudflare sometimes closes idle keep-alive sockets; if the
            # server asked to close, drop our cached handle.
            if resp.getheader("Connection", "").lower() == "close":
                _drop_conn(host)
            return body, status
        except Exception as exc:  # stale socket, reset by peer, timeout
            last_exc = exc
            _drop_conn(host)
            if attempt == 0:
                time.sleep(0.2 * (attempt + 1))
                continue
            raise
    raise RuntimeError(f"fetch_range failed: {last_exc}")


def fetch_full_stream(
    url: str,
    extra_headers: dict[str, str] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    chunk_size: int = 256 * 1024,
    on_chunk=None,
) -> int:
    """GET full body via keep-alive conn, streaming to caller via on_chunk.

    on_chunk(bytes) is called per chunk. Returns total bytes written.
    Retries once on stale pooled connection.
    """
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    path = parts.path + ("?" + parts.query if parts.query else "")
    headers = _headers(extra_headers)

    last_exc: Exception | None = None
    for attempt in range(2):
        conn = _conn_for(host, timeout)
        try:
            conn.request("GET", path or "/", headers=headers)
            resp = conn.getresponse()
            if resp.status not in (200, 206):
                resp.read()  # drain for reuse
                raise IOError(f"HTTP {resp.status} for segment")
            total = 0
            while True:
                data = resp.read(chunk_size)
                if not data:
                    break
                total += len(data)
                if on_chunk:
                    on_chunk(data)
            if resp.getheader("Connection", "").lower() == "close":
                _drop_conn(host)
            return total
        except Exception as exc:
            last_exc = exc
            if isinstance(exc, IOError):
                raise
            _drop_conn(host)
            if attempt == 0:
                time.sleep(0.2 * (attempt + 1))
                continue
            raise
    raise RuntimeError(f"fetch_full_stream failed: {last_exc}")


def close_thread_conns() -> None:
    """Close all pooled connections for this thread (cleanup)."""
    cache: dict[str, http.client.HTTPSConnection] | None = getattr(_local, "conns", None)  # type: ignore[assignment]
    if not cache:
        return
    for conn in list(cache.values()):
        try:
            conn.close()
        except Exception:
            pass
    cache.clear()
