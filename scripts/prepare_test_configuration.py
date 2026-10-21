"""Prepare Telegram's pinned public TEST ONLY configuration without logging values."""

import argparse
import hashlib
from pathlib import Path
import re
from urllib.request import urlopen

from prepare_personal_configuration import prepare_personal_configuration


TEST_SOURCE_URL = (
    "https://raw.githubusercontent.com/telegramdesktop/tdesktop/"
    "65b2db216033aa08b7bc846df27843e566f08981/docs/api_credentials.md"
)
TEST_DOCUMENT_SHA256 = "352d92031365da3ae18374c1e9224b8b7ed3d5c3f08b4407471e42f2a34a661a"
TEST_CREDENTIALS_FINGERPRINT = "10d6093e771144f84a1802333cb0d8381ccd89e41c7186ea3c60ce1a1b25c562"
MAX_DOCUMENT_BYTES = 64 * 1024


def is_test_configuration(configuration):
    api_id = configuration.get("api_id")
    api_hash = configuration.get("api_hash")
    if (type(api_id) is not int or not 0 < api_id <= 2147483647
            or not isinstance(api_hash, str)
            or not re.fullmatch(r"[a-fA-F0-9]{32}", api_hash)):
        return False
    fingerprint = hashlib.sha256(f"{api_id}:{api_hash.lower()}".encode("ascii")).hexdigest()
    return fingerprint == TEST_CREDENTIALS_FINGERPRINT


def test_configuration_receipt():
    return {"source_url": TEST_SOURCE_URL, "document_sha256": TEST_DOCUMENT_SHA256}


def parse_test_document(document):
    if len(document) > MAX_DOCUMENT_BYTES:
        raise ValueError("Official test configuration document exceeds the size limit")
    if hashlib.sha256(document).hexdigest() != TEST_DOCUMENT_SHA256:
        raise ValueError("Official test configuration document does not match the pinned checksum")
    content = document.decode("utf-8")
    ids = re.findall(r"\*\*api_id\*\*:\s*([0-9]+)", content)
    hashes = re.findall(r"\*\*api_hash\*\*:\s*([a-fA-F0-9]{32})", content)
    if len(ids) != 1 or len(hashes) != 1:
        raise ValueError("Official document must contain exactly one test configuration")
    configuration = {"api_id": int(ids[0]), "api_hash": hashes[0].lower()}
    if not is_test_configuration(configuration):
        raise ValueError("Official test configuration does not match the pinned fingerprint")
    return configuration


def prepare_test_configuration(base, output):
    # The official sample is server-limited and is never a production credential fallback.
    with urlopen(TEST_SOURCE_URL, timeout=30) as response:
        document = response.read(MAX_DOCUMENT_BYTES + 1)
    configuration = parse_test_document(document)
    result = prepare_personal_configuration(base, output, {
        "JOBOX_TELEGRAM_API_ID": str(configuration["api_id"]),
        "JOBOX_TELEGRAM_API_HASH": configuration["api_hash"],
    })
    print("Official TEST ONLY configuration prepared; not for deployment. Values omitted.")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    prepare_test_configuration(arguments.base, arguments.output)
