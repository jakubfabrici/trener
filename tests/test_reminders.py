"""Pripomienky – Duolingo správanie nad falošným CalDAV zoznamom v pamäti."""
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from trener.caldav_todo import apply_changes, build_todo_ics, parse_item
from trener.engine import apply_reports
from trener.model import EVENING, MORNING, Day, ReminderState, Settings
from trener.reminders import ReminderSync, due_for, learn_template, session_number
from trener.store import Store

TZ = ZoneInfo("Europe/Bratislava")
# Pozor: D je STREDA – deň s rannou aj večernou fázou. Na vetvy ut/št/so a nedeľu
# treba iné dátumy, inak sa celý nový plán otestuje len na jednom type dňa.
D = date(2026, 9, 2)            # streda – ráno 2X aj večer 2X
D_UT = date(2026, 9, 1)         # utorok – len ráno 2X
D_PO = date(2026, 8, 31)        # pondelok toho istého týždňa – ráno aj večer
D_NE = date(2026, 9, 6)         # nedeľa – voľno, cieľ 0
D_PO_NEXT = date(2026, 9, 7)    # pondelok ďalšieho týždňa – X je už o krok vyššie
X10 = Settings(x=10, x_since="2026-08-31")   # X = 10 pre týždeň 31.8. – 6.9.2026 (jedna séria)


class FakeTodos:
    component = "VTODO"

    def __init__(self):
        self.items = {}     # href -> (etag, ics)
        self.n = 0

    def list(self):
        return [parse_item(h, e, ics, self.component) for h, (e, ics) in self.items.items()]

    def get(self, href):
        if href not in self.items:
            return None
        e, ics = self.items[href]
        return parse_item(href, e, ics, self.component)

    def create(self, uid, ics):
        self.n += 1
        href = f"http://x/jakub/col/{uid}.ics"
        self.items[href] = (f'"{self.n}"', ics)
        return parse_item(href, f'"{self.n}"', ics, self.component)

    def put(self, item, ics):
        assert item.href in self.items
        assert self.items[item.href][0] == item.etag, "stale etag"
        self.n += 1
        self.items[item.href] = (f'"{self.n}"', ics)
        return parse_item(item.href, f'"{self.n}"', ics, self.component)

    def delete(self, item):
        self.items.pop(item.href, None)

    # simulácia úprav z telefónu
    def user_edit(self, href, **changes):
        item = self.get(href)
        self.put(item, apply_changes(item, **changes))

    def by_summary_contains(self, s):
        return [i for i in self.list() if s in i.summary]


def make():
    store = Store(":memory:")
    todos = FakeTodos()
    return store, todos, ReminderSync(todos, store, TZ)


def plan_day(d: date, settings: Settings = X10, **kw) -> Day:
    """Deň s cieľom presne podľa týždenného plánu (nie ručne vymysleným číslom)."""
    return Day(d, settings.goal_for(d), **kw)


def leftover(store, todos, d: date, session: str, summary: str, hhmm: str):
    """Pripomienka, ktorá na dni zostala po starom pláne – aj so záznamom v DB."""
    uid = f"kliky-{d.isoformat()}-{session}-stary"
    created = todos.create(uid, build_todo_ics(uid, summary, due_for(d, hhmm, TZ)))
    store.save_reminder(ReminderState(d, session, created.href, uid, created.etag, summary, hhmm))
    return created


def test_creates_two_reminders_with_times_and_numbers():
    store, todos, rs = make()
    out = rs.sync(Day(D, 10), Settings(morning_time="07:00", evening_time="19:20"))
    assert not out.errors
    items = sorted(todos.list(), key=lambda i: i.due)
    assert [i.summary for i in items] == ["💪 Ráno: 5 klikov", "💪 Večer: 5 klikov"]
    assert items[0].due.astimezone(TZ).strftime("%H:%M") == "07:00"
    assert items[1].due.astimezone(TZ).strftime("%H:%M") == "19:20"
    assert not any(i.completed for i in items)
    # opakovaný sync nič nemení
    out2 = rs.sync(Day(D, 10), Settings())
    assert len(todos.list()) == 2 and not out2.notes


