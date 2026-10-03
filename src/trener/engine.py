"""Čistá rozhodovacia logika – bez Telegramu, DB, siete. Všetko testovateľné.

Tick-engine: každých pár sekúnd sa zavolá `plan(snapshot)` a vráti zoznam
akcií (poslať výzvu, ohlásiť splnenie …). Žiadne naplánované joby, ktoré by
sa strácali pri reštarte – stav (koľko výziev už odišlo a kedy) je v DB.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta, timezone

from trener.model import (EVENING, MORNING, SESSIONS, Day, NagState, Report, Settings, Snapshot,
                          hhmm_to_time, week_start)

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
    # Prehodiť fázu má zmysel len medzi fázami, ktoré v ten deň naozaj sú. V utorok
    # večer nie je kam preliať – kliky o 20:00 sa zapíšu na večer, nech sedí, kedy
    # vznikli. Stráž musí byť na OBOCH stranách: neplánovaná fáza je „hotová“ hneď,
    # takže bez nej by sa večerné kliky ticho presypali do rána.
    if (day.session_planned(base) and day.session_done(base)
            and day.session_planned(other) and not day.session_done(other)):
        return other
    return base


# ── streak a prechod dňa ─────────────────────────────────────────────────────

# stavy týždňa
WEEK_DONE = "week_done"        # všetky tréningové dni splnené
WEEK_FAILED = "week_failed"    # aspoň jeden uzavretý tréningový deň nesplnený
WEEK_OPEN = "week_open"        # ešte beží, zatiaľ bez zlyhania
WEEK_EMPTY = "week_empty"      # samé voľno alebo zamrazené – streak neprerušuje ani nepridáva


def week_status(days: dict[date, Day], monday: date, today: date, settings: Settings,
                since: date | None = None) -> str:
    """Ako dopadol týždeň začínajúci daným pondelkom.

    Deň bez záznamu sa hodnotí podľa plánu: ak naň plán nič nepredpisuje (nedeľa),
    nevadí; ak predpisuje a je v minulosti, týždeň padá. Zamrazené dni sa preskakujú
    presne ako predtým – sú to choroby a dovolenky, nie zlyhania.

    `since` je prvý deň, o ktorom vôbec máme vedieť. Dni pred ním bot ešte nebežal –
    nie sú to zlyhania, inak by prvý rozbehový týždeň nikdy nešiel započítať.
    """
    splnene = False
    otvorene = False
    for i in range(7):
        d = monday + timedelta(days=i)
        if since is not None and d < since:
            continue                       # vtedy bot ešte nebežal
        day = days.get(d)
        if day is not None:
            goal = day.goal
        else:
            goal = settings.goal_for(d)   # deň bez záznamu sa hodnotí podľa plánu
        if goal <= 0:
            continue                       # voľno – netreba nič
        if day is not None and day.done:
            splnene = True
            continue
        if day is not None and day.frozen:
            continue                       # ❄️ neprerušuje
        if d >= today:
            otvorene = True                # ešte sa dá stihnúť
            continue
        return WEEK_FAILED
    if otvorene:
        return WEEK_OPEN
    return WEEK_DONE if splnene else WEEK_EMPTY


def compute_streak(days: list[Day], today: date, settings: Settings) -> int:
    """Streak = počet po sebe **splnených týždňov** (pondelok–nedeľa).

    Týždeň je splnený, keď si zvládol všetky tréningové dni; nedeľa je voľno a
    netreba v nej nič. Bežiaci týždeň sa započíta, až keď je celý hotový – čiže
    typicky v sobotu večer. Celý zamrazený týždeň streak neprerušuje ani nepridáva.
    """
    by_date = {d.date: d for d in days}
    if not by_date:
        return 0
    zaciatok = min(by_date)
    najstarsi = week_start(zaciatok)
    wk = week_start(today)
    streak = 0
    while wk >= najstarsi:
        st = week_status(by_date, wk, today, settings, since=zaciatok)
        if st == WEEK_DONE:
            streak += 1
        elif st in (WEEK_EMPTY, WEEK_OPEN):
            pass                           # bežiaci ani prázdny týždeň streak neruší
        else:
            break
        wk -= timedelta(days=7)
    return streak


def streak_after(days: list[Day], d: date, today: date | None, settings: Settings) -> int:
    """Streak k danému dňu (pre stĺpec „Streak“ v tabuľke).

    Minulý deň sa hodnotí ako uzavretý (nesplnený = reset); dnešok ako otvorený.
    """
    if today is None or d < today:
        ref = d + timedelta(days=1)
    else:
        ref = today
    return compute_streak([x for x in days if x.date <= d], ref, settings)


def next_goal(prev: Day, settings: Settings) -> int:
    """Cieľ dňa NASLEDUJÚCEHO po `prev` – čisto z plánu, bez ohľadu na výsledok.

    Ponechaný názov, aby volajúci kód („čo ťa čaká zajtra“) zostal čitateľný. Deň sa
    berie z `prev`, nikdy zo systémových hodín – testy aj výpadky si vozia vlastný čas.
    """
    return settings.goal_for(prev.date + timedelta(days=1))


def fill_missing_days(days: list[Day], today: date, settings: Settings) -> list[Day]:
    """Doplní chýbajúce dni od posledného známeho po dnešok (výpadok bota).

    Chýbajúce minulé dni sa berú ako zamrazené (bot nebežal, nie je to chyba
    používateľa) – streak sa nestratí, používateľ si ich môže doplniť v tabuľke.
    """
    by_date = {d.date: d for d in days}
    past = [d for d in by_date if d <= today]
    if not past:
        # žiadny dnešný ani minulý deň (prázdna DB alebo len budúce riadky od používateľa)
        goal = settings.goal_for(today)
        return [Day(today, goal, frozen=settings.frozen and goal > 0)]
    last = max(past)
    new: list[Day] = []
    d = last + timedelta(days=1)
    while d <= today:
        goal = settings.goal_for(d)
        # zameškaný deň je zamrazený (bot nebežal), voľný deň zamrazovať netreba
        frozen = (settings.frozen or d < today) and goal > 0
        new.append(Day(d, goal, frozen=frozen))
        by_date[d] = new[-1]
        d += timedelta(days=1)
    return new


def replan(days: list[Day], settings: Settings, today: date,
           predosle: Settings | None = None) -> list[Day]:
    """Prepočíta ciele podľa plánu pre DNEŠOK a budúcnosť. História sa nikdy neprepisuje –
    uzavretý deň sa hodnotí podľa cieľa, ktorý vtedy platil.

    Používa sa, keď sa zmení X (z chatu alebo z tabuľky) – inak by dnešný riadok
    ostal visieť so starým cieľom až do polnoci.

    `predosle` sú nastavenia spred zmeny. Keď ich dostaneme, prepíšeme len tie dni,
    ktoré ešte sedeli na starý plán – ručne prepísaný cieľ v tabuľke ostáva platiť,
    presne ako sľubuje nápoveda v hárku.
    """
    zmenene = []
    for d in days:
        if d.date < today:
            continue
        goal = settings.goal_for(d.date)
        if goal == d.goal:
            continue
        if predosle is not None and predosle.goal_for(d.date) != d.goal:
            continue                        # ručne nastavený cieľ – nechaj ho tak
        zmenene.append(d.copy(goal=goal))
    return zmenene


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
