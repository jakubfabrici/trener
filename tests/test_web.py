"""Web: /wake token (aj ne-ASCII), /health len detaily pre LAN."""
import json
import socket
import urllib.error
import urllib.request

from trener.web import start_web_server


def _port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


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
