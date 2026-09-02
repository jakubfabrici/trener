"""Budík v **iCloud kalendári** – ranný a večerný event s upozornením v čase eventu.

Prečo takto: do iCloud *Pripomienok* server zapisovať nevie (Apple nemá API), ale
iCloud **Kalendár** je cez CalDAV plne prístupný s app-specific heslom. Kalendár
„Kliky“ si iCloud sám rozsynchronizuje na iPhone, iPad aj Mac – netreba nikde nič
pridávať. Event má VALARM presne v čase začiatku, takže telefón zazvoní.

Bot sa z tvojich úprav učí rovnako ako pri pripomienkach: keď event v kalendári
posunieš, je to nový čas fázy pre všetky ďalšie dni; keď ho premenuješ, je to nová
šablóna názvu ({n} = počet klikov).
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from icalendar import Alarm, Calendar, Event, vText

from trener.caldav_todo import CalDavError, Conflict, TodoItem, TodoList
from trener.model import (DONE, EVENING, FAILED, FROZEN, MORNING, SESSIONS, Day, ReminderState, Settings,
                          hhmm_to_time)
from trener.reminders import learn_template, render_title, session_number

log = logging.getLogger("trener.calendar")

PRODID = "-//trener-klikov//v2//SK"
TABLE = "events"
STATE_MARK = {DONE: "✅", FAILED: "❌", FROZEN: "❄️"}


class EventCalendar(TodoList):
    """Ten istý CalDAV klient, len nad kalendárom udalostí."""
    component = "VEVENT"


def _utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc)


def build_event_ics(uid: str, summary: str, start: datetime, minutes: int, alarm: bool = True,
                    description: str | None = None) -> bytes:
    now = datetime.now(timezone.utc)
    cal = Calendar()
    cal.add("VERSION", "2.0")
    cal.add("PRODID", PRODID)
    cal.add("CALSCALE", "GREGORIAN")
    ev = Event()
    ev.add("UID", uid)
    ev.add("DTSTAMP", now)
    ev.add("CREATED", now)
    ev.add("LAST-MODIFIED", now)
    ev.add("SUMMARY", summary)
    ev.add("DTSTART", _utc(start))
    ev.add("DTEND", _utc(start + timedelta(minutes=minutes)))
    ev.add("TRANSP", "TRANSPARENT")     # neblokuje „zaneprázdnený“
    ev.add("SEQUENCE", 0)
    if description:
        ev.add("DESCRIPTION", description)
    if alarm:
        ev.add_component(_alarm())
    cal.add_component(ev)
    return cal.to_ical()


def _alarm() -> Alarm:
    a = Alarm()
    a.add("ACTION", "DISPLAY")
    a.add("DESCRIPTION", "💪 Kliky!")
    a["TRIGGER"] = vText("PT0S")         # presne v čase začiatku eventu (Apple tento tvar píše tiež)
    a.add("UID", str(uuid.uuid4()).upper())
    a.add("X-WR-ALARMUID", str(uuid.uuid4()).upper())
    return a


def apply_event_changes(item: TodoItem, *, summary: str | None = None, start: datetime | None = None,
                        minutes: int = 15, alarm: bool | None = None) -> bytes:
    ev = item.vtodo          # pri component="VEVENT" vracia VEVENT
    now = datetime.now(timezone.utc)

    def _set(key, value):
        if key in ev:
            del ev[key]
        ev.add(key, value)

    if summary is not None:
        _set("SUMMARY", summary)
    if start is not None:
        _set("DTSTART", _utc(start))
        _set("DTEND", _utc(start + timedelta(minutes=minutes)))
    if alarm is False:
        ev.subcomponents = [c for c in ev.subcomponents if c.name != "VALARM"]
    elif alarm is True and not any(c.name == "VALARM" for c in ev.subcomponents):
        ev.add_component(_alarm())
    seq = ev.get("SEQUENCE")
    try:
        seq = int(seq) if seq is not None else 0
    except (TypeError, ValueError):
        seq = 0
    _set("SEQUENCE", seq + 1)
    _set("LAST-MODIFIED", now)
    _set("DTSTAMP", now)
    return item.cal.to_ical()


def event_start(item: TodoItem, tz: ZoneInfo) -> datetime | None:
    ev = item.vtodo
    d = ev.get("DTSTART")
    if d is None:
        return None
    dt = d.dt
    if isinstance(dt, datetime):
        return (dt if dt.tzinfo else dt.replace(tzinfo=tz)).astimezone(tz)
    return None


@dataclass
class SyncOutcome:
    settings: Settings
    settings_changed: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class CalendarSync:
    def __init__(self, cal: EventCalendar, store, tz: ZoneInfo, minutes: int = 15):
        self.cal = cal
        self.store = store
        self.tz = tz
        self.minutes = minutes

    def _start(self, d: date, hhmm: str) -> datetime:
        return datetime.combine(d, hhmm_to_time(hhmm), tzinfo=self.tz)

    def sync(self, day: Day, settings: Settings, allow_create: bool = True) -> SyncOutcome:
        out = SyncOutcome(settings=settings.copy())
        try:
            items = self.cal.list()
        except Exception as e:  # noqa: BLE001 – kalendár je best-effort, tick musí bežať ďalej
            out.errors.append(f"iCloud kalendár nedostupný: {e}")
            return out
        by_uid = {i.uid: i for i in items if i.uid}
        for session in SESSIONS:
            try:
                self._sync_session(day, session, out, by_uid, allow_create)
            except Conflict:
                out.notes.append(f"{_sk(session)}: event práve menil telefón – skúsim o chvíľu")
            except Exception as e:  # noqa: BLE001
                out.errors.append(f"{_sk(session)}: {e}")
        return out

    def _sync_session(self, day: Day, session: str, out: SyncOutcome, by_uid: dict[str, TodoItem],
                      allow_create: bool) -> None:
        settings = out.settings
        st = self.store.get_reminder(day.date, session, TABLE) or ReminderState(day.date, session)
        item = by_uid.get(st.uid) if st.uid else None

        # ── čo spravil používateľ v kalendári ────────────────────────────
        if st.uid and item is None:
            st.missing_seen += 1
            if st.missing_seen >= 2 and not st.user_deleted:
                st.user_deleted = True
                out.notes.append(f"{_sk(session)}: event si zmazal – dnes ho už nevytvorím")
            self.store.save_reminder(st, TABLE)
            return
        if item is not None:
            st.missing_seen = 0
            if item.summary and item.summary != st.last_summary:
                tmpl = learn_template(_strip_mark(item.summary), st.last_n)
                if tmpl != settings.title_template(session) and not day.session_done(session):
                    setattr(settings, f"{session}_title", tmpl)
                    out.settings_changed.append(f"{session}_title")
                    out.notes.append(f"{_sk(session)}: nový názov „{tmpl}“ (podľa tvojej úpravy v kalendári)")
                st.last_summary = item.summary
            start = event_start(item, self.tz)
            if start is not None:
                hhmm = start.strftime("%H:%M")
                if st.last_due_hhmm and hhmm != st.last_due_hhmm and hhmm != settings.session_time(session):
                    setattr(settings, f"{session}_time", hhmm)
                    out.settings_changed.append(f"{session}_time")
                    out.notes.append(f"{_sk(session)}: nový čas {hhmm} (podľa tvojej úpravy v kalendári)")
                st.last_due_hhmm = hhmm
            st.etag = item.etag

        # ── čo má platiť ─────────────────────────────────────────────────
        frozen = settings.frozen or day.frozen
        done = day.session_done(session)
        n = session_number(day, session)
        if done:
            # splnené: ukáž, koľko si naozaj dal (nie „0 klikov“, čo by vyšlo zo zvyšku)
            did = day.morning if session == MORNING else day.evening
            base_title = render_title(settings.title_template(session), did)
        else:
            base_title = render_title(settings.title_template(session), n)
        title = f"✅ {base_title}" if done else base_title
        start = self._start(day.date, settings.session_time(session))

        if item is None:
            if frozen or st.user_deleted or done or not allow_create:
                self.store.save_reminder(st, TABLE)
                return
            uid = f"kliky-{day.date.isoformat()}-{session}-{uuid.uuid4().hex[:6]}"
            ics = build_event_ics(uid, title, start, self.minutes, alarm=True,
                                  description="Vytvoril virtuálny tréner klikov. Čas alebo názov si "
                                              "pokojne uprav – prispôsobím sa mu aj ďalšie dni.")
            created = self.cal.create(uid, ics)
            st = ReminderState(day.date, session, created.href, uid, created.etag, title,
                               start.strftime("%H:%M"), done, False, 0, n, False)
            self.store.save_reminder(st, TABLE)
            out.notes.append(f"{_sk(session)}: budík v kalendári o {st.last_due_hhmm} („{title}“)")
            return

        changes: dict = {}
        has_alarm = any(c.name == "VALARM" for c in item.vtodo.subcomponents)
        if frozen:
            changes["alarm"] = False              # zamrazené: nech nezvoní
            if not item.summary.startswith("❄️"):
                changes["summary"] = f"❄️ {_strip_mark(item.summary)}"
        else:
            if title != item.summary:
                changes["summary"] = title
            cur = event_start(item, self.tz)
            if cur is None or cur.strftime("%H:%M") != start.strftime("%H:%M") or cur.date() != day.date:
                changes["start"] = start
            if done and has_alarm:
                changes["alarm"] = False          # splnené – už netreba zvoniť
            elif not done and not has_alarm:
                changes["alarm"] = True           # odmrazené / znovu otvorené – nech zase zvoní
        if changes:
            ics = apply_event_changes(item, minutes=self.minutes, **changes)
            new = self.cal.put(item, ics)
            st.etag = new.etag
            if "summary" in changes:
                st.last_summary = changes["summary"]
                st.last_n = n
            if "start" in changes:
                st.last_due_hhmm = start.strftime("%H:%M")
            st.completed = done
            out.notes.append(f"{_sk(session)}: event upravený ({', '.join(changes)})")
        self.store.save_reminder(st, TABLE)

    # ── uzavretie starého dňa (streak mriežka v kalendári) ──────────────────
    def finalize(self, day: Day, today: date) -> list[str]:
        notes = []
        status = day.status(today)
        mark = STATE_MARK.get(status, "")
        try:
            items = {i.uid: i for i in self.cal.list() if i.uid}
        except Exception as e:  # noqa: BLE001
            return [f"iCloud kalendár nedostupný: {e}"]
        for session in SESSIONS:
            st = self.store.get_reminder(day.date, session, TABLE)
            if st is None or not st.uid:
                self.store.delete_reminder(day.date, session, TABLE)
                continue
            item = items.get(st.uid)
            if item is None:
                self.store.delete_reminder(day.date, session, TABLE)
                continue
            want = f"{mark} {_strip_mark(item.summary)}".strip()
            try:
                self.cal.put(item, apply_event_changes(item, summary=want, alarm=False,
                                                       minutes=self.minutes))
                notes.append(f"{day.date}: {_sk(session)} uzavretý ({want})")
            except Exception as e:  # noqa: BLE001
                notes.append(f"{day.date} {_sk(session)}: {e}")
            self.store.delete_reminder(day.date, session, TABLE)
        return notes


def _strip_mark(summary: str) -> str:
    """Odstráni náš stavový emoji z názvu (✅ / ❌ / ❄️), zvyšok necháva tak."""
    out = summary.strip()
    for mark in ("✅", "❌", "❄️", "❄"):
        if out.startswith(mark):
            out = out[len(mark):].lstrip()
    return out


def _sk(session: str) -> str:
    return "ráno" if session == MORNING else "večer"
