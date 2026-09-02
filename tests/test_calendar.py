"""Budík v iCloud kalendári: dva eventy denne s alarmom, učenie sa úprav, uzavretie dňa."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from icalendar import Calendar

from trener.calendar_sync import CalendarSync, apply_event_changes, build_event_ics, event_start
from trener.model import EVENING, MORNING, Day, Settings
from trener.store import Store
from test_app import FakeCalendar

TZ = ZoneInfo("Europe/Bratislava")
D = date(2026, 9, 2)


def make():
    store = Store(":memory:")
    cal = FakeCalendar()
    return store, cal, CalendarSync(cal, store, TZ)


def by(cal, word):
    hits = [i for i in cal.list() if word in i.summary]
    return hits[0] if hits else None


def has_alarm(item) -> bool:
    return any(c.name == "VALARM" for c in item.vtodo.subcomponents)


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
