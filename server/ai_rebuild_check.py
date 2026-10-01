#!/usr/bin/env python3
"""AI Rebuild: detection, local fallback, Replicate success, and spelling edits."""

from __future__ import annotations

import os
import subprocess
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
    big = ImageFont.truetype(font_path, 64)
    mid = ImageFont.truetype(font_path, 46)
    # A5-portrait cover keeps roughly the centre 72% of a square. Keep shapes and type inside that band.
    if kind == "panel":
        for y in range(0, 1024, 8):
            shade = 18 + (y % 48)
            draw.rectangle([0, y, 1024, y + 8], fill=(shade, 52 + (y % 30), 130))
        draw.ellipse([500, 50, 820, 370], fill=(230, 90, 40))
        draw.rounded_rectangle([180, 420, 844, 960], radius=42, fill=(12, 28, 70), outline=(210, 170, 90), width=8)
        lines = [
            ("GRAND OPENING", big, (250, 520), (255, 236, 180)),
            ("50% OFF PRINTS", mid, (250, 660), (255, 255, 255)),
            ("SATURDAY 10AM", mid, (250, 790), (180, 220, 255)),
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
            ("SUMMER MARKET", big, (250, 180), (255, 244, 210)),
            ("FRESH DAILY", mid, (250, 320), (255, 255, 255)),
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
    from ocr_reader import local_rows

    result = local_rows(bgr)
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


def _page_box(block: dict, src_shape, scale: float, off_x: int, off_y: int, canvas_w: int, canvas_h: int) -> tuple:
    src_h, src_w = src_shape[:2]
    x, y, bw, bh = [float(v) for v in block["bbox"][:4]]
    px = x * src_w * scale - off_x
    py = y * src_h * scale - off_y
    pw = bw * src_w * scale
    ph = bh * src_h * scale
    return px / canvas_w, py / canvas_h, (px + pw) / canvas_w, (py + ph) / canvas_h


def _ink_bbox(bgr: np.ndarray, rgb: tuple, band: tuple) -> tuple:
    """Tight box of pixels close to rgb, inside a vertical band of the page."""
    height, width = bgr.shape[:2]
    y0 = max(0, int(band[0] * height))
    y1 = min(height, int(band[1] * height))
    view = cv2.cvtColor(bgr[y0:y1], cv2.COLOR_BGR2RGB).astype(np.int16)
    dist = np.linalg.norm(view - np.array(rgb, np.int16), axis=2)
    ys, xs = np.where(dist <= 42)
    if len(xs) < 20:
        return None
    return xs.min() / width, (ys.min() + y0) / height, xs.max() / width, (ys.max() + y0) / height


def _lab_delta(left_bgr: np.ndarray, right_bgr: np.ndarray) -> np.ndarray:
    def lab(img: np.ndarray) -> np.ndarray:
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        lin = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
        matrix = np.array([
            [0.4124564, 0.3575761, 0.1804375],
            [0.2126729, 0.7151522, 0.0721750],
            [0.0193339, 0.1191920, 0.9503041],
        ], np.float32)
        xyz = lin @ matrix.T
        xyz[..., 0] /= 0.95047
        xyz[..., 2] /= 1.08883
        def f(channel):
            return np.where(channel > 0.008856, np.cbrt(channel), 7.787 * channel + 16.0 / 116.0)
        fx, fy, fz = f(xyz[..., 0]), f(xyz[..., 1]), f(xyz[..., 2])
        return np.dstack([116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)])
    return np.linalg.norm(lab(left_bgr) - lab(right_bgr), axis=2)


