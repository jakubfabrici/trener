"""Čistá logika: sčítavanie, fázy, týždenný plán, streak, prechod dňa, limity výziev."""
from datetime import date, datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

from trener import messages as M
from trener.engine import (WEEK_DONE, WEEK_EMPTY, WEEK_FAILED, WEEK_OPEN, apply_reports,
                           compute_streak, current_session, default_session, elapsed,
                           fill_missing_days, nag_due, next_goal, plan, replan, streak_after,
                           week_status)
from trener.model import (EVENING, MORNING, REST, Day, NagState, Report, Settings, Snapshot,
                          plan_targets, split_goal)

TZ = ZoneInfo("Europe/Bratislava")
D = date(2026, 9, 2)

# celý týždeň okolo D, nech je vidno, o ktorý deň v týždni ide
PO = date(2026, 8, 31)
UT = date(2026, 9, 1)
ST = D
STV = date(2026, 9, 3)
PIA = date(2026, 9, 4)
SO = date(2026, 9, 5)
NE = date(2026, 9, 6)
PO2 = date(2026, 9, 7)          # pondelok nasledujúceho týždňa – už s novým X


def at(h, m=0, d=D):
    return datetime.combine(d, dtime(h, m), tzinfo=TZ)


def nast(**kw) -> Settings:
    """X = 3 od pondelka 31. 8. → streda má cieľ 12 (6 ráno + 6 večer), utorok 6."""
    return Settings(x=6, x_since=PO.isoformat(), **kw)


def tyzden_dni(monday: date, s: Settings, *, splnene: bool = True, frozen: bool = False) -> list[Day]:
    """Riadky pondelok–sobota podľa plánu; nedeľa je voľno a riadok pre ňu netreba."""
    out = []
    for i in range(6):
        dd = monday + timedelta(days=i)
        g = s.goal_for(dd)
        m, e = split_goal(g, dd)
        if frozen:
            out.append(Day(dd, g, frozen=True))
        elif splnene:
            out.append(Day(dd, g, m, e))
        else:
            out.append(Day(dd, g))
    return out


# ── plán týždňa ──────────────────────────────────────────────────────────────

def test_split_goal():
    # bez dátumu: pol na pol (spätná kompatibilita so starými volaniami)
    assert split_goal(10) == (5, 5)
    assert split_goal(11) == (6, 5)
    assert split_goal(1) == (1, 0)
    assert split_goal(0) == (0, 0)


def test_split_goal_podla_dna_v_tyzdni():
    assert split_goal(12, PO) == (6, 6)          # pondelok/streda/piatok – ráno aj večer
    assert split_goal(12, ST) == (6, 6)
    assert split_goal(11, PIA) == (6, 5)         # nepárne: ráno nahor
    assert split_goal(6, UT) == (6, 0)           # utorok/štvrtok/sobota – všetko ráno
    assert split_goal(6, SO) == (6, 0)
    assert split_goal(12, NE) == (12, 0)         # ručne nastavená nedeľa sa tiež dá ráno
    assert split_goal(0, ST) == (0, 0)
    assert split_goal(-5, ST) == (0, 0)


def test_plan_targets_pre_cely_tyzden():
    assert [plan_targets(PO + timedelta(days=i), 3) for i in range(7)] == [
        (3, 3), (3, 0), (3, 3), (3, 0), (3, 3), (3, 0), (0, 0)]
    assert plan_targets(PO, 1) == (1, 1)
    assert plan_targets(NE, 100) == (0, 0)       # nedeľa je voľno pri akomkoľvek X
    # týždenný objem: 2X + X + 2X + X + 2X + X + 0 = 9X
    assert sum(sum(plan_targets(PO + timedelta(days=i), 3)) for i in range(7)) == 9 * 3


def test_goal_for_kopiruje_plan_dna():
    s = nast()
    assert s.targets_for(ST) == (6, 6) and s.goal_for(ST) == 12
    assert s.targets_for(UT) == (6, 0) and s.goal_for(UT) == 6
    assert s.targets_for(NE) == (0, 0) and s.goal_for(NE) == 0
    assert s.goal_for(PO2) == 14                 # nový týždeň, X = 7 → 7 + 7


