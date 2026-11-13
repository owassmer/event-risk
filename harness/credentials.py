"""Credentials never enter a store: a store recomputes with no keys, and whoever opens it reads all of it.

Clients send keys in headers and record each request without them. The store refuses
any recorded value that carries one: a field named for a credential, a credential-named
query parameter or header line inside any string, an Authorization value, or the text
of a secret configured in the environment.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

# The environment variables whose values are secrets: every key a client reads, OpenFIGI's among them, and the keys
# a member who trades on Kalshi holds, a key's identifier with the key.
SECRET_ENV = ("MASSIVE_API_KEY", "ANTHROPIC_API_KEY", "BASETEN_API_KEY", "OPENFIGI_API_KEY", "KALSHI_API_KEY_ID",
              "KALSHI_PRIVATE_KEY")
# The environment variables that name a file whose body is a secret, as a private key kept as a PEM is: the body is
# sought whole, and so is each line of it, since a key can be quoted a line at a time; a PEM's armour lines
# (`-----BEGIN ...-----`) name the kind of key and are no secret.
SECRET_FILES = ("KALSHI_PRIVATE_KEY_PATH",)
_read: dict[tuple[str, float], list[str]] = {}

# Field names that hold credentials.
NAMES = frozenset({"apikey", "api_key", "api-key", "x-api-key", "x_api_key", "authorization", "access_token",
                   "auth_token", "client_secret", "secret", "password"})

# A credential-named parameter or header line inside a string: `?apiKey=...`, `&api_key=...`,
# `x-api-key: ...`, `password=...`.
_NAMED = re.compile(r"(?i)(?:^|[?&;#\s\"'{,])(apikey|api_key|api-key|x-api-key|x_api_key|access_token|"
                    r"auth_token|client_secret|secret|password|authorization)\s*[=:]")
# An Authorization value: `Bearer ...`, `Basic ...`.
_SCHEME = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")


def _file_secrets(path: str) -> list[str]:
    """A secret file's body and each of its lines, read once for each version of the file."""
    try:
        stamp = os.stat(path).st_mtime
    except OSError:  # no such file: nothing configured to seek
        return []
    if (path, stamp) not in _read:
        with open(path, encoding="utf-8", errors="replace") as f:
            body = f.read().strip()
        lines = [s for line in body.splitlines() if len(s := line.strip()) >= 8 and not s.startswith("-----")]
        _read[(path, stamp)] = ([body] if len(body) >= 8 else []) + lines
    return _read[(path, stamp)]


def secrets() -> list[str]:
    """The secrets configured in the environment: each secret variable's value, and each secret file's body."""
    found = [v for k in SECRET_ENV if len(v := os.environ.get(k, "").strip()) >= 8]
    for k in SECRET_FILES:
        if path := os.environ.get(k, "").strip():
            found += _file_secrets(os.path.expanduser(path))
    return found


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


def in_text(text: str) -> bool:
    """Whether a configured secret's text sits in `text`, a value's canonical JSON (harness.derive.engine.canonical):
    each secret sought as a JSON string would hold it, escaped as canonical escapes it. A secret in any string of the
    value is in its text so; the search is one pass of the text where find walks every node. A secret all of digits
    could be found in a number too, which find would not: the value is refused then, the safe side."""
    return any(json.dumps(k, ensure_ascii=False)[1:-1] in text for k in secrets())
