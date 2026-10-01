"""OCR result shape and the Gemini model URL."""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace

import gemini_api
import ocr_reader


class OcrShapes(unittest.TestCase):
    def test_old_rapidocr_tuple(self):
        box = [[1, 2], [9, 2], [9, 8], [1, 8]]
        rows = ocr_reader.rows_from_output(([[box, "MARKET DAY", 0.9]], 0.01))
        self.assertEqual(rows, [[box, "MARKET DAY", 0.9]])

    def test_new_rapidocr_output(self):
        import numpy as np

        box = [[4, 5], [20, 5], [20, 12], [4, 12]]
        result = SimpleNamespace(
            boxes=np.array([box]),
            txts=("SATURDAY",),
            scores=np.array([0.8]),
        )
        rows = ocr_reader.rows_from_output(result)
        self.assertEqual(rows[0][1], "SATURDAY")
        self.assertEqual(rows[0][2], 0.8)
        self.assertEqual(len(rows[0][0]), 4)

    def test_gemini_model_is_current_and_configurable(self):
        for key in ("GEMINI_OCR_MODEL", "GEMINI_MODEL", "GEMINI_API_BASE"):
            os.environ.pop(key, None)
        url = gemini_api.gemini_generate_content_url("test-key")
        self.assertIn("/models/gemini-3.6-flash:generateContent", url)
        self.assertNotIn("gemini-2.0-flash", url)
        self.assertTrue(url.startswith("https://generativelanguage.googleapis.com/v1beta/"))
        self.assertIn("key=test-key", url)
        os.environ["GEMINI_OCR_MODEL"] = "gemini-2.5-flash"
        os.environ["GEMINI_API_BASE"] = "https://example.test/v1beta/"
        overridden = gemini_api.gemini_generate_content_url()
        self.assertEqual(
            overridden,
            "https://example.test/v1beta/models/gemini-2.5-flash:generateContent",
        )
        os.environ.pop("GEMINI_OCR_MODEL", None)
        os.environ.pop("GEMINI_API_BASE", None)


if __name__ == "__main__":
    unittest.main()
