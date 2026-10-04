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
        items.append(_paint_clean(press_path, context))
        items.append(_ink_colour(press_path, context))
        items.append(_picture_sharp(press_path, context))
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
        cmyk_ok, tac = _vector_cmyk(path)
        saw_cmyk = cmyk_ok
        cmyk_detail = "The press file is not CMYK."
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


def _vector_cmyk(path: str) -> tuple[bool, float]:
    """A page of CMYK fills and live text, with no RGB. Returns (ok, peak TAC)."""
    import re

    import pymupdf as fitz

    doc = fitz.open(path)
    try:
        raw = "".join(page.read_contents().decode("latin1", "replace") for page in doc)
    finally:
        doc.close()
    if re.search(r"\brg\b|\bRG\b|DeviceRGB", raw):
        return False, 0.0
    found = re.findall(r"([0-9.]+)\s+([0-9.]+)\s+([0-9.]+)\s+([0-9.]+)\s+k\b", raw)
    if not found and "DeviceCMYK" not in raw:
        return False, 0.0
    tac = 0.0
    for parts in found:
        tac = max(tac, sum(float(channel) for channel in parts) * 100.0)
    return True, tac


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
    del context
    import pymupdf as fitz

    doc = fitz.open(path)
    try:
        images = doc[0].get_images() or []
        info = doc[0].get_image_info() or []
    finally:
        doc.close()
    if not images:
        return _item("resolution", "Remaining raster is at least 300 DPI at final size", True, "")
    # Pixel size of the embedded picture against the box it is printed in.
    file_dpi = 0.0
    for image in info:
        bbox = image.get("bbox")
        if not bbox:
            continue
        width_in = max(0.01, (float(bbox[2]) - float(bbox[0])) / 72.0)
        height_in = max(0.01, (float(bbox[3]) - float(bbox[1])) / 72.0)
        dpi = min(float(image.get("width") or 0) / width_in, float(image.get("height") or 0) / height_in)
        file_dpi = max(file_dpi, dpi)
    ok = file_dpi + 1 >= MIN_EFFECTIVE_DPI
    detail = f"The picture is about {file_dpi:.0f} DPI at the print size. It needs 300 DPI."
    return _item("resolution", "Remaining raster is at least 300 DPI at final size", ok, detail)


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


def _retyped_rows(context: dict) -> list:
    return [row for row in list(context.get("textGate") or []) if row.get("retyped")]


def _press_matrix(page):
    """Scale that lands on the embedded picture's pixels.

    600/72 is one pixel off on a 600 PPI page, and that stretch blurs the type.
    """
    import pymupdf as fitz

    images = page.get_images() or []
    if images:
        info = page.parent.extract_image(images[0][0])
        width = int(info.get("width") or 0)
        height = int(info.get("height") or 0)
        if width >= 8 and height >= 8 and page.rect.width > 1 and page.rect.height > 1:
            return fitz.Matrix(width / page.rect.width, height / page.rect.height)
    return fitz.Matrix(600.0 / 72.0, 600.0 / 72.0)


def _page_rgb(path: str, zoom: float):
    import pymupdf as fitz
    import numpy as np

    doc = fitz.open(path)
    try:
        page = doc[0]
        words = page.get_text("words") or []
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False, colorspace=fitz.csRGB)
        rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3].copy()
    finally:
        doc.close()
    return rgb, words


def _pdf_line_groups(words: list) -> list:
    rows = []
    for word in words:
        if len(word) < 5 or not str(word[4]).strip():
            continue
        y0 = float(word[1])
        placed = False
        for row in rows:
            if abs(row["y"] - y0) <= 2.2:
                row["words"].append(word)
                placed = True
                break
        if not placed:
            rows.append({"y": y0, "words": [word]})
    split = []
    for row in rows:
        words_sorted = sorted(row["words"], key=lambda word: float(word[0]))
        current = [words_sorted[0]]
        for word in words_sorted[1:]:
            previous = current[-1]
            gap = float(word[0]) - float(previous[2])
            height = max(4.0, float(previous[3]) - float(previous[1]))
            # Another column on the same baseline is not the same line.
            if gap > max(8.0, height * 1.6):
                split.append({"y": row["y"], "words": current})
                current = [word]
            else:
                current.append(word)
        split.append({"y": row["y"], "words": current})
    for row in split:
        row["text"] = " ".join(str(word[4]) for word in row["words"])
    return split


