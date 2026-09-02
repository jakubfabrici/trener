"""Virtuálny tréner klikov v2 – hlavná slučka a spojenie všetkých častí.

Architektúra:
  • tabuľka (xlsx na OMV) = zdroj pravdy pre dni a nastavenia, číta sa každé 2 min,
  • Pripomienky (CalDAV/Radicale) = kanál upozornení, bot ich vytvára/odčiarkuje a učí sa z úprav,
  • Telegram = hlásenia klikov + príkazy + obmedzené výzvy (max N× po M min, na fázu),
  • tick každých 30 s: prechod dňa → sync tabuľky → sync pripomienok → oznámenie splnenia → výzvy.
Všetko je idempotentné a stav je v SQLite – reštart kedykoľvek nič nepokazí.
"""
from __future__ import annotations

import asyncio
import logging
import logging.handlers
import sys
from datetime import date, datetime, timedelta

from telegram import BotCommand, Update
from telegram.ext import Application, ApplicationBuilder

from trener import config as C
from trener import messages as M
from trener.caldav_todo import TodoList
from trener.engine import (Snapshot, apply_reports, compute_streak, default_session, fill_missing_days,
                           next_goal, plan, streak_after)
from trener.model import EVENING, MORNING, Day, Report, Settings
from trener.parsing import parse_message
from trener.reminders import ReminderSync
from trener.smb_io import Conflict, make_backend
from trener.store import Store
from trener.table import TableCorrupt, merge, parse_workbook, render_workbook
from trener.telegram_ui import BOT_COMMANDS, register
from trener.web import start_web_server

log = logging.getLogger("trener")


