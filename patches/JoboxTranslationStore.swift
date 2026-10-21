import Foundation
import Security

public enum JoboxTranslationStoreError: Error {
    case invalidConfiguration
    case invalidToken
    case keychain(OSStatus)
}

public enum JoboxTranslationSettings {
    private static let configurationPrefix = "jobox.translation.configuration."
    private static let keychainService = (Bundle.main.bundleIdentifier ?? "org.jobox.native") + ".jobox.translation"

    private struct StoredToken: Codable {
        let endpoint: String
        let tenant: String
        let project: String
        let token: String
    }

    public static func configuration(accountId: Int64) -> JoboxTranslationConfiguration? {
        guard let data = UserDefaults.standard.data(forKey: self.configurationPrefix + String(accountId)),
              let configuration = try? JSONDecoder().decode(JoboxTranslationConfiguration.self, from: data),
              configuration.isValid else {
            return nil
        }
        return configuration
    }

    public static func saveConfiguration(_ configuration: JoboxTranslationConfiguration, accountId: Int64) throws {
        guard configuration.isValid else {
            throw JoboxTranslationStoreError.invalidConfiguration
        }
        let data = try JSONEncoder().encode(configuration)
        UserDefaults.standard.set(data, forKey: self.configurationPrefix + String(accountId))
    }

    public static func token(accountId: Int64) -> String? {
        guard let configuration = self.configuration(accountId: accountId) else {
            return nil
        }
        var query = self.tokenQuery(accountId: accountId)
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data,
              let stored = try? JSONDecoder().decode(StoredToken.self, from: data),
              stored.endpoint == configuration.endpoint,
              stored.tenant == configuration.tenant,
              stored.project == configuration.project else {
            return nil
        }
        return JoboxTranslationClient.validToken(stored.token) ? stored.token : nil
    }

    public static func setToken(_ token: String, accountId: Int64) throws {
        guard let configuration = self.configuration(accountId: accountId) else {
            throw JoboxTranslationStoreError.invalidConfiguration
        }
        guard JoboxTranslationClient.validToken(token) else {
            throw JoboxTranslationStoreError.invalidToken
        }
        let stored = StoredToken(endpoint: configuration.endpoint, tenant: configuration.tenant, project: configuration.project, token: token)
        let data = try JSONEncoder().encode(stored)
        let query = self.tokenQuery(accountId: accountId)
        let status = SecItemUpdate(query as CFDictionary, [kSecValueData as String: data] as CFDictionary)
        if status == errSecItemNotFound {
            var attributes = query
            attributes[kSecValueData as String] = data
            attributes[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
            let added = SecItemAdd(attributes as CFDictionary, nil)
            guard added == errSecSuccess else {
                throw JoboxTranslationStoreError.keychain(added)
            }
        } else if status != errSecSuccess {
            throw JoboxTranslationStoreError.keychain(status)
        }
    }

    public static func isConfigured(accountId: Int64) -> Bool {
        return self.token(accountId: accountId) != nil
    }

    public static func clear(accountId: Int64) throws {
        let status = SecItemDelete(self.tokenQuery(accountId: accountId) as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw JoboxTranslationStoreError.keychain(status)
        }
        UserDefaults.standard.removeObject(forKey: self.configurationPrefix + String(accountId))
    }

    private static func tokenQuery(accountId: Int64) -> [String: Any] {
        return [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: self.keychainService,
            kSecAttrAccount as String: String(accountId),
            kSecAttrSynchronizable as String: false
        ]
    }
}
