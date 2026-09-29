"""
Shared HTTP helpers with exponential backoff for rate limits (429) and transient errors.
Keeps the bot from going fully blind when public APIs throttle.
"""

from __future__ import annotations

import random
import time
from typing import Any, Optional

import requests

# Soft global spacing between outbound calls (seconds)
_MIN_GAP = float(__import__("os").getenv("HTTP_MIN_GAP_SEC", "0.05"))
_last_call = 0.0


def _pace() -> None:
    global _last_call
    now = time.time()
    wait = _MIN_GAP - (now - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.time()


def request_with_backoff(
    method: str,
    url: str,
    *,
    max_retries: int = 2,
    timeout: float = 8,
    headers: Optional[dict] = None,
    params: Optional[dict] = None,
    json: Any = None,
    session: Optional[requests.Session] = None,
) -> requests.Response:
    """
    GET/POST with exponential backoff on 429 / 5xx / network errors.

    Backoff: ~0.5s, 1s, 2s, 4s (+ small jitter). Respects Retry-After when present.
    """
    method = method.upper()
    do = session.request if session is not None else requests.request
    last_err: Exception | None = None
    resp: requests.Response | None = None

    for attempt in range(max_retries + 1):
        _pace()
        try:
            resp = do(
                method,
                url,
                headers=headers,
                params=params,
                json=json,
                timeout=timeout,
            )
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt >= max_retries:
                    return resp
                ra = resp.headers.get("Retry-After")
                if ra:
                    try:
                        delay = min(float(ra), 30.0)
                    except ValueError:
                        delay = (2 ** attempt) * 0.5
                else:
                    delay = (2 ** attempt) * 0.5 + random.uniform(0, 0.25)
                time.sleep(delay)
                continue
            return resp
        except requests.RequestException as e:
            last_err = e
            if attempt >= max_retries:
                raise
            delay = (2 ** attempt) * 0.5 + random.uniform(0, 0.25)
            time.sleep(delay)

    if resp is not None:
        return resp
    raise last_err or RuntimeError("request_with_backoff failed")


def get_json(
    url: str,
    *,
    max_retries: int = 4,
    timeout: float = 20,
    headers: Optional[dict] = None,
    params: Optional[dict] = None,
) -> Any:
    """GET and parse JSON; raises on final failure."""
    r = request_with_backoff(
        "GET", url, max_retries=max_retries, timeout=timeout, headers=headers, params=params
    )
    if r.status_code in (401, 403, 402):
        raise RuntimeError(f"http_{r.status_code}")
    if r.status_code == 429:
        raise RuntimeError("rate_limited")
    r.raise_for_status()
    return r.json()
