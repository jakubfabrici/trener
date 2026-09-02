"""Hlasný budík: správny payload pre Pushover/Pushsafer, nikdy nespadne."""
import asyncio
from dataclasses import dataclass

import httpx
import pytest

from trener import alarm as A
from trener.messages import alarm_text, alarm_title
from trener.model import EVENING, MORNING, Day
from datetime import date


@dataclass
class Cfg:
    alarm_mode: str = "pushover"
    pushover_token: str = "apptoken"
    pushover_user: str = "userkey"
    pushover_device: str | None = None
    alarm_priority: int = 2
    alarm_sound: str = "persistent"
    alarm_retry: int = 60
    alarm_expire: int = 600
    alarm_webhook_url: str | None = None


def run(cfg, monkeypatch, status=200):
    sent = {}

    class FakeClient:
        def __init__(self, *a, **kw):
            sent["client_kwargs"] = kw

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, data=None, json=None):
            sent["url"], sent["data"], sent["json"] = url, data, json
            return httpx.Response(status, text="ok" if status < 400 else "chyba")

    monkeypatch.setattr(A.httpx, "AsyncClient", FakeClient)
    ok = asyncio.run(A.send_alarm(cfg, "Ranná dávka: 5 klikov", title="☀️ Kliky – ráno"))
    return ok, sent


def test_pushover_emergency_payload(monkeypatch):
    ok, sent = run(Cfg(), monkeypatch)
    assert ok and sent["url"] == A.PUSHOVER_URL
    d = sent["data"]
    assert d["token"] == "apptoken" and d["user"] == "userkey"
    assert d["priority"] == 2 and d["retry"] >= 30 and d["expire"] == 600
    assert d["sound"] == "persistent" and d["title"] == "☀️ Kliky – ráno"
    assert "device" not in d


def test_pushover_high_priority_has_no_retry(monkeypatch):
    ok, sent = run(Cfg(alarm_priority=1, pushover_device="iphone"), monkeypatch)
    assert ok and "retry" not in sent["data"] and sent["data"]["device"] == "iphone"


def test_pushsafer_payload(monkeypatch):
    ok, sent = run(Cfg(alarm_mode="pushsafer", pushover_user="privatekey"), monkeypatch)
    assert ok and sent["url"] == A.PUSHSAFER_URL
    assert sent["data"]["k"] == "privatekey" and sent["data"]["cr"] == 1


def test_webhook_mode(monkeypatch):
    ok, sent = run(Cfg(alarm_mode="webhook", alarm_webhook_url="https://ha/api/webhook/x"), monkeypatch)
    assert ok and sent["json"] == {"message": "Ranná dávka: 5 klikov", "title": "☀️ Kliky – ráno"}


def test_off_sends_nothing(monkeypatch):
    ok, sent = run(Cfg(alarm_mode="off"), monkeypatch)
    assert ok is False and sent == {}


def test_failures_never_raise(monkeypatch):
    ok, _ = run(Cfg(), monkeypatch, status=500)
    assert ok is False

    class Boom:
        def __init__(self, *a, **kw):
            raise RuntimeError("sieť")
    monkeypatch.setattr(A.httpx, "AsyncClient", Boom)
    assert asyncio.run(A.send_alarm(Cfg(), "x")) is False


def test_alarm_texts():
    d = Day(date(2026, 9, 2), 10, morning=2)
    assert "ráno" in alarm_title(MORNING) and "večer" in alarm_title(EVENING)
    assert "3 klikov" in alarm_text(MORNING, d)
    assert "8 klikov" in alarm_text(EVENING, d)
