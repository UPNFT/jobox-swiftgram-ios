"""Prepare private Telegram application configuration without logging its values."""

import argparse
import json
import os
from pathlib import Path
import re


def prepare_personal_configuration(base, output, environment=None):
    environment = os.environ if environment is None else environment
    api_id = environment.get("JOBOX_TELEGRAM_API_ID", "")
    api_hash = environment.get("JOBOX_TELEGRAM_API_HASH", "")
    if (not re.fullmatch(r"[1-9][0-9]{0,9}", api_id)
            or int(api_id) > 2147483647
            or not re.fullmatch(r"[a-fA-F0-9]{32}", api_hash)
            or api_hash == "0" * 32):
        raise ValueError("Set both private Telegram application secrets before building")
    configuration = json.loads(Path(base).read_text(encoding="utf-8"))
    if configuration.get("api_id") != 0 or configuration.get("api_hash") != "0" * 32:
        raise ValueError("Expected the reviewed placeholder base configuration")
    configuration["api_id"] = int(api_id)
    configuration["api_hash"] = api_hash.lower()
    output = Path(output)
    # The runner-local file is never a release asset or a source-controlled input.
    with open(output, "x", encoding="utf-8", opener=lambda path, flags: os.open(path, flags, 0o600)) as stream:
        json.dump(configuration, stream)
        stream.write("\n")
    print("Private application configuration prepared; values omitted.")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    prepare_personal_configuration(arguments.base, arguments.output)
