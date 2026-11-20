"""FinanceDatabase's equities (https://github.com/JerBouma/FinanceDatabase, MIT): one file of every listed company's
listings, each with its company's classification, home, description and identifiers; public and with no key.

`GET https://raw.githubusercontent.com/JerBouma/FinanceDatabase/main/compression/equities.bz2` answers a bz2-compressed
CSV, a row per listing (read live: 121,603 rows, 18.6 MB, 110 MB decompressed), its columns: `symbol`, Yahoo Finance's
symbol for the listing (`REL.L`, `2330.TW`, a US listing's ticker with a class after a dash, `BRK-B`); `name`; `summary`, what the company does in its own record's words; `currency`; `sector`,
`industry_group` and `industry`, the company's place in the database's one classification for every market (12
sectors, 26 industry groups, 76 industries; an industry left empty on some 6,500 rows); `exchange`, Yahoo's code for
the listing's exchange (`NMS`, `NYQ`, `LSE`, `TAI`), `mic` and `market`, its name; `country`, the company's home, by
name; `state`, `city`, `zipcode`, `website`, `market_cap`; `isin`, `cusip`, `figi`, `composite_figi` and
`shareclass_figi`; and `delisted`, `True` or `False`. A company's listings on several exchanges are rows of one ISIN,
each with the company's classification. A US line of a company registered abroad, a depositary receipt among them,
carries the ISIN of the company's ordinary shares (read live: RELX's NYSE receipt GB00B2B0DG97, ASML's New York
registry shares NL0010273215), so an ISIN's rows are its company's listings and not its share class's: the file
classifies and describes a company, and OpenFIGI's mappings identify a security (harness.venues.openfigi).

The file is kept on this machine (`observations/equities.sqlite`, beside the shared observations) and asked again once
a day with the ETag it was read under, which GitHub answers 304 while the file is unchanged; a check that fails keeps
the file held. What a store reads of it is kept as the store's data (`equity`): `{"status", "subject", "rows"}`, each
row the file's columns but its summary, the rows of one listing (`symbol:{SYMBOL}`) or of one ISIN (`isin:{ISIN}`),
`NOT_FOUND` where the file holds none.
"""

from __future__ import annotations

import bz2
import csv
import io
import logging
import os
import sqlite3
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any, Callable

import httpx

from harness.venues.http import VenueError, client, now

URL = "https://raw.githubusercontent.com/JerBouma/FinanceDatabase/main/compression/equities.bz2"
PATH = Path(__file__).resolve().parent.parent.parent / "observations" / "equities.sqlite"
TIMEOUT = 60.0  # seconds the file's read is given: 18.6 MB, read live in about a second
TRIES = 3
COLUMNS = ("symbol", "name", "currency", "sector", "industry_group", "industry", "exchange", "mic", "market",
           "country", "isin", "cusip", "figi", "composite_figi", "shareclass_figi", "delisted")

log = logging.getLogger("harness.venues.equities")


