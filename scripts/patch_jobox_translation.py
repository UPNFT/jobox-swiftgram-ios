"""Pinned, fail-closed Swiftgram Jobox translation integration.

Only the exact public cf8b23 source bytes or this package's already-patched
bytes are accepted. All existing and added paths are checked before any write.
No network, credentials, signing, build, or model request is performed here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

UPSTREAM_COMMIT = "cf8b23beaaac4126a396337ac2d5be13f9f76b66"
PACKAGE = Path(__file__).resolve().parents[1]
FIXTURES = PACKAGE / "patches" / "fixtures"
PATCHES = PACKAGE / "patches"
SOURCE_HASHES = {
    "Swiftgram/SGSimpleSettings/Sources/SimpleSettings.swift": "3e62312f35b834264591e7f85574c3f14f0aeef8d06b76fd4bd803e15e47ed8c",
    "Swiftgram/SGSettingsUI/Sources/SGSettingsController.swift": "2455728beab2f2063eba0469417f19bb366a1dc60dd6b0eed3a0498148e55d38",
    "Swiftgram/SGSettingsUI/BUILD": "80fabe5c96939189ad629c4adfd361eedcfde9ab8828cb44b0cd926a8cbd551b",
    "Swiftgram/SGGTranslate/BUILD": "363d7f9fa02acc3d795b269dacb40644a0d2c3411246d68dfb9dc096ace80679",
    "submodules/TelegramCore/Sources/TelegramEngine/Messages/TelegramEngineMessages.swift": "6341bae43a2ff635ec12a1ed6bae62da658b82864a1464d5958299d1b31a2b32",
    "submodules/TelegramCore/Sources/TelegramEngine/Messages/Translate.swift": "99e16a6d57722a38d9e9a12eda861d5287d2b204319035ca6ea9fec816b0fd91",
    "submodules/TelegramUI/BUILD": "ac6c540e957ee01bdc1c7a335174a3bc9825757ed7470ecb6cf33f8468831969",
    "submodules/TelegramUI/Sources/Chat/ChatMessageDisplaySendMessageOptions.swift": "67002675613ae4a4c8459cd951f8faef3dbce6eb53bffd4f8a9e3d95814e7180",
    "submodules/TranslateUI/BUILD": "a2faab695b4593e4dd9df2e12bd18e95c3907958b351595df743f847777bb983",
    "submodules/TranslateUI/Sources/ChatTranslation.swift": "4f81f716d2c918e19521ed1ca3ce324ddb2d7423980fecab3656ea75933e1f49",
}
ADDED_FILES = {
    "Swiftgram/SGGTranslate/Sources/JoboxTranslationTransport.swift": "JoboxTranslationTransport.swift",
    "Swiftgram/SGGTranslate/Sources/JoboxTranslationStore.swift": "JoboxTranslationStore.swift",
    "Swiftgram/SGGTranslate/Sources/JoboxTranslationSignal.swift": "Jobox/JoboxTranslationSignal.swift",
    "Swiftgram/SGSettingsUI/Sources/JoboxTranslationSettingsController.swift": "Jobox/JoboxTranslationSettingsController.swift",
    "submodules/TelegramCore/Sources/TelegramEngine/Messages/JoboxTranslationMessages.swift": "Jobox/JoboxTranslationMessages.swift",
    "submodules/TelegramUI/Sources/Chat/JoboxDraftIdentity.swift": "Jobox/JoboxDraftIdentity.swift",
    "submodules/TelegramUI/Sources/Chat/JoboxDraftTranslation.swift": "Jobox/JoboxDraftTranslation.swift",
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def replace_once(source: str, before: str, after: str) -> str:
    count = source.count(before)
    if count != 1:
        raise ValueError(f"Jobox integration anchor must occur once, got {count}")
    return source.replace(before, after, 1)


def _transform(path: str, source: str) -> str:
    def change(before: str, after: str) -> None:
        nonlocal source
        source = replace_once(source, before, after)

    if path.endswith("SGSimpleSettings/Sources/SimpleSettings.swift"):
        change("        case gtranslate\n        case system", "        case gtranslate\n        case jobox\n        case system")
    elif path.endswith("SGSettingsUI/Sources/SGSettingsController.swift"):
        change('title: .text("Swiftgram"), leftNavigationButton:', 'title: .text("Jobox"), leftNavigationButton:')
        change("import SGSimpleSettings\n", "import SGSimpleSettings\nimport SGGTranslate\n")
        change("    case languageSettings\n}", "    case languageSettings\n    case joboxSettings\n}")
        change("private func SGControllerEntries(presentationData:", "private func SGControllerEntries(accountId: Int64, presentationData:")
        change("let entries = SGControllerEntries(presentationData:", "let entries = SGControllerEntries(accountId: context.account.peerId.id._internalGetInt64Value(), presentationData:")
        change('value: i18n("Settings.Translation.Backend.\\(SGSimpleSettings.shared.translationBackend)", lang)', 'value: sgJoboxProviderSelected(accountId: accountId) ? "Jobox" : i18n("Settings.Translation.Backend.\\(SGSimpleSettings.shared.translationBackend)", lang)')
        change("    if SGSimpleSettings.shared.translationBackendEnum != .gtranslate {", "    if SGSimpleSettings.shared.translationBackendEnum != .gtranslate && SGSimpleSettings.shared.translationBackendEnum != .jobox {")
        change('    entries.append(.disclosure(id: id.count, section: .translation, link: .languageSettings, text: strings.Localization_TranslateEntireChat))', '    entries.append(.disclosure(id: id.count, section: .translation, link: .joboxSettings, text: "Jobox 翻译配置"))\n    entries.append(.disclosure(id: id.count, section: .translation, link: .languageSettings, text: strings.Localization_TranslateEntireChat))')
        change('title: i18n("Settings.Translation.Backend.\\(value.rawValue)", presentationData.strings.baseLanguageCode)', 'title: value == .jobox ? "Jobox" : i18n("Settings.Translation.Backend.\\(value.rawValue)", presentationData.strings.baseLanguageCode)')
        change("                    SGSimpleSettings.shared.translationBackend = value\n", "                    if value == SGSimpleSettings.TranslationBackend.jobox.rawValue {\n                        pushControllerImpl?(joboxTranslationSettingsController(context: context))\n                        return\n                    }\n                    let accountId = context.account.peerId.id._internalGetInt64Value()\n                    if var configuration = JoboxTranslationSettings.configuration(accountId: accountId) {\n                        configuration.enabled = false\n                        try? JoboxTranslationSettings.saveConfiguration(configuration, accountId: accountId)\n                    }\n                    SGSimpleSettings.shared.translationBackend = value\n")
        change("            case .languageSettings:\n", "            case .joboxSettings:\n                pushControllerImpl?(joboxTranslationSettingsController(context: context))\n            case .languageSettings:\n")
    elif path.endswith("SGSettingsUI/BUILD"):
        change('        "//Swiftgram/SGSimpleSettings:SGSimpleSettings",', '        "//Swiftgram/SGSimpleSettings:SGSimpleSettings",\n        "//Swiftgram/SGGTranslate:SGGTranslate",\n        "//submodules/AsyncDisplayKit:AsyncDisplayKit",')
    elif path.endswith("SGGTranslate/BUILD"):
        change('    deps = [\n', '    deps = [\n        "//Swiftgram/SGSimpleSettings:SGSimpleSettings",\n')
    elif path.endswith("TelegramEngineMessages.swift"):
        change("messageId: EngineMessage.Id? = nil) -> Signal<(String, [MessageTextEntity])?, TranslationError>", "messageId: EngineMessage.Id? = nil, joboxTask: JoboxTranslationTask = .incoming) -> Signal<(String, [MessageTextEntity])?, TranslationError>")
        before = "sgWrappedTranslateSingle(text: text, toLang: toLang, default:"
        if source.count(before) != 2:
            raise ValueError("Expected both single-translation engine paths")
        source = source.replace(before, "sgWrappedTranslateSingle(accountId: self.account.peerId.id._internalGetInt64Value(), text: text, toLang: toLang, joboxTask: joboxTask, default:")
        change("sgWrappedTranslateMultiple(texts: texts,toLang: toLang, default:", "sgWrappedTranslateMultiple(accountId: self.account.peerId.id._internalGetInt64Value(), texts: texts, toLang: toLang, default:")
        change("private func sgWrappedTranslateSingle(\n    text: String,\n    toLang: String,", "private func sgWrappedTranslateSingle(\n    accountId: Int64,\n    text: String,\n    toLang: String,\n    joboxTask: JoboxTranslationTask,")
        change(
            ") -> Signal<(String, [MessageTextEntity])?, TranslationError> {\n    if SGSimpleSettings.shared.translationBackend == SGSimpleSettings.TranslationBackend.gtranslate.rawValue {",
            ") -> Signal<(String, [MessageTextEntity])?, TranslationError> {\n    if sgJoboxProviderSelected(accountId: accountId) {\n        return sgJoboxTranslate([text], accountId: accountId, toLang: toLang, task: joboxTask)\n            |> map { ($0[0], []) }\n            |> mapError { _ in .generic }\n    }\n    if SGSimpleSettings.shared.translationBackend == SGSimpleSettings.TranslationBackend.gtranslate.rawValue {",
        )
        change("private func sgWrappedTranslateMultiple(\n    texts:", "private func sgWrappedTranslateMultiple(\n    accountId: Int64,\n    texts:")
        change(
            ") -> Signal<[(String, [MessageTextEntity])], TranslationError> {\n    if SGSimpleSettings.shared.translationBackend == SGSimpleSettings.TranslationBackend.gtranslate.rawValue {",
            ") -> Signal<[(String, [MessageTextEntity])], TranslationError> {\n    if sgJoboxProviderSelected(accountId: accountId) {\n        let translatedSignals: [Signal<(String, [MessageTextEntity]), TranslationError>] = texts.map { text, _ in\n            sgJoboxTranslate([text], accountId: accountId, toLang: toLang)\n                |> map { ($0[0], []) }\n                |> mapError { _ in .generic }\n        }\n        return combineLatest(translatedSignals)\n    }\n    if SGSimpleSettings.shared.translationBackend == SGSimpleSettings.TranslationBackend.gtranslate.rawValue {",
        )
    elif path.endswith("Messages/Translate.swift"):
        signature = "func _internal_translateMessagesViaText(account: Account, messagesDict: [EngineMessage.Id: String], fromLang: String?, toLang: String, enableLocalIfPossible: Bool, generateEntitiesFunction: @escaping (String) -> [MessageTextEntity]) -> Signal<Never, TranslationError> {"
        change(signature, signature + "\n    if sgJoboxProviderSelected(accountId: account.peerId.id._internalGetInt64Value()) {\n        return _internal_translateMessagesViaJobox(account: account, messagesDict: messagesDict, toLang: toLang, generateEntitiesFunction: generateEntitiesFunction)\n    }")
    elif path.endswith("TelegramUI/BUILD"):
        change('    "//Swiftgram/SGSimpleSettings:SGSimpleSettings",', '    "//Swiftgram/SGSimpleSettings:SGSimpleSettings",\n    "//Swiftgram/SGGTranslate:SGGTranslate",')
    elif path.endswith("ChatMessageDisplaySendMessageOptions.swift"):
        start = source.index("            let textToTranslate = selfController.presentationInterfaceState.interfaceState.effectiveInputState.inputText.string\n")
        end = source.index("        }, changeTranslationLanguage:", start)
        source = source[:start] + "            if let outgoingMessageTranslateToLang {\n                selfController.joboxTranslateDraft(toLang: outgoingMessageTranslateToLang)\n            }\n" + source[end:]
    elif path.endswith("TranslateUI/BUILD"):
        change('    "//Swiftgram/SGSimpleSettings:SGSimpleSettings"', '    "//Swiftgram/SGSimpleSettings:SGSimpleSettings",\n    "//Swiftgram/SGGTranslate:SGGTranslate"')
    elif path.endswith("ChatTranslation.swift"):
        change("import Foundation\n", "import Foundation\nimport SGGTranslate\n")
        change("        if viaText {", "        if viaText || sgJoboxProviderSelected(accountId: context.account.peerId.id._internalGetInt64Value()) {")
        signature = "public func chatTranslationState(context: AccountContext, peerId: EnginePeer.Id, threadId: Int64?, forcePredict: Bool = false) -> Signal<ChatTranslationState?, NoError> {"
        change(signature, signature + "\n    let joboxLanguage = sgJoboxIncomingLanguage(accountId: context.account.peerId.id._internalGetInt64Value())")
        change("if !settings.translateChats && !autoTranslateEnabled && !forcePredict {", "if !settings.translateChats && !autoTranslateEnabled && !forcePredict && joboxLanguage == nil {")
        change("            if let ignoredLanguages = settings.ignoredLanguages {", "            if let joboxLanguage {\n                dontTranslateLanguages.insert(joboxLanguage)\n            } else if let ignoredLanguages = settings.ignoredLanguages {")
        change("                        return .single(cached)\n", "                        return .single(cached.withToLang(joboxLanguage ?? cached.toLang))\n")
        change("                            } else if autoTranslateEnabled {", "                            } else if autoTranslateEnabled || joboxLanguage != nil {")
        change("                                toLang: cached?.toLang,", "                                toLang: joboxLanguage ?? cached?.toLang,")
    else:
        raise ValueError(f"No pinned transformation for {path}")
    return source


def _expected_files() -> tuple[dict[str, bytes], dict[str, bytes]]:
    changed: dict[str, bytes] = {}
    for relative, expected in SOURCE_HASHES.items():
        original = (FIXTURES / relative.replace("/", "__")).read_bytes()
        if sha256(original) != expected:
            raise ValueError(f"Pinned public source fixture drift: {relative}")
        changed[relative] = _transform(relative, original.decode("utf-8")).encode("utf-8")
    added = {relative: (PATCHES / local).read_bytes() for relative, local in ADDED_FILES.items()}
    return changed, added


def translation_patch_receipt() -> dict:
    changed, added = _expected_files()
    return {
        "schema": 1,
        "feature": "jobox-translation-v1",
        "upstream_commit": UPSTREAM_COMMIT,
        "changed_files": [
            {"path": path, "before_sha256": SOURCE_HASHES[path], "after_sha256": sha256(data)}
            for path, data in sorted(changed.items())
        ],
        "added_files": [{"path": path, "sha256": sha256(data)} for path, data in sorted(added.items())],
    }


def apply_jobox_translation(upstream: Path | str) -> dict:
    root = Path(upstream).resolve(strict=True)
    changed, added = _expected_files()
    pending: list[tuple[Path, bytes]] = []
    # Preflight every source and overlay before changing even the first file.
    for relative, target_data in {**changed, **added}.items():
        path = root / relative
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError(f"Jobox patch path escapes upstream: {relative}")
        if relative in changed:
            if not path.is_file():
                raise ValueError(f"Missing pinned source: {relative}")
            digest = sha256(path.read_bytes())
            if digest not in {SOURCE_HASHES[relative], sha256(target_data)}:
                raise ValueError(f"Pinned upstream source drift: {relative}")
        elif path.exists() and (not path.is_file() or path.read_bytes() != target_data):
            raise ValueError(f"Jobox overlay collision: {relative}")
        if not path.exists() or path.read_bytes() != target_data:
            pending.append((path, target_data))
    for path, data in pending:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return translation_patch_receipt()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(apply_jobox_translation(args.upstream), sort_keys=True))
