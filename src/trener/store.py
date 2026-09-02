"""Lokálny stav v SQLite: cache tabuľky + snímka posledného syncu (3-cestný merge),
stav výziev, stav pripomienok, metadáta. Tabuľka na OMV je zdroj pravdy pre dni
a nastavenia; táto DB drží to, čo v tabuľke nie je (kedy odišla výzva, ETag pripomienky …).
"""
from __future__ import annotations

import sqlite3
from dataclasses import fields
from datetime import date, datetime
from pathlib import Path

from trener.model import Day, NagState, ReminderState, Settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS days (
    date TEXT PRIMARY KEY,
    goal INTEGER NOT NULL, morning INTEGER NOT NULL DEFAULT 0, evening INTEGER NOT NULL DEFAULT 0,
    frozen INTEGER NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '',
    s_goal INTEGER, s_morning INTEGER, s_evening INTEGER, s_frozen INTEGER, s_note TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY, value TEXT NOT NULL, s_value TEXT
);
CREATE TABLE IF NOT EXISTS nags (
    date TEXT NOT NULL, session TEXT NOT NULL, sent INTEGER NOT NULL DEFAULT 0, last_at TEXT,
    PRIMARY KEY (date, session)
);
CREATE TABLE IF NOT EXISTS reminders (
    date TEXT NOT NULL, session TEXT NOT NULL, href TEXT NOT NULL DEFAULT '', uid TEXT NOT NULL DEFAULT '',
    etag TEXT NOT NULL DEFAULT '', last_summary TEXT NOT NULL DEFAULT '', last_due_hhmm TEXT NOT NULL DEFAULT '',
    completed INTEGER NOT NULL DEFAULT 0, user_deleted INTEGER NOT NULL DEFAULT 0,
    missing_seen INTEGER NOT NULL DEFAULT 0,
    last_n INTEGER NOT NULL DEFAULT 0, user_unticked INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (date, session)
);
CREATE TABLE IF NOT EXISTS events (
    date TEXT NOT NULL, session TEXT NOT NULL, href TEXT NOT NULL DEFAULT '', uid TEXT NOT NULL DEFAULT '',
    etag TEXT NOT NULL DEFAULT '', last_summary TEXT NOT NULL DEFAULT '', last_due_hhmm TEXT NOT NULL DEFAULT '',
    completed INTEGER NOT NULL DEFAULT 0, user_deleted INTEGER NOT NULL DEFAULT 0,
    missing_seen INTEGER NOT NULL DEFAULT 0,
    last_n INTEGER NOT NULL DEFAULT 0, user_unticked INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (date, session)
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

SETTINGS_TYPES = {f.name: f.type for f in fields(Settings)}


def _t(table: str) -> str:
    """Povolené sú len naše dve tabuľky – žiadny vstup zvonku sa sem nedostane."""
    if table not in ("reminders", "events"):
        raise ValueError(table)
    return table


def _to_str(v) -> str:
    if isinstance(v, bool):
        return "1" if v else "0"
    return str(v)


def _from_str(name: str, s: str):
    t = SETTINGS_TYPES[name]
    if t in ("bool", bool):
        return s in ("1", "true", "True")
    if t in ("int", int):
        return int(s)
    return s


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.c = sqlite3.connect(self.path, check_same_thread=False)
        self.c.row_factory = sqlite3.Row
        self.c.execute("PRAGMA journal_mode=WAL")
        self.c.executescript(SCHEMA)
        self.c.commit()

    def close(self) -> None:
        self.c.close()

    # ── dni ─────────────────────────────────────────────────────────────────
    @staticmethod
    def _row_day(r: sqlite3.Row) -> Day:
        return Day(date.fromisoformat(r["date"]), r["goal"], r["morning"], r["evening"],
                   bool(r["frozen"]), r["note"] or "")

    @staticmethod
    def _row_synced(r: sqlite3.Row) -> Day | None:
        if r["s_goal"] is None:
            return None
        return Day(date.fromisoformat(r["date"]), r["s_goal"], r["s_morning"] or 0,
                   r["s_evening"] or 0, bool(r["s_frozen"]), r["s_note"] or "")

    def get_day(self, d: date) -> Day | None:
        r = self.c.execute("SELECT * FROM days WHERE date=?", (d.isoformat(),)).fetchone()
        return self._row_day(r) if r else None

    def all_days(self) -> list[Day]:
        return [self._row_day(r) for r in self.c.execute("SELECT * FROM days ORDER BY date")]

    def days_with_synced(self) -> list[tuple[Day, Day | None]]:
        return [(self._row_day(r), self._row_synced(r))
                for r in self.c.execute("SELECT * FROM days ORDER BY date")]

    def save_day(self, day: Day, synced: bool = False) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        self.c.execute(
            "INSERT INTO days (date, goal, morning, evening, frozen, note, updated_at)"
            " VALUES (?,?,?,?,?,?,?)"
            " ON CONFLICT(date) DO UPDATE SET goal=excluded.goal, morning=excluded.morning,"
            " evening=excluded.evening, frozen=excluded.frozen, note=excluded.note,"
            " updated_at=excluded.updated_at",
            (day.date.isoformat(), day.goal, day.morning, day.evening, int(day.frozen),
             day.note or "", now))
        if synced:
            self.mark_day_synced(day)
        self.c.commit()

    def mark_day_synced(self, day: Day) -> None:
        self.c.execute(
            "UPDATE days SET s_goal=?, s_morning=?, s_evening=?, s_frozen=?, s_note=? WHERE date=?",
            (day.goal, day.morning, day.evening, int(day.frozen), day.note or "",
             day.date.isoformat()))
        self.c.commit()

    def delete_day(self, d: date) -> None:
        self.c.execute("DELETE FROM days WHERE date=?", (d.isoformat(),))
        self.c.commit()

    # ── nastavenia ──────────────────────────────────────────────────────────
    def get_settings(self) -> Settings:
        s = Settings()
        for r in self.c.execute("SELECT key, value FROM settings"):
            if r["key"] in SETTINGS_TYPES:
                try:
                    setattr(s, r["key"], _from_str(r["key"], r["value"]))
                except ValueError:
                    pass
        return s

    def get_settings_synced(self) -> dict[str, str | None]:
        return {r["key"]: r["s_value"] for r in self.c.execute("SELECT key, s_value FROM settings")}

    def save_settings(self, s: Settings, synced: bool = False) -> None:
        for name in SETTINGS_TYPES:
            v = _to_str(getattr(s, name))
            if synced:
                self.c.execute("INSERT INTO settings (key, value, s_value) VALUES (?,?,?)"
                               " ON CONFLICT(key) DO UPDATE SET value=excluded.value, s_value=excluded.s_value",
                               (name, v, v))
            else:
                self.c.execute("INSERT INTO settings (key, value) VALUES (?,?)"
                               " ON CONFLICT(key) DO UPDATE SET value=excluded.value", (name, v))
        self.c.commit()

    def mark_settings_synced(self, s: Settings) -> None:
        self.save_settings(s, synced=True)

    # ── výzvy ───────────────────────────────────────────────────────────────
    def get_nag(self, d: date, session: str) -> NagState:
        r = self.c.execute("SELECT * FROM nags WHERE date=? AND session=?",
                           (d.isoformat(), session)).fetchone()
        if not r:
            return NagState(d, session)
        last = datetime.fromisoformat(r["last_at"]) if r["last_at"] else None
        return NagState(d, session, r["sent"], last)

    def save_nag(self, n: NagState) -> None:
        self.c.execute("INSERT INTO nags (date, session, sent, last_at) VALUES (?,?,?,?)"
                       " ON CONFLICT(date, session) DO UPDATE SET sent=excluded.sent, last_at=excluded.last_at",
                       (n.date.isoformat(), n.session, n.sent,
                        n.last_at.isoformat() if n.last_at else None))
        self.c.commit()

    # ── pripomienky (a rovnakým spôsobom kalendárové eventy) ────────────────
    def get_reminder(self, d: date, session: str, table: str = "reminders") -> ReminderState | None:
        r = self.c.execute(f"SELECT * FROM {_t(table)} WHERE date=? AND session=?",
                           (d.isoformat(), session)).fetchone()
        if not r:
            return None
        return ReminderState(d, session, r["href"], r["uid"], r["etag"], r["last_summary"],
                             r["last_due_hhmm"], bool(r["completed"]), bool(r["user_deleted"]),
                             r["missing_seen"], r["last_n"], bool(r["user_unticked"]))

    def reminders_before(self, d: date, table: str = "reminders") -> list[ReminderState]:
        out = []
        for r in self.c.execute(f"SELECT * FROM {_t(table)} WHERE date<? ORDER BY date", (d.isoformat(),)):
            out.append(ReminderState(date.fromisoformat(r["date"]), r["session"], r["href"], r["uid"],
                                     r["etag"], r["last_summary"], r["last_due_hhmm"],
                                     bool(r["completed"]), bool(r["user_deleted"]), r["missing_seen"],
                                     r["last_n"], bool(r["user_unticked"])))
        return out

    def save_reminder(self, s: ReminderState, table: str = "reminders") -> None:
        self.c.execute(
            f"INSERT INTO {_t(table)} (date, session, href, uid, etag, last_summary, last_due_hhmm,"
            " completed, user_deleted, missing_seen, last_n, user_unticked) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(date, session) DO UPDATE SET href=excluded.href, uid=excluded.uid,"
            " etag=excluded.etag, last_summary=excluded.last_summary, last_due_hhmm=excluded.last_due_hhmm,"
            " completed=excluded.completed, user_deleted=excluded.user_deleted,"
            " missing_seen=excluded.missing_seen, last_n=excluded.last_n, user_unticked=excluded.user_unticked",
            (s.date.isoformat(), s.session, s.href, s.uid, s.etag, s.last_summary, s.last_due_hhmm,
             int(s.completed), int(s.user_deleted), s.missing_seen, s.last_n, int(s.user_unticked)))
        self.c.commit()

    def delete_reminder(self, d: date, session: str, table: str = "reminders") -> None:
        self.c.execute(f"DELETE FROM {_t(table)} WHERE date=? AND session=?", (d.isoformat(), session))
        self.c.commit()

    # ── meta ────────────────────────────────────────────────────────────────
    def get_meta(self, key: str, default: str | None = None) -> str | None:
        r = self.c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

    def set_meta(self, key: str, value: str | None) -> None:
        if value is None:
            self.c.execute("DELETE FROM meta WHERE key=?", (key,))
        else:
            self.c.execute("INSERT INTO meta (key, value) VALUES (?,?)"
                           " ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        self.c.commit()
