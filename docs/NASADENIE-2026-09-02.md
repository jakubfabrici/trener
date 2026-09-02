# Záznam o nasadení v2 (2. 9. 2026)

Čo sa zmenilo v homelabe a prečo – aby sa to dalo kedykoľvek dohľadať alebo vrátiť.

## Nový kontajner LXC 122 `trener` (192.168.1.254)

- Debian 12 (`debian-12-standard_12.7`), 1 vCPU, 512 MB, 4 GB na `local-lvm`, unprivileged,
  `nesting=1`, `onboot=1`, TZ Europe/Bratislava. IP `.254` je mimo DHCP poolu UniFi
  (`.10–.249`); `.250–.253` už používajú mcp/zssk/weby/tuke-vpn.
- Služby: `trener.service` (`/opt/trener`, user `trener`, env `/etc/trener/trener.env`),
  `radicale.service` (Radicale 3.5.x z pipu v `/opt/radicale`, user `radicale`, config
  `/etc/radicale/config`, dáta `/var/lib/radicale/collections`).
- Radicale kolekcia „Kliky“ (`/jakub/f572067a-a506-11f1-8000-bc2411acc9ff/`) bola
  **prenesená z LXC 106** aj s dvomi splnenými pripomienkami z 31. 8.; používateľ `jakub`
  a heslo ostali rovnaké ako v v1, takže prípadné existujúce CalDAV konto v iPhone funguje
  bez zmeny. Otvorené položky z 1. 9. boli zmazané.
- Web `/health` a `/wake` na porte 8790 (rovnaký wake token ako v v1 – iOS Skratka netreba meniť).

## Proxmox host `pve` – opravený `/dev/null`

Pri vytváraní kontajnera sa ukázalo, že **na hoste bol `/dev/null` obyčajný súbor**
(138 B, `-rwxr-xr-x`), nie znakové zariadenie. Unprivileged LXC si `/dev/null` bind-mountuje
z hosta, takže každý *novo štartovaný* kontajner mal rozbitý `/dev/null`, systemd v ňom
nedobehol a sieť nenabehla (bežiace kontajnery 120/121 to nepostihlo, štartovali skôr).
Oprava: `rm -f /dev/null && mknod -m 666 /dev/null c 1 3`. Odporúčam pri najbližšom
okne skontrolovať, čo ho prepísalo (`/root/.bash_history`, cron, skripty používajúce
`> /dev/null` po `rm`).

## Nginx Proxy Manager (LXC 104)

- Proxy host 27 `kliky.fabrici.xyz` → `192.168.1.254:8790` (predtým `.115`, mŕtve).
- Proxy host 28 `kliky-cal.fabrici.xyz` → `192.168.1.254:5232` (Radicale).
- Zmenené priamo v `/data/database.sqlite` (záloha `database.sqlite.bak-*-trener`) a
  v `/data/nginx/proxy_host/2{7,8}.conf` (`.bak-trener`), `nginx -s reload`.
  Skript: `deploy/npm-repoint.sh`.

## OpenMediaVault (VM 110)

- Priečinok `Kliky/` v SMB share `share` (`/export/share/share/media/Kliky`), vlastník
  `jakub:webdav-users`, `2775`, ACL zdedené. Tabuľka `kliky.xlsx` bola nasadená so
  seedom: 31. 8. splnené 4 + 6 / 10, 1. 9. zamrazený (bot v1 nebežal), 2. 9. cieľ 12.
- Bot pristupuje cez SMB ako `jakub` (heslo z `/root/.smbcred` na pve, ktoré už používa
  CIFS mount `/mnt/media`).

## FABRICI-HOME-SERVER (LXC 106)

- `~/hhagent/pushup-bot` → `pushup-bot.v1-archiv-2026-09-02`, `~/hhagent/radicale` →
  `radicale.v1-archiv-2026-09-02`, plus `TRENER-KLIKOV-PRESUNUTY.md` s vysvetlením.
  Nič sa tam nesmie spúšťať (rovnaký Telegram token = Conflict na getUpdates).

## Home Assistant

- Automatizácia `kliky_alarm_webhook` (kritický budík) ostala, ale v2 ju **nevolá**
  (`HA_ALARM_WEBHOOK_URL` prázdne). Dá sa kedykoľvek zapnúť v env.

## Čo ostáva na používateľovi

1. Pridať/overiť CalDAV konto v iPhone (návod v README) – v logu Radicale v1 sa iPhone
   ako CalDAV klient nikdy neobjavil.
2. Voliteľne nastaviť Fetch na 15 min a Skratku po zobudení.
3. Excel na Macu: otvoriť `smb://192.168.1.185/share/Kliky/kliky.xlsx` a písať kliky
   priamo tam, ak sa nechce s chatom.
