"""Všetky texty bota. Slovenčina, stručne, bez omáčok."""
from __future__ import annotations

from datetime import date, timedelta

from trener.model import (DONE, FAILED, FROZEN, MORNING, REST, WEEKDAY_SK, Day, Settings,
                          split_goal)


def d(dt: date) -> str:
    return f"{dt.day}.{dt.month}."


def den(dt: date) -> str:
    return WEEKDAY_SK[dt.weekday()]


def sess(session: str) -> str:
    return "ráno" if session == MORNING else "večer"


def rozpis(goal: int, dt: date) -> str:
    """„18 ráno + 18 večer“ / „18 ráno“ / „voľno“ – podľa dňa v týždni."""
    if goal <= 0:
        return "voľno 🌙"
    m, e = split_goal(goal, dt)
    return f"{m} ráno + {e} večer" if e else f"{m} ráno"


def zajtra_riadok(day: Day, next_goal: int) -> str:
    z = day.date + timedelta(days=1)
    if next_goal <= 0:
        return f"Zajtra ({den(z)}) je voľno 🌙"
    return f"Zajtra ({den(z)}): {rozpis(next_goal, z)}"


# ── hlásenia ─────────────────────────────────────────────────────────────────

def report_reply(day: Day, added: dict[str, int], completed_now: bool, streak: int,
                 next_goal: int, when: str = "dnes") -> str:
    parts = []
    for s in (MORNING, "evening"):
        if added.get(s):
            sign = "+" if added[s] > 0 else ""
            parts.append(f"{sign}{added[s]} {sess(s)}")
    head = "✅ " + ", ".join(parts) if parts else "ℹ️ Bez zmeny"
    label = when.capitalize()
    if day.is_rest:
        return f"{head}\n🌙 {label} ({den(day.date)}) je voľno – zapísal som to ako bonus. Spolu {day.total}."
    lines = [head, f"🌅 Ráno: {day.morning}/{day.morning_target}"]
    if day.evening_target > 0:
        lines.append(f"🌙 Večer: {day.evening}/{day.evening_target}")
    elif day.evening:
        lines.append(f"🌙 Večer: {day.evening} (navyše – {den(day.date)} máš len ráno)")
    if completed_now and when != "dnes":
        lines.append(f"📊 {label}: {day.total}/{day.goal} ✅ splnený dodatočne. Streak {streak} týždňov 🔥")
    elif completed_now:
        lines.append(f"📊 {label}: {day.total}/{day.goal}")
        lines.append(f"🎉 Deň splnený! {zajtra_riadok(day, next_goal)}")
    elif day.done:
        lines.append(f"📊 {label}: {day.total}/{day.goal} – cieľ už máš, rátam ďalej 😎")
    else:
        lines.append(f"📊 {label}: {day.total}/{day.goal} · zostáva {day.left}")
    return "\n".join(lines)


NOT_A_NUMBER = ("Nerozumiem. Číslo = kliky, ktoré si práve dal (napr. „2“, „2 ráno“, „5 večer“). "
                "Cieľ meníš cez /ciel. /help pre všetko ostatné.")
LOOKS_LIKE_TIME = "To vyzerá ako čas alebo dátum, nie počet klikov. Časy sa menia cez /rano HH:MM a /vecer HH:MM."
ZERO = "Nula sa nepočíta 😤 (ak chceš opraviť, napíš napr. „ráno = 0“)."
TOO_BIG = "To je priveľa naraz (max 1000). Ak to nie je preklep, pošli to po častiach."
STALE_MESSAGE = ("⏳ Táto správa je z {when} – vtedy som nebežal, tak ju do dneška nepočítam. "
                 "Ak platí, napíš „včera 5“ / „včera večer 5“, alebo to doplň v tabuľke.")
