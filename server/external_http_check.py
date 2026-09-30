"""Replicate and Gemini requests identify this app, and inpaint stays on the mask.

These tests mock the network. They do not call Replicate or Gemini.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ai_enhancements  # noqa: E402
import ai_rebuild  # noqa: E402
import press_ready_engine  # noqa: E402
import text_clearup  # noqa: E402
from http_headers import USER_AGENT, external_headers  # noqa: E402


def _user_agent(req) -> str:
    return req.get_header("User-agent") or req.get_header("User-Agent") or ""


class _Body:
    def __init__(self, data: bytes, status: int = 200):
        self._data = data
        self._pos = 0
        self.status = status

    def read(self, n=-1):
        if n is None or n < 0:
            n = len(self._data) - self._pos
        chunk = self._data[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class ExternalHttpTests(unittest.TestCase):
    def setUp(self):
        self._urlopen = urllib.request.urlopen
        self._token = os.environ.get("REPLICATE_API_TOKEN")
        self._gemini = os.environ.get("GEMINI_API_KEY")
        self._poll = ai_enhancements.REPLICATE_POLL_INTERVAL_S
        press_ready_engine._REPLICATE["checked_at"] = 0.0
        press_ready_engine._REPLICATE["ok"] = False

    def tearDown(self):
        urllib.request.urlopen = self._urlopen
        ai_enhancements.REPLICATE_POLL_INTERVAL_S = self._poll
        press_ready_engine._REPLICATE["checked_at"] = 0.0
        press_ready_engine._REPLICATE["ok"] = False
        if self._token is None:
            os.environ.pop("REPLICATE_API_TOKEN", None)
        else:
            os.environ["REPLICATE_API_TOKEN"] = self._token
        if self._gemini is None:
            os.environ.pop("GEMINI_API_KEY", None)
        else:
            os.environ["GEMINI_API_KEY"] = self._gemini

    def test_user_agent_cannot_be_dropped(self):
        headers = external_headers({
            "User-Agent": "python-urllib/3.14",
            "Authorization": "Bearer secret",
        })
        self.assertEqual(headers["User-Agent"], USER_AGENT)
        self.assertEqual(USER_AGENT, "flyerz-artwork-intelligence/1.0")
        self.assertEqual(headers["Authorization"], "Bearer secret")

    def test_every_replicate_and_gemini_request_has_a_user_agent(self):
        seen = []

        def fake_urlopen(req, timeout=None):
            seen.append(req.full_url)
            self.assertEqual(_user_agent(req), USER_AGENT, req.full_url)
            url = req.full_url
            if "generateContent" in url:
                payload = {
                    "candidates": [{
                        "content": {"parts": [{"text": json.dumps({"blocks": []})}]},
                    }],
                }
                data = json.dumps(payload).encode("utf-8")
            elif "example.test" in url:
                data = b"not-a-png"
            else:
                data = json.dumps({
                    "id": "p1",
                    "status": "succeeded",
                    "output": "https://example.test/out.png",
                    "urls": {
                        "get": "https://api.replicate.com/v1/predictions/p1",
                        "cancel": "https://api.replicate.com/v1/predictions/p1/cancel",
                    },
                }).encode("utf-8")
            return _Body(data)

        urllib.request.urlopen = fake_urlopen
        os.environ["REPLICATE_API_TOKEN"] = "test-token"
        os.environ["GEMINI_API_KEY"] = "test-gemini"
        ai_enhancements.REPLICATE_POLL_INTERVAL_S = 0

        self.assertTrue(press_ready_engine.replicate_available())
        self.assertEqual(ai_rebuild.replicate_credit_status(), "ok")
        created = ai_enhancements._replicate_create_prediction(
            "bria", "eraser", {"image": "data:image/png;base64,YQ=="}, "test-token",
            version=ai_rebuild.INPAINT_MODEL_VERSION,
        )
        self.assertEqual(created.get("status"), "succeeded")
        polled = ai_enhancements._replicate_poll_prediction(
            "https://api.replicate.com/v1/predictions/p1", "test-token", time.time() + 5,
        )
        self.assertEqual(polled.get("status"), "succeeded")
        ai_enhancements._replicate_cancel(
            "https://api.replicate.com/v1/predictions/p1/cancel", "test-token",
        )
        downloaded = ai_enhancements._download_to_ramdisk("https://example.test/out.png")
        self.addCleanup(lambda: os.path.exists(downloaded) and os.unlink(downloaded))

        folder = tempfile.mkdtemp()
        image_path = os.path.join(folder, "tiny.png")
        cv2.imwrite(image_path, np.zeros((8, 8, 3), dtype=np.uint8))
        parsed, err = ai_enhancements._call_gemini_vision(image_path, "read")
        self.assertIsNone(err)
        self.assertEqual(parsed, {"blocks": []})
        blocks, note = ai_rebuild._gemini_blocks(np.zeros((8, 8, 3), dtype=np.uint8))
        self.assertEqual(blocks, [])
        self.assertEqual(note, "")
        body, gem_err = text_clearup._call_gemini_json(np.zeros((8, 8, 3), dtype=np.uint8), "read")
        self.assertIsNone(gem_err)
        self.assertEqual(body, {"blocks": []})

        self.assertGreaterEqual(len(seen), 8)
        self.assertTrue(any(url.endswith("/v1/account") for url in seen))
        self.assertTrue(any("/predictions" in url and not url.endswith("/cancel") for url in seen))
        self.assertTrue(any(url.endswith("/cancel") for url in seen))
        self.assertTrue(any("example.test" in url for url in seen))
        self.assertGreaterEqual(sum("generateContent" in url for url in seen), 3)

    def test_negative_replicate_answer_is_retried_after_a_few_minutes(self):
        calls = {"n": 0}

        def fake_urlopen(req, timeout=None):
            calls["n"] += 1
            self.assertEqual(_user_agent(req), USER_AGENT)
            if calls["n"] == 1:
                raise urllib.error.URLError("error code: 1010")
            return _Body(b"{}", 200)

        urllib.request.urlopen = fake_urlopen
        os.environ["REPLICATE_API_TOKEN"] = "test-token"
        self.assertFalse(press_ready_engine.replicate_available())
        self.assertFalse(press_ready_engine.replicate_available())
        self.assertEqual(calls["n"], 1)
        press_ready_engine._REPLICATE["checked_at"] = (
            time.monotonic() - press_ready_engine.REPLICATE_NEGATIVE_TTL_S - 1
        )
        self.assertTrue(press_ready_engine.replicate_available())
        self.assertEqual(calls["n"], 2)
        self.assertTrue(press_ready_engine.replicate_available())
        self.assertEqual(calls["n"], 2)
        self.assertGreaterEqual(press_ready_engine.REPLICATE_NEGATIVE_TTL_S, 3 * 60)

    def test_inpaint_composites_only_the_masked_area_and_resizes(self):
        original = np.zeros((30, 40, 3), dtype=np.uint8)
        original[:, :] = (10, 20, 30)
        mask = np.zeros((30, 40), dtype=np.uint8)
        mask[5:12, 8:18] = 255
        small = np.full((8, 8, 3), (0, 0, 240), dtype=np.uint8)
        recorded = {}

        def fake_call(name, owner, model, model_input, version=""):
            recorded["name"] = name
            recorded["owner"] = owner
            recorded["model"] = model
            recorded["version"] = version
            recorded["keys"] = set(model_input)
            folder = tempfile.mkdtemp()
            path = os.path.join(folder, "out.png")
            cv2.imwrite(path, small)
            recorded["path"] = path
            return path, None

        real = ai_enhancements._call_replicate
        ai_enhancements._call_replicate = fake_call
        try:
            result = ai_rebuild._replicate_inpaint(original, mask)
        finally:
            ai_enhancements._call_replicate = real
        self.assertIsNotNone(result)
        self.assertEqual(result.shape, original.shape)
        self.assertTrue(np.array_equal(result[mask == 0], original[mask == 0]))
        self.assertFalse(np.array_equal(result[mask == 255], original[mask == 255]))
        self.assertTrue(np.all(result[mask == 255][:, 2] > 200))
        self.assertEqual(recorded["owner"], "bria")
        self.assertEqual(recorded["model"], "eraser")
        self.assertEqual(recorded["version"], ai_rebuild.INPAINT_MODEL_VERSION)
        self.assertNotIn("prompt", recorded["keys"])
        self.assertNotIn("stability-ai", recorded["owner"])

        dirty = np.full_like(original, 255)
        same_size = ai_rebuild.composite_inpaint(original, dirty, mask)
        self.assertTrue(np.array_equal(same_size[mask == 0], original[mask == 0]))
        self.assertTrue(np.all(same_size[mask == 255] == 255))

        mismatched = ai_rebuild.composite_inpaint(original, small, mask)
        self.assertEqual(mismatched.shape, original.shape)
        self.assertTrue(np.array_equal(mismatched[mask == 0], original[mask == 0]))
        self.assertFalse(np.array_equal(mismatched[mask == 255], original[mask == 255]))


if __name__ == "__main__":
    unittest.main()
