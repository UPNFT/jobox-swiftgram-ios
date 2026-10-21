#!/usr/bin/env python3
"""Prepare only the pinned Swiftgram BUILD for an explicit personal sideload build."""

import argparse
import hashlib
import json
from pathlib import Path


SOURCE_SHA = "cf8b23beaaac4126a396337ac2d5be13f9f76b66"
RELATIVE_PATH = "Telegram/BUILD"
BEFORE_SHA256 = "3cc41d7c0bb7cf1afa1a572ac9637d3d0ad33d93210bf82b791a09e1c628b2fa"
AFTER_SHA256 = "7be3b34243575f993ba7d5b539f54d5e1ab888d0d31f6f5dbda0e17efe5365a3"
REQUIRED_BUILD_FLAGS = ("--//Telegram:disableExtensions=true",)
REMOVED_CAPABILITIES = (
    "com.apple.developer.carplay-messaging",
    "com.apple.developer.usernotifications.communication",
)
REPLACEMENTS = (
    (b'''official_carplay_fragment = """
<key>com.apple.developer.carplay-messaging</key>
<true/>
"""
carplay_fragment = official_carplay_fragment # MARK: Swiftgram if telegram_bundle_id in official_bundle_ids else ""

''', b'carplay_fragment = ""\n\n'),
    (b'''official_communication_notifications_fragment = """
<key>com.apple.developer.usernotifications.communication</key>
<true/>
"""
communication_notifications_fragment = official_communication_notifications_fragment # if telegram_bundle_id in official_bundle_ids else ""

''', b'communication_notifications_fragment = ""\n\n'),
)


def sha256(content):
    return hashlib.sha256(content).hexdigest()


def personal_sideload_receipt():
    return {
        "path": RELATIVE_PATH,
        "source_revision": SOURCE_SHA,
        "before_sha256": BEFORE_SHA256,
        "after_sha256": AFTER_SHA256,
        "removed_capabilities": list(REMOVED_CAPABILITIES),
        "required_build_flags": list(REQUIRED_BUILD_FLAGS),
    }


def patch_content(content):
    digest = sha256(content)
    if digest == AFTER_SHA256:
        return content
    if digest != BEFORE_SHA256:
        raise ValueError("Unexpected Telegram/BUILD hash; personal sideload source drift")
    for before, after in REPLACEMENTS:
        content = content.replace(before, after, 1)
    if sha256(content) != AFTER_SHA256:
        raise ValueError("Patched Telegram/BUILD does not match the reviewed capability removal")
    return content


def apply_personal_sideload_fix(upstream):
    upstream = Path(upstream).resolve(strict=True)
    target = (upstream / RELATIVE_PATH).resolve(strict=True)
    if not target.is_relative_to(upstream) or not target.is_file():
        raise ValueError("BUILD must be a file inside the upstream checkout")
    original = target.read_bytes()
    patched = patch_content(original)
    if patched != original:
        target.write_bytes(patched)
    return personal_sideload_receipt()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", required=True)
    arguments = parser.parse_args()
    print(json.dumps(apply_personal_sideload_fix(arguments.upstream), sort_keys=True))


if __name__ == "__main__":
    main()
