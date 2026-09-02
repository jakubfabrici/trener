"""Minimálny CalDAV klient pre zoznam úloh (VTODO) – Radicale, Nextcloud, Baikal…

Zámerne bez knižnice python-caldav: jej vyhľadávanie podľa UID v1 opakovane
zlyhávalo na Radicale („object_by_uid … NotFoundError“). Tu poznáme href
každej úlohy a používame ETag (If-Match), takže nikdy neprepíšeme úpravu,
ktorú medzitým spravil telefón.
"""
from __future__ import annotations

import logging
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timezone
from urllib.parse import quote, unquote, urljoin, urlparse

import httpx
from icalendar import Calendar, Todo, vDatetime, vText

log = logging.getLogger("trener.caldav")

NS = {"D": "DAV:", "C": "urn:ietf:params:xml:ns:caldav"}
PRODID = "-//trener-klikov//v2//SK"


class CalDavError(Exception):
    pass


class Conflict(CalDavError):
    """412 – úloha sa na serveri medzitým zmenila."""


@dataclass
class TodoItem:
    href: str
    etag: str
    uid: str
    summary: str
    due: datetime | None          # aware datetime (alebo None)
    completed: bool
    cal: Calendar                 # celý VCALENDAR (zachovávame cudzie vlastnosti)

    @property
    def vtodo(self) -> Todo:
        for c in self.cal.walk("VTODO"):
            return c
        raise CalDavError("VCALENDAR bez VTODO")


def _fmt_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def build_todo_ics(uid: str, summary: str, due: datetime, alarm: bool = True) -> bytes:
    """Nový VTODO: DUE v UTC + absolútny VALARM v čase DUE (iOS zobrazí upozornenie)."""
    now = datetime.now(timezone.utc)
    cal = Calendar()
    cal.add("VERSION", "2.0")
    cal.add("PRODID", PRODID)
    t = Todo()
    t.add("UID", uid)
    t.add("DTSTAMP", now)
    t.add("CREATED", now)
    t.add("LAST-MODIFIED", now)
    t.add("SUMMARY", summary)
    t.add("DTSTART", due.astimezone(timezone.utc))   # Apple píše DTSTART = DUE
    t.add("DUE", due.astimezone(timezone.utc))
    t.add("STATUS", "NEEDS-ACTION")
    t.add("PERCENT-COMPLETE", 0)
    t.add("SEQUENCE", 0)
    if alarm:
        t.add_component(_alarm(due))
    cal.add_component(t)
    return cal.to_ical()


def _alarm(due: datetime):
    from icalendar import Alarm
    a = Alarm()
    a.add("ACTION", "DISPLAY")
    a.add("DESCRIPTION", "Reminder")
    a.add("TRIGGER", due.astimezone(timezone.utc))   # VALUE=DATE-TIME – absolútny čas
    a.add("X-WR-ALARMUID", str(uuid.uuid4()).upper())
    a.add("UID", str(uuid.uuid4()).upper())
    return a


def parse_item(href: str, etag: str, data: str | bytes) -> TodoItem | None:
    try:
        cal = Calendar.from_ical(data)
    except Exception as e:  # noqa: BLE001
        log.warning("CalDAV: neviem rozparsovať %s: %s", href, e)
        return None
    todos = list(cal.walk("VTODO"))
    if not todos:
        return None
    t = todos[0]
    due = None
    d = t.get("DUE") or t.get("DTSTART")
    if d is not None:
        dt = d.dt
        if isinstance(dt, datetime):
            due = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        elif isinstance(dt, date):
            due = None
    status = str(t.get("STATUS", "")).upper()
    pct = t.get("PERCENT-COMPLETE")
    completed = status == "COMPLETED" or t.get("COMPLETED") is not None or (
        pct is not None and int(pct) >= 100)
    return TodoItem(href, etag, str(t.get("UID", "")), str(t.get("SUMMARY", "")), due, completed, cal)


