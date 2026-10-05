"""Tabuľka: round-trip, 3-cestný merge, vzorce, poškodený súbor, cudzie hárky."""
import io
from datetime import date, datetime, time as dtime

import pytest
from openpyxl import Workbook, load_workbook

from trener.model import Day, Settings
from trener.table import (DAY_HEADERS, SETTINGS_ROWS, SHEET_DAYS, SHEET_SETTINGS, RowVals,
                          TableCorrupt, cell_date, cell_int, merge, parse_workbook, render_workbook)

D = date(2026, 9, 2)          # streda – ráno aj večer (2X + 2X)
Y = date(2026, 9, 1)          # utorok – len ráno (2X)
NE = date(2026, 9, 6)         # nedeľa – voľno (cieľ 0)
# Settings() má x=8 od pondelka 7.9.2026 → pre týždeň 31.8. platí X = 7, streda = 28.
GOAL_D = Settings().goal_for(D)


def render(days, settings=Settings(), existing=None, today=D, parsed=None):
    streaks = {d.date: 0 for d in days}
    return render_workbook(existing, days, settings, streaks, today, parsed)


def _snap(s: Settings) -> dict:
    """Snímka nastavení tak, ako ju drží store (všetko ako text)."""
    return {k: ("1" if v is True else "0" if v is False else str(v)) for k, v in vars(s).items()}


def _row(d, goal=None, morning=None, evening=None, note="", status_text="", row=99) -> RowVals:
    """Riadok tak, ako by ho do tabuľky napísal používateľ."""
    return RowVals(d, goal, morning, evening, note, status_text, False, set(), {}, row, "")


def _old_table(data: bytes) -> bytes:
    """Tabuľka spred zavedenia plánu X: bez stĺpcov „Deň v týždni“ a „X (týždeň)“."""
    wb = load_workbook(io.BytesIO(data))
    wb[SHEET_DAYS].delete_cols(len(DAY_HEADERS) - 1, 2)
    buf = io.BytesIO(); wb.save(buf)
    return buf.getvalue()


def _set_setting(data: bytes, label: str, value) -> bytes:
    """Prepíše hodnotu v hárku Nastavenia tak, ako keby ju zmenil používateľ v Exceli."""
    wb = load_workbook(io.BytesIO(data))
    ws = wb[SHEET_SETTINGS]
    for r in range(2, ws.max_row + 1):
        if ws.cell(r, 1).value == label:
            ws.cell(r, 2, value)
            break
    else:
        raise AssertionError(f"v hárku Nastavenia nie je riadok {label!r}")
    buf = io.BytesIO(); wb.save(buf)
    return buf.getvalue()


def test_round_trip():
    days = [Day(Y, 10, 4, 6, note="pohoda"), Day(D, 12, 2, 0)]
    s = Settings(frozen=True, morning_time="06:30", evening_time="20:00", nag_max=2, nag_interval_min=45,
                 morning_title="Kliky ráno {n}!")
    p = parse_workbook(render(days, s))
    assert p.has_days_sheet and p.has_settings_sheet
    assert p.days[Y].goal == 10 and p.days[Y].morning == 4 and p.days[Y].evening == 6
    assert p.days[Y].note == "pohoda" and p.days[Y].status_text.startswith("✅")
    assert p.days[D].status_text.startswith("⏳")
    assert p.settings["frozen"] is True
    assert p.settings["morning_time"] == "06:30" and p.settings["evening_time"] == "20:00"
    assert p.settings["nag_max"] == 2 and p.settings["nag_interval_min"] == 45
    assert p.settings["morning_title"] == "Kliky ráno {n}!"


def test_cell_conversions():
    assert cell_int(5) == 5 and cell_int(5.0) == 5 and cell_int(" 5 ") == 5 and cell_int("5 klikov") == 5
    assert cell_int(None) is None and cell_int("") is None and cell_int("x") is None
    assert cell_date("2026-09-02") == D and cell_date("2.9.2026") == D and cell_date("02.09.2026") == D
    assert cell_date(datetime(2026, 9, 2, 10)) == D and cell_date("blabla") is None


def _synced(day):
    return [(day, day)]


