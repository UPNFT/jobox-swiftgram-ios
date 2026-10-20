import importlib.util
from pathlib import Path
import plistlib
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from types import SimpleNamespace

SPEC = importlib.util.spec_from_file_location("verify_ipa", Path(__file__).parents[1] / "scripts" / "verify_ipa.py")
verify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify)


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.ipa = Path(self.directory.name) / "check.ipa"

    def create(self, platform="iPhoneOS", extra=None, bundle_id=verify.BUNDLE_ID):
        with zipfile.ZipFile(self.ipa, "w") as archive:
            archive.writestr("Payload/Swiftgram.app/Info.plist", plistlib.dumps({
                "CFBundleSupportedPlatforms": [platform],
                "CFBundleIdentifier": bundle_id, "CFBundleExecutable": "Swiftgram"}))
            archive.writestr("Payload/Swiftgram.app/Swiftgram", struct.pack("<II", 0xFEEDFACF, 0x0100000C) + b"\0" * 24)
            for name, content in (extra or {}).items():
                archive.writestr(name, content)

    def test_device_archive_is_accepted(self):
        self.create()
        info, _ = verify.inspect_archive(self.ipa)
        self.assertEqual(info["CFBundleIdentifier"], verify.BUNDLE_ID)

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
        run.side_effect = [SimpleNamespace(stdout=" platform IOS\n"),
                           SimpleNamespace(returncode=1,stderr="code object is not signed at all")]
        verify.verify_platform(Path("fixture-binary"))
        run.side_effect = [SimpleNamespace(stdout=" platform IOSSIMULATOR\n")]
        with self.assertRaisesRegex(ValueError,"not iOS device"):
            verify.verify_platform(Path("fixture-binary"))
        run.side_effect = [SimpleNamespace(stdout=" platform IOS\n"),
                           SimpleNamespace(returncode=0,stderr="Signature=adhoc\nTeamIdentifier=not set")]
        self.assertEqual(verify.verify_platform(Path("fixture-binary")),"adhoc")
        run.side_effect = [SimpleNamespace(stdout=" platform IOS\n"),
                           SimpleNamespace(returncode=0,stderr="Authority=Apple Development\nTeamIdentifier=TESTTEAM")]
        with self.assertRaisesRegex(ValueError,"Apple identity"):
            verify.verify_platform(Path("fixture-binary"))


if __name__ == "__main__":
    unittest.main()
