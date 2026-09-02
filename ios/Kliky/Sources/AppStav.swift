import Foundation
import SwiftUI

/// Mozog appky: drží stav dňa a spája bota, Pripomienky a budíky.
@MainActor
final class AppStav: ObservableObject {
    @Published private(set) var stav: Stav?
    @Published private(set) var nacitava = false
    @Published var chyba: String?
    @Published private(set) var poslednySync: Date?

    let nastavenia: Nastavenia
    private let pripomienky = Pripomienky()
    private let budiky = Budiky()
    private var uzPovolene = false

    init(nastavenia: Nastavenia) {
        self.nastavenia = nastavenia
    }

    var zostava: Int { stav?.zostava ?? 0 }

    /// Aktuálna fáza podľa času a podľa toho, čo ešte nie je hotové.
    var aktualnaFaza: Faza {
        guard let s = stav else { return .rano }
        if !s.ranoHotovo { return Date() < s.vecerDue ? .rano : .vecer }
        return .vecer
    }

    func povolenia() async {
        guard !uzPovolene else { return }
        uzPovolene = true
        if nastavenia.pripomienkyZapnute { await pripomienky.povol() }
        if nastavenia.budikyZapnute { await budiky.povol() }
    }

    /// Načíta stav a zosúladí Pripomienky aj budíky. Volá sa pri štarte, pull-to-refresh
    /// a z background tasku.
    func obnov(tiche: Bool = false) async {
        guard nastavenia.nastavene else {
            chyba = ApiChyba.nenastavene.errorDescription
            return
        }
        if !tiche { nacitava = true }
        defer { nacitava = false }
        do {
            var novy = try await nastavenia.api.stav()
            await povolenia()

            if nastavenia.pripomienkyZapnute {
                // čo si odškrtol v Pripomienkach, nahlásime botovi a stav načítame znova
                let odskrtnute = try await pripomienky.zosuladi(stav: novy, nazovZoznamu: nastavenia.zoznam)
                for poznamka in odskrtnute {
                    novy = try await nastavenia.api.hotovo(poznamka: poznamka)
                }
                if !odskrtnute.isEmpty {
                    _ = try? await pripomienky.zosuladi(stav: novy, nazovZoznamu: nastavenia.zoznam)
                }
                try? await pripomienky.upratajStare(pred: novy.datum, nazovZoznamu: nastavenia.zoznam)
            }
            if nastavenia.budikyZapnute {
                await budiky.zosuladi(stav: novy)
            } else {
                await budiky.zrusVsetky()
            }
            stav = novy
            poslednySync = Date()
            chyba = nil
        } catch {
            chyba = (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
        }
    }

    func nahlas(_ pocet: Int, faza: Faza? = nil) async {
        guard let s = stav else { return }
        let f = faza ?? aktualnaFaza
        await sPrekrytim {
            self.stav = try await self.nastavenia.api.nahlas(pocet, faza: f,
                                                             poznamka: s.faza(f).poznamka)
        }
    }

    func oprav(_ hodnota: Int, faza: Faza) async {
        guard let s = stav else { return }
        await sPrekrytim {
            self.stav = try await self.nastavenia.api.nahlas(hodnota, faza: faza,
                                                             poznamka: s.faza(faza).poznamka,
                                                             absolutne: true)
        }
    }

    func zmraz(_ zapnut: Bool) async {
        await sPrekrytim { self.stav = try await self.nastavenia.api.zmraz(zapnut) }
    }

    private func sPrekrytim(_ akcia: @escaping () async throws -> Void) async {
        nacitava = true
        defer { nacitava = false }
        do {
            try await akcia()
            chyba = nil
            poslednySync = Date()
            if let s = stav {
                if nastavenia.pripomienkyZapnute {
                    _ = try? await pripomienky.zosuladi(stav: s, nazovZoznamu: nastavenia.zoznam)
                }
                if nastavenia.budikyZapnute {
                    await budiky.zosuladi(stav: s)
                }
            }
        } catch {
            chyba = (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
        }
    }
}