def _proof_bgr(pdf_path: str, dest_png: str) -> np.ndarray:
    """RGB proof of a press PDF through the same FOGRA39 profile."""
    icc = os.path.join(os.path.dirname(__file__), "profiles", "CoatedFOGRA39.icc")
    srgb = "/usr/share/color/icc/ghostscript/srgb.icc"
    cmd = [
        "gs", "-dNOPAUSE", "-dBATCH", "-dSAFER", "-sDEVICE=png16m",
        f"-sOutputFile={dest_png}", "-r110",
        "-dRenderIntent=1", "-dBlackPtComp=1",
        "-dNumRenderingThreads=1", "-dBufferSpace=50000000", "-dMaxBitmap=50000000",
        "-f", pdf_path,
    ]
    if os.path.isfile(icc):
        cmd.insert(-2, f"-sDefaultCMYKProfile={icc}")
    if os.path.isfile(srgb):
        cmd.insert(-2, f"-sOutputICCProfile={srgb}")
    subprocess.run(cmd, check=True, capture_output=True, timeout=90)
    image = cv2.imread(dest_png, cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError("proof render failed")
    return image


def test_inpaint_removes_the_letters() -> None:
    """After the letters are removed, and before new type is drawn, nothing readable remains."""
    from ai_rebuild import _local_inpaint, _mask_from_blocks, read_text_blocks

    reset_providers()
    folder = tempfile.mkdtemp()
    for kind, phrases in (("panel", ("GRAND OPENING", "50% OFF PRINTS", "SATURDAY 10AM")), ("photo", ("SUMMER MARKET", "FRESH DAILY"))):
        src = os.path.join(folder, f"{kind}-mask.png")
        _draw_flyer(src, kind)
        image = cv2.imread(src, cv2.IMREAD_COLOR)
        blocks, _engine, _note = read_text_blocks(image)
        mask = _mask_from_blocks(image, blocks)
        clean = _local_inpaint(image, mask)
        found = " | ".join(line["text"] for line in _ocr_bgr(clean))
        for phrase in phrases:
            check(f"{kind}-no-ghost-ocr", phrase not in found and not any(word in found for word in phrase.split() if len(word) > 3), found)
        height, width = clean.shape[:2]
        for block in blocks:
            x, y, bw, bh = [float(v) for v in block["bbox"][:4]]
            x0, y0 = int(x * width), int(y * height)
            x1, y1 = int((x + bw) * width), int((y + bh) * height)
            roi = clean[y0:y1, x0:x1].astype(np.float32)
            if roi.size == 0:
                continue
            med = np.median(roi.reshape(-1, 3), axis=0)
            inside = np.linalg.norm(roi - med, axis=2)
            above = clean[max(0, y0 - 36):max(0, y0 - 4), x0:x1]
            if above.size == 0:
                continue
            neighbor = np.linalg.norm(above.astype(np.float32) - np.median(above.reshape(-1, 3), axis=0), axis=2)
            check(
                f"{kind}-flat-{block['text']}",
                float(np.percentile(inside, 99)) <= float(np.percentile(neighbor, 99)) + 12,
                f"{np.percentile(inside, 99):.1f} vs {np.percentile(neighbor, 99):.1f}",
            )


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
            from ai_rebuild import _cover, _target_pixels
            canvas_w, canvas_h = _target_pixels(trim_w, trim_h)
            _fitted, scale, off_x, off_y = _cover(source, canvas_w, canvas_h, sharpen=False)
            safe_x = (5.0 + 3.0) / (trim_w + 10.0)
            safe_y = (5.0 + 3.0) / (trim_h + 10.0)
            by_phrase = {}
            for block in result.get("blocks") or []:
                by_phrase[str(block.get("text") or "")] = block
            for phrase, _expected_y, rgb in expect:
                block = by_phrase.get(phrase)
                check(f"{kind}-block-{phrase}", block is not None, str(list(by_phrase)))
                if block is None:
                    continue
                ox0, oy0, ox1, oy1 = _page_box(block, source.shape, scale, off_x, off_y, canvas_w, canvas_h)
                found_box = _ink_bbox(rendered, rgb, (oy0 - 0.04, oy1 + 0.04))
                check(f"{kind}-ink-{phrase}", found_box is not None, phrase)
                if found_box is None:
                    continue
                rx0, ry0, rx1, ry1 = found_box
                bw = max(ox1 - ox0, 1e-4)
                bh = max(oy1 - oy0, 1e-4)
                inside = (
                    rx0 >= ox0 - 0.02 * bw and rx1 <= ox1 + 0.02 * bw
                    and ry0 >= oy0 - 0.02 * bh and ry1 <= oy1 + 0.02 * bh
                )
                check(
                    f"{kind}-fit-{phrase}",
                    inside,
                    f"render {rx0:.3f},{ry0:.3f},{rx1:.3f},{ry1:.3f} ocr {ox0:.3f},{oy0:.3f},{ox1:.3f},{oy1:.3f}",
                )
                check(
                    f"{kind}-safe-{phrase}",
                    rx0 >= safe_x - 0.004 and rx1 <= 1 - safe_x + 0.004 and ry0 >= safe_y - 0.004 and ry1 <= 1 - safe_y + 0.004,
                    f"{rx0:.3f},{ry0:.3f},{rx1:.3f},{ry1:.3f}",
                )
            # Same CMYK profile on the original picture and on the press file.
            import pymupdf as fitz
            ref_pdf = os.path.join(folder, f"{kind}-ref.pdf")
            ref_doc = fitz.open()
            ref_page = ref_doc.new_page(width=(trim_w + 10) * 72 / 25.4, height=(trim_h + 10) * 72 / 25.4)
            ok, encoded = cv2.imencode(".png", _fitted)
            check(f"{kind}-ref-png", ok)
            ref_page.insert_image(ref_page.rect, stream=encoded.tobytes())
            ref_doc.save(ref_pdf)
            ref_doc.close()
            from press_ready_engine import convert_cmyk_keep_text
            ref_cmyk = os.path.join(folder, f"{kind}-ref-cmyk.pdf")
            convert_cmyk_keep_text(ref_pdf, ref_cmyk)
            proof_press = _proof_bgr(press, os.path.join(folder, f"{kind}-press-proof.png"))
            proof_ref = _proof_bgr(ref_cmyk, os.path.join(folder, f"{kind}-ref-proof.png"))
            if proof_press.shape[:2] != proof_ref.shape[:2]:
                proof_press = cv2.resize(proof_press, (proof_ref.shape[1], proof_ref.shape[0]), interpolation=cv2.INTER_AREA)
            ignore = np.zeros(proof_ref.shape[:2], np.uint8)
            for phrase, _expected_y, _rgb in expect:
                block = by_phrase.get(phrase)
                if not block:
                    continue
                ox0, oy0, ox1, oy1 = _page_box(block, source.shape, scale, off_x, off_y, canvas_w, canvas_h)
                pad_x = 0.02 * (ox1 - ox0) + 0.008
                pad_y = 0.02 * (oy1 - oy0) + 0.008
                cv2.rectangle(
                    ignore,
                    (int((ox0 - pad_x) * ignore.shape[1]), int((oy0 - pad_y) * ignore.shape[0])),
                    (int((ox1 + pad_x) * ignore.shape[1]), int((oy1 + pad_y) * ignore.shape[0])),
                    255,
                    -1,
                )
            delta = _lab_delta(proof_ref, proof_press)
            kept = delta[ignore < 128]
            mean_de = float(kept.mean()) if kept.size else 99.0
            check(f"{kind}-deltae", mean_de < 3.0, f"{mean_de:.2f}")
    finally:
        if saved is not None:
            os.environ["REPLICATE_API_TOKEN"] = saved
        reset_providers()


def test_doubtful_marks_are_not_retyped() -> None:
    """Single letters, huge blobs, faint reads, and junk stay out of the retype list."""
    from ai_rebuild import keep_word_blocks

    sale = {"text": "SALE", "bbox": [0.08, 0.28, 0.7, 0.28], "score": 1}
    offer = {"text": "50% OFF PRINTS", "bbox": [0.1, 0.6, 0.5, 0.05], "score": 0.97}
    when = {"text": "SATURDAY 9AM", "bbox": [0.1, 0.8, 0.4, 0.05], "score": 1}
    circle = {"text": "O", "bbox": [0.08, 0.0, 0.66, 0.60], "score": 0.87}
    huge = {"text": "HELLO", "bbox": [0.1, 0.1, 0.5, 0.5], "score": 0.99}
    faint = {"text": "MARKET DAY", "bbox": [0.1, 0.7, 0.4, 0.06], "score": 0.4}
    junk = {"text": "###", "bbox": [0.1, 0.1, 0.2, 0.05], "score": 0.9}
    letter = {"text": "A", "bbox": [0.77, 0.21, 0.12, 0.14], "score": 1}
    kept, dropped = keep_word_blocks([sale, offer, when, circle, huge, faint, junk, letter])
    check("keeps-real-words", [item["text"] for item in kept] == ["SALE", "50% OFF PRINTS", "SATURDAY 9AM"], str([item["text"] for item in kept]))
    check(
        "drops-doubtful",
        [item["text"] for item in dropped] == ["O", "HELLO", "MARKET DAY", "###", "A"],
        str([item["text"] for item in dropped]),
    )


def test_contacts_and_dashes_are_words() -> None:
    """An en dash, an email, and a phone number are lettering."""
    from ai_rebuild import _word_like, keep_word_blocks

    dash = "Invest in your health today \u2013"
    email = "medella.lba@gmail.com"
    phone = "073 703 0766"
    check("dash-word", _word_like(dash) and _word_like("Save \u2014 today") and _word_like("Tom\u2019s \u2022 list"))
    check("email-word", _word_like(email))
    kept, dropped = keep_word_blocks([
        {"text": dash, "bbox": [0.1, 0.3, 0.5, 0.04], "score": 0.91},
        {"text": email, "bbox": [0.1, 0.4, 0.5, 0.04], "score": 0.2},
        {"text": phone, "bbox": [0.1, 0.5, 0.4, 0.04], "score": 0.3},
        {"text": "###", "bbox": [0.1, 0.1, 0.2, 0.05], "score": 0.9},
        {"text": "MARKET DAY", "bbox": [0.1, 0.7, 0.4, 0.06], "score": 0.4},
    ])
    check(
        "keeps-dash-email-phone",
        [item["text"] for item in kept] == [dash, email, phone],
        str([item["text"] for item in kept]),
    )
    check(
        "still-drops-faint-and-junk",
        [item["text"] for item in dropped] == ["###", "MARKET DAY"],
        str([item["text"] for item in dropped]),
    )


def _draw_shape_flyer(path: str) -> None:
    """Big circle, a badge, one large letter, and two real lines. The circle is what OCR calls O."""
    from PIL import ImageDraw, ImageFont

    canvas = Image.new("RGB", (1024, 1024), (24, 72, 140))
    draw = ImageDraw.Draw(canvas)
    font_path = os.path.join(os.path.dirname(__file__), "fonts", "LiberationSans-Bold.ttf")
    draw.ellipse((160, 20, 640, 500), fill=(240, 120, 20))
    draw.ellipse((780, 560, 980, 760), fill=(212, 168, 42))
    draw.regular_polygon((880, 660, 70), 5, rotation=0, fill=(120, 28, 18))
    draw.text((800, 200), "A", font=ImageFont.truetype(font_path, 160), fill=(255, 255, 255))
    draw.text((160, 780), "MARKET DAY", font=ImageFont.truetype(font_path, 64), fill=(255, 236, 180))
    draw.text((160, 900), "SATURDAY 9AM", font=ImageFont.truetype(font_path, 48), fill=(255, 255, 255))
    canvas.save(path, format="PNG")


def _orange_disc(bgr: np.ndarray):
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB).astype(np.int16)
    mask = (rgb[:, :, 0] > 190) & (rgb[:, :, 1] > 70) & (rgb[:, :, 1] < 190) & (rgb[:, :, 2] < 90)
    count, _labels, stats, _cent = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
    best = None
    for index in range(1, count):
        area = int(stats[index, cv2.CC_STAT_AREA])
        if best is None or area > best[0]:
            width = int(stats[index, cv2.CC_STAT_WIDTH])
            height = int(stats[index, cv2.CC_STAT_HEIGHT])
            best = (area, width / float(max(height, 1)), area / float(max(width * height, 1)))
    return best


