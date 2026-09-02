# Kliky — appka pre iPhone

Appka rieši jedinú vec, ktorú server nikdy vyriešiť nemohol: **hlasný budík ráno
a večer, ktorý zazvoní aj cez tichý prepínač a cez Focus.**

Prečo to nešlo inak — v skratke, aby sme sa k tomu už nevracali:

| Cesta | Prejde cez tichý režim? |
|---|---|
| Telegram správa | nie |
| Kalendárová udalosť s upozornením | nie |
| Pripomienka (Reminders) | nie |
| Bežná notifikácia | nie |
| Critical Alert | áno, ale Apple to povoľuje iba zdravotníckym a bezpečnostným appkám |
| **AlarmKit (iOS 26+)** | **áno, bez schvaľovania od Applu** |

AlarmKit je od iOS 26 a je to jediná legálna cesta. Preto tá appka.

## Čo appka robí

* **Budíky** — na 4 dni dopredu naplánuje ranný a večerný budík na časy, ktoré
  drží bot. Zvonia cez systémový alarm, teda aj v tichom režime a cez Focus.
  Keď fázu odrobíš, jej budík sa okamžite zruší. Keď dáš zmraziť, zrušia sa všetky.
* **Pripomienky** — udržiava zoznam „Kliky“ v iCloude. Keď pripomienku odškrtneš
  (aj v Applovej appke Pripomienky, aj na Macu), appka to pri najbližšom
  zosúladení nahlási botovi.
* **Počítanie** — veľké tlačidlá +1 / +2 / +5 / +10, oprava ráno/večer, zmrazenie.
  Všetko ide do toho istého stavu ako Telegram a ako tabuľka na OMV.

Bot je stále zdroj pravdy. Appka je iba budík a diaľkové ovládanie.

## Čo potrebuješ

* iPhone s **iOS 26.1 alebo novším** (AlarmKit).
* Mac s **Xcode 26.1+**.
* `brew install xcodegen`.
* **AltStore Classic** + AltServer na Macu — AltStore PAL raw `.ipa` nainštalovať nevie.
* Bota bežiaceho a dostupného z telefónu (`https://kliky.fabrici.xyz`).

## Postavenie IPA

Lokálne na Macu:

```sh
brew install xcodegen
trener/ios/Kliky/postav-ipa.sh
# → trener/ios/Kliky/build/Kliky.ipa
```

Alebo cez GitHub Actions — workflow `.github/workflows/kliky-ios.yml` sa spustí
pri každej zmene v `trener/ios/**` alebo ručne cez *Run workflow*. Hotovú IPA si
stiahneš z artefaktov behu (`Kliky-ipa`).

IPA je **nepodpísaná naschvál**. Podpíše ju až AltStore tvojím Apple ID priamo na
telefóne — preto v CI netreba žiadne certifikáty ani tajomstvá.

Ak si chceš appku len rýchlo vyskúšať, otvor `Kliky.xcodeproj` (vyrobí ho
`xcodegen generate`) v Xcode, vyber svoj tím v *Signing & Capabilities* a pusti
ju priamo na telefón. Vydrží tiež 7 dní, ale obnovuje sa ručne cez Xcode.

## Inštalácia cez AltStore

1. Na Macu spusti **AltServer** a nechaj ho bežať (pridaj si ho do položiek po
   prihlásení — bez neho sa appka neobnoví).
2. Na iPhone otvor **AltStore → My Apps → +** a vyber stiahnutú `Kliky.ipa`.
3. Zadaj Apple ID. AltStore appku podpíše.

Čo z toho vyplýva a čo ťa bude štvať, ak to nevieš dopredu:

* **Platnosť 7 dní.** To je limit bezplatného Apple účtu, nie AltStoru.
  AltStore obnovuje na pozadí, ale je to nespoľahlivé. Sprav si radšej v Skratkách
  **osobnú automatizáciu**, ktorá každý deň ráno spustí AltStore skratku
  *Refresh All* — vtedy ti platnosť nikdy nevyprší.
* Obnovenie potrebuje **Mac zapnutý na tej istej Wi-Fi** (alebo kábel).
* **Naraz môžeš mať 3 sideloadnuté appky**, AltStore sa počíta ako jedna.
* Appka nemá žiadny widget ani rozšírenie — schválne, aby ti zožrala iba jedno
  App ID zo zásoby 10 na 7 dní.

## Prvé spustenie

1. **Nastavenia** (ozubené koliesko vpravo hore):
   * *Server*: `kliky.fabrici.xyz`
   * *Token*: hodnota `SHORTCUT_TOKEN` z `/etc/trener/trener.env` na LXC 122
     (ak tam nie je vyplnená, použije sa `WAKE_WEBHOOK_TOKEN`).
2. Appka si vypýta dve povolenia:
   * **Pripomienky** — plný prístup, inak nevie zoznam „Kliky“ ani čítať, ani písať.
   * **Budíky** — systémový dialóg AlarmKitu. Bez neho budíky nezazvonia.
3. Potiahnutím nadol appku zosúladíš so serverom kedykoľvek. Sama to robí pri
   otvorení a občas na pozadí.

## Dve veci, ktoré budíky umlčia

* **Neskrývaj appku a nedávaj ju za Face ID ani kód.** Skrytým a zamknutým appkám
  AlarmKit budíky ticho zahodí. Je to Applove správanie, nie chyba appky.
* **Neodmietni povolenie na budíky.** Ak si ho už odmietol, zapni ho v
  *Nastavenia → Kliky*.

## Ako to zapadá do zvyšku

```
Telegram ─┐
Tabuľka ──┼─→  bot (LXC 122)  ─→  iCloud kalendár (záznam, nie budenie)
Appka ────┘         │
                    └─→  /stav, /kliky, /hotovo, /zmraz  ←  appka
                                                             │
                                       AlarmKit budíky ──────┤
                                       Pripomienky iCloud ───┘
```

Appka nepozná pravidlá tréningu — všetko počíta bot. Preto keď zmeníš cieľ
v tabuľke alebo v chate, appka to pri najbližšom zosúladení zoberie na vedomie
aj s budíkmi.

## Známe hranice

* Budíky sú naplánované na 4 dni dopredu. Keby si appku 4 dni neotvoril a ani
  background refresh by neprebehol, piaty deň nezazvoní. V praxi sa appka
  zosúladí pri každom otvorení, takže sa to nestane.
* Časy budíkov sú tie, ktoré drží bot. Keď posunieš udalosť v kalendári, bot sa
  to naučí a appka to prevezme — nie naopak.
* Appka nemá vlastnú ikonu. Ak ti to prekáža, pridaj `Assets.xcassets` s `AppIcon`
  a doplň `ASSETCATALOG_COMPILER_APPICON_NAME` v `project.yml`.
