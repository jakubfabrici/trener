"""Príkazy s diakritikou a aliasmi sa nikdy nesmú zarátať ako kliky."""
from trener.telegram_ui import resolve_command


def test_commands_with_diacritics_are_commands():
    assert resolve_command("/cieľ 10") == ("ciel", ["10"])
    assert resolve_command("/Cieľ 10") == ("ciel", ["10"])
    assert resolve_command("/ciel@Fabrici_pushup_bot 12") == ("ciel", ["12"])
    assert resolve_command("/ráno 7:00") == ("rano", ["7:00"])
    assert resolve_command("/večer 19:20") == ("vecer", ["19:20"])
    assert resolve_command("/prírastok 0") == ("prirastok", ["0"])
    assert resolve_command("/oprav ráno 5") == ("oprav", ["ráno", "5"])
    assert resolve_command("/pauza") == ("zmraz", [])
    assert resolve_command("/štatistika") == ("stats", [])
    assert resolve_command("/blabla 5") == ("?", ["blabla"])


def test_plain_numbers_are_not_commands():
    assert resolve_command("10") is None
    assert resolve_command("2 ráno") is None
    assert resolve_command("") is None
