import Foundation
import SwiftUI

/// Uložené nastavenia appky. Token je citlivý, preto ide do Keychainu, zvyšok do UserDefaults.
@MainActor
final class Nastavenia: ObservableObject {
    @AppStorage("server") var server: String = "kliky.fabrici.xyz"
    @AppStorage("pripomienkyZapnute") var pripomienkyZapnute: Bool = true
    @AppStorage("budikyZapnute") var budikyZapnute: Bool = true
    @AppStorage("zoznam") var zoznam: String = "Kliky"

    @Published var token: String {
        didSet { Keychain.uloz(token, kluc: "token") }
    }

    init() {
        token = Keychain.citaj(kluc: "token") ?? ""
    }

    var api: Api { Api(zaklad: server, token: token) }
    var nastavene: Bool { !server.isEmpty && !token.isEmpty }
}

enum Keychain {
    private static let sluzba = "xyz.fabrici.kliky"

    static func uloz(_ hodnota: String, kluc: String) {
        let dotaz: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                    kSecAttrService as String: sluzba,
                                    kSecAttrAccount as String: kluc]
        SecItemDelete(dotaz as CFDictionary)
        guard !hodnota.isEmpty, let data = hodnota.data(using: .utf8) else { return }
        var novy = dotaz
        novy[kSecValueData as String] = data
        novy[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock
        SecItemAdd(novy as CFDictionary, nil)
    }

    static func citaj(kluc: String) -> String? {
        let dotaz: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                    kSecAttrService as String: sluzba,
                                    kSecAttrAccount as String: kluc,
                                    kSecReturnData as String: true,
                                    kSecMatchLimit as String: kSecMatchLimitOne]
        var vysledok: AnyObject?
        guard SecItemCopyMatching(dotaz as CFDictionary, &vysledok) == errSecSuccess,
              let data = vysledok as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }
}