AMBIGUOUS = ("Viac čísel naraz a neviem, ktoré je ktoré. Napíš jedno číslo („5“), alebo ku každému fázu "
             "(„2 ráno a 3 večer“); dve série po päť napíš ako „2x5“.")
ERRORS = {"no_number": NOT_A_NUMBER, "looks_like_time": LOOKS_LIKE_TIME, "zero": ZERO, "too_big": TOO_BIG,
          "ambiguous": AMBIGUOUS}

# ── výzvy v chate ────────────────────────────────────────────────────────────

def nag(session: str, day: Day, k: int, total: int) -> str:
    tail = f" ({k}/{total})" if total > 1 else ""
    if session == MORNING:
        return (f"☀️ Ráno: {day.morning_left} klikov (dnes {day.total}/{day.goal}). "
                f"Napíš číslo, keď ich dáš.{tail}")
    return (f"🌙 Zostáva {day.left} klikov do dnešného cieľa {day.goal}"
            f" (ráno {day.morning}/{day.morning_target}).{tail}")


def done_from_elsewhere(day: Day, source: str, streak: int) -> str:
    return f"🎉 Dnes {day.total}/{day.goal} splnené ({source}). Streak {streak} týždňov 🔥"


def table_update(day: Day, notes: list[str]) -> str:
    return "📋 Z tabuľky: " + "; ".join(n.split(": ", 1)[-1] for n in notes) + \
        f" → dnes {day.total}/{day.goal}."


def day_failed(day: Day, streak_before: int, today_goal: int, today: date) -> str:
    lost = f" Týždeň je tým pokazený a streak {streak_before} týždňov padá." if streak_before else \
           " Týždeň je tým pokazený."
    dnes = f"Dnes ({den(today)}): {rozpis(today_goal, today)}." if today_goal > 0 \
           else f"Dnes ({den(today)}) je voľno 🌙."
    return f"❌ {d(day.date)} nesplnený ({day.total}/{day.goal}).{lost} {dnes}"


# ── stav / štatistiky ────────────────────────────────────────────────────────

def status(day: Day, settings: Settings, streak: int, table_ok: bool, table_at: str | None,
           reminders_ok: bool | None, next_goal: int, calendar_ok: bool | None = None,
           calendar_name: str = "Kliky") -> str:
    st = day.status(day.date)
    icon = {DONE: "✅", FAILED: "❌", FROZEN: "❄️", REST: "🌙"}.get(st, "⏳")
    x = settings.x_for(day.date)
    if day.is_rest:
        lines = [f"{icon} Dnes {d(day.date)} ({den(day.date)}): voľno."]
        if day.total:
            lines.append(f"Aj tak si dal {day.total} – rešpekt.")
    else:
        detail = f"ráno {day.morning}/{day.morning_target}"
        if day.evening_target > 0:
            detail += f", večer {day.evening}/{day.evening_target}"
        lines = [f"{icon} Dnes {d(day.date)} ({den(day.date)}): {day.total}/{day.goal} ({detail})"]
        if not day.done:
            lines.append(f"zostáva {day.left}")
    lines.append(f"🎯 X = {x} → {rozpis(day.goal, day.date)}")
    lines.append(f"🔥 Streak: {streak} celých týždňov   ·   {zajtra_riadok(day, next_goal)}")
    lines.append(f"⏰ Ráno {settings.morning_time} · Večer {settings.evening_time} · "
                 f"výzvy max {settings.nag_max}× po {settings.nag_interval_min} min")
    if settings.frozen:
        lines.append("❄️ ZAMRAZENÉ – nič nepripomínam, tabuľku ďalej sledujem. /odmraz na pokračovanie.")
    tbl = "✅" if table_ok else "⚠️ nedostupná"
    lines.append(f"📋 Tabuľka: {tbl}" + (f" (sync {table_at})" if table_at else ""))
    if calendar_ok is not None:
        lines.append(f"📅 Budík v kalendári „{calendar_name}“: "
                     + ("✅ ráno aj večer" if calendar_ok else "⚠️ iCloud nedostupný"))
    if reminders_ok is not None:
        lines.append("📱 Pripomienky: " + ("✅" if reminders_ok else "⚠️ CalDAV nedostupný"))
    return "\n".join(lines)


