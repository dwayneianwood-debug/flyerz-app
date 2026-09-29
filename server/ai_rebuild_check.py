#!/usr/bin/env python3
"""AI Rebuild: detection, local fallback, Replicate success, and spelling edits."""

from __future__ import annotations

import os
import sys
import tempfile

import cv2
import numpy as np
from PIL import Image
from PIL.PngImagePlugin import PngInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai_rebuild import (  # noqa: E402
    BLEED_MM,
    assess,
    rebuild,
    replicate_credit_status,
    reset_providers,
    set_providers,
)


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("OK  " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""))
    if not ok:
        raise SystemExit(name)


def _sale_image(path: str, size: int = 256) -> None:
    img = np.full((size, size, 3), (40, 90, 180), np.uint8)
    cv2.rectangle(img, (20, 80), (size - 20, 160), (40, 90, 180), -1)
    cv2.putText(img, "SALE", (30, 140), cv2.FONT_HERSHEY_SIMPLEX, 2.2, (255, 255, 255), 4, cv2.LINE_AA)
    cv2.imwrite(path, img)


def _ocr(_bgr):
    return [{
        "id": "t1",
        "text": "SALE",
        "bbox": [0.08, 0.28, 0.7, 0.28],
        "color_hex": "#ffffff",
        "bold": True,
    }]


def _pdf_text(path: str) -> str:
    import pymupdf as fitz

    doc = fitz.open(path)
    try:
        page = doc[0]
        fonts = " ".join(str(item) for item in (page.get_fonts() or []))
        return (page.get_text("text") or "") + "\n" + fonts
    finally:
        doc.close()


def _boxes(path: str) -> tuple:
    import pymupdf as fitz

    doc = fitz.open(path)
    try:
        page = doc[0]
        return page.mediabox, page.trimbox, page.get_text("text") or ""
    finally:
        doc.close()


def test_detection() -> None:
    folder = tempfile.mkdtemp()
    ai_path = os.path.join(folder, "ai.png")
    Image.new("RGB", (1024, 1024), (20, 40, 200)).save(ai_path)
    verdict = assess(ai_path, 90, 50)
    check("ai-size-detected", verdict.get("detected") is True and verdict.get("autoRebuild") is True, str(verdict.get("reasons")))

    photo = os.path.join(folder, "photo.jpg")
    Image.fromarray(np.zeros((480, 640, 3), np.uint8) + 80).save(photo, format="JPEG", dpi=(300, 300), quality=90)
    quiet = assess(photo, 148, 210)
    check("photo-not-flagged", quiet.get("detected") is False, str(quiet.get("reasons")))

    tagged = os.path.join(folder, "gemini.png")
    info = PngInfo()
    info.add_text("Software", "Made with Gemini")
    Image.new("RGB", (640, 480), (10, 10, 10)).save(tagged, pnginfo=info)
    hinted = assess(tagged, 90, 50)
    check("gemini-meta", hinted.get("detected") is True, str(hinted.get("reasons")))

    import pymupdf as fitz

    pdf_path = os.path.join(folder, "live.pdf")
    doc = fitz.open()
    page = doc.new_page(width=300, height=400)
    page.insert_text((40, 80), "HELLO", fontsize=24)
    doc.save(pdf_path)
    doc.close()
    live = assess(pdf_path, 90, 50)
    check("vector-pdf-skipped", live.get("detected") is False, str(live.get("reasons")))


def test_no_credit() -> None:
    called = {"inpaint": 0, "upscale": 0}

    def inpaint(*_args):
        called["inpaint"] += 1
        raise RuntimeError("402")

    def upscale(*_args):
        called["upscale"] += 1
        raise RuntimeError("402")

    set_providers(ocr=_ocr, inpaint=inpaint, upscale=upscale, account=lambda: "402")
    folder = tempfile.mkdtemp()
    src = os.path.join(folder, "sale.png")
    out = os.path.join(folder, "out.pdf")
    _sale_image(src)
    try:
        result = rebuild(src, {
            "trim_w_mm": 90,
            "trim_h_mm": 50,
            "output_pdf": out,
            "before_path": os.path.join(folder, "before.png"),
            "after_path": os.path.join(folder, "after.png"),
            "force": True,
        })
    finally:
        reset_providers()
    check("402-success", result.get("success") is True and os.path.exists(out), result.get("message", ""))
    check("402-hooks-not-called", called["inpaint"] == 0 and called["upscale"] == 0, str(called))
    engines = {step["name"]: step["engine"] for step in result.get("steps") or []}
    check("402-local-steps", engines.get("Remove text") == "local" and engines.get("Upscale") == "local", str(engines))
    check("402-ocr-text", result.get("ocrText") == "SALE", result.get("ocrText", ""))
    check("402-dpi", int(result.get("effective_dpi") or 0) >= 300, str(result.get("effective_dpi")))
    text = _pdf_text(out)
    check("402-vector-text", "SALE" in text and "FlyerzSans" in text, text[:240])
    media, trim, _ = _boxes(out)
    bleed_pt = BLEED_MM * 72.0 / 25.4
    check("402-bleed-5mm", abs(trim.x0 - bleed_pt) < 0.8 and abs(media.width - (100 * 72.0 / 25.4)) < 1.5, f"{trim.x0:.2f} {media.width:.2f}")
    check("402-thumbs", os.path.exists(os.path.join(folder, "before.png")) and os.path.exists(os.path.join(folder, "after.png")))
    from press_ready_engine import compile_vector_press
    press = os.path.join(folder, "press.pdf")
    live = compile_vector_press(out, press, 90, 50, 5)
    press_text = _pdf_text(press) if live.get("used") else ""
    check("engine-keeps-text", live.get("used") is True and "SALE" in press_text, press_text[:180])
    note = " ".join(step.get("note", "") for step in result["steps"])
    check("402-recorded", "402" in note, note)


