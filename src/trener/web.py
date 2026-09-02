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


def start_web_server(port: int, token: str | None, on_wake, health) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            u = urlparse(self.path)
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

        def log_message(self, fmt, *args):  # ticho
            pass

    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True, name="web").start()
    log.info("Web: /health a /wake na porte %d (wake %s).", port, "zapnutý" if token else "vypnutý")
    return srv