def stats(days: list[Day], today: date, streak: int, best: int) -> str:
    past = [x for x in days if x.date < today]
    done = sum(1 for x in past if x.status(today) == DONE)
    failed = sum(1 for x in past if x.status(today) == FAILED)
    frozen = sum(1 for x in past if x.status(today) == FROZEN)
    rest = sum(1 for x in past if x.status(today) == REST)
    total = sum(x.total for x in days)
    return (f"📊 Dní: {len(past)} (✅ {done} · ❌ {failed} · ❄️ {frozen} · 🌙 {rest})\n"
            f"🔥 Streak {streak} týždňov · 🏆 najviac {best}\n"
            f"💪 Klikov spolu: {total}")


UNDONE = "↩️ Vrátené. Posledné hlásenie som zmazal."
NOTHING_TO_UNDO = "Nie je čo vrátiť – dnes nemám žiadne hlásenie z chatu, alebo sa stav medzitým zmenil. Oprav to ručne: /oprav ráno N, /oprav večer N, /ciel N."


def tyzden_prehlad(settings: Settings, monday: date) -> str:
    """Rozpis celého týždňa, aby bolo hneď vidieť, čo X znamená."""
    riadky = []
    for i in range(7):
        dd = monday + timedelta(days=i)
        riadky.append(f"  {den(dd)}: {rozpis(settings.goal_for(dd), dd)}")
    return "\n".join(riadky)


def x_pick(settings: Settings, day: Day) -> str:
    x = settings.x_for(day.date)
    return (f"🎯 Teraz X = {x}. Dnes ({den(day.date)}) to je {rozpis(day.goal, day.date)}.\n"
            f"Vyber nové X, alebo napíš /ciel N. X je počet klikov v jednej sérii –"
            f" ráno vždy X, v pondelok, stredu a piatok aj večer X.")


def x_set(settings: Settings, monday: date, day: Day, today: date) -> str:
    kedy = "od tohto týždňa" if monday <= today else f"od pondelka {d(monday)}"
    hlava = f"🎯 X = {settings.x} {kedy}. Každý pondelok narastie o {settings.x_step}."
    dnes = (f"Dnes ({den(day.date)}): {rozpis(day.goal, day.date)}."
            if monday <= today else "")
    return "\n".join(x for x in (hlava, dnes, tyzden_prehlad(settings, monday)) if x)

FROZEN_ON = "❄️ Zamrazené. Nebudem nič pripomínať (chat ani Pripomienky), tabuľku ďalej sledujem. /odmraz keď budeš chcieť."
FROZEN_OFF = "▶️ Odmrazené. Ideme ďalej – dnes {total}/{goal}."
ALREADY_FROZEN = "Už je zamrazené. /odmraz na pokračovanie."
ALREADY_RUNNING = "Nie je zamrazené – bežím. /zmraz ak chceš pauzu."

SAVED = "✅ Uložené: {what}"
BAD_INT = "Potrebujem celé číslo ≥ 0, napr. /{cmd} 12"
BAD_TIME = "Potrebujem čas HH:MM, napr. /{cmd} 07:00"
BAD_FIX = "Použitie: /oprav ráno 5   alebo   /oprav večer 3   (nastaví presnú hodnotu)"
SYNCED = "🔄 Sync hotový. Tabuľka: {table}; Pripomienky: {rem}."

