"""Čistá rozhodovacia logika – bez Telegramu, DB, siete. Všetko testovateľné.

Tick-engine: každých pár sekúnd sa zavolá `plan(snapshot)` a vráti zoznam
akcií (poslať výzvu, ohlásiť splnenie …). Žiadne naplánované joby, ktoré by
sa strácali pri reštarte – stav (koľko výziev už odišlo a kedy) je v DB.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta, timezone

from trener.model import (DONE, EVENING, FAILED, FROZEN, MORNING, SESSIONS, Day, NagState,
                          Report, Settings, Snapshot, hhmm_to_time)

WAKE_FLOOR = dtime(4, 0)      # wake signál pred 04:00 nie je ranné vstávanie
MAX_NAG_DELAY = timedelta(hours=3)   # po výpadku nedoháňame výzvy staršie ako 3 h


def elapsed(later: datetime, earlier: datetime) -> timedelta:
    """Skutočný uplynutý čas. Dva aware datetime s TÝM ISTÝM tzinfo Python odčítava po
    nástenných hodinách (pri jesennej zmene času by vyšla hodina navyše/chýbala) –
    preto cez UTC."""
    return later.astimezone(timezone.utc) - earlier.astimezone(timezone.utc)


# ── hlásenia ─────────────────────────────────────────────────────────────────

@dataclass
class ApplyResult:
    day: Day
    added: dict[str, int]          # koľko sa pridalo do ktorej fázy (záporné = oprava dole)
    completed_now: bool            # deň sa práve týmto hlásením splnil
    session_completed_now: list[str]


def apply_reports(day: Day, reports: list[Report]) -> ApplyResult:
    """Pripočíta (alebo nastaví) hlásenia; nikdy nemení cieľ ani nastavenia."""
    was_done = day.done
    was_session_done = {s: day.session_done(s) for s in SESSIONS}
    added = {MORNING: 0, EVENING: 0}
    m, e = day.morning, day.evening
    for r in reports:
        cur = m if r.session == MORNING else e
        other = e if r.session == MORNING else m
        if r.absolute and r.total:
            new = r.n - other            # „spolu 10“: dnešný súčet = 10
        elif r.absolute:
            new = r.n
        else:
            new = cur + r.n              # aj záporné n = oprava dole
        new = max(new, 0)
        added[r.session] += new - cur
        if r.session == MORNING:
            m = new
        else:
            e = new
    new_day = day.copy(morning=m, evening=e)
    completed_now = new_day.done and not was_done
    sess_now = [s for s in SESSIONS if new_day.session_done(s) and not was_session_done[s]]
    return ApplyResult(new_day, added, completed_now, sess_now)


def default_session(now: datetime, settings: Settings, day: Day | None = None) -> str:
    """Do ktorej fázy patrí číslo bez upresnenia.

    Základ je hodinami: pred večerným časom ráno, potom večer. Ak je však vedro tej
    fázy už plné a deň ešte nie je splnený, číslo patrí druhej fáze – kliky navyše
    dopoludnia sú v skutočnosti náskok na večer (nemá zmysel mať „ráno 10/5“).
    """
    base = MORNING if now.time() < hhmm_to_time(settings.evening_time) else EVENING
    if day is None or day.done:
        return base
    other = EVENING if base == MORNING else MORNING
    if day.session_done(base) and not day.session_done(other):
        return other
    return base


# ── streak a prechod dňa ─────────────────────────────────────────────────────

def compute_streak(days: list[Day], today: date) -> int:
    """Streak = počet po sebe splnených dní končiac včerajškom (dnešok sa pridá, ak je splnený).

    Zamrazené dni streak neprerušujú (ako Duolingo streak freeze), ale ani nepridávajú.
    """
    streak = 0
    for d in sorted(days, key=lambda x: x.date):
        if d.date > today:
            continue
        st = d.status(today)
        if st == DONE:
            streak += 1
        elif st == FROZEN:
            continue
        elif st == FAILED:
            streak = 0
        # OPEN (dnešok nesplnený) streak zatiaľ neprerušuje
    return streak


def streak_after(days: list[Day], d: date, today: date | None = None) -> int:
    """Streak k danému dňu (pre stĺpec „Streak“ v tabuľke).

    Minulý deň sa hodnotí ako uzavretý (nesplnený = reset); dnešok ako otvorený.
    """
    if today is None or d < today:
        ref = d + timedelta(days=1)
    else:
        ref = today
    return compute_streak([x for x in days if x.date <= d], ref)


def next_goal(prev: Day | None, settings: Settings, seed_goal: int) -> int:
    """Cieľ nového dňa: po splnenom dni +prírastok, inak rovnaký. Bez trestov."""
    if prev is None:
        return seed_goal
    if prev.done:
        return prev.goal + max(settings.increment, 0)
    return prev.goal


def fill_missing_days(days: list[Day], today: date, settings: Settings, seed_goal: int,
                      frozen_since: date | None = None) -> list[Day]:
    """Doplní chýbajúce dni od posledného známeho po dnešok (výpadok bota).

    Chýbajúce minulé dni sa berú ako zamrazené (bot nebežal, nie je to chyba
    používateľa) – streak sa nestratí, používateľ si ich môže doplniť v tabuľke.
    """
    by_date = {d.date: d for d in days}
    past = [d for d in by_date if d <= today]
    if not past:
        # žiadny dnešný ani minulý deň (prázdna DB alebo len budúce riadky od používateľa)
        return [Day(today, seed_goal, frozen=settings.frozen)]
    last = max(past)
    new: list[Day] = []
    d = last + timedelta(days=1)
    while d <= today:
        prev = by_date[d - timedelta(days=1)]
        goal = next_goal(prev, settings, seed_goal)
        frozen = settings.frozen or d < today
        new.append(Day(d, goal, frozen=frozen))
        by_date[d] = new[-1]
        d += timedelta(days=1)
    return new


# ── výzvy v chate (nag) ──────────────────────────────────────────────────────

def session_start(now: datetime, settings: Settings, session: str) -> datetime:
    t = hhmm_to_time(settings.session_time(session))
    return now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)


def current_session(now: datetime, settings: Settings, woke_up: bool = False) -> str | None:
    """Ktorá fáza práve beží: ráno od morning_time (alebo od wake signálu ≥ 04:00),
    večer od evening_time do polnoci. Mimo toho None."""
    ms = session_start(now, settings, MORNING)
    es = session_start(now, settings, EVENING)
    if now >= es:
        return EVENING
    if now >= ms or (woke_up and now.time() >= WAKE_FLOOR):
        return MORNING
    return None


def nag_due(now: datetime, settings: Settings, session: str, day: Day, nag: NagState,
            woke_up: bool = False) -> bool:
    """Má odísť ďalšia výzva do chatu?

    - nikdy, keď je tréner zamrazený, deň splnený alebo fáza hotová,
    - max `nag_max` výziev na fázu, rozostup `nag_interval_min`,
    - prvá výzva o začiatku fázy (ráno aj hneď po wake signáli), ďalšie po intervale,
    - po výpadku bota nedoháňame staré výzvy (start fázy starší než 3 h → prvá výzva až
      keď to má zmysel – teda vôbec nie, ak už uplynulo priveľa).
    """
    if settings.frozen or day.frozen or day.done or day.session_done(session):
        return False
    if current_session(now, settings, woke_up) != session:
        return False
    if nag.sent >= max(settings.nag_max, 0):
        return False
    interval = timedelta(minutes=max(settings.nag_interval_min, 1))
    if nag.sent == 0:
        start = session_start(now, settings, session)
        if session == MORNING and woke_up and now < start:
            start = now  # wake signál: prvá výzva hneď
        if elapsed(now, start) > MAX_NAG_DELAY:
            return False
        return now >= start
    assert nag.last_at is not None
    if elapsed(now, nag.last_at) < interval:
        return False
    # ďalšie výzvy len kým je fáza „živá“: ráno do večerného času, večer do polnoci
    return True


# ── plán ticku ───────────────────────────────────────────────────────────────

@dataclass
class Action:
    kind: str                 # nag | announce_done
    session: str | None = None


def plan(snap: Snapshot) -> list[Action]:
    actions: list[Action] = []
    for s in SESSIONS:
        nag = snap.nags.get(s) or NagState(snap.today.date, s)
        if nag_due(snap.now, snap.settings, s, snap.today, nag, snap.woke_up):
            actions.append(Action("nag", s))
            break  # najviac jedna výzva na tick
    return actions


def is_new_day(now: datetime, current: date) -> bool:
    return now.date() != current


def reminder_due_local(day: date, hhmm: str, tz) -> datetime:
    t = hhmm_to_time(hhmm)
    return datetime.combine(day, t, tzinfo=tz)
