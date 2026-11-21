"""主服务与独立 OCR 环境之间的桥接测试。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ocr_service as ocr  # noqa: E402


class TestOcrService(unittest.TestCase):
    def test_layouts_are_discovered(self):
        layouts = ocr.list_layouts()
        self.assertGreaterEqual(len(layouts), 2)
        ids = {item["id"] for item in layouts}
        self.assertIn("ocr_reference4", ids)
        self.assertIn("ocr_1600x900_on_1920x1080", ids)
        for item in layouts:
            self.assertEqual(len(item["image_size"]), 2)
            self.assertTrue((ocr.ROOT / item["path"]).is_file())

    def test_status_has_actionable_message(self):
        data = ocr.status(force=True)
        self.assertIn("ready", data)
        self.assertIn("layouts", data)
        self.assertIn("message", data)
        if not data["ready"]:
            self.assertTrue(data["message"])

    def test_unknown_layout_is_rejected(self):
        with self.assertRaises(ocr.OcrError):
            ocr.find_layout("does-not-exist")

    def test_upload_is_written_to_cache(self):
        path = ocr.save_upload(b"\x89PNG\r\n\x1a\nfixture")
        try:
            self.assertTrue(path.is_file())
            self.assertEqual(path.read_bytes(), b"\x89PNG\r\n\x1a\nfixture")
        finally:
            path.unlink(missing_ok=True)

    def test_missing_environment_is_reported(self):
        if ocr.ocr_python() is not None:
            self.skipTest("本机已经安装 OCR 环境")
        with self.assertRaises(ocr.OcrUnavailable):
            ocr.run_frame("missing.png", "ocr_reference4")


if __name__ == "__main__":
    unittest.main()