class Trainer:
    def __init__(self, cfg: C.Config, store: Store, backend, todos: TodoList | None, send):
        self.cfg, self.store, self.backend, self.todos, self.send = cfg, store, backend, todos, send
        self.tz = cfg.tz
        self.rem = ReminderSync(todos, store, cfg.tz) if todos else None
        self.lock = asyncio.Lock()
        self._kick = asyncio.Event()
        self.table_dirty = True
        self.rem_dirty = True
        self.last_table_sync: datetime | None = None
        self.last_rem_sync: datetime | None = None
        self.table_ok = False
        self.rem_ok: bool | None = None if todos else None
        self.table_warnings: list[str] = []
        self.table_stamp = None
        self.table_fail = 0
        self.table_alerted = False
        self.woke_date: date | None = None
        self.started_at = self.now()

    # ── pomôcky ─────────────────────────────────────────────────────────────
    def now(self) -> datetime:
        return datetime.now(self.tz)

    def settings(self) -> Settings:
        return self.store.get_settings()

    def _save_settings(self, s: Settings) -> None:
        self.store.save_settings(s)
        self.table_dirty = True
        self.rem_dirty = True

    def today_day(self) -> Day:
        t = self.now().date()
        d = self.store.get_day(t)
        if d is None:
            days = self.store.all_days()
            for nd in fill_missing_days(days, t, self.settings(), self.cfg.seed_goal):
                self.store.save_day(nd)
            d = self.store.get_day(t)
        return d

    def kick(self) -> None:
        self._kick.set()

    def wake(self) -> None:
        self.woke_date = self.now().date()
        log.info("Wake signál z telefónu.")
        self.kick()

    def health(self) -> dict:
        d = self.store.get_day(self.now().date())
        return {
            "ok": True, "time": self.now().isoformat(), "started": self.started_at.isoformat(),
            "today": None if d is None else {"date": d.date.isoformat(), "goal": d.goal,
                                             "morning": d.morning, "evening": d.evening, "done": d.done},
            "frozen": self.settings().frozen,
            "table": {"ok": self.table_ok, "last_sync": self.last_table_sync, "url": self.cfg.table_url,
                      "warnings": self.table_warnings[:5]},
            "reminders": {"enabled": self.rem is not None, "ok": self.rem_ok, "last_sync": self.last_rem_sync},
        }

    # ── štart ───────────────────────────────────────────────────────────────
    def bootstrap(self) -> None:
        if not self.store.c.execute("SELECT 1 FROM settings LIMIT 1").fetchone():
            s = Settings(increment=self.cfg.seed_increment, morning_time=self.cfg.seed_morning,
                         evening_time=self.cfg.seed_evening)
            self.store.save_settings(s)
            log.info("Prvý štart – nastavenia: %s", s)
        today = self.now().date()
        if self.store.get_meta("current_date") is None:
            self.store.set_meta("current_date", today.isoformat())
        self.today_day()

    # ── hlavná slučka ───────────────────────────────────────────────────────
    async def run_loop(self) -> None:
        log.info("Tick slučka beží (každých %d s).", self.cfg.tick_seconds)
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("Chyba v ticku – pokračujem.")
            try:
                await asyncio.wait_for(self._kick.wait(), timeout=self.cfg.tick_seconds)
            except asyncio.TimeoutError:
                pass
            self._kick.clear()

    async def tick(self) -> None:
        async with self.lock:
            now = self.now()
            await self._rollover_if_needed(now)
            if self.table_dirty or self._due(self.last_table_sync, self.cfg.table_sync_seconds):
                await self._sync_table()
            if self.rem and (self.rem_dirty or self._due(self.last_rem_sync, self.cfg.reminders_sync_seconds)):
                await self._sync_reminders()
            await self._check_completion("tabuľka/Pripomienky")
            await self._nags(now)

    def _due(self, last: datetime | None, seconds: int) -> bool:
        return last is None or (self.now() - last) >= timedelta(seconds=seconds)

    # ── prechod dňa ─────────────────────────────────────────────────────────
    async def _rollover_if_needed(self, now: datetime) -> None:
        today = now.date()
        cur = self.store.get_meta("current_date")
        cur_d = date.fromisoformat(cur) if cur else today
        if cur_d >= today:
            return
        settings = self.settings()
        days = self.store.all_days()
        for nd in fill_missing_days(days, today, settings, self.cfg.seed_goal):
            self.store.save_day(nd)
        yday = self.store.get_day(today - timedelta(days=1))
        tday = self.store.get_day(today)
        log.info("Nový deň %s: cieľ %s (včera %s).", today, tday.goal if tday else "?",
                 f"{yday.total}/{yday.goal} {yday.status(today)}" if yday else "-")
        if yday and tday and yday.status(today) == "failed" and settings.summary_on_fail and not settings.frozen:
            before = streak_after(self.store.all_days(), yday.date - timedelta(days=1), today)
            await self.send(M.day_failed(yday, before, tday.goal))
        self.store.set_meta("current_date", today.isoformat())
        self.woke_date = None
        self.table_dirty = True
        self.rem_dirty = True

    # ── tabuľka ─────────────────────────────────────────────────────────────
    async def _sync_table(self) -> None:
        today = self.now().date()
        try:
            res = await asyncio.to_thread(self.backend.read)
        except Exception as e:  # noqa: BLE001
            await self._table_failure(f"čítanie zlyhalo: {e}")
            return
        existing, stamp, parsed = None, None, None
        if res is not None:
            existing, stamp = res
            try:
                parsed = await asyncio.to_thread(parse_workbook, existing)
            except TableCorrupt as e:
                await self._table_failure(f"súbor sa nedá prečítať ({e}) – NEPREPISUJEM ho")
                return
        m = merge(parsed, self.store.days_with_synced(), self.settings(),
                  self.store.get_settings_synced(), today)
        # prevezmi zmeny z tabuľky do lokálneho stavu
        before_today = self.store.get_day(today)
        for day in m.days:
            cur = self.store.get_day(day.date)
            if cur != day:
                self.store.save_day(day)
        if m.changed_settings:
            self.store.save_settings(m.settings)
            self.rem_dirty = True
        for note in m.from_table:
            log.info("Tabuľka → bot: %s", note)
        if m.to_table:
            streaks = {d.date: streak_after(m.days, d.date, today) for d in m.days}
            extras = {d: v.extra for d, v in parsed.days.items()} if parsed else {}
            data = await asyncio.to_thread(render_workbook, existing, m.days, m.settings, streaks, today, extras)
            try:
                stamp = await asyncio.to_thread(self.backend.write, data, stamp)
                log.info("Tabuľka zapísaná (%d dní).", len(m.days))
            except Conflict:
                log.info("Tabuľka sa medzitým zmenila – skúsim v ďalšom ticku.")
                self.table_dirty = True
                return
            except Exception as e:  # noqa: BLE001
                await self._table_failure(f"zápis zlyhal: {e}")
                return
        for day in m.days:
            self.store.mark_day_synced(day)
        self.store.mark_settings_synced(m.settings)
        self.table_stamp = stamp
        self.table_ok = True
        self.table_warnings = parsed.warnings if parsed else []
        self.last_table_sync = self.now()
        self.table_dirty = False
        if self.table_fail:
            log.info("Tabuľka opäť dostupná.")
            if self.table_alerted:
                await self.send("✅ Tabuľka je opäť dostupná.")
        self.table_fail = 0
        self.table_alerted = False
        # oznám zmeny dnešného riadku z tabuľky (nie pri zamrazení)
        after_today = self.store.get_day(today)
        today_notes = [n for n in m.from_table if n.startswith(f"{today.day}.{today.month}.:")]
        if today_notes and before_today != after_today and not m.settings.frozen:
            if after_today.done and before_today and not before_today.done:
                pass  # oznámi _check_completion
            else:
                await self.send(M.table_update(after_today, today_notes))
        if after_today and after_today != before_today:
            self.rem_dirty = True

    async def _table_failure(self, why: str) -> None:
        self.table_fail += 1
        self.table_ok = False
        if self.table_fail in (1, 5) or self.table_fail % 30 == 0:
            log.warning("Tabuľka (%s): %s [pokus %d]", self.backend.describe(), why, self.table_fail)
        if self.table_fail == 5 and not self.table_alerted and not self.settings().frozen:
            self.table_alerted = True
            await self.send(f"⚠️ Tabuľka je nedostupná ({why[:120]}). Bežím ďalej z lokálnej kópie a skúšam znova.")

    # ── pripomienky ─────────────────────────────────────────────────────────
    async def _sync_reminders(self) -> None:
        if not self.rem:
            return
        today = self.today_day()
        settings = self.settings()
        out = await asyncio.to_thread(self.rem.sync, today, settings)
        for n in out.notes:
            log.info("Pripomienky: %s", n)
        if out.errors:
            if self.rem_ok is not False:
                log.warning("Pripomienky: %s", "; ".join(out.errors))
            self.rem_ok = False
        else:
            if self.rem_ok is False:
                log.info("Pripomienky opäť fungujú.")
            self.rem_ok = True
            self.last_rem_sync = self.now()
            self.rem_dirty = False
        if out.settings_changed:
            self.store.save_settings(out.settings)
            self.table_dirty = True
            if not out.settings.frozen:
                learned = [n for n in out.notes if "podľa tvojej úpravy" in n]
                if learned:
                    await self.send("📱 " + " ".join(learned) + " Platí pre všetky ďalšie dni.")
        if out.reports:
            res = self._apply(out.reports)
            if not res.completed_now and not out.settings.frozen:
                await self.send(M.report_reply(res.day, res.added, False,
                                               compute_streak(self.store.all_days(), today.date),
                                               next_goal(res.day, out.settings, self.cfg.seed_goal))
                                .replace("✅", "📱", 1))

    # ── splnenie a výzvy ────────────────────────────────────────────────────
    async def _check_completion(self, source: str) -> None:
        today = self.now().date()
        day = self.store.get_day(today)
        if day is None or not day.done:
            return
        if self.store.get_meta("announced_done_date") == today.isoformat():
            return
        self.store.set_meta("announced_done_date", today.isoformat())
        if not self.settings().frozen:
            streak = compute_streak(self.store.all_days(), today)
            await self.send(M.done_from_elsewhere(day, source, streak))
        self.rem_dirty = True

    async def _nags(self, now: datetime) -> None:
        s = self.settings()
        day = self.today_day()
        nags = {k: self.store.get_nag(day.date, k) for k in (MORNING, EVENING)}
        snap = Snapshot(now, s, day, nags, woke_up=self.woke_date == now.date())
        for a in plan(snap):
            nag = nags[a.session]
            k = nag.sent + 1
            log.info("Výzva %s %d/%d (deň %s/%s).", a.session, k, s.nag_max, day.total, day.goal)
            await self.send(M.nag(a.session, day, k, s.nag_max))
            if k == 1 and self.cfg.ha_alarm_url:
                await self._ha_alarm(M.nag(a.session, day, k, s.nag_max))
            nag.sent, nag.last_at = k, now
            self.store.save_nag(nag)

    async def _ha_alarm(self, text: str) -> None:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10, verify=False) as client:  # HA na LAN IP, vlastný cert
                r = await client.post(self.cfg.ha_alarm_url, json={"message": text})
                log.info("HA budík (HTTP %s).", r.status_code)
        except Exception as e:  # noqa: BLE001
            log.warning("HA budík zlyhal: %s", e)

    # ── zmeny stavu (z chatu / pripomienok) ─────────────────────────────────
    def _apply(self, reports: list[Report]):
        day = self.today_day()
        res = apply_reports(day, reports)
        self.store.save_day(res.day)
        self.table_dirty = True
        self.rem_dirty = True
        self.kick()
        return res

    async def handle_text(self, text: str) -> str:
        async with self.lock:
            now = self.now()
            await self._rollover_if_needed(now)
            s = self.settings()
            pr = parse_message(text, default_session(now, s))
            if not pr.ok:
                return M.ERRORS.get(pr.error or "no_number", M.NOT_A_NUMBER)
            res = self._apply(pr.reports)
            today = now.date()
            if res.completed_now:
                self.store.set_meta("announced_done_date", today.isoformat())
            streak = compute_streak(self.store.all_days(), today)
            log.info("Hlásenie %s → %s/%s (ráno %s, večer %s)", [(r.session, r.n, r.absolute) for r in pr.reports],
                     res.day.total, res.day.goal, res.day.morning, res.day.evening)
            return M.report_reply(res.day, res.added, res.completed_now, streak,
                                  next_goal(res.day, s, self.cfg.seed_goal))

    async def fix(self, session: str, n: int) -> str:
        async with self.lock:
            res = self._apply([Report(session, n, absolute=True)])
            today = self.now().date()
            if res.completed_now:
                self.store.set_meta("announced_done_date", today.isoformat())
            streak = compute_streak(self.store.all_days(), today)
            return M.report_reply(res.day, res.added, res.completed_now, streak,
                                  next_goal(res.day, self.settings(), self.cfg.seed_goal))

    async def set_goal(self, n: int) -> str:
        async with self.lock:
            day = self.today_day()
            was_done = day.done
            day = day.copy(goal=n)
            self.store.save_day(day)
            self.table_dirty = self.rem_dirty = True
            self.kick()
            extra = ""
            if day.done and not was_done:
                self.store.set_meta("announced_done_date", day.date.isoformat())
                extra = " 🎉 Tým je dnešok splnený."
            return M.SAVED.format(what=f"dnešný cieľ {n} (ráno {day.morning_target} + večer {day.evening_target})") + extra

    async def set_increment(self, n: int) -> str:
        async with self.lock:
            s = self.settings().copy(increment=n)
            self._save_settings(s)
            self.kick()
            return M.SAVED.format(what=f"prírastok +{n} po splnenom dni")

    async def set_time(self, session: str, hhmm: str) -> str:
        async with self.lock:
            s = self.settings()
            s = s.copy(morning_time=hhmm) if session == MORNING else s.copy(evening_time=hhmm)
            self._save_settings(s)
            self.kick()
            return M.SAVED.format(what=f"{'ranný' if session == MORNING else 'večerný'} čas {hhmm}"
                                  " (pripomienka aj výzvy v chate)")

    async def set_frozen(self, frozen: bool) -> str:
        async with self.lock:
            s = self.settings()
            if s.frozen == frozen:
                return M.ALREADY_FROZEN if frozen else M.ALREADY_RUNNING
            self._save_settings(s.copy(frozen=frozen))
            day = self.today_day()
            if not frozen and day.frozen and not day.done:
                self.store.save_day(day.copy(frozen=False))
            if frozen and not day.done:
                self.store.save_day(day.copy(frozen=True))
            self.kick()
            log.info("Zamrazenie: %s", frozen)
            if frozen:
                return M.FROZEN_ON
            day = self.today_day()
            return M.FROZEN_OFF.format(total=day.total, goal=day.goal)

    async def force_sync(self) -> str:
        async with self.lock:
            await self._sync_table()
            if self.rem:
                await self._sync_reminders()
            await self._check_completion("tabuľka/Pripomienky")
        rem = "vypnuté" if not self.rem else ("✅" if self.rem_ok else "⚠️ nedostupné")
        return M.SYNCED.format(table="✅" if self.table_ok else "⚠️ nedostupná", rem=rem)

    async def status_text(self) -> str:
        async with self.lock:
            day = self.today_day()
            s = self.settings()
            streak = compute_streak(self.store.all_days(), day.date)
            at = self.last_table_sync.strftime("%H:%M") if self.last_table_sync else None
            return M.status(day, s, streak, self.table_ok, at, self.rem_ok if self.rem else None,
                            next_goal(day, s, self.cfg.seed_goal))

    async def stats_text(self) -> str:
        async with self.lock:
            days = self.store.all_days()
            today = self.now().date()
            streak = compute_streak(days, today)
            best = max([streak_after(days, d.date, today) for d in days] + [0])
            return M.stats(days, today, streak, best)

    async def table_info(self) -> str:
        async with self.lock:
            day = self.today_day()
            at = self.last_table_sync.strftime("%d.%m. %H:%M") if self.last_table_sync else None
            return M.table_info(self.cfg.table_url, day, self.table_ok, at, self.table_warnings)


