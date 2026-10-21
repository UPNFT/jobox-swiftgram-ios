import gzip
import hashlib
import importlib.util
import os
from pathlib import Path
import re
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location(
    "patch_personal_sideload",
    Path(__file__).resolve().parents[1] / "scripts/patch_personal_sideload.py",
)
fix = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fix)
# Exact public upstream bytes; gzip preserves its whitespace without adding it to diffs.
FIXTURE = Path(__file__).parent / "fixtures/Telegram.BUILD.gz"


def generated_entitlements(content):
    source = content.decode("utf-8")
    start = source.index("official_apple_pay_merchants = [")
    end = source.index('genrule(\n    name = "VersionInfoPlist",', start)
    captured = {}
    namespace = {
        "telegram_bundle_id": "org.jobox.swiftgram.buildcheck",
        "telegram_team_id": "PERSONAL01",
        "telegram_aps_environment": "",
        "telegram_enable_icloud": False,
        "telegram_enable_siri": False,
        "plist_fragment": lambda **arguments: captured.update(arguments),
    }
    exec(compile(source[start:end], "upstream_entitlements.bzl", "exec"), namespace)
    return captured["template"]


class PersonalSideloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = gzip.decompress(FIXTURE.read_bytes())

    def test_actual_pinned_source_and_after_hashes_are_not_mocked(self):
        self.assertEqual(hashlib.sha256(self.original).hexdigest(), fix.BEFORE_SHA256)
        self.assertEqual(hashlib.sha256(fix.patch_content(self.original)).hexdigest(), fix.AFTER_SHA256)

    def test_generated_personal_entitlements_preserve_identity_and_shared_container(self):
        before = generated_entitlements(self.original)
        after = generated_entitlements(fix.patch_content(self.original))
        for capability in fix.REMOVED_CAPABILITIES:
            self.assertIn("<key>" + capability + "</key>", before)
            self.assertNotIn(capability, after)
        self.assertIn("<key>com.apple.security.application-groups</key>", after)
        self.assertIn("<string>group.org.jobox.swiftgram.buildcheck</string>", after)
        self.assertIn("<key>application-identifier</key>", after)
        self.assertIn("<string>PERSONAL01.org.jobox.swiftgram.buildcheck</string>", after)
        for unsupported in ("aps-environment", "com.apple.developer.siri", "associated-domains", "icloud"):
            self.assertNotIn(unsupported, after)

    def test_exactly_three_reviewed_fragments_change_and_other_rules_stay_identical(self):
        patched = fix.patch_content(self.original)
        self.assertEqual(len(fix.REPLACEMENTS), 3)
        restored = patched
        for before, after in fix.REPLACEMENTS:
            self.assertEqual(self.original.count(before), 1)
            self.assertEqual(patched.count(after), 1)
            restored = restored.replace(after, before, 1)
        self.assertEqual(restored, self.original)

    def test_only_main_app_display_name_changes_and_internal_identity_is_preserved(self):
        patched = fix.patch_content(self.original)
        source = patched.decode("utf-8")
        start = source.index('name = "TelegramInfoPlist",')
        end = source.index('    """.format(', start)
        main_plist = source[start:end]
        values = dict(re.findall(r"<key>(CFBundleDisplayName|CFBundleName|CFBundleIdentifier)</key>\s*<string>([^<]+)</string>", main_plist))
        self.assertEqual(values, {"CFBundleDisplayName": "Jobox", "CFBundleName": "Swiftgram",
                                  "CFBundleIdentifier": "{telegram_bundle_id}"})
        self.assertEqual(source.count('<string>Jobox</string>'), 1)
        for retained in (b'ios_application(\n    name = "Swiftgram",',
                         b'bundle_id = "{telegram_bundle_id}".format(',
                         b'project_name = "Swiftgram",', b'name = "AppNameInfoPlist",'):
            self.assertGreater(self.original.count(retained), 0)
            self.assertEqual(patched.count(retained), self.original.count(retained))

    def test_extension_removal_uses_the_existing_opt_in_flag_without_rewriting_rules(self):
        self.assertEqual(fix.REQUIRED_BUILD_FLAGS, ("--//Telegram:disableExtensions=true",))
        patched = fix.patch_content(self.original)
        for marker in (
            b'name = "disableExtensions",\n    build_setting_default = False,',
            b'extensions = select({\n        ":disableExtensionsSetting": [],',
            b'provisioning_profile = select({',
        ):
            self.assertEqual(patched.count(marker), self.original.count(marker))
            self.assertGreater(patched.count(marker), 0)

    def test_idempotent_reviewed_result_rejects_other_byte_drift(self):
        patched = fix.patch_content(self.original)
        self.assertIs(fix.patch_content(patched), patched)
        for changed in (self.original + b"\n", patched + b"\n", self.original.replace(b"group.", b"other.", 1)):
            with self.subTest(digest=hashlib.sha256(changed).hexdigest()):
                with self.assertRaisesRegex(ValueError, "source drift"):
                    fix.patch_content(changed)
        old_capabilities_only = self.original
        for before, after in fix.REPLACEMENTS[:2]:
            old_capabilities_only = old_capabilities_only.replace(before, after, 1)
        with self.assertRaisesRegex(ValueError, "source drift"):
            fix.patch_content(old_capabilities_only)

    def test_applier_is_explicit_records_receipt_and_does_not_rewrite_an_applied_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / fix.RELATIVE_PATH
            target.parent.mkdir()
            target.write_bytes(self.original)
            untouched = root / "other.bin"
            untouched.write_bytes(b"preserve default and signed build inputs")
            self.assertEqual(target.read_bytes(), self.original)
            receipt = fix.apply_personal_sideload_fix(root)
            self.assertEqual(receipt, fix.personal_sideload_receipt())
            self.assertEqual(receipt["required_build_flags"], list(fix.REQUIRED_BUILD_FLAGS))
            self.assertEqual(target.read_bytes(), fix.patch_content(self.original))
            os.utime(target, ns=(1_000_000_000, 1_000_000_000))
            fix.apply_personal_sideload_fix(root)
            self.assertEqual(target.stat().st_mtime_ns, 1_000_000_000)
            self.assertEqual(untouched.read_bytes(), b"preserve default and signed build inputs")

    def test_applier_refuses_drift_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / fix.RELATIVE_PATH
            target.parent.mkdir()
            drift = self.original + b"# user edit\n"
            target.write_bytes(drift)
            with self.assertRaisesRegex(ValueError, "source drift"):
                fix.apply_personal_sideload_fix(directory)
            self.assertEqual(target.read_bytes(), drift)

    def test_applier_refuses_a_symlink_outside_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "upstream"
            root.mkdir()
            (root / "Telegram").mkdir()
            outside = Path(directory) / "BUILD"
            outside.write_bytes(self.original)
            try:
                (root / fix.RELATIVE_PATH).symlink_to(outside)
            except OSError as error:
                self.skipTest("File symlinks unavailable: " + str(error))
            with self.assertRaisesRegex(ValueError, "inside the upstream"):
                fix.apply_personal_sideload_fix(root)
            self.assertEqual(outside.read_bytes(), self.original)


if __name__ == "__main__":
    unittest.main()
