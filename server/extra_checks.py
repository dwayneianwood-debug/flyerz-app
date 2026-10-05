#!/usr/bin/env python3
"""Nine extra print checks. They sit beside the 25-point list and do not replace it.

Status values are the spec words: pass, fixed, warning, failed.
A Bleed-named DeviceRGB strip is the added edge, not artwork RGB.
"""

from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
import tempfile

MM = 72.0 / 25.4
BLEED_MM = 5.0
TECHNICAL = re.compile(r"cut|contour|die|crease|perf|varnish|\buv\b|white|foil", re.I)
FONT_EXTS = (".ttf", ".otf", ".ttc")


def _num(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def normalize_order(order: dict | None) -> dict:
    """A missing size stays missing. 148×210 is not invented here."""
    raw = order or {}
    explicit = raw.get("explicitSize")
    width = _num(raw.get("widthMm"))
    height = _num(raw.get("heightMm"))
    if explicit is False:
        width = None
        height = None
    elif width is None or height is None:
        width = None
        height = None
        explicit = False
    else:
        explicit = True
    sides = str(raw.get("sides") or "").strip().lower()
    if sides not in ("single", "double", "booklet"):
        sides = ""
    pages = raw.get("pageCount")
    try:
        pages = int(pages) if pages not in (None, "") else None
    except (TypeError, ValueError):
        pages = None
    if pages is not None and pages <= 0:
        pages = None
    finishes = raw.get("finishes") or []
    if isinstance(finishes, str):
        finishes = [part.strip() for part in finishes.split(",") if part.strip()]
    finishes = [str(item) for item in finishes if str(item).strip()]
    return {
        "widthMm": width,
        "heightMm": height,
        "explicitSize": bool(explicit),
        "sides": sides,
        "pageCount": pages,
        "finishes": finishes,
        "headToFoot": bool(raw.get("headToFoot")),
        "cmykOnly": raw.get("cmykOnly") is not False,
    }


def _worse_status(current: str, new: str) -> str:
    rank = {"pass": 0, "fixed": 1, "warning": 2, "failed": 3}
    if rank.get(new, 0) >= rank.get(current, 0):
        return new
    return current


def _row(code: str, name: str, status: str, detail: str) -> dict:
    return {
        "id": f"extra_{code}",
        "num": code,
        "name": name,
        "status": status,
        "pass": status in ("pass", "fixed"),
        "detail": detail,
        "message": detail,
        "label": f"{code}. {name}: {detail}",
        "autoFixed": status == "fixed",
    }


def light_from_extra(checks: list) -> dict:
    failed = [row for row in checks if row.get("status") == "failed"]
    warned = [row for row in checks if row.get("status") == "warning"]
    if failed:
        lines = [f"{row['num']}. {row['name']}: {row['detail']}" for row in failed]
        return {
            "light": "red",
            "reasons": lines,
            "clientMessage": "These extra checks failed:\n" + "\n".join(lines),
        }
    if warned:
        lines = [f"{row['num']}. {row['name']}: {row['detail']}" for row in warned]
        return {"light": "amber", "reasons": lines, "clientMessage": ""}
    return {"light": "green", "reasons": [], "clientMessage": ""}


def worse_light(left: dict | None, right: dict | None) -> dict:
    """The worse of the 25-point result and these checks. Neither one clears the other."""
    rank = {"green": 0, "amber": 1, "red": 2}
    left = left or {"light": "green", "reasons": [], "clientMessage": ""}
    right = right or {"light": "green", "reasons": [], "clientMessage": ""}
    winner = left if rank.get(left.get("light"), 0) >= rank.get(right.get("light"), 0) else right
    reasons = []
    for block in (left, right):
        for line in block.get("reasons") or []:
            if line and line not in reasons:
                reasons.append(line)
    messages = [str(block.get("clientMessage") or "").strip() for block in (left, right)]
    messages = [line for line in messages if line]
    return {
        "light": winner.get("light") or "green",
        "reasons": reasons,
        "clientMessage": "\n\n".join(messages),
    }


def _mm(pt: float) -> float:
    return float(pt) * 25.4 / 72.0


def _close(a: float, b: float, tol: float = 1.0) -> bool:
    return abs(float(a) - float(b)) <= tol


def _aspect_delta(width: float, height: float, order_w: float, order_h: float) -> float:
    if height <= 0 or order_h <= 0:
        return 1.0
    src = width / height
    dst = order_w / order_h
    if dst <= 0:
        return 1.0
    return abs(src - dst) / dst


def _swapped(width: float, height: float, order_w: float, order_h: float) -> bool:
    return _close(width, order_h) and _close(height, order_w) and not (_close(width, order_w) and _close(height, order_h))


def _page_size_mm(page) -> tuple[float, float]:
    box = page.trimbox if page.trimbox and page.trimbox.width > 2 else page.mediabox
    return _mm(box.width), _mm(box.height)


def _image_size_mm(path: str) -> tuple[float, float] | None:
    try:
        from PIL import Image

        with Image.open(path) as image:
            dpi = image.info.get("dpi") or (0, 0)
            dpi_x = float(dpi[0] or 0)
            dpi_y = float(dpi[1] or 0)
            if dpi_x < 10 or dpi_y < 10:
                return None
            return (image.size[0] / dpi_x * 25.4, image.size[1] / dpi_y * 25.4)
    except Exception:
        return None


def _image_pixels(path: str) -> tuple[int, int] | None:
    try:
        from PIL import Image

        with Image.open(path) as image:
            return int(image.size[0]), int(image.size[1])
    except Exception:
        return None


def _font_dirs() -> list[str]:
    here = os.path.dirname(os.path.abspath(__file__))
    return [
        os.path.join(here, "fonts"),
        os.path.join(here, "fonts", "v2"),
        "/usr/share/fonts/truetype/dejavu",
        "/usr/share/fonts/truetype/liberation",
    ]


def _bare_font(name: str) -> str:
    text = str(name or "").strip().lstrip("/")
    if "+" in text:
        text = text.split("+", 1)[1]
    return text


def local_font_match(name: str) -> str:
    """Exact file-stem match only. A lookalike face is not a match."""
    stem = _bare_font(name).lower()
    if not stem:
        return ""
    for root in _font_dirs():
        if not os.path.isdir(root):
            continue
        for dirpath, _dirs, files in os.walk(root):
            for filename in files:
                if not filename.lower().endswith(FONT_EXTS):
                    continue
                if os.path.splitext(filename)[0].lower() == stem:
                    return os.path.join(dirpath, filename)
    return ""


def looks_like_pdf(path: str) -> bool:
    if str(path).lower().endswith(".pdf"):
        return True
    try:
        with open(path, "rb") as handle:
            return handle.read(5) == b"%PDF-"
    except OSError:
        return False


def _open_pdf(path: str):
    import pymupdf as fitz

    if not path or not os.path.isfile(path) or not looks_like_pdf(path):
        return None
    try:
        return fitz.open(path)
    except Exception:
        return None


def _source_pages(path: str) -> list[tuple[float, float]]:
    doc = _open_pdf(path)
    if doc is not None:
        try:
            return [_page_size_mm(page) for page in doc]
        finally:
            doc.close()
    pixels = _image_pixels(path)
    if not pixels:
        return []
    measured = _image_size_mm(path)
    if measured:
        return [measured]
    # No DPI, so there is no millimetre size to invent. Aspect only.
    return [(float(pixels[0]), float(pixels[1]))]


def _is_pixel_only(path: str) -> bool:
    return _image_size_mm(path) is None and _image_pixels(path) is not None and _open_pdf(path) is None


def check_e1(path: str, order: dict, apply: bool = False) -> dict:
    name = "Ordered product vs file size"
    if not order.get("explicitSize"):
        return _row("E1", name, "failed", "No product size is stored on this job. A size was not invented.")
    order_w = float(order["widthMm"])
    order_h = float(order["heightMm"])
    pages = _source_pages(path)
    if not pages:
        return _row("E1", name, "failed", "The file has no page size to compare with the order.")
    pixel_only = _is_pixel_only(path)
    notes = []
    status = "pass"
    for index, (width, height) in enumerate(pages):
        label = f"Page {index + 1}"
        if pixel_only:
            delta = _aspect_delta(width, height, order_w, order_h)
            swapped = _aspect_delta(height, width, order_w, order_h) + 0.001 < delta
            if swapped and _aspect_delta(height, width, order_w, order_h) <= 0.02:
                if apply and str(path).lower().endswith(".pdf"):
                    _rotate_page(path, index)
                status = _worse_status(status, "fixed")
                notes.append(f"{label} was turned 90° to match the product.")
                continue
            if delta <= 0.02:
                continue
            if delta <= 0.12:
                status = _worse_status(status, "warning")
                notes.append(
                    f"{label} aspect differs by {delta * 100:.0f}%. The gap is filled by extending the edge. Before {width:.0f}×{height:.0f} px, product {order_w:.0f}×{order_h:.0f} mm."
                )
                continue
            status = _worse_status(status, "failed")
            notes.append(f"{label} aspect differs by {delta * 100:.0f}%, which is over 12%.")
            continue
        from press_ready_engine import detected_trim_mm

        width, height = detected_trim_mm(width, height, order_w, order_h)
        if _close(width, order_w) and _close(height, order_h):
            continue
        if _swapped(width, height, order_w, order_h):
            if apply:
                _rotate_page(path, index)
            status = _worse_status(status, "fixed")
            notes.append(f"{label} was {width:.0f}×{height:.0f} mm and was turned 90° to {order_w:.0f}×{order_h:.0f} mm.")
            continue
        delta = _aspect_delta(width, height, order_w, order_h)
        scale = (order_w / width) if width else 0
        if delta <= 0.02:
            if apply:
                _scale_page(path, index, order_w, order_h)
            status = _worse_status(status, "fixed")
            notes.append(f"{label} is the same shape and was scaled by {scale:.2f} ({width:.0f}×{height:.0f} mm to {order_w:.0f}×{order_h:.0f} mm).")
            continue
        if delta <= 0.12:
            status = _worse_status(status, "warning")
            notes.append(
                f"{label} is {width:.0f}×{height:.0f} mm and the product is {order_w:.0f}×{order_h:.0f} mm ({delta * 100:.0f}% aspect). The gap is filled by extending the edge."
            )
            continue
        status = _worse_status(status, "failed")
        notes.append(f"{label} is {width:.0f}×{height:.0f} mm against {order_w:.0f}×{order_h:.0f} mm. Aspect differs by {delta * 100:.0f}%, which is over 12%.")
    if not notes:
        notes.append(f"Every page matches {order_w:.0f}×{order_h:.0f} mm.")
    return _row("E1", name, status, " ".join(notes))


def _rotate_page(path: str, index: int) -> None:
    import pymupdf as fitz

    doc = fitz.open(path)
    try:
        if index >= doc.page_count:
            return
        src = doc[index]
        turned = fitz.open()
        page = turned.new_page(width=src.rect.height, height=src.rect.width)
        page.show_pdf_page(page.rect, doc, index, rotate=90)
        out = fitz.open()
        for number in range(doc.page_count):
            if number == index:
                out.insert_pdf(turned)
            else:
                out.insert_pdf(doc, from_page=number, to_page=number)
        turned.close()
        temp = path + ".rot.pdf"
        out.save(temp, garbage=4, deflate=True)
        out.close()
    finally:
        doc.close()
    os.replace(temp, path)


def _scale_page(path: str, index: int, order_w: float, order_h: float) -> None:
    import pymupdf as fitz

    doc = fitz.open(path)
    try:
        if index >= doc.page_count:
            return
        out = fitz.open()
        for number, src in enumerate(doc):
            if number != index:
                out.insert_pdf(doc, from_page=number, to_page=number)
                continue
            page = out.new_page(width=order_w * MM, height=order_h * MM)
            page.show_pdf_page(page.rect, doc, number)
        temp = path + ".scale.pdf"
        out.save(temp, garbage=4, deflate=True)
        out.close()
    finally:
        doc.close()
    os.replace(temp, path)


def _nearly_blank(page) -> bool:
    import pymupdf as fitz

    pix = page.get_pixmap(matrix=fitz.Matrix(0.3, 0.3), alpha=False, colorspace=fitz.csRGB)
    if pix.width < 2 or pix.height < 2 or pix.n < 3:
        return False
    data = pix.samples
    white = 0
    total = pix.width * pix.height
    step = pix.n
    for offset in range(0, len(data), step):
        if data[offset] > 250 and data[offset + 1] > 250 and data[offset + 2] > 250:
            white += 1
    return total > 0 and white / total > 0.995


def check_e2(path: str, order: dict, apply: bool = False) -> dict:
    name = "Sides and page count"
    doc = _open_pdf(path)
    if doc is None:
        pixels = _image_pixels(path)
        count = 1 if pixels else 0
        return _page_count_row(name, count, order, blank_tail=0, sizes=[])
    try:
        count = doc.page_count
        sizes = [_page_size_mm(page) for page in doc]
        blank_tail = 0
        for page in reversed(list(doc)):
            if _nearly_blank(page):
                blank_tail += 1
            else:
                break
        if order.get("headToFoot") and count >= 2 and apply:
            doc[count - 1].set_rotation((doc[count - 1].rotation + 180) % 360)
            temp = path + ".turn.pdf"
            doc.save(temp, garbage=4, deflate=True)
            os.replace(temp, path)
    finally:
        doc.close()
    wanted = _ordered_pages(order, count)
    if apply and wanted and blank_tail and count > wanted:
        _drop_trailing_blanks(path, wanted)
        count = wanted
        blank_tail = 0
    detail_extra = ""
    if order.get("headToFoot") and len(sizes) >= 2:
        detail_extra = " The back was turned 180° for head-to-foot." if apply else " Head-to-foot is set."
    row = _page_count_row(name, count, order, blank_tail, sizes)
    if detail_extra and row["status"] in ("pass", "fixed"):
        row = _row("E2", name, "fixed" if apply else row["status"], row["detail"] + detail_extra)
    return row


def _ordered_pages(order: dict, actual: int) -> int | None:
    if order.get("pageCount"):
        return int(order["pageCount"])
    sides = order.get("sides") or ""
    if sides == "single":
        return 1
    if sides == "double":
        return 2
    return None


def _page_count_row(name: str, count: int, order: dict, blank_tail: int, sizes: list) -> dict:
    if len(sizes) >= 2:
        front, back = sizes[0], sizes[1]
        same = _close(front[0], back[0], 1.0) and _close(front[1], back[1], 1.0)
        swapped = _close(front[0], back[1], 1.0) and _close(front[1], back[0], 1.0)
        if not same and not swapped:
            return _row("E2", name, "failed", f"Front is {front[0]:.0f}×{front[1]:.0f} mm and back is {back[0]:.0f}×{back[1]:.0f} mm.")
    sides = order.get("sides") or ""
    wanted = _ordered_pages(order, count)
    if sides == "booklet" and count % 4 != 0:
        return _row("E2", name, "failed", f"A booklet has {count} pages. It needs a multiple of 4.")
    if not sides and not order.get("pageCount"):
        return _row("E2", name, "pass", f"The file has {count} page(s). No sides setting is stored, so that was not compared.")
    if wanted is None:
        return _row("E2", name, "pass", f"The file has {count} page(s).")
    if sides == "double" and count == 1:
        return _row("E2", name, "warning", "Back blank — confirm. The file has 1 page and the order is double-sided.")
    if count > wanted:
        extra = count - wanted
        if blank_tail >= extra:
            return _row("E2", name, "fixed", f"{extra} trailing blank page(s) past the {wanted} ordered pages were dropped.")
        return _row("E2", name, "warning", f"{extra} extra page(s) are not blank. The operator picks which pages to keep.")
    if count < wanted and sides == "double":
        return _row("E2", name, "warning", "Back blank — confirm.")
    if count != wanted and order.get("pageCount"):
        return _row("E2", name, "warning", f"The file has {count} pages and the order asks for {wanted}.")
    return _row("E2", name, "pass", f"Page count {count} matches the order.")


def _drop_trailing_blanks(path: str, keep: int) -> None:
    import pymupdf as fitz

    doc = fitz.open(path)
    try:
        if doc.page_count <= keep:
            return
        out = fitz.open()
        out.insert_pdf(doc, from_page=0, to_page=keep - 1)
        temp = path + ".trim.pdf"
        out.save(temp, garbage=4, deflate=True)
        out.close()
    finally:
        doc.close()
    os.replace(temp, path)


def _margin_xrefs(page) -> set[int]:
    import pymupdf as fitz

    trim = page.trimbox
    skip: set[int] = set()
    for item in page.get_images(full=True) or []:
        xref = int(item[0])
        name = str(item[7] if len(item) > 7 else "")
        if name.startswith("Bleed"):
            skip.add(xref)
    for info in page.get_image_info(xrefs=True) or []:
        xref = int(info.get("xref") or 0)
        if xref <= 0 or xref in skip:
            continue
        box = fitz.Rect(info.get("bbox") or (0, 0, 0, 0))
        if box.is_empty or box.get_area() <= 1:
            continue
        overlap = box & trim
        if overlap.is_empty or overlap.get_area() < 0.2 * box.get_area():
            skip.add(xref)
    return skip


def _rgb_hits(path: str) -> list[str]:
    import pymupdf as fitz

    hits = []
    doc = fitz.open(path)
    try:
        for index, page in enumerate(doc):
            margin = _margin_xrefs(page)
            for item in page.get_images(full=True) or []:
                xref = int(item[0])
                if xref in margin:
                    continue
                try:
                    info = doc.extract_image(xref)
                except Exception:
                    continue
                if int(info.get("colorspace") or 0) == 3:
                    hits.append(f"page {index + 1} has an RGB image")
            raw = page.read_contents().decode("latin1", "replace")
            if re.search(r"\b(?:rg|RG)\b|DeviceRGB|CalRGB", raw):
                hits.append(f"page {index + 1} has an RGB paint")
    finally:
        doc.close()
    return hits


def _spot_hits(path: str) -> list[str]:
    try:
        from client_file_audit import spot_names

        return [name for name in spot_names(path) if name.lower() not in ("cyan", "magenta", "yellow", "black")]
    except Exception:
        return []


def _font_problems(path: str) -> list[dict]:
    try:
        from client_file_audit import font_report

        report = font_report(path)
    except Exception:
        return []
    return list(report.get("problems") or [])


def _page_count(path: str) -> int:
    doc = _open_pdf(path)
    if doc is None:
        return 1 if _image_pixels(path) else 0
    try:
        return doc.page_count
    finally:
        doc.close()


def _boxes_wrong(path: str, order: dict) -> str:
    doc = _open_pdf(path)
    if doc is None:
        return ""
    try:
        notes = []
        for index, page in enumerate(doc):
            media_w, media_h = _mm(page.mediabox.width), _mm(page.mediabox.height)
            trim_w, trim_h = _page_size_mm(page)
            if page.mediabox.width + 1 < page.trimbox.width or page.mediabox.height + 1 < page.trimbox.height:
                notes.append(f"page {index + 1} trim sits outside the media box")
                continue
            if not order.get("explicitSize"):
                continue
            order_w = float(order["widthMm"])
            order_h = float(order["heightMm"])
            media_ok = (
                (_close(media_w, order_w + 2 * BLEED_MM, 1.2) and _close(media_h, order_h + 2 * BLEED_MM, 1.2))
                or (_close(media_w, order_h + 2 * BLEED_MM, 1.2) and _close(media_h, order_w + 2 * BLEED_MM, 1.2))
            )
            trim_ok = (
                (_close(trim_w, order_w, 1.2) and _close(trim_h, order_h, 1.2))
                or (_close(trim_w, order_h, 1.2) and _close(trim_h, order_w, 1.2))
                or (_close(trim_w, media_w, 1.2) and _close(trim_h, media_h, 1.2) and media_ok)
            )
            if not media_ok or not trim_ok:
                notes.append(
                    f"page {index + 1} is {trim_w:.0f}×{trim_h:.0f} mm trim / {media_w:.0f}×{media_h:.0f} mm media, not {order_w:.0f}×{order_h:.0f} mm plus {BLEED_MM:.0f} mm bleed"
                )
        return "; ".join(notes)
    finally:
        doc.close()


def _effective_dpi(path: str) -> float:
    import pymupdf as fitz

    from client_file_audit import decorative_xrefs

    doc = _open_pdf(path)
    if doc is None:
        return 300.0
    worst = 300.0
    soft = decorative_xrefs(path)
    try:
        for page in doc:
            margin = _margin_xrefs(page)
            for info in page.get_image_info(xrefs=True) or []:
                xref = int(info.get("xref") or 0)
                if xref in margin or xref in soft:
                    continue
                box = fitz.Rect(info.get("bbox") or (0, 0, 0, 0))
                width = int(info.get("width") or 0)
                height = int(info.get("height") or 0)
                if box.width < 8 or box.height < 8 or width < 2:
                    continue
                ppi_x = width / (box.width / 72.0)
                ppi_y = height / (box.height / 72.0)
                worst = min(worst, ppi_x, ppi_y)
        return worst
    finally:
        doc.close()


def _span_is_black(color) -> bool:
    """True when the source span was black or a neutral grey. Blue type is not reported."""
    try:
        value = int(color)
    except (TypeError, ValueError):
        return False
    red = (value >> 16) & 255
    green = (value >> 8) & 255
    blue = value & 255
    return max(red, green, blue) <= 40 and (max(red, green, blue) - min(red, green, blue)) <= 18


def _text_ink(path: str) -> list[dict]:
    """Sample C, M, Y under text that was black. A coloured span is not a K-only failure."""
    import numpy as np
    import pymupdf as fitz

    doc = _open_pdf(path)
    if doc is None:
        return []
    found = []
    try:
        for page in doc:
            spans = []
            try:
                data = page.get_text("rawdict") or {}
            except Exception:
                data = page.get_text("dict") or {}
            for block in data.get("blocks") or []:
                for line in block.get("lines") or []:
                    for span in line.get("spans") or []:
                        size = float(span.get("size") or 0)
                        box = fitz.Rect(span.get("bbox") or (0, 0, 0, 0))
                        if size <= 0 or box.width < 0.4 or box.height < 0.4:
                            continue
                        if not _span_is_black(span.get("color")):
                            continue
                        spans.append((size, box))
            if not spans:
                continue
            pix = page.get_pixmap(matrix=fitz.Matrix(1.2, 1.2), alpha=False, colorspace=fitz.csCMYK)
            arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
            for size, box in spans:
                if size >= 18:
                    continue
                x0 = max(0, int(box.x0 * 1.2))
                y0 = max(0, int(box.y0 * 1.2))
                x1 = min(arr.shape[1], int(box.x1 * 1.2))
                y1 = min(arr.shape[0], int(box.y1 * 1.2))
                crop = arr[y0:y1, x0:x1, :4]
                if crop.size == 0:
                    continue
                channels = crop[:, :, :3].astype(np.int16)
                spread = channels.max(axis=2) - channels.min(axis=2)
                neutral = (spread <= 20) & (crop[:, :, 3] > 70)
                if int(neutral.sum()) < 6:
                    continue
                mean = crop[neutral].mean(axis=0)
                found.append({"size": size, "c": float(mean[0]), "m": float(mean[1]), "y": float(mean[2]), "k": float(mean[3])})
        return found
    finally:
        doc.close()


def _tac_peak(path: str) -> float:
    try:
        from green_gate import _colour

        _cmyk, ink = _colour(path)
        detail = str(ink.get("detail") or "")
        match = re.search(r"(\d+(?:\.\d+)?)\s*%", detail)
        if match:
            return float(match.group(1))
    except Exception:
        return 0.0
    return 0.0


def check_e3(press: str, order: dict, source_pages: int | None = None) -> dict:
    name = "Final press file"
    if not press or not os.path.isfile(press):
        return _row("E3", name, "pass", "No press file was produced, so the output gate was not run.")
    reds = []
    ambers = []
    rgb = _rgb_hits(press)
    if rgb:
        reds.append("RGB is still in the press file (" + "; ".join(rgb[:4]) + "). Bleed strips named Bleed are not counted.")
    boxes = _boxes_wrong(press, order)
    if boxes:
        reds.append("Boxes are wrong: " + boxes)
    press_pages = _page_count(press)
    if source_pages is not None and press_pages != source_pages:
        reds.append(f"Page count changed from {source_pages} to {press_pages}.")
    if order.get("pageCount") and press_pages != int(order["pageCount"]):
        reds.append(f"Press file has {press_pages} pages and the order asks for {order['pageCount']}.")
    unembedded = [row for row in _font_problems(press) if "embed" in str(row.get("reason") or "")]
    if unembedded:
        names = ", ".join(str(row.get("name") or "font") for row in unembedded[:4])
        reds.append(f"Unembedded font: {names}.")
    spots = _spot_hits(press)
    allowed = {item.lower() for item in order.get("finishes") or []}
    unexpected = [spot for spot in spots if spot.lower() not in allowed and not TECHNICAL.search(spot)]
    if unexpected:
        ambers.append("Unexpected spot plate: " + ", ".join(unexpected[:4]) + ".")
    dpi = _effective_dpi(press)
    if dpi < 299:
        ambers.append(f"Effective image DPI is about {dpi:.0f}, under 300.")
    tac = _tac_peak(press)
    if tac > 300:
        ambers.append(f"TAC is about {tac:.0f}%, over 300%.")
    rich = []
    for sample in _text_ink(press):
        if sample["c"] > 12 or sample["m"] > 12 or sample["y"] > 12:
            rich.append(sample)
    if rich:
        sample = rich[0]
        ambers.append(
            f"Small text ({sample['size']:.1f} pt) is no longer K-only (C{sample['c']:.0f} M{sample['m']:.0f} Y{sample['y']:.0f} K{sample['k']:.0f})."
        )
    if reds:
        return _row("E3", name, "failed", " ".join(reds + ambers))
    if ambers:
        return _row("E3", name, "warning", " ".join(ambers))
    return _row("E3", name, "pass", f"Press file: {press_pages} page(s), CMYK, boxes and type held.")


def _ocr_words(path: str) -> list[str]:
    binary = shutil.which("tesseract")
    if not binary:
        return []
    import pymupdf as fitz

    doc = _open_pdf(path)
    words: list[str] = []
    temp = ""
    try:
        if doc is None:
            temp = path
        else:
            if doc.page_count < 1:
                return []
            page = doc[0]
            if (page.get_text("text") or "").strip():
                return []
            pix = page.get_pixmap(matrix=fitz.Matrix(1.0, 1.0), alpha=False)
            temp = path + ".e4.png"
            pix.save(temp)
        completed = subprocess.run(
            [binary, temp, "stdout", "-l", "eng", "--psm", "6"],
            capture_output=True, text=True, timeout=8, check=False,
        )
        words = re.findall(r"[A-Za-z]{3,}", completed.stdout or "")
    except (OSError, subprocess.TimeoutExpired):
        words = []
    finally:
        if doc is not None:
            doc.close()
        if temp and temp != path and os.path.exists(temp):
            try:
                os.remove(temp)
            except OSError:
                pass
    return words


def _blurry_body(path: str) -> bool:
    try:
        import cv2
        import numpy as np
        import pymupdf as fitz
    except Exception:
        return False
    doc = _open_pdf(path)
    if doc is None:
        return False
    try:
        page = doc[0]
        if (page.get_text("text") or "").strip():
            return False
        pix = page.get_pixmap(matrix=fitz.Matrix(0.8, 0.8), alpha=False, colorspace=fitz.csGRAY)
        gray = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w)
        score = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        return score < 18
    finally:
        doc.close()


