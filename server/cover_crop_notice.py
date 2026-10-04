"""What cover-scale will trim off before the customer approves the page."""

import os

import cv2
import numpy as np

from ai_artwork import FLAT_EDGE_STD, cover_crop_box, edge_std, sample_edge_cmyk

SAFE_ZONE_MM = 3.0
CONTENT_STD = 14.0


def _std(strip: np.ndarray) -> float:
    if strip is None or getattr(strip, "size", 0) == 0:
        return 0.0
    gray = cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY) if strip.ndim == 3 else strip
    return float(np.std(gray))


def cover_trim_amounts(src_w: int, src_h: int, trim_w_mm: float, trim_h_mm: float) -> dict:
    """Millimetres cover-scale removes from each side of the print."""
    box = cover_crop_box(src_w, src_h, trim_w_mm, trim_h_mm, 0.5)
    left = right = top = bottom = 0.0
    if box.get("axis") == "x" and box["w"] > 0:
        scale = float(trim_w_mm) / float(box["w"])
        left = box["x"] * scale
        right = (src_w - box["x"] - box["w"]) * scale
    elif box.get("axis") == "y" and box["h"] > 0:
        scale = float(trim_h_mm) / float(box["h"])
        top = box["y"] * scale
        bottom = (src_h - box["y"] - box["h"]) * scale
    amounts = {
        "left": round(left, 1),
        "right": round(right, 1),
        "top": round(top, 1),
        "bottom": round(bottom, 1),
    }
    amounts["cropped"] = max(amounts.values()) >= 0.2
    amounts["box"] = box
    return amounts


def _content_flags(bgr: np.ndarray, amounts: dict, trim_w_mm: float, trim_h_mm: float) -> dict:
    box = amounts["box"]
    x, y, cw, ch = int(box["x"]), int(box["y"]), int(box["w"]), int(box["h"])
    height, width = bgr.shape[:2]
    strips = []
    if y > 0:
        strips.append(bgr[0:y, :])
    if y + ch < height:
        strips.append(bgr[y + ch : height, :])
    if x > 0:
        strips.append(bgr[y : y + ch, 0:x])
    if x + cw < width:
        strips.append(bgr[y : y + ch, x + cw : width])
    content_trimmed = any(_std(strip) > CONTENT_STD for strip in strips)

    kept = bgr[y : y + ch, x : x + cw]
    safe_x = max(1, int(round(SAFE_ZONE_MM / float(trim_w_mm) * cw))) if trim_w_mm else 1
    safe_y = max(1, int(round(SAFE_ZONE_MM / float(trim_h_mm) * ch))) if trim_h_mm else 1
    ring = []
    if kept.size:
        ring.extend([kept[:safe_y, :], kept[-safe_y:, :], kept[:, :safe_x], kept[:, -safe_x:]])
    content_outside_safe = any(_std(part) > CONTENT_STD for part in ring)
    return {
        "contentInTrim": bool(content_trimmed),
        "contentOutsideSafe": bool(content_outside_safe),
    }


def describe_trim(amounts: dict, flags: dict) -> dict:
    parts = []
    for side in ("left", "right", "top", "bottom"):
        mm = amounts[side]
        if mm >= 0.2:
            parts.append(f"{mm:g} mm off the {side}")
    if parts:
        summary = "Filling the page will trim " + " and ".join(parts) + "."
    else:
        summary = "The artwork already fills this page shape. Nothing extra is trimmed off."
    warning = ""
    if flags["contentInTrim"]:
        warning = "Text or a logo in the shaded area will be cut off."
    elif flags["contentOutsideSafe"] and amounts["cropped"]:
        warning = "Something important sits outside the safe zone, closer than 3 mm to the cut."
    return {"summary": summary, "warning": warning}


def recommend_whole_artwork(bgr: np.ndarray) -> dict:
    """Fit the whole picture by extending the edge or adding a colour border. No white bars."""
    cmyk = sample_edge_cmyk(bgr)
    edge = {"c": cmyk[0], "m": cmyk[1], "y": cmyk[2], "k": cmyk[3]}
    if edge_std(bgr) <= FLAT_EDGE_STD:
        method = "border"
        note = "The whole artwork is kept. The gaps are filled with a colour border matched to the edge."
    else:
        method = "extend"
        note = "The whole artwork is kept. The gaps are filled by extending the background."
    return {"method": method, "edge": edge, "note": note}


