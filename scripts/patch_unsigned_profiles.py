#!/usr/bin/env python3
"""Keep profile embedding consistent with the pinned rules_apple signing decision."""

import argparse
import hashlib
import json
from pathlib import Path
import re


RULES_APPLE_SHA = "1791d916de4083388f22e20248d8b010d23f0d6b"
RELATIVE_PATH = "build-system/bazel-rules/rules_apple/apple/internal/ios_rules.bzl"
BEFORE_SHA256 = "900fe828aa2d5f9ac236aec3555cf4f01944a012273ea03a12f9b52e8b601540"
AFTER_SHA256 = "caf1c9913f6cd1b26c678e30c305134680e8779aa880209bfe3de97ac7790ee0"
FUNCTIONS = ("_ios_application_impl", "_ios_extension_impl")
BEFORE = """    if platform_prerequisites.platform.is_device:
        processor_partials.append(
            partials.provisioning_profile_partial("""
AFTER = """    if platform_prerequisites.platform.is_device and codesigning_support.should_sign_bundles(
        provisioning_profile = provisioning_profile,
        rule_descriptor = rule_descriptor,
        features = features,
    ):
        processor_partials.append(
            partials.provisioning_profile_partial("""


def sha256(content):
    return hashlib.sha256(content).hexdigest()


def profile_fix_receipt():
    return {
        "path": RELATIVE_PATH,
        "rules_apple_revision": RULES_APPLE_SHA,
        "before_sha256": BEFORE_SHA256,
        "after_sha256": AFTER_SHA256,
        "changed_functions": list(FUNCTIONS),
    }


def patch_content(content):
    if sha256(content) != BEFORE_SHA256:
        raise ValueError("Unexpected ios_rules.bzl hash; drift or repeated patch application")
    source = content.decode("utf-8")
    for name in FUNCTIONS:
        definitions = list(re.finditer(r"^def " + re.escape(name) + r"\(", source, re.MULTILINE))
        if len(definitions) != 1:
            raise ValueError("Expected exactly one definition of " + name)
        start = definitions[0].start()
        following = re.search(r"^def ", source[definitions[0].end():], re.MULTILINE)
        end = definitions[0].end() + following.start() if following else len(source)
        section = source[start:end]
        if section.count(BEFORE) != 1:
            raise ValueError("Expected exactly one profile registration in " + name)
        source = source[:start] + section.replace(BEFORE, AFTER, 1) + source[end:]
    patched = source.encode("utf-8")
    if sha256(patched) != AFTER_SHA256:
        raise ValueError("Patched ios_rules.bzl does not match the reviewed two-guard change")
    return patched


def apply_unsigned_profile_fix(upstream):
    upstream = Path(upstream).resolve(strict=True)
    target = (upstream / RELATIVE_PATH).resolve(strict=True)
    if not target.is_relative_to(upstream) or not target.is_file():
        raise ValueError("Rules source must be a file inside the upstream checkout")
    original = target.read_bytes()
    patched = patch_content(original)
    target.write_bytes(patched)
    return profile_fix_receipt()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", required=True)
    arguments = parser.parse_args()
    print(json.dumps(apply_unsigned_profile_fix(arguments.upstream), sort_keys=True))


if __name__ == "__main__":
    main()