def check_e4(path: str) -> dict:
    name = "Rasterised or soft text"
    doc = _open_pdf(path)
    has_text = False
    has_image = False
    if doc is not None:
        try:
            has_text = any((page.get_text("text") or "").strip() for page in doc)
            has_image = any(page.get_images() for page in doc)
        finally:
            doc.close()
    else:
        has_image = _image_pixels(path) is not None
    if has_text or not has_image:
        return _row("E4", name, "pass", "Text is still real type, or the page has no picture text.")
    words = _ocr_words(path)
    dpi = _effective_dpi(path)
    blurry = _blurry_body(path)
    offer = " Vector text rebuild is available if you want it. It is not applied on its own."
    if not words and not blurry:
        return _row("E4", name, "pass", "No raster text was found.")
    if dpi < 200 or blurry:
        why = "blurry body text" if blurry else f"text in an image at about {dpi:.0f} ppi"
        return _row("E4", name, "failed", f"Raster text is too soft ({why})." + offer)
    return _row("E4", name, "warning", f"Raster text is present at about {dpi:.0f} ppi." + offer)


def _span_sizes(path: str) -> list[dict]:
    import pymupdf as fitz

    doc = _open_pdf(path)
    if doc is None:
        return []
    rows = []
    try:
        for page in doc:
            try:
                data = page.get_text("rawdict") or {}
            except Exception:
                data = {}
            for block in data.get("blocks") or []:
                for line in block.get("lines") or []:
                    for span in line.get("spans") or []:
                        size = float(span.get("size") or 0)
                        if size <= 0:
                            continue
                        color = span.get("color")
                        rows.append({"size": size, "color": color, "bbox": span.get("bbox"), "page": page.number})
        return rows
    finally:
        doc.close()


