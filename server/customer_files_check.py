#!/usr/bin/env python3
"""Customer-file press checks: every product, bleed kind, colour, and the six jobs."""

from __future__ import annotations

import json
import os
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
            left = (0.85, 0.12, 0.1) if index % 2 == 0 else (0.1, 0.55, 0.75)
            right = (0.1, 0.2, 0.8) if index % 2 == 0 else (0.75, 0.15, 0.55)
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
    if spec.get("fixable", True) and light == "red":
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
        _expect_press(name, result, {
            "trim_w": tw, "trim_h": th, "pages": 1, "product": pid, "fixable": True,
            "reason_lacks": ["cannot be extended", "could not be read", "Traceback"],
        })
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


def test_bleed_matches_edge() -> None:
    """A grey, a one-pixel white fringe, and a diagonal must extend without a step or a darker grey."""
    import pymupdf as fitz
    from PIL import Image
    from press_ready_engine import compile_vector_press

    folder = tempfile.mkdtemp(prefix="cust-bleed-match-")
    width, height = 1806, 1300
    image = np.zeros((height, width, 3), np.uint8)
    image[:, :] = (183, 184, 185)
    image[:, -1] = (230, 210, 210)
    for y in range(height):
        boundary = int((y - 900) / 3)
        if 0 <= boundary < width:
            image[y, :boundary] = (20, 40, 80)
    # A 0.45 pt keyline blooms to about three pixels. It must not become the bleed.
    image[-3:, :] = (245, 245, 245)
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
    page_w, page_h = 433.5 * 25.4 / 72.0, 312.0 * 25.4 / 72.0
    seam_x = (5.0 - (page_w - 148.0) / 2.0) * 72.0 / 25.4
    seam_y = (5.0 - (page_h - 105.0) / 2.0) * 72.0 / 25.4
    from press_ready_engine import _gs_rgb_pages, _window_max_delta_e

    gs_ok = False
    gs_note = "no gs page"
    worst = 99.0
    if os.path.exists(out):
        rendered = _gs_rgb_pages(out)
        if rendered:
            gs = rendered[0]
            doc = fitz.open(out)
            try:
                rect = doc[0].rect
                gs_h, gs_w = gs.shape[:2]
                sx = int(round(seam_x / float(rect.width) * gs_w))
                sy = int(round(seam_y / float(rect.height) * gs_h))
            finally:
                doc.close()
            # Adjacent pixels, through the same png16m render as the press. A 6 px
            # mean looks past a diagonal and reports a step that is not on the seam.
            pairs = {
                "left": ((sy, gs_h - sy, sx - 1, sx), (sy, gs_h - sy, sx, sx + 1), "y"),
                "right": ((sy, gs_h - sy, gs_w - sx, gs_w - sx + 1), (sy, gs_h - sy, gs_w - sx - 1, gs_w - sx), "y"),
                "top": ((sy - 1, sy, sx, gs_w - sx), (sy, sy + 1, sx, gs_w - sx), "x"),
                "bottom": ((gs_h - sy, gs_h - sy + 1, sx, gs_w - sx), (gs_h - sy - 1, gs_h - sy, sx, gs_w - sx), "x"),
            }
            worst = 0.0
            worst_name = ""
            for name, (outside, inside, axis) in pairs.items():
                value = _window_max_delta_e(gs, outside, inside, axis)
                score = 99.0 if value is None else float(value)
                if score >= worst:
                    worst = score
                    worst_name = name
            seam_row = gs[min(gs_h - 1, max(0, gs_h - sy))]
            white_frac = float(np.mean(np.min(seam_row, axis=1) > 240))
            spot = gs[min(gs_h - 1, sy + 40), min(gs_w - 1, 2)]
            inside = gs[min(gs_h - 1, sy + 40), min(gs_w - 1, sx + 4)]
            gs_grey = abs(int(spot[0]) - int(inside[0])) <= 2 and abs(int(spot[1]) - int(inside[1])) <= 2
            gs_ok = white_frac < 0.05 and gs_grey and worst < 5.0
            gs_note = f"white {white_frac:.3f} adj {worst_name} {worst:.2f} grey {'ok' if gs_grey else 'shifted'}"
    record(
        "bleed-matches-edge",
        bool(result.get("used")) and gs_ok,
        product="a6-landscape",
        pages=1,
        size="seam",
        light="n/a",
        reasons=gs_note,
        live="",
        qr="",
    )