class Equities:
    def __init__(self, *, path: Path | str = PATH, http: httpx.Client | None = None, pause: float = 1.0):
        self.path = Path(path)
        self.http = http or client(TIMEOUT)
        self.pause = pause
        self._lock = threading.Lock()
        self._checked: str | None = None  # the day the file was last asked for

    # ---------------------------------------------------------------- the file

    def _meta(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            with sqlite3.connect(self.path) as db:
                return dict(db.execute("SELECT key, value FROM meta").fetchall())
        except sqlite3.DatabaseError:
            return {}

    def _build(self, body: bytes, etag: str | None, retrieved: str) -> None:
        """The file's rows into a new database, put in place of the one held."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        building = self.path.with_name(f"{self.path.name}.{os.getpid()}.{threading.get_ident()}")
        building.unlink(missing_ok=True)
        with sqlite3.connect(building) as db:
            db.execute(f"CREATE TABLE equities ({', '.join(COLUMNS)}, summary)")
            db.execute("CREATE TABLE meta (key PRIMARY KEY, value)")
            with bz2.open(io.BytesIO(body), "rt", encoding="utf-8", newline="") as text:
                rows = ((*(r.get(c) or None for c in COLUMNS[:-1]), 1 if r.get("delisted") == "True" else 0,
                         r.get("summary") or None) for r in csv.DictReader(text) if r.get("symbol"))
                db.executemany(f"INSERT INTO equities VALUES ({', '.join('?' * (len(COLUMNS) + 1))})", rows)
            db.execute("CREATE INDEX by_symbol ON equities (symbol)")
            db.execute("CREATE INDEX by_isin ON equities (isin)")
            db.executemany("INSERT INTO meta VALUES (?, ?)", [("etag", etag or ""), ("retrieved_at", retrieved)])
        os.replace(building, self.path)

    def _ready(self) -> None:
        """The file held and asked for once today: read again where GitHub answers it changed; a check that fails keeps
        the file held, and fails the read only where none is held."""
        today = date.today().isoformat()
        with self._lock:
            if self._checked == today and self.path.exists():
                return
            meta = self._meta()
            held = bool(meta) and self.path.exists()
            failure = None
            for attempt in range(TRIES):
                retrieved = now()
                try:
                    response = self.http.get(URL, headers={"If-None-Match": meta["etag"]} if held and meta.get("etag")
                                             else {})
                except httpx.HTTPError as e:
                    failure = f"FinanceDatabase's equities: {e}"
                else:
                    if response.status_code == 304:
                        break
                    if response.status_code == 200:
                        try:
                            self._build(response.content, response.headers.get("etag"), retrieved)
                            break
                        except (OSError, EOFError, csv.Error, sqlite3.Error) as e:
                            failure = f"FinanceDatabase's equities could not be read: {e}"
                    else:
                        failure = f"FinanceDatabase's equities answered {response.status_code}"
                if attempt + 1 < TRIES:
                    time.sleep(self.pause * (attempt + 1))
            else:
                if not held:
                    raise VenueError(failure or "FinanceDatabase's equities did not answer")
                log.warning("%s; the file held is read", failure)
            self._checked = today

    def _query(self, sql: str, args: tuple) -> list[dict]:
        self._ready()
        with sqlite3.connect(f"file:{self.path}?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute(sql, args).fetchall()]

    # ---------------------------------------------------------------- reads

    def rows(self, *, symbol: str | None = None, isin: str | None = None) -> list[dict]:
        """The rows of a listing by its symbol, or of an ISIN, each the file's columns but its summary, `delisted` a
        boolean."""
        column, value = ("symbol", symbol) if symbol else ("isin", isin)
        found = self._query(f"SELECT {', '.join(COLUMNS)} FROM equities WHERE {column} = ? ORDER BY symbol",
                            (str(value or "").strip().upper(),))
        return [{**r, "delisted": bool(r["delisted"])} for r in found]

    def summary(self, symbol: str) -> str | None:
        """What a listing's company does, in its own record's words."""
        found = self._query("SELECT summary FROM equities WHERE symbol = ? AND summary IS NOT NULL",
                            (symbol.strip().upper(),))
        return found[0]["summary"] if found else None

    def equity(self, subject: str, day: str) -> dict:
        """The rows of `symbol:{SYMBOL}` or `isin:{ISIN}`, as the store keeps them."""
        how, _, value = subject.partition(":")
        if how not in ("symbol", "isin") or not value:
            raise ValueError(f"an equity is read as symbol:{{SYMBOL}} or isin:{{ISIN}}, not {subject}")
        found = self.rows(**{how: value})
        retrieved = self._meta().get("retrieved_at") or now()
        return {"payload": {"status": "OK" if found else "NOT_FOUND", "subject": subject, "rows": found},
                "request": {"method": "GET", "url": URL}, "retrieved_at": retrieved, "range": (day, day)}

    def fetchers(self) -> dict[str, Callable[[str, Any], dict]]:
        def equity(subject: str, covering: Any) -> dict:
            return self.equity(subject, covering[1])

        equity.many = lambda subjects, covering: {s: self.equity(s, covering[1]) for s in subjects}  # type: ignore
        return {"equity": equity}