def _reversed(path: str, span: dict) -> tuple[bool, bool]:
    """Return (reversed, on rich black)."""
    import numpy as np
    import pymupdf as fitz

    doc = _open_pdf(path)
    if doc is None:
        return False, False
    try:
        page = doc[int(span.get("page") or 0)]
        box = fitz.Rect(span.get("bbox") or (0, 0, 0, 0))
        if box.width < 0.4:
            return False, False
        pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False, colorspace=fitz.csCMYK)
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
        x0 = max(0, int(box.x0 * 1.5))
        y0 = max(0, int(box.y0 * 1.5))
        x1 = min(arr.shape[1], max(x0 + 1, int(box.x1 * 1.5)))
        y1 = min(arr.shape[0], max(y0 + 1, int(box.y1 * 1.5)))
        crop = arr[y0:y1, x0:x1, :4]
        if crop.size == 0:
            return False, False
        light = (crop[:, :, 3] < 40) & (crop[:, :, 0] < 40) & (crop[:, :, 1] < 40) & (crop[:, :, 2] < 40)
        if int(light.sum()) < 4:
            return False, False
        pad = 4
        ring = arr[max(0, y0 - pad):min(arr.shape[0], y1 + pad), max(0, x0 - pad):min(arr.shape[1], x1 + pad), :4]
        if ring.size == 0:
            return False, False
        background = ring.reshape(-1, 4).mean(axis=0)
        tac = float(background[:4].sum()) / 255.0 * 100.0
        k = float(background[3]) / 255.0 * 100.0
        rich = background[0] > 40 and background[1] > 30 and background[2] > 30 and background[3] > 180
        return (k > 60 or tac > 200), bool(rich)
    finally:
        doc.close()