def test_merge_user_edit_wins():
    bot = Day(D, 10, 0, 0)
    p = parse_workbook(render([bot]))
    p.days[D].morning = 5           # používateľ dopísal 5 do Ráno
    m = merge(p, _synced(bot), Settings(), {}, D)
    assert m.days[0].morning == 5 and D in m.changed_days
    assert any("ráno 0 → 5" in n for n in m.from_table)


def test_merge_bot_change_is_written():
    synced = Day(D, 10, 0, 0)
    bot = Day(D, 10, 2, 0)          # hlásenie z chatu
    p = parse_workbook(render([synced]))
    m = merge(p, [(bot, synced)], Settings(), {}, D)
    assert m.days[0].morning == 2 and m.to_table and not m.changed_days


def test_merge_both_changed_table_wins():
    synced = Day(D, 10, 0, 0)
    bot = Day(D, 10, 2, 0)
    p = parse_workbook(render([synced]))
    p.days[D].morning = 7
    m = merge(p, [(bot, synced)], Settings(), {}, D)
    assert m.days[0].morning == 7


def test_merge_new_row_from_table_and_missing_row_written():
    bot = Day(D, 10)
    p = parse_workbook(render([Day(date(2026, 8, 30), 8, 4, 4)]))
    m = merge(p, _synced(bot), Settings(), {}, D)
    assert [d.date for d in m.days] == [date(2026, 8, 30), D]
    assert m.days[0].total == 8 and m.to_table


def test_frozen_from_status_column():
    bot = Day(D, 10)
    p = parse_workbook(render([bot]))
    p.days[D].status_text = "zamrazený"
    p.days[D].frozen_by_user = True
    m = merge(p, _synced(bot), Settings(), {}, D)
    assert m.days[0].frozen


def test_settings_merge_table_wins_and_time_cell():
    bot = Day(D, 10)
    data = render([bot], Settings())
    wb = load_workbook(io.BytesIO(data))
    ws = wb[SHEET_SETTINGS]
    for r in range(2, ws.max_row + 1):
        if ws.cell(r, 1).value == "Zamrazené":
            ws.cell(r, 2, "ÁNO")
        if ws.cell(r, 1).value == "Ranný čas":
            ws.cell(r, 2, dtime(6, 45))       # Excel to uloží ako čas
    buf = io.BytesIO(); wb.save(buf)
    p = parse_workbook(buf.getvalue())
    synced = {"frozen": "0", "morning_time": "07:00"}
    m = merge(p, _synced(bot), Settings(), synced, D)
    assert m.settings.frozen is True and m.settings.morning_time == "06:45"
    assert set(m.changed_settings) == {"frozen", "morning_time"}


def test_settings_bot_change_written_when_table_unchanged():
    bot = Day(D, 10)
    p = parse_workbook(render([bot], Settings(frozen=False)))
    m = merge(p, _synced(bot), Settings(frozen=True), {"frozen": "0"}, D)
    assert m.settings.frozen is True and m.to_table and not m.changed_settings


def test_formula_cell_is_respected_and_kept():
    bot = Day(D, 10, 0, 0)
    data = render([bot])
    wb = load_workbook(io.BytesIO(data))
    ws = wb[SHEET_DAYS]
    ws.cell(2, 3, "=2+3")            # Ráno ako vzorec
    buf = io.BytesIO(); wb.save(buf)
    p = parse_workbook(buf.getvalue())
    assert "morning" in p.days[D].formula_cells
    m = merge(p, _synced(bot), Settings(), {}, D)
    # openpyxl nemá cache hodnôt vzorca → hodnota None → nič sa nemení, ale ani neprepíše
    assert m.days[0].morning == 0


def test_corrupt_file_raises_and_is_never_overwritten():
    with pytest.raises(TableCorrupt):
        parse_workbook(b"toto nie je xlsx")
    with pytest.raises(TableCorrupt):
        parse_workbook(render([Day(D, 10)])[:500])


