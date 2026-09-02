"""Tabuľka: round-trip, 3-cestný merge, vzorce, poškodený súbor, cudzie hárky."""
import io
from datetime import date, datetime, time as dtime

import pytest
from openpyxl import Workbook, load_workbook

from trener.model import Day, Settings
from trener.table import (SHEET_DAYS, SHEET_SETTINGS, TableCorrupt, cell_date, cell_int, merge,
                          parse_workbook, render_workbook)

D = date(2026, 9, 2)
Y = date(2026, 9, 1)


def render(days, settings=Settings(), existing=None, today=D, parsed=None):
    streaks = {d.date: 0 for d in days}
    return render_workbook(existing, days, settings, streaks, today, parsed)


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


def test_new_row_without_goal_inherits_previous_goal():
    a = Day(Y, 12, 6, 6)
    p = parse_workbook(render([a]))
    p.days[D] = type(p.days[Y])(D, None, 3, 0, "", "", False, set(), {}, 3, "")
    m = merge(p, [(a, a)], Settings(), {}, D, default_goal=10)
    assert m.days[1].goal == 12 and m.days[1].status(D) == "open"


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
