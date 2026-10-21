"""Offline integration contracts; Swift runtime checks run separately on macOS."""

from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / "scripts"))
import patch_jobox_translation as integration


class JoboxTranslationPatchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "upstream"
        self.root.mkdir()
        for relative in integration.SOURCE_HASHES:
            source = integration.FIXTURES / relative.replace("/", "__")
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)

    def read(self, relative):
        return (self.root / relative).read_text(encoding="utf-8")

    def apply(self):
        return integration.apply_jobox_translation(self.root)

    def assert_originals_preserved(self, except_path=None):
        for relative, digest in integration.SOURCE_HASHES.items():
            if relative != except_path:
                self.assertEqual(hashlib.sha256((self.root / relative).read_bytes()).hexdigest(), digest)

    def test_original_source_applies_and_every_output_matches_receipt(self):
        receipt = self.apply()
        self.assertEqual(receipt["schema"], 1)
        self.assertEqual(receipt["upstream_commit"], "cf8b23beaaac4126a396337ac2d5be13f9f76b66")
        self.assertEqual(len(receipt["changed_files"]), 10)
        self.assertEqual(len(receipt["added_files"]), 7)
        for row in receipt["changed_files"]:
            self.assertEqual(hashlib.sha256((self.root / row["path"]).read_bytes()).hexdigest(), row["after_sha256"])
            self.assertNotEqual(row["before_sha256"], row["after_sha256"])
        for row in receipt["added_files"]:
            self.assertEqual(hashlib.sha256((self.root / row["path"]).read_bytes()).hexdigest(), row["sha256"])

    def test_second_application_is_idempotent_and_performs_no_writes(self):
        receipt = self.apply()
        with patch.object(Path, "write_bytes", side_effect=AssertionError("already patched must not write")):
            self.assertEqual(self.apply(), receipt)

    def test_last_source_drift_is_rejected_before_any_file_is_written(self):
        relative = list(integration.SOURCE_HASHES)[-1]
        (self.root / relative).write_bytes((self.root / relative).read_bytes() + b"\n// external edit\n")
        with self.assertRaisesRegex(ValueError, "source drift"):
            self.apply()
        self.assert_originals_preserved(except_path=relative)
        self.assertFalse(any((self.root / path).exists() for path in integration.ADDED_FILES))

    def test_overlay_collision_is_rejected_before_originals_are_modified(self):
        relative = list(integration.ADDED_FILES)[-1]
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("// user file\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "overlay collision"):
            self.apply()
        self.assert_originals_preserved()
        self.assertEqual(target.read_text(), "// user file\n")

    def test_missing_source_is_rejected_before_any_other_write(self):
        relative = list(integration.SOURCE_HASHES)[-1]
        (self.root / relative).unlink()
        with self.assertRaisesRegex(ValueError, "Missing pinned source"):
            self.apply()
        self.assert_originals_preserved(except_path=relative)

    def test_fixture_drift_is_rejected(self):
        original = integration.FIXTURES
        bad = Path(self.temporary.name) / "bad-fixtures"
        shutil.copytree(original, bad)
        first = next(bad.iterdir())
        first.write_bytes(first.read_bytes() + b"\n")
        with patch.object(integration, "FIXTURES", bad):
            with self.assertRaisesRegex(ValueError, "fixture drift"):
                self.apply()
        self.assert_originals_preserved()

    def test_anchor_loss_or_ambiguity_is_rejected(self):
        for value in ("missing", "anchor anchor"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                integration.replace_once(value, "anchor", "replacement")

    def test_symlink_escape_is_rejected_without_touching_external_file(self):
        relative = next(iter(integration.SOURCE_HASHES))
        target = self.root / relative
        outside = Path(self.temporary.name) / "outside.swift"
        outside.write_bytes(target.read_bytes())
        target.unlink()
        try:
            target.symlink_to(outside)
        except OSError:
            self.skipTest("Windows symlink privilege unavailable")
        with self.assertRaisesRegex(ValueError, "escapes upstream"):
            self.apply()
        self.assertEqual(hashlib.sha256(outside.read_bytes()).hexdigest(), integration.SOURCE_HASHES[relative])

    def test_jobox_routes_before_fallback_and_explicit_outbound_survives_engine(self):
        self.apply()
        engine = self.read("submodules/TelegramCore/Sources/TelegramEngine/Messages/TelegramEngineMessages.swift")
        for function in ("sgWrappedTranslateSingle", "sgWrappedTranslateMultiple"):
            body = engine.split("private func " + function + "(", 1)[1]
            route = body.split("    if SGSimpleSettings.shared.translationBackend", 1)[0]
            self.assertIn("if sgJoboxProviderSelected(accountId: accountId)", route)
            self.assertIn("return ", route)
            self.assertNotIn("gtranslate(", route)
            self.assertNotIn("`catch`", route)
        self.assertEqual(engine.count("text: text, toLang: toLang, joboxTask: joboxTask, default:"), 2)
        self.assertIn("task: joboxTask", engine)
        self.assertIn("|> map { ($0[0], []) }", engine)
        sender = self.read("submodules/TelegramUI/Sources/Chat/ChatMessageDisplaySendMessageOptions.swift")
        self.assertIn("selfController.joboxTranslateDraft(toLang:", sender)
        self.assertNotIn("let textToTranslate =", sender)
        draft = self.read("submodules/TelegramUI/Sources/Chat/JoboxDraftTranslation.swift")
        self.assertIn("joboxTask: .outbound", draft)
        self.assertIn("self.chatLocation.peerId == peerId", draft)  # full namespace, not numeric id alone
        self.assertIn("request.gate.accepts(requestId", draft)
        self.assertIn("request?.gate.cancel()", draft)
        self.assertIn("request?.disposable.set(nil)", draft)
        self.assertNotIn("sendMessages(", draft)
        self.assertNotIn("enqueueOutgoing", draft)

    def test_receive_uses_original_state_ids_and_cache_without_forcing_paused_chat(self):
        self.apply()
        state = self.read("submodules/TranslateUI/Sources/ChatTranslation.swift")
        self.assertIn("if let currentIsEnabled = cached?.isEnabled {\n                                isEnabled = currentIsEnabled", state)
        self.assertIn("else if autoTranslateEnabled || joboxLanguage != nil", state)
        self.assertIn("cached.withToLang(joboxLanguage ?? cached.toLang)", state)
        # The shared entry covers history, pinned messages and quick translate;
        # Premium callers cannot bypass the explicitly selected Jobox provider.
        self.assertIn("if viaText || sgJoboxProviderSelected(accountId: context.account.peerId.id._internalGetInt64Value())", state)
        self.assertNotIn("withIsEnabled(true)", state)
        helper = self.read("submodules/TelegramCore/Sources/TelegramEngine/Messages/JoboxTranslationMessages.swift")
        self.assertIn("sgJoboxTranslate([entry.value]", helper)
        self.assertIn("transaction.getMessage(entry.key)?.text == entry.value", helper)
        self.assertIn("TranslationMessageAttribute(text: translatedText", helper)
        self.assertNotIn("gtranslate(", helper)
        self.assertNotIn("aroundMessageHistoryView", helper)
        self.assertNotIn("Timer", helper)

    def test_account_override_never_rewrites_global_backend_on_configuration_save(self):
        self.apply()
        controller = self.read("Swiftgram/SGSettingsUI/Sources/JoboxTranslationSettingsController.swift")
        self.assertNotIn("SGSimpleSettings.shared.translationBackend =", controller)
        self.assertIn("enabled: self.enabled.isOn", controller)
        self.assertIn("self.token.isSecureTextEntry = true", controller)
        self.assertNotIn("self.token.text = JoboxTranslationSettings.token", controller)
        signal = self.read("Swiftgram/SGGTranslate/Sources/JoboxTranslationSignal.swift")
        self.assertIn("configuration(accountId: accountId)?.enabled == true", signal)
        self.assertIn("configuration.enabled", signal)
        self.assertNotIn("gtranslate(", signal)

    def test_generated_modules_have_direct_dependencies_and_native_sources_glob(self):
        self.apply()
        for path in ("Swiftgram/SGSettingsUI/BUILD", "submodules/TelegramUI/BUILD", "submodules/TranslateUI/BUILD"):
            build = self.read(path)
            self.assertIn('"//Swiftgram/SGGTranslate:SGGTranslate"', build)
            self.assertIn('"Sources/**/*.swift"', build)
        self.assertIn('"//submodules/AsyncDisplayKit:AsyncDisplayKit"', self.read("Swiftgram/SGSettingsUI/BUILD"))
        self.assertIn('"//Swiftgram/SGSimpleSettings:SGSimpleSettings"', self.read("Swiftgram/SGGTranslate/BUILD"))


if __name__ == "__main__":
    unittest.main()
