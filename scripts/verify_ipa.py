"""Verify a local unsigned iPhone build; never logs configuration or app contents."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import plistlib
import re
import struct
import subprocess
import tempfile
import zipfile

from patch_unsigned_profiles import profile_fix_receipt

SOURCE_SHA = "cf8b23beaaac4126a396337ac2d5be13f9f76b66"
BUNDLE_ID = "org.jobox.swiftgram.buildcheck"
MAX_IPA_BYTES = 450 * 1024 * 1024


def inspect_archive(ipa):
    if not ipa.is_file() or ipa.stat().st_size > MAX_IPA_BYTES:
        raise ValueError("IPA missing or exceeds 450 MiB artifact budget")
    with zipfile.ZipFile(ipa) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("Duplicate archive entries")
        for name in names:
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or "\\" in name:
                raise ValueError("Unsafe archive path")
            if path.suffix.lower() in {".p12", ".p8", ".key", ".mobileprovision"}:
                raise ValueError("Signing material must not be published")
            if path.suffix.lower() == ".pem":
                data = archive.read(name)
                if b"PRIVATE KEY" in data:
                    raise ValueError("Private key must not be published")
        plists = [name for name in names if re.fullmatch(r"Payload/[^/]+\.app/Info\.plist", name)]
        if len(plists) != 1:
            raise ValueError("Expected exactly one main iPhone app")
        info = plistlib.loads(archive.read(plists[0]))
        if info.get("CFBundleSupportedPlatforms") != ["iPhoneOS"]:
            raise ValueError("Expected iPhone device build, not simulator")
        if info.get("CFBundleIdentifier") != BUNDLE_ID:
            raise ValueError("Unexpected build-check bundle identifier")
        executable_name = info.get("CFBundleExecutable", "")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", executable_name):
            raise ValueError("Invalid main executable name")
        executable = archive.read(str(PurePosixPath(plists[0]).parent / executable_name))
        if len(executable) < 32 or struct.unpack_from("<II", executable) != (0xFEEDFACF, 0x0100000C):
            raise ValueError("Expected a thin arm64 Mach-O executable")
        binaries = {str(PurePosixPath(plists[0]).parent / executable_name): executable}
        for name in names:
            if name != plists[0] and re.fullmatch(r"Payload/[^/]+\.app/.+\.(?:appex|framework)/Info\.plist", name):
                nested = plistlib.loads(archive.read(name))
                executable_name = nested.get("CFBundleExecutable", "")
                if not re.fullmatch(r"[A-Za-z0-9_.-]+", executable_name):
                    raise ValueError("Invalid bundled executable name")
                binary_name = str(PurePosixPath(name).parent / executable_name)
                binaries[binary_name] = archive.read(binary_name)
            elif name.endswith(".dylib"):
                binaries[name] = archive.read(name)
        return info, binaries


def validate_ios_slice(architecture, output):
    commands = re.findall(r"^\s*cmd\s+(LC_BUILD_VERSION|LC_VERSION_MIN_\w+)\s*$", output, re.MULTILINE)
    platforms = re.findall(r"^\s*platform\s+(\S+)\s*$", output, re.MULTILINE | re.IGNORECASE)
    modern_ios = commands == ["LC_BUILD_VERSION"] and len(platforms) == 1 and platforms[0].upper() in {"IOS", "2"}
    legacy_ios = commands == ["LC_VERSION_MIN_IPHONEOS"] and not platforms
    if architecture not in {"arm64", "arm64e", "armv7", "armv7s"} or not (modern_ios or legacy_ios):
        summary = {"architecture": architecture, "commands": commands, "platforms": platforms}
        raise ValueError("Mach-O slice is not iOS device: " + json.dumps(summary, sort_keys=True))


def verify_platform(binary_path):
    architectures = subprocess.run(["xcrun", "lipo", "-archs", str(binary_path)],
                                   check=True, capture_output=True, text=True).stdout.split()
    if not architectures:
        raise ValueError("Mach-O has no readable architecture")
    for architecture in architectures:
        # Inspect each slice; a valid iOS slice cannot hide another platform in a fat file.
        result = subprocess.run(["xcrun", "vtool", "-arch", architecture, "-show-build", str(binary_path)],
                                check=True, capture_output=True, text=True)
        validate_ios_slice(architecture, result.stdout)
    signed = subprocess.run(["codesign", "--display", "--verbose=4", str(binary_path)],
                            capture_output=True, text=True)
    details = signed.stderr + getattr(signed, "stdout", "")
    if signed.returncode != 0:
        if "not signed at all" not in details.lower():
            raise ValueError("Could not confirm unsigned executable")
        return "none"
    fields = dict(line.split("=", 1) for line in details.splitlines() if "=" in line)
    if (fields.get("Signature", "").lower() != "adhoc" or "Authority" in fields
            or fields.get("TeamIdentifier", "not set") != "not set"):
        raise ValueError("Apple identity signature must not be published in this build check")
    return "adhoc"


def read_build_receipt(path):
    receipt = json.loads(path.read_text(encoding="utf-8"))
    if (receipt.get("source_sha") != SOURCE_SHA
            or receipt.get("unsigned_profile_fix") != profile_fix_receipt()
            or not re.fullmatch(r"[a-f0-9]{40}", receipt.get("harness_commit") or "")):
        raise ValueError("Missing or unreviewed source adjustment provenance")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ipa", required=True, type=Path)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--build-receipt", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()
    if args.source_sha != SOURCE_SHA:
        raise ValueError("Unexpected source revision")
    receipt = read_build_receipt(args.build_receipt)
    info, binaries = inspect_archive(args.ipa)
    signature_kinds = set()
    with tempfile.TemporaryDirectory(prefix="jobox-ipa-check-") as directory:
        for index, (archive_name, executable) in enumerate(binaries.items()):
            binary_path = Path(directory) / f"executable-{index}"
            binary_path.write_bytes(executable)
            try:
                signature_kinds.add(verify_platform(binary_path))
            except (ValueError, subprocess.CalledProcessError) as error:
                tool_detail = (getattr(error, "stderr", "") or "")[:1600]
                raise ValueError("Artifact rejected at " + json.dumps(archive_name) + ": " + str(error) + " " + tool_detail) from error
    digest = hashlib.sha256(args.ipa.read_bytes()).hexdigest()
    manifest = {
        "upstream": "Swiftgram/Telegram-iOS", "source_sha": SOURCE_SHA,
        "harness_commit": receipt["harness_commit"],
        "source_adjustments": [receipt["unsigned_profile_fix"]],
        "bundle_id": info["CFBundleIdentifier"], "platform": "iPhoneOS",
        "architecture": "arm64", "apple_identity_signed": False,
        "platform_validation": "each Mach-O slice: modern iOS or legacy ARM iPhoneOS",
        "signature_kinds": sorted(signature_kinds), "checked_executables": len(binaries),
        "api_mode": "invalid-placeholder",
        "login_verified": False, "translation_verified": False,
        "directly_installable": False, "ipa_bytes": args.ipa.stat().st_size,
        "ipa_sha256": digest,
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    args.ipa.with_suffix(args.ipa.suffix + ".sha256").write_text(
        f"{digest}  {args.ipa.name}\n", encoding="utf-8")
    print("Verified unsigned arm64 iPhone artifact with placeholder API configuration.")


if __name__ == "__main__":
    main()
