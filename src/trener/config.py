"""Konfigurácia z prostredia (systemd EnvironmentFile /etc/trener/trener.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo


def _env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    if v is None or v.strip() == "":
        return default
    return v.strip()


@dataclass(frozen=True)
class Config:
    bot_token: str
    owner_chat_id: int
    tz: ZoneInfo
    state_db: Path
    log_file: Path | None
    # tabuľka
    table_backend: str            # "smb" | "local"
    smb_server: str
    smb_share: str
    smb_path: str                 # relatívna cesta v share, napr. "Kliky/kliky.xlsx"
    smb_username: str
    smb_password: str
    local_table_path: Path
    table_sync_seconds: int
    # pripomienky: "shortcuts" (iCloud cez iOS Skratku), "caldav" (Radicale) alebo "off"
    reminders_mode: str
    reminders_list: str
    shortcut_token: str | None
    # budík v iCloud kalendári (CalDAV) – ranný a večerný event s upozornením
    calendar_mode: str            # "icloud" | "off"
    calendar_url: str
    calendar_user: str | None
    calendar_password: str | None
    calendar_name: str
    calendar_color: str
    calendar_minutes: int
    calendar_alarms: tuple[int, ...]
    calendar_sync_seconds: int
    # pripomienky (Radicale CalDAV)
    caldav_url: str | None
    caldav_username: str | None
    caldav_password: str | None
    caldav_list: str
    reminders_sync_seconds: int
    # web (health + wake)
    web_port: int
    wake_token: str | None
    # hlasný budík (Critical Alerts) – pushover | pushsafer | webhook | off
    alarm_mode: str
    pushover_token: str | None
    pushover_user: str | None
    pushover_device: str | None
    alarm_priority: int
    alarm_sound: str
    alarm_retry: int
    alarm_expire: int
    alarm_webhook_url: str | None
    # východiskové hodnoty pri prvom štarte (kým tabuľka neexistuje)
    seed_goal: int
    seed_increment: int
    seed_morning: str
    seed_evening: str
    tick_seconds: int

    @property
    def table_url(self) -> str:
        if self.table_backend == "smb":
            return f"smb://{self.smb_server}/{self.smb_share}/{self.smb_path}"
        return str(self.local_table_path)


def load() -> Config:
    token = _env("BOT_TOKEN")
    if not token:
        raise SystemExit("Chýba BOT_TOKEN (viď deploy/trener.env.example).")
    owner = _env("OWNER_CHAT_ID")
    if not owner or not owner.lstrip("-").isdigit():
        raise SystemExit("Chýba OWNER_CHAT_ID (číslo tvojho Telegram chatu).")
    backend = (_env("TABLE_BACKEND", "smb") or "smb").strip().lower()
    if backend not in ("smb", "local"):
        raise SystemExit(f"TABLE_BACKEND musí byť 'smb' alebo 'local' (je {backend!r}).")
    cal_mode = (_env("CALENDAR_MODE", "icloud") or "icloud").strip().lower()
    if cal_mode not in ("icloud", "off"):
        raise SystemExit(f"CALENDAR_MODE musí byť 'icloud' alebo 'off' (je {cal_mode!r}).")
    alarm_mode = (_env("ALARM_MODE", "off") or "off").strip().lower()
    if alarm_mode not in ("off", "pushover", "pushsafer", "webhook"):
        raise SystemExit(f"ALARM_MODE musí byť off/pushover/pushsafer/webhook (je {alarm_mode!r}).")
    if alarm_mode in ("pushover", "pushsafer") and not (_env("PUSHOVER_USER")
                                                        and (_env("PUSHOVER_TOKEN") or alarm_mode == "pushsafer")):
        raise SystemExit(f"ALARM_MODE={alarm_mode} vyžaduje PUSHOVER_USER (a PUSHOVER_TOKEN pre pushover).")
    if alarm_mode == "webhook" and not (_env("ALARM_WEBHOOK_URL") or _env("HA_ALARM_WEBHOOK_URL")):
        raise SystemExit("ALARM_MODE=webhook vyžaduje ALARM_WEBHOOK_URL.")
    mode = (_env("REMINDERS_MODE", "shortcuts") or "shortcuts").strip().lower()
    if mode not in ("shortcuts", "caldav", "off"):
        raise SystemExit(f"REMINDERS_MODE musí byť 'shortcuts', 'caldav' alebo 'off' (je {mode!r}).")
    return Config(
        bot_token=token,
        owner_chat_id=int(owner),
        tz=ZoneInfo(_env("TZ_NAME", "Europe/Bratislava")),
        state_db=Path(_env("STATE_DB", "/var/lib/trener/trener.db")),
        log_file=Path(_env("LOG_FILE")) if _env("LOG_FILE") else None,
        table_backend=backend,
        smb_server=_env("SMB_SERVER", "192.168.1.185"),
        smb_share=_env("SMB_SHARE", "share"),
        smb_path=_env("SMB_PATH", "Kliky/kliky.xlsx"),
        smb_username=_env("SMB_USERNAME", ""),
        smb_password=_env("SMB_PASSWORD", ""),
        local_table_path=Path(_env("LOCAL_TABLE_PATH", "/var/lib/trener/kliky.xlsx")),
        table_sync_seconds=int(_env("TABLE_SYNC_SECONDS", "120")),
        calendar_mode=cal_mode,
        calendar_url=_env("CALENDAR_URL", "https://caldav.icloud.com/"),
        calendar_user=_env("ICLOUD_USERNAME"),
        calendar_password=_env("ICLOUD_APP_PASSWORD"),
        calendar_name=_env("CALENDAR_NAME", "Kliky"),
        calendar_color=_env("CALENDAR_COLOR", "#FF6B35"),
        calendar_minutes=int(_env("CALENDAR_EVENT_MINUTES", "25")),
        calendar_alarms=tuple(sorted({max(int(x), 0) for x in
                                      (_env("CALENDAR_ALARMS", "0,3,7,12,20") or "0").split(",") if x.strip()})),
        calendar_sync_seconds=int(_env("CALENDAR_SYNC_SECONDS", "120")),
        reminders_mode=mode,
        reminders_list=_env("REMINDERS_LIST", _env("CALDAV_LIST", "Kliky")),
        shortcut_token=_env("SHORTCUT_TOKEN") or _env("WAKE_WEBHOOK_TOKEN"),
        caldav_url=_env("CALDAV_URL"),
        caldav_username=_env("CALDAV_USERNAME"),
        caldav_password=_env("CALDAV_PASSWORD"),
        caldav_list=_env("CALDAV_LIST", "Kliky"),
        reminders_sync_seconds=int(_env("REMINDERS_SYNC_SECONDS", "120")),
        web_port=int(_env("WEB_PORT", "8790")),
        wake_token=_env("WAKE_WEBHOOK_TOKEN"),
        alarm_mode=alarm_mode,
        pushover_token=_env("PUSHOVER_TOKEN"),
        pushover_user=_env("PUSHOVER_USER"),
        pushover_device=_env("PUSHOVER_DEVICE"),
        alarm_priority=int(_env("ALARM_PRIORITY", "2")),
        alarm_sound=_env("ALARM_SOUND", "persistent"),
        alarm_retry=int(_env("ALARM_RETRY", "60")),
        alarm_expire=int(_env("ALARM_EXPIRE", "600")),
        alarm_webhook_url=_env("ALARM_WEBHOOK_URL") or _env("HA_ALARM_WEBHOOK_URL"),
        seed_goal=int(_env("SEED_GOAL", "10")),
        seed_increment=int(_env("SEED_INCREMENT", "2")),
        seed_morning=_env("SEED_MORNING_TIME", "07:00"),
        seed_evening=_env("SEED_EVENING_TIME", "19:20"),
        tick_seconds=int(_env("TICK_SECONDS", "30")),
    )