def apply_changes(item: TodoItem, *, summary: str | None = None, due: datetime | None = None,
                  complete: bool | None = None) -> bytes:
    """Upraví VTODO (na mieste v item.cal) a vráti serializovaný VCALENDAR."""
    t = item.vtodo
    now = datetime.now(timezone.utc)

    def _set(key: str, value) -> None:
        if key in t:
            del t[key]
        t.add(key, value)

    if summary is not None and summary != str(t.get("SUMMARY", "")):
        _set("SUMMARY", summary)
    if due is not None:
        _set("DUE", due.astimezone(timezone.utc))
        if "DTSTART" in t:
            _set("DTSTART", due.astimezone(timezone.utc))   # Apple drží DTSTART = DUE
        # absolútne alarmy posuň na nový čas
        for a in t.walk("VALARM"):
            trig = a.get("TRIGGER")
            if trig is not None and isinstance(getattr(trig, "dt", None), datetime):
                del a["TRIGGER"]
                a.add("TRIGGER", due.astimezone(timezone.utc))
    if complete is True:
        _set("STATUS", "COMPLETED")
        _set("COMPLETED", now)
        _set("PERCENT-COMPLETE", 100)
        # VALARM ostáva – Apple ho pri dokončení tiež nemaže (iOS dokončené neupozorňuje)
    elif complete is False:
        _set("STATUS", "NEEDS-ACTION")
        for k in ("COMPLETED", "PERCENT-COMPLETE"):
            if k in t:
                del t[k]
    seq = t.get("SEQUENCE")
    try:
        seq = int(seq) if seq is not None else 0
    except (TypeError, ValueError):
        seq = 0
    _set("SEQUENCE", seq + 1)
    _set("LAST-MODIFIED", now)
    _set("DTSTAMP", now)
    return item.cal.to_ical()


