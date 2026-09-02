"""Most do **iCloud Pripomienok** cez iOS Skratku (ako to robí Duolingo).

Server do iCloud Pripomienok zapisovať nevie – Apple na to nemá API. Zapisovať však
vie sám telefón: Skratka si od bota vypýta plán (`/plan`) a v iCloud zozname „Kliky“
pripomienky vytvorí, takže sa cez iCloud zosynchronizujú na všetky zariadenia bez
pridávania akéhokoľvek účtu. Keď pripomienku odškrtneš, Skratka to ohlási späť
(`/hotovo`), a keď jej zmeníš názov alebo čas, ohlási aj to (`/uprav`) – bot sa
prispôsobí rovnako ako pri CalDAV verzii.

Tu je len čistá logika (žiadna sieť, žiadny asyncio) – volá ju app.py.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

from trener.engine import apply_reports
from trener.model import EVENING, MORNING, SESSIONS, Day, ReminderState, Report, Settings, hhmm_to_time
from trener.reminders import learn_template, render_title, session_number

log = logging.getLogger("trener.shortcuts")

NOTE_PREFIX = "kliky"


def make_note(d: date, session: str) -> str:
    """Text do poznámky pripomienky – podľa neho bot vie, čoho sa hlásenie týka."""
    return f"{NOTE_PREFIX}:{d.isoformat()}:{session}"


def parse_note(note: str) -> tuple[date, str] | None:
    for line in (note or "").splitlines():
        parts = line.strip().split(":")
        if len(parts) >= 3 and parts[0] == NOTE_PREFIX and parts[2] in SESSIONS:
            try:
                return date.fromisoformat(parts[1]), parts[2]
            except ValueError:
                return None
    return None


def due_at(d: date, hhmm: str, tz: ZoneInfo) -> datetime:
    return datetime.combine(d, hhmm_to_time(hhmm), tzinfo=tz)


def app_status(day: Day, settings: Settings, streak: int, next_goal_value: int, tz) -> dict:
    """Kompletný stav pre mobilnú appku (jedno volanie = všetko, čo potrebuje)."""
    return {
        "datum": day.date.isoformat(),
        "ciel": day.goal,
        "rano": day.morning, "vecer": day.evening, "spolu": day.total, "zostava": day.left,
        "rano_ciel": day.morning_target, "vecer_ciel": day.evening_target,
        "rano_hotovo": day.session_done(MORNING), "vecer_hotovo": day.session_done(EVENING),
        "splneny": day.done,
        "zamrazene": bool(settings.frozen or day.frozen),
        "streak": streak,
        "zajtra_ciel": next_goal_value,
        "rano_cas": settings.morning_time,
        "vecer_cas": settings.evening_time,
        "rano_nazov": render_title(settings.title_template(MORNING), session_number(day, MORNING)),
        "vecer_nazov": render_title(settings.title_template(EVENING), session_number(day, EVENING)),
        "rano_due": due_at(day.date, settings.morning_time, tz).isoformat(timespec="seconds"),
        "vecer_due": due_at(day.date, settings.evening_time, tz).isoformat(timespec="seconds"),
        "poznamka_rano": make_note(day.date, MORNING),
        "poznamka_vecer": make_note(day.date, EVENING),
    }


@dataclass
class Outcome:
    settings: Settings
    reports: list[Report] = field(default_factory=list)
    settings_changed: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class ShortcutBridge:
    def __init__(self, store, tz: ZoneInfo, list_name: str = "Kliky"):
        self.store = store
        self.tz = tz
        self.list_name = list_name

    # ── plán pre telefón ────────────────────────────────────────────────────
    def plan(self, day: Day, settings: Settings) -> dict:
        """Čo má byť dnes v zozname. Zapíše si, čo vydalo (kvôli učeniu sa úprav)."""
        items = []
        for session in SESSIONS:
            st = self.store.get_reminder(day.date, session) or ReminderState(day.date, session)
            done = day.session_done(session)
            n = session_number(day, session)
            title = render_title(settings.title_template(session), n)
            hhmm = settings.session_time(session)
            frozen = settings.frozen or day.frozen
            if st.last_summary != title or st.last_due_hhmm != hhmm or st.completed != done or st.last_n != n:
                st = st.copy(last_summary=title, last_due_hhmm=hhmm, completed=done, last_n=n,
                             uid=make_note(day.date, session))
                self.store.save_reminder(st)
            if frozen or done or st.user_deleted:
                continue          # zamrazené / hotové / zmazané používateľom sa nevytvárajú
            items.append({
                "nazov": title,
                "poznamka": make_note(day.date, session),
                "cas": due_at(day.date, hhmm, self.tz).isoformat(timespec="seconds"),
                "cas_kratky": hhmm,
                "faza": session,
                "kliky": n,
            })
        return {
            "datum": day.date.isoformat(),
            "zoznam": self.list_name,
            "zamrazene": bool(settings.frozen or day.frozen),
            "ciel": day.goal,
            "rano": day.morning,
            "vecer": day.evening,
            "spolu": day.total,
            "zostava": day.left,
            "pripomienky": items,
            "pocet": len(items),
        }

    # ── hlásenia z telefónu ─────────────────────────────────────────────────
    def apply(self, day: Day, settings: Settings, payloads: list[dict]) -> Outcome:
        out = Outcome(settings=settings.copy())
        # `day` sa priebežne prepočítava – dve odškrtnutia v jednej dávke sa nesmú
        # počítať zo zastaraného stavu (inak by večer vyšlo „celý cieľ“ ešte raz)
        for p in payloads:
            kind = p.get("kind")
            parsed = parse_note(p.get("poznamka") or p.get("note") or "")
            if parsed is None:
                out.notes.append(f"neznáma pripomienka ({p!r}) – ignorujem")
                continue
            d, session = parsed
            if d != day.date:
                out.notes.append(f"pripomienka z {d} sa netýka dneška – ignorujem")
                continue
            st = self.store.get_reminder(d, session) or ReminderState(d, session)
            if kind == "reps":
                try:
                    n = int(p.get("n") or 0)
                except (TypeError, ValueError):
                    n = 0
                if n == 0:
                    out.notes.append("hlásenie bez počtu – ignorujem")
                    continue
                rep = Report(session, n, absolute=bool(p.get("absolute")))
                out.reports.append(rep)
                day = apply_reports(day, [rep]).day
                out.notes.append(f"{_sk(session)}: {'nastavené na' if p.get('absolute') else '+'}{n} z appky")
            elif kind == "done":
                if st.completed or day.session_done(session):
                    continue                      # už je zarátané, žiadne dvojité pripísanie
                n = (max(day.morning, day.morning_target) if session == MORNING
                     else max(day.evening, day.goal - day.morning))
                rep = Report(session, n, absolute=True)
                out.reports.append(rep)
                day = apply_reports(day, [rep]).day
                self.store.save_reminder(st.copy(completed=True))
                out.notes.append(f"{_sk(session)}: odškrtnuté v Pripomienkach → beriem ako splnené")
            elif kind == "deleted":
                self.store.save_reminder(st.copy(user_deleted=True))
                out.notes.append(f"{_sk(session)}: pripomienku si zmazal – dnes ju už neposielam")
            elif kind == "edit":
                title = (p.get("nazov") or p.get("title") or "").strip()
                hhmm = (p.get("cas") or p.get("time") or "").strip()[:5]
                changed = st
                if title and title != st.last_summary:
                    tmpl = learn_template(title, st.last_n)
                    if tmpl != out.settings.title_template(session):
                        setattr(out.settings, f"{session}_title", tmpl)
                        out.settings_changed.append(f"{session}_title")
                        out.notes.append(f"{_sk(session)}: nový názov „{tmpl}“ (podľa tvojej úpravy)")
                    changed = changed.copy(last_summary=title)
                if hhmm and _valid_hhmm(hhmm) and hhmm != st.last_due_hhmm:
                    if hhmm != out.settings.session_time(session):
                        setattr(out.settings, f"{session}_time", hhmm)
                        out.settings_changed.append(f"{session}_time")
                        out.notes.append(f"{_sk(session)}: nový čas {hhmm} (podľa tvojej úpravy)")
                    changed = changed.copy(last_due_hhmm=hhmm)
                if changed != st:
                    self.store.save_reminder(changed)
            else:
                out.notes.append(f"neznámy typ hlásenia {kind!r}")
        return out


def _valid_hhmm(s: str) -> bool:
    h, _, m = s.partition(":")
    return h.isdigit() and m.isdigit() and 0 <= int(h) <= 23 and 0 <= int(m) <= 59


def _sk(session: str) -> str:
    return "ráno" if session == MORNING else "večer"
