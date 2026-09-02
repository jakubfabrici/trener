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


def render(days, settings=Settings(), existing=None, today=D, extras=None):
    streaks = {d.date: 0 for d in days}
    return render_workbook(existing, days, settings, streaks, today, extras)


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


def test_render_preserves_foreign_sheet_and_extra_columns():
    wb = Workbook(); ws = wb.active; ws.title = "Moje"; ws["A1"] = "moje veci"
    buf = io.BytesIO(); wb.save(buf)
    data = render([Day(D, 10)], existing=buf.getvalue(), extras={D: {9: "extra"}})
    wb2 = load_workbook(io.BytesIO(data))
    assert "Moje" in wb2.sheetnames and wb2["Moje"]["A1"].value == "moje veci"
    assert wb2[SHEET_DAYS].cell(2, 9).value == "extra"
    p = parse_workbook(data)
    assert p.days[D].extra == {9: "extra"}


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