def test_completes_when_session_done_and_updates_evening_number():
    store, todos, rs = make()
    rs.sync(Day(D, 10), Settings())
    out = rs.sync(Day(D, 10, morning=2), Settings())
    m = todos.by_summary_contains("Ráno")[0]
    e = todos.by_summary_contains("Večer")[0]
    assert m.summary == "💪 Ráno: 3 klikov" and e.summary == "💪 Večer: 8 klikov"
    rs.sync(Day(D, 10, morning=5), Settings())
    m = todos.by_summary_contains("Ráno")[0]
    assert m.completed
    rs.sync(Day(D, 10, morning=5, evening=5), Settings())
    assert all(i.completed for i in todos.list())


def test_user_renames_reminder_learns_template():
    store, todos, rs = make()
    rs.sync(Day(D, 10), Settings())
    m = todos.by_summary_contains("Ráno")[0]
    todos.user_edit(m.href, summary="Kliky ráno – 5 kusov 💪")
    out = rs.sync(Day(D, 10), Settings())
    assert out.settings.morning_title == "Kliky ráno – {n} kusov 💪"
    assert "morning_title" in out.settings_changed
    # a s novým číslom sa použije nová šablóna
    out = rs.sync(Day(D, 10, morning=2), out.settings)
    assert todos.by_summary_contains("Kliky ráno")[0].summary == "Kliky ráno – 3 kusov 💪"


def test_user_changes_time_becomes_new_default():
    store, todos, rs = make()
    rs.sync(Day(D, 10), Settings(morning_time="07:00"))
    m = todos.by_summary_contains("Ráno")[0]
    new_due = datetime(2026, 9, 2, 6, 30, tzinfo=TZ)
    todos.user_edit(m.href, due=new_due)
    out = rs.sync(Day(D, 10), Settings(morning_time="07:00"))
    assert out.settings.morning_time == "06:30" and "morning_time" in out.settings_changed
    # bot čas nevracia späť
    out = rs.sync(Day(D, 10), out.settings)
    assert todos.by_summary_contains("Ráno")[0].due.astimezone(TZ).strftime("%H:%M") == "06:30"


def test_user_ticks_reminder_counts_as_done():
    store, todos, rs = make()
    rs.sync(Day(D, 10), Settings())
    m = todos.by_summary_contains("Ráno")[0]
    todos.user_edit(m.href, complete=True)
    out = rs.sync(Day(D, 10), Settings())
    assert [(r.session, r.n, r.absolute) for r in out.reports] == [(MORNING, 5, True)]
    e = todos.by_summary_contains("Večer")[0]
    todos.user_edit(e.href, complete=True)
    out = rs.sync(Day(D, 10, morning=5), Settings())
    assert [(r.session, r.n, r.absolute) for r in out.reports] == [(EVENING, 5, True)]


def test_user_unticks_is_respected():
    store, todos, rs = make()
    rs.sync(Day(D, 10), Settings())
    rs.sync(Day(D, 10, morning=5), Settings())
    m = todos.by_summary_contains("Ráno")[0]
    assert m.completed
    todos.user_edit(m.href, complete=False)
    rs.sync(Day(D, 10, morning=5), Settings())
    assert not todos.by_summary_contains("Ráno")[0].completed


def test_user_deletes_reminder_not_recreated_today():
    store, todos, rs = make()
    rs.sync(Day(D, 10), Settings())
    m = todos.by_summary_contains("Ráno")[0]
    todos.delete(m)
    rs.sync(Day(D, 10), Settings())
    rs.sync(Day(D, 10), Settings())
    assert len(todos.by_summary_contains("Ráno")) == 0
    # zajtra sa vytvorí normálne
    rs.sync(Day(date(2026, 9, 3), 12), Settings())
    assert len(todos.by_summary_contains("Ráno")) == 1


