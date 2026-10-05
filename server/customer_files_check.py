#!/usr/bin/env python3
"""Customer-file press checks: every product, bleed kind, colour, and the six jobs."""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time

import cv2
import numpy as np

MM = 72.0 / 25.4
STYLE_IDS = (
    "bgExtract",
    "stretch",
    "mirror",
    "replicate",
    "gradient_extrapolate",
    "frequency_separated",
    "upscale",
    "ai_outpaint",
    "colourBorder",
)
ROWS = []
FAILURES = []


def record(name: str, ok: bool, **fields) -> None:
    fields["name"] = name
    fields["ok"] = "pass" if ok else "FAIL"
    ROWS.append(fields)
    mark = "ok " if ok else "FAIL"
    print(
        f"{mark} {name} | {fields.get('product', '')} | p{fields.get('pages', '')} | "
        f"{fields.get('size', '')} | {fields.get('light', '')} | {fields.get('reasons', '')} | {fields.get('note', '')}",
        flush=True,
    )
    if not ok:
        FAILURES.append(name)


def _products() -> list:
    from quick_print import _products as load

    return load()


def _decode_qr(bgr: np.ndarray) -> str:
    detector = cv2.QRCodeDetector()
    data, _points, _ = detector.detectAndDecode(bgr)
    if data:
        return str(data)
    try:
        from pyzbar.pyzbar import ZBarSymbol
        from pyzbar.pyzbar import decode as pyzbar_decode
        from PIL import Image

        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        hits = pyzbar_decode(Image.fromarray(rgb), symbols=[ZBarSymbol.QRCODE])
        if hits:
            return hits[0].data.decode("utf-8", errors="replace")
    except Exception:
        pass
    return ""


def _qr_image(payload: str, module: int = 8) -> np.ndarray:
    encoder = cv2.QRCodeEncoder_create()
    qr = encoder.encode(payload)
    if qr is None or qr.size == 0:
        raise RuntimeError("QR encoder returned nothing")
    sharp = np.where(qr < 128, 0, 255).astype(np.uint8)
    big = cv2.resize(sharp, None, fx=module, fy=module, interpolation=cv2.INTER_NEAREST)
    quiet = 4 * module
    canvas = np.full((big.shape[0] + quiet * 2, big.shape[1] + quiet * 2), 255, np.uint8)
    canvas[quiet:quiet + big.shape[0], quiet:quiet + big.shape[1]] = big
    return cv2.cvtColor(canvas, cv2.COLOR_GRAY2BGR)


_DEJAVU = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def _embed_text(page, point, text: str, size: float, color) -> None:
    page.insert_font(fontname="DEJAVU", fontfile=_DEJAVU)
    page.insert_text(point, text, fontsize=size, fontname="DEJAVU", color=color)


def _write_pdf(path: str, spec: dict) -> None:
    import pymupdf as fitz
    from PIL import Image

    trim_w = float(spec["trim_w"])
    trim_h = float(spec["trim_h"])
    bleed = float(spec.get("bleed_mm") or 0)
    pages = int(spec.get("pages") or 1)
    page_w = (trim_w + 2 * bleed) * MM
    page_h = (trim_h + 2 * bleed) * MM
    inset = bleed * MM
    doc = fitz.open()
    folder = os.path.dirname(path)
    qr_path = ""
    if spec.get("qr"):
        qr_path = os.path.join(folder, "qr.png")
        cv2.imwrite(qr_path, _qr_image("https://flyerz.co.za/pay"))
    cmyk_path = ""
    if spec.get("cmyk"):
        cmyk_path = os.path.join(folder, "plate.tif")
        plate_px = (
            max(8, int(round(page_w / 72.0 * 300))),
            max(8, int(round(page_h / 72.0 * 300))),
        )
        Image.new("CMYK", plate_px, (0, 40, 80, 0)).save(cmyk_path, format="TIFF")
    mask_bytes = b""
    swatch = ""
    if spec.get("mask"):
        swatch = os.path.join(folder, "swatch.tif")
        shade = os.path.join(folder, "shade.png")
        Image.new("CMYK", (24, 24), (0, 90, 90, 0)).save(swatch, format="TIFF")
        Image.new("L", (24, 24), 170).save(shade, format="PNG")
        with open(shade, "rb") as handle:
            mask_bytes = handle.read()
    for index in range(pages):
        page = doc.new_page(width=page_w, height=page_h)
        if cmyk_path:
            page.insert_image(page.rect, filename=cmyk_path)
        else:
            # Each page has its own colour, so a later-page preview cannot be a copy of page 1.
            left = ((0.25 + 0.22 * index) % 1.0, 0.12 + 0.08 * (index % 3), 0.15)
            right = (0.1, (0.25 + 0.18 * index) % 1.0, 0.55)
            page.draw_rect(page.rect, color=None, fill=left)
            page.draw_rect(fitz.Rect(page_w / 2.0, 0, page_w, page_h), color=None, fill=right)
        label = spec.get("text") or "SAFE"
        if pages > 1:
            label = f"{label}{index + 1}"
        if spec.get("near") and index == 0:
            baseline = page_h - inset - (0.6 * MM)
            _embed_text(page, (inset + 18, baseline), "LOCATION:", 28, (0, 0, 0))
        else:
            _embed_text(page, (inset + 18, inset + 36), label, 18, (0, 0, 0))
        if qr_path and index == 0:
            side = min(page_w, page_h) * 0.42
            origin_x = (page_w - side) / 2.0
            origin_y = (page_h - side) / 2.0
            page.insert_image(fitz.Rect(origin_x, origin_y, origin_x + side, origin_y + side), filename=qr_path)
        if mask_bytes and swatch and index == 0:
            page.insert_image(
                fitz.Rect(inset + 20, inset + 70, inset + 60, inset + 110),
                filename=swatch,
                mask=mask_bytes,
            )
        rect = page.rect
        page.set_mediabox(rect)
        page.set_cropbox(rect)
        if spec.get("boxes") == "trim" and bleed > 0:
            trim = fitz.Rect(inset, inset, page_w - inset, page_h - inset)
            page.set_trimbox(trim)
            page.set_bleedbox(rect)
        else:
            page.set_trimbox(rect)
            page.set_bleedbox(rect)
    doc.save(path)
    doc.close()