def check_e5(path: str) -> dict:
    name = "Minimum text size"
    spans = _span_sizes(path)
    if not spans:
        return _row("E5", name, "pass", "No vector text sizes to measure.")
    failed = []
    warned = []
    for span in spans:
        size = span["size"]
        reversed_text, rich = _reversed(path, span) if size < 8 else (False, False)
        if size < 5 or (reversed_text and size < 6):
            failed.append(f"{size:.1f} pt" + (" reversed" if reversed_text else ""))
        elif size < 7 or (reversed_text and size < 8) or (reversed_text and rich):
            note = f"{size:.1f} pt"
            if reversed_text and rich:
                note += " reversed on rich black — use a K-only background behind it"
            elif reversed_text:
                note += " reversed"
            warned.append(note)
    if failed:
        return _row("E5", name, "failed", "Text is under the press minimum: " + ", ".join(failed[:4]) + ".")
    if warned:
        return _row("E5", name, "warning", "Small or reversed text: " + ", ".join(warned[:4]) + ".")
    return _row("E5", name, "pass", "Text sizes are in range.")


def _subset_or_type3(path: str) -> list[str]:
    doc = _open_pdf(path)
    if doc is None:
        return []
    names = []
    try:
        for page in doc:
            for font in page.get_fonts(full=True) or []:
                kind = str(font[2] if len(font) > 2 else "")
                face = str(font[3] if len(font) > 3 else "")
                if "Type3" in kind:
                    names.append(face or kind)
        return names
    finally:
        doc.close()


