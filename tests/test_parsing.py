"""Parsovanie hlásení – vrátane scenára, ktorý v1 pokazil („2“ + „2“ ≠ splnených 10)."""
import pytest

from trener.model import EVENING, MORNING
from trener.parsing import parse_message, word_number


def rep(text, default=MORNING):
    pr = parse_message(text, default)
    assert pr.ok, f"{text!r}: {pr.error}"
    return [(r.session, r.n, r.absolute) for r in pr.reports]


def test_bare_number_goes_to_default_session():
    assert rep("2") == [(MORNING, 2, False)]
    assert rep("2", EVENING) == [(EVENING, 2, False)]
    assert rep(" 12 ") == [(MORNING, 12, False)]


@pytest.mark.parametrize("text", ["ráno 2", "2 ráno", "dal som dva ráno", "Ráno som dal 2.",
                                  "rano 2", "2 doobeda", "2r"])
def test_morning_keyword(text):
    assert rep(text, EVENING) == [(MORNING, 2, False)]


@pytest.mark.parametrize("text", ["5 večer", "vecer 5", "večer som dal päť", "5 poobede"])
def test_evening_keyword(text):
    assert rep(text, MORNING) == [(EVENING, 5, False)]


def test_two_clauses():
    assert rep("2 ráno a 3 večer") == [(MORNING, 2, False), (EVENING, 3, False)]
    assert rep("ráno 2, večer 3") == [(MORNING, 2, False), (EVENING, 3, False)]
    assert rep("2 ráno 3 večer") == [(MORNING, 2, False), (EVENING, 3, False)]
    assert rep("ráno 2 + večer 3") == [(MORNING, 2, False), (EVENING, 3, False)]


def test_absolute_correction():
    assert rep("ráno = 5") == [(MORNING, 5, True)]
    assert rep("ráno spolu 5") == [(MORNING, 5, True)]
    assert rep("ráno mám 5") == [(MORNING, 5, True)]
    assert rep("večer = 0", MORNING) == [(EVENING, 0, True)]


def test_zero_is_rejected_unless_correction():
    assert parse_message("0", MORNING).error == "zero"
    assert parse_message("nula", MORNING).error == "zero"


@pytest.mark.parametrize("text", ["07:30", "7:05", "2.9.", "2. 9. 2026", "o 19:20"])
def test_time_and_date_are_not_reports(text):
    assert parse_message(text, MORNING).error == "looks_like_time"


@pytest.mark.parametrize("text", ["ahoj", "", "   ", "hotovo", "dnes nič"])
def test_no_number(text):
    assert parse_message(text, MORNING).error == "no_number"


def test_too_big():
    assert parse_message("5000", MORNING).error == "too_big"


def test_word_numbers():
    assert word_number("pat") == 5
    assert word_number("dvadsatpat") == 25
    assert word_number("sto") == 100
    assert rep("päť") == [(MORNING, 5, False)]
    assert rep("dvadsaťpäť večer") == [(EVENING, 25, False)]
    assert rep("desať") == [(MORNING, 10, False)]


def test_numbers_never_change_settings():
    # v1 chyba: „2“ v zabudnutom menu prepísala denný cieľ. Tu je číslo vždy hlásenie.
    assert rep("1") == [(MORNING, 1, False)]
    assert rep("2") == [(MORNING, 2, False)]