def _region_delta(source_bgr: np.ndarray, rendered_bgr: np.ndarray, box: tuple) -> float:
    from ai_rebuild import _cover

    fitted, _scale, _x0, _y0 = _cover(source_bgr, rendered_bgr.shape[1], rendered_bgr.shape[0], sharpen=False)

    def crop(img: np.ndarray) -> np.ndarray:
        height, width = img.shape[:2]
        x0, y0, x1, y1 = box
        return img[int(y0 * height):int(y1 * height), int(x0 * width):int(x1 * width)]

    left = crop(fitted).astype(np.float32)
    right = crop(rendered_bgr).astype(np.float32)
    if left.size == 0 or right.size == 0:
        return 999.0
    if left.shape != right.shape:
        right = cv2.resize(right, (left.shape[1], left.shape[0]), interpolation=cv2.INTER_AREA)
    return float(np.mean(np.abs(left - right)))


def _span_boxes(path: str) -> list:
    import pymupdf as fitz

    doc = fitz.open(path)
    try:
        found = []
        for block in doc[0].get_text("dict")["blocks"]:
            for line in block.get("lines") or []:
                for span in line.get("spans") or []:
                    text = str(span.get("text") or "").strip()
                    if not text:
                        continue
                    box = tuple(float(v) for v in span["bbox"])
                    found.append((text, box, float(span["size"])))
        return found
    finally:
        doc.close()


