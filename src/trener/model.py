"""Dátový model – čisté dataclassy bez I/O.

Tréningový plán je týždenný a riadi ho jediné číslo **X**:

    pondelok, streda, piatok   ráno 2X + večer 2X   (spolu 4X)
    utorok, štvrtok, sobota    ráno 2X, večer nič   (spolu 2X)
    nedeľa                     voľno                (cieľ 0)

X rastie každý pondelok o `x_step` (štandardne 1), bez ohľadu na to, ako
týždeň dopadol. Cieľ dňa sa teda nepočíta z včerajška, ale z dátumu – čo
znamená, že plán platí aj pre dni, ktoré bot zmeškal, aj pre budúcnosť.

Deň má dve „vedrá“: ráno a večer. Splnený deň = ráno + večer >= cieľ, bez
ohľadu na rozdelenie. Voľný deň (cieľ 0) nie je ani splnený, ani nesplnený.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, time as dtime, timedelta

MORNING = "morning"
EVENING = "evening"
SESSIONS = (MORNING, EVENING)

# dni v týždni podľa date.weekday(): pondelok = 0
BOTH_DAYS = frozenset({0, 2, 4})          # pondelok, streda, piatok – ráno aj večer
MORNING_ONLY_DAYS = frozenset({1, 3, 5})  # utorok, štvrtok, sobota – len ráno
REST_DAYS = frozenset({6})                # nedeľa – voľno

WEEKDAY_SK = ("pondelok", "utorok", "streda", "štvrtok", "piatok", "sobota", "nedeľa")

# stavy dňa (stĺpec „Stav“ v tabuľke)
OPEN = "open"          # dnešok, ešte nesplnený
DONE = "done"          # splnený
FAILED = "failed"      # minulý deň, nesplnený
FROZEN = "frozen"      # zamrazený deň – nepočíta sa do streaku ani ho neprerušuje
REST = "rest"          # plánované voľno (cieľ 0) – nie je ani splnený, ani nesplnený

STATUS_LABEL = {
    OPEN: "⏳ otvorený",
    DONE: "✅ splnený",
    FAILED: "❌ nesplnený",
    FROZEN: "❄️ zamrazený",
    REST: "🌙 voľno",
}
# čo v stĺpci „Stav“ napísané používateľom znamená „zamrazený deň“
FROZEN_WORDS = {"zamrazeny", "zamrazený", "zamrazene", "zamrazené", "frozen", "❄", "❄️", "pauza", "freeze"}


def parse_hhmm(text: str) -> str | None:
    """'7:5' / '07:05' / '7.05' → '07:05'; inak None."""
    t = str(text).strip().replace(".", ":")
    if ":" not in t:
        return None
    h, _, m = t.partition(":")
    if not (h.isdigit() and m.isdigit()):
        return None
    hh, mm = int(h), int(m)
    if 0 <= hh <= 23 and 0 <= mm <= 59:
        return f"{hh:02d}:{mm:02d}"
    return None


def hhmm_to_time(hhmm: str) -> dtime:
    h, m = hhmm.split(":")
    return dtime(int(h), int(m))


def week_start(d: date) -> date:
    """Pondelok týždňa, do ktorého dátum patrí."""
    return d - timedelta(days=d.weekday())


def parse_iso_date(text: str, fallback: date) -> date:
    try:
        return date.fromisoformat(str(text).strip())
    except (TypeError, ValueError):
        return fallback


def split_goal(goal: int, d: date | None = None) -> tuple[int, int]:
    """Rozdelenie cieľa na (ráno, večer) podľa dňa v týždni.

    V pondelok/stredu/piatok pol na pol (ráno nahor), inak všetko ráno. Bez dátumu
    (staré volania, ručné výpočty) sa delí pol na pol ako predtým.
    """
    goal = max(int(goal), 0)
    if goal == 0:
        return 0, 0
    if d is not None and d.weekday() not in BOTH_DAYS:
        return goal, 0                 # utorok/štvrtok/sobota aj ručne nastavená nedeľa
    m = (goal + 1) // 2
    return m, goal - m


def x_from_goal(goal: int, d: date) -> int | None:
    """Aké X zodpovedá tomuto cieľu v tento deň. None, keď cieľ na plán nesedí.

    Používa sa na stĺpec „X (týždeň)“ v tabuľke: musí hovoriť o TOM riadku, nie
    o dnešných nastaveniach – inak by sa história po zmene X spätne preznačila.
    """
    if goal <= 0:
        return None
    delitel = 4 if d.weekday() in BOTH_DAYS else 2
    return goal // delitel if goal % delitel == 0 else None


def plan_targets(d: date, x: int) -> tuple[int, int]:
    """Plánované (ráno, večer) pre daný deň a dané X."""
    x = max(int(x), 0)
    wd = d.weekday()
    if wd in REST_DAYS:
        return 0, 0
    if wd in MORNING_ONLY_DAYS:
        return 2 * x, 0
    return 2 * x, 2 * x


@dataclass
class Settings:
    x: int = 8                          # týždenné X: ráno 2X, v po/st/pi aj večer 2X
    x_since: str = "2026-09-07"         # pondelok, pre ktorý X platí (ISO dátum)
    x_step: int = 1                     # o koľko rastie X každý pondelok
    morning_time: str = "07:00"         # začiatok rannej fázy (pripomienka + prvá výzva v chate)
    evening_time: str = "19:20"         # začiatok večernej fázy
    nag_max: int = 3                    # max správ v chate za sebou (na fázu)
    nag_interval_min: int = 30          # rozostup medzi správami v chate
    frozen: bool = False                # ❄️ tréner zamrazený
    morning_title: str = "💪 Ráno: {n} klikov"   # šablóna rannej pripomienky ({n} = počet)
    evening_title: str = "💪 Večer: {n} klikov"  # šablóna večernej pripomienky
    summary_on_fail: bool = True        # správa v chate pri nesplnenom dni (o polnoci)

    def x_for(self, d: date) -> int:
        """X platné pre daný dátum. Rastie po týždňoch, dopredu aj dozadu.

        Spodná hranica je 1, nie 0: pri pohľade dosť ďaleko do minulosti by X kleslo
        na nulu, celé staré týždne by sa tvárili ako voľno a týždenný streak by sa
        nikdy neprerušil.
        """
        base = week_start(parse_iso_date(self.x_since, date(2026, 9, 7)))
        weeks = (week_start(d) - base).days // 7
        return max(int(self.x) + weeks * int(self.x_step), 1)

    def targets_for(self, d: date) -> tuple[int, int]:
        return plan_targets(d, self.x_for(d))

    def goal_for(self, d: date) -> int:
        m, e = self.targets_for(d)
        return m + e

    def with_x(self, x: int, today: date) -> tuple["Settings", date]:
        """Nastaví X a vráti aj pondelok, od ktorého platí.

        V nedeľu (voľno, týždeň sa končí) to myslíš na ten nasledujúci – inak by si
        zadal 9 a v pondelok by ti naskočilo 10.
        """
        monday = week_start(today)
        if today.weekday() in REST_DAYS:
            monday += timedelta(days=7)
        return self.copy(x=max(int(x), 1), x_since=monday.isoformat()), monday

    def session_time(self, session: str) -> str:
        return self.morning_time if session == MORNING else self.evening_time

    def title_template(self, session: str) -> str:
        return self.morning_title if session == MORNING else self.evening_title

    def copy(self, **kw) -> "Settings":
        return replace(self, **kw)


@dataclass
class Day:
    date: date
    goal: int
    morning: int = 0
    evening: int = 0
    frozen: bool = False
    note: str = ""

    # ── odvodené hodnoty ────────────────────────────────────────────────────
    @property
    def total(self) -> int:
        return int(self.morning) + int(self.evening)

    @property
    def is_rest(self) -> bool:
        """Voľný deň: nič sa nečaká, nič sa nepripomína, streak sa nemení."""
        return self.goal <= 0

    @property
    def weekday_sk(self) -> str:
        return WEEKDAY_SK[self.date.weekday()]

    @property
    def morning_target(self) -> int:
        return split_goal(self.goal, self.date)[0]

    @property
    def evening_target(self) -> int:
        return split_goal(self.goal, self.date)[1]

    @property
    def left(self) -> int:
        return max(self.goal - self.total, 0)

    @property
    def morning_left(self) -> int:
        return max(self.morning_target - self.morning, 0)

    @property
    def evening_left(self) -> int:
        """Koľko ešte večer: zvyšok celého dňa (ráno 2/5 → večer 8).

        V deň bez večernej fázy (utorok/štvrtok/sobota) je to vždy 0 – čo nestihneš
        ráno, si dobehneš ráno; večer sa už nič nečaká.
        """
        if self.evening_target <= 0:
            return 0
        return self.left

    @property
    def done(self) -> bool:
        return self.goal > 0 and self.total >= self.goal

    def session_target(self, session: str) -> int:
        return self.morning_target if session == MORNING else self.evening_target

    def session_planned(self, session: str) -> bool:
        """Je táto fáza v tento deň vôbec v pláne? (Nie v nedeľu a nie večer v ut/št/so.)"""
        return self.session_target(session) > 0

    def session_done(self, session: str) -> bool:
        """Ranná fáza je hotová, keď je ranné vedro plné (alebo je splnený deň);
        večerná fáza má dokončiť deň → je hotová až so splneným dňom.

        Fáza, ktorá v daný deň vôbec nie je v pláne (cieľ 0), je hotová vždy –
        inak by sa na ňu čakalo a pýtalo.
        """
        if self.is_rest or self.done:
            return True
        if self.session_target(session) <= 0:
            return True
        if session == MORNING:
            return self.morning >= self.morning_target
        return False

    def session_count(self, session: str) -> int:
        return self.morning if session == MORNING else self.evening

    def status(self, today: date) -> str:
        if self.is_rest:
            return REST
        if self.done:
            return DONE
        if self.frozen:
            return FROZEN
        if self.date < today:
            return FAILED
        return OPEN

    def copy(self, **kw) -> "Day":
        return replace(self, **kw)


@dataclass
class Report:
    """Jedno hlásenie z chatu: +n do fázy (alebo nastav = n).

    total=True: „spolu 10“ bez určenia fázy → dnešný súčet má byť n (dopočíta sa do
    aktuálnej fázy). day_offset=-1: hlásenie sa týka včerajška („včera 5“).
    """
    session: str
    n: int
    absolute: bool = False
    total: bool = False
    day_offset: int = 0


@dataclass
class NagState:
    date: date
    session: str
    sent: int = 0
    last_at: datetime | None = None


@dataclass
class ReminderState:
    """Čo o pripomienke (VTODO) vieme my – na detekciu úprav používateľom."""
    date: date
    session: str
    href: str = ""
    uid: str = ""
    etag: str = ""
    last_summary: str = ""        # čo sme naposledy zapísali/videli
    last_due_hhmm: str = ""       # čas (HH:MM lokálne), ktorý sme naposledy zapísali/videli
    completed: bool = False       # naposledy videný stav
    user_deleted: bool = False    # používateľ ju zmazal – dnes ju nevytvárame znova
    missing_seen: int = 0
    last_n: int = 0               # číslo, s ktorým sme naposledy vyplnili šablónu názvu
    user_unticked: bool = False   # používateľ ju odškrtol späť – neodčiarkujeme znova

    def copy(self, **kw) -> "ReminderState":
        return replace(self, **kw)


@dataclass
class Snapshot:
    """Stav pre čisté rozhodovanie (engine) – všetko, čo tick potrebuje."""
    now: datetime
    settings: Settings
    today: Day
    nags: dict[str, NagState] = field(default_factory=dict)
    woke_up: bool = False         # wake signál z telefónu dnes prišiel
