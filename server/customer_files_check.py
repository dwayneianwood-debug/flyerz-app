#!/usr/bin/env python3
"""Customer-file press checks: every product, bleed kind, colour, and the six jobs."""

from __future__ import annotations

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
        Image.new("CMYK", (32, 32), (0, 40, 80, 0)).save(cmyk_path, format="TIFF")
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
            page.insert_text((inset + 18, baseline), "LOCATION:", fontsize=28, fontname="helv", color=(0, 0, 0))
        else:
            page.insert_text((inset + 18, inset + 36), label, fontsize=18, fontname="helv", color=(0, 0, 0))
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
    page.insert_text((bleed + 36, bleed + 80), "ALREADY BLEED", fontsize=28, fontname="helv", color=(1, 1, 1))
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
    # DeviceCMYK navy that reaches the page edge. The second page is darker.
    inks = ((210, 160, 40, 80), (230, 180, 30, 120))
    for index, ink in enumerate(inks):
        pixels_w = int(round(page_w / 72.0 * 150))
        pixels_h = int(round(page_h / 72.0 * 150))
        plate = Image.new("CMYK", (pixels_w, pixels_h), ink)
        jpeg = os.path.join(folder, f"navy-{index}.jpg")
        plate.save(jpeg, format="JPEG", quality=92)
        accent = Image.new("CMYK", (160, 110), (0, 190, 210, 0))
        accent_path = os.path.join(folder, f"accent-{index}.tif")
        accent.save(accent_path, format="TIFF")
        mask_px = np.zeros((110, 160), np.uint8)
        cv2.ellipse(mask_px, (80, 55), (60, 40), 0, 0, 360, 255, -1)
        mask_px = cv2.GaussianBlur(mask_px, (21, 21), 0)
        mask_path = os.path.join(folder, f"mask-{index}.png")
        Image.fromarray(mask_px, mode="L").save(mask_path)
        with open(mask_path, "rb") as handle:
            mask_bytes = handle.read()
        page = doc.new_page(width=page_w, height=page_h)
        page.insert_image(page.rect, filename=jpeg)
        page.insert_image(fitz.Rect(90, 80, 230, 170), filename=accent_path, mask=mask_bytes)
        qr_path = os.path.join(folder, f"qr-{index}.png")
        cv2.imwrite(qr_path, _qr_image("https://flyerz.co.za/pay", module=4))
        page.insert_image(fitz.Rect(300, 36, 370, 106), filename=qr_path)
        writer = fitz.TextWriter(page.rect)
        writer.append((48, 230), f"ANTON {index + 1}", font=font, fontsize=32)
        writer.write_text(page, color=(1, 1, 1))
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
    worst = 99.0
    parts = []
    for row in seams:
        values = [row.get(edge) for edge in ("left", "right", "top", "bottom")]
        values += list((row.get("corners") or {}).values())
        numbers = [float(item) for item in values if item is not None]
        if numbers:
            worst = min(worst, max(numbers)) if worst == 99.0 else max(worst, max(numbers))
        parts.append(
            "p{page} L{left} R{right} T{top} B{bottom} br{br}".format(
                page=row.get("page"),
                left=row.get("left"),
                right=row.get("right"),
                top=row.get("top"),
                bottom=row.get("bottom"),
                br=(row.get("corners") or {}).get("br"),
            )
        )
    seam_note = " ".join(parts)
    reasons = " ".join(str(item) for item in (result.get("reasons") or []))
    problems = []
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
    if worst >= 3.0:
        problems.append(f"seam dE {worst}")
    _expect_press("canva-a6-auto", result, {
        "trim_w": 148, "trim_h": 105, "pages": 2, "product": "a6-landscape", "fixable": True,
        "live": ["ANTON 1", "ANTON 2"], "qr": True,
        "reason_lacks": ["cannot be extended", "cut line", "not CMYK", "Traceback", "Could not read"],
    })
    record(
        "canva-a6-seam",
        not problems and worst < 3.0,
        product=str(result.get("productId") or ""),
        pages=2,
        size=f"dE {worst:.2f}",
        light=str(result.get("light") or ""),
        reasons=seam_note,
        live="kept" if result.get("existingBleedKept") else "",
        qr="",
        note="; ".join(problems),
    )


def test_styles_are_different() -> None:
    from smart_bleed import auto_resolve_safe_zone

    image = np.zeros((96, 140, 3), np.uint8)
    image[:, :] = (30, 40, 90)
    gradient = np.linspace(0, 255, 140, dtype=np.uint8)
    image[0, :] = np.stack([gradient, 255 - gradient, gradient // 2], axis=1)
    noise = np.random.default_rng(3).integers(0, 255, size=(8, 140, 3), dtype=np.uint8)
    image[:8] = noise
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
        "stretch-frequency": float(np.mean(np.abs(ring(made["stretch"]) - ring(made["frequency_separated"])))),
        "gradient-frequency": float(np.mean(np.abs(ring(made["gradient_extrapolate"]) - ring(made["frequency_separated"])))),
    }
    record(
        "manual-styles-differ",
        all(value > 1.0 for value in gaps.values()),
        product="card",
        pages=1,
        size="styles",
        light="n/a",
        reasons=" ".join(f"{key} {value:.1f}" for key, value in gaps.items()),
        live="",
        qr="",
        note="",
    )


def test_cover_reads_pdf() -> None:
    from cover_crop_notice import load_artwork_bgr

    folder = tempfile.mkdtemp(prefix="cust-cover-")
    src = os.path.join(folder, "page.pdf")
    _write_canva(src)
    image = load_artwork_bgr(src)
    record(
        "cover-reads-pdf",
        image is not None and image.size > 0,
        product="a6-landscape",
        pages=1,
        size=f"{0 if image is None else image.shape[1]}x{0 if image is None else image.shape[0]}",
        light="n/a",
        reasons="",
        live="",
        qr="",
        note="" if image is not None else "Could not read artwork",
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
    test_canva_a6()
    test_styles_are_different()
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
