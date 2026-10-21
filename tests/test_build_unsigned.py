import importlib.util
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import sys
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "build_unsigned", Path(__file__).resolve().parents[1] / "scripts/build_unsigned.py"
)
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)
from patch_personal_sideload import personal_sideload_receipt


class UnsignedBuildTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.upstream = self.root / "upstream"
        self.upstream.mkdir()
        signing = self.upstream / build.RULES_APPLE_PATH / "apple/internal/codesigning_support.bzl"
        signing.parent.mkdir(parents=True)
        signing.write_text('if "disable_legacy_signing" in features:\n    pass\n', encoding="utf-8")
        self.config = self.root / "configuration.json"
        self.configuration = {
            "api_id": 0, "api_hash": "0" * 32,
            "bundle_id": "org.jobox.swiftgram.buildcheck", "team_id": "0000000000",
            "sg_config": json.dumps({
                "api_url": "https://jobox-build.invalid",
                "webapp_url": "https://jobox-build.invalid",
                "bot_username": "", "public_key": None, "iaps": [],
            }),
        }
        self.write_configuration()
        self.bazel = self.root / "bazel"
        self.bazel.touch()
        self.output = self.root / "artifacts/Swiftgram-unsigned.ipa"
        self.command = Mock()
        self.command.common_build_args = []
        self.command.common_debug_args = [
            '--@build_bazel_rules_swift//swift:copt="-j"',
            '--@build_bazel_rules_swift//swift:copt="2"',
        ]
        self.configuration_debug_args = []
        self.command.set_configuration.side_effect = lambda _: self.configuration_debug_args.extend(self.command.common_debug_args)
        self.make = Mock()
        self.make.BazelCommandLine.return_value = self.command
        self.command.invoke_build.side_effect = self.create_ipa
        for target, value in [
            ("platform.system", "Darwin"), ("platform.machine", "x86_64")
        ]:
            patcher = patch.object(build.platform, target.split(".")[1], return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        importer = patch.object(build.importlib, "import_module", return_value=self.make)
        self.importer = importer.start()
        self.addCleanup(importer.stop)
        revision = patch.object(build, "git_revision", side_effect=[
            build.UPSTREAM_SHA, build.RULES_APPLE_SHA, build.RULES_APPLE_SHA
        ])
        revision.start()
        self.addCleanup(revision.stop)
        profile_fix = patch.object(build, "apply_unsigned_profile_fix", return_value={"reviewed": True})
        self.profile_fix = profile_fix.start()
        self.addCleanup(profile_fix.stop)
        personal_fix = patch.object(build, "apply_personal_sideload_fix", return_value=personal_sideload_receipt())
        self.personal_fix = personal_fix.start()
        self.addCleanup(personal_fix.stop)

    def write_configuration(self):
        self.config.write_text(json.dumps(self.configuration), encoding="utf-8")

    def create_ipa(self):
        ipa = self.upstream / "bazel-bin/Telegram/Swiftgram.ipa"
        ipa.parent.mkdir(parents=True, exist_ok=True)
        ipa.write_bytes(b"mock-unsigned-ipa")

    def run_build(self):
        return build.build_unsigned(self.upstream, self.config, self.bazel, 42, self.output)

    def test_invokes_device_build_without_signing_or_remote_credentials(self):
        previous_directory = Path.cwd()
        result = self.run_build()
        self.assertEqual(result, self.output)
        self.assertEqual(self.output.read_bytes(), b"mock-unsigned-ipa")
        self.assertEqual(Path.cwd(), previous_directory)
        self.profile_fix.assert_called_once_with(self.upstream)
        self.personal_fix.assert_not_called()
        self.importer.assert_called_once_with("Make")
        receipt = json.loads(self.output.with_suffix(".build.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["source_sha"], build.UPSTREAM_SHA)
        self.assertEqual(receipt["configuration"], "debug_arm64")
        self.assertEqual(receipt["unsigned_profile_fix"], {"reviewed": True})
        self.make.BazelCommandLine.assert_called_once_with(
            bazel=str(self.bazel), override_bazel_version=False,
            override_xcode_version=False, bazel_user_root=None,
        )
        self.command.set_configuration.assert_called_once_with("debug_arm64")
        self.assertEqual(self.configuration_debug_args, [
            '--@build_bazel_rules_swift//swift:copt=-j',
            '--@build_bazel_rules_swift//swift:copt=2',
        ])
        self.command.set_custom_target.assert_called_once_with("//Telegram:Swiftgram")
        self.command.set_build_number.assert_called_once_with(42)
        self.command.set_disable_provisioning_profiles.assert_called_once_with()
        self.command.invoke_build.assert_called_once_with()
        self.assertEqual(self.command.common_build_args, [
            "--features=disable_legacy_signing", "--define=apple.package_swift_support=no",
            "--define=apple.compress_ipa=yes", "--jobs=2"
        ])
        arguments = self.make.resolve_configuration.call_args.kwargs["arguments"]
        self.assertIsNone(arguments.gitCodesigningRepository)
        self.assertIsNone(arguments.codesigningInformationPath)
        self.assertTrue(arguments.xcodeManagedCodesigning)

    def configure_personal_build(self):
        # Synthetic application values exercise propagation without loading real credentials.
        self.configuration.update(api_id=123456, api_hash="a" * 32)
        self.write_configuration()
        self.translation_receipt = {
            "source_revision": build.UPSTREAM_SHA,
            "changed_files": [{"path": "fixture.swift", "before_sha256": "1" * 64, "after_sha256": "2" * 64}],
        }
        self.translation = SimpleNamespace(apply_jobox_translation=Mock(return_value=self.translation_receipt))
        self.importer.side_effect = lambda name: self.translation if name == "patch_jobox_translation" else self.make

    def run_personal_build(self):
        return build.build_unsigned(self.upstream, self.config, self.bazel, 42, self.output, profile="personal")

    def test_personal_configuration_cannot_enter_the_default_buildcheck(self):
        self.configure_personal_build()
        with self.assertRaisesRegex(ValueError, "api_id=0"):
            self.run_build()
        self.importer.assert_not_called()
        self.personal_fix.assert_not_called()
        self.profile_fix.assert_not_called()
        self.command.invoke_build.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_personal_build_applies_both_patches_before_make_and_preserves_unsigned_gate(self):
        self.configure_personal_build()

        def verify_adjustments_before_configure(**arguments):
            self.translation.apply_jobox_translation.assert_called_once_with(self.upstream)
            self.personal_fix.assert_called_once_with(self.upstream)
            self.profile_fix.assert_called_once_with(self.upstream)
            self.assertIsNone(arguments["arguments"].gitCodesigningRepository)
            self.assertIsNone(arguments["arguments"].codesigningInformationPath)

        self.make.resolve_configuration.side_effect = verify_adjustments_before_configure
        captured = io.StringIO()
        with redirect_stdout(captured), patch.dict(build.os.environ, {"GITHUB_SHA": "3" * 40,
                                                                     "JOBOX_TEST_TOKEN": "synthetic-private-token"}):
            self.assertEqual(self.run_personal_build(), self.output)
        self.assertEqual([item.args[0] for item in self.importer.call_args_list], ["patch_jobox_translation", "Make"])
        self.command.set_configuration.assert_called_once_with("debug_arm64")
        self.command.set_custom_target.assert_called_once_with("//Telegram:Swiftgram")
        self.command.set_disable_provisioning_profiles.assert_called_once_with()
        self.assertEqual(self.command.common_build_args, [
            "--features=disable_legacy_signing", "--define=apple.package_swift_support=no",
            "--define=apple.compress_ipa=yes", "--jobs=2", "--//Telegram:disableExtensions=true",
        ])
        receipt_text = self.output.with_suffix(".build.json").read_text(encoding="utf-8")
        receipt = json.loads(receipt_text)
        self.assertEqual(receipt["build_profile"], "personal")
        self.assertEqual(receipt["api_mode"], "private-application")
        self.assertEqual(receipt["personal_sideload_fix"], personal_sideload_receipt())
        self.assertEqual(receipt["translation_patch"], self.translation_receipt)
        self.assertEqual(receipt["unsigned_profile_fix"], {"reviewed": True})
        self.assertEqual(receipt["source_sha"], build.UPSTREAM_SHA)
        self.assertEqual(receipt["harness_commit"], "3" * 40)
        for secret in (self.configuration["api_hash"], "synthetic-private-token", "api_hash", "JOBOX_TEST_TOKEN"):
            self.assertNotIn(secret, receipt_text + captured.getvalue())

    def test_personal_requires_nonzero_integer_api_and_nonplaceholder_hash(self):
        self.configure_personal_build()
        for api_id, api_hash in (
            (0, "a" * 32), (-1, "a" * 32), (True, "a" * 32), ("123456", "a" * 32),
            (2147483648, "a" * 32), (123456, "0" * 32), (123456, "a" * 31),
            (123456, "z" * 32), (123456, None), (123456, 123), (123456, ["a" * 32]),
        ):
            with self.subTest(api_id_type=type(api_id).__name__, hash_type=type(api_hash).__name__):
                self.configuration.update(api_id=api_id, api_hash=api_hash)
                self.write_configuration()
                with self.assertRaisesRegex(ValueError, "private Telegram application configuration"):
                    build.validate_configuration(self.config, profile="personal")
        self.importer.assert_not_called()
        self.personal_fix.assert_not_called()
        self.command.invoke_build.assert_not_called()

    def test_personal_build_still_requires_the_fixed_upstream_revision(self):
        self.configure_personal_build()
        build.git_revision.side_effect = ["f" * 40]
        with self.assertRaisesRegex(ValueError, "source revision"):
            self.run_personal_build()
        self.importer.assert_not_called()
        self.personal_fix.assert_not_called()
        self.profile_fix.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_unknown_profile_does_not_apply_patches_or_build(self):
        with self.assertRaisesRegex(ValueError, "Unknown build profile"):
            build.build_unsigned(self.upstream, self.config, self.bazel, 42, self.output, profile="production")
        self.importer.assert_not_called()
        self.personal_fix.assert_not_called()
        self.profile_fix.assert_not_called()
        self.command.invoke_build.assert_not_called()

    def test_personal_capability_drift_stops_before_make_or_output(self):
        self.configure_personal_build()
        self.personal_fix.side_effect = ValueError("personal sideload source drift")
        with self.assertRaisesRegex(ValueError, "source drift"):
            self.run_personal_build()
        self.assertEqual([item.args[0] for item in self.importer.call_args_list], ["patch_jobox_translation"])
        self.profile_fix.assert_not_called()
        self.command.invoke_build.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_personal_translation_drift_stops_before_make_or_output(self):
        self.configure_personal_build()
        self.translation.apply_jobox_translation.side_effect = ValueError("translation source drift")
        with self.assertRaisesRegex(ValueError, "source drift"):
            self.run_personal_build()
        self.profile_fix.assert_not_called()
        self.command.invoke_build.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_rejects_live_api_configuration_before_import_or_build(self):
        self.configuration["api_id"] = 123456
        self.write_configuration()
        with self.assertRaisesRegex(ValueError, "api_id=0"):
            self.run_build()
        build.importlib.import_module.assert_not_called()
        self.profile_fix.assert_not_called()
        self.command.invoke_build.assert_not_called()

    def test_rejects_live_swiftgram_endpoint(self):
        self.configuration["sg_config"] = json.dumps({"apiUrl": "https://api.swiftgram.app"})
        self.write_configuration()
        with self.assertRaisesRegex(ValueError, ".invalid"):
            self.run_build()
        self.command.invoke_build.assert_not_called()

    def test_rejects_empty_config_and_revision_drift(self):
        self.configuration["sg_config"] = "{}"
        self.write_configuration()
        with self.assertRaisesRegex(ValueError, "full .invalid build configuration"):
            self.run_build()
        build.git_revision.side_effect = ["f" * 40]
        with self.assertRaisesRegex(ValueError, "source revision"):
            self.run_build()
        self.command.invoke_build.assert_not_called()

    def test_rejects_invalid_endpoint_under_wrong_keys_that_trigger_upstream_defaults(self):
        self.configuration["sg_config"] = json.dumps({"apiUrl":"https://jobox-build.invalid"})
        self.write_configuration()
        with self.assertRaisesRegex(ValueError,"missing keys restore upstream defaults"):
            self.run_build()
        self.command.invoke_build.assert_not_called()

    def test_preserves_existing_artifact_and_restores_directory_on_failure(self):
        self.output.parent.mkdir()
        self.output.write_bytes(b"existing")
        with self.assertRaises(FileExistsError):
            self.run_build()
        self.assertEqual(self.output.read_bytes(), b"existing")
        self.output.unlink()
        previous_directory = Path.cwd()
        self.command.invoke_build.side_effect = RuntimeError("compile failed")
        with self.assertRaisesRegex(RuntimeError, "compile failed"):
            self.run_build()
        self.assertEqual(Path.cwd(), previous_directory)
        self.assertFalse(self.output.exists())

    def test_missing_device_output_does_not_publish_placeholder(self):
        self.command.invoke_build.side_effect = None
        with self.assertRaisesRegex(FileNotFoundError, "device IPA"):
            self.run_build()
        self.assertFalse(self.output.exists())

    def test_profile_source_drift_stops_before_build(self):
        self.profile_fix.side_effect = ValueError("unexpected profile rule hash")
        with self.assertRaisesRegex(ValueError, "rule hash"):
            self.run_build()
        build.importlib.import_module.assert_not_called()
        self.command.invoke_build.assert_not_called()
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
