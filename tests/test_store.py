from datetime import date, datetime

from trener.model import Day, NagState, ReminderState, Settings
from trener.store import Store


def test_days_settings_nags_reminders_round_trip():
    s = Store(":memory:")
    d = Day(date(2026, 9, 2), 10, 2, 0, note="x")
    s.save_day(d)
    assert s.get_day(d.date) == d
    assert s.days_with_synced() == [(d, None)]
    s.mark_day_synced(d)
    assert s.days_with_synced() == [(d, d)]
    st = Settings(frozen=True, morning_time="06:30", nag_max=2, morning_title="A {n}")
    s.save_settings(st)
    assert s.get_settings() == st
    assert s.get_settings_synced()["frozen"] is None
    s.mark_settings_synced(st)
    assert s.get_settings_synced()["frozen"] == "1"
    n = NagState(d.date, "morning", 2, datetime(2026, 9, 2, 7, 30))
    s.save_nag(n)
    assert s.get_nag(d.date, "morning") == n
    r = ReminderState(d.date, "evening", "h", "u", "e", "sum", "19:20", False, False, 0, 5, False)
    s.save_reminder(r)
    assert s.get_reminder(d.date, "evening") == r
    assert s.reminders_before(date(2026, 9, 3)) == [r]
    s.set_meta("k", "v")
    assert s.get_meta("k") == "v"
    s.set_meta("k", None)
    assert s.get_meta("k") is None


def test_config_rejects_unknown_backend(monkeypatch):
    import pytest
    from trener import config as C
    for k, v in {"BOT_TOKEN": "t", "OWNER_CHAT_ID": "1", "TABLE_BACKEND": "SMB", "SMB_USERNAME": "u", "SMB_PASSWORD": "p"}.items():
        monkeypatch.setenv(k, v)
    assert C.load().table_backend == "smb"           # veľkosť písmen sa normalizuje
    monkeypatch.setenv("TABLE_BACKEND", "samba")
    with pytest.raises(SystemExit):
        C.load()
