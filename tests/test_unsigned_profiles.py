import hashlib
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


SPEC = importlib.util.spec_from_file_location(
    "patch_unsigned_profiles",
    Path(__file__).resolve().parents[1] / "scripts/patch_unsigned_profiles.py",
)
fix = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fix)


OTHER_FUNCTIONS = (
    "_ios_app_clip_impl", "_ios_imessage_application_impl",
    "_ios_imessage_extension_impl", "_ios_sticker_pack_extension_impl",
)


def fixture_function(name):
    return """def NAME(ctx):
    platform_prerequisites = ctx.platform_prerequisites
    provisioning_profile = ctx.provisioning_profile
    rule_descriptor = ctx.rule_descriptor
    features = ctx.features
    actions = ctx.actions
    label = ctx.label
    processor_partials = []
    if platform_prerequisites.platform.is_device:
        processor_partials.append(
            partials.provisioning_profile_partial(
                actions = actions,
                profile_artifact = provisioning_profile,
                rule_label = label,
            ),
        )
    return processor_partials

""".replace("NAME", name)


class UnsignedProfileFixTests(unittest.TestCase):
    def setUp(self):
        self.original = "".join(fixture_function(name) for name in fix.FUNCTIONS + OTHER_FUNCTIONS).encode()
        # Fixture checksums let the same fail-closed applier exercise small runnable rule bodies.
        reviewed = self.original.decode()
        for name in fix.FUNCTIONS:
            old = fixture_function(name)
            reviewed = reviewed.replace(old, old.replace(fix.BEFORE, fix.AFTER))
        self.expected = reviewed.encode()
        for name, value in (
            ("BEFORE_SHA256", hashlib.sha256(self.original).hexdigest()),
            ("AFTER_SHA256", hashlib.sha256(self.expected).hexdigest()),
        ):
            patcher = patch.object(fix, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def evaluate(self, function_name, *, device, unsigned, profile):
        # The pinned public should_sign_bundles helper decides False for this feature,
        # and True for ordinary application/extension rules without that feature.
        # Keep the profile partial's original missing-profile failure as the observable contract.
        def should_sign_bundles(*, provisioning_profile, rule_descriptor, features):
            self.assertIs(provisioning_profile, profile)
            self.assertEqual(rule_descriptor, "device-rule")
            return "disable_legacy_signing" not in features

        def profile_partial(*, actions, profile_artifact, rule_label):
            if profile_artifact is None:
                raise ValueError("device requires provisioning profile")
            return profile_artifact

        helper = Mock(side_effect=should_sign_bundles)
        partial = Mock(side_effect=profile_partial)
        namespace = {
            "codesigning_support": SimpleNamespace(should_sign_bundles=helper),
            "partials": SimpleNamespace(provisioning_profile_partial=partial),
        }
        exec(compile(fix.patch_content(self.original), "rules_fixture.bzl", "exec"), namespace)
        ctx = SimpleNamespace(
            platform_prerequisites=SimpleNamespace(platform=SimpleNamespace(is_device=device)),
            provisioning_profile=profile, rule_descriptor="device-rule",
            features=["disable_legacy_signing"] if unsigned else [],
            actions=object(), label=function_name,
        )
        return namespace[function_name](ctx), helper, partial

    def test_unsigned_device_omits_profile_embedding_for_app_and_extensions(self):
        for name in fix.FUNCTIONS:
            with self.subTest(name=name):
                result, helper, partial = self.evaluate(name, device=True, unsigned=True, profile=None)
                self.assertEqual(result, [])
                helper.assert_called_once()
                partial.assert_not_called()

    def test_signed_device_preserves_embedding_and_missing_profile_failure(self):
        for name in fix.FUNCTIONS:
            with self.subTest(name=name):
                profile = object()
                result, helper, partial = self.evaluate(name, device=True, unsigned=False, profile=profile)
                self.assertEqual(result, [profile])
                helper.assert_called_once()
                partial.assert_called_once()
                with self.assertRaisesRegex(ValueError, "requires provisioning"):
                    self.evaluate(name, device=True, unsigned=False, profile=None)

    def test_simulator_still_never_registers_profile_partial(self):
        for name in fix.FUNCTIONS:
            result, helper, partial = self.evaluate(name, device=False, unsigned=False, profile=None)
            self.assertEqual(result, [])
            helper.assert_not_called()
            partial.assert_not_called()

    def test_four_other_rule_implementations_remain_byte_identical_and_strict(self):
        changed = fix.patch_content(self.original)
        for name in OTHER_FUNCTIONS:
            with self.subTest(name=name):
                self.assertIn(fixture_function(name).encode(), changed)
                with self.assertRaisesRegex(ValueError, "requires provisioning"):
                    self.evaluate(name, device=True, unsigned=True, profile=None)
        self.assertEqual(changed.count(b"and codesigning_support.should_sign_bundles("), 2)

    def test_rejects_byte_drift_and_repeated_application(self):
        with self.assertRaisesRegex(ValueError, "hash"):
            fix.patch_content(self.original + b"# drift\n")
        patched = fix.patch_content(self.original)
        with self.assertRaisesRegex(ValueError, "repeated"):
            fix.patch_content(patched)

    def test_rejects_missing_or_duplicate_anchor_before_writing(self):
        for body in (
            self.original.replace(b"    if platform_prerequisites.platform.is_device:", b"    if True:", 1),
            self.original + fixture_function(fix.FUNCTIONS[0]).encode(),
        ):
            with self.subTest(body=body[:40]):
                with patch.object(fix, "BEFORE_SHA256", hashlib.sha256(body).hexdigest()):
                    with self.assertRaisesRegex(ValueError, "exactly one"):
                        fix.patch_content(body)

    def test_applier_records_hashes_and_only_writes_reviewed_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / fix.RELATIVE_PATH
            target.parent.mkdir(parents=True)
            target.write_bytes(self.original)
            untouched = root / "untouched.txt"
            untouched.write_bytes(b"preserve")
            receipt = fix.apply_unsigned_profile_fix(root)
            self.assertEqual(target.read_bytes(), self.expected)
            self.assertEqual(untouched.read_bytes(), b"preserve")
            self.assertEqual(receipt["before_sha256"], hashlib.sha256(self.original).hexdigest())
            self.assertEqual(receipt["after_sha256"], hashlib.sha256(self.expected).hexdigest())
            with self.assertRaises(ValueError):
                fix.apply_unsigned_profile_fix(root)
            self.assertEqual(target.read_bytes(), self.expected)


if __name__ == "__main__":
    unittest.main()
