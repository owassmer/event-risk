"""Each exchange's official sessions as data: the days it trades, and when each session opens and closes, its early
closes and the closures no rule foresees included (the New York Stock Exchange closed on 2025-01-09, a national day of
mourning), from the exchange_calendars library (https://github.com/gerrymanoim/exchange_calendars, Apache 2.0), which
keeps 71 exchanges' calendars to their published hours and holidays, each by its ISO 10383 MIC, with their history
(Tokyo's close moved from 15:00 to 15:30 on 2024-11-05).

A listing abroad's close is struck when its exchange's session ends: the calendar's close for that session. A vendor's
own record of the session can run later, where its prices include a closing auction the calendar's hours end before
(Yahoo Finance's Hong Kong sessions end at 16:10, after the closing auction session, where the calendar's end at
16:00), or end earlier, where its intraday prices leave out an auction its daily close comes from (Yahoo's Korea
Exchange sessions end at 15:00, while its daily close is the 15:30 closing auction's, the calendar's close). A price
cannot be struck before either, so a close's moment is the later of the two (harness.venues.yahoo). An exchange the
library keeps no calendar for, under its own MIC or one whose calendar it shares (SHARED), takes the vendor's sessions
alone: Qatar, Dubai, Kuwait, Egypt, Vietnam, and Munich among those Yahoo lists.

Where an exchange's own published timetable states a regular close the library does not keep, the timetable's is the
close (TIMETABLES), from the first date its documents establish it, and it is the moment the close was struck, which no
vendor's record moves (stated): the library keeps fixed local hours for exchanges whose sessions follow New York's
daylight time, and a vendor's sessions are a fixed schedule too (Yahoo's end Santiago's at 17:00 all year). Set against
each session's last trade across a change of New York's daylight time, the library's close was an hour late for B3 and
the Mexican exchange while New York is on daylight time, for Buenos Aires an hour early while it is not, and for
Santiago an hour late from November to February; the 41 other exchanges with trades read agreed within minutes, or by
the later of the two. An early close the library keeps for one of them stays as it is.

Each exchange is named here by its MIC, as the data that names a listing's exchange gives it (each vendor client puts
its own codes in MICs). An exchange's calendar is built over the years a question asks of it, as far as the library
records them, once in a process, under a lock, and a calendar built before over years that include them answers it.
What a calendar answers for a day, whether it is a session and when it closes, is the same whatever span it was built
over (read across 20 years of six exchanges, each built over one year and over twenty), so an answer rests on the day
asked and on what the library records, never on the day the question is put: a store reads the same on any later
day.
"""

from __future__ import annotations

import threading
import warnings
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

import exchange_calendars as xc

# Seconds after a session's close its closing prices are published: a read of its closes before then is not them.
SETTLE = 15 * 60

# Exchanges that trade on another's calendar, its hours and its holidays, which the library keeps under that one:
# KOSDAQ on the Korea Exchange's, the Taipei Exchange on the Taiwan Stock Exchange's, Shenzhen on Shanghai's, and the
# National Stock Exchange of India on the Bombay Stock Exchange's.
SHARED = {"XKOS": "XKRX", "ROCO": "XTAI", "XSHE": "XSHG", "XNSE": "XBOM"}
# The library's own uses of pandas and NumPy features their makers have deprecated, which are the library's to change.
warnings.filterwarnings("ignore", module=r"exchange_calendars(\.|$)")
warnings.filterwarnings("ignore", message="DatetimeIndex.weekday is deprecated")
warnings.filterwarnings("ignore", message="Series.values returning an ndarray that drops timezone information")

_lock = threading.Lock()


def _new_york_daylight(day: date) -> bool:
    """Whether New York's clocks are on daylight time at noon of a day: an exchange that follows New York moves its
    session on the first weekday after New York's clocks change, which is always a Sunday."""
    noon = datetime(day.year, day.month, day.day, 12, tzinfo=ZoneInfo("America/New_York"))
    return bool(noon.dst())


