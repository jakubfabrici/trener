"""Všetky texty bota. Slovenčina, stručne, bez omáčok."""
from __future__ import annotations

from datetime import date

from trener.model import DONE, FAILED, FROZEN, MORNING, Day, Settings


def d(dt: date) -> str:
    return f"{dt.day}.{dt.month}."


def sess(session: str) -> str:
    return "ráno" if session == MORNING else "večer"


# ── hlásenia ─────────────────────────────────────────────────────────────────

def report_reply(day: Day, added: dict[str, int], completed_now: bool, streak: int,
                 next_goal: int, when: str = "dnes") -> str:
    parts = []
    for s in (MORNING, "evening"):
        if added.get(s):
            sign = "+" if added[s] > 0 else ""
            parts.append(f"{sign}{added[s]} {sess(s)}")
    head = "✅ " + ", ".join(parts) if parts else "ℹ️ Bez zmeny"
    lines = [head,
             f"🌅 Ráno: {day.morning}/{day.morning_target}",
             f"🌙 Večer: {day.evening}/{day.evening_target}"]
    label = when.capitalize()
    if completed_now and when != "dnes":
        lines.append(f"📊 {label}: {day.total}/{day.goal} ✅ splnený dodatočne. Streak {streak} 🔥")
    elif completed_now:
        lines.append(f"📊 {label}: {day.total}/{day.goal}")
        lines.append(f"🎉 Cieľ splnený! Streak {streak} 🔥 Zajtra ťa čaká {next_goal}.")
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
    return f"🎉 Dnes {day.total}/{day.goal} splnené ({source}). Streak {streak} 🔥"


def table_update(day: Day, notes: list[str]) -> str:
    return "📋 Z tabuľky: " + "; ".join(n.split(": ", 1)[-1] for n in notes) + \
        f" → dnes {day.total}/{day.goal}."


def day_failed(day: Day, streak_before: int, today_goal: int) -> str:
    lost = f" Streak {streak_before} je fuč." if streak_before else ""
    return f"❌ {d(day.date)} nesplnený ({day.total}/{day.goal}).{lost} Dnes odznova: {today_goal} klikov."


# ── stav / štatistiky ────────────────────────────────────────────────────────

def status(day: Day, settings: Settings, streak: int, table_ok: bool, table_at: str | None,
           reminders_ok: bool | None, next_goal: int) -> str:
    st = day.status(day.date)
    icon = {DONE: "✅", FAILED: "❌", FROZEN: "❄️"}.get(st, "⏳")
    lines = [f"{icon} Dnes {d(day.date)}: {day.total}/{day.goal}"
             f" (ráno {day.morning}/{day.morning_target}, večer {day.evening}/{day.evening_target})"]
    if not day.done:
        lines.append(f"zostáva {day.left}")
    lines.append(f"🔥 Streak: {streak}   🎯 Zajtra: {next_goal}")
    lines.append(f"⏰ Ráno {settings.morning_time} · Večer {settings.evening_time} · "
                 f"výzvy max {settings.nag_max}× po {settings.nag_interval_min} min")
    if settings.frozen:
        lines.append("❄️ ZAMRAZENÉ – nič nepripomínam, tabuľku ďalej sledujem. /odmraz na pokračovanie.")
    tbl = "✅" if table_ok else "⚠️ nedostupná"
    lines.append(f"📋 Tabuľka: {tbl}" + (f" (sync {table_at})" if table_at else ""))
    if reminders_ok is not None:
        lines.append("📱 Pripomienky: " + ("✅" if reminders_ok else "⚠️ CalDAV nedostupný"))
    return "\n".join(lines)


def stats(days: list[Day], today: date, streak: int, best: int) -> str:
    past = [x for x in days if x.date < today]
    done = sum(1 for x in past if x.status(today) == DONE)
    failed = sum(1 for x in past if x.status(today) == FAILED)
    frozen = sum(1 for x in past if x.status(today) == FROZEN)
    total = sum(x.total for x in days)
    return (f"📊 Dní: {len(past)} (✅ {done} · ❌ {failed} · ❄️ {frozen})\n"
            f"🔥 Streak {streak} · 🏆 najdlhší {best}\n"
            f"💪 Klikov spolu: {total}")


UNDONE = "↩️ Vrátené. Posledné hlásenie som zmazal."
UNDONE_AS_GOAL = "🎯 Jasné – {n} nebolo hlásenie, ale cieľ. Kliky som vrátil na predchádzajúci stav a dnešný cieľ je {n} ({m} ráno + {e} večer)."
NOTHING_TO_UNDO = "Nie je čo vrátiť – dnes nemám žiadne hlásenie z chatu, alebo sa stav medzitým zmenil. Oprav to ručne: /oprav ráno N, /oprav večer N, /ciel N."
GOAL_PICK = "🎯 Dnešný cieľ je {goal} ({m} ráno + {e} večer). Vyber nový, alebo napíš /ciel N:"
GOAL_SET = "🎯 Dnešný cieľ: {goal} ({m} ráno + {e} večer). Po splnenom dni rastie o {inc} (/prirastok N, 0 = nerastie)."

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
         "• Číslo = kliky, ktoré si PRÁVE dal („2“, „2 ráno“, „5 večer“) – sčítavam ich. Pomýlil si sa? Pod odpoveďou je ↩️ Vrátiť.\n"
         "• Cieľ meníš cez /ciel (tlačidlá) alebo /ciel 10.\n"
         "• Alebo ich napíš rovno do tabuľky – čítam ju každé 2 minúty.\n"
         "• Pripomienky máš v appke Pripomienky (zoznam „Kliky“) – uprav si čas či text, prispôsobím sa.\n"
         "/stav · /zmraz · /odmraz · /help")

HELP = ("🤖 Tréner klikov – ako na to\n"
        "ČÍSLO = kliky, ktoré si práve dal (nie cieľ!). Cieľ meníš cez /ciel.\n"
        "• číslo – práve dané kliky do aktuálnej fázy (pred večerným časom ráno, potom večer)\n"
        "• „2 ráno“, „5 večer“, „2 ráno a 3 večer“ – do konkrétnej fázy\n"
        "• „ráno = 5“ alebo /oprav ráno 5 – nastaví presnú hodnotu (oprava)\n"
        "/stav – dnešok, streak, stav tabuľky a pripomienok\n"
        "/zmraz · /odmraz – pauza (žiadne výzvy ani nové pripomienky; tabuľku ďalej sledujem)\n"
        "/ciel – vybrať dnešný cieľ tlačidlami, /ciel 10 – nastaviť priamo (ďalšie dni rastú o prírastok)\n"
        "↩️ Vrátiť / 🎯 Bol to cieľ – tlačidlá pod každým hlásením, keď sa pomýliš\n"
        "/prirastok N – o koľko rastie cieľ po splnenom dni\n"
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
