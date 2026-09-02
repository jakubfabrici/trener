import EventKit
import Foundation

/// Pripomienky v **iCloud** zozname – presne to, čo robí Duolingo: appka v telefóne
/// zapisuje cez EventKit, iCloud si ich sám rozsynchronizuje na všetky zariadenia.
@MainActor
final class Pripomienky {
    private let store = EKEventStore()
    private(set) var povolene = false

    /// EventKit vyhodí výnimku, ak `dueDateComponents` nie sú z gregoriánskeho
    /// kalendára — `Calendar.current` ním byť nemusí.
    private let gregorian = Calendar(identifier: .gregorian)

    /// Vyžiada plný prístup (iOS 17+). Bez neho sa nedá čítať ani zapisovať.
    @discardableResult
    func povol() async -> Bool {
        do {
            povolene = try await store.requestFullAccessToReminders()
        } catch {
            povolene = false
        }
        return povolene
    }

    /// Nájde (alebo vytvorí) zoznam v iCloude. Vracia nil, ak iCloud účet nie je k dispozícii.
    func zoznam(nazov: String) throws -> EKCalendar? {
        let existujuce = store.calendars(for: .reminder)
        if let n = existujuce.first(where: { $0.title == nazov }) { return n }
        // iCloud zdroj: typ .calDAV s názvom „iCloud“; ako záloha čokoľvek zapisovateľné
        let icloud = store.sources.first { $0.sourceType == .calDAV && $0.title.lowercased() == "icloud" }
        let zdroj = icloud
            ?? store.defaultCalendarForNewReminders()?.source
            ?? store.sources.first { $0.sourceType == .local }
        guard let zdroj else { return nil }
        let kalendar = EKCalendar(for: .reminder, eventStore: store)
        kalendar.title = nazov
        kalendar.source = zdroj
        try store.saveCalendar(kalendar, commit: true)
        return kalendar
    }

    /// Zosúladí zoznam s tým, čo hovorí bot: vytvorí chýbajúce, upraví text a čas,
    /// odškrtne splnené. Vracia poznámky odškrtnuté používateľom (na nahlásenie botovi).
    @discardableResult
    func zosuladi(stav: Stav, nazovZoznamu: String) async throws -> [String] {
        guard povolene, let kal = try zoznam(nazov: nazovZoznamu) else { return [] }
        let existujuce = try await najdi(v: kal)

        // Zmrazené = ticho. Nesplnené pripomienky zmažeme, splnené necháme ako históriu.
        guard !stav.zamrazene else {
            for r in existujuce where !r.isCompleted { try store.remove(r, commit: false) }
            try store.commit()
            return []
        }

        var odskrtnutePouzivatelom: [String] = []

        for faza in Faza.allCases {
            let f = stav.faza(faza)
            let moja = existujuce.first { ($0.notes ?? "").contains(f.poznamka) }

            if let r = moja {
                // 1) používateľ ju odškrtol → nahlásime botovi
                if r.isCompleted && !f.hotovo {
                    odskrtnutePouzivatelom.append(f.poznamka)
                }
                // 2) bot hovorí, že fáza je hotová → odškrtneme ju
                if f.hotovo && !r.isCompleted {
                    r.isCompleted = true
                    r.completionDate = Date()
                    try store.save(r, commit: false)
                } else if !f.hotovo {
                    var zmena = false
                    if r.title != f.nazov { r.title = f.nazov; zmena = true }
                    if let due = r.dueDateComponents.flatMap(gregorian.date(from:)),
                       abs(due.timeIntervalSince(f.cas)) > 60 {
                        nastavCas(r, f.cas); zmena = true
                    }
                    if zmena { try store.save(r, commit: false) }
                }
            } else if !f.hotovo {
                let r = EKReminder(eventStore: store)
                r.calendar = kal
                r.title = f.nazov
                r.notes = f.poznamka
                r.priority = 1                      // vysoká – v appke Pripomienky ‼️
                nastavCas(r, f.cas)
                try store.save(r, commit: false)
            }
        }
        try store.commit()
        return odskrtnutePouzivatelom
    }

    /// Zmaže staré pripomienky bota (z predošlých dní), splnené nechá ako históriu.
    func upratajStare(pred datum: String, nazovZoznamu: String) async throws {
        guard povolene, let kal = try zoznam(nazov: nazovZoznamu) else { return }
        for r in try await najdi(v: kal) where !r.isCompleted {
            guard let p = r.notes, p.hasPrefix("kliky:"), !p.contains(datum) else { continue }
            try store.remove(r, commit: false)
        }
        try store.commit()
    }

    private func nastavCas(_ r: EKReminder, _ kedy: Date) {
        let zlozky = gregorian.dateComponents([.year, .month, .day, .hour, .minute], from: kedy)
        r.startDateComponents = zlozky              // iOS vyžaduje začiatok, keď je termín
        r.dueDateComponents = zlozky
        r.alarms?.forEach { r.removeAlarm($0) }
        r.addAlarm(EKAlarm(absoluteDate: kedy))     // bez alarmu iOS neupozorní
    }

    private func najdi(v kalendar: EKCalendar) async throws -> [EKReminder] {
        let predikat = store.predicateForReminders(in: [kalendar])
        return await withCheckedContinuation { pokracovanie in
            store.fetchReminders(matching: predikat) { pokracovanie.resume(returning: $0 ?? []) }
        }
    }
}
