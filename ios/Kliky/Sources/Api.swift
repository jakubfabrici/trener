import Foundation

/// Stav dňa tak, ako ho vracia bot (`GET /stav?token=…`).
struct Stav: Codable, Equatable, Sendable {
    var datum: String
    var ciel: Int
    var rano: Int
    var vecer: Int
    var spolu: Int
    var zostava: Int
    var ranoCiel: Int
    var vecerCiel: Int
    var ranoHotovo: Bool
    var vecerHotovo: Bool
    var splneny: Bool
    var zamrazene: Bool
    var streak: Int
    var zajtraCiel: Int
    var ranoCas: String
    var vecerCas: String
    var ranoNazov: String
    var vecerNazov: String
    var ranoDue: Date
    var vecerDue: Date
    var poznamkaRano: String
    var poznamkaVecer: String

    /// Pomocník pre obe fázy naraz.
    func faza(_ f: Faza) -> (nazov: String, cas: Date, hotovo: Bool, poznamka: String, zostava: Int) {
        switch f {
        case .rano:
            return (ranoNazov, ranoDue, ranoHotovo, poznamkaRano, max(ranoCiel - rano, 0))
        case .vecer:
            return (vecerNazov, vecerDue, vecerHotovo, poznamkaVecer, zostava)
        }
    }
}

enum Faza: String, CaseIterable, Sendable {
    case rano, vecer
    var popis: String { self == .rano ? "Ráno" : "Večer" }
    var symbol: String { self == .rano ? "sunrise.fill" : "moon.stars.fill" }
}

enum ApiChyba: LocalizedError {
    case nenastavene
    case zlyToken
    case server(Int)
    case siet(String)

    var errorDescription: String? {
        switch self {
        case .nenastavene: return "Nastav adresu servera a token v nastaveniach."
        case .zlyToken: return "Server token odmietol (403). Skontroluj ho v nastaveniach."
        case .server(let code): return "Server vrátil chybu \(code)."
        case .siet(let m): return "Server je nedostupný: \(m)"
        }
    }
}

/// Tenký klient k botovi. Všetko sú GET-y s tokenom v query, aby to bolo jednoduché.
struct Api: Sendable {
    var zaklad: String
    var token: String

    private static let dekoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        let fSekundy = ISO8601DateFormatter()
        fSekundy.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        d.dateDecodingStrategy = .custom { dec in
            let s = try dec.singleValueContainer().decode(String.self)
            if let dt = f.date(from: s) ?? fSekundy.date(from: s) { return dt }
            throw DecodingError.dataCorrupted(.init(codingPath: dec.codingPath,
                                                    debugDescription: "Neznámy formát dátumu: \(s)"))
        }
        return d
    }()

    private func url(_ cesta: String, _ polozky: [String: String] = [:]) throws -> URL {
        guard !zaklad.isEmpty, !token.isEmpty,
              var c = URLComponents(string: zaklad.hasPrefix("http") ? zaklad : "https://\(zaklad)")
        else { throw ApiChyba.nenastavene }
        c.path = cesta
        c.queryItems = [URLQueryItem(name: "token", value: token)]
            + polozky.map { URLQueryItem(name: $0.key, value: $0.value) }
        guard let u = c.url else { throw ApiChyba.nenastavene }
        return u
    }

    private func data(_ cesta: String, _ polozky: [String: String] = [:]) async throws -> Data {
        var req = URLRequest(url: try url(cesta, polozky))
        req.timeoutInterval = 20
        req.cachePolicy = .reloadIgnoringLocalCacheData
        do {
            let (data, resp) = try await URLSession.shared.data(for: req)
            let kod = (resp as? HTTPURLResponse)?.statusCode ?? 0
            if kod == 403 { throw ApiChyba.zlyToken }
            guard (200..<300).contains(kod) else { throw ApiChyba.server(kod) }
            return data
        } catch let e as ApiChyba {
            throw e
        } catch {
            throw ApiChyba.siet(error.localizedDescription)
        }
    }

    func stav() async throws -> Stav {
        try Self.dekoder.decode(Stav.self, from: try await data("/stav"))
    }

    /// Nahlási kliky. `absolutne` = nastav presnú hodnotu namiesto pripočítania.
    @discardableResult
    func nahlas(_ pocet: Int, faza: Faza, poznamka: String, absolutne: Bool = false) async throws -> Stav {
        _ = try await data("/kliky", ["n": String(pocet), "poznamka": poznamka,
                                      "absolute": absolutne ? "1" : "0"])
        return try await stav()
    }

    /// Fáza je hotová (odškrtnutá pripomienka alebo zastavený budík).
    @discardableResult
    func hotovo(poznamka: String) async throws -> Stav {
        _ = try await data("/hotovo", ["poznamka": poznamka])
        return try await stav()
    }

    @discardableResult
    func zmraz(_ zapnut: Bool) async throws -> Stav {
        _ = try await data("/zmraz", ["hodnota": zapnut ? "1" : "0"])
        return try await stav()
    }

    /// Používateľ zmenil čas alebo názov pripomienky/budíka – bot sa tomu prispôsobí.
    func uprav(poznamka: String, nazov: String? = nil, cas: String? = nil) async throws {
        var p = ["poznamka": poznamka]
        if let nazov { p["nazov"] = nazov }
        if let cas { p["cas"] = cas }
        _ = try await data("/uprav", p)
    }
}
