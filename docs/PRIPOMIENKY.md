# Upozornenia: čo na iPhone naozaj ide a čo nie

## Zvolené riešenie (2. 9. 2026)

**Výlučne Apple Kalendár.** Bot cez CalDAV spravuje iCloud kalendár „Kliky“: dva eventy
denne, každý s piatimi upozorneniami (0/+3/+7/+12/+20 min). Po nahlásení klikov bot
zvyšné upozornenia z eventu odstráni, takže zvonenie prestane. Žiadna ďalšia appka,
žiadny účet navyše, žiadny Home Assistant, žiadne Skratky – iCloud si kalendár
rozsynchronizuje sám.

Hranica, ktorú Apple nedovolí prekročiť: upozornenie z Kalendára (ani z Pripomienok)
**neprebije tichý prepínač**. Cez tichý režim a Nerušiť smie zvoniť len appka
s oprávnením **Critical Alerts** (Home Assistant, Pushover, Pushsafer). Kód pre takýto
kanál v bote je (`alarm.py`, `ALARM_MODE`), ale je **vypnutý** – zapína sa len na výslovné
želanie.

## Krátko

Duolingo píše do iCloud Pripomienok preto, že je to **aplikácia v telefóne**. iOS jej cez
rozhranie EventKit (po odsúhlasení prístupu k Pripomienkam) dovolí zapisovať do lokálnej
databázy Pripomienok a iCloud si to potom sám rozsynchronizuje na všetky zariadenia.

**Server to nedokáže.** Apple nemá verejné API do iCloud Pripomienok:

- CloudKit, cez ktorý Pripomienky od iOS 13 synchronizujú, je pre Pripomienky privátny,
- `caldav.icloud.com` vystavuje len „neupgradované“ staré zoznamy, nové iCloud zoznamy tam
  nie sú (Apple to potvrdzuje v HT102457 – po upgrade sa Pripomienky prestali dať čítať
  cez CalDAV),
- app-specific password na to nestačí, chýba samotné rozhranie.

Preto sú reálne len tri cesty:

| | kde zoznam žije | synchronizuje sa | nastavenie |
|---|---|---|---|
| **A. iOS Skratka** (odporúčané) | **iCloud** (ako Duolingo) | áno, všade automaticky | raz postaviť Skratku + 2–3 automatizácie v telefóne |
| B. CalDAV účet | vlastný server (Radicale) | len tam, kde účet pridáš | pridať účet na každom zariadení |
| C. skript na Macu (EventKit) | iCloud | áno | Mac musí byť zapnutý a odomknutý |

Bot vie A aj B, prepína sa `REMINDERS_MODE` v `/etc/trener/trener.env`
(`shortcuts` / `caldav` / `off`).

## A. Ako funguje režim `shortcuts`

Telefón je ten, kto pripomienky vytvára – bot mu len povie, čo tam má byť, a telefón mu
hlási, čo si spravil. Zoznam „Kliky“ je normálny **iCloud** zoznam, takže ho vidíš na
iPhone, iPade aj Macu bez pridávania čohokoľvek.

```
   bot (LXC 122)                        iPhone (Skratka + automatizácie)
   ─────────────                        ────────────────────────────────
   GET /plan      ──── čo má byť ────►  vytvorí pripomienky v iCloud zozname „Kliky“
   GET /hotovo    ◄─── odškrtol som ──  nájde odškrtnuté a nahlási ich
   GET /uprav     ◄─── premenoval/     nahlási názov a čas otvorených pripomienok
                       posunul som
   GET /zmazane   ◄─── zmazal som ───   (voliteľné) dnes už ju bot neponúkne
```

Bot sa z hlásení učí presne ako predtým: nový čas alebo nový text platí pre všetky ďalšie
dni, odškrtnutie znamená splnenú fázu (zapíše sa do tabuľky aj do chatu).

### Rozhranie (všetko GET, aby to Skratka zvládla jedným krokom)

Základ: `https://kliky.fabrici.xyz`, token je `SHORTCUT_TOKEN` (default = `WAKE_WEBHOOK_TOKEN`).

**`GET /plan?token=…`** – čo má dnes v zozname byť (už bez splnených a zamrazených):

```json
{
  "datum": "2026-09-02",
  "zoznam": "Kliky",
  "zamrazene": false,
  "ciel": 10, "rano": 5, "vecer": 0, "spolu": 5, "zostava": 5,
  "pocet": 1,
  "pripomienky": [
    {
      "nazov": "💪 Večer: 5 klikov",
      "poznamka": "kliky:2026-09-02:evening",
      "cas": "2026-09-02T19:20:00+02:00",
      "cas_kratky": "19:20",
      "faza": "evening",
      "kliky": 5
    }
  ]
}
```

`poznamka` sa zapisuje do poznámky pripomienky – podľa nej bot spozná, čoho sa hlásenie
týka, aj keď pripomienku premenuješ.

**`GET /hotovo?token=…&poznamka=kliky:2026-09-02:evening`** – fáza je splnená.
Opakované hlásenie tej istej pripomienky sa **nepripíše druhýkrát**.

**`GET /uprav?token=…&poznamka=…&nazov=💪 Večer: 5 klikov&cas=19:20`** – používateľ zmenil
názov alebo čas; bot si to zapamätá pre ďalšie dni (`{n}` = číslo klikov).

**`GET /zmazane?token=…&poznamka=…`** – pripomienku si zmazal, dnes ju už neponúkaj.

Odpoveď je vždy JSON, `{"ok": true}` pri hláseniach. Zlý token → HTTP 403.

### Vlastnosti

- Bot do zoznamu nikdy nesiahne sám, takže nič neprepíše „pod rukami“ – zdroj pravdy
  ostáva tabuľka na OMV a chat.
- Keď telefón mlčí (lietadlo, vybitý), bot beží ďalej: tabuľka, chat aj výzvy fungujú,
  pripomienky sa dorovnajú pri najbližšom behu Skratky.
- `/stav` ukazuje, kedy sa telefón naposledy ozval.