def test_x_rastie_kazdy_pondelok():
    s = Settings(x=8, x_since="2026-09-07")
    assert s.x_for(date(2026, 9, 13)) == 8       # nedeľa – ešte starý týždeň
    assert s.x_for(date(2026, 9, 14)) == 9       # pondelok – nové X
    assert s.x_for(date(2026, 9, 21)) == 10      # dva týždne dopredu
    assert s.x_for(date(2026, 8, 31)) == 7       # týždeň dozadu
    assert Settings(x=8, x_since="2026-09-07", x_step=3).x_for(date(2026, 9, 14)) == 11
    assert Settings(x=8, x_since="2026-09-07", x_step=0).x_for(date(2026, 9, 21)) == 8
    # x_since nemusí byť pondelok ani platný dátum – zarovná sa / spadne na default
    assert Settings(x=8, x_since="2026-09-09").x_for(date(2026, 9, 14)) == 9
    assert Settings(x=8, x_since="blbost").x_for(date(2026, 9, 14)) == 9


def test_x_nikdy_neklesne_pod_jedna():
    s = Settings(x=8, x_since="2026-09-07")
    assert s.x_for(date(2019, 1, 7)) == 1
    assert s.x_for(date(2026, 6, 1)) >= 1
    # …a preto sa staré týždne netvária ako samé voľno (inak by streak nešlo prerušiť)
    assert s.goal_for(date(2019, 1, 7)) == 2     # pondelok = X + X
    assert s.goal_for(date(2019, 1, 8)) == 1     # utorok = X


def test_with_x_plati_od_pondelka_a_ma_spodnu_hranicu():
    s = nast()
    s2, monday = s.with_x(9, ST)
    assert monday == PO and s2.x == 9 and s2.x_since == PO.isoformat()
    assert s2.goal_for(ST) == 18 and s2.goal_for(UT) == 9
    # v nedeľu (týždeň sa končí) sa nové X myslí na nasledujúci týždeň
    s3, monday3 = s.with_x(9, NE)
    assert monday3 == PO2 and s3.goal_for(PO2) == 18
    # X pod 1 nedáva zmysel
    assert s.with_x(0, ST)[0].x == 1


# ── deň, fázy, voľno ─────────────────────────────────────────────────────────

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


def test_utorok_ma_len_rannu_fazu():
    ut = Day(UT, 6, morning=2)
    assert ut.weekday_sk == "utorok"
    assert (ut.morning_target, ut.evening_target) == (6, 0)
    assert ut.session_planned(MORNING) and not ut.session_planned(EVENING)
    assert not ut.session_done(MORNING) and ut.session_done(EVENING)   # večer sa nič nečaká
    assert ut.morning_left == 4 and ut.evening_left == 0
    hotovy = ut.copy(morning=6)
    assert hotovy.done and hotovy.session_done(MORNING) and hotovy.session_done(EVENING)
    # ranné hlásenie utorok uzavrie, večerná fáza sa ako „práve hotová“ nehlási
    r = apply_reports(ut, [Report(MORNING, 4)])
    assert r.completed_now and r.session_completed_now == [MORNING]


def test_volna_nedela_je_rest_a_nic_sa_v_nej_necaka():
    ne = Day(NE, 0)
    assert ne.is_rest and ne.weekday_sk == "nedeľa"
    assert ne.status(NE) == REST and ne.status(PO2) == REST       # ani po polnoci nie je „nesplnená“
    assert Day(NE, 0, frozen=True).status(PO2) == REST            # voľno je silnejšie než ❄️
    assert not ne.done and ne.left == 0
    assert not ne.session_planned(MORNING) and not ne.session_planned(EVENING)
    assert ne.session_done(MORNING) and ne.session_done(EVENING)
    assert (ne.morning_target, ne.evening_target) == (0, 0)
    assert not Day(ST, 12).is_rest


def test_nedelny_bonus_sa_zaratava_ale_nic_neplni():
    r = apply_reports(Day(NE, 0), [Report(MORNING, 5)])
    assert r.day.total == 5 and not r.completed_now and r.session_completed_now == []
    assert r.day.status(PO2) == REST
    txt = M.report_reply(r.day, r.added, r.completed_now, 2, 16)
    assert "voľno" in txt and "Spolu 5" in txt


# ── do ktorej fázy patrí číslo ───────────────────────────────────────────────

def test_default_session_by_evening_time():
    s = Settings(evening_time="19:20")
    assert default_session(at(10), s) == MORNING
    assert default_session(at(19, 19), s) == MORNING
    assert default_session(at(19, 20), s) == EVENING
    assert default_session(at(23, 59), s) == EVENING


