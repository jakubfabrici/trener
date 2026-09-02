"""Hlasný budík na telefón – kanál, ktorý prebije tichý režim.

Prečo to nejde inak: na iPhone smie zvoniť cez tichý režim a Nerušiť len appka, ktorej
Apple pridelil oprávnenie **Critical Alerts**. Kalendárový event ani pripomienka to
nedokážu – ich upozornenie je obyčajná notifikácia. Preto budík posielame cez appku,
ktorá to oprávnenie má:

- ``pushover``  – https://pushover.net (jednorazovo ~5 €, v appke zapneš Critical Alerts).
                  Priorita 2 = „emergency“: zvoní a opakuje sa, kým to nepotvrdíš.
- ``pushsafer`` – https://www.pushsafer.com, to isté iným poskytovateľom.
- ``webhook``   – ľubovoľné vlastné HTTP volanie (napr. Home Assistant), ak by si chcel.
- ``off``       – vypnuté; ostáva len kalendár a chat.
"""
from __future__ import annotations

import logging

import httpx

log = logging.getLogger("trener.alarm")

PUSHOVER_URL = "https://api.pushover.net/1/messages.json"
PUSHSAFER_URL = "https://www.pushsafer.com/api"


async def send_alarm(cfg, text: str, title: str = "💪 Kliky") -> bool:
    """Best-effort: nikdy nevyhodí výnimku, len zaloguje. Vráti True, ak to prešlo."""
    mode = cfg.alarm_mode
    if mode == "off":
        return False
    try:
        async with httpx.AsyncClient(timeout=15, verify=(mode != "webhook")) as client:
            if mode == "pushover":
                data = {
                    "token": cfg.pushover_token, "user": cfg.pushover_user,
                    "title": title, "message": text,
                    "priority": cfg.alarm_priority, "sound": cfg.alarm_sound,
                }
                if cfg.alarm_priority >= 2:      # emergency: opakuj, kým to nepotvrdím
                    data["retry"] = max(cfg.alarm_retry, 30)
                    data["expire"] = cfg.alarm_expire
                if cfg.pushover_device:
                    data["device"] = cfg.pushover_device
                r = await client.post(PUSHOVER_URL, data=data)
            elif mode == "pushsafer":
                data = {"k": cfg.pushover_user, "t": title, "m": text,
                        "pr": cfg.alarm_priority, "s": cfg.alarm_sound, "cr": 1,
                        "re": max(cfg.alarm_retry, 60), "ex": cfg.alarm_expire}
                if cfg.pushover_device:
                    data["d"] = cfg.pushover_device
                r = await client.post(PUSHSAFER_URL, data=data)
            else:                                 # webhook (napr. Home Assistant)
                r = await client.post(cfg.alarm_webhook_url, json={"message": text, "title": title})
        ok = r.status_code < 400
        log.log(logging.INFO if ok else logging.WARNING,
                "Budík (%s): HTTP %s%s", mode, r.status_code, "" if ok else f" – {r.text[:200]}")
        return ok
    except Exception as e:  # noqa: BLE001 – budík nikdy nesmie zhodiť tick
        log.warning("Budík (%s) zlyhal: %s", mode, e)
        return False
