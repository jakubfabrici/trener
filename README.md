# 💪 Virtuálny tréner klikov v2

Telegram bot + **Apple Pripomienky** (cez vlastný CalDAV server Radicale) + **tabuľka na OMV**
ako jediný zdroj pravdy. Beží v samostatnom LXC kontajneri na Proxmoxe (`trener`, 122,
192.168.1.254). Deterministický, bez AI, prežije reštart aj výpadok siete.

## Ako to funguje

- **Deň má dve fázy**: ráno a večer. Cieľ sa delí na polovice (10 → 5 + 5, 11 → 6 + 5).
  Deň je splnený, keď ráno + večer ≥ cieľ. Po splnenom dni cieľ rastie o *prírastok*
  (default 2), po nesplnenom ostáva rovnaký (žiadne tresty).
- **Pripomienky (štýl Duolingo)**: bot každý deň o polnoci vytvorí v appke Pripomienky
  dve pripomienky v zozname „Kliky“ – „💪 Ráno: 5 klikov“ o rannom čase a „💪 Večer: 5 klikov“
  o večernom. Keď je fáza splnená (z chatu, z tabuľky, odkiaľkoľvek), bot ju **odčiarkne**.
  Čo si v pripomienke zmeníš ty, platí ďalej:
  - zmeníš **čas** → nový ranný/večerný čas pre všetky ďalšie dni (aj výzvy v chate),
  - zmeníš **text** → nová šablóna názvu (číslo klikov sa dosádza samo),
  - **odškrtneš** ju → fáza sa berie ako splnená a zapíše sa do tabuľky,
  - **zmažeš** ju → dnes sa už nevytvorí, zajtra normálne.
- **Chat (Telegram)**: kliky hlásiš číslom. „2“ = +2 do aktuálnej fázy (pred večerným časom
  ráno, potom večer); „2 ráno“, „5 večer“, „2 ráno a 3 večer“, „dal som dva ráno“ fungujú
  tiež; „ráno = 5“ alebo `/oprav ráno 5` nastaví presnú hodnotu. Číslo **nikdy** nemení
  nastavenia (to bola chyba v1). Odpoveď vždy ukáže obe vedrá a zostatok.
- **Výzvy v chate**: na začiatku fázy jedna správa a potom **max 3× po 30 min**
  (nastaviteľné), kým fáza nie je splnená. Po reštarte sa staré výzvy nedoháňajú.
  Žiadne HA kritické budíky, žiadne iCloud eventy, žiadne polnočné eseje – len jedna
  správa, ak deň nebol splnený (dá sa vypnúť).
- **Tabuľka** `smb://192.168.1.185/share/Kliky/kliky.xlsx` (OMV): bot ju číta **každé
  2 minúty** a zapisuje do nej. Čo napíšeš do tabuľky, platí (aj spätne, aj počas
  zamrazenia) – nemusíš botovi nič písať.
- **Zamrazenie**: `/zmraz` (alebo tlačidlo, alebo „Zamrazené = ÁNO“ v tabuľke) → žiadne
  výzvy, žiadne nové pripomienky, existujúce sa nechajú tak; tabuľka sa sleduje ďalej.
  Zamrazené dni streak neprerušujú. `/odmraz` pokračuje.

## Tabuľka

Hárok **Kliky** – jeden riadok = deň:

| Dátum | Cieľ | Ráno | Večer | Spolu | Stav | Streak | Poznámka |
|---|---|---|---|---|---|---|---|
| ty aj bot | ty aj bot | ty aj bot | ty aj bot | bot | bot (ty: „zamrazený“) | bot | ty aj bot |

Hárok **Nastavenia**: Zamrazené (ÁNO/NIE), Prírastok cieľa, Ranný čas, Večerný čas,
Max výziev v chate, Rozostup výziev (min), Názov rannej/večernej pripomienky (`{n}` = počet),
Správa pri nesplnenom dni. Hárok **Návod** vysvetľuje to isté v tabuľke.