# Each exchange's regular close where its own timetable states one the library does not keep: from when (the first
# session its documents establish it for), and the close in its local time on a session's date.
TIMETABLES: dict[str, tuple[date, Callable[[date], time]]] = {
    # B3 (Brasil, Bolsa, Balcao) moves its cash equity market's session each time New York's daylight time begins or
    # ends, its circulars say, "devido ao início do horário de verão nos Estados Unidos": trading to 16:55 and the
    # closing call 16:55 to 17:00 while New York is on daylight time, to 17:55 and 17:55 to 18:00 otherwise, from
    # 2019-11-04, when Brazil's own daylight time had ended for good and the library moves its close to 18:00: the
    # season from 2020-03-09 was set by Ofício Circular 007/2020-PRE (which 094/2020-PRE revokes), reported
    # "voltam a se encerrar às 17h ... até as 16h55min, com o início do leilão de fechamento":
    # https://www.jornaldocomercio.com/_conteudo/economia/2020/03/727864-negociacoes-da-b3-voltam-a-fechar-as-17h-a-partir-de-9-de-marco.html
    # Before it, B3's session moved with Brazil's daylight time as well (Circular Letter 012/2017-DO: its cash market
    # to 17:55 and its closing call to 18:00 local from 2017-10-16, in Brazil's summer), which this rule does not
    # state, so the library's close stands there. Ofícios Circulares 094/2020-PRE, from 2020-11-03; 020/2022-PRE;
    # 132/2024-PRE; 014/2025-PRE; 040/2025-VNC:
    # https://www.b3.com.br/data/files/71/43/8B/34/340A37109CFAD937AC094EA8/OC%20094-2020%20PRE%20Novos%20Hor%C3%A1rios%20de%20Negocia%C3%A7%C3%A3o%202020_01112020_R.pdf
    # https://www.b3.com.br/data/files/40/B4/53/5E/BC4EE710301EBDE7AC094EA8/OC%20020-2022%20PRE%20Novos%20Hor%C3%A1rios%20de%20Negocia%C3%A7%C3%A3o%20(PT).pdf
    # https://b3.com.br/data/files/50/55/44/FF/83D629106EEC8429AC094EA8/OC%20132-2024%20PRE%20%20Novos%20Horarios%20de%20negociacao%20(PT).pdf
    # https://b3.com.br/data/files/63/C3/B3/53/16A1591029BEEC39AC094EA8/OC%20014-2025%20PRE%20Novos%20Horarios%20de%20Negociacao%202025%20vf%20(PT).pdf
    # https://b3.com.br/data/files/A5/90/2C/43/244E9910F5D34899AC094EA8/OC%20040-2025-VNC%20NOVOS%20HORARIOS%20DE%20NEGOCIACAO_PT.pdf
    "BVMF": (date(2019, 11, 4), lambda day: time(17) if _new_york_daylight(day) else time(18)),
    # The Mexican exchange (BMV): its continuous market, and its orders at the closing price, end at 15:00, and at
    # 14:00 "durante el horario de verano EUA", "para fines de mantener homologado el horario de la sesión de remate
    # de la BMV con el horario del mercado de Nueva York": its Manual Operativo, version 1.96 of 2026-02-11; from the
    # first session after Mexico's own daylight time ended for good (2022-10-30) and New York's did (2022-11-06), the
    # two clocks differing thereafter only by New York's:
    # https://www.bmv.com.mx/docs-pub/MARCO_NORMATIVO/CTEN_MERMO/20260211_V1.96_PPP_PteConf_Suspensi%C3%B3n_Remate_.pdf
    "XMEX": (date(2022, 11, 7), lambda day: time(14) if _new_york_daylight(day) else time(15)),
    # Santiago's exchange states its equity session in each day's bulletin, "(DE 09:00 A 16:00 HORAS)", the same on
    # every bulletin read from 2019-07-15 to 2026-11-06, in both of New York's seasons and both of Chile's (its opening
    # 08:45 from October 2026), where the library closes it at 17:00 from November to February; each bulletin is named
    # ibd and its date as DDMMYY, the one of 2025-12-26:
    # https://cibe.bolsadesantiago.com/Documentos/EstadisticasyPublicaciones/Boletines%20Burstiles/ibd261225.pdf
    "XSGO": (date(2019, 7, 15), lambda day: time(16)),
    # Bolsas y Mercados Argentinos (BYMA): equities trade to 18:00 while New York is not on daylight time and to 17:00
    # while it is, from the session after each change of New York's, its communications say (18179, from 2022-11-07;
    # 18293, of 2023-03-03; 19031, from 2026-11-02), where the library closes it at 17:00 all year:
    # https://data-widgets.byma.com.ar/wp-content/uploads/2022/10/BYMA-COM18179-Horarios-BYMA-Desde-Nov2022.pdf
    # https://data-widgets.byma.com.ar/wp-content/uploads/2022/10/BYMA-COM18293-Horarios-de-Negociacion-Liquidacion-y-recepcion-de-informacion.pdf
    # https://www.byma.com.ar/comunicados/com19031-horarios
    "XBUE": (date(2022, 11, 7), lambda day: time(17) if _new_york_daylight(day) else time(18)),
}


# Each exchange's calendars built in this process, by the years each spans: the calendar, or why the library cannot
# build it over them (Unbuilt).
_built: dict[str, dict[tuple[int, int], Any]] = {}


def _bounds(mic: str) -> tuple[int | None, int | None]:
    """The years the library can build an exchange's calendar over, where it records its holidays only between two
    dates (Bombay's to 2026, Saudi Arabia's from 2021: each calendar's own bound_min and bound_max); None where its
    rules reach any year."""
    dispatcher = xc.calendar_utils.global_calendar_dispatcher
    found = dispatcher._calendar_factories.get(dispatcher.resolve_alias(mic))
    if found is None:
        return None, None
    lo, hi = found.bound_min(), found.bound_max()
    return (lo.year if lo is not None else None), (hi.year if hi is not None else None)