def test_upscaled_text_matches_local_placement() -> None:
    """A 4× upscaler must not shrink the words or slide them into the corner."""

    def ocr(_bgr):
        return [
            {"id": "t1", "text": "MARKET DAY", "bbox": [0.12, 0.18, 0.50, 0.09], "color_hex": "#ffffff", "bold": True, "score": 1},
            {"id": "t2", "text": "SATURDAY 9AM", "bbox": [0.12, 0.32, 0.46, 0.07], "color_hex": "#ffffff", "bold": False, "score": 1},
        ]

    def inpaint(bgr, _mask):
        return bgr.copy()

    def upscale(bgr, _tw, _th):
        return cv2.resize(bgr, (bgr.shape[1] * 4, bgr.shape[0] * 4), interpolation=cv2.INTER_CUBIC)

    folder = tempfile.mkdtemp()
    src = os.path.join(folder, "plain.png")
    Image.new("RGB", (320, 320), (36, 48, 120)).save(src)
    local_pdf = os.path.join(folder, "local.pdf")
    remote_pdf = os.path.join(folder, "remote.pdf")
    set_providers(ocr=ocr, inpaint=inpaint, account=lambda: "none")
    try:
        local = rebuild(src, {"trim_w_mm": 148, "trim_h_mm": 210, "output_pdf": local_pdf, "force": True})
    finally:
        reset_providers()
    set_providers(ocr=ocr, inpaint=inpaint, upscale=upscale, account=lambda: "ok")
    try:
        remote = rebuild(src, {"trim_w_mm": 148, "trim_h_mm": 210, "output_pdf": remote_pdf, "force": True})
    finally:
        reset_providers()
    engines = {step["name"]: step["engine"] for step in remote.get("steps") or []}
    check("upscale-used", engines.get("Upscale") == "replicate" and local.get("success") is True, str(engines))
    left = _span_boxes(local_pdf)
    right = _span_boxes(remote_pdf)
    check("upscale-same-lines", [item[0] for item in left] == [item[0] for item in right] == ["MARKET DAY", "SATURDAY 9AM"], str(left))
    for (ltext, lbox, lsize), (rtext, rbox, rsize) in zip(left, right):
        origin = max(abs(lbox[0] - rbox[0]), abs(lbox[1] - rbox[1]), abs(lbox[2] - rbox[2]), abs(lbox[3] - rbox[3]))
        check(f"upscale-place-{ltext}", origin < 1.5, f"{origin:.2f} local {lbox} remote {rbox}")
        check(f"upscale-size-{ltext}", abs(lsize - rsize) / max(lsize, 1) < 0.04, f"local {lsize:.2f} remote {rsize:.2f}")
        check(f"upscale-not-quarter-{ltext}", rsize > lsize * 0.8, f"{rsize:.2f} vs {lsize:.2f}")


