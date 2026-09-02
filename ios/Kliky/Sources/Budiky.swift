import AlarmKit
import CryptoKit
import Foundation
import SwiftUI

/// Metadáta budíka. AlarmKit ich vracia späť pri zvonení, nám stačí vedieť,
/// ktorý deň a ktorá fáza to je.
struct KlikyBudik: AlarmMetadata {
    var faza: String
    var datum: String
}

/// Budíky cez AlarmKit (iOS 26.1+).
///
/// Toto je jediná vec na iPhone, ktorá zazvoní aj cez tichý prepínač a cez Focus
/// bez toho, aby appka potrebovala od Applu povolenie na Critical Alerts.
/// Kalendárová udalosť ani pripomienka to nedokážu — preto tu sú.
///
/// Plánujeme jednorazové budíky (`Alarm.Schedule.fixed`) na `horizont` dní dopredu,
/// aby zvonili aj vtedy, keď sa appka pár dní neotvorí. Pri každom zosúladení sa
/// zoznam prepočíta: čo už je hotové alebo zamrazené, sa zruší, čo chýba, sa doplní.
/// Opakujúci sa budík by bol menej práce, ale nedá sa preskočiť jeden deň —
/// a presne to potrebujeme, keď kliky spravíš skôr, než zazvoní.
actor Budiky {
    /// Na koľko dní dopredu plánujeme. Systém má na počet budíkov strop, ktorý
    /// nikde neuvádza, tak ich držíme nízko — zvyšok doplní ďalšie zosúladenie.
    private static let horizont = 4

    private var manazer: AlarmManager { AlarmManager.shared }

    private static let denFormat: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()

    var povolene: Bool { manazer.authorizationState == .authorized }

    /// Vypýta si povolenie na budíky. Systémový dialóg sa ukáže iba raz.
    @discardableResult
    func povol() async -> Bool {
        switch manazer.authorizationState {
        case .authorized:
            return true
        case .denied:
            return false
        default:
            return (try? await manazer.requestAuthorization()) == .authorized
        }
    }

    /// Zruší všetky budíky, ktoré appka naplánovala.
    func zrusVsetky() {
        guard let existujuce = try? manazer.alarms else { return }
        for budik in existujuce { try? manazer.cancel(id: budik.id) }
    }

    /// Prepočíta budíky podľa stavu dňa: doplní chýbajúce, zruší tie, ktoré už netreba.
    func zosuladi(stav: Stav) async {
        guard await povol() else { return }
        // Zamrazené = žiadne zvonenie, presne ako v chate a v kalendári.
        guard !stav.zamrazene else { return zrusVsetky() }

        let chcene = plan(stav: stav)
        guard let existujuce = try? manazer.alarms else { return }
        let existujuceId = Set(existujuce.map(\.id))

        for budik in existujuce where chcene[budik.id] == nil {
            try? manazer.cancel(id: budik.id)
        }
        // Od najbližšieho: keby systém povedal dosť, nech stoja aspoň tie dnešné.
        let chybajuce = chcene.filter { !existujuceId.contains($0.key) }
                              .sorted { $0.value.kedy < $1.value.kedy }
        for (id, polozka) in chybajuce {
            guard await naplanuj(id: id, polozka) else { break }
        }
    }

    // MARK: - Plán

    private struct Polozka {
        var kedy: Date
        var nazov: String
        var faza: Faza
        var datum: String
    }

    /// Budíky, ktoré by práve teraz mali existovať.
    private func plan(stav: Stav) -> [UUID: Polozka] {
        var vysledok: [UUID: Polozka] = [:]
        let teraz = Date()
        for odstup in 0..<Self.horizont {
            for faza in Faza.allCases {
                // Dnešnú fázu, ktorá je už odrobená, nebudíme.
                if odstup == 0, stav.splneny || stav.faza(faza).hotovo { continue }
                let zaklad = faza == .rano ? stav.ranoDue : stav.vecerDue
                guard let kedy = cas(zaklad, oDni: odstup), kedy > teraz else { continue }

                let datum = Self.denFormat.string(from: kedy)
                let pocet = odstup == 0
                    ? stav.faza(faza).zostava
                    : (faza == .rano ? stav.ranoCiel : stav.vecerCiel)
                let id = Self.identifikator("kliky|\(datum)|\(faza.rawValue)")
                let nazov = faza == .rano ? "Ranné kliky · \(pocet)" : "Večerné kliky · \(pocet)"
                vysledok[id] = Polozka(kedy: kedy,
                                       nazov: nazov,
                                       faza: faza,
                                       datum: datum)
            }
        }
        return vysledok
    }

    @discardableResult
    private func naplanuj(id: UUID, _ polozka: Polozka) async -> Bool {
        let alert = AlarmPresentation.Alert(title: LocalizedStringResource(stringLiteral: polozka.nazov))
        let atributy = AlarmAttributes(presentation: AlarmPresentation(alert: alert),
                                       metadata: KlikyBudik(faza: polozka.faza.rawValue,
                                                            datum: polozka.datum),
                                       tintColor: Color.orange)
        let konfiguracia = AlarmManager.AlarmConfiguration.alarm(
            schedule: .fixed(polozka.kedy),
            attributes: atributy)
        do {
            _ = try await manazer.schedule(id: id, configuration: konfiguracia)
            return true
        } catch {
            return false
        }
    }

    /// Ten istý čas ako `zaklad`, ale o `oDni` neskôr. Cez `bySettingHour` preto,
    /// aby prechod na letný/zimný čas neposunul budík o hodinu.
    private func cas(_ zaklad: Date, oDni: Int) -> Date? {
        let kalendar = Calendar.current
        let hm = kalendar.dateComponents([.hour, .minute], from: zaklad)
        guard let den = kalendar.date(byAdding: .day, value: oDni, to: zaklad) else { return nil }
        return kalendar.date(bySettingHour: hm.hour ?? 7, minute: hm.minute ?? 0, second: 0, of: den)
    }

    /// Stabilné UUID z textového kľúča, aby ten istý deň a fáza mali vždy to isté id
    /// a dali sa nájsť a zrušiť aj po reštarte appky.
    private static func identifikator(_ kluc: String) -> UUID {
        var b = Array(Insecure.MD5.hash(data: Data(kluc.utf8)))
        b[6] = (b[6] & 0x0F) | 0x30   // verzia 3
        b[8] = (b[8] & 0x3F) | 0x80   // variant RFC 4122
        return UUID(uuid: (b[0], b[1], b[2], b[3], b[4], b[5], b[6], b[7],
                           b[8], b[9], b[10], b[11], b[12], b[13], b[14], b[15]))
    }
}
