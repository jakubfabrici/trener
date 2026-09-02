import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var app: AppStav
    @State private var nastaveniaOtvorene = false
    @State private var vlastnyPocet = ""
    @FocusState private var vlastneAktivne: Bool

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 20) {
                    if let s = app.stav {
                        prehlad(s)
                        tlacidla(s)
                        fazy(s)
                        spodok(s)
                    } else if app.nacitava {
                        ProgressView("Načítavam…").padding(.top, 60)
                    } else {
                        prazdno
                    }
                    if let chyba = app.chyba {
                        Label(chyba, systemImage: "exclamationmark.triangle.fill")
                            .font(.footnote)
                            .foregroundStyle(.orange)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding()
                            .background(.orange.opacity(0.12), in: RoundedRectangle(cornerRadius: 12))
                    }
                }
                .padding()
            }
            .navigationTitle("Kliky")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { nastaveniaOtvorene = true } label: { Image(systemName: "gearshape") }
                }
            }
            .refreshable { await app.obnov() }
            .sheet(isPresented: $nastaveniaOtvorene) { NastaveniaView() }
            .task { await app.obnov() }
        }
    }

    // ── prehľad dňa ─────────────────────────────────────────────────────────
    private func prehlad(_ s: Stav) -> some View {
        VStack(spacing: 8) {
            Text(s.splneny ? "Hotovo na dnes 🎉" : "Zostáva")
                .font(.subheadline).foregroundStyle(.secondary)
            Text(s.splneny ? "\(s.spolu)/\(s.ciel)" : "\(s.zostava)")
                .font(.system(size: 76, weight: .bold, design: .rounded))
                .contentTransition(.numericText())
                .foregroundStyle(s.splneny ? .green : .primary)
            if !s.splneny {
                Text("z dnešných \(s.ciel) · máš \(s.spolu)")
                    .font(.subheadline).foregroundStyle(.secondary)
            }
            HStack(spacing: 16) {
                Label("\(s.streak)", systemImage: "flame.fill").foregroundStyle(.orange)
                if s.zamrazene {
                    Label("Zamrazené", systemImage: "snowflake").foregroundStyle(.blue)
                }
            }
            .font(.callout.weight(.medium))
            ProgressView(value: Double(min(s.spolu, s.ciel)), total: Double(max(s.ciel, 1)))
                .tint(s.splneny ? .green : .accentColor)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 8)
    }

    // ── rýchle hlásenie ─────────────────────────────────────────────────────
    private func tlacidla(_ s: Stav) -> some View {
        VStack(spacing: 12) {
            HStack(spacing: 10) {
                ForEach([1, 2, 5, 10], id: \.self) { n in
                    Button {
                        Task { await app.nahlas(n) }
                    } label: {
                        Text("+\(n)").font(.title3.weight(.semibold))
                            .frame(maxWidth: .infinity, minHeight: 52)
                    }
                    .buttonStyle(.borderedProminent)
                }
            }
            HStack {
                TextField("vlastný počet", text: $vlastnyPocet)
                    .keyboardType(.numberPad)
                    .focused($vlastneAktivne)
                    .textFieldStyle(.roundedBorder)
                Button("Pridať") {
                    if let n = Int(vlastnyPocet), n > 0 {
                        Task { await app.nahlas(n) }
                        vlastnyPocet = ""
                        vlastneAktivne = false
                    }
                }
                .buttonStyle(.bordered)
                .disabled(Int(vlastnyPocet) == nil)
            }
            Text("Zapisuje sa do fázy: \(app.aktualnaFaza.popis.lowercased())")
                .font(.caption).foregroundStyle(.secondary)
        }
        .disabled(app.nacitava)
    }

    // ── ráno / večer ────────────────────────────────────────────────────────
    private func fazy(_ s: Stav) -> some View {
        VStack(spacing: 10) {
            ForEach(Faza.allCases, id: \.self) { f in
                let d = s.faza(f)
                let hotova = d.hotovo
                HStack(spacing: 12) {
                    Image(systemName: hotova ? "checkmark.circle.fill" : f.symbol)
                        .font(.title2)
                        .foregroundStyle(hotova ? .green : .accentColor)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(f.popis).font(.headline)
                        Text(hotova ? "splnené" : "budík o \(f == .rano ? s.ranoCas : s.vecerCas)")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Text(f == .rano ? "\(s.rano)/\(s.ranoCiel)" : "\(s.vecer)/\(s.vecerCiel)")
                        .font(.title3.monospacedDigit().weight(.semibold))
                        .foregroundStyle(hotova ? .green : .primary)
                }
                .padding()
                .background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 14))
                .contextMenu {
                    ForEach([0, 5, 10], id: \.self) { n in
                        Button("Oprav na \(n)") { Task { await app.oprav(n, faza: f) } }
                    }
                }
            }
        }
    }

    // ── zamrazenie a sync ───────────────────────────────────────────────────
    private func spodok(_ s: Stav) -> some View {
        VStack(spacing: 12) {
            Toggle(isOn: Binding(get: { s.zamrazene },
                                 set: { nove in Task { await app.zmraz(nove) } })) {
                Label("Zamraziť trénera", systemImage: "snowflake")
            }
            .padding(.horizontal, 4)

            if let kedy = app.poslednySync {
                Text("Naposledy zosynchronizované \(kedy.formatted(date: .omitted, time: .shortened))")
                    .font(.caption2).foregroundStyle(.secondary)
            }
        }
    }

    private var prazdno: some View {
        VStack(spacing: 14) {
            Image(systemName: "figure.strengthtraining.traditional")
                .font(.system(size: 54)).foregroundStyle(.secondary)
            Text("Nastav adresu servera a token").font(.headline)
            Button("Otvoriť nastavenia") { nastaveniaOtvorene = true }
                .buttonStyle(.borderedProminent)
        }
        .padding(.top, 60)
    }
}

struct NastaveniaView: View {
    @EnvironmentObject private var app: AppStav
    @Environment(\.dismiss) private var zavri

    var body: some View {
        NavigationStack {
            Form {
                Section("Server") {
                    TextField("adresa", text: app.nastavenia.$server)
                        .textInputAutocapitalization(.never).autocorrectionDisabled()
                    SecureField("token", text: Binding(get: { app.nastavenia.token },
                                                        set: { app.nastavenia.token = $0 }))
                }
                Section("Upozornenia") {
                    Toggle("Pripomienky v iCloude",
                           isOn: app.nastavenia.$pripomienkyZapnute)
                    Toggle("Budík ráno a večer",
                           isOn: app.nastavenia.$budikyZapnute)
                    TextField("názov zoznamu", text: app.nastavenia.$zoznam)
                }
                Section {
                    Button("Skúsiť pripojenie") { Task { await app.obnov() } }
                } footer: {
                    Text("Budík zvoní aj cez tichý režim a Sústredenie. Keď ho zastavíš, "
                         + "tréner to berie ako splnenú fázu.")
                }
            }
            .navigationTitle("Nastavenia")
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Hotovo") { zavri() } } }
            .onDisappear { Task { await app.obnov() } }
        }
    }
}
