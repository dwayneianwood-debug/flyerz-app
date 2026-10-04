#!/usr/bin/env python3
"""Checks for optional AI upscale: scale cap, fallback, failure, and enhance-then-bleed."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai_upscale import (  # noqa: E402
    AI_BUSY_MESSAGE,
    MAX_LONG_EDGE,
    UPSCALE_MODEL_ID,
    UPSCALE_MODEL_VERSION,
    apply_ai_upscale,
    assess_artwork,
    basic_lanczos_upscale,
    choose_upscale_plan,
    effective_print_dpi,
    set_upscale_provider,
)
from colour_border import apply_colour_border_bgr, cmyk_to_bgr  # noqa: E402


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("OK  " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""))
    if not ok:
        raise SystemExit(name)


def test_scale_plan() -> None:
    dpi = effective_print_dpi(200, 300, 90, 140, 5)
    check("low-dpi", dpi < 200, str(dpi))
    plan = choose_upscale_plan(200, 300, 90, 140, 5)
    check("model-scale", plan["model_scale"] in (2, 4), str(plan))
    check("plan-dpi", plan["effective_dpi"] == dpi, str(plan["effective_dpi"]))
    huge = choose_upscale_plan(3000, 2000, 500, 500, 5)
    check("capped-scale", huge["model_scale"] == 1, str(huge["model_scale"]))
    check("capped-edge", max(huge["target_w"], huge["target_h"]) <= MAX_LONG_EDGE, str(huge))
    check("cap-constant", MAX_LONG_EDGE == 4000)


def test_basic_fallback() -> None:
    saved = os.environ.pop("REPLICATE_API_TOKEN", None)
    set_upscale_provider(None)
    try:
        src = np.zeros((48, 64, 3), dtype=np.uint8)
        src[:, :] = (30, 40, 50)
        src[10:30, 20:40] = (200, 20, 20)
        folder = tempfile.mkdtemp()
        path = os.path.join(folder, "soft.jpg")
        cv2.imwrite(path, src)
        result = apply_ai_upscale(path, {
            "provider": "basic",
            "trim_w_mm": 90,
            "trim_h_mm": 140,
            "bleed_mm": 5,
            "output_path": os.path.join(folder, "full.png"),
            "before_preview_path": os.path.join(folder, "before.png"),
            "after_preview_path": os.path.join(folder, "after.png"),
        })
        check("basic-success", result.get("success") is True and result.get("used_original") is False, str(result.get("message")))
        check("basic-provider", result.get("provider") == "basic" and result.get("basic") is True)
        check("basic-label", "basic enhancement" in result.get("message", "").lower())
        check("basic-not-ai-note", "AI enhancement was applied" not in result.get("note", ""))
        check("basic-note", "Basic enhancement was applied" in result.get("note", ""))
        check("basic-larger", result["out_w"] >= 64 and result["out_h"] >= 48, f"{result['out_w']}x{result['out_h']}")
        check("basic-cap", max(result["out_w"], result["out_h"]) <= MAX_LONG_EDGE)
        with Image.open(result["enhanced_path"]) as image:
            dpi = image.info.get("dpi")
        check("basic-dpi-meta", dpi is not None and round(dpi[0]) == 300 and round(dpi[1]) == 300, str(dpi))
        check("previews", os.path.exists(os.path.join(folder, "before.png")) and os.path.exists(os.path.join(folder, "after.png")))
        up = cv2.imread(result["enhanced_path"], cv2.IMREAD_COLOR)
        bordered = apply_colour_border_bgr(up, 5, (0, 100, 100, 0))
        red = cmyk_to_bgr((0, 100, 100, 0))
        check("border-after-upscale", tuple(int(v) for v in bordered[0, 0]) == tuple(int(v) for v in red))
        check("trim-is-enhanced", np.array_equal(bordered[5:-5, 5:-5], up))
    finally:
        set_upscale_provider(None)
        if saved is not None:
            os.environ["REPLICATE_API_TOKEN"] = saved


def test_stub_and_failure() -> None:
    folder = tempfile.mkdtemp()
    path = os.path.join(folder, "tiny.png")
    cv2.imwrite(path, np.full((40, 30, 3), 80, np.uint8))

    def stub(src, scale, target_w, target_h):
        out = np.zeros((target_h, target_w, 3), dtype=np.uint8)
        out[:, :] = (9, 8, 7)
        dest = os.path.join(folder, "stub.png")
        cv2.imwrite(dest, out)
        return dest, None

    set_upscale_provider(stub)
    try:
        result = apply_ai_upscale(path, {
            "provider": "stub",
            "assume_token": True,
            "trim_w_mm": 90,
            "trim_h_mm": 120,
            "output_path": os.path.join(folder, "stub-full.png"),
        })
        check("stub-provider", result.get("provider") == "stub" and result.get("used_original") is False, str(result))
        check("stub-model", result.get("model") == UPSCALE_MODEL_ID and result.get("version") == UPSCALE_MODEL_VERSION)
        loaded = cv2.imread(result["enhanced_path"], cv2.IMREAD_COLOR)
        check("stub-pixel", tuple(int(v) for v in loaded[loaded.shape[0] // 2, loaded.shape[1] // 2]) == (9, 8, 7))
    finally:
        set_upscale_provider(None)

    def boom(src, scale, target_w, target_h):
        return None, "timed out talking to Replicate"

    set_upscale_provider(boom)
    try:
        failed = apply_ai_upscale(path, {
            "provider": "stub",
            "assume_token": True,
            "trim_w_mm": 90,
            "trim_h_mm": 120,
        })
        check("failure-continues", failed.get("success") is True and failed.get("used_original") is True, str(failed))
        check("failure-message", failed.get("message") == AI_BUSY_MESSAGE, failed.get("message", ""))
        check("failure-no-note", failed.get("note") == "")
    finally:
        set_upscale_provider(None)


def test_vector_pdf_skipped() -> None:
    import fitz

    folder = tempfile.mkdtemp()
    path = os.path.join(folder, "vector.pdf")
    doc = fitz.open()
    page = doc.new_page(width=200, height=200)
    page.insert_text((36, 72), "Sharp vector headline")
    page.draw_rect(fitz.Rect(20, 100, 180, 160), color=(0, 0, 0), width=1)
    doc.save(path)
    doc.close()
    assessed = assess_artwork(path, 90, 140, 5)
    check("vector-skipped", assessed.get("eligible") is False and assessed.get("kind") == "vector_pdf", str(assessed))
    result = apply_ai_upscale(path, {"trim_w_mm": 90, "trim_h_mm": 140, "provider": "basic"})
    check("vector-original", result.get("used_original") is True and result.get("provider") == "original")


def test_compile_uses_enhanced_before_border() -> None:
    folder = tempfile.mkdtemp()
    src = os.path.join(folder, "art.jpg")
    original = np.full((60, 80, 3), (40, 50, 60), np.uint8)
    cv2.imwrite(src, original, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
    enhanced_path = os.path.join(folder, "enhanced.png")
    enhanced = np.full((150, 200, 3), (180, 40, 20), np.uint8)
    cv2.imwrite(enhanced_path, enhanced)
    note = (
        f"AI enhancement was applied before bleed using {UPSCALE_MODEL_ID} "
        f"version {UPSCALE_MODEL_VERSION} at scale 2. "
        "Bleed, including any solid colour border, was added afterwards and was not AI-processed."
    )
    with open(enhanced_path + ".json", "w", encoding="utf-8") as handle:
        json.dump({"src_w": 80, "src_h": 60, "kind": "raster", "note": note}, handle)

    dest = os.path.join(folder, "press.pdf")
    status = os.path.join(folder, "status.json")
    result_path = os.path.join(folder, "result.json")
    cmd = [
        sys.executable,
        os.path.join(os.path.dirname(__file__), "compile_press_pdf.py"),
        "--input", src,
        "--output", dest,
        "--strategy", "colourBorder",
        "--color-space", "cmyk",
        "--trim-w", "40",
        "--trim-h", "30",
        "--status-file", status,
        "--result-file", result_path,
        "--border-c", "0",
        "--border-m", "100",
        "--border-y", "100",
        "--border-k", "0",
        "--border-label", "Red",
        "--base-name", "ai-upscale-check",
        "--ai-upscale-path", enhanced_path,
        "--ai-upscale-note", note,
    ]
    proc = subprocess.run(cmd, cwd=os.path.dirname(os.path.dirname(__file__)), capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        check("compile", False, (proc.stderr or proc.stdout or "")[-1500:])
    with open(result_path, encoding="utf-8") as handle:
        payload = json.load(handle)
    resolution = payload["audit_report"]["resolution_and_lenses"]["action_taken"]
    check("report-notes-ai", "AI enhancement was applied" in resolution, resolution[:240])
    check("report-model", UPSCALE_MODEL_VERSION in resolution and UPSCALE_MODEL_ID in resolution)
    check("stats-flag", payload["compile_stats"].get("ai_upscale_applied") is True)

    import fitz
    import pikepdf

    doc = fitz.open(dest)
    page = doc[0]
    images = page.get_images(full=True)
    check("single-image", len(images) == 1, str(len(images)))
    doc.close()
    pdf = pikepdf.open(dest)
    xobjects = pdf.pages[0].Resources.XObject
    image_name = next(iter(xobjects.keys()))
    obj = xobjects[image_name]
    width = int(obj.Width)
    height = int(obj.Height)
    arr = np.frombuffer(obj.read_bytes(), dtype=np.uint8).reshape(height, width, 4)
    pdf.close()
    border = tuple(int(v) for v in arr[0, 0])
    center = tuple(int(v) for v in arr[height // 2, width // 2])
    check("border-is-red", border == (0, 255, 255, 0), str(border))
    check("trim-not-border", center != border, str(center))
    # Enhanced artwork was blue-dominant BGR (180, 40, 20) → cyan-heavy, not the grey original.
    check("trim-from-enhanced", center[0] > center[1] and center[0] > 40, str(center))


def test_lanczos_cap() -> None:
    src = np.full((100, 100, 3), 128, np.uint8)
    out = basic_lanczos_upscale(src, 8000, 1000)
    check("lanczos-cap", max(out.shape[:2]) <= MAX_LONG_EDGE, str(out.shape))


def test_press_upscale_stays_inside_the_job() -> None:
    """A slow Replicate call must not sit on the end of the job, and its token stays out of the log."""
    import io
    import json
    import threading
    import time
    import urllib.request
    from contextlib import redirect_stderr

    from ai_enhancements import _replicate_create_prediction
    from ai_upscale import (
        ESRGAN_MESSAGE,
        JOB_BUDGET_S,
        LANCZOS_MESSAGE,
        PRESS_GRACE_S,
        PRESS_UPLOAD_EDGE,
        PlateUpscale,
        _press_scale,
        _press_upload,
        log_replicate,
        start_plate_upscale,
    )
    from vector_trace import _enlarge_line, _upgrade_raster

    wide = np.zeros((1536, 1024, 3), np.uint8)
    upload = _press_upload(wide)
    check("upload-under-1600", max(upload.shape[:2]) == PRESS_UPLOAD_EDGE and PRESS_UPLOAD_EDGE < 1600, str(upload.shape))
    original = np.zeros((400, 300, 3), np.uint8)
    check("upload-original", _press_upload(original).shape == original.shape)
    check("scale-quick", _press_scale(upload, 5000) == 2)
    check("scale-small", _press_scale(np.zeros((200, 120, 3), np.uint8), 4000) == 4)

    saved_grace = PRESS_GRACE_S
    saved_budget = JOB_BUDGET_S
    import ai_upscale as upscale_mod

    seen = {}
    gate = threading.Event()

    def slow(upload_bgr, scale, _deadline):
        seen["shape"] = tuple(upload_bgr.shape)
        seen["scale"] = scale
        gate.wait(5)
        return None, "failed", "denied super-secret-token"

    picture = np.full((80, 100, 3), 40, np.uint8)
    upscale_mod.PRESS_GRACE_S = 0.35
    upscale_mod.JOB_BUDGET_S = 35.0
    job = PlateUpscale(picture, 148, 210, runner=slow)
    job._token = "super-secret-token"
    try:
        job.start()
        began = time.perf_counter()
        while "scale" not in seen and time.perf_counter() - began < 2:
            time.sleep(0.01)
        check("started-early", seen.get("scale") in (2, 4) and max(seen.get("shape", (0,))) <= PRESS_UPLOAD_EDGE, str(seen))
        buf = io.StringIO()
        with redirect_stderr(buf):
            tick = time.perf_counter()
            missed = job.take()
            waited = time.perf_counter() - tick
            gate.set()
            job._done.wait(2)
        text = buf.getvalue()
        check("miss-is-none", missed is None)
        check("miss-does-not-block", waited < 1.5, f"{waited:.2f}s")
        check("log-status", "status=" in text and ("deadline" in text or "failed" in text), text)
        check("log-hides-token", "super-secret-token" not in text, text)
    finally:
        gate.set()
        upscale_mod.PRESS_GRACE_S = saved_grace
        upscale_mod.JOB_BUDGET_S = saved_budget
        job.cancel()

    buf = io.StringIO()
    with redirect_stderr(buf):
        log_replicate("failed", "model said super-secret-token exploded", "super-secret-token")
    logged = buf.getvalue()
    check("log-error-text", "status=failed" in logged and "exploded" in logged, logged)
    check("log-strips-token", "super-secret-token" not in logged, logged)

    plate = np.full((80, 120, 3), 30, np.uint8)
    placed = {"scale_mm": 25.4 / 180.0, "art_box": (0, 0, 120, 80)}
    missed_job = PlateUpscale(plate, 148, 210)
    missed_job._done.set()
    missed_job.status = "failed"
    missed_job.error = "OOM"
    _upgraded, note = _upgrade_raster(plate, [], placed, plate, missed_job)
    line = _enlarge_line("Lanczos", note)
    check("one-lanczos-message", line == LANCZOS_MESSAGE and "was not used" not in line and "Real-ESRGAN" not in line, line)

    ready = PlateUpscale(plate, 148, 210)
    ready._done.set()
    ready.image = np.full((40, 60, 3), (0, 255, 0), np.uint8)
    upgraded, note = _upgrade_raster(plate, [], placed, plate, ready)
    esrgan_line = _enlarge_line("Lanczos", note)
    check("one-esrgan-message", esrgan_line == ESRGAN_MESSAGE and "Lanczos" not in esrgan_line, esrgan_line)
    check("esrgan-pixels", int(upgraded[20, 30, 1]) > 200, str(tuple(int(v) for v in upgraded[20, 30])))

    sharp = np.full((1200, 2000, 3), 10, np.uint8)
    check("sharp-skips", start_plate_upscale(sharp, 90, 50, runner=slow) is None)
    called = {}

    def marker(upload_bgr, scale, _deadline):
        called["hit"] = True
        return None, "failed", "should-not-run"

    soft = np.zeros((400, 300, 3), np.uint8)
    saved_flag = os.environ.pop("VECTOR_ESRGAN", None)
    try:
        idle = start_plate_upscale(soft, 148, 210, runner=marker)
        check("esrgan-off-no-start", idle is None and "hit" not in called)
        os.environ["VECTOR_ESRGAN"] = "1"
        started = start_plate_upscale(soft, 148, 210, runner=marker)
        check("esrgan-on-starts", started is not None)
        if started is not None:
            started.cancel()
            started._done.wait(2)
    finally:
        if saved_flag is None:
            os.environ.pop("VECTOR_ESRGAN", None)
        else:
            os.environ["VECTOR_ESRGAN"] = saved_flag

    seen_headers = {}

    class _Body:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps({
                "id": "p",
                "status": "starting",
                "urls": {"get": "https://example.test/p", "cancel": "https://example.test/c"},
            }).encode()

    def fake_urlopen(req, timeout=None):
        seen_headers["prefer"] = req.get_header("Prefer")
        seen_headers["timeout"] = timeout
        seen_headers["auth"] = req.get_header("Authorization")
        return _Body()

    old_urlopen = urllib.request.urlopen
    urllib.request.urlopen = fake_urlopen
    try:
        created = _replicate_create_prediction(
            "nightmareai", "real-esrgan", {"image": "x"}, "super-secret-token",
            version="abc", prefer_wait=False,
        )
    finally:
        urllib.request.urlopen = old_urlopen
    check("async-status", created.get("status") == "starting", str(created))
    check("async-no-prefer", not seen_headers.get("prefer"), str(seen_headers.get("prefer")))
    check("async-short-socket", float(seen_headers.get("timeout") or 99) <= 20, str(seen_headers.get("timeout")))
    check("async-token-not-in-result", "super-secret-token" not in json.dumps(created))


def main() -> None:
    test_scale_plan()
    test_basic_fallback()
    test_stub_and_failure()
    test_vector_pdf_skipped()
    test_lanczos_cap()
    test_press_upscale_stays_inside_the_job()
    test_compile_uses_enhanced_before_border()
    print("all ai upscale checks passed")


if __name__ == "__main__":
    main()