def test_render_preserves_foreign_sheet_extra_columns_and_foreign_rows():
    wb = Workbook(); ws = wb.active; ws.title = "Moje"; ws["A1"] = "moje veci"
    buf = io.BytesIO(); wb.save(buf)
    data = render([Day(D, 10)], existing=buf.getvalue())
    # používateľ pridá vlastný stĺpec, komentár-riadok s nečitateľným dátumom a vzorec
    wb2 = load_workbook(io.BytesIO(data)); ws2 = wb2[SHEET_DAYS]
    ws2.cell(1, 9, "Nálada"); ws2.cell(2, 9, "super")
    ws2.cell(3, 1, "poznámka pod tabuľkou"); ws2.cell(3, 8, "toto nie je deň")
    buf2 = io.BytesIO(); wb2.save(buf2)
    p = parse_workbook(buf2.getvalue())
    assert p.days[D].extra == {9: "super"} and p.foreign_rows == 1 and p.warnings
    data3 = render([Day(D, 10, 3, 0), Day(Y, 8, 4, 4)], existing=buf2.getvalue(), parsed=p)
    wb3 = load_workbook(io.BytesIO(data3)); ws3 = wb3[SHEET_DAYS]
    assert "Moje" in wb3.sheetnames and wb3["Moje"]["A1"].value == "moje veci"
    assert ws3.cell(2, 9).value == "super" and ws3.cell(2, 3).value == 3        # extra stĺpec ostal, Ráno = 3
    assert ws3.cell(3, 1).value == "poznámka pod tabuľkou"                      # cudzí riadok nedotknutý
    assert cell_date(ws3.cell(4, 1).value) == Y                                # nový deň dopísaný na koniec


def test_user_moved_and_inserted_columns_are_found_by_header():
    data = render([Day(D, 10, 2, 0)])
    wb = load_workbook(io.BytesIO(data)); ws = wb[SHEET_DAYS]
    ws.insert_cols(3)                       # nový stĺpec medzi Cieľ a Ráno
    ws.cell(1, 3, "Váha"); ws.cell(2, 3, 81.5)
    buf = io.BytesIO(); wb.save(buf)
    p = parse_workbook(buf.getvalue())
    assert p.layout["morning"] == 4 and p.days[D].morning == 2 and p.days[D].goal == 10
    data2 = render([Day(D, 10, 5, 0)], existing=buf.getvalue(), parsed=p)
    ws2 = load_workbook(io.BytesIO(data2))[SHEET_DAYS]
    assert ws2.cell(2, 3).value == 81.5 and ws2.cell(2, 4).value == 5 and ws2.cell(1, 4).value == "Ráno"


def test_formula_cell_is_never_overwritten_on_render():
    data = render([Day(D, 10, 0, 0)])
    wb = load_workbook(io.BytesIO(data)); wb[SHEET_DAYS].cell(2, 3, "=2+3")
    buf = io.BytesIO(); wb.save(buf)
    p = parse_workbook(buf.getvalue())
    data2 = render([Day(D, 10, 5, 0)], existing=buf.getvalue(), parsed=p)
    assert load_workbook(io.BytesIO(data2))[SHEET_DAYS].cell(2, 3).value == "=2+3"


def test_deleted_row_is_deleted_from_bot_but_not_when_table_emptied():
    a, b = Day(Y, 10, 4, 6), Day(D, 12, 0, 0)
    p = parse_workbook(render([a, b]))
    del p.days[Y]                                     # používateľ zmazal včerajšok
    m = merge(p, [(a, a), (b, b)], Settings(), {}, D)
    assert m.deleted_days == [Y] and [d.date for d in m.days] == [D]
    p2 = parse_workbook(render([a, b]))
    p2.days.clear()                                   # tabuľka náhle prázdna → nič nemažeme, dopíšeme
    m2 = merge(p2, [(a, a), (b, b)], Settings(), {}, D)
    assert m2.deleted_days == [] and len(m2.days) == 2 and m2.to_table
    # dnešok sa nikdy nemaže
    p3 = parse_workbook(render([a, b])); del p3.days[D]
    assert merge(p3, [(a, a), (b, b)], Settings(), {}, D).deleted_days == []


def test_new_row_without_goal_takes_goal_from_plan():
    """Prázdny Cieľ v novom riadku sa doplní z PLÁNU (X pre ten týždeň), nie z predošlého dňa."""
    a = Day(Y, 12, 6, 6)                       # utorok s ručne nastaveným cieľom 12
    p = parse_workbook(render([a]))
    p.days[D] = _row(D, goal=None, morning=3, row=3)
    m = merge(p, [(a, a)], Settings(), {}, D)
    assert m.days[1].goal == GOAL_D == 14 and m.days[1].goal != a.goal
    assert m.days[1].status(D) == "open"
    # a nedeľný riadok bez cieľa vyjde z plánu ako voľno, nie ako kópia stredy
    p2 = parse_workbook(render([a]))
    p2.days[NE] = _row(NE, goal=None, row=3)
    m2 = merge(p2, [(a, a)], Settings(), {}, D)
    assert m2.days[1].goal == 0 and m2.days[1].is_rest


