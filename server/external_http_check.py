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
        self._pause = ai_enhancements._pause
        self._timeout = ai_enhancements.REPLICATE_TIMEOUT_S
        press_ready_engine._REPLICATE["checked_at"] = 0.0
        press_ready_engine._REPLICATE["ok"] = False

    def tearDown(self):
        urllib.request.urlopen = self._urlopen
        ai_enhancements.REPLICATE_POLL_INTERVAL_S = self._poll
        ai_enhancements._pause = self._pause
        ai_enhancements.REPLICATE_TIMEOUT_S = self._timeout
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
            result, err = ai_rebuild._replicate_inpaint(original, mask)
        finally:
            ai_enhancements._call_replicate = real
        self.assertEqual(err, "")
        self.assertIsNotNone(result)
        self.assertEqual(result.shape, original.shape)
        # A corner far from the letters stays exact. The foreign 8×8 picture is
        # scaled back, then blended, so it is not pasted as a hard red block.
        self.assertTrue(np.array_equal(result[0, 0], original[0, 0]))
        self.assertLess(abs(int(result[8, 13, 2]) - int(original[8, 13, 2])), 40)
        self.assertEqual(recorded["owner"], "bria")
        self.assertEqual(recorded["model"], "eraser")
        self.assertEqual(recorded["version"], ai_rebuild.INPAINT_MODEL_VERSION)
        self.assertNotIn("prompt", recorded["keys"])
        self.assertNotIn("stability-ai", recorded["owner"])

        soft = ai_rebuild._soft_cover(mask, original.shape[0], original.shape[1])
        far = soft == 0
        self.assertTrue(np.array_equal(result[far], original[far]))

        mismatched = ai_rebuild.composite_inpaint(original, small, mask)
        self.assertEqual(mismatched.shape, original.shape)
        self.assertTrue(np.array_equal(mismatched[far], original[far]))
        self.assertLess(abs(int(mismatched[8, 13, 2]) - 30), 40)

    def _http_error(self, url: str, code: int, body: bytes, retry_after: str = ""):
        import io
        from email.message import EmailMessage

        headers = EmailMessage()
        if retry_after:
            headers["Retry-After"] = retry_after
        return urllib.error.HTTPError(url, code, "error", headers, io.BytesIO(body))

    def test_rate_limit_waits_and_retries(self):
        calls = {"n": 0}
        slept = []
        timeouts = []

        def fake_urlopen(req, timeout=None):
            calls["n"] += 1
            timeouts.append(timeout)
            self.assertEqual(_user_agent(req), USER_AGENT)
            if calls["n"] == 1:
                raise self._http_error(req.full_url, 429, b'{"detail":"throttled once"}', "7")
            data = json.dumps({
                "id": "p1",
                "status": "succeeded",
                "output": "https://example.test/out.png",
            }).encode("utf-8")
            return _Body(data)

        urllib.request.urlopen = fake_urlopen
        ai_enhancements._pause = lambda seconds: slept.append(seconds)
        created = ai_enhancements._replicate_create_prediction(
            "nightmareai", "real-esrgan", {"image": "data:image/png;base64,YQ=="}, "test-token",
            version="f121d640bd286e1fdc67f9799164c1d5be36ff74576ee11c803ae5b665dd46aa",
        )
        self.assertEqual(created.get("status"), "succeeded")
        self.assertEqual(calls["n"], 2)
        self.assertEqual(slept, [7])
        self.assertEqual(ai_enhancements.REPLICATE_TIMEOUT_S, 90)
        self.assertGreaterEqual(timeouts[0], 60)

    def test_rate_limit_error_is_the_replicate_message(self):
        calls = {"n": 0}
        slept = []
        body = b'{"detail":"Request was throttled. Your rate limit is 1 request per 10 seconds."}'

        def fake_urlopen(req, timeout=None):
            calls["n"] += 1
            raise self._http_error(req.full_url, 429, body, "10")

        urllib.request.urlopen = fake_urlopen
        ai_enhancements._pause = lambda seconds: slept.append(seconds)
        os.environ["REPLICATE_API_TOKEN"] = "test-token"
        out, err = ai_enhancements._call_replicate(
            "ai_rebuild_upscale", "nightmareai", "real-esrgan", {"image": "x"}, version="abc",
        )
        self.assertIsNone(out)
        self.assertIn("Request was throttled", err)
        self.assertIn("API error 429", err)
        self.assertNotIn("not available", err.lower())
        self.assertEqual(calls["n"], 4)
        self.assertEqual(slept, [10, 10, 10])

        picture = np.full((24, 32, 3), 40, np.uint8)
        _fitted, engine, detail, _scale, _x, _y = ai_rebuild._enlarge(picture, 48, 72, "ok")
        self.assertEqual(engine, "local")
        self.assertIn("Request was throttled", detail)
        self.assertNotIn("not available", detail.lower())

        mask = np.zeros((24, 32), np.uint8)
        mask[4:10, 4:14] = 255
        _filled, inpaint_engine, inpaint_detail = ai_rebuild._remove_text(picture, mask, "ok")
        self.assertEqual(inpaint_engine, "local")
        self.assertIn("Request was throttled", inpaint_detail)
        self.assertNotIn("not available", inpaint_detail.lower())

    def test_step_budget_is_90_seconds_and_still_stops(self):
        self.assertEqual(ai_enhancements.REPLICATE_TIMEOUT_S, 90)
        ai_enhancements.REPLICATE_TIMEOUT_S = 0.35
        ai_enhancements.REPLICATE_POLL_INTERVAL_S = 0
        ai_enhancements._pause = lambda _seconds: None

        def fake_urlopen(req, timeout=None):
            data = json.dumps({
                "id": "p1",
                "status": "processing",
                "urls": {"get": "https://api.replicate.com/v1/predictions/p1", "cancel": ""},
            }).encode("utf-8")
            return _Body(data)

        urllib.request.urlopen = fake_urlopen
        os.environ["REPLICATE_API_TOKEN"] = "test-token"
        started = time.time()
        out, err = ai_enhancements._call_replicate(
            "ai_upscale", "nightmareai", "real-esrgan", {"image": "x"}, version="abc",
        )
        elapsed = time.time() - started
        self.assertIsNone(out)
        self.assertIn("busy", err.lower())
        self.assertLess(elapsed, 3.0)


if __name__ == "__main__":
    unittest.main()