def _calendar(mic: str, first: int, last: int) -> Any:
    """An exchange's calendar over the years `first` to `last`, as far as the library records them: one built before
    over years that include them, or one built over them now. A day outside what the library records has no session
    and no close."""
    with _lock:
        built = _built.setdefault(mic, {})
        bound_lo, bound_hi = _bounds(mic)
        start, end = max(first, bound_lo or first), min(last, bound_hi or last)
        if start > end:
            raise Unbuilt(f"the library records no year of {mic} from {first} to {last}")
        found = built.get((start, end))
        if found is None:
            found = next((c for (lo, hi), c in built.items()
                          if lo <= start and end <= hi and not isinstance(c, Exception)), None)
        if found is None:
            try:
                found = xc.get_calendar(mic, start=f"{start}-01-01", end=f"{end}-12-31")
            except xc.errors.InvalidCalendarName:
                raise
            except (ValueError, NotImplementedError) as e:
                found = Unbuilt(f"the calendar of {mic} cannot be built from {start} to {end}: {e}")
            built[(start, end)] = found
    if isinstance(found, Exception):
        raise found
    return found


class Unbuilt(Exception):
    """An exchange whose calendar the library names but cannot build (read live: Tel Aviv's, whose special closes
    fall on Sundays though it trades Monday to Friday since 2026; Athens', not implemented)."""


def calendar(mic: str | None, first: date, last: date | None = None) -> Any | None:
    """An exchange's calendar over the days from `first` to `last` (or `first` alone); None for an exchange the
    library keeps none for, or whose calendar it cannot build, whose sessions are then its price source's own
    (harness.venues.yahoo)."""
    if not mic:
        return None
    try:
        return _calendar(SHARED.get(mic, mic), first.year, (last or first).year)
    except (xc.errors.InvalidCalendarName, Unbuilt):
        return None


def stated(mic: str | None) -> bool:
    """Whether an exchange's closes are its own timetable's (TIMETABLES), the moment each was struck."""
    return SHARED.get(mic or "", mic) in TIMETABLES


def _close(found: Any, mic: str, session: Any) -> datetime:
    """When a session closed: the library's close, or where the exchange's own timetable states its regular close
    (TIMETABLES), that one, for a session on or after the timetable's first; an early close the library keeps for it,
    any close other than its regular one for that date, stays."""
    close = found.session_close(session).to_pydatetime()
    day = session.date()
    stated = TIMETABLES.get(SHARED.get(mic, mic))
    if stated is None or day < stated[0]:
        return close
    zone = ZoneInfo(str(found.tz))
    regular = [t for since, t in found.close_times if since is None or since.date() <= day][-1]
    if close.astimezone(zone).time() != regular:
        return close
    return datetime.combine(day, stated[1](day), zone)


def closes(mic: str | None, start: str, end: str) -> dict[str, float]:
    """When each session of an exchange from `start` to `end` closed, in Unix seconds, by its date there."""
    found = calendar(mic, date.fromisoformat(start), date.fromisoformat(end))
    if found is None:
        return {}
    lo, hi = max(date.fromisoformat(start), found.first_session.date()), min(date.fromisoformat(end),
                                                                             found.last_session.date())
    if lo > hi:
        return {}
    days = found.sessions_in_range(lo.isoformat(), hi.isoformat())
    return {d.date().isoformat(): _close(found, mic, d).timestamp() for d in days}


def is_session(mic: str, day: date) -> bool:
    found = calendar(mic, day)
    return bool(found is not None and found.first_session.date() <= day <= found.last_session.date()
                and found.is_session(day.isoformat()))


def close_of(mic: str, day: date) -> datetime | None:
    """When an exchange's session on `day` closed, in UTC; None where it held none."""
    if not is_session(mic, day):
        return None
    found = calendar(mic, day)
    return _close(found, mic, found.date_to_session(day.isoformat(), "none")).astimezone(timezone.utc)


def holidays(mic: str, year: int) -> set[date]:
    """The weekdays of a year an exchange held no session: its holidays, observed, and its unforeseen closures. A
    calendar spans whole years (_calendar), so a weekday of its year before its first session or after its last is one
    the exchange holds none on; a year the library does not record has none."""
    found = calendar(mic, date(year, 1, 1))
    if found is None:
        return set()
    first, last = found.first_session.date(), found.last_session.date()
    days = (date(year, 1, 1) + timedelta(days=k) for k in range((date(year + 1, 1, 1) - date(year, 1, 1)).days))
    return {d for d in days if d.weekday() < 5
            and not (first <= d <= last and found.is_session(d.isoformat()))}
