import base64
import gzip
import hashlib
import io
import json
import unittest
from unittest.mock import patch

from chatgpt_archive.archive import ArchiveError
from chatgpt_archive.capture_io import read_capture


class CaptureIOTests(unittest.TestCase):
    def envelope(self, value):
        raw = json.dumps(value, ensure_ascii=False).encode()
        return {"encoding": "gzip-base64", "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "data": base64.b64encode(gzip.compress(raw)).decode()}

    def test_exact_roundtrip_and_plain_input(self):
        capture = {"messages": [{"text": "한글\u00a0\n표|코드\n" * 1000}]}
        for value in (capture, self.envelope(capture)):
            self.assertEqual(read_capture(io.StringIO(json.dumps(value))), capture)

    def test_truncation_hash_and_length_mismatch(self):
        original = self.envelope({"text": "그대로"})
        for update in ({"data": original["data"][:-8]}, {"sha256": "0" * 64}, {"bytes": 0}):
            with self.assertRaises(ArchiveError):
                read_capture(io.StringIO(json.dumps(dict(original, **update))))

    def test_decompression_size_limit(self):
        with patch("chatgpt_archive.capture_io.MAX_CAPTURE_BYTES", 100):
            with self.assertRaises(ArchiveError):
                read_capture(io.StringIO(json.dumps(self.envelope({"text": "x" * 1000}))))
