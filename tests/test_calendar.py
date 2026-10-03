"""Budík v iCloud kalendári: dva eventy denne s alarmom, učenie sa úprav, uzavretie dňa."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from icalendar import Calendar

from trener.calendar_sync import CalendarSync, apply_event_changes, build_event_ics, event_start
from trener.model import EVENING, MORNING, Day, ReminderState, Settings, hhmm_to_time
from trener.store import Store
from test_app import FakeCalendar

TZ = ZoneInfo("Europe/Bratislava")
# Pozor: D je STREDA – deň s rannou aj večernou fázou. Utorok/štvrtok/sobota majú
# len ráno a nedeľa je voľno; tie vetvy treba testovať na vlastných dátumoch.
D = date(2026, 9, 2)            # streda – ráno 2X aj večer 2X
D_UT = date(2026, 9, 1)         # utorok – len ráno 2X
D_NE = date(2026, 9, 6)         # nedeľa – voľno, cieľ 0
D_PO_NEXT = date(2026, 9, 7)    # pondelok ďalšieho týždňa – X je už o krok vyššie
X5 = Settings(x=5, x_since="2026-08-31")     # X = 5 pre týždeň 31.8. – 6.9.2026, teda 2X = 10


def make():
    store = Store(":memory:")
    cal = FakeCalendar()
    return store, cal, CalendarSync(cal, store, TZ)


def by(cal, word):
    hits = [i for i in cal.list() if word in i.summary]
    return hits[0] if hits else None


def has_alarm(item) -> bool:
    return any(c.name == "VALARM" for c in item.vtodo.subcomponents)


def plan_day(d: date, settings: Settings = X5, **kw) -> Day:
    """Deň s cieľom presne podľa týždenného plánu (nie ručne vymysleným číslom)."""
    return Day(d, settings.goal_for(d), **kw)


def leftover(store, cal, d: date, session: str, summary: str, hhmm: str):
    """Udalosť, ktorá na dni zostala po starom pláne – aj so záznamom v DB."""
    uid = f"kliky-{d.isoformat()}-{session}-stary"
    start = datetime.combine(d, hhmm_to_time(hhmm), tzinfo=TZ)
    created = cal.create(uid, build_event_ics(uid, summary, start, 15))
    store.save_reminder(ReminderState(d, session, created.href, uid, created.etag, summary, hhmm),
                        "events")
    return created


def test_creates_morning_and_evening_alarm_events():
    store, cal, cs = make()
    out = cs.sync(Day(D, 10), Settings(morning_time="07:00", evening_time="19:20"))
    assert not out.errors and len(cal.list()) == 2
    m, e = by(cal, "Ráno"), by(cal, "Večer")
    assert m.summary == "💪 Ráno: 5 klikov" and e.summary == "💪 Večer: 5 klikov"
    assert event_start(m, TZ).strftime("%H:%M") == "07:00"
    assert event_start(e, TZ).strftime("%H:%M") == "19:20"
    assert has_alarm(m) and has_alarm(e)
    # prvý alarm presne v čase začiatku, ďalšie opakovane; event je „voľný“
    ics = build_event_ics("u", "x", datetime(2026, 9, 2, 7, tzinfo=TZ), 25)
    assert b"TRIGGER:PT0S" in ics and b"TRANSP:TRANSPARENT" in ics
    for off in (3, 7, 12, 20):
        assert f"TRIGGER:PT{off}M".encode() in ics
    assert ics.count(b"BEGIN:VALARM") == 5
    # opakovaný sync nič nemení
    cs.sync(Day(D, 10), Settings())
    assert len(cal.list()) == 2


def test_updates_number_and_marks_done_without_alarm():
    store, cal, cs = make()
    cs.sync(Day(D, 10), Settings())
    cs.sync(Day(D, 10, morning=2), Settings())
    assert by(cal, "Ráno").summary == "💪 Ráno: 3 klikov"
    assert by(cal, "Večer").summary == "💪 Večer: 8 klikov"
    cs.sync(Day(D, 10, morning=5), Settings())
    m = by(cal, "Ráno")
    assert m.summary == "✅ 💪 Ráno: 5 klikov" and not has_alarm(m)   # ukáže, koľko si dal
    assert has_alarm(by(cal, "Večer"))       # večer ešte zvoní
    cs.sync(Day(D, 10, morning=5, evening=5), Settings())
    assert all(i.summary.startswith("✅") and not has_alarm(i) for i in cal.list())


def test_learns_time_moved_in_calendar():
    store, cal, cs = make()
    cs.sync(Day(D, 10), Settings(morning_time="07:00"))
    m = by(cal, "Ráno")
    cal.put(m, apply_event_changes(m, start=datetime(2026, 9, 2, 6, 15, tzinfo=TZ)))
    out = cs.sync(Day(D, 10), Settings(morning_time="07:00"))
    assert out.settings.morning_time == "06:15" and "morning_time" in out.settings_changed
    # bot čas nevracia späť
    cs.sync(Day(D, 10), out.settings)
    assert event_start(by(cal, "Ráno"), TZ).strftime("%H:%M") == "06:15"


def test_learns_renamed_event():
    store, cal, cs = make()
    cs.sync(Day(D, 10), Settings())
    e = by(cal, "Večer")
    cal.put(e, apply_event_changes(e, summary="Kliky večer 5 ks"))
    out = cs.sync(Day(D, 10), Settings())
    assert out.settings.evening_title == "Kliky večer {n} ks"
    out2 = cs.sync(Day(D, 10, morning=2), out.settings)
    assert by(cal, "Kliky večer").summary == "Kliky večer 8 ks"


def test_frozen_silences_alarms_and_creates_nothing_new():
    store, cal, cs = make()
    cs.sync(Day(D, 10), Settings(frozen=True))
    assert cal.list() == []
    cs.sync(Day(D, 10), Settings())
    assert len(cal.list()) == 2
    cs.sync(Day(D, 10), Settings(frozen=True))
    assert all(i.summary.startswith("❄️") and not has_alarm(i) for i in cal.list())
    cs.sync(Day(D, 10), Settings())
    assert all(has_alarm(i) and not i.summary.startswith("❄️") for i in cal.list())


def test_deleted_event_not_recreated_today():
    store, cal, cs = make()
    cs.sync(Day(D, 10), Settings())
    cal.delete(by(cal, "Ráno"))
    cs.sync(Day(D, 10), Settings())
    cs.sync(Day(D, 10), Settings())
    assert by(cal, "Ráno") is None
    cs.sync(Day(date(2026, 9, 3), 12), Settings())      # zajtra normálne
    assert by(cal, "Ráno") is not None


def test_finalize_marks_day_and_silences():
    store, cal, cs = make()
    cs.sync(Day(D, 10), Settings())
    notes = cs.finalize(Day(D, 10, morning=5), date(2026, 9, 3))    # nesplnený deň
    assert notes and all(i.summary.startswith("❌") for i in cal.list())
    assert not any(has_alarm(i) for i in cal.list())
    assert store.get_reminder(D, MORNING, "events") is None


def test_backend_down_is_not_fatal():
    store, cal, cs = make()

    def boom():
        raise RuntimeError("iCloud down")
    cal.list = boom
    out = cs.sync(Day(D, 10), Settings())
    assert out.errors and not out.notes


def test_apple_style_event_round_trip_keeps_unknown_props():
    ics = (b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Apple Inc.//iPhone OS 18.7//EN\r\n"
           b"BEGIN:VEVENT\r\nUID:kliky-2026-09-02-morning-abc123\r\nDTSTAMP:20260902T050000Z\r\n"
           b"DTSTART;TZID=Europe/Bratislava:20260902T063000\r\nDTEND;TZID=Europe/Bratislava:20260902T064500\r\n"
           b"SUMMARY:\xf0\x9f\x92\xaa R\xc3\xa1no: 5 klikov\r\nSEQUENCE:2\r\nX-APPLE-CREATOR-IDENTITY:x\r\n"
           b"BEGIN:VALARM\r\nACTION:DISPLAY\r\nDESCRIPTION:Reminder\r\nTRIGGER:PT0S\r\nEND:VALARM\r\n"
           b"END:VEVENT\r\nEND:VCALENDAR\r\n")
    from trener.caldav_todo import parse_item
    item = parse_item("h", "e", ics, "VEVENT")
    assert item.summary == "💪 Ráno: 5 klikov"
    assert event_start(item, TZ).strftime("%H:%M") == "06:30"
    out = apply_event_changes(item, summary="✅ 💪 Ráno: 5 klikov", alarm=False)
    assert b"X-APPLE-CREATOR-IDENTITY:x" in out and b"SEQUENCE:3" in out and b"VALARM" not in out


def test_reported_reps_silence_the_remaining_alarms():
    """Kľúčová vlastnosť: keď kliky nahlásiš, zvyšné zvonenia z eventu zmiznú."""
    store, cal, cs = make()
    cs.sync(Day(D, 10), Settings())
    m = by(cal, "Ráno")
    assert sum(1 for c in m.vtodo.subcomponents if c.name == "VALARM") == 5
    cs.sync(Day(D, 10, morning=5), Settings())          # ranná fáza hotová
    m = by(cal, "Ráno")
    assert not has_alarm(m) and m.summary.startswith("✅")
    assert sum(1 for c in by(cal, "Večer").vtodo.subcomponents if c.name == "VALARM") == 5


def test_alarm_offsets_are_configurable():
    store, cal, cs = make()
    cs.offsets = (0, 5)
    cs.sync(Day(D, 10), Settings())
    assert sum(1 for c in by(cal, "Ráno").vtodo.subcomponents if c.name == "VALARM") == 2


# ── nový týždenný plán: voľné dni a fázy, ktoré v ten deň nie sú ────────────

def test_v_nedelu_ziadny_event():
    store, cal, cs = make()
    day = plan_day(D_NE)
    assert day.is_rest and day.goal == 0
    out = cs.sync(day, X5)
    assert cal.list() == [] and not out.errors
    cs.sync(day, X5)                                   # ani po druhom syncu
    assert cal.list() == []
    assert store.get_reminder(D_NE, MORNING, "events") is None
    assert store.get_reminder(D_NE, EVENING, "events") is None


def test_v_utorok_len_ranny_event():
    store, cal, cs = make()
    day = plan_day(D_UT)
    assert (day.goal, day.morning_target, day.evening_target) == (10, 10, 0)   # 2X ráno
    out = cs.sync(day, X5)
    assert [i.summary for i in cal.list()] == ["💪 Ráno: 10 klikov"]
    assert has_alarm(by(cal, "Ráno")) and by(cal, "Večer") is None and not out.errors
    assert store.get_reminder(D_UT, EVENING, "events") is None
    cs.sync(day, X5)                                   # večerný nepribudne ani neskôr
    assert by(cal, "Večer") is None


def test_stary_vecerny_event_v_utorok_sa_zmaze_nie_oznaci():
    store, cal, cs = make()
    leftover(store, cal, D_UT, EVENING, "💪 Večer: 6 klikov", "19:20")
    out = cs.sync(plan_day(D_UT), X5)
    assert by(cal, "Večer") is None                                      # naozaj zmazaný
    assert [i.summary for i in cal.list()] == ["💪 Ráno: 10 klikov"]     # nezostalo „✅ Večer“
    assert store.get_reminder(D_UT, EVENING, "events") is None
    assert any("zmazaný" in n and "utorok" in n for n in out.notes)


def test_nedelny_zvysok_po_starom_plane_sa_zmaze():
    store, cal, cs = make()
    leftover(store, cal, D_NE, MORNING, "💪 Ráno: 6 klikov", "07:00")
    leftover(store, cal, D_NE, EVENING, "💪 Večer: 6 klikov", "19:20")
    out = cs.sync(plan_day(D_NE), X5)
    assert cal.list() == [] and not out.errors
    assert store.get_reminder(D_NE, MORNING, "events") is None
    assert store.get_reminder(D_NE, EVENING, "events") is None


def test_nedelny_zvysok_sa_zmaze_aj_bez_zaznamu_v_db():
    """Po strate DB udalosť poznáme len podľa UID – aj tak ju treba upratať,
    inak v nedeľu naveky zvoní budík na kliky, ktoré plán nechce."""
    store, cal, cs = make()
    uid = f"kliky-{D_NE.isoformat()}-morning-stary"
    start = datetime.combine(D_NE, hhmm_to_time("07:00"), tzinfo=TZ)
    cal.create(uid, build_event_ics(uid, "💪 Ráno: 6 klikov", start, 15))
    out = cs.sync(plan_day(D_NE), X5)
    assert cal.list() == [] and not out.errors


def test_utorkove_rano_zavrie_cely_den_a_umlci_budik():
    store, cal, cs = make()
    day = plan_day(D_UT)
    cs.sync(day, X5)
    done = day.copy(morning=day.morning_target)
    assert done.done                                   # 2X ráno je celý utorkový cieľ
    cs.sync(done, X5)
    m = by(cal, "Ráno")
    assert m.summary == "✅ 💪 Ráno: 10 klikov" and not has_alarm(m)
    assert len(cal.list()) == 1                        # večerný event nepribudne ani po splnení


def test_nazvy_eventov_obsahuju_dvojnasobok_x():
    store, cal, cs = make()
    cs.sync(plan_day(D), X5)                           # streda: 2X ráno + 2X večer
    assert sorted(i.summary for i in cal.list()) == ["💪 Ráno: 10 klikov", "💪 Večer: 10 klikov"]
    _, cal2, cs2 = make()
    cs2.sync(plan_day(D_PO_NEXT), X5)                  # o týždeň je X o krok vyššie → 2X = 12
    assert sorted(i.summary for i in cal2.list()) == ["💪 Ráno: 12 klikov", "💪 Večer: 12 klikov"]


def test_strata_db_neduplikuje_eventy():
    """Rovnaká záruka ako pri pripomienkach (test_lost_db_adopts_existing_reminders…):
    dnešný event sa po strate DB prevezme podľa UID, nevytvorí sa druhý raz."""
    store, cal, cs = make()
    cs.sync(plan_day(D), X5)
    assert len(cal.list()) == 2
    cs2 = CalendarSync(cal, Store(":memory:"), TZ)      # nová prázdna DB, ten istý kalendár
    out = cs2.sync(plan_day(D), X5)
    assert len(cal.list()) == 2 and not out.errors