Pravidlá zápisu: bot robí 3-cestný merge (tabuľka / bot / posledný sync) – ak si bunku
zmenil ty, vyhráva tabuľka; ak sa zmenilo niečo u bota (chat, pripomienka), dopíše to.
Zapisuje atomicky (dočasný súbor + premenovanie) a **nikdy neprepíše súbor, ktorý sa
nedá prečítať** (napr. rozpísaný Excelom). Ak má Excel súbor otvorený so zámkom, bot to
skúsi o 2 minúty znova. Numbers na iPhone/Macu súbor otvorí, ale ukladá kópiu – zmeny
radšej cez Excel, chat alebo Pripomienky.

## Príkazy

`/stav` · `/zmraz` · `/odmraz` · `/ciel N` · `/prirastok N` · `/rano HH:MM` · `/vecer HH:MM`
· `/oprav ráno|večer N` · `/tabulka` · `/sync` · `/stats` · `/help`

## iPhone: zoznam „Kliky“ v appke Pripomienky (jednorazovo)

Radicale je publikované cez Nginx Proxy Manager ako **https://kliky-cal.fabrici.xyz** (HTTPS
je pre Apple povinné). Na iPhone (iOS 18):

1. **Nastavenia → Aplikácie → Pripomienky → Účty Pripomienok → Pridať účet → Iné →
   Pridať účet CalDAV**.
2. Server: `kliky-cal.fabrici.xyz` · Meno: `jakub` · Heslo: (CALDAV_PASSWORD z
   `/etc/trener/trener.env`) · Popis: `Kliky`.
3. Ulož, v účte nechaj zapnuté **Pripomienky**. V appke Pripomienky pribudne zoznam „Kliky“.
4. Odporúčané: **Nastavenia → Aplikácie → Kalendár → Účty kalendára → Načítať nové dáta →
   Načítať: každých 15 minút** (CalDAV nemá push; inak sa zmeny sťahujú len pri otvorení
   appky alebo pri nabíjaní na Wi-Fi).

Pri „Nedá sa overiť účet“: skús v Rozšírených nastaveniach účtu Port 443, Použiť SSL zapnuté
a URL účtu `https://kliky-cal.fabrici.xyz/jakub/`. V logu Radicale je pri prvom pripojení
normálne vidieť `301` na `/.well-known/caldav` a `401` pre anonymný PROPFIND.

Voliteľne: iOS Skratka „Keď sa vypne režim Spánok → Získať obsah z URL
`https://kliky.fabrici.xyz/wake?token=…`“ – ranná fáza začne hneď po zobudení.

## Nasadenie

Na Proxmoxe (`pve`), z checkoutu tejto vetvy:

```bash
VMID=122 IP_CIDR=192.168.1.254/24 bash trener/deploy/bootstrap-lxc.sh
pct exec 122 -- nano /etc/trener/trener.env      # BOT_TOKEN, OWNER_CHAT_ID, SMB_PASSWORD
pct exec 122 -- systemctl restart trener
pct exec 122 -- journalctl -u trener -f
```

Aktualizácia kódu: znova `bootstrap-lxc.sh` (kopíruje zdroje a reinštaluje, konfig nechá).
Presmerovanie NPM po zmene IP: `NEW_IP=192.168.1.254 bash deploy/npm-repoint.sh` v LXC 104.

Kontrola: `curl -s http://192.168.1.254:8790/health` (stav tabuľky, pripomienok, dneška).

## Prevádzka

- Logy: `journalctl -u trener`, `/var/log/trener/trener.log` (rotovaný), Radicale:
  `journalctl -u radicale`.
- Stav: SQLite `/var/lib/trener/trener.db` je len cache + stav výziev/pripomienok; pravda je
  v tabuľke. Zmazať DB = bot si všetko načíta z tabuľky (stratí len históriu výziev).
- Radicale dáta: `/var/lib/radicale/collections/collection-root/jakub/…` (záloha = kópia
  adresára; pri kopírovaní za behu použi `flock /var/lib/radicale/collections/.Radicale.lock`).
- Testy: `python -m pytest trener/tests` (76 testov vrátane integračného proti reálnemu
  Radicale, ak je nainštalované).

Diagnóza pôvodnej verzie: [docs/DIAGNOZA-v1.md](docs/DIAGNOZA-v1.md).
