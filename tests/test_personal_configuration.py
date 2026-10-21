import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location(
    "prepare_personal_configuration", Path(__file__).parents[1] / "scripts/prepare_personal_configuration.py"
)
prepare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare)


class PersonalConfigurationTests(unittest.TestCase):
    def test_private_values_are_in_only_the_new_runner_file(self):
        with tempfile.TemporaryDirectory() as directory:
            base, output = Path(directory) / "base.json", Path(directory) / "private.json"
            original = {"api_id": 0, "api_hash": "0" * 32, "sg_config": "unchanged"}
            base.write_text(json.dumps(original), encoding="utf-8")
            before = base.read_bytes()
            environment = {"JOBOX_TELEGRAM_API_ID": "123456", "JOBOX_TELEGRAM_API_HASH": "abcdef12" * 4}
            captured = io.StringIO()
            with contextlib.redirect_stdout(captured):
                prepare.prepare_personal_configuration(base, output, environment)
            self.assertEqual(base.read_bytes(), before)
            self.assertEqual(json.loads(output.read_text()), {**original, "api_id": 123456, "api_hash": "abcdef12" * 4})
            self.assertNotIn(environment["JOBOX_TELEGRAM_API_HASH"], captured.getvalue())
            self.assertNotIn(environment["JOBOX_TELEGRAM_API_ID"], captured.getvalue())
            if os.name != "nt":
                self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                prepare.prepare_personal_configuration(base, output, environment)

    def test_bad_or_missing_application_secrets_never_write_or_echo_values(self):
        with tempfile.TemporaryDirectory() as directory:
            base, output = Path(directory) / "base.json", Path(directory) / "private.json"
            base.write_text('{"api_id":0,"api_hash":"' + "0" * 32 + '"}')
            for api_id, api_hash in [("", ""), ("-1", "a" * 32), ("0", "a" * 32),
                                      ("2147483648", "a" * 32), ("123456", "0" * 32),
                                      ("123456", "synthetic-private-value")]:
                with self.subTest(api_id=api_id), self.assertRaisesRegex(ValueError, "private Telegram application secrets"):
                    prepare.prepare_personal_configuration(base, output, {
                        "JOBOX_TELEGRAM_API_ID": api_id, "JOBOX_TELEGRAM_API_HASH": api_hash
                    })
                self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