class TodoList:
    """Jeden zoznam úloh (kolekcia) na CalDAV serveri."""

    def __init__(self, base_url: str, username: str, password: str, list_name: str,
                 timeout: float = 20.0):
        self.base_url = base_url if base_url.endswith("/") else base_url + "/"
        self.username = username
        self.list_name = list_name
        self.client = httpx.Client(auth=(username, password), timeout=timeout,
                                   headers={"User-Agent": "trener-klikov/2"}, follow_redirects=True)
        self.collection_url: str | None = None

    def close(self) -> None:
        self.client.close()

    # ── HTTP pomôcky ────────────────────────────────────────────────────────
    def _req(self, method: str, url: str, **kw) -> httpx.Response:
        r = self.client.request(method, url, **kw)
        if r.status_code == 412:
            raise Conflict(f"{method} {url}: 412 Precondition Failed")
        if r.status_code >= 400 and r.status_code not in (404,):
            raise CalDavError(f"{method} {url}: HTTP {r.status_code} {r.text[:200]}")
        return r

    def _abs(self, href: str) -> str:
        return urljoin(self.base_url, href)

    # ── kolekcia ────────────────────────────────────────────────────────────
    def principal_url(self) -> str:
        # Radicale: /<user>/ ; iné servery vrátia current-user-principal
        body = ('<?xml version="1.0"?><D:propfind xmlns:D="DAV:"><D:prop>'
                '<D:current-user-principal/></D:prop></D:propfind>')
        r = self._req("PROPFIND", self.base_url, content=body, headers={"Depth": "0"})
        try:
            root = ET.fromstring(r.text)
            href = root.find(".//D:current-user-principal/D:href", NS)
            if href is not None and href.text:
                return self._abs(href.text)
        except ET.ParseError:
            pass
        return self._abs(quote(self.username) + "/")

    def calendar_home(self, principal: str) -> str:
        body = ('<?xml version="1.0"?><D:propfind xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">'
                '<D:prop><C:calendar-home-set/></D:prop></D:propfind>')
        r = self._req("PROPFIND", principal, content=body, headers={"Depth": "0"})
        try:
            root = ET.fromstring(r.text)
            href = root.find(".//C:calendar-home-set/D:href", NS)
            if href is not None and href.text:
                return self._abs(href.text)
        except ET.ParseError:
            pass
        return principal

    def find_collection(self) -> str | None:
        home = self.calendar_home(self.principal_url())
        body = ('<?xml version="1.0"?><D:propfind xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">'
                '<D:prop><D:displayname/><D:resourcetype/><C:supported-calendar-component-set/></D:prop>'
                '</D:propfind>')
        r = self._req("PROPFIND", home, content=body, headers={"Depth": "1"})
        root = ET.fromstring(r.text)
        fallback = None
        for resp in root.findall("D:response", NS):
            href = resp.find("D:href", NS)
            rt = resp.find(".//D:resourcetype", NS)
            if href is None or rt is None or rt.find("C:calendar", NS) is None:
                continue
            name = resp.find(".//D:displayname", NS)
            comps = [c.get("name") for c in resp.findall(".//C:supported-calendar-component-set/C:comp", NS)]
            if name is not None and (name.text or "").strip() == self.list_name:
                if not comps or "VTODO" in comps:
                    return self._abs(href.text)
                fallback = fallback or self._abs(href.text)
        return fallback

    def ensure_collection(self) -> str:
        if self.collection_url:
            return self.collection_url
        url = self.find_collection()
        if url is None:
            home = self.calendar_home(self.principal_url())
            url = urljoin(home, str(uuid.uuid4()) + "/")
            body = ('<?xml version="1.0" encoding="utf-8"?>'
                    '<C:mkcalendar xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav"><D:set><D:prop>'
                    f'<D:displayname>{self.list_name}</D:displayname>'
                    '<C:supported-calendar-component-set><C:comp name="VTODO"/></C:supported-calendar-component-set>'
                    '</D:prop></D:set></C:mkcalendar>')
            r = self.client.request("MKCALENDAR", url, content=body,
                                    headers={"Content-Type": "application/xml; charset=utf-8"})
            if r.status_code not in (200, 201):
                raise CalDavError(f"MKCALENDAR zlyhal: HTTP {r.status_code} {r.text[:200]}")
            log.info("CalDAV: vytvorený zoznam '%s' (%s)", self.list_name, url)
        self.collection_url = url
        return url

    def check(self) -> str:
        return self.ensure_collection()

    # ── úlohy ───────────────────────────────────────────────────────────────
    def list(self) -> list[TodoItem]:
        col = self.ensure_collection()
        body = ('<?xml version="1.0" encoding="utf-8"?>'
                '<C:calendar-query xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">'
                '<D:prop><D:getetag/><C:calendar-data/></D:prop>'
                '<C:filter><C:comp-filter name="VCALENDAR"><C:comp-filter name="VTODO"/></C:comp-filter></C:filter>'
                '</C:calendar-query>')
        r = self._req("REPORT", col, content=body,
                      headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"})
        out: list[TodoItem] = []
        root = ET.fromstring(r.text)
        for resp in root.findall("D:response", NS):
            href = resp.find("D:href", NS)
            etag = resp.find(".//D:getetag", NS)
            data = resp.find(".//C:calendar-data", NS)
            if href is None or data is None or not data.text:
                continue
            item = parse_item(self._abs(href.text), (etag.text or "") if etag is not None else "", data.text)
            if item:
                out.append(item)
        return out

    def get(self, href: str) -> TodoItem | None:
        r = self.client.get(href)
        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            raise CalDavError(f"GET {href}: HTTP {r.status_code}")
        return parse_item(href, r.headers.get("ETag", ""), r.content)

    def create(self, uid: str, ics: bytes) -> TodoItem:
        col = self.ensure_collection()
        href = urljoin(col, quote(uid) + ".ics")
        r = self.client.put(href, content=ics, headers={"Content-Type": "text/calendar; charset=utf-8",
                                                          "If-None-Match": "*"})
        if r.status_code == 412:
            raise Conflict(f"PUT {href}: už existuje")
        if r.status_code not in (200, 201, 204):
            raise CalDavError(f"PUT {href}: HTTP {r.status_code} {r.text[:200]}")
        etag = r.headers.get("ETag", "")
        item = parse_item(href, etag, ics)
        if not etag:
            fresh = self.get(href)
            if fresh:
                item = fresh
        assert item is not None
        return item

    def put(self, item: TodoItem, ics: bytes) -> TodoItem:
        headers = {"Content-Type": "text/calendar; charset=utf-8"}
        if item.etag:
            headers["If-Match"] = item.etag
        r = self.client.put(item.href, content=ics, headers=headers)
        if r.status_code == 412:
            raise Conflict(f"PUT {item.href}: 412 (zmenené na serveri)")
        if r.status_code not in (200, 201, 204):
            raise CalDavError(f"PUT {item.href}: HTTP {r.status_code} {r.text[:200]}")
        etag = r.headers.get("ETag", "")
        new = parse_item(item.href, etag, ics)
        if not etag:
            fresh = self.get(item.href)
            if fresh:
                new = fresh
        assert new is not None
        return new

    def delete(self, item: TodoItem) -> None:
        headers = {"If-Match": item.etag} if item.etag else {}
        r = self.client.delete(item.href, headers=headers)
        if r.status_code == 412:
            raise Conflict(f"DELETE {item.href}: 412")
        if r.status_code not in (200, 202, 204, 404):
            raise CalDavError(f"DELETE {item.href}: HTTP {r.status_code}")


def href_name(href: str) -> str:
    return unquote(urlparse(href).path.rsplit("/", 1)[-1])
