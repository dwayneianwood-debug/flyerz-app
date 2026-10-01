"""Headers for calls to Replicate, Gemini, and other outside services.

Python's urllib sends no User-Agent. Cloudflare answers that with HTTP 403
"error code: 1010", so a Replicate account that has credit still looks closed.
Every external request from this app uses the same header.
"""

from __future__ import annotations

from typing import Mapping, Optional

USER_AGENT = "flyerz-artwork-intelligence/1.0"


def external_headers(extra: Optional[Mapping[str, object]] = None) -> dict:
    """Return headers that always include the Flyerz User-Agent."""
    headers = {}
    if extra:
        for key, value in extra.items():
            if value is None:
                continue
            headers[str(key)] = str(value)
    headers["User-Agent"] = USER_AGENT
    return headers
