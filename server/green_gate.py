#!/usr/bin/env python3
"""Strict press checklist. Green only when every item passes.

A failed item that can still be printed is amber, with the plain reason.
Text on the cut, a blank page, or a shape that cannot be extended is red.
Nothing here sends a message. The screen only prepares one to copy.
"""

from __future__ import annotations

BLEED_MM = 5.0
SAFE_MM = 3.0
TAC_LIMIT = 300.0
MIN_EFFECTIVE_DPI = 300.0

PROPORTION_MESSAGE = (
    "Thanks for the artwork. The shape does not match the print size, and there is no extra "
    "picture around the edge for us to extend. Please send a file that is already the right "
    "proportions, ideally with 5 mm of extra image past the cut line."
)
MISSING_MESSAGE = (
    "Thanks for sending this. The page looks empty, so part of the artwork is missing. "
    "Please send the full design again as a PDF, JPG, or PNG."
)
CUT_MESSAGE = (
    "Thanks for the artwork. Some of the writing sits on the cut line, so the printer would "
    "trim it off. Please move that text at least 3 mm inside the edge and send the file again."
)
LOW_RES_MESSAGE = (
    "Thanks for the picture. It is too small to print sharply at this size, even if we enlarge it. "
    "Please send a larger file, about 300 DPI at the finished size, or a PDF from the design program."
)


def client_message(kind: str, label: str = "this size", trim_w: float = 148, trim_h: float = 210) -> str:
    if kind == "proportions":
        return PROPORTION_MESSAGE
    if kind == "missing":
        return MISSING_MESSAGE
    if kind == "cut":
        return CUT_MESSAGE
    if kind == "low-res":
        return LOW_RES_MESSAGE
    return ""


def assess(press_path: str, trim_w: float, trim_h: float, context: dict | None = None) -> dict:
    """Pass/fail checklist for one press PDF. Never raises."""
    context = context or {}
    items = []
    severity = ""
    message = ""
    try:
        items.append(_bleed(press_path, trim_w, trim_h))
        items.append(_boxes(press_path, trim_w, trim_h))
        cmyk, ink = _colour(press_path)
        items.append(cmyk)
        items.append(ink)
        items.append(_resolution(press_path, context))
        safe, cut = _safe_zone(press_path, trim_w, trim_h, context)
        items.append(safe)
        if cut:
            severity = "red"
            message = client_message("cut")
        items.append(_lines(context))
        items.append(_letters(context))
        items.append(_fonts(press_path))
        if _page_empty(press_path):
            severity = "red"
            message = client_message("missing")
            items.append({
                "id": "content",
                "label": "The page has the artwork on it",
                "passed": False,
                "detail": "The page looks empty, so some of the artwork is missing.",
            })
        else:
            items.append({
                "id": "content",
                "label": "The page has the artwork on it",
                "passed": True,
                "detail": "",
            })
    except Exception as exc:
        items.append({
            "id": "checklist",
            "label": "The press file could be checked",
            "passed": False,
            "detail": f"The press check could not finish ({str(exc)[:120]}).",
        })
    return {"items": items, "severity": severity, "clientMessage": message}


def _item(item_id: str, label: str, passed: bool, detail: str) -> dict:
    return {"id": item_id, "label": label, "passed": bool(passed), "detail": "" if passed else detail}


def _bleed(path: str, trim_w: float, trim_h: float) -> dict:
    import pymupdf as fitz

    doc = fitz.open(path)
    try:
        page = doc[0]
        pix = page.get_pixmap(matrix=fitz.Matrix(1, 1), alpha=False, colorspace=fitz.csRGB)
        import numpy as np

        rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3].copy()
    finally:
        doc.close()
    gray = rgb.mean(axis=2)
    # 5 mm at 72 DPI is about 14 px. A sliver is a bright line the fill did not cover.
    band = max(4, int(round(BLEED_MM / 25.4 * 72.0)))
    sliver = _sliver(gray, band)
    short = min(gray.shape[0], gray.shape[1])
    filled = short > band * 2 and not sliver
    detail = "The 5 mm bleed has a thin empty gap." if sliver else "The 5 mm bleed is missing or empty."
    return _item("bleed", "5 mm bleed is present and filled, with no slivers", filled, detail)


