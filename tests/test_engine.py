"""Čistá logika: sčítavanie, fázy, streak, prechod dňa, limity výziev."""
from datetime import date, datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

from trener import messages as M
from trener.engine import (apply_reports, compute_streak, current_session, default_session,
                           fill_missing_days, nag_due, next_goal, plan, streak_after)
from trener.model import EVENING, MORNING, Day, NagState, Report, Settings, Snapshot, split_goal

TZ = ZoneInfo("Europe/Bratislava")
D = date(2026, 9, 2)


def at(h, m=0, d=D):
    return datetime.combine(d, dtime(h, m), tzinfo=TZ)


def test_split_goal():
    assert split_goal(10) == (5, 5)
    assert split_goal(11) == (6, 5)
    assert split_goal(1) == (1, 0)
    assert split_goal(0) == (0, 0)


def test_user_scenario_two_plus_two_is_four_not_ten():
    day = Day(D, 10)
    r1 = apply_reports(day, [Report(MORNING, 2)])
    assert (r1.day.morning, r1.day.total, r1.completed_now) == (2, 2, False)
    r2 = apply_reports(r1.day, [Report(MORNING, 2)])
    assert (r2.day.morning, r2.day.total, r2.day.left, r2.completed_now) == (4, 4, 6, False)
    assert not r2.day.done
    assert r2.day.morning_left == 1


def test_completion_announced_exactly_once():
    day = Day(D, 10)
    r = apply_reports(day, [Report(MORNING, 5)])
    assert r.session_completed_now == [MORNING] and not r.completed_now
    r = apply_reports(r.day, [Report(EVENING, 5)])
    assert r.completed_now and r.day.done
    r = apply_reports(r.day, [Report(EVENING, 1)])
    assert not r.completed_now and r.day.done and r.day.total == 11


def test_absolute_report_sets_value():
    day = Day(D, 10, morning=4)
    r = apply_reports(day, [Report(MORNING, 5, absolute=True)])
    assert r.day.morning == 5 and r.added[MORNING] == 1
    r = apply_reports(r.day, [Report(MORNING, 0, absolute=True)])
    assert r.day.morning == 0 and r.added[MORNING] == -5


def test_whole_goal_in_morning_completes_both_sessions():
    r = apply_reports(Day(D, 10), [Report(MORNING, 10)])
    assert r.completed_now and r.day.session_done(EVENING)


def test_default_session_by_evening_time():
    s = Settings(evening_time="19:20")
    assert default_session(at(10), s) == MORNING
    assert default_session(at(19, 19), s) == MORNING
    assert default_session(at(19, 20), s) == EVENING
    assert default_session(at(23, 59), s) == EVENING


def test_streak_rules():
    days = [Day(date(2026, 8, 29), 10, 5, 5), Day(date(2026, 8, 30), 10, 10, 0),
            Day(date(2026, 8, 31), 10, 0, 0, frozen=True), Day(date(2026, 9, 1), 12, 6, 6),
            Day(D, 14, 0, 0)]
    assert compute_streak(days, D) == 3          # dnešok otvorený streak neprerušuje
    days[1] = Day(date(2026, 8, 30), 10, 2, 0)   # nesplnený → reset
    assert compute_streak(days, D) == 1
    assert streak_after(days, date(2026, 8, 29), D) == 1
    assert streak_after(days, date(2026, 8, 30), D) == 0
    assert streak_after(days, D, D) == 1            # dnešok otvorený: streak z včerajška


def test_next_goal_progression():
    s = Settings(increment=2)
    assert next_goal(None, s, 10) == 10
    assert next_goal(Day(D, 10, 5, 5), s, 10) == 12
    assert next_goal(Day(D, 10, 4, 0), s, 10) == 10      # bez trestu
    assert next_goal(Day(D, 10, 5, 5), Settings(increment=0), 10) == 10


def test_fill_missing_days_after_downtime_freezes_gap():
    days = [Day(date(2026, 8, 31), 10, 4, 6)]
    new = fill_missing_days(days, date(2026, 9, 3), Settings(increment=2), 10)
    assert [(d.date.day, d.goal, d.frozen) for d in new] == [(1, 12, True), (2, 12, True), (3, 12, False)]
    assert compute_streak(days + new, date(2026, 9, 3)) == 1


def test_fill_missing_days_normal_midnight():
    days = [Day(date(2026, 9, 1), 12, 6, 6)]
    new = fill_missing_days(days, D, Settings(increment=2), 10)
    assert [(d.date, d.goal, d.frozen) for d in new] == [(D, 14, False)]


