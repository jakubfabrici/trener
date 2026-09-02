"""Mini HTTP server: GET /health (JSON stav) a GET /wake?token=… (iOS Skratka po zobudení)."""
from __future__ import annotations

import hmac
import ipaddress
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

log = logging.getLogger("trener.web")


def _client_is_local(handler: BaseHTTPRequestHandler) -> bool:
    """Priamy peer musí byť z LAN; za reverse proxy (NPM) platí POSLEDNÝ záznam v
    X-Forwarded-For – ten pridáva proxy, predchádzajúce si môže klient podvrhnúť."""
    try:
        peer = ipaddress.ip_address(handler.client_address[0])
    except ValueError:
        return False
    if not peer.is_private:
        return False
    xff = handler.headers.get("X-Forwarded-For", "")
    if not xff:
        return True
    try:
        return ipaddress.ip_address(xff.split(",")[-1].strip()).is_private
    except ValueError:
        return False


def start_web_server(port: int, token: str | None, on_wake, health, shortcut_token: str | None = None,
                     plan=None, report=None) -> ThreadingHTTPServer:
    """`plan()` vráti dnešný plán pripomienok pre iOS Skratku, `report(dict)` prijme,
    čo Skratka nahlásila (odškrtnutie, úprava, zmazanie). Obe sa volajú z vlákna
    servera – musia byť bezpečné (v app.py idú cez snímku a frontu)."""

    def _json(handler, code, data):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        handler.send_response(code)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    class Handler(BaseHTTPRequestHandler):
        def _shortcut_ok(self, q) -> bool:
            got = q.get("token", [""])[0]
            return bool(shortcut_token) and hmac.compare_digest(got.encode("utf-8"),
                                                                shortcut_token.encode("utf-8"))

        def do_GET(self):  # noqa: N802
            u = urlparse(self.path)
            if u.path in ("/plan", "/hotovo", "/uprav", "/zmazane", "/stav", "/kliky", "/zmraz"):
                q = parse_qs(u.query)
                if not self._shortcut_ok(q):
                    self._plain(403, "Forbidden")
                    return
                if u.path in ("/plan", "/stav"):
                    if plan is None:
                        _json(self, 200, {"chyba": "vypnuté"})
                        return
                    data = plan()
                    _json(self, 200, data.get("stav", data) if u.path == "/stav" else data)
                    return
                if report is None:
                    _json(self, 200, {"ok": False, "chyba": "vypnuté"})
                    return
                kind = {"/hotovo": "done", "/uprav": "edit", "/zmazane": "deleted",
                        "/kliky": "reps", "/zmraz": "freeze"}[u.path]
                one = lambda k: q.get(k, [""])[0]  # noqa: E731
                _json(self, 200, report({"kind": kind, "poznamka": one("poznamka"),
                                         "nazov": one("nazov"), "cas": one("cas"),
                                         "n": one("n"), "absolute": one("absolute") in ("1", "true", "ano"),
                                         "faza": one("faza"), "hodnota": one("hodnota")}))
                return
            if u.path == "/health":
                data = health() if _client_is_local(self) else {"ok": True}   # z internetu len „žijem“
                body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if u.path == "/wake":
                got = parse_qs(u.query).get("token", [""])[0]
                if token and hmac.compare_digest(got.encode("utf-8"), token.encode("utf-8")):
                    on_wake()
                    self._plain(200, "OK")
                else:
                    self._plain(403, "Forbidden")
                return
            self._plain(404, "Not found")

        def _plain(self, code: int, text: str) -> None:
            body = text.encode()
            self.send_response(code)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):  # noqa: N802 – Skratka vie poslať aj POST
            self.do_GET()

        def log_message(self, fmt, *args):  # ticho
            pass

    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True, name="web").start()
    log.info("Web: /health a /wake na porte %d (wake %s).", port, "zapnutý" if token else "vypnutý")
    return srv
