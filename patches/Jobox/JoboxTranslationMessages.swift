import Foundation
import Postbox
import SwiftSignalKit
import SGGTranslate

// Reuse the existing message IDs, processing manager and translation attributes.
// Each request contains one complete message, never a per-line fanout. The
// server's sequential translator must not consume a 20-message batch deadline.
func _internal_translateMessagesViaJobox(
    account: Account,
    messagesDict: [EngineMessage.Id: String],
    toLang: String,
    generateEntitiesFunction: @escaping (String) -> [MessageTextEntity]
) -> Signal<Never, TranslationError> {
    let entries = Array(messagesDict)
    let signals: [Signal<Void, TranslationError>] = entries.map { entry in
        return sgJoboxTranslate([entry.value], accountId: account.peerId.id._internalGetInt64Value(), toLang: toLang)
        |> mapError { _ -> TranslationError in .generic }
        |> mapToSignal { translations -> Signal<Void, TranslationError> in
            return account.postbox.transaction { transaction in
                for translatedText in translations {
                    // An edit while the request was in flight invalidates only
                    // this result; preserve the actual message and its attributes.
                    guard transaction.getMessage(entry.key)?.text == entry.value else { continue }
                    transaction.updateMessage(entry.key, update: { currentMessage in
                        var attributes = currentMessage.attributes.filter { !($0 is TranslationMessageAttribute) }
                        attributes.append(TranslationMessageAttribute(text: translatedText, entities: generateEntitiesFunction(translatedText), toLang: toLang))
                        return .update(StoreMessage(id: currentMessage.id, customStableId: nil, globallyUniqueId: currentMessage.globallyUniqueId, groupingKey: currentMessage.groupingKey, threadId: currentMessage.threadId, timestamp: currentMessage.timestamp, flags: StoreMessageFlags(currentMessage.flags), tags: currentMessage.tags, globalTags: currentMessage.globalTags, localTags: currentMessage.localTags, forwardInfo: currentMessage.forwardInfo.flatMap(StoreMessageForwardInfo.init), authorId: currentMessage.author?.id, text: currentMessage.text, attributes: attributes, media: currentMessage.media))
                    })
                }
            }
            |> castError(TranslationError.self)
        }
    }
    return combineLatest(signals) |> ignoreValues
}
