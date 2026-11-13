"""Synpath ids: `venue:native`, one per market and one per event.

A Synpath id names one listing on one venue and carries the venue in front of
the venue's own id, separated by a colon: `kalshi:KXFEDDECISION-26SEP-C25`,
`polymarket:2252244`, `polymarket_us:tec-mlb-nlchamp-2026-09-27-atl`,
`opinion:5342`, `hyperliquid:7173`, `betfair:1.234567890-47972`,
`smarkets:164973231-456060183`, `novig:01a1577c-d666-7e81-852e-68bed1446801`,
`matchbook:34569494493900023-34569495835700023-34569495836701023`,
`prophetx:44285780-90de769f4f4977a04446ed203e957351`,
`azuro:600610060000000000943019940000000000001802657415`, `myriad:56-2385` (a
network and a market number, which Myriad's API takes together;
`myriad:56-amm-2385` for an AMM market),
`gemini:GEMI-NBA-2610232000-CHI-MIL-M-MIL`. Nothing is hashed or re-keyed, so
the part after the colon is what the venue's own website and API call the
thing.

The venue in the id is what lets one field route a call: `synpath.Client`
reads the prefix and hands the call to that venue's adapter. Every venue
adapter also accepts the bare native id, because a caller holding a ticker
already knows which adapter it is talking to.

The colon is safe as a separator: no venue's native id contains one.
"""

from __future__ import annotations

def qualify(venue: str, native_id: str) -> str:
    """`("kalshi", "KXFOO-25")` -> `"kalshi:KXFOO-25"`. Idempotent: an id
    that already carries this venue's prefix is returned as is."""
    if native_id.startswith(f"{venue}:"):
        return native_id
    return f"{venue}:{native_id}"
