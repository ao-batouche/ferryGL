"""JSON over HTTP with retries, shared by the GitHub and Linear clients."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx

RETRY_STATUSES = frozenset({429, 502, 503, 504})


class HttpError(Exception):
    """A request failed, or returned an error status without a GraphQL ``errors`` body."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def post_json(
    client: httpx.Client,
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    *,
    attempts: int = 3,
    sleep: Callable[[float], None] = time.sleep,
    idempotent: bool = True,
) -> dict[str, Any]:
    """POST ``payload`` and return the decoded JSON object.

    Rate-limited responses are retried, honoring ``Retry-After``. When ``idempotent``,
    network errors and temporarily unavailable responses are retried too; otherwise they
    are raised, because the server may have acted on the request already. An error status
    whose body carries GraphQL ``errors`` is returned rather than raised, so the caller can
    report the server's own message.
    """
    for attempt in range(1, attempts + 1):
        try:
            response = client.post(url, json=payload, headers=headers)
        except httpx.TransportError as exc:
            if attempt == attempts or not idempotent:
                raise HttpError(f"could not reach {url}: {exc}") from exc
            sleep(float(2**attempt))
            continue
        if attempt < attempts and _should_retry(response, idempotent):
            sleep(_retry_after(response, default=float(2**attempt)))
            continue
        return _decode(url, response)
    raise ValueError("attempts must be at least 1")


def _should_retry(response: httpx.Response, idempotent: bool) -> bool:
    if response.status_code == 429:
        return True
    # GitHub reports secondary rate limits as 403 with Retry-After.
    if response.status_code == 403 and "retry-after" in response.headers:
        return True
    return idempotent and response.status_code in RETRY_STATUSES


def _retry_after(response: httpx.Response, default: float) -> float:
    try:
        return min(float(response.headers["retry-after"]), 60.0)
    except (KeyError, ValueError):
        return default


def _decode(url: str, response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict) and (response.status_code < 400 or "errors" in body):
        return body
    snippet = response.text[:200].strip()
    raise HttpError(
        f"{url} returned HTTP {response.status_code}: {snippet}", status=response.status_code
    )
