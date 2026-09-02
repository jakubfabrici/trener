"""Prístup k súboru tabuľky: lokálny disk (testy/vývoj) alebo SMB share na OMV.

Zápis je vždy atomický: dočasný súbor v tom istom adresári + premenovanie.
Pred zápisom sa overí, že sa súbor medzitým nezmenil (optimistický zámok cez
mtime + veľkosť) – ak áno, vyhodí sa Conflict a volajúci si súbor načíta znova.
"""
from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

log = logging.getLogger("trener.table.io")


class Conflict(Exception):
    """Súbor sa zmenil odkedy sme ho čítali."""


@dataclass(frozen=True)
class Stamp:
    mtime: float
    size: int

    def same(self, other: "Stamp | None") -> bool:
        return other is not None and abs(self.mtime - other.mtime) < 0.01 and self.size == other.size


class LocalBackend:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def describe(self) -> str:
        return str(self.path)

    def read(self) -> tuple[bytes, Stamp] | None:
        if not self.path.exists():
            return None
        st = self.path.stat()
        return self.path.read_bytes(), Stamp(st.st_mtime, st.st_size)

    def stamp(self) -> Stamp | None:
        if not self.path.exists():
            return None
        st = self.path.stat()
        return Stamp(st.st_mtime, st.st_size)

    def write(self, data: bytes, expected: Stamp | None) -> Stamp:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        cur = self.stamp()
        if (expected is None) != (cur is None) or (expected is not None and not expected.same(cur)):
            raise Conflict("súbor sa medzitým zmenil")
        tmp = self.path.with_name(f".{self.path.name}.{secrets.token_hex(4)}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, self.path)
        st = self.path.stat()
        return Stamp(st.st_mtime, st.st_size)


class SmbBackend:
    """Súbor na Samba share cez smbprotocol (bez mountu, čisto v Pythone)."""

    def __init__(self, server: str, share: str, path: str, username: str, password: str,
                 port: int = 445, timeout: int = 20):
        self.server, self.share, self.username, self.password = server, share, username, password
        self.port, self.timeout = port, timeout
        rel = PureWindowsPath(path.replace("/", "\\"))
        self.unc = f"\\\\{server}\\{share}\\{rel}"
        self.dir_unc = f"\\\\{server}\\{share}\\{rel.parent}" if str(rel.parent) != "." else f"\\\\{server}\\{share}"
        self.name = rel.name

    def describe(self) -> str:
        return f"smb://{self.server}/{self.share}/{self.unc.split(chr(92) + self.share + chr(92), 1)[1].replace(chr(92), '/')}"

    # smbclient importujeme lenivo, aby testy bez smbprotocol bežali
    def _cred(self) -> dict:
        return {"username": self.username, "password": self.password, "port": self.port,
                "connection_timeout": self.timeout}

    def _reset(self) -> None:
        try:
            import smbclient
            smbclient.reset_connection_cache()
        except Exception:  # noqa: BLE001
            pass

    def stamp(self) -> Stamp | None:
        import smbclient
        try:
            st = smbclient.stat(self.unc, **self._cred())
        except FileNotFoundError:
            return None
        except OSError as e:
            if getattr(e, "errno", None) == 2:
                return None
            self._reset()
            raise
        return Stamp(st.st_mtime, st.st_size)

    def read(self) -> tuple[bytes, Stamp] | None:
        import smbclient
        try:
            stamp = self.stamp()
            if stamp is None:
                return None
            with smbclient.open_file(self.unc, mode="rb", share_access="rwd", **self._cred()) as f:
                data = f.read()
            # ak sa počas čítania menil, vezmi stamp znova (konzistentnejší na konflikty)
            after = self.stamp()
            return data, (after or stamp)
        except Exception:
            self._reset()
            raise

    def write(self, data: bytes, expected: Stamp | None) -> Stamp:
        import smbclient
        try:
            smbclient.makedirs(self.dir_unc, exist_ok=True, **self._cred())
            cur = self.stamp()
            if (expected is None) != (cur is None) or (expected is not None and not expected.same(cur)):
                raise Conflict("súbor sa medzitým zmenil")
            tmp = f"{self.dir_unc}\\.{self.name}.{secrets.token_hex(4)}.tmp"
            try:
                with smbclient.open_file(tmp, mode="wb", **self._cred()) as f:
                    f.write(data)
                smbclient.replace(tmp, self.unc, **self._cred())
            except Exception:
                try:
                    smbclient.remove(tmp, **self._cred())
                except Exception:  # noqa: BLE001
                    pass
                raise
            st = smbclient.stat(self.unc, **self._cred())
            return Stamp(st.st_mtime, st.st_size)
        except Conflict:
            raise
        except Exception:
            self._reset()
            raise


def make_backend(cfg) -> LocalBackend | SmbBackend:
    if cfg.table_backend == "smb":
        if not cfg.smb_username:
            raise SystemExit("TABLE_BACKEND=smb vyžaduje SMB_USERNAME a SMB_PASSWORD.")
        return SmbBackend(cfg.smb_server, cfg.smb_share, cfg.smb_path, cfg.smb_username, cfg.smb_password)
    return LocalBackend(cfg.local_table_path)
