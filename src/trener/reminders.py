"""Pripomienky v appke Pripomienky (Apple) cez CalDAV – princíp Duolingo.

Bot každý deň vytvorí dve pripomienky (ráno, večer) s časom. Používateľ si ich
môže v appke ľubovoľne upraviť – a bot sa tomu prispôsobí:
  • zmena času  → nový ranný/večerný čas pre všetky ďalšie dni (aj výzvy v chate),
  • zmena textu → nová šablóna názvu ({n} = číslo klikov) pre ďalšie dni,
  • odškrtnutie → fáza sa berie ako splnená (zapíše sa do tabuľky),
  • zmazanie    → dnes sa už nevytvorí, zajtra normálne.
Bot pripomienku odčiarkne, keď je fáza splnená (z chatu, z tabuľky, odkiaľkoľvek).
Pri zamrazení sa nové pripomienky nevytvárajú a existujúce sa nechajú tak.

Všetko tu je čistá logika nad `TodoItem`/`ReminderState`; sieť rieši TodoList.
"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime
from zoneinfo import ZoneInfo

from trener.caldav_todo import CalDavError, Conflict, TodoItem, TodoList, apply_changes, build_todo_ics
from trener.model import EVENING, MORNING, SESSIONS, Day, ReminderState, Report, Settings, hhmm_to_time

log = logging.getLogger("trener.reminders")


def render_title(template: str, n: int) -> str:
    return template.replace("{n}", str(n))


def learn_template(new_summary: str, last_n: int) -> str:
    """Z názvu upraveného používateľom odvodí šablónu: číslo → {n}."""
    s = new_summary.strip()
    if last_n > 0 and str(last_n) in s:
        return s.replace(str(last_n), "{n}", 1)
    nums = re.findall(r"\d+", s)
    if len(nums) == 1:
        return s.replace(nums[0], "{n}", 1)
    return s     # bez čísla → pevný text


def session_number(day: Day, session: str) -> int:
    """Číslo v pripomienke: ráno = koľko ešte ráno, večer = zvyšok dňa (pred prvým klikom
    len večerná polovica, aby 10 nevyzeralo ako 'večer 10')."""
    if session == MORNING:
        return day.morning_left
    if day.total == 0:
        return day.evening_target
    return day.left


def local_hhmm(dt: datetime, tz: ZoneInfo) -> str:
    return dt.astimezone(tz).strftime("%H:%M")


def due_for(day: date, hhmm: str, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, hhmm_to_time(hhmm), tzinfo=tz)


@dataclass
class SyncOutcome:
    settings: Settings
    settings_changed: list[str] = field(default_factory=list)
    reports: list[Report] = field(default_factory=list)   # odškrtnutia používateľom → hlásenia
    notes: list[str] = field(default_factory=list)        # ľudsky čitateľné zmeny (log/oznámenie)
    errors: list[str] = field(default_factory=list)


class ReminderSync:
    def __init__(self, todos: TodoList, store, tz: ZoneInfo):
        self.todos = todos
        self.store = store
        self.tz = tz

    # ── hlavný sync ─────────────────────────────────────────────────────────
    def sync(self, today: Day, settings: Settings) -> SyncOutcome:
        out = SyncOutcome(settings=settings.copy())
        try:
            items = self.todos.list()
        except (CalDavError, OSError) as e:
            out.errors.append(f"CalDAV nedostupný: {e}")
            return out
        by_uid = {i.uid: i for i in items if i.uid}
        by_href = {i.href: i for i in items}

        # 1) staré (včerajšie a staršie) nesplnené pripomienky preč, splnené nechaj ako históriu
        for st in self.store.reminders_before(today.date):
            item = by_uid.get(st.uid) or by_href.get(st.href)
            if item is not None and not item.completed and not settings.frozen:
                try:
                    self.todos.delete(item)
                    out.notes.append(f"zmazaná stará pripomienka {st.date} {st.session}")
                except (CalDavError, OSError) as e:
                    out.errors.append(f"mazanie {st.uid}: {e}")
                    continue
            self.store.delete_reminder(st.date, st.session)

        # 2) dnešné pripomienky
        for session in SESSIONS:
            try:
                self._sync_session(today, session, out, by_uid, by_href)
            except Conflict:
                out.notes.append(f"{session}: pripomienku práve menil telefón – skúsim o chvíľu")
            except (CalDavError, OSError) as e:
                out.errors.append(f"{session}: {e}")
        return out

    def _sync_session(self, today: Day, session: str, out: SyncOutcome,
                      by_uid: dict[str, TodoItem], by_href: dict[str, TodoItem]) -> None:
        settings = out.settings
        st = self.store.get_reminder(today.date, session) or ReminderState(today.date, session)
        item = None
        if st.uid:
            item = by_uid.get(st.uid) or by_href.get(st.href)

        # ── čo spravil používateľ od posledného syncu ─────────────────────
        if st.uid and item is None:
            # existovala a zmizla → používateľ ju zmazal; dnes ju nevnucujeme
            st.missing_seen += 1
            if st.missing_seen >= 2 and not st.user_deleted:
                st.user_deleted = True
                out.notes.append(f"{_sk(session)}: pripomienku si zmazal – dnes ju už nevytvorím")
            self.store.save_reminder(st)
            return
        if item is not None:
            st.missing_seen = 0
            # a) názov
            if item.summary and item.summary != st.last_summary:
                tmpl = learn_template(item.summary, st.last_n)
                if tmpl != settings.title_template(session):
                    setattr(settings, f"{session}_title", tmpl)
                    out.settings_changed.append(f"{session}_title")
                    out.notes.append(f"{_sk(session)}: nový názov pripomienky „{tmpl}“ (podľa tvojej úpravy)")
                st.last_summary = item.summary
            # b) čas
            if item.due is not None:
                hhmm = local_hhmm(item.due, self.tz)
                if st.last_due_hhmm and hhmm != st.last_due_hhmm and hhmm != settings.session_time(session):
                    setattr(settings, f"{session}_time", hhmm)
                    out.settings_changed.append(f"{session}_time")
                    out.notes.append(f"{_sk(session)}: nový čas {hhmm} (podľa tvojej úpravy v Pripomienkach)")
                st.last_due_hhmm = hhmm
            # c) odškrtnutie / odškrtnutie späť
            if item.completed and not st.completed:
                st.completed = True
                st.user_unticked = False
                if not today.session_done(session):
                    if session == MORNING:
                        n = max(today.morning, today.morning_target)
                    else:
                        n = max(today.evening, today.goal - today.morning)
                    out.reports.append(Report(session, n, absolute=True))
                    out.notes.append(f"{_sk(session)}: odškrtnuté v Pripomienkach → beriem ako splnené")
            elif not item.completed and st.completed:
                st.completed = False
                st.user_unticked = True
                out.notes.append(f"{_sk(session)}: odškrtnutie zrušené v Pripomienkach – nechávam tak")
            st.etag = item.etag

        # ── čo má platiť ──────────────────────────────────────────────────
        frozen = settings.frozen or today.frozen
        # po prípadnom prevzatí odškrtnutia je fáza splnená až po aplikovaní reportu – rátame s tým
        session_done = today.session_done(session) or any(
            r.session == session for r in out.reports)
        n = session_number(today, session)
        desired_summary = render_title(settings.title_template(session), n)
        desired_due = due_for(today.date, settings.session_time(session), self.tz)

        if item is None:
            if frozen or st.user_deleted or session_done:
                self.store.save_reminder(st)
                return
            uid = f"kliky-{today.date.isoformat()}-{session}-{uuid.uuid4().hex[:6]}"
            created = self.todos.create(uid, build_todo_ics(uid, desired_summary, desired_due))
            st = ReminderState(today.date, session, created.href, uid, created.etag, desired_summary,
                               local_hhmm(desired_due, self.tz), False, False, 0, n, False)
            self.store.save_reminder(st)
            out.notes.append(f"{_sk(session)}: vytvorená pripomienka „{desired_summary}“ o {st.last_due_hhmm}")
            return

        changes: dict = {}
        if not item.completed:
            if frozen:
                pass  # zamrazené: nechaj tak
            else:
                if desired_summary != item.summary:
                    changes["summary"] = desired_summary
                cur_hhmm = local_hhmm(item.due, self.tz) if item.due else ""
                if cur_hhmm != local_hhmm(desired_due, self.tz) or (
                        item.due and item.due.astimezone(self.tz).date() != today.date):
                    changes["due"] = desired_due
                if session_done and not st.user_unticked:
                    changes["complete"] = True
        if changes:
            ics = apply_changes(item, **changes)
            new = self.todos.put(item, ics)
            st.etag = new.etag
            if "summary" in changes:
                st.last_summary = desired_summary
                st.last_n = n
            if "due" in changes:
                st.last_due_hhmm = local_hhmm(desired_due, self.tz)
            if changes.get("complete"):
                st.completed = True
                out.notes.append(f"{_sk(session)}: pripomienka odčiarknutá ✅")
            else:
                out.notes.append(f"{_sk(session)}: pripomienka upravená ({', '.join(changes)})")
        self.store.save_reminder(st)


def _sk(session: str) -> str:
    return "ráno" if session == MORNING else "večer"