# ── štart procesu ─────────────────────────────────────────────────────────────

def setup_logging(cfg: C.Config) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    root.addHandler(sh)
    if cfg.log_file:
        cfg.log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(cfg.log_file, maxBytes=2_000_000, backupCount=5,
                                                  encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    for noisy in ("httpx", "httpcore", "apscheduler", "smbprotocol", "spnego", "telegram.ext.Application"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main() -> None:
    cfg = C.load()
    setup_logging(cfg)
    store = Store(cfg.state_db)
    backend = make_backend(cfg)
    todos = None
    if cfg.caldav_url and cfg.caldav_username and cfg.caldav_password:
        todos = TodoList(cfg.caldav_url, cfg.caldav_username, cfg.caldav_password, cfg.caldav_list)
    else:
        log.warning("CALDAV_* nie sú nastavené – Pripomienky vypnuté.")
    log.info("Tabuľka: %s", backend.describe())

    application: Application = (ApplicationBuilder().token(cfg.bot_token)
                                .get_updates_read_timeout(40).read_timeout(30).connect_timeout(30)
                                .post_init(post_init).post_stop(post_stop)
                                .post_shutdown(post_shutdown).build())

    async def send(text: str) -> None:
        for attempt in range(3):
            try:
                await application.bot.send_message(chat_id=cfg.owner_chat_id, text=text)
                return
            except Exception as e:  # noqa: BLE001
                log.warning("Telegram send zlyhal (%d/3): %s", attempt + 1, e)
                await asyncio.sleep(2 * (attempt + 1))

    trainer = Trainer(cfg, store, backend, todos, send)
    application.bot_data["trainer"] = trainer
    application.bot_data["cfg"] = cfg
    register(application, trainer, cfg.owner_chat_id)
    log.info("Štartujem polling…")
    application.run_polling(drop_pending_updates=False, allowed_updates=Update.ALL_TYPES)


async def post_init(application: Application) -> None:
    trainer: Trainer = application.bot_data["trainer"]
    cfg: C.Config = application.bot_data["cfg"]
    trainer.bootstrap()
    if trainer.todos:
        try:
            url = await asyncio.to_thread(trainer.todos.check)
            log.info("Pripomienky: zoznam '%s' → %s", cfg.caldav_list, url)
        except Exception as e:  # noqa: BLE001
            log.warning("Pripomienky nedostupné pri štarte (%s) – skúsim neskôr.", e)
    try:
        await application.bot.set_my_commands([BotCommand(c, d) for c, d in BOT_COMMANDS])
    except Exception as e:  # noqa: BLE001
        log.warning("set_my_commands: %s", e)
    loop = asyncio.get_running_loop()
    try:
        application.bot_data["web"] = start_web_server(
            cfg.web_port, cfg.wake_token,
            on_wake=lambda: loop.call_soon_threadsafe(trainer.wake),
            health=trainer.health)
    except OSError as e:
        log.error("Web server na porte %d sa nespustil: %s", cfg.web_port, e)
    # vlastný task (nie application.create_task – ten by Application.stop() čakal donekonečna)
    application.bot_data["loop_task"] = loop.create_task(trainer.run_loop(), name="trener-tick")
    log.info("Bot pripravený. Dnes: %s, zamrazené: %s", trainer.today_day(), trainer.settings().frozen)


async def post_stop(application: Application) -> None:
    task = application.bot_data.get("loop_task")
    if task:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    log.info("Tick slučka zastavená.")


async def post_shutdown(application: Application) -> None:
    web = application.bot_data.get("web")
    if web:
        web.shutdown()
    trainer: Trainer = application.bot_data["trainer"]
    if trainer.todos:
        trainer.todos.close()
    trainer.store.close()


if __name__ == "__main__":
    main()
