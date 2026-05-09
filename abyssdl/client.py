from __future__ import annotations

import ssl
import urllib.request
from contextlib import contextmanager
from typing import Iterator

from .constants import ABYSS_BASE, DEFAULT_TIMEOUT, USER_AGENT


def default_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = {
        "Referer": f"{ABYSS_BASE}/",
        "Origin": ABYSS_BASE,
        "User-Agent": USER_AGENT,
    }
    if extra:
        headers.update(extra)
    return headers


def build_request(url: str, headers: dict[str, str] | None = None) -> urllib.request.Request:
    req = urllib.request.Request(url)
    for key, value in default_headers(headers).items():
        req.add_header(key, value)
    return req


def fetch_text(url: str, headers: dict[str, str] | None = None, timeout: int = DEFAULT_TIMEOUT) -> str:
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(build_request(url, headers), timeout=timeout, context=ctx) as resp:
        return resp.read().decode("utf-8", errors="ignore")


@contextmanager
def fetch_binary_response(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> Iterator:
    ctx = ssl.create_default_context()
    resp = urllib.request.urlopen(build_request(url, headers), timeout=timeout, context=ctx)
    try:
        yield resp
    finally:
        resp.close()