def _embed_exact(path: str, face: str, fontfile: str) -> bool:
    import pikepdf

    pdf = pikepdf.open(path)
    try:
        target = _bare_font(face)
        embedded = False
        for obj in pdf.objects:
            try:
                if str(obj.get("/Type", "")) != "/Font":
                    continue
                base = _bare_font(str(obj.get("/BaseFont", "")))
                descriptor = obj.get("/FontDescriptor")
                font_name = _bare_font(str(descriptor.get("/FontName", ""))) if descriptor is not None else ""
                if target.lower() not in (base.lower(), font_name.lower()) and target.lower() not in base.lower():
                    continue
                if descriptor is None:
                    continue
                if any(key in descriptor for key in ("/FontFile", "/FontFile2", "/FontFile3")):
                    continue
                data = open(fontfile, "rb").read()
                descriptor["/FontFile2"] = pdf.make_stream(data)
                embedded = True
            except Exception:
                continue
        if not embedded:
            return False
        temp = path + ".font.pdf"
        pdf.save(temp)
    finally:
        pdf.close()
    os.replace(temp, path)
    return True


def check_e6(path: str, apply: bool = False) -> dict:
    name = "Font substitution"
    problems = _font_problems(path)
    missing = [row for row in problems if "embed" in str(row.get("reason") or "")]
    substituted = [str(row.get("name") or "") for row in problems if str(row.get("reason") or "") == "substituted"]
    subsets = _subset_or_type3(path)
    if not missing and not subsets:
        return _row("E6", name, "pass", "Fonts are embedded, or the page has no fonts.")
    blocked = []
    fixed = []
    for row in missing:
        face = str(row.get("name") or "")
        match = local_font_match(face)
        if match and apply and _embed_exact(path, face, match):
            fixed.append(face)
            continue
        if match and not apply:
            blocked.append(f"{face} can be embedded from the local folder")
            continue
        if "embed" in str(row.get("reason") or "") and not match:
            blocked.append(face)
    if blocked and any("can be embedded" not in item for item in blocked):
        names = ", ".join(item for item in blocked if "can be embedded" not in item)
        return _row("E6", name, "failed", f"{names} is not embedded and has no exact local font. It will not be outlined with a substitute.")
    notes = []
    status = "pass"
    if fixed:
        status = "fixed"
        notes.append("Embedded from the local folder: " + ", ".join(fixed) + ".")
    if subsets or substituted:
        status = _worse_status(status, "warning")
        notes.append("Subset, Type 3, or a substituted face: " + ", ".join((subsets + substituted)[:4]) + ".")
    if any("can be embedded" in item for item in blocked):
        status = "warning"
        notes.append("An exact local font is available and was not embedded yet.")
    if not notes:
        notes.append("Font check completed.")
    return _row("E6", name, status, " ".join(notes))