def test_default_session_switches_when_morning_bucket_is_full():
    s = Settings(evening_time="19:20")
    # ráno 5/5 hotové, deň nie → poobedné číslo ide do večera, nie do „ráno 10/5“
    assert default_session(at(13, 45), s, Day(D, 10, morning=5)) == EVENING
    assert default_session(at(13, 45), s, Day(D, 10, morning=4)) == MORNING
    # splnený deň: extra kliky ostávajú v aktuálnej fáze podľa hodín
    assert default_session(at(13, 45), s, Day(D, 10, morning=5, evening=5)) == MORNING
    # večer ostáva večerom aj keď ráno chýba (dobehnúť sa dá cez „ráno N“)
    assert default_session(at(20, 0), s, Day(D, 10)) == EVENING


def test_default_session_v_utorok_vecer_neprelieva_do_rana():
    """Utorok večer nie je v pláne – číslo o 20:00 sa zapíše na večer, nie spätne do rána."""
    s = Settings(evening_time="19:20")
    ut = Day(UT, 6, morning=2)
    assert not ut.session_planned(EVENING)
    assert default_session(at(20, 0, UT), s, ut) == EVENING
    assert default_session(at(10, 0, UT), s, ut) == MORNING      # dopoludnia normálne ráno
    # to isté v nedeľu: fáza sa riadi hodinami, prelievať nie je kam
    assert default_session(at(20, 0, NE), s, Day(NE, 0)) == EVENING
    assert default_session(at(10, 0, NE), s, Day(NE, 0)) == MORNING


# ── stav týždňa a streak ─────────────────────────────────────────────────────

def test_week_status_splneny_tyzden():
    s = nast()
    dni = {d.date: d for d in tyzden_dni(PO, s)}
    assert week_status(dni, PO, PO2, s) == WEEK_DONE             # nedeľa sa nevyžaduje


def test_week_status_nesplneny_uzavrety_den_kazi_tyzden():
    s = nast()
    dni = {d.date: d for d in tyzden_dni(PO, s)}
    dni[UT] = Day(UT, 6, 2, 0)
    assert week_status(dni, PO, PO2, s) == WEEK_FAILED
    # chýbajúci riadok v minulosti je to isté ako nesplnený deň
    dni.pop(UT)
    assert week_status(dni, PO, PO2, s) == WEEK_FAILED


def test_week_status_bezici_tyzden_je_otvoreny():
    s = nast()
    dni = {d.date: d for d in tyzden_dni(PO, s)[:2]}             # pondelok + utorok splnené
    dni[ST] = Day(ST, 12)                                        # dnešok ešte otvorený
    assert week_status(dni, PO, ST, s) == WEEK_OPEN
    # nesplnený dnešok týždeň ešte nezhodí, nesplnený včerajšok áno
    dni[UT] = Day(UT, 6, 1, 0)
    assert week_status(dni, PO, ST, s) == WEEK_FAILED


def test_week_status_zamrazeny_tyzden_je_prazdny():
    s = nast()
    dni = {d.date: d for d in tyzden_dni(PO, s, frozen=True)}
    assert week_status(dni, PO, PO2, s) == WEEK_EMPTY
    # stačí jeden splnený deň medzi zamrazenými a týždeň sa počíta
    dni[ST] = Day(ST, 12, 6, 6)
    assert week_status(dni, PO, PO2, s) == WEEK_DONE


def test_week_status_tyzden_bez_zaznamov_v_minulosti_pada():
    s = nast()
    stary_pondelok = date(2019, 1, 7)
    assert s.x_for(stary_pondelok) == 1                          # X zarazené na 1, nie na 0
    assert week_status({}, stary_pondelok, PO, s) == WEEK_FAILED


def test_streak_rules():
    """Streak = celé splnené týždne. Zamrazený deň ani otvorený dnešok ho nerušia."""
    s = nast()
    dni = tyzden_dni(PO, s)
    dni[3] = Day(STV, 6, 0, 0, frozen=True)                      # ❄️ štvrtok – nevadí
    dni.append(Day(PO2, 16))                                     # dnešok, nový týždeň, otvorený
    assert compute_streak(dni, PO2, s) == 1
    dni[1] = Day(UT, 6, 2, 0)                                    # utorok nesplnený → týždeň padá
    assert compute_streak(dni, PO2, s) == 0
    assert compute_streak([], PO2, s) == 0


