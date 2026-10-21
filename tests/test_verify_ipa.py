import copy
import importlib.util
import json
from pathlib import Path
import plistlib
import re
import struct
import tempfile
import unittest
import sys
from unittest.mock import call, Mock, patch
import zipfile
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
SPEC = importlib.util.spec_from_file_location("verify_ipa", Path(__file__).parents[1] / "scripts" / "verify_ipa.py")
verify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify)


MODERN_IOS = """Load command 8
      cmd LC_BUILD_VERSION
  cmdsize 32
 platform IOS
    minos 13.0
      sdk 26.2
"""
LEGACY_IOS = """Load command 7
      cmd LC_VERSION_MIN_IPHONEOS
  cmdsize 16
  version 9.0
      sdk 12.0
"""


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.ipa = Path(self.directory.name) / "check.ipa"

    def create(self, platform="iPhoneOS", extra=None, bundle_id=verify.BUNDLE_ID, display_name="Jobox"):
        info = {"CFBundleSupportedPlatforms": [platform],
                "CFBundleIdentifier": bundle_id, "CFBundleExecutable": "Swiftgram"}
        if display_name is not None:
            info["CFBundleDisplayName"] = display_name
        with zipfile.ZipFile(self.ipa, "w") as archive:
            archive.writestr("Payload/Swiftgram.app/Info.plist", plistlib.dumps(info))
            archive.writestr("Payload/Swiftgram.app/Swiftgram", struct.pack("<II", 0xFEEDFACF, 0x0100000C) + b"\0" * 24)
            for name, content in (extra or {}).items():
                archive.writestr(name, content)

    def test_device_archive_is_accepted(self):
        self.create(display_name="Swiftgram")
        info, _ = verify.inspect_archive(self.ipa)
        self.assertEqual(info["CFBundleIdentifier"], verify.BUNDLE_ID)
        self.assertEqual(info["CFBundleDisplayName"], "Swiftgram")

    def test_app_store_support_is_rejected_only_at_archive_root(self):
        for name in ("SwiftSupport/", "SwiftSupport/iphoneos/libswift_Concurrency.dylib"):
            with self.subTest(name=name):
                self.create(extra={name: b"fixture"})
                with self.assertRaisesRegex(ValueError, "App Store SwiftSupport"):
                    verify.inspect_archive(self.ipa)
        self.create(extra={"Payload/Swiftgram.app/Assets/SwiftSupport/readme.txt": b"keep"})
        verify.inspect_archive(self.ipa)

    def test_build_provenance_requires_reviewed_rules_and_harness_commit(self):
        path = Path(self.directory.name) / "receipt.json"
        receipt = {"source_sha": verify.SOURCE_SHA, "configuration": "debug_arm64",
                   "unsigned_profile_fix": verify.profile_fix_receipt(), "harness_commit": "1" * 40}
        path.write_text(json.dumps(receipt), encoding="utf-8")
        self.assertEqual(verify.read_build_receipt(path), receipt)
        for configuration in (None, "release_arm64", "debug_sim_arm64"):
            with self.subTest(configuration=configuration):
                receipt["configuration"] = configuration
                path.write_text(json.dumps(receipt), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "provenance"):
                    verify.read_build_receipt(path)
        receipt["configuration"] = "debug_arm64"
        receipt["unsigned_profile_fix"]["after_sha256"] = "0" * 64
        path.write_text(json.dumps(receipt), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "provenance"):
            verify.read_build_receipt(path)

    def prepare_personal_receipt(self, profile="personal"):
        self.translation_receipt = {
            "source_revision": verify.SOURCE_SHA,
            "changed_files": [{"path": "fixture.swift", "before_sha256": "1" * 64, "after_sha256": "2" * 64}],
        }
        self.translation_receipt_reader = Mock(return_value=self.translation_receipt)
        patcher = patch.dict(sys.modules, {"patch_jobox_translation": SimpleNamespace(
            translation_patch_receipt=self.translation_receipt_reader,
        )})
        patcher.start()
        self.addCleanup(patcher.stop)
        return {
            "source_sha": verify.SOURCE_SHA, "configuration": "debug_arm64",
            "unsigned_profile_fix": verify.profile_fix_receipt(), "harness_commit": "3" * 40,
            "build_profile": profile,
            "api_mode": "official-test-only" if profile == "prototype" else "private-application",
            "test_api_source": verify.test_configuration_receipt() if profile == "prototype" else None,
            "personal_sideload_fix": verify.personal_sideload_receipt(),
            "translation_patch": copy.deepcopy(self.translation_receipt),
        }

    def write_receipt(self, receipt):
        path = Path(self.directory.name) / "receipt.json"
        path.write_text(json.dumps(receipt), encoding="utf-8")
        return path

    def test_personal_provenance_requires_exact_reviewed_patches_and_private_api_mode(self):
        self.assert_integrated_provenance("personal")

    def test_prototype_requires_exact_patches_and_official_test_source(self):
        self.assert_integrated_provenance("prototype")

    def assert_integrated_provenance(self, profile):
        reviewed = self.prepare_personal_receipt(profile)
        self.assertEqual(verify.read_build_receipt(self.write_receipt(reviewed)), reviewed)
        self.translation_receipt_reader.assert_called_once_with()
        variants = []
        for field in ("personal_sideload_fix", "translation_patch"):
            missing = copy.deepcopy(reviewed)
            missing.pop(field)
            variants.append(missing)
            drift = copy.deepcopy(reviewed)
            drift[field]["unreviewed_extra_adjustment"] = True
            variants.append(drift)
        changed_hash = copy.deepcopy(reviewed)
        changed_hash["personal_sideload_fix"]["after_sha256"] = "0" * 64
        variants.append(changed_hash)
        changed_translation = copy.deepcopy(reviewed)
        changed_translation["translation_patch"]["changed_files"][0]["after_sha256"] = "0" * 64
        variants.append(changed_translation)
        for mode in (None, "invalid-placeholder", "live",
                     "private-application" if profile == "prototype" else "official-test-only"):
            changed_mode = copy.deepcopy(reviewed)
            changed_mode["api_mode"] = mode
            variants.append(changed_mode)
        wrong_source = copy.deepcopy(reviewed)
        wrong_source["test_api_source"] = {"source_url": "https://unreviewed.invalid", "document_sha256": "0" * 64}
        variants.append(wrong_source)
        if profile == "prototype":
            missing_source = copy.deepcopy(reviewed)
            missing_source.pop("test_api_source")
            variants.append(missing_source)
            for field in ("source_url", "document_sha256"):
                source_drift = copy.deepcopy(reviewed)
                source_drift["test_api_source"].pop(field)
                variants.append(source_drift)
                changed_source = copy.deepcopy(reviewed)
                changed_source["test_api_source"][field] = "0" * 64 if field == "document_sha256" else "https://unreviewed.invalid"
                variants.append(changed_source)
            extra_source = copy.deepcopy(reviewed)
            extra_source["test_api_source"]["unreviewed_override"] = True
            variants.append(extra_source)
        for receipt in variants:
            with self.subTest(receipt=receipt):
                with self.assertRaisesRegex(ValueError, profile + " translation provenance"):
                    verify.read_build_receipt(self.write_receipt(receipt))

    def test_unknown_profiles_and_personal_changes_in_buildcheck_are_rejected(self):
        reviewed = self.prepare_personal_receipt()
        for profile in (None, "", "production", "buildcheck"):
            with self.subTest(profile=profile):
                receipt = copy.deepcopy(reviewed)
                receipt["build_profile"] = profile
                with self.assertRaisesRegex(ValueError, "build-check profile"):
                    verify.read_build_receipt(self.write_receipt(receipt))
        for field in ("personal_sideload_fix", "translation_patch"):
            receipt = copy.deepcopy(reviewed)
            receipt.update(build_profile="buildcheck", api_mode="invalid-placeholder")
            receipt["translation_patch"] = None
            receipt["personal_sideload_fix"] = None
            receipt[field] = reviewed[field]
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, "build-check profile"):
                    verify.read_build_receipt(self.write_receipt(receipt))

    def test_personal_archive_rejects_extensions_even_without_info_or_executable(self):
        for name in (
            "Payload/Swiftgram.app/PlugIns/Share.appex/",
            "Payload/Swiftgram.app/PlugIns/Share.appex/leftover.dat",
            "Payload/Swiftgram.app/PlugIns/Share.appex/Info.plist",
        ):
            with self.subTest(name=name):
                self.create(extra={name: b"extension residue"})
                with self.assertRaisesRegex(ValueError, "extensions"):
                    verify.inspect_archive(self.ipa, personal=True)
        self.create(extra={"Payload/Swiftgram.app/Assets/notes.appex.txt": b"ordinary resource"})
        verify.inspect_archive(self.ipa, personal=True)

    def test_default_archive_inspection_keeps_extensions_and_personal_keeps_frameworks(self):
        executable = struct.pack("<II", 0xFEEDFACF, 0x0100000C) + b"\0" * 24
        extension_root = "Payload/Swiftgram.app/PlugIns/Share.appex/"
        self.create(extra={
            extension_root + "Info.plist": plistlib.dumps({"CFBundleExecutable": "Share"}),
            extension_root + "Share": executable,
        })
        _, binaries = verify.inspect_archive(self.ipa)
        self.assertIn(extension_root + "Share", binaries)
        framework_root = "Payload/Swiftgram.app/Frameworks/Runtime.framework/"
        self.create(extra={
            framework_root + "Info.plist": plistlib.dumps({"CFBundleExecutable": "Runtime"}),
            framework_root + "Runtime": executable,
        })
        _, binaries = verify.inspect_archive(self.ipa, personal=True)
        self.assertIn(framework_root + "Runtime", binaries)

    def test_personal_manifest_records_reviewed_provenance_without_claiming_device_success(self):
        self.assert_integrated_manifest("personal")

    def test_prototype_manifest_distinguishes_test_only_api_without_claiming_device_success(self):
        self.assert_integrated_manifest("prototype")

    def assert_integrated_manifest(self, profile):
        receipt = self.prepare_personal_receipt(profile)
        receipt_path = self.write_receipt(receipt)
        self.create()
        manifest_path = Path(self.directory.name) / "manifest.json"
        with patch.object(sys, "argv", [
            "verify_ipa.py", "--ipa", str(self.ipa), "--source-sha", verify.SOURCE_SHA,
            "--build-receipt", str(receipt_path), "--manifest", str(manifest_path),
        ]), patch.object(verify, "verify_platform", return_value="none") as check:
            verify.main()
        check.assert_called_once()
        manifest_text = manifest_path.read_text(encoding="utf-8")
        manifest = json.loads(manifest_text)
        self.assertEqual(manifest["build_profile"], profile)
        self.assertEqual(manifest["app_display_name"], "Jobox")
        self.assertEqual(manifest["api_mode"], "official-test-only" if profile == "prototype" else "private-application")
        self.assertEqual(manifest["test_api_source"], verify.test_configuration_receipt() if profile == "prototype" else None)
        self.assertEqual(manifest["translation_integration"], "jobox-configurable")
        self.assertEqual(manifest["source_adjustments"], [
            receipt["unsigned_profile_fix"], receipt["personal_sideload_fix"], receipt["translation_patch"],
        ])
        for field in ("apple_identity_signed", "login_verified", "translation_verified", "directly_installable"):
            self.assertIs(manifest[field], False)
        for field in ("api_hash", "token"):
            self.assertNotIn(field, manifest_text)

    def test_integrated_main_rejects_unbranded_app_before_tools_or_success_files(self):
        for profile in ("personal", "prototype"):
            receipt_path = self.write_receipt(self.prepare_personal_receipt(profile))
            for display_name in (None, "", "Swiftgram"):
                with self.subTest(profile=profile, display_name=display_name):
                    self.create(display_name=display_name)
                    manifest = Path(self.directory.name) / "manifest.json"
                    with patch.object(sys, "argv", [
                        "verify_ipa.py", "--ipa", str(self.ipa), "--source-sha", verify.SOURCE_SHA,
                        "--build-receipt", str(receipt_path), "--manifest", str(manifest),
                    ]), patch.object(verify, "verify_platform") as check:
                        with self.assertRaisesRegex(ValueError, "Jobox name"):
                            verify.main()
                    check.assert_not_called()
                    self.assertFalse(manifest.exists())
                    self.assertFalse(self.ipa.with_suffix(".ipa.sha256").exists())

    def test_integrated_main_rejects_extension_residue_before_tools_or_success_files(self):
        for profile in ("personal", "prototype"):
            with self.subTest(profile=profile):
                receipt_path = self.write_receipt(self.prepare_personal_receipt(profile))
                self.create(extra={"Payload/Swiftgram.app/PlugIns/Share.appex/orphan.dat": b"residue"})
                manifest = Path(self.directory.name) / "manifest.json"
                with patch.object(sys, "argv", [
                    "verify_ipa.py", "--ipa", str(self.ipa), "--source-sha", verify.SOURCE_SHA,
                    "--build-receipt", str(receipt_path), "--manifest", str(manifest),
                ]), patch.object(verify, "verify_platform") as check:
                    with self.assertRaisesRegex(ValueError, "extensions"):
                        verify.main()
                check.assert_not_called()
                self.assertFalse(manifest.exists())
                self.assertFalse(self.ipa.with_suffix(".ipa.sha256").exists())

    def test_prototype_rejects_bad_framework_platform_or_identity_signature_without_success_files(self):
        receipt_path = self.write_receipt(self.prepare_personal_receipt("prototype"))
        nested_name = "Payload/Swiftgram.app/Frameworks/Nested.dylib"
        self.create(extra={nested_name: b"synthetic framework executable"})
        manifest = Path(self.directory.name) / "manifest.json"
        valid_main = [SimpleNamespace(stdout="arm64"), SimpleNamespace(stdout=MODERN_IOS),
                      SimpleNamespace(returncode=1, stderr="code object is not signed at all")]
        rejected_nested = [
            [SimpleNamespace(stdout="arm64 arm64e"), SimpleNamespace(stdout=MODERN_IOS),
             SimpleNamespace(stdout=MODERN_IOS.replace("platform IOS", "platform WATCHOS"))],
            [SimpleNamespace(stdout="arm64"), SimpleNamespace(stdout=MODERN_IOS),
             SimpleNamespace(returncode=0, stderr="Authority=Apple Development\nTeamIdentifier=TESTTEAM")],
        ]
        for outputs in rejected_nested:
            with self.subTest(outputs=outputs), patch.object(sys, "argv", [
                "verify_ipa.py", "--ipa", str(self.ipa), "--source-sha", verify.SOURCE_SHA,
                "--build-receipt", str(receipt_path), "--manifest", str(manifest),
            ]), patch.object(verify.subprocess, "run", side_effect=valid_main + outputs) as run:
                with self.assertRaisesRegex(ValueError, re.escape(nested_name)):
                    verify.main()
                self.assertEqual(sum(item.args[0][:3] == ["xcrun", "lipo", "-archs"] for item in run.call_args_list), 2)
            self.assertFalse(manifest.exists())
            self.assertFalse(self.ipa.with_suffix(".ipa.sha256").exists())

    def test_prototype_keeps_the_450_mib_archive_budget(self):
        self.assertEqual(verify.MAX_IPA_BYTES, 450 * 1024 * 1024)
        receipt_path = self.write_receipt(self.prepare_personal_receipt("prototype"))
        self.create()
        manifest = Path(self.directory.name) / "manifest.json"
        with patch.object(sys, "argv", [
            "verify_ipa.py", "--ipa", str(self.ipa), "--source-sha", verify.SOURCE_SHA,
            "--build-receipt", str(receipt_path), "--manifest", str(manifest),
        ]), patch.object(verify, "MAX_IPA_BYTES", self.ipa.stat().st_size - 1), patch.object(verify, "verify_platform") as check:
            with self.assertRaisesRegex(ValueError, "450 MiB"):
                verify.main()
        check.assert_not_called()
        self.assertFalse(manifest.exists())

    def test_simulator_and_wrong_identity_are_rejected(self):
        for kwargs in [{"platform":"iPhoneSimulator"}, {"bundle_id":"ph.telegra.Telegraph"}]:
            with self.subTest(kwargs=kwargs):
                self.create(**kwargs)
                with self.assertRaises(ValueError):
                    verify.inspect_archive(self.ipa)

    def test_signing_material_and_unsafe_paths_are_rejected(self):
        for name in ["Payload/App.app/embedded.mobileprovision", "Payload/App.app/key.p12",
                     "../outside", "Payload/key.p8"]:
            with self.subTest(name=name):
                self.create(extra={name:b"fixture"})
                with self.assertRaises(ValueError):
                    verify.inspect_archive(self.ipa)
        self.create(extra={"Payload/App.app/cert.pem":b"-----BEGIN PRIVATE KEY-----"})
        with self.assertRaises(ValueError):
            verify.inspect_archive(self.ipa)

    @patch.object(verify.subprocess, "run")
    def test_macho_platform_and_unsigned_are_independently_verified(self, run):
        run.side_effect = [SimpleNamespace(stdout="arm64\n"), SimpleNamespace(stdout=MODERN_IOS),
                           SimpleNamespace(returncode=1,stderr="code object is not signed at all")]
        self.assertEqual(verify.verify_platform(Path("fixture-binary")), "none")
        self.assertEqual(run.call_args_list, [
            call(["xcrun", "lipo", "-archs", "fixture-binary"], check=True, capture_output=True, text=True),
            call(["xcrun", "vtool", "-arch", "arm64", "-show-build", "fixture-binary"],
                 check=True, capture_output=True, text=True),
            call(["codesign", "--display", "--verbose=4", "fixture-binary"],
                 capture_output=True, text=True),
        ])
        run.side_effect = [SimpleNamespace(stdout="arm64\n"), SimpleNamespace(stdout=LEGACY_IOS),
                           SimpleNamespace(returncode=0,stderr="Signature=adhoc\nTeamIdentifier=not set")]
        self.assertEqual(verify.verify_platform(Path("fixture-binary")),"adhoc")

    def test_modern_and_legacy_device_slices_are_accepted(self):
        for architecture in ("arm64", "arm64e", "armv7", "armv7s"):
            for output in (MODERN_IOS, MODERN_IOS.replace("platform IOS", "platform 2"), LEGACY_IOS):
                with self.subTest(architecture=architecture, output=output):
                    verify.validate_ios_slice(architecture, output)

    def test_simulator_architectures_are_rejected_even_with_iphoneos_command(self):
        for architecture in ("x86_64", "i386", "arm64_32"):
            for output in (MODERN_IOS, LEGACY_IOS):
                with self.subTest(architecture=architecture, output=output):
                    with self.assertRaisesRegex(ValueError, "not iOS device"):
                        verify.validate_ios_slice(architecture, output)

    def test_wrong_unknown_and_ambiguous_platform_commands_are_rejected(self):
        outputs = [MODERN_IOS.replace("platform IOS", "platform " + platform)
                   for platform in ("IOSSIMULATOR", "IOS_SIMULATOR", "7", "WATCHOS", "4",
                                    "TVOS", "3", "MACOS", "1", "MACCATALYST", "6", "UNKNOWN", "0")]
        outputs.extend([
            "", " platform IOS\n", MODERN_IOS.replace(" platform IOS\n", ""),
            MODERN_IOS + " platform IOS\n", MODERN_IOS + " platform WATCHOS\n",
            MODERN_IOS + LEGACY_IOS, MODERN_IOS + MODERN_IOS,
            MODERN_IOS + " cmd LC_BUILD_VERSION\n", LEGACY_IOS + LEGACY_IOS,
            LEGACY_IOS + " platform IOS\n",
            LEGACY_IOS.replace("LC_VERSION_MIN_IPHONEOS", "LC_VERSION_MIN_WATCHOS"),
            LEGACY_IOS.replace("LC_VERSION_MIN_IPHONEOS", "LC_VERSION_MIN_TVOS"),
            LEGACY_IOS.replace("LC_VERSION_MIN_IPHONEOS", "LC_VERSION_MIN_MACOSX"),
            " cmd LC_UNRECOGNIZED\n platform IOS\n",
        ])
        for output in outputs:
            with self.subTest(output=output):
                with self.assertRaisesRegex(ValueError, "not iOS device"):
                    verify.validate_ios_slice("arm64", output)

    @patch.object(verify.subprocess, "run")
    def test_fat_binary_checks_every_slice_before_accepting_signature(self, run):
        run.side_effect = [SimpleNamespace(stdout="arm64 arm64e\n"),
                           SimpleNamespace(stdout=MODERN_IOS), SimpleNamespace(stdout=LEGACY_IOS),
                           SimpleNamespace(returncode=1, stderr="code object is not signed at all")]
        self.assertEqual(verify.verify_platform(Path("fat-device")), "none")
        self.assertEqual(run.call_args_list[1].args[0],
                         ["xcrun", "vtool", "-arch", "arm64", "-show-build", "fat-device"])
        self.assertEqual(run.call_args_list[2].args[0],
                         ["xcrun", "vtool", "-arch", "arm64e", "-show-build", "fat-device"])
        self.assertEqual(run.call_args_list[3].args[0][0], "codesign")

    @patch.object(verify.subprocess, "run")
    def test_one_bad_fat_slice_cannot_be_hidden_by_good_ios_slice(self, run):
        for second_architecture, second_output in (
            ("arm64e", MODERN_IOS.replace("platform IOS", "platform WATCHOS")),
            ("arm64e", ""), ("x86_64", LEGACY_IOS),
        ):
            with self.subTest(architecture=second_architecture, output=second_output):
                run.reset_mock()
                run.side_effect = [SimpleNamespace(stdout="arm64 " + second_architecture),
                                   SimpleNamespace(stdout=MODERN_IOS), SimpleNamespace(stdout=second_output)]
                with self.assertRaisesRegex(ValueError, "not iOS device"):
                    verify.verify_platform(Path("fat-mixed"))
                self.assertEqual(run.call_count, 3)
                self.assertTrue(all(item.args[0][0] != "codesign" for item in run.call_args_list))

    @patch.object(verify.subprocess, "run")
    def test_missing_architecture_is_rejected(self, run):
        run.return_value = SimpleNamespace(stdout=" \n")
        with self.assertRaisesRegex(ValueError, "no readable architecture"):
            verify.verify_platform(Path("empty-binary"))
        self.assertEqual(run.call_count, 1)

    @patch.object(verify.subprocess, "run")
    def test_identity_signatures_and_uncertain_signature_failures_remain_rejected(self, run):
        signatures = [
            SimpleNamespace(returncode=0, stderr="Authority=Apple Development\nTeamIdentifier=TESTTEAM"),
            SimpleNamespace(returncode=0, stderr="Signature=adhoc\nAuthority=Apple Development\nTeamIdentifier=not set"),
            SimpleNamespace(returncode=0, stderr="Signature=adhoc\nTeamIdentifier=TESTTEAM"),
            SimpleNamespace(returncode=0, stderr="Signature=unknown\nTeamIdentifier=not set"),
            SimpleNamespace(returncode=1, stderr="unable to inspect code signature"),
        ]
        for signature in signatures:
            with self.subTest(signature=signature):
                run.side_effect = [SimpleNamespace(stdout="arm64"), SimpleNamespace(stdout=LEGACY_IOS), signature]
                with self.assertRaises(ValueError):
                    verify.verify_platform(Path("identity-binary"))

    def test_failure_names_real_archive_binary_and_never_writes_success_manifest(self):
        nested_name = "Payload/Swiftgram.app/Frameworks/LegacySupport.dylib"
        self.create(extra={nested_name: b"fixture dylib"})
        manifest = Path(self.directory.name) / "manifest.json"
        for error in (ValueError("Mach-O slice is not iOS device"),
                      verify.subprocess.CalledProcessError(1, ["xcrun", "vtool"])):
            with self.subTest(error=error):
                with patch.object(sys, "argv", [
                    "verify_ipa.py", "--ipa", str(self.ipa), "--source-sha", verify.SOURCE_SHA,
                    "--build-receipt", "fixture-receipt.json", "--manifest", str(manifest),
                ]), patch.object(verify, "read_build_receipt", return_value={}), \
                        patch.object(verify, "verify_platform", side_effect=["none", error]) as check:
                    with self.assertRaisesRegex(ValueError, re.escape(nested_name)):
                        verify.main()
                    self.assertEqual(check.call_count, 2)
                self.assertFalse(manifest.exists())
                self.assertFalse(self.ipa.with_suffix(".ipa.sha256").exists())


