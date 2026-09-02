import BackgroundTasks
import SwiftUI

@main
struct KlikyApp: App {
    @StateObject private var nastavenia = Nastavenia()
    @StateObject private var app: AppStav
    @Environment(\.scenePhase) private var faza

    static let obnovaID = "xyz.fabrici.kliky.obnova"

    init() {
        let n = Nastavenia()
        _nastavenia = StateObject(wrappedValue: n)
        _app = StateObject(wrappedValue: AppStav(nastavenia: n))
    }

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(app)
                .environmentObject(nastavenia)
        }
        .onChange(of: faza) { _, nova in
            if nova == .active {
                Task { await app.obnov(tiche: true) }
            } else if nova == .background {
                Self.naplanujObnovu()
            }
        }
        .backgroundTask(.appRefresh(Self.obnovaID)) {
            await Self.obnovaNaPozadi()
        }
    }

    /// Beh na pozadí si robí vlastný `AppStav` – nesiahame na stav obrazovky
    /// z iného vlákna. Nič sa tým nestratí, pravda je aj tak na serveri.
    @MainActor
    private static func obnovaNaPozadi() async {
        let stav = AppStav(nastavenia: Nastavenia())
        await stav.obnov(tiche: true)
        naplanujObnovu()
    }

    /// Systém rozhodne, kedy to naozaj spustí – žiadame o ~30 minút.
    @MainActor
    private static func naplanujObnovu() {
        let ziadost = BGAppRefreshTaskRequest(identifier: obnovaID)
        ziadost.earliestBeginDate = Date(timeIntervalSinceNow: 30 * 60)
        try? BGTaskScheduler.shared.submit(ziadost)
    }
}
