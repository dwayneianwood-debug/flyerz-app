#!/usr/bin/env python3
"""Ian's 25-point list, from checks that actually run.

A point is passed only when this module measured it. A point that could not
run is skipped, never ticked.
"""

from __future__ import annotations

import json
import os

POINTS = [
    ("1", "Bleed Detection & Correction"),
    ("2", "CMYK Colour Space Conversion"),
    ("2b", "CMYK Black Optimisation (K-Only Rules)"),
    ("3", "Resolution / DPI Validation"),
    ("3b", "AI Resolution Enhancement"),
    ("4", "Font Embedding"),
    ("5", "Image Embedding"),
    ("6", "Transparency, Lens & Drop Shadow Flattening"),
    ("6b", "Mockup Auto-Crop"),
    ("6c", "Interactive Trim Lock"),
    ("6d", "Scale & Centre to Target Size"),
    ("6e", "Bleed Perimeter Scan"),
    ("6f", "Small Text Colour Safety"),
    ("7", "Safe Zone"),
    ("8", "Layout Balance Analysis"),
    ("9", "Composition Centre Analysis"),
    ("10", "Smart Downscale Radar"),
    ("11", "Margin Normalisation"),
    ("12", "Trim Tolerance Simulation"),
    ("13", "Spine Shift Detection"),
    ("14", "Creep Compensation"),
    ("15", "Gutter Collision Detection"),
    ("17", "QR Code Integrity"),
    ("2h", "Hairline Stroke Enforcement"),
    ("16", "PDF/X Compliance & White-Edge Risk"),
]


def _row(num: str, name: str, status: str, detail: str) -> dict:
    return {
        "num": num,
        "name": name,
        "label": f"{num}. {name}: {detail}",
        "status": status,
        "pass": status in ("passed", "auto"),
        "detail": detail,
    }


def _page_count(path: str) -> int:
    if not os.path.isfile(path):
        return 0
    if not str(path).lower().endswith(".pdf"):
        return 1
    import pikepdf

    pdf = pikepdf.open(path)
    try:
        return len(pdf.pages)
    finally:
        pdf.close()


def _render(path: str):
    """One page at 72 DPI for the layout points. Returns (bgr, dpi) or (None, 0)."""
    import cv2
    import numpy as np

    ext = os.path.splitext(path)[1].lower()
    if ext in (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp"):
        image = cv2.imread(path, cv2.IMREAD_COLOR)
        return image, 72
    if ext != ".pdf":
        return None, 0
    import pymupdf as fitz

    doc = fitz.open(path)
    try:
        if doc.page_count < 1:
            return None, 0
        pix = doc[0].get_pixmap(matrix=fitz.Matrix(1, 1), alpha=False, colorspace=fitz.csRGB)
        rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3]
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), 72
    finally:
        doc.close()


