from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

import baidupan


class UploadHelperTests(unittest.TestCase):
    def test_normalize_remote_path(self) -> None:
        self.assertEqual(
            baidupan.normalize_remote_path("//apps//Demo///paper/"),
            "/apps/Demo/paper",
        )

    def test_file_block_md5(self) -> None:
        payload = b"a" * baidupan.UPLOAD_BLOCK_SIZE + b"bc"
        with tempfile.NamedTemporaryFile() as handle:
            handle.write(payload)
            handle.flush()
            actual = baidupan.file_block_md5(Path(handle.name))
        expected = [
            hashlib.md5(payload[: baidupan.UPLOAD_BLOCK_SIZE]).hexdigest(),
            hashlib.md5(b"bc").hexdigest(),
        ]
        self.assertEqual(actual, expected)

    def test_empty_file_has_one_md5(self) -> None:
        with tempfile.NamedTemporaryFile() as handle:
            actual = baidupan.file_block_md5(Path(handle.name))
        self.assertEqual(actual, [hashlib.md5(b"").hexdigest()])

    def test_upload_block_requires_explicit_success(self) -> None:
        self.assertFalse(baidupan.upload_block_succeeded(None))
        self.assertFalse(baidupan.upload_block_succeeded({}))
        self.assertFalse(baidupan.upload_block_succeeded({"errno": 2}))
        self.assertTrue(baidupan.upload_block_succeeded({"errno": 0}))
        self.assertTrue(baidupan.upload_block_succeeded({"md5": "abc"}))


if __name__ == "__main__":
    unittest.main()