HELLO = ("Ahoj, som tvoj tréner klikov v2. 💪\n"
         "• Plán: ráno X každý deň okrem nedele; v pondelok, stredu a piatok aj večer X. Nedeľa voľno.\n"
         "• X rastie každý pondelok o 1. Streak = počet celých splnených týždňov.\n"
         "• Číslo = kliky, ktoré si PRÁVE dal („2“, „2 ráno“, „5 večer“) – sčítavam ich. Pomýlil si sa? Pod odpoveďou je ↩️ Vrátiť.\n"
         "• X meníš cez /ciel (tlačidlá) alebo /ciel 9.\n"
         "• Alebo ich napíš rovno do tabuľky – čítam ju každé 2 minúty.\n"
         "• Pripomienky máš v appke Pripomienky (zoznam „Kliky“) – uprav si čas či text, prispôsobím sa.\n"
         "/stav · /zmraz · /odmraz · /help")

HELP = ("🤖 Tréner klikov – ako na to\n"
        "PLÁN: po/st/pi = ráno X + večer X · ut/št/so = ráno X · nedeľa voľno.\n"
        "X rastie každý pondelok o 1 (/prirastok N zmení o koľko). Streak = celé splnené týždne.\n"
        "ČÍSLO = kliky, ktoré si práve dal (nie cieľ!). X meníš cez /ciel.\n"
        "• číslo – práve dané kliky do aktuálnej fázy (pred večerným časom ráno, potom večer)\n"
        "• „2 ráno“, „5 večer“, „2 ráno a 3 večer“ – do konkrétnej fázy\n"
        "• „ráno = 5“ alebo /oprav ráno 5 – nastaví presnú hodnotu (oprava)\n"
        "/stav – dnešok, streak, stav tabuľky a pripomienok\n"
        "/zmraz · /odmraz – pauza (žiadne výzvy ani nové pripomienky; tabuľku ďalej sledujem)\n"
        "/ciel – vybrať X tlačidlami, /ciel 9 – nastaviť priamo (platí pre celý bežiaci týždeň)\n"
        "↩️ Vrátiť / 🎯 Bolo to X – tlačidlá pod každým hlásením, keď sa pomýliš\n"
        "/prirastok N – o koľko rastie X každý pondelok\n"
        "/rano HH:MM · /vecer HH:MM – časy pripomienok a výziev\n"
        "/tabulka – kde je tabuľka a čo je v nej dnes\n"
        "/sync – načítaj tabuľku a pripomienky hneď\n"
        "/stats – celková štatistika\n\n"
        "Výzvy v chate: max {nag_max}× za sebou po {interval} min, ráno aj večer. "
        "Splnenú fázu odčiarknem v Pripomienkach; keď odčiarkneš ty, beriem ju ako splnenú.")


def table_info(url: str, day: Day, ok: bool, at: str | None, warnings: list[str], busy: bool = False) -> str:
    lines = [f"📋 Tabuľka: {url}",
             f"Dnes {d(day.date)}: cieľ {day.goal}, ráno {day.morning}, večer {day.evening}, "
             f"spolu {day.total}",
             ("✅ posledný sync " + at) if ok and at else "⚠️ tabuľka je momentálne nedostupná – bežím z lokálnej kópie"]
    if busy:
        lines.append("✏️ Súbor má teraz otvorený iný program (Excel) – čítam ho, zapíšem, keď ho zavrieš.")
    if warnings:
        lines.append("⚠️ " + " | ".join(warnings[:3]))
    return "\n".join(lines)


# voliteľný hlasný budík (kanál s Critical Alerts) – default vypnutý
def alarm_title(session: str) -> str:
    return "☀️ Kliky – ráno" if session == MORNING else "🌙 Kliky – večer"


def alarm_text(session: str, day: Day) -> str:
    if session == MORNING:
        return f"Ranná dávka: {day.morning_left} klikov (dnes {day.total}/{day.goal})."
    return f"Večerná dávka: zostáva {day.left} klikov z dnešných {day.goal}."


BAD_X = "X musí byť celé číslo 1 až 100, napr. /ciel 25 (X je jedna séria – ráno a vo väčšine dní aj večer)."
