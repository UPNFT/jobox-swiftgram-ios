import Foundation
import SwiftSignalKit
import SGSimpleSettings

// Jobox is an explicit provider. Configuration and transport failures must never
// disclose the same message to the Google or Telegram fallback providers.
public func sgJoboxProviderSelected(accountId: Int64) -> Bool {
    return JoboxTranslationSettings.configuration(accountId: accountId)?.enabled == true
        || SGSimpleSettings.shared.translationBackendEnum == .jobox
}

public func sgJoboxIsEnabled(accountId: Int64) -> Bool {
    return sgJoboxProviderSelected(accountId: accountId)
        && JoboxTranslationSettings.configuration(accountId: accountId)?.enabled == true
        && JoboxTranslationSettings.isConfigured(accountId: accountId)
}

public func sgJoboxIncomingLanguage(accountId: Int64) -> String? {
    guard sgJoboxIsEnabled(accountId: accountId) else { return nil }
    return JoboxTranslationSettings.configuration(accountId: accountId)?.incomingLanguage
}

public func sgJoboxTranslate(
    _ texts: [String],
    accountId: Int64,
    toLang: String,
    task: JoboxTranslationTask = .incoming
) -> Signal<[String], TranslateFetchError> {
    return Signal { subscriber in
        guard let configuration = JoboxTranslationSettings.configuration(accountId: accountId), configuration.enabled,
              let token = JoboxTranslationSettings.token(accountId: accountId) else {
            subscriber.putError(.network)
            return EmptyDisposable
        }
        let client = JoboxTranslationClient(configuration: configuration, token: token)
        let request = client.translate(texts: texts, targetLanguage: toLang, task: task) { result in
            switch result {
            case let .success(translations):
                subscriber.putNext(translations)
                subscriber.putCompletion()
            case .failure:
                subscriber.putError(.network)
            }
        }
        return ActionDisposable { request.cancel() }
    }
}
