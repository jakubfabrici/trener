# Prečo v1 (pushup-bot na LXC 106) fungoval zle – nález z logov a kódu

Zdroj: `/home/hhagent/pushup-bot/` na FABRICI-HOME-SERVER (LXC 106), `trener.log`,
`start-debug.log`, `trener.db`, Radicale log, NPM databáza, HA automatizácie. Stav k 2. 9. 2026.

## 1. Bot vôbec nebežal (od 1. 9. 05:01 UTC)

- `pushup-bot.service` existoval len ako súbor v projekte, **nikdy nebol nainštalovaný**
  do systemd. Bot aj Radicale sa púšťali ručne z agentovej relácie (žiadny proces,
  žiadna jednotka, `ps` prázdny).
- Posledný záznam: `2026-09-01 05:01:30 Application is stopping` a `Stopping Radicale`.
  Kontajner 106 sa o 08:08 reštartoval a nič sa nespustilo.
- Dôsledok: žiadne pripomienky, žiadne odpovede, iPhone CalDAV konto (kliky-cal.fabrici.xyz →
  192.168.1.115:5232) smerovalo na mŕtvy port.

## 2. Číslo v chate znamenalo raz kliky, raz nastavenie (skrytý stav)

- `on_text` rozhodoval podľa `STATE["mode"]` (normal / menu / value / setup_*). Bare číslo
  v zabudnutom menu = nová hodnota nastavenia.
- 31. 8. 07:51–07:52 presne toto nastalo: **„2“ prepísalo denný cieľ 10 → 2**, deň sa
  okamžite „splnil“ (`goal: 2, done: 2, status: done`), bot gratuloval, kalendár dostal
  „✅ 2 klikov“. Agent to potom ručne opravil v DB a pridal 3-minútový timeout menu –
  náplasť, nie riešenie.
- Prirodzené vety („dal som dva ráno“) končili ako `NAN` → „Nerozumiem“, takže ranné
  výzvy bežali ďalej (spam sa zastavil len po *platnom* hlásení).

## 3. Žiadne vedrá ráno/večer

- DB mala len `done` za deň; „Ráno: 5“ / „Večer: 5“ v kalendári sa iba dopočítavali
  (`ceil(goal/2)`, `goal - done`). Hlásenie „2 ráno“ sa nedalo ani zapísať.

## 4. Štyri kanály upozornení naraz („nezmyselné hlásenia“)

Za deň mohlo prísť: 1 ranná správa + 3 výzvy po 15 min, 1 večerná + 3 výzvy, polnočný
verdikt, **HA kritický budík** (obchádza Nerušiť) ráno aj večer, 2 iCloud eventy s alarmom
a 2 Radicale pripomienky s alarmom. Navyše každý reštart bota (na 31. 8. ich bolo ~10)
znovu vytváral eventy → duplicity v kalendári.

## 5. Odčiarkovanie pripomienok padalo

- `object_by_uid` z knižnice python-caldav na Radicale hlásil `NotFoundError` (hľadanie bez
  comp-filtra), fallback raz prešiel, o 22:00 zlyhal 3× po sebe s prázdnou chybou →
  pripomienky ostali neodčiarknuté.

## 6. iPhone sa na CalDAV nikdy nepripojil

- Radicale log obsahuje len Safari (web UI) z iPhonu, žiadneho CalDAV klienta
  (`iOS/…`, `dataaccessd`). Konto v Nastaveniach pravdepodobne nebolo dokončené alebo
  zlyhalo – návod na to je v README v2.

## 7. Ďalšie

- Telegram `NetworkError: Bad Gateway` (1. 9. 01:13) sa logoval ako ERROR so stack trace –
  šum, nie príčina.
- Wake signál o 07:00:04 bol ignorovaný, lebo okno končilo presne o `morning_time` (07:00).
- Radicale heslo v plaintexte v `users` súbore, sekrety v `.env` v home agenta.
- Žiadna možnosť pauzy (zamrazenia), žiadna tabuľka, dáta len v lokálnom SQLite.

## Ako to rieši v2

| Problém | v2 |
|---|---|
| nebežal | vlastný LXC 122, systemd `trener.service` + `radicale.service`, `Restart=always` |
| skrytý stav | číslo je **vždy** hlásenie; nastavenia len príkazmi s argumentom (`/ciel 12`) |
| ráno/večer | dve vedrá, cieľ 10 = 5 + 5; „2 ráno“, „5 večer“, „ráno = 5“ |
| spam | max 3 výzvy po 30 min na fázu, HA budík vypnutý, iCloud eventy zrušené |
| pripomienky | vlastný CalDAV klient s href + ETag, Duolingo správanie (učí sa čas/text, odčiarkuje) |
| zdroj pravdy | tabuľka `kliky.xlsx` na OMV, číta sa každé 2 min, 3-cestný merge |
| pauza | `/zmraz` / `/odmraz` / tlačidlo / bunka „Zamrazené“ v tabuľke |
| výpadok | tick-engine: po reštarte nedoháňa staré výzvy, chýbajúce dni označí ako zamrazené |
