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


def _ssim(original_bgr: np.ndarray, rendered_bgr: np.ndarray, ignore: np.ndarray) -> float:
    """Mean SSIM on the pixels outside ignore. ignore is 255 where text may differ."""
    height, width = original_bgr.shape[:2]
    rendered = rendered_bgr
    if rendered.shape[:2] != (height, width):
        rendered = cv2.resize(rendered, (width, height), interpolation=cv2.INTER_AREA)
    mask = ignore
    if mask.shape[:2] != (height, width):
        mask = cv2.resize(mask, (width, height), interpolation=cv2.INTER_NEAREST)
    left = cv2.cvtColor(original_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    right = cv2.cvtColor(rendered, cv2.COLOR_BGR2GRAY).astype(np.float32)
    keep = mask < 128
    left = left.copy()
    right = right.copy()
    left[~keep] = 0
    right[~keep] = 0
    c1 = (0.01 * 255) ** 2
    c2 = (0.03 * 255) ** 2
    mu1 = cv2.GaussianBlur(left, (11, 11), 1.5)
    mu2 = cv2.GaussianBlur(right, (11, 11), 1.5)
    sigma1 = cv2.GaussianBlur(left * left, (11, 11), 1.5) - mu1 * mu1
    sigma2 = cv2.GaussianBlur(right * right, (11, 11), 1.5) - mu2 * mu2
    sigma12 = cv2.GaussianBlur(left * right, (11, 11), 1.5) - mu1 * mu2
    score = ((2 * mu1 * mu2 + c1) * (2 * sigma12 + c2)) / ((mu1 * mu1 + mu2 * mu2 + c1) * (sigma1 + sigma2 + c2))
    return float(score[keep].mean())


def _draw_flyer(path: str, kind: str) -> None:
    from PIL import ImageDraw, ImageFont

    canvas = Image.new("RGB", (1024, 1024), (18, 52, 120))
    draw = ImageDraw.Draw(canvas)
    font_path = os.path.join(os.path.dirname(__file__), "fonts", "LiberationSans-Bold.ttf")
    big = ImageFont.truetype(font_path, 78)
    mid = ImageFont.truetype(font_path, 48)
    # A5-portrait cover keeps roughly the centre 72% of a square. Keep shapes and type inside that band.
    if kind == "panel":
        for y in range(0, 1024, 8):
            shade = 18 + (y % 48)
            draw.rectangle([0, y, 1024, y + 8], fill=(shade, 52 + (y % 30), 130))
        draw.ellipse([500, 50, 820, 370], fill=(230, 90, 40))
        draw.rounded_rectangle([180, 420, 844, 960], radius=42, fill=(12, 28, 70), outline=(210, 170, 90), width=8)
        lines = [
            ("GRAND OPENING", big, (190, 500), (255, 236, 180)),
            ("50% OFF PRINTS", mid, (220, 660), (255, 255, 255)),
            ("SATURDAY 10AM", mid, (220, 780), (180, 220, 255)),
        ]
    else:
        rng = np.random.default_rng(3)
        noise = rng.integers(20, 80, (1024, 1024, 3), dtype=np.uint8)
        grad = np.linspace(30, 180, 1024, dtype=np.uint8)
        noise[:, :, 2] = np.clip(noise[:, :, 2].astype(np.int16) + grad[None, :], 0, 255).astype(np.uint8)
        canvas = Image.fromarray(noise, "RGB")
        draw = ImageDraw.Draw(canvas)
        draw.ellipse([560, 620, 860, 920], fill=(40, 140, 90))
        lines = [
            ("SUMMER MARKET", big, (160, 180), (255, 244, 210)),
            ("FRESH DAILY", mid, (210, 320), (255, 255, 255)),
        ]
    for text, font, xy, fill in lines:
        x, y = xy
        for word in text.split(" "):
            draw.text((x, y), word, font=font, fill=fill)
            x += draw.textlength(word, font=font) + 26
    arr = cv2.cvtColor(np.array(canvas), cv2.COLOR_RGB2BGR)
    arr = cv2.GaussianBlur(arr, (0, 0), 1.05)
    cv2.imwrite(path, arr)


def _ocr_bgr(bgr: np.ndarray) -> list:
    from rapidocr_onnxruntime import RapidOCR

    engine = RapidOCR()
    result, _elapsed = engine(bgr)
    lines = []
    height, width = bgr.shape[:2]
    for item in result or []:
        box, text = item[0], str(item[1] or "").strip()
        if not text:
            continue
        ys = [float(p[1]) for p in box]
        xs = [float(p[0]) for p in box]
        lines.append({
            "text": text.upper(),
            "cx": (min(xs) + max(xs)) / 2 / width,
            "cy": (min(ys) + max(ys)) / 2 / height,
            "x0": min(xs) / width,
            "y0": min(ys) / height,
            "x1": max(xs) / width,
            "y1": max(ys) / height,
        })
    return lines


def _ink_colour(bgr: np.ndarray, line: dict) -> tuple:
    """Colour of the glyphs, not the gap between words."""
    height, width = bgr.shape[:2]
    x0 = max(0, int(line["x0"] * width))
    y0 = max(0, int(line["y0"] * height))
    x1 = min(width, int(line["x1"] * width))
    y1 = min(height, int(line["y1"] * height))
    roi = bgr[y0:max(y0 + 1, y1), x0:max(x0 + 1, x1)]
    if roi.size == 0:
        return (0, 0, 0)
    flat = roi.reshape(-1, 3).astype(np.float32)
    border = np.concatenate([
        roi[0].reshape(-1, 3),
        roi[-1].reshape(-1, 3),
        roi[:, 0],
        roi[:, -1],
    ]).astype(np.float32)
    bg = np.median(border, axis=0)
    dist = np.linalg.norm(flat - bg, axis=1)
    cutoff = float(np.percentile(dist, 90))
    ink = flat[dist >= max(cutoff, 12)]
    if ink.size == 0:
        ink = flat
    colour = np.median(ink, axis=0)
    return int(colour[2]), int(colour[1]), int(colour[0])


def test_restore_spaces_from_gaps() -> None:
    """A wide gap between glyph groups is a word space, even when OCR returns one string."""
    from PIL import Image, ImageDraw, ImageFont
    from ai_rebuild import restore_spaces

    canvas = Image.new("RGB", (980, 140), (12, 28, 70))
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.truetype(os.path.join(os.path.dirname(__file__), "fonts", "LiberationSans-Bold.ttf"), 64)
    x = 24
    for word in ("50%", "OFF", "PRINTS"):
        draw.text((x, 30), word, font=font, fill=(255, 255, 255))
        x += int(draw.textlength(word, font=font)) + 34
    bgr = cv2.cvtColor(np.array(canvas), cv2.COLOR_RGB2BGR)
    text = restore_spaces("50%OFFPRINTS", bgr, [0.0, 0.1, 0.98, 0.75])
    check("gap-spaces", text == "50% OFF PRINTS", text)


def test_press_pdf_shows_the_words() -> None:
    """The file that goes to press must show the words, in place, on an intact background."""
    from press_ready_engine import compile_vector_press

    reset_providers()
    saved = os.environ.pop("REPLICATE_API_TOKEN", None)
    folder = tempfile.mkdtemp()
    try:
        cases = [
            ("panel", 148, 210, [
                ("GRAND OPENING", 0.53, (255, 236, 180)),
                ("50% OFF PRINTS", 0.67, (255, 255, 255)),
                ("SATURDAY 10AM", 0.79, (180, 220, 255)),
            ]),
            ("photo", 148, 210, [
                ("SUMMER MARKET", 0.22, (255, 244, 210)),
                ("FRESH DAILY", 0.35, (255, 255, 255)),
            ]),
        ]
        for kind, trim_w, trim_h, expect in cases:
            src = os.path.join(folder, f"{kind}.png")
            rebuilt = os.path.join(folder, f"{kind}-rebuilt.pdf")
            press = os.path.join(folder, f"{kind}-press.pdf")
            _draw_flyer(src, kind)
            result = rebuild(src, {"trim_w_mm": trim_w, "trim_h_mm": trim_h, "output_pdf": rebuilt, "force": True})
            check(f"{kind}-built", result.get("success") is True and os.path.exists(rebuilt), result.get("message", ""))
            check(f"{kind}-spaced", all(phrase in (result.get("ocrText") or "") for phrase, _cy, _rgb in expect), result.get("ocrText", ""))
            live = compile_vector_press(rebuilt, press, trim_w, trim_h, 5.0)
            check(f"{kind}-press", live.get("used") is True and os.path.exists(press), str(live.get("report", {}).get("status")))
            import pymupdf as fitz
            doc = fitz.open(press)
            page = doc[0]
            zoom = 130 / 72
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            rendered = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
            rendered = cv2.cvtColor(rendered[:, :, :3], cv2.COLOR_RGB2BGR)
            doc.close()
            lines = _ocr_bgr(rendered)
            blob = " | ".join(line["text"] for line in lines)
            for phrase, expected_y, rgb in expect:
                hits = [line for line in lines if all(word in line["text"] for word in phrase.split())]
                check(f"{kind}-ocr-{phrase}", len(hits) == 1, blob)
                if not hits:
                    continue
                check(
                    f"{kind}-place-{phrase}",
                    abs(hits[0]["cy"] - expected_y) < 0.08,
                    f"cy {hits[0]['cy']:.3f} expected {expected_y:.2f}",
                )
                found = _ink_colour(rendered, hits[0])
                delta = max(abs(found[i] - rgb[i]) for i in range(3))
                check(f"{kind}-colour-{phrase}", delta <= 55, f"{found} vs {rgb}")
            source = cv2.imread(src, cv2.IMREAD_COLOR)
            from ai_rebuild import _cover
            fitted, _scale, _x0, _y0 = _cover(source, rendered.shape[1], rendered.shape[0], sharpen=False)
            ignore = np.zeros(rendered.shape[:2], np.uint8)
            for line in lines:
                x0 = int(line["x0"] * rendered.shape[1]) - 6
                y0 = int(line["y0"] * rendered.shape[0]) - 6
                x1 = int(line["x1"] * rendered.shape[1]) + 6
                y1 = int(line["y1"] * rendered.shape[0]) + 6
                cv2.rectangle(ignore, (x0, y0), (x1, y1), 255, -1)
            # Background outside the words should still be the artwork, not a wiped panel.
            score = _ssim(fitted, rendered, ignore)
            check(f"{kind}-background", score >= 0.82, f"{score:.3f}")
            import pymupdf as fitz
            text_doc = fitz.open(press)
            press_text = text_doc[0].get_text("text") or ""
            text_doc.close()
            for phrase, _expected_y, _rgb in expect:
                check(f"{kind}-vector-{phrase}", phrase in press_text, press_text.replace("\n", " | "))
    finally:
        if saved is not None:
            os.environ["REPLICATE_API_TOKEN"] = saved
        reset_providers()


if __name__ == "__main__":
    test_detection()
    test_no_credit()
    test_replicate_and_edit()
    test_provider_crash_falls_back()
    test_real_local_ocr()
    test_restore_spaces_from_gaps()
    test_press_pdf_shows_the_words()
    print("AI rebuild checks passed")