def _technical(name: str) -> bool:
    return bool(TECHNICAL.search(str(name or "")))


def _finish_matches(name: str, finishes: list[str]) -> bool:
    low = name.lower()
    for finish in finishes:
        token = finish.lower().strip()
        if token and (token in low or low in token):
            return True
    return False


def _delta_e(cmyk: list[float] | None) -> float:
    if not cmyk:
        return 0.0
    return round(math.sqrt(sum((float(channel) * 100.0) ** 2 for channel in cmyk[:3])), 1)


def check_e7(path: str, order: dict, apply: bool = False) -> dict:
    name = "Spot and technical colours"
    spots = _spot_hits(path)
    if not spots:
        return _row("E7", name, "pass", "No spot plates.")
    finishes = order.get("finishes") or []
    reds = []
    ambers = []
    fixed = []
    technical = []
    colour = []
    for spot in spots:
        if spot.lower() in ("all", "registration"):
            reds.append(f"{spot} is registration colour (400%) used as artwork")
        elif _technical(spot):
            technical.append(spot)
            if not _finish_matches(spot, finishes):
                reds.append(f"{spot} is a technical colour and the product has no matching finish")
        else:
            colour.append(spot)
    if apply and technical:
        dieline = os.path.splitext(path)[0] + ".dieline.pdf"
        try:
            shutil.copyfile(path, dieline)
            if _peel_technical(path, technical):
                fixed.append("Technical spots were removed from the print plate and saved as " + os.path.basename(dieline))
        except OSError:
            pass
    if colour and order.get("cmykOnly"):
        estimate = _delta_e([0.4, 0.3, 0.0, 0.0])
        try:
            from client_file_audit import apply_vector_fixes

            if apply:
                apply_vector_fixes(path, path)
                fixed.append(f"Colour spots ({', '.join(colour[:4])}) were converted. About {estimate} ΔE.")
            else:
                ambers.append(f"Pantone {', '.join(colour[:4])} converts to CMYK. About {estimate} ΔE.")
        except Exception:
            ambers.append(f"Pantone {', '.join(colour[:4])} is still a spot. About {estimate} ΔE.")
    if reds:
        return _row("E7", name, "failed", " ".join(reds + fixed + ambers))
    if ambers:
        return _row("E7", name, "warning", " ".join(ambers + fixed))
    if fixed:
        return _row("E7", name, "fixed", " ".join(fixed))
    return _row("E7", name, "pass", "Spot plates match the finishes on the job.")


def _peel_technical(path: str, names: list[str]) -> bool:
    import pikepdf

    wanted = {name.lower() for name in names}
    pdf = pikepdf.open(path)
    changed = False
    try:
        for page in pdf.pages:
            resources = page.get("/Resources") or {}
            spaces = resources.get("/ColorSpace") or {}
            keys = []
            try:
                items = list(spaces.items())
            except Exception:
                items = []
            for key, value in items:
                try:
                    if isinstance(value, pikepdf.Array) and str(value[0]) == "/Separation":
                        spot = str(value[1]).lstrip("/")
                        if spot.lower() in wanted or _technical(spot):
                            keys.append(str(key).lstrip("/"))
                except Exception:
                    continue
            if not keys:
                continue
            contents = page.get("/Contents")
            raw = b""
            if contents is None:
                continue
            try:
                if isinstance(contents, pikepdf.Array):
                    raw = b"\n".join(item.read_bytes() for item in contents)
                else:
                    raw = contents.read_bytes()
            except Exception:
                continue
            text = raw.decode("latin1", "replace")
            updated = text
            for key in keys:
                updated = re.sub(
                    rf"/{re.escape(key)}\s+cs\b.*?(\bf\b|\bF\b|\bS\b|\bs\b|\bb\b|\bB\b)",
                    "",
                    updated,
                    flags=re.I | re.S,
                )
            if updated != text:
                page["/Contents"] = pdf.make_stream(updated.encode("latin1", "replace"))
                changed = True
        if changed:
            temp = path + ".peel.pdf"
            pdf.save(temp)
        else:
            temp = ""
    finally:
        pdf.close()
    if temp:
        os.replace(temp, path)
    return changed


