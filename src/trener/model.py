"""Dátový model – čisté dataclassy bez I/O.

Deň má dve „vedrá“: ráno a večer. Cieľ dňa sa delí na rannú časť
(zaokrúhlenú nahor) a večernú časť (zvyšok): 10 → 5 + 5, 11 → 6 + 5.
Splnený deň = ráno + večer >= cieľ, bez ohľadu na rozdelenie.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, time as dtime

MORNING = "morning"
EVENING = "evening"
SESSIONS = (MORNING, EVENING)

# stavy dňa (stĺpec „Stav“ v tabuľke)
OPEN = "open"          # dnešok, ešte nesplnený
DONE = "done"          # splnený
FAILED = "failed"      # minulý deň, nesplnený
FROZEN = "frozen"      # zamrazený deň – nepočíta sa do streaku ani ho neprerušuje

STATUS_LABEL = {
    OPEN: "⏳ otvorený",
    DONE: "✅ splnený",
    FAILED: "❌ nesplnený",
    FROZEN: "❄️ zamrazený",
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


def split_goal(goal: int) -> tuple[int, int]:
    """Rozdelenie cieľa na (ráno, večer); ráno zaokrúhlené nahor."""
    goal = max(int(goal), 0)
    m = (goal + 1) // 2
    return m, goal - m


@dataclass
class Settings:
    increment: int = 2                  # o koľko rastie cieľ po splnenom dni
    morning_time: str = "07:00"         # začiatok rannej fázy (pripomienka + prvá výzva v chate)
    evening_time: str = "19:20"         # začiatok večernej fázy
    nag_max: int = 3                    # max správ v chate za sebou (na fázu)
    nag_interval_min: int = 30          # rozostup medzi správami v chate
    frozen: bool = False                # ❄️ tréner zamrazený
    morning_title: str = "💪 Ráno: {n} klikov"   # šablóna rannej pripomienky ({n} = počet)
    evening_title: str = "💪 Večer: {n} klikov"  # šablóna večernej pripomienky
    summary_on_fail: bool = True        # správa v chate pri nesplnenom dni (o polnoci)

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
    def morning_target(self) -> int:
        return split_goal(self.goal)[0]

    @property
    def evening_target(self) -> int:
        return split_goal(self.goal)[1]

    @property
    def left(self) -> int:
        return max(self.goal - self.total, 0)

    @property
    def morning_left(self) -> int:
        return max(self.morning_target - self.morning, 0)

    @property
    def evening_left(self) -> int:
        """Koľko ešte večer: zvyšok celého dňa (ráno 2/5 → večer 8)."""
        return self.left

    @property
    def done(self) -> bool:
        return self.goal > 0 and self.total >= self.goal

    def session_done(self, session: str) -> bool:
        """Ranná fáza je hotová, keď je ranné vedro plné (alebo je splnený deň);
        večerná fáza má dokončiť deň → je hotová až so splneným dňom."""
        if self.done:
            return True
        if session == MORNING:
            return self.morning >= self.morning_target
        return False

    def session_count(self, session: str) -> int:
        return self.morning if session == MORNING else self.evening

    def status(self, today: date) -> str:
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