def test_frozen_no_new_reminders_and_existing_left_alone():
    store, todos, rs = make()
    rs.sync(Day(D, 10), Settings(frozen=True))
    assert todos.list() == []
    rs.sync(Day(D, 10), Settings())
    assert len(todos.list()) == 2
    rs.sync(Day(D, 10, morning=2), Settings(frozen=True))
    assert todos.by_summary_contains("Ráno")[0].summary == "💪 Ráno: 5 klikov"   # nezmenené


def test_old_open_reminders_deleted_completed_kept():
    # starý deň musí mať obe fázy, inak nie je čo mazať – teda pondelok, nie utorok
    store, todos, rs = make()
    rs.sync(Day(D_PO, 10), Settings())
    rs.sync(Day(D_PO, 10, morning=5), Settings())   # ranná odčiarknutá
    assert len(todos.list()) == 2
    rs.sync(Day(D, 10), Settings())
    summaries = sorted(i.summary for i in todos.list())
    # pondelková večerná (otvorená) zmazaná, pondelková ranná (splnená) ostala, + 2 dnešné
    assert len(summaries) == 3 and sum(1 for i in todos.list() if i.completed) == 1


def test_learn_template_and_numbers():
    assert learn_template("Kliky ráno 5!", 5) == "Kliky ráno {n}!"
    assert learn_template("Padaj na zem", 5) == "Padaj na zem"
    assert learn_template("2x denne 7 klikov", 7) == "2x denne {n} klikov"
    assert learn_template("Kliky 10 ráno", 1) == "Kliky {n} ráno"        # nie „{n}0“
    assert learn_template("Kliky 1 ráno (10 sérií)", 1) == "Kliky {n} ráno (10 sérií)"
    assert learn_template("Ráno {n} klikov", 3) == "Ráno {n} klikov"
    assert session_number(Day(D, 10), EVENING) == 5
    assert session_number(Day(D, 10, morning=2), EVENING) == 8
    assert session_number(Day(D, 10, morning=2), MORNING) == 3


def test_apple_style_vtodo_round_trip_keeps_x_props():
    ics = (b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Apple Inc.//iOS 18.6//EN\r\n"
           b"BEGIN:VTIMEZONE\r\nTZID:Europe/Bratislava\r\nBEGIN:DAYLIGHT\r\nTZOFFSETFROM:+0100\r\n"
           b"RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU\r\nDTSTART:19810329T020000\r\nTZNAME:CEST\r\n"
           b"TZOFFSETTO:+0200\r\nEND:DAYLIGHT\r\nBEGIN:STANDARD\r\nTZOFFSETFROM:+0200\r\n"
           b"RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU\r\nDTSTART:19961027T030000\r\nTZNAME:CET\r\n"
           b"TZOFFSETTO:+0100\r\nEND:STANDARD\r\nEND:VTIMEZONE\r\n"
           b"BEGIN:VTODO\r\nUID:abc\r\nDTSTAMP:20260902T050000Z\r\nSUMMARY:\xf0\x9f\x92\xaa R\xc3\xa1no: 5 klikov\r\n"
           b"DUE;TZID=Europe/Bratislava:20260902T063000\r\nX-APPLE-SORT-ORDER:12345\r\nSEQUENCE:3\r\n"
           b"BEGIN:VALARM\r\nACTION:DISPLAY\r\nTRIGGER;VALUE=DATE-TIME:20260902T043000Z\r\n"
           b"X-WR-ALARMUID:1\r\nUID:1\r\nEND:VALARM\r\nEND:VTODO\r\nEND:VCALENDAR\r\n")
    item = parse_item("h", "e", ics)
    assert item.due.astimezone(TZ).strftime("%H:%M") == "06:30" and not item.completed
    out = apply_changes(item, complete=True)
    assert b"X-APPLE-SORT-ORDER:12345" in out and b"STATUS:COMPLETED" in out and b"SEQUENCE:4" in out
    assert b"BEGIN:VALARM" in out and b"X-WR-ALARMUID:1" in out      # ako Apple: alarm ostáva
    assert b"PERCENT-COMPLETE:100" in out and b"COMPLETED:" in out
    item2 = parse_item("h", "e", out)
    assert item2.completed


