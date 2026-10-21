"""Only synthetic credentials and mocked HTTP are used in these checks."""

import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import prepare_test_configuration as test_config


class TestOnlyConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.fake_id = 123456
        self.fake_hash = "a" * 32
        self.document = (
            "For test purposes you can use TEST ONLY credentials.\n"
            f"**api_id**: {self.fake_id}\n**api_hash**: {self.fake_hash}\n"
        ).encode()
        self.fingerprint = hashlib.sha256(
            f"{self.fake_id}:{self.fake_hash}".encode()
        ).hexdigest()

    def test_document_and_pair_must_both_match_their_pins(self):
        with patch.object(test_config, "TEST_DOCUMENT_SHA256", hashlib.sha256(self.document).hexdigest()):
            with self.assertRaisesRegex(ValueError, "fingerprint"):
                test_config.parse_test_document(self.document)
            with patch.object(test_config, "TEST_CREDENTIALS_FINGERPRINT", self.fingerprint):
                self.assertEqual(test_config.parse_test_document(self.document), {
                    "api_id": self.fake_id, "api_hash": self.fake_hash,
                })
                with self.assertRaisesRegex(ValueError, "checksum"):
                    test_config.parse_test_document(self.document + b"drift")
        with self.assertRaisesRegex(ValueError, "size limit"):
            test_config.parse_test_document(b"x" * (test_config.MAX_DOCUMENT_BYTES + 1))

    def test_duplicate_pairs_rejected_even_with_reviewed_document_checksum(self):
        document = self.document + self.document
        with patch.object(test_config, "TEST_DOCUMENT_SHA256", hashlib.sha256(document).hexdigest()):
            with self.assertRaisesRegex(ValueError, "exactly one"):
                test_config.parse_test_document(document)

    def test_configuration_gate_rejects_placeholders_private_pairs_and_wrong_types(self):
        with patch.object(test_config, "TEST_CREDENTIALS_FINGERPRINT", self.fingerprint):
            self.assertTrue(test_config.is_test_configuration({
                "api_id": self.fake_id, "api_hash": self.fake_hash.upper(),
            }))
            for api_id, api_hash in [
                (0, "0" * 32), (True, self.fake_hash), (str(self.fake_id), self.fake_hash),
                (self.fake_id + 1, self.fake_hash), (self.fake_id, "b" * 32),
                (self.fake_id, None), (self.fake_id, self.fake_hash + "x"),
            ]:
                with self.subTest(api_id_type=type(api_id).__name__):
                    self.assertFalse(test_config.is_test_configuration({
                        "api_id": api_id, "api_hash": api_hash,
                    }))

    def test_prepare_fetches_only_fixed_bounded_source_and_omits_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary) / "base.json"
            output = Path(temporary) / "prototype.json"
            base.write_text(json.dumps({"api_id": 0, "api_hash": "0" * 32, "keep": "value"}))
            response = unittest.mock.MagicMock()
            response.__enter__.return_value = response
            response.read.return_value = self.document
            printed = io.StringIO()
            with (patch.object(test_config, "urlopen", return_value=response) as request,
                  patch.object(test_config, "TEST_DOCUMENT_SHA256", hashlib.sha256(self.document).hexdigest()),
                  patch.object(test_config, "TEST_CREDENTIALS_FINGERPRINT", self.fingerprint),
                  contextlib.redirect_stdout(printed)):
                self.assertEqual(test_config.prepare_test_configuration(base, output), output)
            request.assert_called_once_with(test_config.TEST_SOURCE_URL, timeout=30)
            response.read.assert_called_once_with(test_config.MAX_DOCUMENT_BYTES + 1)
            result = json.loads(output.read_text())
            self.assertEqual(result, {"api_id": self.fake_id, "api_hash": self.fake_hash, "keep": "value"})
            self.assertNotIn(str(self.fake_id), printed.getvalue())
            self.assertNotIn(self.fake_hash, printed.getvalue())
            self.assertEqual(set(test_config.test_configuration_receipt()), {"source_url", "document_sha256"})


if __name__ == "__main__":
    unittest.main()