def test_note_starting_with_equals_is_text_and_dv_not_duplicated():
    data = render([Day(D, 10, note="=fajn")])
    wb = load_workbook(io.BytesIO(data))
    assert wb[SHEET_DAYS].cell(2, 8).value == "=fajn" and wb[SHEET_DAYS].cell(2, 8).data_type == "s"
    p = parse_workbook(data)
    assert p.days[D].note == "=fajn"
    for _ in range(3):
        data = render([Day(D, 10)], existing=data, parsed=parse_workbook(data))
    assert len(load_workbook(io.BytesIO(data))[SHEET_SETTINGS].data_validations.dataValidation) == 1


def test_user_edit_on_done_day_refreshes_total():
    a = Day(Y, 10, 4, 6)
    p = parse_workbook(render([a]))
    p.days[Y].morning = 5                              # ✅ ostáva, ale Spolu musí byť 11
    m = merge(p, [(a, a)], Settings(), {}, D)
    assert m.days[0].total == 11 and m.to_table


def test_no_write_when_nothing_changed():
    day = Day(Y, 10, 4, 6)
    s = Settings()
    synced_settings = {k: ("1" if v is True else "0" if v is False else str(v))
                       for k, v in vars(s).items()}
    data = render([day], s)
    p = parse_workbook(data)
    m = merge(p, _synced(day), s, synced_settings, D)
    assert not m.to_table and not m.changed_days and not m.changed_settings


def test_rows_sorted_and_status_recomputed():
    days = [Day(D, 12, 0, 0), Day(Y, 10, 4, 6), Day(date(2026, 8, 31), 10, 0, 0)]
    data = render(days)
    wb = load_workbook(io.BytesIO(data))
    ws = wb[SHEET_DAYS]
    assert [ws.cell(r, 1).value.day for r in (2, 3, 4)] == [31, 1, 2]
    assert ws.cell(2, 6).value.startswith("❌") and ws.cell(3, 6).value.startswith("✅")


def test_unfreeze_by_bot_is_not_undone_by_own_status_text():
    """/odmraz: bot deň odmrazí, v tabuľke ešte svieti náš „❄️ zamrazený“ → nesmie sa znovu zamraziť."""
    frozen_day = Day(D, 12, 0, 0, frozen=True)
    p = parse_workbook(render([frozen_day]))          # Stav = ❄️ zamrazený (náš zápis)
    assert p.days[D].frozen_by_user
    bot_now = Day(D, 12, 0, 0, frozen=False)          # bot odmrazil
    m = merge(p, [(bot_now, frozen_day)], Settings(), {}, D)
    assert m.days[0].frozen is False and m.to_table
    # ale ak používateľ napísal „zamrazený“ do dňa, ktorý sme mali ako nezamrazený → prevezmeme
    open_day = Day(D, 12, 0, 0)
    p2 = parse_workbook(render([open_day]))
    p2.days[D].status_text = "zamrazený"; p2.days[D].frozen_by_user = True
    m2 = merge(p2, [(open_day, open_day)], Settings(), {}, D)
    assert m2.days[0].frozen is True


def test_empty_goal_cell_keeps_bot_goal():
    bot = Day(D, 12, 2, 0)
    p = parse_workbook(render([bot]))
    p.days[D].goal = None                          # používateľ omylom zmazal Cieľ
    m = merge(p, [(bot, bot)], Settings(), {}, D)
    assert m.days[0].goal == 12 and m.to_table
    # ale prázdne Ráno/Večer = 0 (zmazal hodnotu naschvál)
    p2 = parse_workbook(render([bot]))
    p2.days[D].morning = None
    m2 = merge(p2, [(bot, bot)], Settings(), {}, D)
    assert m2.days[0].morning == 0


def test_no_write_churn_with_zero_totals_today():
    day = Day(D, 12, 0, 0)
    s = Settings()
    synced_settings = {k: ("1" if v is True else "0" if v is False else str(v)) for k, v in vars(s).items()}
    data = render([day], s)
    p = parse_workbook(data)
    m = merge(p, [(day, day)], s, synced_settings, D)
    assert not m.to_table, m