def test_both_ticked_in_one_sync_gives_exact_goal_not_double():
    store, todos, rs = make()
    rs.sync(Day(D, 12), Settings())
    for it in todos.list():
        todos.user_edit(it.href, complete=True)
    out = rs.sync(Day(D, 12), Settings())
    from trener.engine import apply_reports
    day = apply_reports(Day(D, 12), out.reports).day
    assert (day.morning, day.evening, day.total) == (6, 6, 12)


def test_evening_reminder_completes_only_with_whole_day():
    store, todos, rs = make()
    rs.sync(Day(D, 12), Settings())
    rs.sync(Day(D, 12, morning=2, evening=6), Settings())     # večerné vedro „plné“, deň nie
    e = todos.by_summary_contains("Večer")[0]
    assert not e.completed and e.summary == "💪 Večer: 4 klikov"
    rs.sync(Day(D, 12, morning=2, evening=10), Settings())
    assert todos.by_summary_contains("Večer")[0].completed


def test_completion_keeps_last_summary_not_zero():
    store, todos, rs = make()
    rs.sync(Day(D, 10), Settings())
    rs.sync(Day(D, 10, morning=3), Settings())
    rs.sync(Day(D, 10, morning=5), Settings())
    m = todos.by_summary_contains("Ráno")[0]
    assert m.completed and m.summary == "💪 Ráno: 2 klikov"


def test_lost_db_adopts_existing_reminders_instead_of_duplicating():
    store, todos, rs = make()
    rs.sync(Day(D, 10), Settings())
    assert len(todos.list()) == 2
    fresh_store = Store(":memory:")
    rs2 = ReminderSync(todos, fresh_store, TZ)
    out = rs2.sync(Day(D, 10, morning=2), Settings())
    assert len(todos.list()) == 2 and any("prevzatá" in n for n in out.notes)
    assert todos.by_summary_contains("Ráno")[0].summary == "💪 Ráno: 3 klikov"


def test_backend_exception_becomes_error_not_crash():
    store, todos, rs = make()

    class Boom(FakeTodos):
        def list(self):
            raise RuntimeError("connection refused")
    rs.todos = Boom()
    out = rs.sync(Day(D, 10), Settings())
    assert out.errors and not out.reports


def test_alarm_trigger_is_absolute_with_value_param():
    from trener.caldav_todo import build_todo_ics
    ics = build_todo_ics("u1", "x", datetime(2026, 9, 2, 5, 0, tzinfo=timezone.utc))
    assert b"TRIGGER;VALUE=DATE-TIME:20260902T050000Z" in ics
    it = parse_item("h", "e", ics)
    out = apply_changes(it, due=datetime(2026, 9, 2, 6, 0, tzinfo=timezone.utc))
    assert b"TRIGGER;VALUE=DATE-TIME:20260902T060000Z" in out and out.count(b"TRIGGER") == 1


# ── nový týždenný plán: voľné dni a fázy, ktoré v ten deň nie sú ────────────

def test_v_nedelu_ziadna_pripomienka():
    store, todos, rs = make()
    day = plan_day(D_NE)
    assert day.is_rest and day.goal == 0
    out = rs.sync(day, X10)
    assert todos.list() == [] and not out.errors and not out.reports
    rs.sync(day, X10)                                   # ani po druhom syncu
    assert todos.list() == []
    assert store.get_reminder(D_NE, MORNING) is None
    assert store.get_reminder(D_NE, EVENING) is None


