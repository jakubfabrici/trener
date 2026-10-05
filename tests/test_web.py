"""Web: /wake token (aj ne-ASCII), /health len detaily pre LAN a telefónne API
(/stav, /plan, /kliky, /zmraz, /hotovo) nad týždenným plánom X."""
import asyncio
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from trener import app as A
from trener.config import Config
from trener.model import DONE, EVENING, FROZEN, MORNING, OPEN, REST, Day
from trener.shortcuts import make_note
from trener.smb_io import LocalBackend
from trener.store import Store
from trener.web import start_web_server

TZ = ZoneInfo("Europe/Bratislava")
TOK = "skratka"
# X = 3 (seed) → po/st/pi 6+6 = 12, ut/št/so 6+0 = 6, nedeľa 0. Pozor na deň v týždni:
# väčšina vetiev sa líši len ním, nie číslami.
PO = date(2026, 9, 7)         # pondelok – ráno aj večer, tu začína platnosť X
UT = date(2026, 9, 8)         # utorok – len ráno
SO = date(2026, 9, 12)        # sobota – len ráno
NE = date(2026, 9, 13)        # nedeľa – voľno


def _port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def at(h, m=0, d=PO):
    return datetime.combine(d, dtime(h, m), tzinfo=TZ)


class Phone:
    """Celý bot bez Telegramu (falošné odosielanie, lokálna tabuľka, riadený čas) aj s jeho
    web serverom – testy sa ho pýtajú cez HTTP presne ako telefón."""

    def __init__(self, tmp: Path, start: datetime):
        self.clock = [start]
        self.sent: list[str] = []
        self.cfg = Config(bot_token="x", owner_chat_id=1, tz=TZ, state_db=tmp / "t.db", log_file=None,
                          table_backend="local", smb_server="", smb_share="", smb_path="",
                          smb_username="", smb_password="", local_table_path=tmp / "kliky.xlsx",
                          table_sync_seconds=120,
                          calendar_mode="off", calendar_url="", calendar_user=None,
                          calendar_password=None, calendar_name="Kliky", calendar_color="#FF6B35",
                          calendar_minutes=25, calendar_alarms=(0,), calendar_sync_seconds=120,
                          reminders_mode="shortcuts", reminders_list="Kliky", shortcut_token=TOK,
                          caldav_url=None, caldav_username=None, caldav_password=None,
                          caldav_list="Kliky", reminders_sync_seconds=120,
                          web_port=0, wake_token=None,
                          alarm_mode="off", pushover_token=None, pushover_user=None,
                          pushover_device=None, alarm_priority=2, alarm_sound="persistent",
                          alarm_retry=60, alarm_expire=600, alarm_webhook_url=None,
                          seed_x=6, seed_x_step=1, seed_morning="07:00", seed_evening="19:20",
                          tick_seconds=30)
        self.store = Store(self.cfg.state_db)
        self.t = A.Trainer(self.cfg, self.store, LocalBackend(self.cfg.local_table_path), None, self._send)
        self.t.rem = None
        self.t.todos = None
        self.t.now = lambda: self.clock[0]
        self.t.bootstrap()
        self.port = _port()
        self.srv = start_web_server(self.port, "wake", lambda: None, self.t.health,
                                    shortcut_token=TOK, plan=self.t.shortcut_plan,
                                    report=self.t.shortcut_report)

    async def _send(self, text):
        self.sent.append(text)

    # ── riadenie času ───────────────────────────────────────────────────────
    def tick_at(self, when: datetime):
        self.clock[0] = when
        asyncio.run(self.t.tick())

    # ── HTTP ako z telefónu ─────────────────────────────────────────────────
    def get(self, path: str, **q):
        url = f"http://127.0.0.1:{self.port}{path}?" + urllib.parse.urlencode({"token": TOK, **q})
        return json.loads(urllib.request.urlopen(url).read())

    def stav(self) -> dict:
        return self.get("/stav")

    def health(self) -> dict:
        return json.loads(urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health").read())

    def uloz_tyzden(self, monday: date, splneny: bool = True) -> None:
        """Vloží do DB celý tréningový týždeň podľa plánu (voliteľne rovno splnený).
        Rozdelenie ráno/večer je jedno – deň je splnený súčtom."""
        s = self.t.settings()
        for i in range(7):
            d = monday + timedelta(days=i)
            goal = s.goal_for(d)
            if goal > 0:
                self.store.save_day(Day(d, goal, morning=goal if splneny else 0))

    def close(self):
        self.srv.shutdown()
        self.store.close()


@pytest.fixture
def telefon(tmp_path):
    """Továreň na bota s web serverom – každý test si volí, ktorý deň v týždni je."""
    made: list[Phone] = []

    def make(day: date, h: int = 6, m: int = 0) -> Phone:
        made.append(Phone(tmp_path, at(h, m, day)))
        return made[-1]

    yield make
    for p in made:
        p.close()


def test_wake_and_health():
    port = _port()
    hits = []
    srv = start_web_server(port, "tajny", lambda: hits.append(1),
                           lambda: {"ok": True, "today": {"goal": 12}, "frozen": False})
    try:
        base = f"http://127.0.0.1:{port}"
        assert urllib.request.urlopen(base + "/wake?token=tajny").status == 200 and hits == [1]
        for bad in ("/wake?token=zly", "/wake?token=%C3%A9%C5%A1", "/wake"):
            try:
                urllib.request.urlopen(base + bad)
                assert False, bad
            except urllib.error.HTTPError as e:
                assert e.code == 403
        assert hits == [1]
        # LAN klient (priamo z 127.0.0.1) vidí detaily
        d = json.loads(urllib.request.urlopen(base + "/health").read())
        assert d["today"]["goal"] == 12 and d["ok"]
        # klient z internetu cez proxy: NPM pridá skutočnú IP na KONIEC X-Forwarded-For → len ok
        req = urllib.request.Request(base + "/health", headers={"X-Forwarded-For": "8.8.8.8"})
        d2 = json.loads(urllib.request.urlopen(req).read())
        assert d2 == {"ok": True}
        req = urllib.request.Request(base + "/health", headers={"X-Forwarded-For": "192.168.1.213, 8.8.8.8"})
        assert json.loads(urllib.request.urlopen(req).read()) == {"ok": True}
        req = urllib.request.Request(base + "/health", headers={"X-Forwarded-For": "192.168.1.50"})
        assert "today" in json.loads(urllib.request.urlopen(req).read())
        # podvrhnutý privátny prefix pred skutočnou IP (NPM appenduje) → stále len ok
        req = urllib.request.Request(base + "/health", headers={"X-Forwarded-For": "10.0.0.1, 1.1.1.1"})
        assert json.loads(urllib.request.urlopen(req).read()) == {"ok": True}
    finally:
        srv.shutdown()


def test_shortcut_routes():
    port = _port()
    got = []
    plan = {"datum": "2026-09-02", "zoznam": "Kliky", "pocet": 1,
            "pripomienky": [{"nazov": "💪 Večer: 5 klikov", "poznamka": "kliky:2026-09-02:evening",
                             "cas": "2026-09-02T19:20:00+02:00"}]}
    srv = start_web_server(port, "wake", lambda: None, lambda: {"ok": True},
                           shortcut_token="skratka", plan=lambda: plan,
                           report=lambda p: (got.append(p), {"ok": True})[1])
    try:
        base = f"http://127.0.0.1:{port}"
        d = json.loads(urllib.request.urlopen(base + "/plan?token=skratka").read())
        assert d["pripomienky"][0]["nazov"] == "💪 Večer: 5 klikov"
        # zlý token nikde neprejde
        for path in ("/plan?token=zly", "/hotovo?token=zly&poznamka=x", "/uprav?token=wake&poznamka=x"):
            try:
                urllib.request.urlopen(base + path)
                assert False, path
            except urllib.error.HTTPError as e:
                assert e.code == 403
        # odškrtnutie
        u = urllib.parse.quote("kliky:2026-09-02:evening")
        assert json.loads(urllib.request.urlopen(f"{base}/hotovo?token=skratka&poznamka={u}").read()) == {"ok": True}
        # úprava s emoji a diakritikou v názve
        naz = urllib.parse.quote("💪 Večer: 5 klikov ♥")
        urllib.request.urlopen(f"{base}/uprav?token=skratka&poznamka={u}&nazov={naz}&cas=20:00")
        urllib.request.urlopen(f"{base}/zmazane?token=skratka&poznamka={u}")
        assert [g["kind"] for g in got] == ["done", "edit", "deleted"]
        assert got[1]["nazov"] == "💪 Večer: 5 klikov ♥" and got[1]["cas"] == "20:00"
        assert all(g["poznamka"] == "kliky:2026-09-02:evening" for g in got)
        # POST funguje rovnako (Skratka vie oboje)
        req = urllib.request.Request(f"{base}/hotovo?token=skratka&poznamka={u}", data=b"", method="POST")
        assert json.loads(urllib.request.urlopen(req).read()) == {"ok": True}
    finally:
        srv.shutdown()


def test_stav_prechadza_tyzdnom_podla_planu(telefon):
    """/stav musí každý deň sedieť s plánom: deň v týždni, cieľ, rozpad na fázy aj zajtrajšok.
    V nedeľu cieľ 0 a zajtra už nové X (3 → 4, teda 4·4 = 16)."""
    p = telefon(PO)
    ocakavane = [
        ("pondelok", 12, 6, 6, 6),      # zajtra utorok 2X
        ("utorok", 6, 6, 0, 12),        # zajtra streda 4X
        ("streda", 12, 6, 6, 6),
        ("štvrtok", 6, 6, 0, 12),
        ("piatok", 12, 6, 6, 6),
        ("sobota", 6, 6, 0, 0),         # zajtra nedeľa – voľno
        ("nedeľa", 0, 0, 0, 14),        # zajtra pondelok s X = 4
    ]
    for i, (den, ciel, rano, vecer, zajtra) in enumerate(ocakavane):
        p.tick_at(at(6, 0, PO + timedelta(days=i)))
        s = p.stav()
        assert (s["den"], s["ciel"], s["rano_ciel"], s["vecer_ciel"], s["zajtra_ciel"]) == \
               (den, ciel, rano, vecer, zajtra)
        assert s["datum"] == (PO + timedelta(days=i)).isoformat()
        assert s["x"] == 6                      # X sa mení až v pondelok, celý týždeň drží
        assert s["volno"] is (ciel == 0)
    # ďalší pondelok X samo narastie o x_step (3 → 4) bez akéhokoľvek zásahu
    p.tick_at(at(6, 0, PO + timedelta(days=7)))
    s = p.stav()
    assert (s["den"], s["x"], s["ciel"], s["rano_ciel"], s["vecer_ciel"]) == ("pondelok", 7, 14, 7, 7)


def test_stav_v_utorok_neponuka_vecer(telefon):
    """Utorok má len rannú fázu: večer nemá čas ani poznámku (telefón si naň nesmie dať budík),
    ale ráno áno – a v pláne je jediná pripomienka."""
    p = telefon(UT)
    p.tick_at(at(6, 5, UT))
    s = p.stav()
    assert (s["den"], s["ciel"], s["volno"], s["stav"]) == ("utorok", 6, False, OPEN)
    assert s["vecer_ciel"] == 0 and s["vecer_due"] is None and s["poznamka_vecer"] == ""
    assert s["vecer_hotovo"] is True          # fáza mimo plánu sa nečaká
    assert s["rano_ciel"] == 6 and s["rano_hotovo"] is False
    assert s["rano_due"] == f"{UT.isoformat()}T07:00:00+02:00"
    assert s["poznamka_rano"] == make_note(UT, MORNING)
    plan = p.get("/plan")
    assert plan["pocet"] == 1 and [i["faza"] for i in plan["pripomienky"]] == [MORNING]
    assert plan["stav"] == s                  # /stav je presne časť „stav“ z /plan


def test_stav_v_nedelu_je_volno(telefon):
    """Nedeľa: cieľ 0, stav „voľno“, žiadna fáza v pláne – teda ani čas, ani poznámka."""
    p = telefon(NE)
    p.tick_at(at(6, 5, NE))
    s = p.stav()
    assert (s["den"], s["ciel"], s["volno"], s["stav"]) == ("nedeľa", 0, True, REST)
    assert (s["rano_ciel"], s["vecer_ciel"]) == (0, 0)
    assert s["rano_due"] is None and s["vecer_due"] is None
    assert s["poznamka_rano"] == "" and s["poznamka_vecer"] == ""
    assert s["rano_hotovo"] is True and s["vecer_hotovo"] is True
    assert s["splneny"] is False and s["zostava"] == 0
    assert s["zajtra_ciel"] == 14             # pondelok už s X = 7
    assert p.get("/plan")["pocet"] == 0       # v nedeľu žiadne pripomienky


def test_health_v_nedelu_ukazuje_ciel_nula(telefon):
    """Voľný deň nie je porucha: /health má dnešok s cieľom 0 (done False je pri ňom v poriadku),
    ok ostáva True a nič nie je zamrazené."""
    p = telefon(NE)
    p.tick_at(at(6, 5, NE))
    h = p.health()
    assert h["ok"] is True and h["frozen"] is False
    assert h["today"] is not None and h["today"]["date"] == NE.isoformat()
    assert h["today"]["goal"] == 0 and h["today"]["done"] is False
    assert (h["today"]["morning"], h["today"]["evening"]) == (0, 0)
    assert h["table"]["ok"] is True            # tabuľka sa zapísala aj vo voľný deň


def test_stav_streak_rata_tyzdne_nie_dni(telefon):
    """Streak v telefóne = počet celých splnených TÝŽDŇOV. Dva hotové týždne (12 tréningových
    dní) sú streak 2, nie 12; jeden nesplnený deň v staršom týždni zhodí streak na 1."""
    p = telefon(PO)
    p.uloz_tyzden(PO - timedelta(days=14))
    p.uloz_tyzden(PO - timedelta(days=7))
    p.tick_at(at(6, 5, PO))
    assert p.stav()["streak"] == 2
    # dnešný (bežiaci) týždeň sa započíta až keď je celý hotový – splnený pondelok nestačí
    p.get("/kliky", poznamka=make_note(PO, MORNING), n=12)
    p.tick_at(at(6, 10, PO))
    s = p.stav()
    assert s["splneny"] is True and s["stav"] == DONE and s["streak"] == 2
    # v staršom týždni vypadne streda → ten týždeň padá a streak ostane len za novší
    stara_streda = PO - timedelta(days=12)
    p.store.save_day(p.store.get_day(stara_streda).copy(morning=0, evening=0))
    p.tick_at(at(6, 15, PO))
    assert p.stav()["streak"] == 1


def test_kliky_a_zmraz_v_utorok(telefon):
    """Hlásenie klikov z appky a zamrazenie musia fungovať aj v deň bez večernej fázy:
    utorkový cieľ sa uzavrie ráno a stav sa medzitým vie prepnúť na zamrazený."""
    p = telefon(UT)
    p.tick_at(at(6, 5, UT))
    p.get("/kliky", poznamka=make_note(UT, MORNING), n=4)
    p.tick_at(at(6, 10, UT))
    s = p.stav()
    assert (s["rano"], s["vecer"], s["spolu"], s["zostava"]) == (4, 0, 4, 2)
    assert s["splneny"] is False and s["stav"] == OPEN and s["rano_hotovo"] is False
    # zamrazenie z appky: stav sa zmení, cieľ ostáva
    p.get("/zmraz", hodnota=1)
    p.tick_at(at(6, 15, UT))
    s = p.stav()
    assert s["zamrazene"] is True and s["stav"] == FROZEN and s["ciel"] == 6
    assert p.get("/plan")["pocet"] == 0        # zamrazené → telefón nič nevytvára
    p.get("/zmraz", hodnota=0)
    p.tick_at(at(6, 20, UT))
    assert p.stav()["zamrazene"] is False and p.stav()["stav"] == OPEN
    # dorazenie cieľa ráno uzavrie celý utorok, večer sa už nič nečaká
    p.get("/kliky", poznamka=make_note(UT, MORNING), n=6, absolute=1)
    p.tick_at(at(6, 25, UT))
    s = p.stav()
    assert (s["rano"], s["spolu"], s["zostava"]) == (6, 6, 0)
    assert s["splneny"] is True and s["stav"] == DONE
    assert s["vecer_due"] is None and p.get("/plan")["pocet"] == 0


def test_odskrtnutie_fazy_mimo_planu_nic_nepripise(telefon):
    """Stará pripomienka odškrtnutá v telefóne (utorok večer, nedeľa ráno) nesmie nič
    pripísať – tie fázy v pláne nie sú."""
    p = telefon(UT)
    p.tick_at(at(6, 5, UT))
    p.get("/hotovo", poznamka=make_note(UT, EVENING))
    p.tick_at(at(6, 10, UT))
    s = p.stav()
    assert (s["rano"], s["vecer"], s["spolu"], s["ciel"]) == (0, 0, 0, 6)
    assert s["splneny"] is False

    n = telefon(NE)
    n.tick_at(at(6, 5, NE))
    n.get("/hotovo", poznamka=make_note(NE, MORNING))
    n.tick_at(at(6, 10, NE))
    s = n.stav()
    assert (s["rano"], s["vecer"], s["ciel"], s["stav"]) == (0, 0, 0, REST)


def test_hotovo_v_utorok_uzavrie_cely_den(telefon):
    """V utorok je ranná fáza celý cieľ (2X), takže odškrtnutie rannej pripomienky
    uzavrie deň – po starom (pol na pol) by ostala visieť druhá polovica."""
    p = telefon(UT)
    p.tick_at(at(6, 5, UT))
    p.get("/hotovo", poznamka=make_note(UT, MORNING))
    p.tick_at(at(6, 10, UT))
    s = p.stav()
    assert (s["rano"], s["spolu"], s["zostava"]) == (6, 6, 0)
    assert s["splneny"] is True and s["stav"] == DONE
    # opakované odškrtnutie tej istej pripomienky nepripíše nič druhýkrát
    p.get("/hotovo", poznamka=make_note(UT, MORNING))
    p.tick_at(at(6, 15, UT))
    assert p.stav()["rano"] == 6


def test_stav_v_sobotu_ohlasuje_volnu_nedelu(telefon):
    """Sobota je posledný tréningový deň týždňa: má len ráno a zajtrajší cieľ je 0."""
    p = telefon(SO)
    p.tick_at(at(6, 5, SO))
    s = p.stav()
    assert (s["den"], s["ciel"], s["rano_ciel"], s["vecer_ciel"]) == ("sobota", 6, 6, 0)
    assert s["vecer_due"] is None and s["poznamka_vecer"] == ""
    assert s["zajtra_ciel"] == 0 and s["volno"] is False