def _texture_energy(img: np.ndarray, selected: np.ndarray) -> float:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
    high = gray - cv2.GaussianBlur(gray, (0, 0), 1.6)
    values = high[selected]
    if values.size == 0:
        return 0.0
    return float(np.std(values))


def test_fill_matches_noisy_gradient() -> None:
    """A smooth eraser fill must not leave a flat letter-shaped patch on a noisy gradient."""
    from ai_rebuild import composite_inpaint

    height, width = 240, 300
    rng = np.random.default_rng(4)
    yy = np.linspace(0, 1, height)[:, None]
    xx = np.linspace(0, 1, width)[None, :]
    field = yy * 0.72 + xx * 0.28
    truth = np.zeros((height, width, 3), np.float32)
    truth[..., 0] = 28 + field * 100
    truth[..., 1] = 18 + field * 55
    truth[..., 2] = 78 + (1.0 - field) * 120
    truth += rng.normal(0, 7.5, truth.shape)
    truth = np.clip(truth, 0, 255).astype(np.uint8)
    painted = truth.copy()
    cv2.putText(painted, "MARKET", (24, 92), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (245, 245, 245), 3, cv2.LINE_AA)
    cv2.putText(painted, "DAY", (24, 142), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (245, 245, 245), 3, cv2.LINE_AA)
    mask = np.zeros((height, width), np.uint8)
    mask[55:155, 18:250] = 255
    smooth = cv2.GaussianBlur(painted, (0, 0), 11)
    bad = painted.copy()
    bad[mask > 0] = smooth[mask > 0]
    fixed = composite_inpaint(painted, bad, mask)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    hole = cv2.erode(mask, kernel, iterations=1) > 0
    inside = _texture_energy(fixed, hole)
    reference = _texture_energy(truth, hole)
    bad_energy = _texture_energy(bad, hole)
    rel = abs(inside - reference) / max(reference, 1e-3)
    check("grain-matches-neighbour", rel < 0.45 and inside > bad_energy * 1.4, f"fixed {inside:.2f} truth {reference:.2f} bad {bad_energy:.2f} rel {rel:.2f}")
    colour = float(np.mean(np.abs(fixed[hole].astype(np.float32) - truth[hole].astype(np.float32))))
    bad_colour = float(np.mean(np.abs(bad[hole].astype(np.float32) - truth[hole].astype(np.float32))))
    check("colour-matches-gradient", colour < 14 and colour < bad_colour, f"fixed {colour:.1f} bad {bad_colour:.1f}")