@unittest.skipUnless(sys.platform == "darwin", "Native Apple tool smoke test requires macOS")
class NativePlatformSmokeTests(unittest.TestCase):
    def test_native_tools_read_modern_and_legacy_arm64_load_commands(self):
        commands = {
            "modern": struct.pack("<IIIIII", 0x32, 24, 2, 13 << 16, (26 << 16) | (2 << 8), 0),
            "legacy": struct.pack("<IIII", 0x25, 16, 9 << 16, 12 << 16),
        }
        with tempfile.TemporaryDirectory() as directory:
            for name, command in commands.items():
                with self.subTest(format=name):
                    binary = Path(directory) / name
                    # Minimal arm64 MH_DYLIB: tools only inspect its header/load command.
                    header = struct.pack("<IIIIIIII", 0xFEEDFACF, 0x0100000C, 0, 6, 1, len(command), 0, 0)
                    binary.write_bytes(header + command)
                    architectures = verify.subprocess.run(
                        ["xcrun", "lipo", "-archs", str(binary)],
                        check=True, capture_output=True, text=True,
                    ).stdout.split()
                    self.assertEqual(architectures, ["arm64"])
                    output = verify.subprocess.run(
                        ["xcrun", "vtool", "-arch", "arm64", "-show-build", str(binary)],
                        check=True, capture_output=True, text=True,
                    ).stdout
                    verify.validate_ios_slice("arm64", output)


if __name__ == "__main__":
    unittest.main()
