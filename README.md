# 💪 Virtuálny tréner klikov v2

Telegram bot + **Apple Pripomienky** (cez vlastný CalDAV server Radicale) + **tabuľka na OMV**
ako jediný zdroj pravdy. Beží v samostatnom LXC kontajneri na Proxmoxe (`trener`, 122,
192.168.1.254). Deterministický, bez AI, prežije reštart aj výpadok siete.

## Ako to funguje

- **Deň má dve fázy**: ráno a večer. Cieľ sa delí na polovice (10 → 5 + 5, 11 → 6 + 5).
  Deň je splnený, keď ráno + večer ≥ cieľ. Po splnenom dni cieľ rastie o *prírastok*
  (default 2), po nesplnenom ostáva rovnaký (žiadne tresty).
- **Budík v kalendári (Apple Kalendár, zoznam „Kliky“)**: bot každý deň vytvorí dva
  eventy – ranný o `morning_time` a večerný o `evening_time`, každý s **piatimi
  upozorneniami** (v čase eventu a potom +3, +7, +12 a +20 minút), takže zvoní znova,
  kým kliky nespravíš. Len čo ich nahlásiš (chat alebo tabuľka), bot **zvyšné
  upozornenia z eventu odstráni** a event premenuje na „✅ …“. Kalendár „Kliky“ je
  bežný **iCloud** kalendár – objaví sa sám na iPhone, iPade aj Macu, netreba nikde
  pridávať žiadny účet ani appku. Čo v evente zmeníš ty, platí ďalej:
  - posunieš **čas** → nový ranný/večerný čas pre všetky ďalšie dni (aj výzvy v chate),
  - **premenuješ** ho → nová šablóna názvu (číslo klikov sa dosádza samo),
  - **zmažeš** ho → dnes sa už nevytvorí, zajtra normálne.
  O polnoci sa deň uzavrie: event dostane ✅ (splnené), ❌ (nesplnené) alebo ❄️
  (zamrazené) a stíchne – v kalendári tak máš streak mriežku.

  > Poznámka o hlasitosti: upozornenie z Kalendára je bežná notifikácia – zvoní na
  > hlasitosti zvonenia, ale **tichý prepínač neprebije** (to na iPhone smie len appka
  > s oprávnením Critical Alerts od Apple, a Kalendár medzi ne nepatrí). Preto sú
  > upozornenia opakované. Nastav si raz: *Nastavenia → Oznámenia → Kalendár* → Zvuky
  > zapnuté a **Časovo citlivé oznámenia** zapnuté (prejde aj cez Sústredenie/Nerušiť),
  > a *Nastavenia → Zvuky a haptika* → hlasitosť zvonenia hore a **Meniť tlačidlami
  > vypnuté**.

- **Chat (Telegram)**: kliky hlásiš číslom. „2“ = +2 do aktuálnej fázy (pred večerným časom
  ráno, potom večer); „2 ráno“, „5 večer“, „2 ráno a 3 večer“, „dal som dva ráno“, „2x5“
  (dve série po päť = 10), „-2“ / „uber 2“ (oprava dole), „spolu 10“ (dnešný súčet má byť 10),
  „včera večer 5“ (dopísať včerajšok) fungujú tiež; „ráno = 5“ alebo `/oprav ráno 5` nastaví
  presnú hodnotu. Viac čísel bez určenia fázy („3. séria 5“) bot radšej odmietne, než by
  hádal. Číslo **nikdy** nemení nastavenia (to bola chyba v1) – cieľ sa mení cez `/ciel`
  (tlačidlá s hodnotami) alebo `/ciel 10`. Pod každým hlásením sú tlačidlá **↩️ Vrátiť**
  a **🎯 Bol to cieľ, nie kliky**, keby si sa pomýlil. Odpoveď je v riadkoch: ráno, večer,
  dnes a zostatok. Editované správy sa ignorujú (aby sa oprava čísla nezarátala dvakrát).
  Oprava nadol (chat, tabuľka, ↩️) zruší aj odčiarknutie pripomienky.
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

## iPhone: nič nenastavuješ

Kalendár **Kliky** je v tvojom iCloude, takže sa objaví sám v appke Kalendár na všetkých
zariadeniach. Skontroluj len, že je zapnutý: *Kalendár → Kalendáre → Kliky*.

Odporúčané jednorazové nastavenia kvôli hlasitosti (žiadna ďalšia appka):
*Nastavenia → Oznámenia → Kalendár* → Zvuky **zapnuté**, Časovo citlivé oznámenia
**zapnuté**; *Nastavenia → Zvuky a haptika* → hlasitosť zvonenia hore, „Meniť tlačidlami“
vypnuté.

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

Kontrola: `curl -s http://192.168.1.254:8790/health` (stav tabuľky, pripomienok, dneška). Cez
internet (`https://kliky.fabrici.xyz/health`) sa vracia len `{"ok": true}` – detaily vidí iba LAN.
Správy z Telegramu doručené dodatočne po výpadku (z iného dňa) bot nepočíta – odpovie, ako ich
doplniť („včera 5“ alebo tabuľka).

## Prevádzka

- Logy: `journalctl -u trener`, `/var/log/trener/trener.log` (rotovaný), Radicale:
  `journalctl -u radicale`.
- Stav: SQLite `/var/lib/trener/trener.db` je len cache + stav výziev/pripomienok; pravda je
  v tabuľke. Zmazať DB = bot si všetko načíta z tabuľky (stratí len históriu výziev).
- Radicale dáta: `/var/lib/radicale/collections/collection-root/jakub/…` (záloha = kópia
  adresára; pri kopírovaní za behu použi `flock /var/lib/radicale/collections/.Radicale.lock`).
- Testy: `python -m pytest trener/tests` (100 testov: parsovanie, engine, tabuľka, pripomienky,
  end-to-end simulácia dní a integračný test proti reálnemu Radicale, ak je nainštalované).

## Appka pre iPhone

Budíky, ktoré zvonia aj cez tichý režim, nevie spraviť žiadny server — musí to byť
appka na telefóne s AlarmKitom. Zdroják je v [ios/Kliky](ios/Kliky), postup na
zostavenie a nasadenie cez AltStore v [docs/IOS-APPKA.md](docs/IOS-APPKA.md).

Diagnóza pôvodnej verzie: [docs/DIAGNOZA-v1.md](docs/DIAGNOZA-v1.md).