def render_cover_preview(bgr: np.ndarray, trim_w_mm: float, trim_h_mm: float, dest: str) -> dict:
    amounts = cover_trim_amounts(bgr.shape[1], bgr.shape[0], trim_w_mm, trim_h_mm)
    flags = _content_flags(bgr, amounts, trim_w_mm, trim_h_mm)
    text = describe_trim(amounts, flags)
    box = amounts["box"]
    preview = bgr[:, :, :3].copy() if bgr.ndim == 3 else cv2.cvtColor(bgr, cv2.COLOR_GRAY2BGR)
    height, width = preview.shape[:2]
    long_edge = max(height, width, 1)
    if long_edge > 900:
        scale = 900.0 / float(long_edge)
        preview = cv2.resize(
            preview,
            (max(1, int(round(width * scale))), max(1, int(round(height * scale)))),
            interpolation=cv2.INTER_AREA,
        )
        box = {
            **box,
            "x": int(round(box["x"] * scale)),
            "y": int(round(box["y"] * scale)),
            "w": int(round(box["w"] * scale)),
            "h": int(round(box["h"] * scale)),
        }
        height, width = preview.shape[:2]

    x, y, cw, ch = int(box["x"]), int(box["y"]), int(box["w"]), int(box["h"])
    x2, y2 = min(width, x + cw), min(height, y + ch)
    overlay = preview.copy()
    if y > 0:
        cv2.rectangle(overlay, (0, 0), (width, y), (0, 0, 180), -1)
    if y2 < height:
        cv2.rectangle(overlay, (0, y2), (width, height), (0, 0, 180), -1)
    if x > 0:
        cv2.rectangle(overlay, (0, y), (x, y2), (0, 0, 180), -1)
    if x2 < width:
        cv2.rectangle(overlay, (x2, y), (width, y2), (0, 0, 180), -1)
    preview = cv2.addWeighted(overlay, 0.45, preview, 0.55, 0)
    cv2.rectangle(preview, (x, y), (max(x, x2 - 1), max(y, y2 - 1)), (0, 0, 255), 2)

    safe_x = max(1, int(round(SAFE_ZONE_MM / float(trim_w_mm) * cw))) if trim_w_mm else 1
    safe_y = max(1, int(round(SAFE_ZONE_MM / float(trim_h_mm) * ch))) if trim_h_mm else 1
    sx1, sy1 = x + safe_x, y + safe_y
    sx2, sy2 = x2 - safe_x, y2 - safe_y
    if sx2 > sx1 and sy2 > sy1:
        cv2.rectangle(preview, (sx1, sy1), (sx2, sy2), (0, 200, 0), 1)

    folder = os.path.dirname(os.path.abspath(dest))
    if folder:
        os.makedirs(folder, exist_ok=True)
    cv2.imwrite(dest, preview, [cv2.IMWRITE_PNG_COMPRESSION, 6])
    return {
        "success": True,
        "cropped": amounts["cropped"],
        "leftMm": amounts["left"],
        "rightMm": amounts["right"],
        "topMm": amounts["top"],
        "bottomMm": amounts["bottom"],
        "contentInTrim": flags["contentInTrim"],
        "contentOutsideSafe": flags["contentOutsideSafe"],
        "summary": text["summary"],
        "warning": text["warning"],
        "previewPath": dest,
    }


def _looks_like_pdf(path: str) -> bool:
    """Manual uploads are stored without an extension, so the header decides."""
    try:
        with open(path, "rb") as handle:
            return handle.read(5) == b"%PDF-"
    except OSError:
        return False


def load_artwork_bgr(path: str):
    """A PDF page is rendered. OpenCV cannot read a PDF, which reported 'Could not read artwork'."""
    if _looks_like_pdf(path):
        import pymupdf as fitz

        doc = fitz.open(path)
        try:
            if doc.page_count < 1:
                return None
            page = doc[0]
            scale = 150.0 / 72.0
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False, colorspace=fitz.csRGB)
            if pix.width < 2 or pix.height < 2:
                return None
            rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
            return cv2.cvtColor(np.ascontiguousarray(rgb[:, :, :3]), cv2.COLOR_RGB2BGR)
        finally:
            doc.close()
    return cv2.imread(path, cv2.IMREAD_COLOR)


if __name__ == "__main__":
    import json
    import sys

    action = sys.argv[1] if len(sys.argv) > 1 else ""
    if action == "recommend" and len(sys.argv) >= 3:
        image = load_artwork_bgr(sys.argv[2])
        if image is None:
            print(json.dumps({"success": False, "error": "Could not read artwork"}))
            sys.exit(1)
        advice = recommend_whole_artwork(image)
        print(json.dumps({"success": True, **advice}))
    elif action == "preview" and len(sys.argv) >= 6:
        image = load_artwork_bgr(sys.argv[2])
        if image is None:
            print(json.dumps({"success": False, "error": "Could not read artwork"}))
            sys.exit(1)
        print(json.dumps(render_cover_preview(image, float(sys.argv[4]), float(sys.argv[5]), sys.argv[3])))
    else:
        print(json.dumps({"success": False, "error": "Usage: cover_crop_notice.py preview|recommend ..."}))
        sys.exit(1)
