"""Integračný test proti skutočnému Radicale (ak je nainštalované): overuje, že náš
CalDAV klient vie vytvoriť zoznam, vytvoriť/čítať/upraviť/odčiarknuť/zmazať VTODO
a že ETag (If-Match) chráni pred prepísaním. Presne to v1 rozbíjalo."""
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from trener.caldav_todo import Conflict, TodoList, apply_changes, build_todo_ics

radicale = pytest.importorskip("radicale")


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture(scope="module")
def server():
    tmp = Path(tempfile.mkdtemp())
    port = _free_port()
    (tmp / "users").write_text("jakub:tajne\n")
    (tmp / "config").write_text(f"""[server]
hosts = 127.0.0.1:{port}
[auth]
type = htpasswd
htpasswd_filename = {tmp / 'users'}
htpasswd_encryption = plain
[storage]
filesystem_folder = {tmp / 'collections'}
[logging]
level = warning
""")
    proc = subprocess.Popen([sys.executable, "-m", "radicale", "--config", str(tmp / "config")],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    yield f"http://127.0.0.1:{port}/"
    proc.terminate()
    proc.wait(timeout=5)
    shutil.rmtree(tmp, ignore_errors=True)


def test_full_lifecycle(server):
    tl = TodoList(server, "jakub", "tajne", "Kliky")
    url = tl.check()
    assert url.startswith(server + "jakub/")
    # druhý klient nájde ten istý zoznam (nevytvorí duplikát)
    tl2 = TodoList(server, "jakub", "tajne", "Kliky")
    assert tl2.check() == url

    due = datetime(2026, 9, 2, 5, 0, tzinfo=timezone.utc)
    item = tl.create("kliky-test-1", build_todo_ics("kliky-test-1", "💪 Ráno: 5 klikov", due))
    assert item.etag and item.uid == "kliky-test-1"
    items = tl.list()
    assert [i.uid for i in items] == ["kliky-test-1"]
    assert items[0].summary == "💪 Ráno: 5 klikov" and items[0].due == due and not items[0].completed

    # úprava + odčiarknutie
    upd = tl.put(items[0], apply_changes(items[0], summary="💪 Ráno: 3 klikov"))
    assert upd.etag != items[0].etag
    done = tl.put(upd, apply_changes(upd, complete=True))
    fresh = tl.get(done.href)
    assert fresh.completed and fresh.summary == "💪 Ráno: 3 klikov"

    # zastaraný ETag → Conflict, nič sa neprepíše
    with pytest.raises(Conflict):
        tl.put(items[0], apply_changes(items[0], summary="prepis"))
    assert tl.get(done.href).summary == "💪 Ráno: 3 klikov"

    # duplicitné UID → Conflict
    with pytest.raises(Conflict):
        tl.create("kliky-test-1", build_todo_ics("kliky-test-1", "dup", due))

    tl.delete(tl.get(done.href))
    assert tl.list() == []
    tl.close(); tl2.close()
