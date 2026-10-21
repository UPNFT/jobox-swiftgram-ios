import Foundation
import ObjectiveC
import SwiftSignalKit
import Display
import TelegramCore
import SGSimpleSettings
import SGGTranslate
import TextFormat
import OverlayStatusController
import AlertUI

private var joboxDraftTranslationAssociation: UInt8 = 0

private final class JoboxDraftTranslationRequest {
    let disposable = MetaDisposable()
    let gate = JoboxDraftRequestGate()
    deinit { self.disposable.dispose() }
}

extension ChatControllerImpl {
    // The request belongs to this controller, not a global queue. Replacing it
    // cancels the previous transport; releasing the controller also cancels it.
    func joboxTranslateDraft(toLang: String) {
        guard let peerId = self.chatLocation.peerId else { return }
        let threadId = self.chatLocation.threadId
        let draft = NSAttributedString(attributedString: self.presentationInterfaceState.interfaceState.effectiveInputState.inputText)
        guard !draft.string.isEmpty else { return }
        let languageKey = SGSimpleSettings.makeOutgoingLanguageTranslationKey(accountId: self.context.account.peerId.id._internalGetInt64Value(), peerId: peerId.id._internalGetInt64Value())
        let configuredLanguage = SGSimpleSettings.shared.outgoingLanguageTranslation[languageKey]
        let accountId = self.context.account.peerId.id._internalGetInt64Value()
        let configuration = JoboxTranslationSettings.configuration(accountId: accountId)
        let provider = sgJoboxProviderSelected(accountId: accountId) ? "jobox" : SGSimpleSettings.shared.translationBackend
        let request = JoboxDraftTranslationRequest()
        let identity = JoboxDraftIdentity(peerId: peerId.id._internalGetInt64Value(), threadId: threadId, text: draft, language: toLang, provider: provider)
        let requestId = request.gate.begin(identity)
        objc_setAssociatedObject(self, &joboxDraftTranslationAssociation, request, .OBJC_ASSOCIATION_RETAIN_NONATOMIC)
        var dismissStatus: (() -> Void)?
        let statusController = OverlayStatusController(theme: self.presentationData.theme, type: .loading(cancelled: { [weak request] in
            request?.gate.cancel()
            request?.disposable.set(nil)
            dismissStatus?()
        }))
        dismissStatus = { [weak statusController] in statusController?.dismiss() }
        self.present(statusController, in: .window(.root))
        let entities = self.presentationInterfaceState.interfaceState.synchronizeableInputState?.entities ?? []
        request.disposable.set((self.context.engine.messages.translate(text: draft.string, toLang: toLang, entities: entities, joboxTask: .outbound)
        |> deliverOnMainQueue).start(next: { [weak self, weak request] result in
            dismissStatus?()
            guard let self, let request, let result,
                  let currentRequest = objc_getAssociatedObject(self, &joboxDraftTranslationAssociation) as? JoboxDraftTranslationRequest,
                  currentRequest === request,
                  self.chatLocation.peerId == peerId,
                  JoboxTranslationSettings.configuration(accountId: accountId) == configuration,
                  let currentPeer = self.chatLocation.peerId,
                  SGSimpleSettings.shared.outgoingLanguageTranslation[languageKey] == configuredLanguage,
                  let currentLanguage = configuredLanguage ?? self.contentData?.state.predictedChatLanguage,
                  request.gate.accepts(requestId, current: JoboxDraftIdentity(peerId: currentPeer.id._internalGetInt64Value(), threadId: self.chatLocation.threadId, text: self.presentationInterfaceState.interfaceState.effectiveInputState.inputText, language: currentLanguage, provider: sgJoboxProviderSelected(accountId: accountId) ? "jobox" : SGSimpleSettings.shared.translationBackend)) else { return }
            request.gate.cancel()
            // A translation replaces the entire draft. Only offsets returned for
            // the new text are applied; Jobox deliberately returns no old offsets.
            let newInputText = chatInputStateStringWithAppliedEntities(result.0, entities: result.1)
            let newState = ChatTextInputState(inputText: newInputText, selectionRange: 0 ..< newInputText.length)
            self.updateChatPresentationInterfaceState(interactive: true, { state in
                return state.updatedInterfaceState { $0.withUpdatedEffectiveInputState(newState) }
            })
        }, error: { [weak self, weak request] _ in
            dismissStatus?()
            guard let self, let request,
                  let currentRequest = objc_getAssociatedObject(self, &joboxDraftTranslationAssociation) as? JoboxDraftTranslationRequest,
                  currentRequest === request else { return }
            request.gate.cancel()
            self.present(textAlertController(context: self.context, updatedPresentationData: self.updatedPresentationData, title: "翻译未完成", text: "原草稿已保留，请稍后重试。", actions: [TextAlertAction(type: .defaultAction, title: "好", action: {})]), in: .window(.root))
        }, completed: {
            dismissStatus?()
        }))
    }
}
