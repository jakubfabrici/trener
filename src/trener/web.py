"""Mini HTTP server: GET /health (JSON stav) a GET /wake?token=… (iOS Skratka po zobudení)."""
from __future__ import annotations

import hmac
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

log = logging.getLogger("trener.web")


def start_web_server(port: int, token: str | None, on_wake, health) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            u = urlparse(self.path)
            if u.path == "/health":
                body = json.dumps(health(), ensure_ascii=False, default=str).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if u.path == "/wake":
                got = parse_qs(u.query).get("token", [""])[0]
                if token and hmac.compare_digest(got, token):
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
