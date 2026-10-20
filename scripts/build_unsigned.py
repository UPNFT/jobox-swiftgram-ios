#!/usr/bin/env python3
"""Build the pinned Swiftgram device target with no signing material or live API ID."""

import argparse
import importlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from types import SimpleNamespace

from patch_unsigned_profiles import apply_unsigned_profile_fix


UPSTREAM_SHA = "cf8b23beaaac4126a396337ac2d5be13f9f76b66"
RULES_APPLE_SHA = "1791d916de4083388f22e20248d8b010d23f0d6b"
RULES_APPLE_PATH = "build-system/bazel-rules/rules_apple"


def git_revision(directory, revision):
    return subprocess.check_output(
        ["git", "-C", str(directory), "rev-parse", revision], text=True
    ).strip()


def validate_configuration(path):
    configuration = json.loads(path.read_text(encoding="utf-8"))
    if configuration.get("api_id") != 0 or configuration.get("api_hash") != "0" * 32:
        raise ValueError("Build checks require api_id=0 and a 32-zero api_hash")
    if configuration.get("bundle_id") != "org.jobox.swiftgram.buildcheck":
        raise ValueError("Unexpected build-check bundle ID")
    if configuration.get("team_id") != "0000000000":
        raise ValueError("Build checks require the placeholder team ID")
    sg_configuration = json.loads(configuration["sg_config"])
    if sg_configuration != {
        "api_url": "https://jobox-build.invalid",
        "webapp_url": "https://jobox-build.invalid",
        "bot_username": "", "public_key": None, "iaps": [],
    }:
        raise ValueError("sg_config must provide the full .invalid build configuration; missing keys restore upstream defaults")


def build_unsigned(upstream, config, bazel, build_number, output):
    upstream = Path(upstream).resolve(strict=True)
    config = Path(config).resolve(strict=True)
    bazel = Path(bazel).resolve(strict=True)
    output = Path(output).resolve()
    if platform.system() != "Darwin" or platform.machine() != "x86_64":
        raise RuntimeError("This verified Bazel binary requires a macOS Intel runner")
    if build_number < 1:
        raise ValueError("Build number must be positive")
    receipt_path = output.with_suffix(".build.json")
    if output.exists() or receipt_path.exists():
        raise FileExistsError("Refusing to replace an existing output")
    if git_revision(upstream, "HEAD") != UPSTREAM_SHA:
        raise ValueError("Unexpected Swiftgram source revision")
    if git_revision(upstream, "HEAD:" + RULES_APPLE_PATH) != RULES_APPLE_SHA:
        raise ValueError("Unexpected rules_apple gitlink")
    if git_revision(upstream / RULES_APPLE_PATH, "HEAD") != RULES_APPLE_SHA:
        raise ValueError("rules_apple checkout does not match the pinned gitlink")
    signing_source = upstream / RULES_APPLE_PATH / "apple/internal/codesigning_support.bzl"
    if 'if "disable_legacy_signing" in features:' not in signing_source.read_text(encoding="utf-8"):
        raise ValueError("Pinned rules_apple has no explicit unsigned build feature")
    validate_configuration(config)
    profile_fix = apply_unsigned_profile_fix(upstream)
    print("Unsigned profile gate: " + json.dumps(profile_fix, sort_keys=True))

    previous_directory = Path.cwd()
    make_path = str(upstream / "build-system/Make")
    sys.path.insert(0, make_path)
    try:
        os.chdir(upstream)
        make = importlib.import_module("Make")
        command = make.BazelCommandLine(
            bazel=str(bazel),
            override_bazel_version=False,
            override_xcode_version=False,
            bazel_user_root=None,
        )
        # This source only returns an empty APNs environment; it loads no identities.
        make.resolve_configuration(
            base_path=str(upstream),
            bazel_command_line=command,
            arguments=SimpleNamespace(
                configurationPath=str(config),
                gitCodesigningRepository=None,
                codesigningInformationPath=None,
                xcodeManagedCodesigning=True,
            ),
            additional_codesigning_output_path=None,
        )
        command.set_configuration("release_arm64")
        command.set_build_number(build_number)
        command.set_custom_target("//Telegram:Swiftgram")
        command.set_disable_provisioning_profiles()
        # Make.py parses --bazelArguments but never uses it in this pinned revision.
        command.common_build_args.extend(["--features=disable_legacy_signing", "--jobs=2"])
        command.invoke_build()
        ipa = upstream / "bazel-bin/Telegram/Swiftgram.ipa"
        if not ipa.is_file():
            raise FileNotFoundError("Expected device IPA was not produced")
        output.parent.mkdir(parents=True, exist_ok=True)
        with ipa.open("rb") as source, output.open("xb") as destination:
            shutil.copyfileobj(source, destination)
        with receipt_path.open("x", encoding="utf-8") as receipt:
            json.dump({"source_sha": UPSTREAM_SHA, "unsigned_profile_fix": profile_fix,
                       "harness_commit": os.environ.get("GITHUB_SHA")}, receipt, indent=2)
            receipt.write("\n")
    finally:
        os.chdir(previous_directory)
        sys.path.remove(make_path)
    print("Device IPA built; signature and payload verification are still required.")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--bazel", required=True)
    parser.add_argument("--build-number", required=True, type=int)
    parser.add_argument("--output", required=True)
    arguments = parser.parse_args()
    build_unsigned(
        arguments.upstream, arguments.config, arguments.bazel,
        arguments.build_number, arguments.output,
    )


if __name__ == "__main__":
    main()
