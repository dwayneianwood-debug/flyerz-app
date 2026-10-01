"""Gemini generateContent URL. The model name is configurable.

gemini-2.0-flash was shut down on 1 June 2026. The replacement for that
OCR call is gemini-3.6-flash. Set GEMINI_OCR_MODEL or GEMINI_MODEL to override.
Set GEMINI_API_BASE to point at another host. No key is required to build the URL.
"""

from __future__ import annotations

import os

DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"
DEFAULT_GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"


def gemini_model() -> str:
    chosen = (os.environ.get("GEMINI_OCR_MODEL") or os.environ.get("GEMINI_MODEL") or "").strip()
    return chosen or DEFAULT_GEMINI_MODEL


def gemini_api_base() -> str:
    raw = (os.environ.get("GEMINI_API_BASE") or "").strip().rstrip("/")
    return raw or DEFAULT_GEMINI_API_BASE


def gemini_generate_content_url(api_key: str = "") -> str:
    url = f"{gemini_api_base()}/models/{gemini_model()}:generateContent"
    key = (api_key or "").strip()
    if key:
        return f"{url}?key={key}"
    return url