# ── nový model plánu: voľno, stĺpce Deň/X, X v Nastaveniach ──────────────────

def test_rest_day_row_renders_with_volno_status():
    """Nedeľa (cieľ 0) sa vykreslí ako „🌙 voľno“ – kedysi tu STATUS_FILL padol na KeyError."""
    data = render([Day(NE, 0, 0, 0)])
    ws = load_workbook(io.BytesIO(data))[SHEET_DAYS]
    assert ws.cell(2, 6).value == "🌙 voľno"
    assert ws.cell(2, 6).fill.fgColor.rgb.endswith("EDEDED")          # vlastná farba voľna
    assert ws.cell(2, 2).value == 0 and ws.cell(2, 5).value == 0
    assert ws.cell(2, 9).value == "nedeľa" and ws.cell(2, 10).value == 7
    p = parse_workbook(data)
    assert p.days[NE].goal == 0 and p.days[NE].status_text == "🌙 voľno"
    assert not p.days[NE].frozen_by_user                              # „voľno“ nie je „zamrazený“


def test_weekday_and_x_columns_show_the_plan():
    """Stĺpce Deň v týždni a X (týždeň) sedia na plán: X+X v stredu, X v utorok, 0 v nedeľu."""
    days = [Day(Y, 7, 0, 0), Day(D, 14, 0, 0), Day(NE, 0, 0, 0)]
    ws = load_workbook(io.BytesIO(render(days)))[SHEET_DAYS]
    assert [ws.cell(r, 9).value for r in (2, 3, 4)] == ["utorok", "streda", "nedeľa"]
    assert [ws.cell(r, 10).value for r in (2, 3, 4)] == [7, 7, 7]
    assert [ws.cell(r, 2).value for r in (2, 3, 4)] == [7, 14, 0]


def test_missing_weekday_and_x_columns_are_added_to_old_table():
    """Stará tabuľka bez nových stĺpcov: merge si vypýta zápis a render ich dopíše na koniec."""
    bot = Day(D, 28, 2, 0)
    s = Settings()
    old = _old_table(render([bot], s))
    p = parse_workbook(old)
    assert "weekday" not in p.layout and "x" not in p.layout and not p.warnings
    m = merge(p, _synced(bot), s, _snap(s), D)
    # nič iné sa nezmenilo – zápis je vynútený práve chýbajúcimi stĺpcami
    assert m.to_table and not m.changed_days and not m.changed_settings and not m.from_table
    ws = load_workbook(io.BytesIO(render([bot], s, existing=old, parsed=p)))[SHEET_DAYS]
    assert [ws.cell(1, c).value for c in (9, 10)] == ["Deň v týždni", "X (týždeň)"]
    assert ws.cell(2, 9).value == "streda" and ws.cell(2, 10).value == 7
    assert ws.cell(2, 3).value == 2                                   # pôvodné dáta ostali na mieste


def test_x_change_in_settings_sheet_wins_and_is_not_pushed_back():
    """Zmena X v hárku vyhráva nad botom a bot svoje staré X neposiela späť do tabuľky."""
    bot = Day(D, 28, 0, 0)
    s = Settings()
    p = parse_workbook(_set_setting(render([bot], s), "X (základ plánu)", 5))
    assert p.settings["x"] == 5
    m = merge(p, _synced(bot), s, _snap(s), D)
    assert m.settings.x == 5 and m.changed_settings == ["x"]
    assert any("X (základ plánu): 8 → 5" in n for n in m.from_table)
    assert m.settings.goal_for(D) == 8                                # nový plán: 4 + 4
    # Zápis späť do hárku JE potrebný, ale nie kvôli X v Nastaveniach (to vyhralo),
    # lež kvôli stĺpcu „X (týždeň)“, ktorý po zmene plánu ukazuje staré číslo.
    assert m.to_table
    assert _set_setting(render(m.days, m.settings), "X (základ plánu)", 5) is not None
    znovu = parse_workbook(render(m.days, m.settings))
    assert znovu.settings["x"] == 5                                   # bot nevrátil svoju 8