def _paint_clean(path: str, context: dict) -> dict:
    """A retyped line must not leave a grey band on the paper around it."""
    rows = _retyped_rows(context)
    label = "Retyped lettering sits on the same paper"
    if not rows:
        return _item("paint", label, True, "")
    try:
        import cv2
        import numpy as np

        zoom = 4.0
        rgb, words = _page_rgb(path, zoom)
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        wanted = {" ".join(str(row.get("text") or "").split()) for row in rows}
        wanted.discard("")
        bad = ""
        for group in _pdf_line_groups(words):
            if group["text"] not in wanted and not any(group["text"] in line or line in group["text"] for line in wanted):
                continue
            for word in group["words"]:
                x0 = max(0, int(float(word[0]) * zoom) - 2)
                y0 = max(0, int(float(word[1]) * zoom) - 2)
                x1 = min(gray.shape[1], int(float(word[2]) * zoom) + 3)
                y1 = min(gray.shape[0], int(float(word[3]) * zoom) + 3)
                if x1 - x0 < 6 or y1 - y0 < 6:
                    continue
                pad = 28
                cx0, cy0 = max(0, x0 - pad), max(0, y0 - pad)
                cx1, cy1 = min(gray.shape[1], x1 + pad), min(gray.shape[0], y1 + pad)
                patch = gray[cy0:cy1, cx0:cx1]
                paper = float(np.median(patch))
                ink = (patch < paper - 28).astype(np.uint8)
                if int(ink.sum()) < 8:
                    continue
                near_k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11))
                far_k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (49, 49))
                skip = cv2.dilate(ink, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
                near = (cv2.dilate(ink, near_k) > 0) & (skip == 0)
                far = (cv2.dilate(ink, far_k) > 0) & (cv2.dilate(ink, near_k) == 0)
                if int(near.sum()) < 12 or int(far.sum()) < 12:
                    continue
                near_px = patch[near].astype(np.float32)
                far_px = patch[far].astype(np.float32)
                if abs(float(near_px.mean()) - float(far_px.mean())) > 8.0:
                    bad = group["text"][:60]
                    break
                if abs(float(near_px.std()) - float(far_px.std())) > 6.0:
                    bad = group["text"][:60]
                    break
            if bad:
                break
    except Exception as exc:
        return _item("paint", label, False, f"The paper around the type could not be checked ({str(exc)[:80]}).")
    detail = f"A grey patch shows around the type ({bad})." if bad else ""
    return _item("paint", label, not bad, detail)


def _ink_colour(path: str, context: dict) -> dict:
    """Retyped ink is the dark stroke core, not the grey edge."""
    rows = [row for row in _retyped_rows(context) if row.get("sourceInk")]
    label = "Retyped lettering matches the source ink colour"
    if not rows:
        return _item("typecolour", label, True, "")
    try:
        import cv2
        import numpy as np

        zoom = 8.0
        rgb, words = _page_rgb(path, zoom)
        wanted = {}
        for row in rows:
            text = " ".join(str(row.get("text") or "").split())
            if text:
                wanted[text] = [int(v) for v in row["sourceInk"][:3]]
        bad = ""
        for group in _pdf_line_groups(words):
            target = wanted.get(group["text"])
            if target is None:
                for text, ink in wanted.items():
                    if group["text"] and (group["text"] in text or text in group["text"]):
                        target = ink
                        break
            if target is None:
                continue
            samples = []
            for word in group["words"]:
                x0 = max(0, int(float(word[0]) * zoom))
                y0 = max(0, int(float(word[1]) * zoom))
                x1 = min(rgb.shape[1], int(float(word[2]) * zoom) + 1)
                y1 = min(rgb.shape[0], int(float(word[3]) * zoom) + 1)
                if x1 <= x0 or y1 <= y0:
                    continue
                patch = rgb[y0:y1, x0:x1]
                gray = cv2.cvtColor(patch, cv2.COLOR_RGB2GRAY)
                paper = float(np.median(gray))
                dark = gray < paper - 24
                if int(dark.sum()) < 6:
                    continue
                pixels = patch[dark].astype(np.float32)
                # Darkest 15% by the RGB sum. A grey image would lose the green.
                darkness = pixels.sum(axis=1)
                core = pixels[darkness <= np.percentile(darkness, 15)]
                if core.shape[0] < 4:
                    core = pixels
                samples.append(np.median(core, axis=0))
            if not samples:
                continue
            found = np.median(np.stack(samples, axis=0), axis=0)
            target_px = np.array(target, dtype=np.float32)
            source_chroma = float(target_px.max() - target_px.min())
            found_chroma = float(found.max() - found.min())
            distance = float(np.linalg.norm(found - target_px))
            if source_chroma >= 8.0 and found_chroma < source_chroma * 0.5:
                bad = group["text"][:60]
                break
            if distance > 42.0:
                bad = group["text"][:60]
                break
    except Exception as exc:
        return _item("typecolour", label, False, f"The ink colour could not be checked ({str(exc)[:80]}).")
    detail = f"The type is a different colour from the source ({bad})." if bad else ""
    return _item("typecolour", label, not bad, detail)


def _picture_sharp(path: str, context: dict) -> dict:
    """The picture that was not retyped has to stay as sharp as the source.

    Laplacian variance over that raster, press at least 95% of the source
    at the same scale. Vector lines are left out so crisp type cannot hide
    a soft picture.
    """
    label = "The press picture is as sharp as the source"
    source = context.get("sourceBgr")
    placement = context.get("placement") or {}
    art = placement.get("artBox")
    if source is None or not art or len(art) < 4:
        return _item("sharp", label, True, "")
    try:
        import cv2
        import numpy as np

        import pymupdf as fitz

        ppi = float(placement.get("ppi") or 600)
        if ppi < 72:
            ppi = 600.0
        doc = fitz.open(path)
        try:
            page = doc[0]
            matrix = _press_matrix(page)
            pix = page.get_pixmap(matrix=matrix, alpha=False, colorspace=fitz.csRGB)
            rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3].copy()
        finally:
            doc.close()
        ax, ay, aw, ah = [int(v) for v in art[:4]]
        y0, x0 = max(0, ay), max(0, ax)
        y1, x1 = min(rgb.shape[0], ay + max(1, ah)), min(rgb.shape[1], ax + max(1, aw))
        if y1 - y0 < 16 or x1 - x0 < 16:
            return _item("sharp", label, True, "")
        press = rgb[y0:y1, x0:x1]
        src = np.asarray(source)
        if src.ndim != 3 or src.shape[2] < 3:
            return _item("sharp", label, True, "")
        src = cv2.cvtColor(src[:, :, :3], cv2.COLOR_BGR2RGB)
        if src.shape[1] != press.shape[1] or src.shape[0] != press.shape[0]:
            src = cv2.resize(src, (press.shape[1], press.shape[0]), interpolation=cv2.INTER_LANCZOS4)
        # The bleed join is feathered on purpose. The picture inside it is the check.
        inset = int(round(4.0 / 25.4 * ppi))
        if press.shape[0] > inset * 4 and press.shape[1] > inset * 4:
            press = press[inset:-inset, inset:-inset]
            src = src[inset:-inset, inset:-inset]
        else:
            inset = 0
        mask = np.ones(press.shape[:2], np.uint8)
        for row in list(context.get("textGate") or []):
            if row.get("mode") != "vector":
                continue
            rect = row.get("plateRect")
            if not rect or len(rect) < 4:
                continue
            rx, ry, rw, rh = [int(v) for v in rect[:4]]
            left = max(0, rx - x0 - inset)
            top = max(0, ry - y0 - inset)
            right = min(mask.shape[1], rx + max(1, rw) - x0 - inset)
            bottom = min(mask.shape[0], ry + max(1, rh) - y0 - inset)
            if right > left and bottom > top:
                mask[top:bottom, left:right] = 0
        mask = cv2.erode(mask, np.ones((5, 5), np.uint8))
        if int(mask.sum()) < 400:
            return _item("sharp", label, True, "")
        press_gray = cv2.cvtColor(press, cv2.COLOR_RGB2GRAY)
        src_gray = cv2.cvtColor(src, cv2.COLOR_RGB2GRAY)
        press_var = float(cv2.Laplacian(press_gray, cv2.CV_64F)[mask > 0].var())
        source_var = float(cv2.Laplacian(src_gray, cv2.CV_64F)[mask > 0].var())
        if source_var <= 1.0:
            return _item("sharp", label, True, "")
        ratio = press_var / source_var
        detail = "" if ratio >= 0.95 else f"The press picture is softer than the source ({ratio:.2f})."
        return _item("sharp", label, ratio >= 0.95, detail)
    except Exception as exc:
        return _item("sharp", label, False, f"The sharpness could not be checked ({str(exc)[:80]}).")


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