def test_v_utorok_len_ranna_pripomienka():
    store, todos, rs = make()
    day = plan_day(D_UT)
    assert (day.goal, day.morning_target, day.evening_target) == (10, 10, 0)   # 2X ráno
    out = rs.sync(day, X10)
    assert [i.summary for i in todos.list()] == ["💪 Ráno: 10 klikov"]
    assert not out.errors
    assert store.get_reminder(D_UT, EVENING) is None
    rs.sync(day, X10)                                   # večerná nepribudne ani neskôr
    assert todos.by_summary_contains("Večer") == []


def test_stara_vecerna_pripomienka_v_utorok_sa_zmaze_nie_odciarkne():
    store, todos, rs = make()
    leftover(store, todos, D_UT, EVENING, "💪 Večer: 6 klikov", "19:20")
    out = rs.sync(plan_day(D_UT), X10)
    assert [i.summary for i in todos.list()] == ["💪 Ráno: 10 klikov"]   # večerná je preč
    assert not any(i.completed for i in todos.list())                    # nie „splnená“, ale zmazaná
    assert store.get_reminder(D_UT, EVENING) is None
    assert any("zmazaná" in n and "utorok" in n for n in out.notes)


def test_nedelny_zvysok_po_starom_plane_sa_zmaze():
    store, todos, rs = make()
    leftover(store, todos, D_NE, MORNING, "💪 Ráno: 6 klikov", "07:00")
    leftover(store, todos, D_NE, EVENING, "💪 Večer: 6 klikov", "19:20")
    out = rs.sync(plan_day(D_NE), X10)
    assert todos.list() == [] and not out.errors
    assert store.get_reminder(D_NE, MORNING) is None
    assert store.get_reminder(D_NE, EVENING) is None


def test_nedelny_zvysok_sa_zmaze_aj_bez_zaznamu_v_db():
    """Po strate DB pripomienku poznáme len podľa UID – aj tak ju treba upratať."""
    store, todos, rs = make()
    for session, summary, hhmm in ((MORNING, "💪 Ráno: 6 klikov", "07:00"),
                                   (EVENING, "💪 Večer: 6 klikov", "19:20")):
        uid = f"kliky-{D_NE.isoformat()}-{session}-stary"
        todos.create(uid, build_todo_ics(uid, summary, due_for(D_NE, hhmm, TZ)))
    out = rs.sync(plan_day(D_NE), X10)
    assert todos.list() == [] and not out.errors


def test_odciarknutie_rannej_v_utorok_zavrie_cely_den():
    store, todos, rs = make()
    day = plan_day(D_UT)
    rs.sync(day, X10)
    m = todos.by_summary_contains("Ráno")[0]
    todos.user_edit(m.href, complete=True)
    out = rs.sync(day, X10)
    assert [(r.session, r.n, r.absolute) for r in out.reports] == [(MORNING, 10, True)]
    closed = apply_reports(day, out.reports)
    assert closed.day.total == 10 and closed.day.done and closed.completed_now
    # deň je hotový rannou fázou – večerná sa nedorobí ani teraz
    rs.sync(closed.day, X10)
    assert [i.summary for i in todos.list()] == ["💪 Ráno: 10 klikov"]
    assert todos.by_summary_contains("Ráno")[0].completed


def test_nazvy_pripomienok_nesu_jedno_x():
    store, todos, rs = make()
    rs.sync(plan_day(D), X10)                           # streda: 2X ráno + 2X večer
    assert sorted(i.summary for i in todos.list()) == ["💪 Ráno: 10 klikov", "💪 Večer: 10 klikov"]
    _, todos2, rs2 = make()
    rs2.sync(plan_day(D_PO_NEXT), X10)                  # o týždeň je X o krok vyššie → 2X = 12
    assert sorted(i.summary for i in todos2.list()) == ["💪 Ráno: 11 klikov", "💪 Večer: 11 klikov"]


def test_session_number_v_neplanovanych_fazach():
    assert session_number(plan_day(D_UT), MORNING) == 10          # 2X
    assert plan_day(D_UT).session_planned(EVENING) is False
    assert session_number(plan_day(D_NE), MORNING) == 0
    assert plan_day(D_NE).session_planned(MORNING) is False
