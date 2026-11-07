"""The Claude key, in one place: the client the analyst runs on, and the headers of the web's own requests to the
Messages API. Every request goes on ANTHROPIC_API_KEY, with nothing added."""

from __future__ import annotations

import os
from typing import Any

KEY = "ANTHROPIC_API_KEY"
API = "https://api.anthropic.com/v1/messages"


class NoCredential(Exception):
    """The key is not set."""


def api_key() -> str | None:
    """The key, or None when it is not set."""
    return os.environ.get(KEY, "").strip() or None


def headers(key: str) -> dict[str, str]:
    """The headers of one raw request to the Messages API."""
    return {"anthropic-version": "2023-06-01", "content-type": "application/json", "x-api-key": key}


def client(**kw: Any):
    """The SDK client on the key."""
    import anthropic

    key = api_key()
    if key is None:
        raise NoCredential(f"No Claude key ({KEY} in .env)")
    return anthropic.Anthropic(api_key=key, **kw)