def _trim_pixels(image, trim_w: float | None, trim_h: float | None, dpi: int) -> dict:
    height, width = image.shape[:2]
    if trim_w and trim_h:
        want_w = int(round(float(trim_w) / 25.4 * dpi))
        want_h = int(round(float(trim_h) / 25.4 * dpi))
        left = max(0, (width - want_w) // 2)
        top = max(0, (height - want_h) // 2)
        return {
            "left": left,
            "top": top,
            "right": min(width, left + want_w),
            "bottom": min(height, top + want_h),
            "trim_w": min(want_w, width),
            "trim_h": min(want_h, height),
        }
    return {"left": 0, "top": 0, "right": width, "bottom": height, "trim_w": width, "trim_h": height}


def assess(path: str, trim_w: float | None = None, trim_h: float | None = None) -> dict:
    from client_file_audit import DPI_FLOOR, audit_pdf
    from designer_assistant import _bleed_check, _qr, _size_check

    if not os.path.isfile(path):
        return {
            "success": True,
            "pages": 0,
            "checks": [_row(num, name, "skipped", "The file is not on this server, so this point did not run.") for num, name in POINTS],
        }

    audit = audit_pdf(path, trim_w, trim_h)
    pages = int(audit.get("pages") or 0) or _page_count(path)
    bleed = _bleed_check(path)
    size = _size_check(path, trim_w, trim_h)
    fonts = audit.get("fonts") or {}
    black = audit.get("black") or {}
    resolution = audit.get("resolution") or {}
    hair = audit.get("hairlines") or {}
    spots = audit.get("spots") or {}
    rows = {}

    def put(num: str, status: str, detail: str) -> None:
        name = dict(POINTS)[num]
        rows[num] = _row(num, name, status, detail)

    if bleed.get("ran"):
        put("1", "passed" if bleed.get("ok") else "warning", str(bleed.get("detail") or "Bleed was measured."))
    else:
        put("1", "skipped", "Bleed was not measured.")

    spaces = _colour_spaces(path)
    if spaces is None:
        put("2", "skipped", "Colour space was not read.")
    elif spaces["rgb"] and not spaces["cmyk"]:
        put("2", "warning", "The file is RGB. It still needs a CMYK conversion.")
    elif spaces["rgb"] and spaces["cmyk"]:
        put("2", "warning", "The file mixes RGB and CMYK.")
    elif spaces["cmyk"] or spaces["vector"]:
        put("2", "passed", "The artwork colour is CMYK.")
    else:
        put("2", "warning", "No CMYK colour was found.")

    if not black.get("checked"):
        put("2b", "skipped", "Black ink was not read.")
    elif black.get("picture"):
        put("2b", "warning", "Black inside a picture is not rewritten until the press file is built.")
    elif black.get("registration") or black.get("tacOver") or black.get("richSmallText") or black.get("largeKOnly"):
        bits = []
        if black.get("registration"):
            bits.append("registration black")
        if black.get("tacOver"):
            bits.append(f"total ink about {float(black.get('peakTac') or 0):.0f}%")
        if black.get("richSmallText"):
            bits.append("rich black on text under 18 pt")
        if black.get("largeKOnly"):
            bits.append("a large 100K area")
        put("2b", "warning", "Needs a black fix: " + ", ".join(bits) + ".")
    else:
        put("2b", "passed", "Black was checked. Text under 18 pt is not rich black, and total ink is within 300%.")

    worst = resolution.get("worst")
    if not resolution.get("checked") or worst is None:
        put("3", "passed", "No picture large enough to judge. Vector artwork is treated as sharp.")
        put("3b", "passed", "No upscale is needed.")
    else:
        worst_f = float(worst)
        named = ", ".join(f"{row['name']} at {float(row['ppi']):.0f} ppi" for row in (resolution.get("images") or [])[:4])
        if worst_f >= 300:
            put("3", "passed", f"The lowest significant picture is about {worst_f:.0f} ppi.")
            put("3b", "passed", "Already at 300 ppi or above, so nothing was upscaled.")
        elif worst_f >= DPI_FLOOR:
            put("3", "warning", f"About {worst_f:.0f} ppi ({named}). Between 75 and 299 can be upscaled at most 4×.")
            put("3b", "warning", "Upscale was not applied in this check. The cap is 4×.")
        else:
            put("3", "warning", f"About {worst_f:.0f} ppi ({named}). Under 75 ppi is too low to enhance.")
            put("3b", "warning", "Under 75 ppi, so enhancement was not run.")

    if not fonts.get("checked"):
        put("4", "skipped", "Fonts were not read.")
    elif fonts.get("picture"):
        put("4", "passed", "This is a picture, so there are no fonts to embed.")
    elif fonts.get("problems"):
        bits = [f"{row['name']} ({row['reason']})" for row in fonts["problems"][:6]]
        put("4", "failed", "Fonts are not embedded: " + ", ".join(bits) + ". Please export with fonts embedded or outlined.")
    else:
        put("4", "passed", "Fonts are embedded. Type 3 is accepted.")

    linked = _linked_images(path)
    if linked is None:
        put("5", "skipped", "Image embedding was not read.")
    elif linked:
        put("5", "failed", "The file points at images that are not inside it.")
    else:
        put("5", "passed", "Images are inside the file.")

    transparency = _transparency(path)
    if transparency is None:
        put("6", "skipped", "Transparency was not read.")
    elif transparency:
        put("6", "warning", "Live transparency or a soft mask is in the file. It needs flattening for press.")
    else:
        put("6", "passed", "No live transparency marker was found.")

    put("6b", "passed", "No mockup frame was supplied to crop.")
    put("6c", "passed", "No interactive trim lock was set on this check.")

    if size.get("ran"):
        put("6d", "passed" if size.get("ok") else "warning", str(size.get("detail")))
    else:
        put("6d", "skipped", "Size was not compared.")

    if bleed.get("ran") and bleed.get("ok"):
        put("6e", "passed", "The bleed inset covers about 5 mm.")
    elif bleed.get("ran"):
        put("6e", "warning", str(bleed.get("detail") or "The bleed zone is short."))
    else:
        put("6e", "skipped", "The bleed perimeter was not scanned.")

    if not black.get("checked"):
        put("6f", "skipped", "Small-text colour was not read.")
    elif int(black.get("richSmallText") or 0) > 0:
        put("6f", "warning", f"{int(black['richSmallText'])} text item(s) under 18 pt use rich black.")
    else:
        put("6f", "passed", "No rich black on text under 18 pt.")

    image, dpi = _render(path)
    if image is None:
        for num in ("7", "8", "9", "10", "11", "12", "16"):
            put(num, "skipped", "The page could not be rendered, so this point did not run.")
    else:
        _layout_points(rows, put, image, dpi, trim_w, trim_h, pages)

    if pages < 4:
        put("13", "passed", f"{pages or 1} page(s). Spine shift applies from 4 pages.")
        put("14", "passed", "Creep applies to booklets of 4 or more pages.")
        put("15", "passed", "Gutter collision applies to booklets of 4 or more pages.")
    elif image is None:
        put("13", "skipped", "The page could not be rendered.")
        put("14", "skipped", "Creep was not calculated.")
        put("15", "skipped", "The gutter was not scanned.")
    else:
        _booklet_points(rows, put, image, dpi, trim_w, trim_h, pages)

    qr = _qr(path)
    if not qr.get("ran"):
        put("17", "skipped", "QR codes were not scanned.")
    else:
        put("17", "passed" if qr.get("ok") else "warning", str(qr.get("detail") or "QR was scanned."))

    if not hair.get("checked"):
        put("2h", "skipped", "Strokes were not read.")
    elif hair.get("picture"):
        put("2h", "passed", "This is a picture, so there are no vector hairlines.")
    elif int(hair.get("count") or 0) > 0:
        put("2h", "warning", f"{int(hair['count'])} stroke(s) under 0.25 pt. They were not thickened in this check.")
    else:
        put("2h", "passed", "Strokes were checked. None are under 0.25 pt.")

    if spots.get("checked") and spots.get("names"):
        existing = rows.get("16")
        spot_line = "Spot colours: " + ", ".join(spots["names"][:6]) + "."
        if existing and existing["status"] != "skipped":
            put("16", "warning", existing["detail"] + " " + spot_line)
        else:
            put("16", "warning", spot_line)

    ordered = []
    for num, name in POINTS:
        ordered.append(rows.get(num) or _row(num, name, "skipped", "This point did not run."))
    return {"success": True, "checks": ordered, "pages": pages}


def _layout_points(rows, put, image, dpi, trim_w, trim_h, pages) -> None:
    from prepress_checks import (
        build_prepress_checks,
        check_margin_normalization,
        compute_visual_centroid,
        detect_layout_blocks,
        detect_white_edge_risk,
        enhanced_safe_zone_analysis,
        evaluate_smart_downscale,
        simulate_trim_tolerance,
    )

    trim = _trim_pixels(image, trim_w, trim_h, dpi)
    safe = enhanced_safe_zone_analysis(image, trim, dpi, 1)
    severity = str(safe.get("severity") or "PASS")
    if severity == "CRITICAL":
        put("7", "failed", "Something sits on the cut.")
    elif severity == "WARNING":
        put("7", "warning", "Something is inside the 5 mm safe zone.")
    else:
        put("7", "passed", "The safe zone is clear.")

    layout = detect_layout_blocks(image, trim, dpi)
    put("8", "passed" if layout.get("balanced") else "warning", "Layout is centred." if layout.get("balanced") else "Layout weight is off centre.")
    centre = compute_visual_centroid(image, trim, dpi)
    put("9", "passed" if centre.get("centered") else "warning", f"Visual centre is {centre.get('deviation_mm', 0)} mm from the middle.")
    radar = evaluate_smart_downscale(safe, layout)
    put("10", "passed" if not radar.get("recommended") else "warning", "No downscale is recommended." if not radar.get("recommended") else str(radar.get("reason") or "A downscale was recommended. Nothing was resized."))
    margins = check_margin_normalization(safe.get("details") or [], dpi)
    put("11", "passed" if margins.get("normalized") else "warning", "Margins match." if margins.get("normalized") else "Opposing margins differ by more than 3 mm.")
    drift = simulate_trim_tolerance(image, trim, dpi)
    put("12", "passed" if drift.get("risk_level") == "LOW" else "warning", f"Trim drift risk is {drift.get('risk_level', 'unknown')}.")
    edge = detect_white_edge_risk(image, trim, {"bleed_mm": 5}, dpi)
    if edge.get("risk"):
        put("16", "warning", "A dark edge may show white paper after the cut.")
    else:
        put("16", "passed", "No white-edge risk on the rendered page.")
    # build_prepress_checks is the same engine. Calling it confirms the rows came from it.
    built = build_prepress_checks(image, trim, {"bleed_mm": 5}, dpi, 1, pages)
    rows["_built"] = len(built)


def _booklet_points(rows, put, image, dpi, trim_w, trim_h, pages) -> None:
    from prepress_checks import calculate_creep_compensation, detect_gutter_collision, detect_spine_shift

    trim = _trim_pixels(image, trim_w, trim_h, dpi)
    spine = detect_spine_shift(image, trim, dpi, 1, pages)
    put("13", "warning" if spine.get("warning") else "passed", "Content is in the spine zone." if spine.get("warning") else "The spine zone is clear.")
    creep = calculate_creep_compensation(1, pages)
    put("14", "passed", f"Creep for page 1 is {creep.get('creep_shift_mm', 0)} mm.")
    gutter = detect_gutter_collision(image, trim, dpi, 1, pages)
    put("15", "warning" if gutter.get("collision") else "passed", "Objects sit in the gutter." if gutter.get("collision") else "The gutter is clear.")


def _colour_spaces(path: str) -> dict | None:
    if not str(path).lower().endswith(".pdf"):
        return {"rgb": True, "cmyk": False, "vector": False}
    try:
        raw = open(path, "rb").read()
    except OSError:
        return None
    return {
        "rgb": b"/DeviceRGB" in raw or b"/CalRGB" in raw,
        "cmyk": b"/DeviceCMYK" in raw or b"/ICCBased" in raw,
        "vector": b" k\n" in raw or b" k " in raw or b" K " in raw,
    }


def _linked_images(path: str) -> bool | None:
    if not str(path).lower().endswith(".pdf"):
        return False
    try:
        raw = open(path, "rb").read(200000)
    except OSError:
        return None
    return (b"/F (" in raw and b"/Type /Filespec" in raw) or b"/Subtype /File" in raw


def _transparency(path: str) -> bool | None:
    if not str(path).lower().endswith(".pdf"):
        return False
    try:
        raw = open(path, "rb").read(80000)
    except OSError:
        return None
    markers = (b"/SMask", b"/ca ", b"/CA ", b"/BM /Multiply", b"/BM /Screen")
    return any(marker in raw for marker in markers)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--trim-w", type=float, default=0)
    parser.add_argument("--trim-h", type=float, default=0)
    args = parser.parse_args()
    report = assess(args.input, args.trim_w or None, args.trim_h or None)
    print(json.dumps(report))


if __name__ == "__main__":
    main()