def test_replicate_and_edit() -> None:
    def inpaint(bgr, _mask):
        out = bgr.copy()
        out[:4, :4] = (0, 255, 0)
        return out

    def upscale(bgr, tw, th):
        return cv2.resize(bgr, (int(tw), int(th)), interpolation=cv2.INTER_CUBIC)

    set_providers(ocr=_ocr, inpaint=inpaint, upscale=upscale, account=lambda: "ok")
    folder = tempfile.mkdtemp()
    src = os.path.join(folder, "sale.png")
    out = os.path.join(folder, "out.pdf")
    edited = os.path.join(folder, "edited.pdf")
    _sale_image(src)
    try:
        result = rebuild(src, {"trim_w_mm": 90, "trim_h_mm": 50, "output_pdf": out, "force": True})
        fixed = rebuild(src, {
            "trim_w_mm": 90,
            "trim_h_mm": 50,
            "output_pdf": edited,
            "force": True,
            "blocks": [{"id": "t1", "text": "FIXED"}],
        })
    finally:
        reset_providers()
    engines = {step["name"]: step["engine"] for step in result.get("steps") or []}
    check("replicate-steps", engines.get("Remove text") == "replicate" and engines.get("Upscale") == "replicate", str(engines))
    check("replicate-text", "SALE" in _pdf_text(out))
    edited_text = _pdf_text(edited)
    check("spelling-edit", "FIXED" in edited_text and "SALE" not in fixed.get("ocrText", ""), fixed.get("ocrText", ""))
    check("edit-success", fixed.get("success") is True)


def test_provider_crash_falls_back() -> None:
    def upscale(*_args):
        raise RuntimeError("402 payment required")

    set_providers(ocr=_ocr, upscale=upscale, account=lambda: "ok")
    folder = tempfile.mkdtemp()
    src = os.path.join(folder, "sale.png")
    out = os.path.join(folder, "out.pdf")
    _sale_image(src, 128)
    try:
        result = rebuild(src, {"trim_w_mm": 90, "trim_h_mm": 50, "output_pdf": out, "force": True})
    finally:
        reset_providers()
    engines = {step["name"]: step["engine"] for step in result.get("steps") or []}
    check("crash-still-succeeds", result.get("success") is True and os.path.exists(out), result.get("message", ""))
    check("crash-local-upscale", engines.get("Upscale") == "local", str(engines))
    check("no-token-status", replicate_credit_status() in ("none", "error", "402", "ok"))


def test_real_local_ocr() -> None:
    reset_providers()
    saved = os.environ.pop("REPLICATE_API_TOKEN", None)
    folder = tempfile.mkdtemp()
    src = os.path.join(folder, "sale.png")
    out = os.path.join(folder, "out.pdf")
    _sale_image(src, 320)
    try:
        result = rebuild(src, {"trim_w_mm": 90, "trim_h_mm": 50, "output_pdf": out, "force": True})
    finally:
        if saved is not None:
            os.environ["REPLICATE_API_TOKEN"] = saved
        reset_providers()
    check("rapid-success", result.get("success") is True, result.get("message", ""))
    engines = {step["name"]: step["engine"] for step in result.get("steps") or []}
    check("rapid-local", engines.get("OCR") == "local" and engines.get("Upscale") == "local", str(result.get("steps")))
    check("rapid-reads-sale", "SALE" in (result.get("ocrText") or "") and "SALE" in _pdf_text(out), result.get("ocrText", ""))


if __name__ == "__main__":
    test_detection()
    test_no_credit()
    test_replicate_and_edit()
    test_provider_crash_falls_back()
    test_real_local_ocr()
    print("AI rebuild checks passed")
