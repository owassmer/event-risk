"""The Claude key, in one place: the client the analyst runs on, and the headers of the web's own requests to the
Messages API. Every request goes on ANTHROPIC_API_KEY, with nothing added."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

KEY = "ANTHROPIC_API_KEY"
API = "https://api.anthropic.com/v1/messages"


@dataclass(frozen=True)
class Credential:
    name: str  # the environment variable it is read from, which an error line names
    secret: str


class NoCredential(Exception):
    """The key is not set."""


def credential() -> Credential | None:
    """The key; None when it is not set."""
    value = os.environ.get(KEY, "").strip()
    return Credential(KEY, value) if value else None


def headers(cred: Credential) -> dict[str, str]:
    """The headers of one raw request to the Messages API."""
    return {"anthropic-version": "2023-06-01", "content-type": "application/json", "x-api-key": cred.secret}


def client(cred: Credential | None = None, **kw: Any):
    """The SDK client on the key."""
    import anthropic

    cred = cred if cred is not None else credential()
    if cred is None:
        raise NoCredential(f"No Claude key ({KEY} in .env)")
    return anthropic.Anthropic(api_key=cred.secret, **kw)