def _sliver(gray, band: int) -> bool:
    import numpy as np

    height, width = gray.shape
    if height <= band * 2 + 4 or width <= band * 2 + 4:
        return False

    def gap(outer, inner) -> bool:
        # A line much lighter than the artwork beside it, across a long stretch.
        delta = outer.astype(np.int16) - inner.astype(np.int16)
        bright = (outer > 245) & (delta > 40)
        return int(np.count_nonzero(bright)) > max(8, int(0.25 * bright.size))

    top = gap(gray[band // 2], gray[band + 2])
    bottom = gap(gray[-(band // 2) - 1], gray[-(band + 3)])
    left = gap(gray[:, band // 2], gray[:, band + 2])
    right = gap(gray[:, -(band // 2) - 1], gray[:, -(band + 3)])
    return bool(top or bottom or left or right)


def _boxes(path: str, trim_w: float, trim_h: float) -> dict:
    import pymupdf as fitz

    doc = fitz.open(path)
    try:
        page = doc[0]
        media = page.mediabox
        trim = page.trimbox
        bleed = page.bleedbox
    finally:
        doc.close()
    def mm(value: float) -> float:
        return float(value) * 25.4 / 72.0

    inset_x = mm(trim.x0)
    inset_y = mm(trim.y0)
    ok = (
        abs(inset_x - BLEED_MM) < 0.6
        and abs(inset_y - BLEED_MM) < 0.6
        and abs(mm(trim.width) - float(trim_w)) < 1.2
        and abs(mm(trim.height) - float(trim_h)) < 1.2
        and abs(mm(bleed.width) - mm(media.width)) < 1.5
        and abs(mm(bleed.height) - mm(media.height)) < 1.5
        and abs(mm(media.width) - (float(trim_w) + 2 * BLEED_MM)) < 1.5
        and abs(mm(media.height) - (float(trim_h) + 2 * BLEED_MM)) < 1.5
    )
    detail = (
        f"Trim is {mm(trim.width):.1f} × {mm(trim.height):.1f} mm and the bleed inset is "
        f"{inset_x:.1f} mm. Expected {trim_w:.0f} × {trim_h:.0f} mm with a 5 mm inset."
    )
    return _item("boxes", "Trim, bleed and media boxes are correct", ok, detail)


def _colour(path: str) -> tuple[dict, dict]:
    import pymupdf as fitz

    doc = fitz.open(path)
    try:
        page = doc[0]
        images = page.get_images() or []
        spaces = []
        tac = 0.0
        saw_cmyk = False
        for image in images:
            info = doc.extract_image(image[0])
            space = int(info.get("colorspace") or 0)
            spaces.append(space)
            if space == 4:
                saw_cmyk = True
                tac = max(tac, _tac(info.get("image") or b""))
    finally:
        doc.close()
    if not images:
        cmyk_ok = False
        cmyk_detail = "The file has no CMYK picture. Press files need to be CMYK."
    else:
        cmyk_ok = all(space == 4 for space in spaces)
        cmyk_detail = "The press picture is not CMYK."
    ink_ok = (not saw_cmyk) or tac <= TAC_LIMIT + 8
    ink_detail = f"The heaviest ink is about {tac:.0f}%. The limit is {TAC_LIMIT:.0f}%."
    if not saw_cmyk:
        ink_ok = cmyk_ok
        ink_detail = "Ink could not be measured because the file is not CMYK."
    return (
        _item("cmyk", "The press file is CMYK", cmyk_ok, cmyk_detail),
        _item("ink", "Total ink stays within the press limit", ink_ok, ink_detail),
    )


def _tac(blob: bytes) -> float:
    if not blob:
        return 0.0
    import io

    import numpy as np
    from PIL import Image

    image = Image.open(io.BytesIO(blob))
    if image.mode != "CMYK":
        return 0.0
    image.thumbnail((48, 48))
    arr = np.asarray(image, dtype=np.float32)
    if arr.ndim != 3 or arr.shape[2] < 4:
        return 0.0
    total = arr[:, :, :4].sum(axis=2) / 255.0 * 100.0
    return float(np.percentile(total, 99))


def _resolution(path: str, context: dict) -> dict:
    upscale = float(context.get("upscale") or 1)
    if upscale < 0.05:
        upscale = 1.0
    effective = 300.0 / upscale
    raster_left = _raster_text(context)
    import pymupdf as fitz

    doc = fitz.open(path)
    try:
        images = doc[0].get_images() or []
        info = doc[0].get_image_info() or []
    finally:
        doc.close()
    file_dpi = 0.0
    for image in info:
        bbox = image.get("bbox")
        if not bbox:
            continue
        width_in = max(0.01, (float(bbox[2]) - float(bbox[0])) / 72.0)
        height_in = max(0.01, (float(bbox[3]) - float(bbox[1])) / 72.0)
        file_dpi = max(file_dpi, float(image.get("width") or 0) / width_in, float(image.get("height") or 0) / height_in)
    if not images:
        return _item("resolution", "Remaining raster is at least 300 DPI", True, "")
    # The file can claim 400 PPI after an enlarge. The honest figure is the source.
    honest = min(effective, file_dpi) if file_dpi else effective
    ok = honest + 1 >= MIN_EFFECTIVE_DPI or not raster_left
    if raster_left and honest + 1 < MIN_EFFECTIVE_DPI:
        ok = False
    detail = f"The picture is about {honest:.0f} DPI at the print size. It needs 300 DPI."
    if not raster_left and honest + 1 < MIN_EFFECTIVE_DPI:
        # The picture was enlarged, but no text is still a picture. Flag the raster anyway.
        ok = False
        detail = f"The remaining picture is about {honest:.0f} DPI at the print size. It needs 300 DPI."
    return _item("resolution", "Remaining raster is at least 300 DPI at final size", ok, detail)


def _raster_text(context: dict) -> bool:
    for row in context.get("textGate") or []:
        text = str(row.get("text") or "")
        letters = sum(1 for ch in text if ch.isalnum())
        if letters >= 3 and row.get("mode") != "vector":
            return True
    return False


def _safe_zone(path: str, trim_w: float, trim_h: float, context: dict) -> tuple[dict, bool]:
    edge = context.get("edge") or {}
    over = [str(text) for text in (edge.get("overCut") or []) if text]
    near = [str(text) for text in (edge.get("nearTrim") or []) if text]
    if not over and not near:
        over, near = _pdf_text_edge(path, trim_w, trim_h)
    if over:
        sample = over[0]
        return _item(
            "safe",
            "No text or key content within 3 mm of the trim",
            False,
            f"Text sits on the cut line ({sample}).",
        ), True
    if near:
        sample = near[0]
        return _item(
            "safe",
            "No text or key content within 3 mm of the trim",
            False,
            f"Text is within 3 mm of the trim ({sample}).",
        ), False
    return _item("safe", "No text or key content within 3 mm of the trim", True, ""), False


def _pdf_text_edge(path: str, trim_w: float, trim_h: float) -> tuple[list, list]:
    import pymupdf as fitz

    doc = fitz.open(path)
    try:
        page = doc[0]
        words = page.get_text("words") or []
        trim = page.trimbox
    finally:
        doc.close()
    safe = SAFE_MM * 72.0 / 25.4
    over, near = [], []
    for word in words:
        x0, y0, x1, y1, text = word[:5]
        if not str(text).strip():
            continue
        rect = fitz.Rect(x0, y0, x1, y1)
        if not trim.contains(rect):
            over.append(str(text))
            continue
        inner = fitz.Rect(trim.x0 + safe, trim.y0 + safe, trim.x1 - safe, trim.y1 - safe)
        if not inner.contains(rect):
            near.append(str(text))
    return over[:8], near[:8]


def _lines(context: dict) -> dict:
    gate = list(context.get("textGate") or [])
    if not gate:
        return _item("lines", "Every text line is vector, or a sharp picture", True, "")
    weak = []
    for row in gate:
        text = str(row.get("text") or "").strip()
        if sum(1 for ch in text if ch.isalnum()) < 3:
            continue
        if row.get("mode") == "vector":
            continue
        if row.get("sharp") is False:
            weak.append(text[:60])
    # A raster line on a file under 300 DPI is not a sharp picture. The resolution item covers that.
    # Here, a raster line is allowed. A vector line that failed its own read is not.
    bad = [
        str(row.get("text") or "")[:60]
        for row in gate
        if row.get("mode") == "vector" and row.get("ok") is False
    ]
    detail = "A vector line does not match its picture."
    if weak:
        bad = weak
        detail = "A text line is still a soft picture."
    return _item("lines", "Every text line is vector, or a sharp picture", not bad, detail if bad else "")


def _letters(context: dict) -> dict:
    gate = list(context.get("textGate") or [])
    if not gate:
        return _item("letters", "The press text matches the source text", True, "")
    changed = []
    for row in gate:
        if row.get("mode") != "vector":
            continue
        source = " ".join(str(row.get("text") or "").split())
        render = " ".join(str(row.get("render") or "").split())
        if source and render and source != render:
            changed.append(source[:60])
    detail = f"A line changed ({changed[0]})." if changed else ""
    return _item("letters", "No letters changed from the source", not changed, detail)


def _fonts(path: str) -> dict:
    from vector_retype import fonts_embedded

    ok = bool(fonts_embedded(path))
    return _item("fonts", "Fonts are embedded", ok, "A font in the press file is not embedded.")


def _page_empty(path: str) -> bool:
    import pymupdf as fitz
    import numpy as np

    doc = fitz.open(path)
    try:
        pix = doc[0].get_pixmap(matrix=fitz.Matrix(0.3, 0.3), alpha=False, colorspace=fitz.csRGB)
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n).copy()
    finally:
        doc.close()
    return float(arr.std()) < 4.0
