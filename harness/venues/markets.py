"""What every prediction-market venue's reads share. The venues are read through our copy of Synpath's read paths
(harness.venues.synpath, from https://github.com/Synpath-ai/synpath 0.11.0, MIT): each venue reached through the
surface that serves it best, its own search among them, and its answers read by Synpath's pure normalizers over the
venue's raw payloads, which the store keeps as data. Each venue's module is a plain class over Synpath's HTTP client,
with what the store reads besides.

A family is a venue's own grouping of markets, a Kalshi, Polymarket, or Polymarket US event, named
`family:{venue}:{id}` after Synpath's event id and titled by its venue's title. A search returns a venue's families in
its own order of relevance, at most FAMILIES of them; a family with more than LARGE open markets is given as its count
and its span, and read whole by its id.

A market's book is read with its last trade, each venue's reading one payload: the book's levels give its best bid and
ask, its price where both sides quote, and its depth (harness.derive.markets), and its last trade is the latest trade
on the market, in YES terms, with its time.

Every read names its client: USER_AGENT, this package and its version. A venue's edge can throttle a generic library's
own User-Agent far below its documented rate: Polymarket US's gateway answered httpx's default with 429 (`Retry-After:
10`) after five book reads in a row, against its documented 25 a second
(https://docs.polymarket.us/api-reference/rate-limits), and answered the same reads named as this client in full.

A read that fails just now (the network, a timeout, a venue unavailable or throttling) is tried again, ATTEMPTS
times in all; past them it is the venue not answering (VenueError), which is logged for us and reaches the analyst as
a read to try again shortly. A venue that refuses the request itself (VenueRefused) is not asked again, since asking
again would not change its answer. A venue's answer that it lists no such thing is a LookupError.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from harness.venues.synpath.errors import BadRequest, MarketNotFound, SynpathError

from harness.venues.http import TIMEOUT, USER_AGENT, VenueError, VenueRefused, client  # noqa: F401

NY = ZoneInfo("America/New_York")
FAMILIES = 10  # the families a search returns from each venue, the first by the venue's own relevance
# For a past as-of, a venue is asked for a page of this many, its families of every status, so that its first FAMILIES
# with a market open on the as-of are found among them: the venue ranks as of today.
PAST_PAGE = 50
LARGE = 12  # past this many open markets, a family in a search is its count and its span
ATTEMPTS = 3  # a read tried this many times in all before the venue is taken as not answering
WAIT = 1.0  # seconds before the second try, twice that before the third

log = logging.getLogger("harness.venues")



def ny_date(value: Any) -> str | None:
    """The New York date of a time: an ISO string, or milliseconds since the epoch."""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            moment = datetime.fromtimestamp(value / 1000, timezone.utc)
        else:
            moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return moment.astimezone(NY).date().isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


def moment(value: Any) -> str | None:
    """A time as one form gives every venue's: UTC to the second, `YYYY-MM-DDTHH:MM:SSZ`; from an ISO string, or
    milliseconds since the epoch."""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            at = datetime.fromtimestamp(value / 1000, timezone.utc)
        else:
            at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if at.tzinfo is None:
            return None
        return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OverflowError):
        return None


def family_id(venue: str, native: str) -> str:
    return f"family:{venue}:{native}"


def number(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def read(what: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """One read through Synpath: tried again where the venue failed just now, a venue's answer that it lists no such
    thing as LookupError, and a failure past ATTEMPTS as VenueError, logged."""
    last: Exception | None = None
    for attempt in range(ATTEMPTS):
        try:
            return fn(*args, **kwargs)
        except MarketNotFound as e:
            raise LookupError(str(e)) from None
        except BadRequest as e:  # the venue refused the request itself: asking again would not change its answer
            log.warning("%s: refused: %s", what, e)
            raise VenueRefused(f"{what}: {e}") from None
        except SynpathError as e:
            last = e
            if attempt < ATTEMPTS - 1:
                time.sleep(WAIT * (attempt + 1))
    log.warning("%s: no answer after %d tries: %s", what, ATTEMPTS, last)
    raise VenueError(f"{what}: {last}")