def test_compute_streak_cez_viac_tyzdnov_vratane_zamrazeneho():
    s = nast()
    a, b, c = date(2026, 8, 17), date(2026, 8, 24), PO
    dni = (tyzden_dni(a, s) + tyzden_dni(b, s, frozen=True) + tyzden_dni(c, s)
           + [Day(PO2, 16, 8, 8), Day(date(2026, 9, 8), 8, 8, 0)])
    dnes = date(2026, 9, 9)                                      # streda ďalšieho týždňa
    # bežiaci týždeň sa ešte neráta, zamrazený týždeň streak neprerušuje ani nepridáva
    assert compute_streak(dni, dnes, s) == 2
    # nesplnený deň v zamrazenom týždni reťaz preruší – staršie týždne sa už nerátajú
    dni[7] = Day(b + timedelta(days=1), s.goal_for(b + timedelta(days=1)))
    assert compute_streak(dni, dnes, s) == 1


def test_streak_after_stlpec_v_tabulke():
    s = nast()
    dni = tyzden_dni(PO, s)
    assert streak_after(dni, PO, PO2, s) == 0                    # týždeň ešte nie je hotový
    assert streak_after(dni, ST, PO2, s) == 0
    assert streak_after(dni, SO, PO2, s) == 1                    # v sobotu je týždeň uzavretý
    assert streak_after(dni, SO, None, s) == 1                   # bez „dnes“ = všetko uzavreté
    zly = [d if d.date != PIA else Day(PIA, 12) for d in dni]
    assert streak_after(zly, SO, PO2, s) == 0


# ── prechod dňa, doplnenie a preplánovanie ───────────────────────────────────

def test_next_goal_progression():
    """Cieľ zajtrajška je čisto z plánu – nezávisí od toho, ako dopadol dnešok."""
    s = nast()
    assert next_goal(Day(ST, 12, 6, 6), s) == 6                  # po stredu ide štvrtok: X
    assert next_goal(Day(ST, 12, 0, 0), s) == 6                  # ani odmena, ani trest
    assert next_goal(Day(STV, 6, 6, 0), s) == 12                 # piatok: X + X
    assert next_goal(Day(SO, 6, 6, 0), s) == 0                   # nedeľa je voľno
    assert next_goal(Day(NE, 0), s) == 14                        # pondelok už s novým X = 7


def test_fill_missing_days_after_downtime_freezes_gap():
    s = nast()
    days = [Day(PO, 12, 6, 6)]
    new = fill_missing_days(days, STV, s)                        # bot naskočil až vo štvrtok
    assert [(d.date, d.goal, d.frozen) for d in new] == [(UT, 6, True), (ST, 12, True), (STV, 6, False)]
    # zamrazená diera týždeň nezhodí a streak z minulého týždňa ostáva
    assert week_status({d.date: d for d in days + new}, PO, STV, s) == WEEK_OPEN
    assert compute_streak(tyzden_dni(date(2026, 8, 24), s) + days + new, STV, s) == 1


def test_fill_missing_days_normal_midnight():
    days = [Day(UT, 6, 6, 0)]
    new = fill_missing_days(days, ST, nast())
    assert [(d.date, d.goal, d.frozen) for d in new] == [(ST, 12, False)]


def test_fill_missing_days_when_frozen():
    days = [Day(UT, 6, 0, 0, frozen=True)]
    new = fill_missing_days(days, ST, nast(frozen=True))
    assert new[0].frozen and new[0].goal == 12


def test_fill_missing_days_ignores_future_rows():
    s = nast()
    days = [Day(UT, 6, 6, 0), Day(date(2026, 9, 20), 30)]        # používateľ si naplánoval budúcnosť
    new = fill_missing_days(days, ST, s)
    assert [(d.date, d.goal) for d in new] == [(ST, 12)]
    assert fill_missing_days([Day(date(2026, 9, 20), 30)], ST, s)[0].date == ST


def test_fill_missing_days_cez_vikend():
    """Sobota sa zamrazí, nedeľa je voľno (nikdy nie zamrazená) a pondelok má nové X."""
    s = nast()
    new = fill_missing_days([Day(PIA, 12, 6, 6)], PO2, s)
    assert [(d.date, d.goal, d.frozen) for d in new] == [(SO, 6, True), (NE, 0, False), (PO2, 14, False)]
    assert new[1].is_rest and new[1].status(PO2) == REST
    assert new[2].morning_target == 7 and new[2].evening_target == 7