def test_fill_missing_days_when_frozen():
    days = [Day(date(2026, 9, 1), 12, 0, 0, frozen=True)]
    new = fill_missing_days(days, D, Settings(frozen=True), 10)
    assert new[0].frozen and new[0].goal == 12


def _run_nags(s, day, session, times, woke=False):
    nag = NagState(D, session)
    sent_at = []
    for t in times:
        if nag_due(t, s, session, day, nag, woke):
            nag.sent += 1
            nag.last_at = t
            sent_at.append(t.strftime("%H:%M"))
    return sent_at


def test_nag_max_three_every_thirty_minutes():
    s = Settings(morning_time="07:00", evening_time="19:20", nag_max=3, nag_interval_min=30)
    times = [at(6, 59)] + [at(7, 0) + timedelta(minutes=i) for i in range(0, 200, 1)]
    assert _run_nags(s, Day(D, 10), MORNING, times) == ["07:00", "07:30", "08:00"]


def test_nag_stops_when_session_done_or_day_done():
    s = Settings()
    assert _run_nags(s, Day(D, 10, morning=5), MORNING, [at(7), at(7, 30)]) == []
    assert _run_nags(s, Day(D, 10, morning=5), EVENING, [at(19, 20), at(19, 50)]) == ["19:20", "19:50"]
    assert _run_nags(s, Day(D, 10, morning=5, evening=5), EVENING, [at(19, 20)]) == []


def test_partial_morning_keeps_nagging_for_remainder_only_up_to_limit():
    s = Settings()
    day = Day(D, 10, morning=2)
    assert _run_nags(s, day, MORNING, [at(7), at(7, 30), at(8), at(8, 30), at(9)]) == ["07:00", "07:30", "08:00"]
    assert "3" in M.nag(MORNING, day, 1, 3)


def test_nag_never_when_frozen():
    assert _run_nags(Settings(frozen=True), Day(D, 10), MORNING, [at(7), at(7, 30)]) == []
    assert _run_nags(Settings(), Day(D, 10, frozen=True), MORNING, [at(7), at(7, 30)]) == []


def test_no_nag_burst_after_downtime():
    s = Settings()
    # bot naskočí o 11:00 – ranná výzva z 07:00 sa už nedoháňa
    assert _run_nags(s, Day(D, 10), MORNING, [at(11), at(11, 30)]) == []
    # večer o 23:30 (štart 19:20 je starší než 3 h) – tiež nie
    assert _run_nags(s, Day(D, 10), EVENING, [at(23, 30)]) == []
    # ale o 21:00 ešte áno (1 h 40 po štarte)
    assert _run_nags(s, Day(D, 10), EVENING, [at(21)]) == ["21:00"]


def test_morning_nags_stop_at_evening_time():
    s = Settings(morning_time="07:00", evening_time="07:40", nag_interval_min=30)
    assert _run_nags(s, Day(D, 10), MORNING, [at(7), at(7, 30), at(8)]) == ["07:00", "07:30"]


def test_wake_signal_starts_morning_early():
    s = Settings(morning_time="07:00")
    assert _run_nags(s, Day(D, 10), MORNING, [at(5, 30)], woke=True) == ["05:30"]
    assert _run_nags(s, Day(D, 10), MORNING, [at(3, 30)], woke=True) == []
    assert _run_nags(s, Day(D, 10), MORNING, [at(5, 30)], woke=False) == []


def test_current_session():
    s = Settings(morning_time="07:00", evening_time="19:20")
    assert current_session(at(6), s) is None
    assert current_session(at(7), s) == MORNING
    assert current_session(at(19, 20), s) == EVENING


def test_plan_at_most_one_action():
    s = Settings(morning_time="07:00", evening_time="19:20")
    snap = Snapshot(at(19, 20), s, Day(D, 10), {})
    acts = plan(snap)
    assert [(a.kind, a.session) for a in acts] == [("nag", EVENING)]


def test_report_reply_text():
    day = Day(D, 10, morning=4)
    txt = M.report_reply(day, {MORNING: 2, EVENING: 0}, False, 1, 12)
    assert "+2 ráno" in txt and "ráno 4/5" in txt and "dnes 4/10" in txt and "zostáva 6" in txt
    done = M.report_reply(Day(D, 10, 5, 5), {MORNING: 0, EVENING: 5}, True, 3, 12)
    assert "🎉" in done and "Streak 3" in done and "12" in done