def test_x_zero_in_sheet_is_rejected_and_bot_value_restored():
    """X = 0 (ani záporné) sa neprevezme – celý plán by sa zmenil na voľno."""
    bot = Day(D, 28, 0, 0)
    s = Settings()
    for bad in (0, -3):
        p = parse_workbook(_set_setting(render([bot], s), "X (základ plánu)", bad))
        assert "x" not in p.settings and any("X (základ plánu)" in w for w in p.warnings)
        m = merge(p, _synced(bot), s, _snap(s), D)
        assert m.settings.x == 8 and not m.changed_settings
        assert m.to_table                                             # botovo X sa vráti do hárku
    # X = 1 je ešte platné
    p1 = parse_workbook(_set_setting(render([bot], s), "X (základ plánu)", 1))
    assert p1.settings["x"] == 1 and not p1.warnings


def test_x_since_is_snapped_to_monday():
    """„X platí od“ zadané na hocijaký deň sa zarovná na pondelok jeho týždňa."""
    bot = Day(D, 28, 0, 0)
    s = Settings()
    p = parse_workbook(_set_setting(render([bot], s), "X platí od (pondelok)", "2026-09-02"))
    assert p.settings["x_since"] == "2026-08-31"
    m = merge(p, _synced(bot), s, _snap(s), D)
    assert m.settings.x_since == "2026-08-31" and m.changed_settings == ["x_since"]
    assert m.settings.x_for(D) == 8 and m.settings.goal_for(D) == 16   # X 8 už platí pre tento týždeň
    # nezmyselný dátum sa odmietne a bot si nechá svoj
    p2 = parse_workbook(_set_setting(render([bot], s), "X platí od (pondelok)", "blabla"))
    assert "x_since" not in p2.settings and p2.warnings
    assert merge(p2, _synced(bot), s, _snap(s), D).settings.x_since == s.x_since


def test_user_zero_goal_is_respected_as_rest_day():
    """Nula napísaná používateľom je poctivé voľno – nesmie sa „opraviť“ podľa plánu."""
    bot = Day(D, 28, 0, 0)
    p = parse_workbook(render([bot]))
    p.days[D].goal = 0                                  # dnes si dávam voľno
    m = merge(p, _synced(bot), Settings(), {}, D)
    assert m.days[0].goal == 0 and m.days[0].is_rest and m.days[0].status(D) == "rest"
    assert any("cieľ 28 → 0" in n for n in m.from_table)
    # to isté pri úplne novom riadku (nedeľa, ktorú bot ešte nepozná)
    p2 = parse_workbook(render([bot]))
    p2.days[NE] = _row(NE, goal=0, row=3)
    m2 = merge(p2, _synced(bot), Settings(), {}, D)
    assert [(d.date, d.goal) for d in m2.days] == [(D, 28), (NE, 0)]


def test_frozen_rest_day_is_not_unfrozen_by_its_own_volno_status():
    """Zamrazená nedeľa: v Stave svieti „🌙 voľno“ (náš zápis) – to nie je odmrazenie od používateľa."""
    sunday = Day(NE, 0, 0, 0, frozen=True)
    p = parse_workbook(render([sunday]))
    assert p.days[NE].status_text == "🌙 voľno" and not p.days[NE].frozen_by_user
    m = merge(p, _synced(sunday), Settings(), {}, D)
    assert m.days[0].frozen is True
    assert not m.to_table and not m.changed_days                      # ani žiadne zbytočné prepisovanie
    # kontrast: zamrazený tréningový deň má v Stave „❄️ zamrazený“ a ten sa prečíta späť
    training = Day(D, 28, 0, 0, frozen=True)
    p2 = parse_workbook(render([training]))
    assert p2.days[D].status_text.startswith("❄️") and p2.days[D].frozen_by_user


def test_user_column_named_x_is_not_adopted_by_bot():
    """Používateľov stĺpec „X“ nie je náš „X (týždeň)“ – bot si ho nesmie privlastniť."""
    bot = Day(D, 28, 2, 0)
    s = Settings()
    old = _old_table(render([bot], s))
    wb = load_workbook(io.BytesIO(old)); ws = wb[SHEET_DAYS]
    ws.cell(1, 9, "X"); ws.cell(2, 9, "moje X")
    buf = io.BytesIO(); wb.save(buf)
    p = parse_workbook(buf.getvalue())
    assert "x" not in p.layout and p.days[D].extra == {9: "moje X"}
    ws2 = load_workbook(io.BytesIO(render([bot], s, existing=buf.getvalue(), parsed=p)))[SHEET_DAYS]
    assert ws2.cell(1, 9).value == "X" and ws2.cell(2, 9).value == "moje X"
    assert [ws2.cell(1, c).value for c in (10, 11)] == ["Deň v týždni", "X (týždeň)"]
    assert ws2.cell(2, 10).value == "streda" and ws2.cell(2, 11).value == 7