def test_fill_missing_days_prazdna_db_v_nedelu():
    new = fill_missing_days([], NE, nast(frozen=True))
    assert (new[0].date, new[0].goal) == (NE, 0)
    assert new[0].is_rest and new[0].status(NE) == REST
    assert not new[0].frozen            # voľný deň netreba mraziť (rovnako ako v hlavnej vetve)


def test_replan_meni_dnesok_a_buducnost_nie_historiu():
    s = nast()
    dni = [Day(UT, 6, 6, 0), Day(ST, 12), Day(STV, 6), Day(SO, 10)]
    nove, monday = s.with_x(5, ST)
    assert monday == PO
    zmenene = replan(dni, nove, ST)
    assert [(d.date, d.goal) for d in zmenene] == [(ST, 10), (STV, 5), (SO, 5)]
    # včerajšok sa neprepisuje vôbec (sobota po novom X = 5, nie 10)
    assert all(d.date >= ST for d in zmenene)
    # keď ciele sedia s plánom, replan nevráti nič (netreba nič zapisovať)
    assert replan([Day(UT, 6, 6, 0), Day(ST, 12), Day(STV, 6)], s, ST) == []


# ── výzvy v chate ────────────────────────────────────────────────────────────

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


def test_nag_ticho_v_nedelu_a_v_utorok_vecer():
    s = nast(morning_time="07:00", evening_time="19:20")
    # nedeľa – ani ráno, ani večer
    assert _run_nags(s, Day(NE, 0), MORNING, [at(7, 0, NE), at(7, 30, NE)]) == []
    assert _run_nags(s, Day(NE, 0), EVENING, [at(19, 20, NE), at(19, 50, NE)]) == []
    assert plan(Snapshot(at(19, 20, NE), s, Day(NE, 0), {})) == []
    # utorok – ráno áno, večer nie
    ut = Day(UT, 6, morning=2)
    assert _run_nags(s, ut, MORNING, [at(7, 0, UT), at(7, 30, UT)]) == ["07:00", "07:30"]
    assert _run_nags(s, ut, EVENING, [at(19, 20, UT), at(19, 50, UT)]) == []
    assert [(a.kind, a.session) for a in plan(Snapshot(at(19, 20, UT), s, ut, {}))] == []


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


# ── texty ────────────────────────────────────────────────────────────────────

def test_report_reply_text():
    day = Day(D, 10, morning=4)
    txt = M.report_reply(day, {MORNING: 2, EVENING: 0}, False, 1, 12)
    assert "+2 ráno" in txt and "Ráno: 4/5" in txt and "Dnes: 4/10" in txt and "zostáva 6" in txt
    # splnený dnešok: gratulácia a rozpis na zajtra (streda → štvrtok je len ráno)
    done = M.report_reply(Day(D, 10, 5, 5), {MORNING: 0, EVENING: 5}, True, 3, 6)
    assert "🎉" in done and "Dnes: 10/10" in done and "Zajtra (štvrtok): 6 ráno" in done
    # dodatočne splnený deň hlási streak – po novom v týždňoch
    vcera = M.report_reply(Day(UT, 6, 6, 0), {MORNING: 4, EVENING: 0}, True, 3, 12, when="včera")
    assert "splnený dodatočne" in vcera and "Streak 3 týždňov" in vcera
    assert "Večer" not in vcera                  # utorok večernú fázu nemá


def test_total_report_sets_day_total():
    r = apply_reports(Day(D, 10, morning=3), [Report(EVENING, 10, absolute=True, total=True)])
    assert (r.day.morning, r.day.evening, r.completed_now) == (3, 7, True)
    r = apply_reports(Day(D, 10, morning=4), [Report(MORNING, -2)])
    assert r.day.morning == 2 and r.added[MORNING] == -2


def test_elapsed_is_dst_safe():
    # 25. 10. 2026 sa o 03:00 CEST vracia na 02:00 CET – 02:30 existuje dvakrát
    first = datetime(2026, 10, 25, 2, 30, tzinfo=TZ, fold=0)
    second = datetime(2026, 10, 25, 2, 30, tzinfo=TZ, fold=1)
    assert elapsed(second, first) == timedelta(hours=1)
    assert second - first == timedelta(0)            # naivné odčítanie by výzvy zaseklo
