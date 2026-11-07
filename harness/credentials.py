"""Credentials never enter a store: a store recomputes with no keys, and whoever opens it reads all of it.

Clients send keys in headers and record each request without them. The store refuses
any recorded value that carries one: a field named for a credential, a credential-named
query parameter or header line inside any string, an Authorization value, or the text
of a secret configured in the environment.
"""

from __future__ import annotations

import os
import re
from typing import Any

# The environment variables whose values are secrets.
SECRET_ENV = ("MASSIVE_API_KEY", "ANTHROPIC_API_KEY", "BASETEN_API_KEY")

# Field names that hold credentials.
NAMES = frozenset({"apikey", "api_key", "api-key", "x-api-key", "x_api_key", "authorization", "access_token",
                   "auth_token", "client_secret", "secret", "password"})

# A credential-named parameter or header line inside a string: `?apiKey=...`, `&api_key=...`,
# `x-api-key: ...`, `password=...`.
_NAMED = re.compile(r"(?i)(?:^|[?&;#\s\"'{,])(apikey|api_key|api-key|x-api-key|x_api_key|access_token|"
                    r"auth_token|client_secret|secret|password|authorization)\s*[=:]")
# An Authorization value: `Bearer ...`, `Basic ...`.
_SCHEME = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")


def secrets() -> list[str]:
    """The secrets configured in the environment."""
    return [v for k in SECRET_ENV if len(v := os.environ.get(k, "").strip()) >= 8]


def find(obj: Any, *, names: bool = True) -> str | None:
    """Where a credential sits in `obj`, or None. With `names`, credential-named fields,
    parameters, and Authorization values count; a configured secret's text always does."""
    keys = secrets()

    def walk(x: Any, path: str) -> str | None:
        if isinstance(x, dict):
            for k, v in x.items():
                if names and isinstance(k, str) and k.lower() in NAMES:
                    return f"{path}{k}"
                found = walk(k, f"{path}<key>") if isinstance(k, str) else None
                found = found or walk(v, f"{path}{k}.")
                if found:
                    return found
            return None
        if isinstance(x, (list, tuple)):
            for i, v in enumerate(x):
                found = walk(v, f"{path}{i}.")
                if found:
                    return found
            return None
        if isinstance(x, str):
            if any(k in x for k in keys):
                return f"{path.rstrip('.')} (a configured secret)"
            if names and (_NAMED.search(x) or _SCHEME.search(x)):
                return f"{path.rstrip('.')} (a credential in the text)"
        return None

    return walk(obj, "")