def _press_facts(path: str) -> dict:
    import pymupdf as fitz

    doc = fitz.open(path)
    try:
        media = doc[0].mediabox
        texts = []
        qr = ""
        for page in doc:
            texts.append(page.get_text("text") or "")
            if not qr:
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False, colorspace=fitz.csRGB)
                rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, 3)
                qr = _decode_qr(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
        return {
            "pages": doc.page_count,
            "width": media.width * 25.4 / 72.0,
            "height": media.height * 25.4 / 72.0,
            "text": "\n".join(texts),
            "qr": qr,
        }
    finally:
        doc.close()


def _run_print(src: str, folder: str, trim_w: float, trim_h: float, product_id: str, label: str, detect: bool) -> dict:
    from quick_print import make_print_ready

    return make_print_ready(
        src,
        os.path.join(folder, "out"),
        trim_w,
        trim_h,
        product_id,
        label,
        filename=os.path.basename(src),
        detect_size=detect,
    )


def _expect_press(name: str, result: dict, spec: dict) -> None:
    reasons = [str(item) for item in (result.get("reasons") or [])]
    joined = " ".join(reasons)
    light = str(result.get("light") or "")
    product = str(result.get("productId") or "")
    press = result.get("pressPath") or ""
    facts = {"pages": 0, "width": 0.0, "height": 0.0, "text": "", "qr": ""}
    problems = []
    if spec.get("fixable", True) and light == "red" and not spec.get("checklistRed"):
        problems.append(f"red:{joined}")
    if spec.get("light") and light != spec["light"]:
        problems.append(f"light {light}")
    if spec.get("product") and product != spec["product"]:
        problems.append(f"product {product}")
    if spec.get("reason_has") and spec["reason_has"].lower() not in joined.lower():
        problems.append(f"missing reason {spec['reason_has']}")
    for banned in spec.get("reason_lacks") or []:
        if banned.lower() in joined.lower():
            problems.append(f"bad reason {banned}")
    if spec.get("fixable", True):
        if not press or not os.path.exists(press):
            problems.append("no press file")
        else:
            facts = _press_facts(press)
            if facts["pages"] != int(spec["pages"]):
                problems.append(f"pages {facts['pages']}")
            want_w = float(spec["trim_w"]) + 10.0
            want_h = float(spec["trim_h"]) + 10.0
            if abs(facts["width"] - want_w) > 1.5 or abs(facts["height"] - want_h) > 1.5:
                problems.append(f"size {facts['width']:.1f}x{facts['height']:.1f}")
            for word in spec.get("live") or []:
                if word not in facts["text"]:
                    problems.append(f"missing live {word}")
            if spec.get("qr") and "flyerz.co.za/pay" not in facts["qr"]:
                problems.append(f"qr {facts['qr']!r}")
    elif spec.get("light") == "red" and light != "red":
        problems.append(f"expected red, got {light}")
    size = f"{facts['width']:.1f}×{facts['height']:.1f}" if facts["pages"] else ""
    record(
        name,
        not problems,
        product=product,
        pages=facts["pages"] or "",
        size=size,
        light=light,
        reasons=joined[:180],
        live="kept" if spec.get("live") and not any(p.startswith("missing live") for p in problems) else "",
        qr=facts["qr"][:40],
        note="; ".join(problems),
    )


def _case_pdf(name: str, spec: dict) -> None:
    folder = tempfile.mkdtemp(prefix=f"cust-{name}-")
    src = os.path.join(folder, f"{name}.pdf")
    _write_pdf(src, spec)
    started = time.perf_counter()
    result = _run_print(src, folder, 148, 210, "a5", "A5", True)
    result["_seconds"] = round(time.perf_counter() - started, 2)
    _expect_press(name, result, spec)


def test_every_product() -> None:
    for product in _products():
        _case_pdf(f"size-{product['id']}", {
            "trim_w": product["widthMm"],
            "trim_h": product["heightMm"],
            "pages": 1,
            "bleed_mm": 0,
            "boxes": "equal",
            "text": "SAFE",
            "product": product["id"],
            "pages_expect": 1,
            "live": ["SAFE"],
            "fixable": True,
            "reason_lacks": ["cannot be extended", "cut line", "not CMYK", "Traceback"],
        })


def test_bleed_colour_pages() -> None:
    a5 = {"trim_w": 148, "trim_h": 210, "product": "a5", "fixable": True, "reason_lacks": ["cannot be extended", "not CMYK", "Traceback"]}
    _case_pdf("pages-2", {**a5, "pages": 2, "text": "SIDE", "live": ["SIDE1", "SIDE2"]})
    _case_pdf("pages-3", {**a5, "pages": 3, "text": "SIDE", "live": ["SIDE1", "SIDE2", "SIDE3"]})
    _case_pdf("no-bleed", {**a5, "pages": 1, "bleed_mm": 0, "text": "SAFE", "live": ["SAFE"], "light": "green"})
    _case_pdf("canva-2-5mm", {
        **a5, "pages": 2, "bleed_mm": 2.5, "boxes": "equal", "text": "CANVA", "live": ["CANVA1", "CANVA2"],
        "reason_lacks": ["cannot be extended", "cut line", "not CMYK", "Traceback"],
    })
    _case_pdf("trimbox-3mm", {**a5, "pages": 1, "bleed_mm": 3, "boxes": "trim", "text": "TRIM", "live": ["TRIM"]})
    _case_pdf("trimbox-8mm", {**a5, "pages": 1, "bleed_mm": 8, "boxes": "trim", "text": "WIDE", "live": ["WIDE"]})
    _case_pdf("equal-5mm", {**a5, "pages": 1, "bleed_mm": 5, "boxes": "equal", "text": "FIVE", "live": ["FIVE"]})
    _case_pdf("equal-8mm", {**a5, "pages": 1, "bleed_mm": 8, "boxes": "equal", "text": "EIGHT", "live": ["EIGHT"]})
    _case_pdf("cmyk-mask", {**a5, "pages": 1, "cmyk": True, "mask": True, "text": "MASK", "live": ["MASK"]})
    _case_pdf("rgb", {**a5, "pages": 1, "text": "RGB", "live": ["RGB"]})
    _case_pdf("text-near", {
        **a5, "pages": 2, "near": True, "text": "INNER", "live": ["LOCATION:", "INNER2"],
        "light": "amber", "reason_has": "within 3 mm",
        "reason_lacks": ["cut line", "cannot be extended", "Traceback"],
    })
    _case_pdf("text-safe", {**a5, "pages": 1, "text": "INSIDE", "live": ["INSIDE"], "light": "green"})
    _case_pdf("qr-pdf", {**a5, "pages": 1, "qr": True, "text": "PAY", "live": ["PAY"]})


def _image(path: str, width: int, height: int) -> None:
    img = np.zeros((height, width, 3), np.uint8)
    img[:, :] = (30, 90, 200)
    img[:, width // 2:] = (200, 80, 40)
    cv2.putText(img, "FLYERZ", (24, max(32, height // 2)), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 2, cv2.LINE_AA)
    cv2.imwrite(path, img)


def test_images() -> None:
    folder = tempfile.mkdtemp(prefix="cust-img-")
    good_w = int(round(90 / 25.4 * 300))
    good_h = int(round(50 / 25.4 * 300))
    for kind in ("png", "jpg"):
        path = os.path.join(folder, f"good.{kind}")
        _image(path, good_w, good_h)
        result = _run_print(path, os.path.join(folder, kind), 90, 50, "card-90x50", "Business card 90 × 50", False)
        _expect_press(f"image-good-{kind}", result, {
            "trim_w": 90, "trim_h": 50, "pages": 1, "product": "card-90x50", "fixable": True,
            "reason_lacks": ["cannot be extended", "Traceback", "could not be read"],
        })
        low = os.path.join(folder, f"low.{kind}")
        _image(low, 80, 40)
        low_result = _run_print(low, os.path.join(folder, f"low-{kind}"), 90, 50, "card-90x50", "Business card 90 × 50", False)
        _expect_press(f"image-low-{kind}", low_result, {
            "trim_w": 90, "trim_h": 50, "pages": 1, "fixable": False, "light": "red",
            "reason_has": "too small",
        })


def _bled_pdf(path: str) -> None:
    import pymupdf as fitz

    bleed = 5 * MM
    tw, th = 148 * MM, 210 * MM
    doc = fitz.open()
    page = doc.new_page(width=tw + 2 * bleed, height=th + 2 * bleed)
    page.draw_rect(page.rect, color=None, fill=(0.75, 0.08, 0.08))
    page.draw_rect(fitz.Rect(bleed, bleed, bleed + tw, bleed + th), color=None, fill=(0.1, 0.55, 0.2))
    _embed_text(page, (bleed + 36, bleed + 80), "ALREADY BLEED", 28, (1, 1, 1))
    trim = fitz.Rect(bleed, bleed, bleed + tw, bleed + th)
    page.set_trimbox(trim)
    page.set_bleedbox(page.rect)
    page.set_cropbox(page.rect)
    doc.save(path)
    doc.close()


def test_plate_facts_skip_reencode() -> None:
    """Reading the plate size must not turn the CMYK JPEG into another JPEG."""
    import io

    import pymupdf as fitz
    from PIL import Image

    from green_gate import _press_matrix
    from vector_trace import _inspect_plate

    buf = io.BytesIO()
    Image.new("CMYK", (400, 400), (20, 40, 60, 10)).save(buf, format="JPEG", quality=90)
    doc = fitz.open()
    page = doc.new_page(width=72, height=72)
    page.insert_image(page.rect, stream=buf.getvalue())
    path = os.path.join(tempfile.mkdtemp(prefix="facts-"), "plate.pdf")
    doc.save(path)
    doc.close()
    calls = []
    original = fitz.Document.extract_image

    def boom(self, xref):
        calls.append(int(xref))
        raise RuntimeError("the picture was encoded again")

    fitz.Document.extract_image = boom
    try:
        qa = {}
        _inspect_plate(path, 72 * 25.4 / 72.0 - 10, 72 * 25.4 / 72.0 - 10, 5, qa)
        opened = fitz.open(path)
        try:
            matrix = _press_matrix(opened[0])
        finally:
            opened.close()
    finally:
        fitz.Document.extract_image = original
    ok = not calls and qa.get("cmyk") is True and abs(matrix.a - 400 / 72) < 0.01
    record(
        "plate-facts",
        ok,
        product="n/a",
        pages=1,
        size="400px",
        light="n/a",
        reasons="",
        note=f"cmyk {qa.get('cmyk')} scale {matrix.a:.3f} reencode {len(calls)}",
    )


def test_six_jobs() -> None:
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures"))
    jobs = [
        ("reg-card-front", os.path.join(root, "medella", "card_front.png"), 90, 50, "card-90x50", "Business card 90 × 50", None),
        ("reg-card-back", os.path.join(root, "medella", "card_back.png"), 90, 50, "card-90x50", "Business card 90 × 50", None),
        ("reg-flyer-front", os.path.join(root, "medella", "flyer_front.png"), 148, 210, "a5", "A5", None),
        ("reg-flyer-back", os.path.join(root, "medella", "flyer_back.png"), 148, 210, "a5", "A5", None),
        ("reg-poster", os.path.join(root, "catch_fire", "src.jpg"), 148, 210, "a5", "A5", None),
    ]
    for name, src, tw, th, pid, label, _live in jobs:
        folder = tempfile.mkdtemp(prefix=f"{name}-")
        result = _run_print(src, folder, tw, th, pid, label, False)
        spec = {
            "trim_w": tw, "trim_h": th, "pages": 1, "product": pid, "fixable": True,
            "reason_lacks": ["cannot be extended", "could not be read", "Traceback"],
        }
        # A square poster on A5 is more than 12% off. E1 blocks the light. The press file is still built.
        if name == "reg-poster":
            spec["checklistRed"] = True
        _expect_press(name, result, spec)
    folder = tempfile.mkdtemp(prefix="reg-bleed-")
    src = os.path.join(folder, "already.pdf")
    _bled_pdf(src)
    result = _run_print(src, folder, 148, 210, "a5", "A5", True)
    _expect_press("reg-existing-bleed", result, {
        "trim_w": 148, "trim_h": 210, "pages": 1, "product": "a5", "fixable": True,
        "live": ["ALREADY BLEED"], "light": "green",
        "reason_lacks": ["cannot be extended", "cut line", "Traceback"],
    })


def _anton_font() -> str:
    path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures", "fonts", "Anton-Regular.ttf"))
    if not os.path.isfile(path):
        raise RuntimeError("Anton is missing from tests/fixtures/fonts")
    return path


def _write_canva(path: str) -> None:
    """Two-page Canva-shaped A6: equal boxes, CMYK JPEG, soft mask, Anton, QR."""
    import pymupdf as fitz
    from PIL import Image

    page_w, page_h = 433.5, 312.0
    font = fitz.Font(fontfile=_anton_font())
    doc = fitz.open()
    folder = os.path.dirname(path)
    # Page 1 is flat navy. Page 2 is grey meeting a navy curve, with a thin light edge line.
    inks = ((210, 160, 40, 80), (30, 24, 24, 70))
    for index, ink in enumerate(inks):
        pixels_w = int(round(page_w / 72.0 * 150))
        pixels_h = int(round(page_h / 72.0 * 150))
        plate = Image.new("CMYK", (pixels_w, pixels_h), ink)
        if index == 1:
            curved = np.array(plate)
            # 4 mm and 5 mm grey columns cross the top and bottom edges.
            column_px = max(1, int(round(150.0 / 25.4)))
            for origin, width_mm, tone in ((0.28, 4.0, (18, 14, 14, 40)), (0.62, 5.5, (55, 42, 40, 20))):
                x0 = int(pixels_w * origin)
                width = max(3, int(round(width_mm * column_px)))
                curved[:, x0:x0 + width] = tone
            # Navy curve meets the grey at the left edge, just above the corner.
            cv2.ellipse(
                curved,
                (0, int(pixels_h * 0.78)),
                (int(pixels_w * 0.22), int(pixels_h * 0.16)),
                0, 270, 450,
                (210, 160, 40, 90),
                -1,
            )
            # 8x8 halftone, then JPEG quality 80, so the source has real block noise.
            dots = (curved.shape[0] // 8) * 8
            dot_rows = np.arange(4, dots, 8)
            dot_cols = np.arange(4, (curved.shape[1] // 8) * 8, 8)
            curved[np.ix_(dot_rows, dot_cols)] = (200, 150, 30, 80)
            # Dense block grain just inside the trim. The outer rim stays even so the seam can match.
            rim = 16
            block = np.random.default_rng(11).integers(-36, 37, size=(8, 8, 4), dtype=np.int16)
            grain = np.tile(block, (curved.shape[0] // 8 + 1, curved.shape[1] // 8 + 1, 1))
            grain = grain[: curved.shape[0], : curved.shape[1]]
            lifted = curved.astype(np.int16)
            lifted[rim:-rim, rim:-rim] += grain[rim:-rim, rim:-rim]
            curved = np.clip(lifted, 0, 255).astype(np.uint8)
            plate = Image.fromarray(curved, mode="CMYK")
        jpeg = os.path.join(folder, f"navy-{index}.jpg")
        plate.save(jpeg, format="JPEG", quality=80 if index == 1 else 92)
        accent = Image.new("CMYK", (600, 400), (0, 190, 210, 0))
        accent_path = os.path.join(folder, f"accent-{index}.tif")
        accent.save(accent_path, format="TIFF")
        mask_px = np.zeros((400, 600), np.uint8)
        cv2.ellipse(mask_px, (300, 200), (220, 140), 0, 0, 360, 255, -1)
        mask_px = cv2.GaussianBlur(mask_px, (21, 21), 0)
        mask_path = os.path.join(folder, f"mask-{index}.png")
        Image.fromarray(mask_px, mode="L").save(mask_path)
        with open(mask_path, "rb") as handle:
            mask_bytes = handle.read()
        page = doc.new_page(width=page_w, height=page_h)
        page.insert_image(page.rect, filename=jpeg)
        page.insert_image(fitz.Rect(90, 80, 230, 170), filename=accent_path, mask=mask_bytes)
        qr_path = os.path.join(folder, f"qr-{index}.png")
        cv2.imwrite(qr_path, _qr_image("https://flyerz.co.za/pay", module=12))
        page.insert_image(fitz.Rect(300, 36, 370, 106), filename=qr_path)
        writer = fitz.TextWriter(page.rect)
        writer.append((48, 230), f"ANTON {index + 1}", font=font, fontsize=32)
        writer.write_text(page, color=(1, 1, 1))
        if index == 1:
            fringe = 0.45
            light = (0.94, 0.94, 0.94)
            page.draw_rect(fitz.Rect(0, 0, page_w, fringe), fill=light, color=None)
            page.draw_rect(fitz.Rect(0, page_h - fringe, page_w, page_h), fill=light, color=None)
            page.draw_rect(fitz.Rect(0, 0, fringe, page_h), fill=light, color=None)
            page.draw_rect(fitz.Rect(page_w - fringe, 0, page_w, page_h), fill=light, color=None)
        rect = page.rect
        page.set_mediabox(rect)
        page.set_cropbox(rect)
        page.set_trimbox(rect)
        page.set_bleedbox(rect)
        page.set_artbox(rect)
    doc.save(path)
    doc.close()


def _canva_source_ok(path: str) -> str:
    import pymupdf as fitz

    doc = fitz.open(path)
    try:
        if doc.page_count != 2:
            return f"pages {doc.page_count}"
        page = doc[0]
        if abs(page.rect.width - 433.5) > 0.2 or abs(page.rect.height - 312) > 0.2:
            return f"size {page.rect.width:.1f}x{page.rect.height:.1f}"
        boxes = (page.mediabox, page.cropbox, page.trimbox, page.bleedbox, page.artbox)
        if any(abs(box.width - page.rect.width) > 0.4 or abs(box.height - page.rect.height) > 0.4 for box in boxes):
            return "boxes differ"
        fonts = page.get_fonts() or []
        cid = any("CID" in str(item) or "Identity" in str(item) for item in fonts)
        if not cid:
            return f"font {fonts}"
        images = page.get_images(full=True) or []
        if not any(len(item) > 1 and int(item[1] or 0) > 0 for item in images):
            return "no soft mask"
        return ""
    finally:
        doc.close()


def _corner_near_white(path: str, seam_x_pt: float, seam_y_pt: float) -> list:
    """Near-white pixels left in a corner square are a gap or an uncovered light line."""
    import pymupdf as fitz

    doc = fitz.open(path)
    counts = []
    try:
        scale = 300.0 / 72.0
        for index, page in enumerate(doc):
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False, colorspace=fitz.csRGB)
            rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, 3)
            height, width = rgb.shape[:2]
            sx = min(max(int(round(seam_x_pt * scale)), 2), width // 2)
            sy = min(max(int(round(seam_y_pt * scale)), 2), height // 2)
            boxes = {
                "tl": rgb[:sy, :sx],
                "tr": rgb[:sy, width - sx:],
                "bl": rgb[height - sy:, :sx],
                "br": rgb[height - sy:, width - sx:],
            }
            for name, crop in boxes.items():
                white = int(np.count_nonzero(np.all(crop >= 245, axis=2)))
                counts.append(f"p{index + 1}{name}:{white}")
        return counts
    finally:
        doc.close()


def _window_note(seams: list) -> tuple[float, str]:
    worst = 0.0
    parts = []
    if not seams:
        return 99.0, "no seam report"
    for row in seams:
        page_max = float(row.get("page_max") if row.get("page_max") is not None else 99)
        worst = max(worst, page_max)
        edges = row.get("edges") or {}
        corners = row.get("corners") or {}
        parts.append(
            "p{page} max {mx} L{left} R{right} T{top} B{bottom}".format(
                page=row.get("page"),
                mx=page_max,
                left=(edges.get("L") or {}).get("strip_vs_inner_max"),
                right=(edges.get("R") or {}).get("strip_vs_inner_max"),
                top=(edges.get("T") or {}).get("strip_vs_inner_max"),
                bottom=(edges.get("B") or {}).get("strip_vs_inner_max"),
            )
        )
        if corners:
            parts[-1] += " corners " + " ".join(
                f"{name}:{max(float(item.get('vs_side_strip') or 0), float(item.get('vs_vert_strip') or 0)):.2f}"
                for name, item in corners.items()
            )
    return worst, " ".join(parts)


def _one_cmyk_plate(path: str) -> str:
    import pymupdf as fitz

    doc = fitz.open(path)
    try:
        notes = []
        for index, page in enumerate(doc):
            images = page.get_images(full=True) or []
            raw = page.read_contents().decode("latin1", "replace")
            rgb = "rgb" if re.search(r"\b(?:rg|RG)\b|DeviceRGB|CalRGB", raw) else ""
            spaces = []
            for item in images:
                info = doc.extract_image(int(item[0]))
                spaces.append(str(info.get("colorspace")))
            if len(images) != 1 or any(space == "3" for space in spaces) or rgb:
                notes.append(f"p{index + 1} images {len(images)} cs {spaces} {rgb}")
        return "; ".join(notes)
    finally:
        doc.close()


def test_bleed_matches_edge() -> None:
    """A partial bleed is one mirrored CMYK plate. The added strip matches the inner band."""
    import pymupdf as fitz
    from PIL import Image
    from press_ready_engine import compile_vector_press, press_window_seam

    folder = tempfile.mkdtemp(prefix="cust-bleed-match-")
    width, height = 1806, 1300
    image = np.full((height, width, 3), (183, 184, 185), np.uint8)
    # A unique centre locks the seam measurement. The outer band stays even, which is what a mirror repeats.
    image[420:980, 500:1300] = (24, 64, 150)
    for y in range(980, height - 80):
        boundary = 80 + (y - 980) * 2
        if boundary < width - 80:
            image[y, 80:boundary] = (20, 40, 80)
    png = os.path.join(folder, "plate.png")
    Image.fromarray(image, mode="RGB").save(png)
    src = os.path.join(folder, "plate.pdf")
    doc = fitz.open()
    page = doc.new_page(width=433.5, height=312)
    page.insert_image(page.rect, filename=png)
    page.set_mediabox(page.rect)
    page.set_cropbox(page.rect)
    page.set_trimbox(page.rect)
    page.set_bleedbox(page.rect)
    doc.save(src)
    doc.close()
    out = os.path.join(folder, "press.pdf")
    result = compile_vector_press(src, out, 148, 105, 5)
    seams = press_window_seam(src, out) if os.path.exists(out) else []
    worst, note = _window_note(seams)
    plate = _one_cmyk_plate(out) if os.path.exists(out) else "missing"
    record(
        "bleed-matches-edge",
        bool(result.get("used")) and worst < 5.0 and not plate,
        product="a6-landscape",
        pages=len(seams),
        size=f"dE {worst:.2f}",
        light="n/a",
        reasons=note,
        live="",
        qr="",
        note=plate,
    )


def test_bleed_diagonal_and_column() -> None:
    """A diagonal and a column are mirrored into the shortfall. The seam window stays under 5."""
    import pymupdf as fitz
    from PIL import Image
    from press_ready_engine import compile_vector_press, press_window_seam

    folder = tempfile.mkdtemp(prefix="cust-bleed-slope-")
    width, height = 1806, 1300
    image = np.full((height, width, 3), (183, 184, 185), np.uint8)
    image[-1, :] = (250, 250, 250)
    for y in range(height - 180, height - 1):
        boundary = int(16 + (height - 2 - y) * 1.2)
        if 0 <= boundary < width - 20:
            image[y, boundary:boundary + 260] = (8, 30, 70)
    image[1000:height - 1, 1100:] = (8, 30, 70)
    png = os.path.join(folder, "plate.png")
    Image.fromarray(image, mode="RGB").save(png)
    src = os.path.join(folder, "plate.pdf")
    doc = fitz.open()
    page = doc.new_page(width=433.5, height=312)
    page.insert_image(page.rect, filename=png)
    page.set_mediabox(page.rect)
    page.set_cropbox(page.rect)
    page.set_trimbox(page.rect)
    page.set_bleedbox(page.rect)
    doc.save(src)
    doc.close()
    out = os.path.join(folder, "press.pdf")
    result = compile_vector_press(src, out, 148, 105, 5)
    seams = press_window_seam(src, out) if os.path.exists(out) else []
    worst, note = _window_note(seams)
    plate = _one_cmyk_plate(out) if os.path.exists(out) else "missing"
    problems = []
    if not result.get("used"):
        problems.append("not used")
    if worst >= 5.0:
        problems.append(f"seam {worst:.2f}")
    if plate:
        problems.append(plate)
    record(
        "bleed-diagonal-column",
        not problems,
        product="a6-landscape",
        pages=len(seams),
        size=f"dE {worst:.2f}",
        light="n/a",
        reasons=note if not problems else "; ".join(problems),
        live="",
        qr="",
        note=note,
    )


def test_real_a6_file() -> None:
    """Ian's real A6, kept in /tmp and never committed. seam.py on the press PDF must be under 5."""
    from press_ready_engine import press_window_seam

    src = "/tmp/real_a6.pdf"
    if not os.path.isfile(src) or os.path.getsize(src) < 1000:
        record(
            "real-a6-seam",
            True,
            product="a6-landscape",
            pages=0,
            size="skip",
            light="n/a",
            reasons="file not in /tmp",
            live="",
            qr="",
            note="skipped",
        )
        return
    folder = tempfile.mkdtemp(prefix="cust-real-a6-")
    result = _run_print(src, folder, 148, 210, "a5", "A5", True)
    press = result.get("pressPath") or ""
    seams = press_window_seam(src, press) if press and os.path.exists(press) else []
    worst, parts_text = _window_note(seams)
    parts = [parts_text]
    problems = []
    if not seams or len(seams) < 2:
        worst = 99.0
        problems.append("no seam report")
    if worst >= 5.0:
        problems.append(f"seam dE {worst}")
    card = [str(item) for item in (result.get("reasons") or [])]
    if any("does not match" in item or "35 ppi" in item or "no longer K-only" in item or "Under 75" in item for item in card):
        problems.append("false amber")
    if not any("LOCATION" in item for item in card):
        problems.append("missing location")
    if any("LOCATION" not in item for item in card):
        problems.append("extra reason")
    if press and os.path.exists(press):
        import pymupdf as fitz

        sized = fitz.open(press)
        try:
            rect = sized[0].rect
            width_mm = rect.width * 25.4 / 72.0
            height_mm = rect.height * 25.4 / 72.0
            if abs(width_mm - 158.0) > 1.5 or abs(height_mm - 115.0) > 1.5 or sized.page_count != 2:
                problems.append(f"size {sized.page_count}p {width_mm:.1f}x{height_mm:.1f}")
        finally:
            sized.close()
    if not result.get("existingBleedKept"):
        problems.append("existing bleed not kept")
    from designer_assistant import reply_from_checks

    cat_rows = list(result.get("prepressChecks") or []) + list(result.get("extraChecks") or [])
    for item in result.get("checklist") or []:
        if item.get("passed"):
            continue
        detail = str(item.get("detail") or item.get("label") or "")
        if detail:
            cat_rows.append({"num": "", "name": str(item.get("label") or "Press check"), "status": "warning", "detail": detail})
    reply, _actions = reply_from_checks(cat_rows)
    print("A6-REASONS " + " | ".join(card))
    print("A6-REPLY " + reply)
    record(
        "real-a6-seam",
        not problems and worst < 5.0,
        product=str(result.get("productId") or ""),
        pages=len(seams),
        size=f"dE {worst:.2f}",
        light=str(result.get("light") or ""),
        reasons=" ".join(parts),
        live="",
        qr="",
        note="; ".join(problems + card),
    )


def test_canva_a6() -> None:
    from press_ready_engine import press_window_seam

    folder = tempfile.mkdtemp(prefix="cust-canva-")
    src = os.path.join(folder, "canva-a6.pdf")
    _write_canva(src)
    built = _canva_source_ok(src)
    page_w, page_h = 433.5 * 25.4 / 72.0, 312.0 * 25.4 / 72.0
    seam_x = (5.0 - (page_w - 148.0) / 2.0) * 72.0 / 25.4
    seam_y = (5.0 - (page_h - 105.0) / 2.0) * 72.0 / 25.4
    started = time.perf_counter()
    result = _run_print(src, folder, 148, 210, "a5", "A5", True)
    result["_seconds"] = round(time.perf_counter() - started, 2)
    press = result.get("pressPath") or ""
    seams = press_window_seam(src, press) if press and os.path.exists(press) else []
    worst, seam_note = _window_note(seams)
    if not seams or len(seams) < 2:
        worst = 99.0
        problems_seed = ["no seam report"]
    else:
        problems_seed = []
    reasons = " ".join(
        str(item) for item in list(result.get("decisions") or []) + list(result.get("reasons") or [])
    )
    if result.get("clientMessage"):
        reasons = f"{reasons} {result.get('clientMessage')}"
    problems = list(problems_seed)
    if built:
        problems.append(built)
    if result.get("light") == "red":
        problems.append("red")
    if result.get("productId") != "a6-landscape":
        problems.append(f"product {result.get('productId')}")
    if not result.get("existingBleedKept"):
        problems.append("existingBleedKept false")
    if "bleed" not in reasons.lower() or "kept" not in reasons.lower():
        problems.append("message does not say the bleed was kept")
    if "already had 5 mm" in reasons.lower():
        problems.append("claimed a full 5 mm")
    if worst >= 5.0:
        problems.append(f"local seam dE {worst}")
    white = _corner_near_white(press, seam_x, seam_y) if press and os.path.exists(press) else ["unread"]
    if any(int(item.rsplit(":", 1)[-1] or 99) > 4 for item in white):
        problems.append("near-white " + " ".join(white))
    _expect_press("canva-a6-auto", result, {
        "trim_w": 148, "trim_h": 105, "pages": 2, "product": "a6-landscape", "fixable": True,
        "live": ["ANTON 1", "ANTON 2"], "qr": True,
        "reason_lacks": ["cannot be extended", "cut line", "not CMYK", "Traceback", "Could not read"],
    })
    record(
        "canva-a6-seam",
        not problems and worst < 5.0,
        product=str(result.get("productId") or ""),
        pages=2,
        size=f"dE {worst:.2f}",
        light=str(result.get("light") or ""),
        reasons=seam_note,
        live="kept" if result.get("existingBleedKept") else "",
        qr="",
        note="; ".join(problems),
    )


def test_gs_and_colour_border() -> None:
    """Informational Ghostscript stderr is not a failure, and both preview pages write a PNG."""
    from gs_binary import ghostscript_succeeded
    from colour_border import render_colour_border_preview

    folder = tempfile.mkdtemp(prefix="cust-gs-")
    good = os.path.join(folder, "out.pdf")
    with open(good, "wb") as handle:
        handle.write(b"%PDF-1.4 ok")
    missing = os.path.join(folder, "missing.pdf")
    checks = [
        ghostscript_succeeded(0, good, min_bytes=8),
        ghostscript_succeeded(1, good, min_bytes=8),
        not ghostscript_succeeded(0, missing, min_bytes=8),
        not ghostscript_succeeded(-9, good, min_bytes=8),
    ]
    src = os.path.join(folder, "canva.pdf")
    _write_canva(src)
    bare = os.path.join(folder, "upload-no-ext")
    with open(src, "rb") as handle, open(bare, "wb") as out:
        out.write(handle.read())
    pages = []
    for page in (1, 2):
        dest = os.path.join(folder, f"border-{page}.png")
        info = render_colour_border_preview(bare, dest, 148, 105, 5, (0, 100, 100, 0), False, page)
        pages.append(bool(info.get("success")) and os.path.getsize(dest) > 32)
    record(
        "colour-border-gs",
        all(checks) and all(pages),
        product="a6-landscape",
        pages=2,
        size="preview",
        light="n/a",
        reasons="exit+file" if all(checks) else "gs rule",
        live="",
        qr="",
        note="" if all(pages) else "preview missing",
    )


def test_styles_are_different() -> None:
    from smart_bleed import auto_resolve_safe_zone

    image = np.zeros((120, 160, 3), np.uint8)
    image[:, :] = (30, 40, 90)
    # The outer pixels are a smooth ramp. The texture sits just inside the trim.
    gradient = np.linspace(0, 255, 160, dtype=np.uint8)
    ramp = np.stack([gradient, 255 - gradient, gradient // 2], axis=1)
    image[:6] = ramp
    texture = np.random.default_rng(3).integers(0, 255, size=(28, 160, 3), dtype=np.uint8)
    image[8:36] = texture
    image[8:36:8, ::8] = (15, 15, 15)
    made = {}
    for name in ("stretch", "gradient_extrapolate", "frequency_separated"):
        out, _meta = auto_resolve_safe_zone(image.copy(), target_bleed_px=10, bleed_strategy=name, dpi=72.0, allow_cloud=False)
        made[name] = out
    def ring(item):
        return np.concatenate([
            item[:10].reshape(-1),
            item[-10:].reshape(-1),
            item[:, :10].reshape(-1),
            item[:, -10:].reshape(-1),
        ]).astype(np.int16)
    gaps = {
        "stretch-gradient": float(np.mean(np.abs(ring(made["stretch"]) - ring(made["gradient_extrapolate"])))),
        "stretch-frequency": float(np.mean(np.abs(made["stretch"][:10].astype(np.int16) - made["frequency_separated"][:10].astype(np.int16)))),
        "gradient-frequency": float(np.mean(np.abs(ring(made["gradient_extrapolate"]) - ring(made["frequency_separated"])))),
    }
    textured = np.abs(made["stretch"][:10].astype(np.int16) - made["frequency_separated"][:10].astype(np.int16))
    textured_share = float(np.mean(textured > 12))
    gaps["frequency-share"] = textured_share
    record(
        "manual-styles-differ",
        gaps["stretch-gradient"] > 1.0 and gaps["gradient-frequency"] > 1.0 and gaps["stretch-frequency"] > 8.0 and textured_share > 0.15,
        product="card",
        pages=1,
        size="styles",
        light="n/a",
        reasons=" ".join(f"{key} {value:.1f}" for key, value in gaps.items()),
        live="",
        qr="",
        note="",
    )


def test_bad_client_files() -> None:
    """Every bad-client case: fonts, type 3, substitutes, hairlines, black, spots, resolution."""
    from client_file_audit_check import main as audit_main

    try:
        audit_main()
        record("bad-client-audit", True, product="suite", pages=1, size="audit", light="red", reasons="fonts hairlines black spots resolution", live="checked", qr="")
    except SystemExit as exc:
        record("bad-client-audit", False, product="suite", pages=0, size="audit", light="", reasons=str(exc), live="", qr="", note=str(exc))


def test_cover_reads_pdf() -> None:
    import subprocess
    import sys

    from cover_crop_notice import load_artwork_bgr

    folder = tempfile.mkdtemp(prefix="cust-cover-")
    src = os.path.join(folder, "page.pdf")
    _write_canva(src)
    bare = os.path.join(folder, "upload-no-ext")
    with open(src, "rb") as handle, open(bare, "wb") as out:
        out.write(handle.read())
    image = load_artwork_bgr(bare)
    script = os.path.join(os.path.dirname(__file__), "cover_crop_notice.py")
    failed = subprocess.run(
        [sys.executable, script, "preview", os.path.join(folder, "missing"), os.path.join(folder, "out.png"), "148", "105"],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        payload = json.loads(failed.stdout.strip() or "{}")
    except json.JSONDecodeError:
        payload = {}
    route = open(os.path.join(os.path.dirname(__file__), "routes.ts"), encoding="utf-8").read()
    catch = route.split("Cover crop notice failed", 1)[-1][:500]
    reported_failure = failed.returncode != 0 and payload.get("success") is False and "success: false" in catch
    record(
        "cover-reads-pdf",
        image is not None and image.size > 0 and reported_failure,
        product="a6-landscape",
        pages=1,
        size=f"{0 if image is None else image.shape[1]}x{0 if image is None else image.shape[0]}",
        light="n/a",
        reasons="header" if image is not None else "Could not read artwork",
        live="",
        qr="",
        note="" if reported_failure else f"exit {failed.returncode} {payload}",
    )


def test_imagen_stays_local() -> None:
    import smart_bleed

    called = {"n": 0}
    original = smart_bleed._ai_outpaint_gemini_cloud_bgr

    def _boom(*_args, **_kwargs):
        called["n"] += 1
        raise RuntimeError("paid cloud call")

    smart_bleed._ai_outpaint_gemini_cloud_bgr = _boom
    os.environ["GEMINI_API_KEY"] = "test-key-not-a-real-call"
    try:
        image = np.full((40, 60, 3), 80, np.uint8)
        folder = tempfile.mkdtemp(prefix="cust-ai-")
        smart_bleed.generate_bleed_variants(image, 72.0, os.path.join(folder, "preview"), ".png")
        preview_calls = called["n"]
        smart_bleed.auto_resolve_safe_zone(
            image, target_bleed_px=6, bleed_strategy="ai_outpaint", dpi=72.0, allow_cloud=True,
        )
        chosen = called["n"]
    finally:
        smart_bleed._ai_outpaint_gemini_cloud_bgr = original
        os.environ.pop("GEMINI_API_KEY", None)
    record(
        "imagen-only-when-picked",
        preview_calls == 0 and chosen == 1,
        product="n/a",
        pages=1,
        size="ai",
        light="n/a",
        reasons=f"preview {preview_calls} picked {chosen}",
        live="",
        qr="",
        note="",
    )


def test_manual_styles() -> None:
    from smart_bleed import build_style_previews

    folder = tempfile.mkdtemp(prefix="cust-styles-")
    src = os.path.join(folder, "three.pdf")
    _write_pdf(src, {"trim_w": 90, "trim_h": 50, "pages": 3, "bleed_mm": 0, "text": "STYLE"})
    preview = build_style_previews(src, os.path.join(folder, "style"), 72)
    pages = preview.get("pages") or []
    missing = []
    if preview.get("pageCount") != 3 or len(pages) != 3:
        missing.append(f"pages {preview.get('pageCount')}")
    for index, paths in enumerate(pages):
        for method in STYLE_IDS:
            target = paths.get(method) if isinstance(paths, dict) else None
            if not target or not os.path.exists(target):
                missing.append(f"p{index + 1}:{method}")
                continue
            image = cv2.imread(target)
            if image is None or float(np.std(image)) < 2.0:
                missing.append(f"blank p{index + 1}:{method}")
                continue
            if index == 0:
                continue
            first = pages[0].get(method) if isinstance(pages[0], dict) else None
            other = cv2.imread(first) if first else None
            if other is None or other.shape != image.shape:
                continue
            if float(np.mean(np.abs(other.astype(np.float32) - image.astype(np.float32)))) < 1.0:
                missing.append(f"same p{index + 1}:{method}")
    record(
        "manual-style-pages",
        not missing,
        product="card",
        pages=preview.get("pageCount") or 0,
        size="styles",
        light="n/a",
        reasons="" if not missing else "; ".join(missing[:8]),
        live="3 pages",
        qr="",
        note="; ".join(missing[:8]),
    )


def _write_table() -> None:
    out = "/opt/cursor/artifacts/customer_files_table.txt"
    os.makedirs(os.path.dirname(out), exist_ok=True)
    headers = ("file", "product", "pages", "size", "light", "reasons", "result")
    lines = ["\t".join(headers)]
    for row in ROWS:
        lines.append("\t".join(str(row.get(key, "")) for key in ("name", "product", "pages", "size", "light", "reasons", "ok")))
    text = "\n".join(lines) + "\n"
    with open(out, "w", encoding="utf-8") as handle:
        handle.write(text)
    print(text)


def main() -> None:
    os.environ.pop("VECTOR_ESRGAN", None)
    test_every_product()
    test_bleed_colour_pages()
    test_images()
    test_manual_styles()
    test_bleed_matches_edge()
    test_bleed_diagonal_and_column()
    test_real_a6_file()
    test_canva_a6()
    test_gs_and_colour_border()
    test_styles_are_different()
    test_bad_client_files()
    test_cover_reads_pdf()
    test_imagen_stays_local()
    test_plate_facts_skip_reencode()
    test_six_jobs()
    _write_table()
    if FAILURES:
        raise SystemExit(f"{len(FAILURES)} failed: {', '.join(FAILURES)}")
    print(f"customer file checks passed ({len(ROWS)})")


if __name__ == "__main__":
    main()