def test_foreign_columns_rows_and_formulas_survive_added_columns():
    """Doplnenie nových stĺpcov nesmie rozhádzať cudzie stĺpce, cudzie riadky ani vzorce."""
    bot = Day(D, 28, 0, 0)
    s = Settings()
    old = _old_table(render([bot], s))
    wb = load_workbook(io.BytesIO(old)); ws = wb[SHEET_DAYS]
    ws.cell(2, 3, "=2+3")                                    # vzorec v Ráno
    ws.cell(1, 9, "Nálada"); ws.cell(2, 9, "super")          # vlastný stĺpec
    ws.cell(3, 1, "poznámka pod tabuľkou")                   # cudzí riadok
    buf = io.BytesIO(); wb.save(buf)
    p = parse_workbook(buf.getvalue())
    assert "morning" in p.days[D].formula_cells and p.foreign_rows == 1
    data = render([Day(D, 28, 5, 0), Day(NE, 0)], s, existing=buf.getvalue(), parsed=p)
    ws2 = load_workbook(io.BytesIO(data))[SHEET_DAYS]
    assert ws2.cell(2, 3).value == "=2+3"                    # vzorec nikdy neprepisujeme
    assert ws2.cell(2, 9).value == "super" and ws2.cell(1, 9).value == "Nálada"
    assert ws2.cell(3, 1).value == "poznámka pod tabuľkou"
    assert [ws2.cell(1, c).value for c in (10, 11)] == ["Deň v týždni", "X (týždeň)"]
    assert cell_date(ws2.cell(4, 1).value) == NE and ws2.cell(4, 6).value == "🌙 voľno"
    assert ws2.cell(4, 10).value == "nedeľa" and ws2.cell(4, 11).value == 7


def test_obsolete_increment_row_is_replaced_by_x_rows():
    """Migrácia starého hárku: „Prírastok cieľa“ zmizne, pribudnú tri riadky o X."""
    bot = Day(D, 28, 0, 0)
    s = Settings()
    wb = load_workbook(io.BytesIO(render([bot], s)))
    wss = wb[SHEET_SETTINGS]
    wss.delete_rows(3, 3)                                    # staré hárky riadky o X nemali
    wss.insert_rows(3)
    wss.cell(3, 1, "Prírastok cieľa"); wss.cell(3, 2, 2)
    buf = io.BytesIO(); wb.save(buf)
    p = parse_workbook(buf.getvalue())
    assert "x" not in p.settings and not p.warnings           # neznámy riadok ticho ignorujeme
    m = merge(p, _synced(bot), s, _snap(s), D)
    assert m.to_table and m.settings.x == 8 and not m.changed_settings
    wss2 = load_workbook(io.BytesIO(render([bot], s, existing=buf.getvalue(), parsed=p)))[SHEET_SETTINGS]
    vals = {wss2.cell(r, 1).value: wss2.cell(r, 2).value for r in range(2, wss2.max_row + 1)}
    assert "Prírastok cieľa" not in vals
    assert vals["X (základ plánu)"] == 8 and vals["X platí od (pondelok)"] == "2026-09-07"
    assert vals["Rast X za týždeň"] == 1


def test_headers_and_settings_rows_follow_the_x_model():
    """Nové stĺpce sa pridávajú NA KONIEC a nastavenia poznajú X namiesto prírastku."""
    assert DAY_HEADERS[:8] == ["Dátum", "Cieľ", "Ráno", "Večer", "Spolu", "Stav", "Streak", "Poznámka"]
    assert DAY_HEADERS[8:] == ["Deň v týždni", "X (týždeň)"]
    keys = [k for k, _, _ in SETTINGS_ROWS]
    assert "increment" not in keys and {"x", "x_since", "x_step"} <= set(keys)
    assert not hasattr(Settings(), "increment")