def test_bleed_diagonal_and_column() -> None:
    """A diagonal must keep its slope through Ghostscript, and a column must not shear or grow a dark line."""
    import pymupdf as fitz
    from PIL import Image
    from press_ready_engine import _delta_e00, _gs_rgb_pages, compile_vector_press

    folder = tempfile.mkdtemp(prefix="cust-bleed-slope-")
    width, height = 1806, 1300
    image = np.full((height, width, 3), (183, 184, 185), np.uint8)
    image[-1, :] = (250, 250, 250)
    # The boundary reaches the bottom-left corner and keeps moving left.
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
    rendered = _gs_rgb_pages(out) if os.path.exists(out) else []
    problems = []
    if not result.get("used") or not rendered:
        problems.append("no press page")
    else:
        gs = rendered[0]
        doc = fitz.open(out)
        try:
            rect = doc[0].rect
            page_w, page_h = 433.5 * 25.4 / 72.0, 312.0 * 25.4 / 72.0
            seam_x = (5.0 - (page_w - 148.0) / 2.0) * 72.0 / 25.4
            seam_y = (5.0 - (page_h - 105.0) / 2.0) * 72.0 / 25.4
            gs_h, gs_w = gs.shape[:2]
            sx = int(round(seam_x / float(rect.width) * gs_w))
            sy = int(round(seam_y / float(rect.height) * gs_h))
        finally:
            doc.close()
        seam = gs[min(gs_h - 1, gs_h - sy)]
        if float(np.mean(np.min(seam, axis=1) > 240)) > 0.05:
            problems.append("white seam")
        flat = gs[sy + 80, 2]
        if abs(int(flat[0]) - 183) > 2:
            problems.append(f"grey {tuple(int(v) for v in flat)}")

        def first_navy(row):
            found = np.where(row[:, 0] < 40)[0]
            return int(found[0]) if len(found) else -1

        positions = []
        for y in (gs_h - sy - 36, gs_h - sy - 12, gs_h - sy + 8, gs_h - 3):
            positions.append(first_navy(gs[y, : sx + 160]))
        if any(item < 0 for item in positions):
            problems.append(f"diagonal missing {positions}")
        elif any(positions[index + 1] > positions[index] + 3 for index in range(len(positions) - 1)):
            problems.append(f"diagonal step {positions}")
        jumps = []
        for y in (gs_h - sy - 6, gs_h - sy + 2, gs_h - sy + 10, gs_h - 2):
            band = gs[y, sx + 980:sx + 1200, 0].astype(int)
            delta = np.abs(np.diff(band))
            strong = np.where(delta > 80)[0]
            jumps.append(int(strong[0]) if len(strong) else -1)
        if any(item < 0 for item in jumps) or max(jumps) - min(jumps) > 1:
            problems.append(f"column shear {jumps}")
        corner = gs[gs_h - sy:, :sx]
        grey_px = int(np.sum(corner[:, :, 0] > 150))
        navy_px = int(np.sum(corner[:, :, 0] < 40))
        if grey_px < 30 or navy_px < 30:
            problems.append(f"corner grey {grey_px} navy {navy_px}")
        seam_px = gs[sy + 80, sx]
        bleed_px = gs[sy + 80, 1]
        if _delta_e00(seam_px, bleed_px) > 5:
            problems.append(f"adjacent dE {_delta_e00(seam_px, bleed_px):.1f}")
    record(
        "bleed-diagonal-column",
        not problems,
        product="a6-landscape",
        pages=1,
        size="slope",
        light="n/a",
        reasons="gs slope" if not problems else "; ".join(problems),
        live="",
        qr="",
        note="",
    )


def test_canva_a6() -> None:
    from press_ready_engine import edge_seam_delta_e

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
    seams = edge_seam_delta_e(press, seam_x, seam_y) if press and os.path.exists(press) else []
    worst = 0.0
    parts = []
    local_keys = ("left", "right", "top", "bottom", "tl", "tr", "bl", "br")
    if not seams:
        worst = 99.0
        problems_seed = ["no seam report"]
    else:
        problems_seed = []
    for row in seams:
        local = row.get("local") or {}
        numbers = [None if local.get(key) is None else float(local.get(key)) for key in local_keys]
        if any(item is None for item in numbers):
            worst = 99.0
        else:
            worst = max(worst, max(numbers))
        parts.append(
            "p{page} local L{left} R{right} T{top} B{bottom} tl{tl} tr{tr} bl{bl} br{br}".format(
                page=row.get("page"),
                left=local.get("left"),
                right=local.get("right"),
                top=local.get("top"),
                bottom=local.get("bottom"),
                tl=local.get("tl"),
                tr=local.get("tr"),
                bl=local.get("bl"),
                br=local.get("br"),
            )
        )
    seam_note = " ".join(parts)
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
