"""Parsovanie správ z chatu na hlásenia klikov – deterministicky, bez skrytého stavu.

Pravidlá:
- číslo bez ničoho → +n do aktuálnej fázy (ráno pred večerným časom, potom večer),
- „ráno 2“, „2 ráno“, „dal som dva ráno“, „5 večer“, „2 ráno a 3 večer“ → +n do danej fázy,
- „ráno = 5“, „ráno spolu 5“, „ráno mám 5“ → nastav vedro na presne 5 (oprava),
- slovné číslovky (dva, päť, desať, dvadsaťpäť …) sa berú ako čísla,
- čas („07:30“), dátum („2.9.“) ani nič bez čísla nie je hlásenie → chyba s dôvodom.
Nikdy sa tu nemení žiadne nastavenie – na to sú príkazy /ciel, /rano, /vecer …
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from trener.model import EVENING, MORNING, Report

MAX_PER_REPORT = 1000

MORNING_WORDS = {"rano", "ranajky", "doobeda", "dopoludnia", "dopoludnim", "predpoludnim", "ranne", "ranna",
                 "ranny", "ranu"}
EVENING_WORDS = {"vecer", "poobede", "popoludni", "vecerne", "vecerna", "vecerny", "podvecer", "vecere"}
# len jednoznačné slová – „mám“, „je“, „v“ sú bežné slová a nesmú meniť význam
ABSOLUTE_WORDS = {"spolu", "celkom", "celkovo", "dokopy", "=", "oprav", "nastav"}
YESTERDAY_WORDS = {"vcera", "vcerajsok", "vcerajsi", "vcerajsie"}
MINUS_WORDS = {"minus", "menej", "odpocitaj", "odrataj", "uber"}
# spojky, ktoré oddeľujú vety/klauzuly („2 ráno a 3 večer“)
CLAUSE_SPLIT = re.compile(r"\s*(?:,|;|\+|\ba\b|\ba potom\b|\bpotom\b|\bplus\b)\s*")

UNITS = {
    "nula": 0, "jeden": 1, "jedna": 1, "jedno": 1, "dva": 2, "dve": 2, "tri": 3, "styri": 4,
    "pat": 5, "sest": 6, "sedem": 7, "osem": 8, "devat": 9, "desat": 10, "jedenast": 11,
    "dvanast": 12, "trinast": 13, "strnast": 14, "patnast": 15, "sestnast": 16,
    "sedemnast": 17, "osemnast": 18, "devatnast": 19,
}
TENS = {"dvadsat": 20, "tridsat": 30, "styridsat": 40, "patdesiat": 50, "sestdesiat": 60,
        "sedemdesiat": 70, "osemdesiat": 80, "devatdesiat": 90, "sto": 100}


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def normalize(text: str) -> str:
    return strip_accents(text).lower().replace("ľ", "l").replace("ĺ", "l")


def word_number(word: str) -> int | None:
    """'dva' → 2, 'dvadsatpat' → 25, 'sto' → 100; inak None."""
    w = word
    if w in UNITS:
        return UNITS[w]
    if w in TENS:
        return TENS[w]
    for t, tv in TENS.items():
        if w.startswith(t) and w[len(t):] in UNITS and UNITS[w[len(t):]] > 0:
            return tv + UNITS[w[len(t):]]
    return None


@dataclass
class ParseResult:
    reports: list[Report] = field(default_factory=list)
    error: str | None = None      # no_number | zero | too_big | looks_like_time | ambiguous
    words: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.reports)


_TIME_RE = re.compile(r"\b\d{1,2}[:.]\d{2}\b")
_DATE_RE = re.compile(r"\b\d{1,2}\.\s*\d{1,2}\.")
_TOKEN_RE = re.compile(r"-?\d+|=|[a-z]+", re.IGNORECASE)
_TIMES_RE = re.compile(r"\b(\d+)\s*[x×*]\s*(\d+)\b")   # „2x5“ = dve série po päť = 10


def _clause_reports(clause: str, default_session: str) -> tuple[list[Report], str | None]:
    clause = _TIMES_RE.sub(lambda m: str(int(m.group(1)) * int(m.group(2))), clause)
    tokens = _TOKEN_RE.findall(clause)
    nums: list[tuple[int, int]] = []       # (index tokenu, hodnota)
    keywords: list[tuple[int, str]] = []   # (index tokenu, fáza)
    absolute = False
    negate = False
    for i, tok in enumerate(tokens):
        if tok.lstrip("-").isdigit():
            n = int(tok)
            if negate:
                n = -abs(n)
                negate = False
            nums.append((i, n))
            continue
        if tok == "=" or tok in ABSOLUTE_WORDS:
            absolute = True
            continue
        if tok in MINUS_WORDS:
            negate = True
            continue
        if tok in MORNING_WORDS:
            keywords.append((i, MORNING))
        elif tok in EVENING_WORDS:
            keywords.append((i, EVENING))
        else:
            wn = word_number(tok)
            if wn is not None:
                nums.append((i, -wn if negate else wn))
                negate = False
    if not nums:
        return [], None
    if len(nums) == 1:
        n = nums[0][1]
        sessions = sorted({k[1] for k in keywords}, key=lambda x: x != MORNING)
        if len(sessions) == 2:
            # „ráno aj večer po 5“
            return [Report(sess, n, absolute) for sess in sessions], None
        if not sessions:
            return [Report(default_session, n, absolute, total=absolute)], None
        return [Report(sessions[0], n, absolute)], None
    # viac čísel v jednej klauzule: len keď každé má svoje slovo („2 ráno 3 večer“ / „ráno 2 večer 3“)
    if len(keywords) != len(nums):
        return [], "ambiguous"
    return [Report(keywords[idx][1], n, absolute) for idx, (_, n) in enumerate(nums)], None


def parse_message(text: str, default_session: str) -> ParseResult:
    raw = (text or "").strip()
    if not raw:
        return ParseResult(error="no_number")
    if _TIME_RE.search(raw) or _DATE_RE.search(raw):
        return ParseResult(error="looks_like_time")
    norm = normalize(raw)
    if not re.search(r"\d", norm) and not any(word_number(w) is not None for w in re.findall(r"[a-z]+", norm)):
        return ParseResult(error="no_number", words=re.findall(r"[a-z]+", norm))
    words = set(re.findall(r"[a-z]+", norm))
    day_offset = -1 if words & YESTERDAY_WORDS else 0
    reports: list[Report] = []
    for clause in CLAUSE_SPLIT.split(norm):
        if not clause.strip():
            continue
        r, err = _clause_reports(clause, default_session)
        if err:
            return ParseResult(error=err)
        reports.extend(r)
    if not reports:
        return ParseResult(error="no_number")
    for r in reports:
        r.day_offset = day_offset
        if abs(r.n) > MAX_PER_REPORT:
            return ParseResult(error="too_big")
        if r.n == 0 and not r.absolute:
            return ParseResult(error="zero")
        if r.n < 0 and r.absolute:
            return ParseResult(error="ambiguous")
    return ParseResult(reports=reports)
