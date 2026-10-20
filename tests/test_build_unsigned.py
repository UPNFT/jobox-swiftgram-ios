import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import sys
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "build_unsigned", Path(__file__).resolve().parents[1] / "scripts/build_unsigned.py"
)
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)


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
        importer.start()
        self.addCleanup(importer.stop)
        revision = patch.object(build, "git_revision", side_effect=[
            build.UPSTREAM_SHA, build.RULES_APPLE_SHA, build.RULES_APPLE_SHA
        ])
        revision.start()
        self.addCleanup(revision.stop)
        profile_fix = patch.object(build, "apply_unsigned_profile_fix", return_value={"reviewed": True})
        self.profile_fix = profile_fix.start()
        self.addCleanup(profile_fix.stop)

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
        receipt = json.loads(self.output.with_suffix(".build.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["source_sha"], build.UPSTREAM_SHA)
        self.assertEqual(receipt["unsigned_profile_fix"], {"reviewed": True})
        self.make.BazelCommandLine.assert_called_once_with(
            bazel=str(self.bazel), override_bazel_version=False,
            override_xcode_version=False, bazel_user_root=None,
        )
        self.command.set_configuration.assert_called_once_with("release_arm64")
        self.command.set_custom_target.assert_called_once_with("//Telegram:Swiftgram")
        self.command.set_build_number.assert_called_once_with(42)
        self.command.set_disable_provisioning_profiles.assert_called_once_with()
        self.command.invoke_build.assert_called_once_with()
        self.assertEqual(self.command.common_build_args, [
            "--features=disable_legacy_signing", "--define=apple.package_swift_support=no", "--jobs=2"
        ])
        arguments = self.make.resolve_configuration.call_args.kwargs["arguments"]
        self.assertIsNone(arguments.gitCodesigningRepository)
        self.assertIsNone(arguments.codesigningInformationPath)
        self.assertTrue(arguments.xcodeManagedCodesigning)

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