def test_circle_stays_a_circle() -> None:
    """A circle, a badge, and a lone letter stay pixels. Real words are still retyped."""
    from ai_rebuild import DOUBTFUL_REASON

    reset_providers()
    saved = os.environ.pop("REPLICATE_API_TOKEN", None)
    folder = tempfile.mkdtemp()
    try:
        src = os.path.join(folder, "shapes.png")
        rebuilt = os.path.join(folder, "shapes.pdf")
        _draw_shape_flyer(src)
        result = rebuild(src, {"trim_w_mm": 148, "trim_h_mm": 148, "output_pdf": rebuilt, "force": True})
        check("shape-built", result.get("success") is True and os.path.exists(rebuilt), result.get("message", ""))
        texts = [str(block.get("text") or "") for block in result.get("blocks") or []]
        check("shape-words", texts == ["MARKET DAY", "SATURDAY 9AM"], str(texts))
        check("shape-no-letter-block", "O" not in texts and "A" not in texts, str(texts))
        check("shape-doubtful", result.get("doubtful") is True and result.get("doubtfulReason") == DOUBTFUL_REASON, str(result.get("doubtfulReason")))
        import pymupdf as fitz
        doc = fitz.open(rebuilt)
        page = doc[0]
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        rendered = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
        rendered = cv2.cvtColor(rendered[:, :, :3], cv2.COLOR_RGB2BGR)
        vector = page.get_text("text") or ""
        doc.close()
        check("shape-vector-market", "MARKET DAY" in vector and "SATURDAY 9AM" in vector, vector.replace("\n", " | "))
        lines = [line.strip() for line in vector.splitlines() if line.strip()]
        check("shape-vector-no-o", "O" not in lines and "A" not in lines, str(lines))
        disc = _orange_disc(rendered)
        check("shape-circle-found", disc is not None, "" if disc else "no orange disc")
        if disc is not None:
            area, aspect, fill = disc
            check("shape-circle-round", 0.85 <= aspect <= 1.18 and fill >= 0.68 and area > 8000, f"area {area} aspect {aspect:.2f} fill {fill:.2f}")
        source = cv2.imread(src, cv2.IMREAD_COLOR)
        # Circle, badge, and the lone letter. Words sit lower and are allowed to be retyped.
        regions = {
            "circle": (0.16, 0.02, 0.62, 0.48),
            "badge": (0.76, 0.55, 0.96, 0.74),
            "letter": (0.77, 0.21, 0.90, 0.36),
        }
        for name, box in regions.items():
            delta = _region_delta(source, rendered, box)
            check(f"shape-pixels-{name}", delta < 22, f"{delta:.1f}")
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
    test_inpaint_removes_the_letters()
    test_doubtful_marks_are_not_retyped()
    test_contacts_and_dashes_are_words()
    test_upscaled_text_matches_local_placement()
    test_fill_matches_noisy_gradient()
    test_circle_stays_a_circle()
    test_press_pdf_shows_the_words()
    print("AI rebuild checks passed")