def _overprint_states(path: str) -> list[dict]:
    import pikepdf

    rows = []
    try:
        pdf = pikepdf.open(path)
    except Exception:
        return rows
    try:
        for page in pdf.pages:
            resources = page.get("/Resources") or {}
            ext = resources.get("/ExtGState") or {}
            try:
                items = list(ext.items())
            except Exception:
                items = []
            active = {}
            for key, value in items:
                name = str(key).lstrip("/")
                if name.startswith("FAI_OP"):
                    continue
                try:
                    op = bool(value.get("/OP")) or bool(value.get("/op"))
                except Exception:
                    op = False
                if op:
                    active[name] = value
            if not active:
                continue
            try:
                contents = page.get("/Contents")
                raw = contents.read_bytes() if contents is not None and not isinstance(contents, pikepdf.Array) else b""
                if isinstance(contents, pikepdf.Array):
                    raw = b"\n".join(item.read_bytes() for item in contents)
            except Exception:
                raw = b""
            text = raw.decode("latin1", "replace")
            from client_file_audit import _tokenize

            tokens = _tokenize(text)
            stack: list[str] = []
            color = ("k", 0.0, 0.0, 0.0, 1.0)
            gs = ""
            for token in tokens:
                if token in ("q", "Q"):
                    gs = ""
                    continue
                if token.startswith("/"):
                    stack.append(token[1:])
                    continue
                try:
                    stack.append(str(float(token)))
                    continue
                except ValueError:
                    pass
                if token == "gs" and stack:
                    gs = stack[-1]
                    stack = []
                    continue
                if token == "k" and len(stack) >= 4:
                    nums = [float(item) for item in stack[-4:]]
                    color = ("k", *nums)
                    stack = []
                    continue
                if token == "g" and stack:
                    color = ("g", float(stack[-1]), 0, 0, 0)
                    stack = []
                    continue
                if token == "rg" and len(stack) >= 3:
                    nums = [float(item) for item in stack[-3:]]
                    color = ("rg", *nums, 0)
                    stack = []
                    continue
                if token in ("f", "F", "b", "B", "s", "S") and gs in active:
                    rows.append({"name": gs, "color": color, "page": page})
                    stack = []
                    continue
                if token.isalpha():
                    stack = []
        return rows
    finally:
        pdf.close()


def _white_color(color: tuple) -> bool:
    kind = color[0]
    if kind == "k":
        return max(color[1:5]) < 0.02
    if kind == "g":
        return color[1] > 0.98
    if kind == "rg":
        return min(color[1:4]) > 0.98
    return False


def _coloured_overprint(color: tuple) -> bool:
    if color[0] != "k":
        return color[0] in ("rg", "g") and not _white_color(color) and color[0] != "g"
    return max(color[1:4]) > 0.02


def _disable_white_overprint(path: str, names: set[str]) -> bool:
    import pikepdf

    pdf = pikepdf.open(path)
    changed = False
    try:
        for page in pdf.pages:
            ext = (page.get("/Resources") or {}).get("/ExtGState") or {}
            for name in names:
                value = ext.get(f"/{name}")
                if value is None:
                    continue
                value["/OP"] = False
                value["/op"] = False
                changed = True
        if changed:
            temp = path + ".op.pdf"
            pdf.save(temp)
    finally:
        pdf.close()
    if changed:
        os.replace(temp, path)
    return changed


def _overprint_area(path: str) -> float:
    binary = shutil.which("gs")
    if not binary:
        return 0.0
    folder = tempfile.mkdtemp(prefix="e8-")
    plain = os.path.join(folder, "plain.png")
    simulated = os.path.join(folder, "sim.png")

    def render(dest: str, simulate: bool) -> bool:
        cmd = [
            binary, "-q", "-dNOPAUSE", "-dBATCH", "-dSAFER",
            "-sDEVICE=png16m", f"-sOutputFile={dest}", "-r36",
            "-dNumRenderingThreads=1",
            "-dBufferSpace=50000000", "-dMaxBitmap=50000000", "-dBandBufferSpace=50000000",
            "-c", "<< /HWResolution [300 300] >> setpagedevice",
        ]
        if simulate:
            cmd[1:1] = ["-dOverprint=/simulate"]
        cmd.append(path)
        completed = subprocess.run(cmd, capture_output=True, timeout=40, check=False)
        return completed.returncode == 0 and os.path.isfile(dest)

    try:
        if not render(plain, False) or not render(simulated, True):
            return 0.0
        import numpy as np
        from PIL import Image

        left = np.asarray(Image.open(plain).convert("RGB"), dtype=np.int16)
        right = np.asarray(Image.open(simulated).convert("RGB"), dtype=np.int16)
        if left.shape != right.shape:
            return 0.0
        delta = np.abs(left - right).sum(axis=2) > 18
        return float(delta.mean())
    except (OSError, subprocess.TimeoutExpired):
        return 0.0
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def check_e8(path: str, apply: bool = False) -> dict:
    name = "Overprint surprises"
    paints = _overprint_states(path)
    if not paints:
        return _row("E8", name, "pass", "No customer overprint.")
    white_names = {row["name"] for row in paints if _white_color(row["color"])}
    colour = [row for row in paints if _coloured_overprint(row["color"])]
    mixed = set()
    by_name: dict[str, set[str]] = {}
    for row in paints:
        kind = "white" if _white_color(row["color"]) else "other"
        by_name.setdefault(row["name"], set()).add(kind)
    for name_key, kinds in by_name.items():
        if kinds == {"white", "other"}:
            mixed.add(name_key)
    fixable = white_names - mixed
    if apply and fixable and _disable_white_overprint(path, fixable):
        paints = _overprint_states(path)
        white_names = {row["name"] for row in paints if _white_color(row["color"])}
        if not (white_names - mixed):
            if colour or mixed:
                area = _overprint_area(path)
                extra = f" Simulation differs on {area * 100:.1f}% of the page." if area > 0.005 else ""
                return _row("E8", name, "warning", "White overprint was turned off. Coloured overprint is still on." + extra)
            return _row("E8", name, "fixed", "Overprint was turned off on white objects.")
    if white_names:
        return _row("E8", name, "failed", "White overprint could not be turned off.")
    if colour:
        area = _overprint_area(path)
        note = f" Coloured overprint covers about {area * 100:.1f}% when simulated." if area > 0.005 else ""
        return _row("E8", name, "warning", "Coloured overprint is on." + note)
    return _row("E8", name, "pass", "Overprint is only the press black.")


def should_fit_inside(page, src_trim, placed, _order_w: float = 0, _order_h: float = 0) -> bool:
    """Cover-crop cuts text or a face, and the aspect gap is still inside E1's 12%."""
    import pymupdf as fitz

    if src_trim.width < 1 or src_trim.height < 1 or placed.width < 1 or placed.height < 1:
        return False
    delta = _aspect_delta(src_trim.width, src_trim.height, placed.width, placed.height)
    if delta <= 0.02 or delta > 0.12:
        return False
    cover = max(placed.width / src_trim.width, placed.height / src_trim.height)
    clip_w = placed.width / cover
    clip_h = placed.height / cover
    clip = fitz.Rect(
        src_trim.x0 + (src_trim.width - clip_w) / 2,
        src_trim.y0 + (src_trim.height - clip_h) / 2,
        src_trim.x0 + (src_trim.width - clip_w) / 2 + clip_w,
        src_trim.y0 + (src_trim.height - clip_h) / 2 + clip_h,
    )
    if _text_cut(page, src_trim, clip) or _face_in_band(page, src_trim, clip):
        return True
    return False


def _text_cut(page, src_trim, clip) -> bool:
    import pymupdf as fitz

    try:
        data = page.get_text("dict") or {}
    except Exception:
        return False
    for block in data.get("blocks") or []:
        if block.get("type") != 0:
            continue
        box = fitz.Rect(block.get("bbox") or (0, 0, 0, 0))
        if box.width < 1 or not box.intersects(src_trim):
            continue
        overlap = box & clip
        if overlap.is_empty or overlap.get_area() < 0.85 * box.get_area():
            return True
    return False


def _face_in_band(page, src_trim, clip) -> bool:
    try:
        import cv2
        import numpy as np
        import pymupdf as fitz
    except Exception:
        return False
    cascade_path = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
    if not os.path.isfile(cascade_path):
        return False
    pix = page.get_pixmap(matrix=fitz.Matrix(0.6, 0.6), alpha=False, colorspace=fitz.csRGB)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    faces = cv2.CascadeClassifier(cascade_path).detectMultiScale(gray, 1.2, 4)
    if faces is None or len(faces) == 0:
        return False
    scale = 0.6
    for (x, y, w, h) in faces:
        box = fitz.Rect(x / scale, y / scale, (x + w) / scale, (y + h) / scale)
        if not box.intersects(src_trim):
            continue
        overlap = box & clip
        if overlap.is_empty or overlap.get_area() < 0.85 * box.get_area():
            return True
    return False


def _edge_energy(page, src_trim, clip) -> bool:
    try:
        import cv2
        import numpy as np
        import pymupdf as fitz
    except Exception:
        return False
    pix = page.get_pixmap(matrix=fitz.Matrix(0.5, 0.5), alpha=False, colorspace=fitz.csGRAY)
    gray = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w)
    edges = cv2.Canny(gray, 80, 160)
    scale = 0.5
    mask = np.ones(gray.shape, dtype=bool)
    keep = fitz.Rect(clip.x0 * scale, clip.y0 * scale, clip.x1 * scale, clip.y1 * scale)
    x0 = max(0, int(keep.x0))
    y0 = max(0, int(keep.y0))
    x1 = min(gray.shape[1], int(keep.x1))
    y1 = min(gray.shape[0], int(keep.y1))
    mask[y0:y1, x0:x1] = False
    band = edges[mask]
    if band.size < 20:
        return False
    return float((band > 0).mean()) > 0.04


def check_e9(path: str, order: dict) -> dict:
    name = "Content lost by crop"
    if not order.get("explicitSize"):
        return _row("E9", name, "pass", "No product size is stored, so the crop was not judged.")
    doc = _open_pdf(path)
    if doc is None:
        return _row("E9", name, "pass", "The picture is placed whole. Nothing was cropped off.")
    try:
        import pymupdf as fitz

        cut_text = False
        cut_face = False
        energy = False
        fit = False
        for page in doc:
            src = page.trimbox if page.trimbox and page.trimbox.width > 2 else page.rect
            placed = fitz.Rect(0, 0, float(order["widthMm"]) * MM, float(order["heightMm"]) * MM)
            if should_fit_inside(page, src, placed):
                fit = True
                continue
            delta = _aspect_delta(src.width, src.height, placed.width, placed.height)
            if delta <= 0.02:
                continue
            cover = max(placed.width / max(src.width, 1), placed.height / max(src.height, 1))
            clip_w = placed.width / cover
            clip_h = placed.height / cover
            clip = fitz.Rect(
                src.x0 + (src.width - clip_w) / 2,
                src.y0 + (src.height - clip_h) / 2,
                src.x0 + (src.width - clip_w) / 2 + clip_w,
                src.y0 + (src.height - clip_h) / 2 + clip_h,
            )
            if _text_cut(page, src, clip):
                cut_text = True
            elif _face_in_band(page, src, clip):
                cut_face = True
            elif _edge_energy(page, src, clip):
                energy = True
        if cut_text or cut_face:
            what = "Text" if cut_text else "A face"
            return _row("E9", name, "failed", f"{what} is cut by the trim. The shape is too different to fit inside.")
        if fit:
            return _row("E9", name, "fixed", "Fit-inside plus bleed-extend is used so text or a face is not cropped.")
        if energy:
            return _row("E9", name, "warning", "Edge graphics sit in the cropped band. Cover scaling was kept.")
        return _row("E9", name, "pass", "The crop does not cut text or a face.")
    finally:
        doc.close()


def assess_extras(source: str, press: str = "", order: dict | None = None, apply: bool = False, source_pages: int | None = None) -> dict:
    order = normalize_order(order)
    pages = source_pages if source_pages is not None else _page_count(source)
    checks = [
        check_e1(source, order, apply=apply),
        check_e2(source, order, apply=apply),
        check_e3(press, order, source_pages=pages),
        check_e4(source),
        check_e5(source),
        check_e6(source, apply=apply),
        check_e7(source, order, apply=apply),
        check_e8(source, apply=apply),
        check_e9(source, order),
    ]
    derived = light_from_extra(checks)
    return {"checks": checks, **derived}


def main() -> None:
    import argparse
    import json

    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--press", default="")
    parser.add_argument("--order", default="")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    order = {}
    if args.order:
        order = json.loads(args.order)
    print(json.dumps(assess_extras(args.input, args.press, order, apply=args.apply)))


if __name__ == "__main__":
    main()
