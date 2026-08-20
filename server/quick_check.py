#!/usr/bin/env python3
"""
Flyerz.co.za Artwork Intelligence — Quick Pre-flight Check (Step 1)
Lightweight scan that checks 5 core litho-readiness criteria:
  1. Bleed (5mm all around)
  2. CMYK color space
  3. Transparency / lenses / drop shadows
  4. Resolution (300+ DPI)
  5. Print readiness (fonts, artwork size, centering)

Returns instant pass/fail per check with fix suggestions.
PDFs: invalid CropBox/TrimBox/Bleed vs MediaBox are vector-repaired in-place (PyMuPDF) at ingest
before analysis; all other checks are read-only.
"""

import sys
import json
import os
import shutil
import glob as globmod
import traceback

import cv2
cv2.setNumThreads(4)
import numpy as np
import fitz  # PyMuPDF

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pdf_geometry_sanitize import sanitize_pdf_geometry_inplace


def find_gs_binary():
    gs_path = shutil.which("gs")
    if gs_path:
        return gs_path
    nix_matches = globmod.glob("/nix/store/*/bin/gs")
    for p in sorted(nix_matches, reverse=True):
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return "gs"

GS_BIN = find_gs_binary()
BLEED_TARGET_MM = 5.0
MIN_DPI = 300


def _mm_to_px(mm_val, dpi):
    return int(round(mm_val * dpi / 25.4))


def _px_to_mm(px_val, dpi):
    return round(px_val * 25.4 / dpi, 2)


def detect_dpi_from_image(img_path):
    try:
        from PIL import Image
        with Image.open(img_path) as img:
            dpi_info = img.info.get("dpi")
            if dpi_info:
                return float(max(dpi_info))
            exif_data = img._getexif() if hasattr(img, '_getexif') and img._getexif() else {}
            if exif_data:
                x_res = exif_data.get(282)
                if x_res:
                    if hasattr(x_res, 'numerator'):
                        return float(x_res.numerator / x_res.denominator)
                    return float(x_res)
    except Exception:
        pass
    return 72.0


def detect_artwork_size(doc, img_bgr, dpi, file_type):
    """
    Detect the actual artwork/trim size in mm, EXCLUDING bleed.
    For PDFs: uses TrimBox if available, otherwise estimates from content bounds.
    For images: detects content foreground bounding box.
    Returns { width_mm, height_mm, has_bleed, bleed_mm, document_width_mm, document_height_mm }
    """
    result = {
        "width_mm": 0,
        "height_mm": 0,
        "has_bleed": False,
        "bleed_mm": {"top": 0, "bottom": 0, "left": 0, "right": 0},
        "document_width_mm": 0,
        "document_height_mm": 0,
    }

    if file_type == "pdf" and doc is not None:
        page = doc[0]
        media = page.rect
        doc_w = round(media.width * 25.4 / 72, 1)
        doc_h = round(media.height * 25.4 / 72, 1)
        result["document_width_mm"] = doc_w
        result["document_height_mm"] = doc_h

        try:
            trim = page.trimbox
        except Exception:
            trim = None

        if trim and trim != media:
            trim_w = round(trim.width * 25.4 / 72, 1)
            trim_h = round(trim.height * 25.4 / 72, 1)
            result["width_mm"] = trim_w
            result["height_mm"] = trim_h
            result["has_bleed"] = True
            result["bleed_mm"] = {
                "top": round((trim.y0 - media.y0) * 25.4 / 72, 1),
                "bottom": round((media.y1 - trim.y1) * 25.4 / 72, 1),
                "left": round((trim.x0 - media.x0) * 25.4 / 72, 1),
                "right": round((media.x1 - trim.x1) * 25.4 / 72, 1),
            }
        else:
            gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
            _, fg_mask = cv2.threshold(gray, 250, 255, cv2.THRESH_BINARY_INV)
            rows = np.any(fg_mask > 0, axis=1)
            cols = np.any(fg_mask > 0, axis=0)

            if np.any(rows) and np.any(cols):
                top_px = int(np.argmax(rows))
                bottom_px = int(len(rows) - 1 - np.argmax(rows[::-1]))
                left_px = int(np.argmax(cols))
                right_px = int(len(cols) - 1 - np.argmax(cols[::-1]))

                content_w = right_px - left_px + 1
                content_h = bottom_px - top_px + 1
                result["width_mm"] = round(content_w * 25.4 / dpi, 1)
                result["height_mm"] = round(content_h * 25.4 / dpi, 1)

                margin_top = _px_to_mm(top_px, dpi)
                margin_bottom = _px_to_mm(img_bgr.shape[0] - 1 - bottom_px, dpi)
                margin_left = _px_to_mm(left_px, dpi)
                margin_right = _px_to_mm(img_bgr.shape[1] - 1 - right_px, dpi)

                if min(margin_top, margin_bottom, margin_left, margin_right) >= 2.0:
                    result["has_bleed"] = True
                    result["bleed_mm"] = {
                        "top": margin_top, "bottom": margin_bottom,
                        "left": margin_left, "right": margin_right,
                    }
            else:
                result["width_mm"] = doc_w
                result["height_mm"] = doc_h
    else:
        h, w = img_bgr.shape[:2]
        doc_w = _px_to_mm(w, dpi)
        doc_h = _px_to_mm(h, dpi)
        result["document_width_mm"] = round(doc_w, 1)
        result["document_height_mm"] = round(doc_h, 1)

        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        _, fg_mask = cv2.threshold(gray, 250, 255, cv2.THRESH_BINARY_INV)
        rows = np.any(fg_mask > 0, axis=1)
        cols = np.any(fg_mask > 0, axis=0)

        if np.any(rows) and np.any(cols):
            top_px = int(np.argmax(rows))
            bottom_px = int(len(rows) - 1 - np.argmax(rows[::-1]))
            left_px = int(np.argmax(cols))
            right_px = int(len(cols) - 1 - np.argmax(cols[::-1]))

            content_w = right_px - left_px + 1
            content_h = bottom_px - top_px + 1
            result["width_mm"] = round(content_w * 25.4 / dpi, 1)
            result["height_mm"] = round(content_h * 25.4 / dpi, 1)

            margin_top = _px_to_mm(top_px, dpi)
            margin_bottom = _px_to_mm(h - 1 - bottom_px, dpi)
            margin_left = _px_to_mm(left_px, dpi)
            margin_right = _px_to_mm(w - 1 - right_px, dpi)

            if min(margin_top, margin_bottom, margin_left, margin_right) >= 2.0:
                result["has_bleed"] = True
                result["bleed_mm"] = {
                    "top": margin_top, "bottom": margin_bottom,
                    "left": margin_left, "right": margin_right,
                }
        else:
            result["width_mm"] = round(doc_w, 1)
            result["height_mm"] = round(doc_h, 1)

    return result


def check_bleed(doc, img_bgr, dpi, file_type):
    """Check if artwork has at least 5mm bleed on all sides."""
    result = {
        "id": "bleed",
        "name": "5mm Bleed",
        "passed": False,
        "message": "",
        "details": "",
        "fixType": "auto",
        "severity": "CRITICAL"
    }

    if file_type == "pdf" and doc is not None:
        page = doc[0]
        media = page.rect
        try:
            trim = page.trimbox
        except Exception:
            trim = None

        if trim and trim != media:
            bleed_top = round((trim.y0 - media.y0) * 25.4 / 72, 1)
            bleed_bottom = round((media.y1 - trim.y1) * 25.4 / 72, 1)
            bleed_left = round((trim.x0 - media.x0) * 25.4 / 72, 1)
            bleed_right = round((media.x1 - trim.x1) * 25.4 / 72, 1)

            bleeds = {"top": bleed_top, "bottom": bleed_bottom, "left": bleed_left, "right": bleed_right}
            min_bleed = min(bleeds.values())

            if min_bleed >= BLEED_TARGET_MM:
                result["passed"] = True
                result["message"] = f"Bleed present: {min_bleed}mm minimum (target: {BLEED_TARGET_MM}mm)"
                result["severity"] = "PASS"
            else:
                short_sides = [f"{s}: {v}mm" for s, v in bleeds.items() if v < BLEED_TARGET_MM]
                result["message"] = f"Insufficient bleed on {', '.join(short_sides)}. Need {BLEED_TARGET_MM}mm all around."
                result["details"] = (
                    f"Trim size: {trim.width*25.4/72:.1f} x {trim.height*25.4/72:.1f}mm. "
                    f"Document size: {media.width*25.4/72:.1f} x {media.height*25.4/72:.1f}mm. "
                    f"Bleed — T:{bleed_top}mm B:{bleed_bottom}mm L:{bleed_left}mm R:{bleed_right}mm. "
                    f"Artwork background should extend {BLEED_TARGET_MM}mm beyond trim on all sides."
                )
        else:
            w_mm = round(media.width * 25.4 / 72, 1)
            h_mm = round(media.height * 25.4 / 72, 1)
            result["message"] = f"No TrimBox defined — cannot verify bleed. Document is {w_mm} x {h_mm}mm."
            result["details"] = (
                f"The PDF has no TrimBox set. This means the artwork boundary is unknown. "
                f"For proper bleed: keep actual content at trim size, extend backgrounds {BLEED_TARGET_MM}mm outward, "
                f"and center the artwork on the page."
            )
    else:
        h, w = img_bgr.shape[:2]
        w_mm = _px_to_mm(w, dpi)
        h_mm = _px_to_mm(h, dpi)

        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        _, fg_mask = cv2.threshold(gray, 250, 255, cv2.THRESH_BINARY_INV)

        rows = np.any(fg_mask > 0, axis=1)
        cols = np.any(fg_mask > 0, axis=0)

        if np.any(rows) and np.any(cols):
            top_px = int(np.argmax(rows))
            bottom_px = int(len(rows) - 1 - np.argmax(rows[::-1]))
            left_px = int(np.argmax(cols))
            right_px = int(len(cols) - 1 - np.argmax(cols[::-1]))

            bleed_top = _px_to_mm(top_px, dpi)
            bleed_bottom = _px_to_mm(h - 1 - bottom_px, dpi)
            bleed_left = _px_to_mm(left_px, dpi)
            bleed_right = _px_to_mm(w - 1 - right_px, dpi)

            bleeds = {"top": bleed_top, "bottom": bleed_bottom, "left": bleed_left, "right": bleed_right}
            min_bleed = min(bleeds.values())

            if min_bleed >= BLEED_TARGET_MM:
                result["passed"] = True
                result["message"] = f"Content has {min_bleed:.1f}mm minimum margin from edges (target: {BLEED_TARGET_MM}mm)"
                result["severity"] = "PASS"
            else:
                short_sides = [f"{s}: {v:.1f}mm" for s, v in bleeds.items() if v < BLEED_TARGET_MM]
                result["message"] = f"Content too close to edge on {', '.join(short_sides)}. Need {BLEED_TARGET_MM}mm bleed."
                result["details"] = f"Image: {w_mm:.1f} x {h_mm:.1f}mm at {dpi} DPI. Extend background outward by {BLEED_TARGET_MM}mm on all sides."
        else:
            result["passed"] = True
            result["message"] = "No foreground content detected — bleed not applicable"
            result["severity"] = "PASS"

    return result


def check_cmyk(doc, file_type, input_path):
    """Check if artwork is in CMYK color space."""
    result = {
        "id": "cmyk",
        "name": "CMYK Color Space",
        "passed": False,
        "message": "",
        "details": "",
        "fixType": "auto",
        "severity": "HIGH"
    }

    if file_type == "pdf" and doc is not None:
        rgb_found = False
        cmyk_found = False

        for page_num in range(len(doc)):
            page = doc[page_num]
            images = page.get_images(full=True)
            for img_info in images:
                img_xref = img_info[0]
                try:
                    img_obj = doc.xref_object(img_xref)
                    if "/DeviceCMYK" in img_obj or "/ICCBased" in img_obj:
                        cmyk_found = True
                    if "/DeviceRGB" in img_obj:
                        rgb_found = True
                except Exception:
                    pass

            try:
                xref = page.xref
                page_obj = doc.xref_object(xref)
                if "/DeviceCMYK" in page_obj:
                    cmyk_found = True
                if "/DeviceRGB" in page_obj:
                    rgb_found = True
            except Exception:
                pass

        if cmyk_found and not rgb_found:
            result["passed"] = True
            result["message"] = "Artwork is in CMYK color space — print ready"
            result["severity"] = "PASS"
        elif rgb_found:
            result["message"] = "RGB color space detected — must convert to CMYK for litho printing"
            result["details"] = "RGB colors may shift during print. Convert all images and vectors to CMYK (DeviceCMYK) for accurate color reproduction."
        else:
            result["message"] = "Color space could not be determined — recommend CMYK conversion"
            result["details"] = "No explicit color space declarations found. Run through CMYK conversion to ensure print fidelity."
    else:
        try:
            from PIL import Image
            with Image.open(input_path) as img:
                mode = img.mode
                if mode == "CMYK":
                    result["passed"] = True
                    result["message"] = "Image is in CMYK color space — print ready"
                    result["severity"] = "PASS"
                else:
                    result["message"] = f"Image is in {mode} color space — must convert to CMYK for litho printing"
                    result["details"] = f"Current mode: {mode}. CMYK conversion required for accurate litho color reproduction."
        except Exception as e:
            result["message"] = f"Could not determine color space: {str(e)}"

    return result


def check_transparency(doc, file_type, input_path):
    """Check for transparency, lenses, and drop shadows."""
    result = {
        "id": "transparency",
        "name": "No Lenses / Drop Shadows",
        "passed": True,
        "message": "No live transparency, lenses, or drop shadows detected",
        "details": "",
        "fixType": "auto",
        "severity": "PASS"
    }

    issues = []

    if file_type == "pdf" and doc is not None:
        for page_num in range(len(doc)):
            page = doc[page_num]
            try:
                xref = page.xref
                page_obj = doc.xref_object(xref)

                if "/Group" in page_obj and "/Transparency" in page_obj:
                    issues.append(f"Page {page_num+1}: Transparency group")

                if "/ExtGState" in page_obj:
                    if "/ca " in page_obj or "/CA " in page_obj:
                        issues.append(f"Page {page_num+1}: Alpha transparency (opacity effects)")
                    if "/BM " in page_obj and "/Normal" not in page_obj.split("/BM ")[1][:20]:
                        issues.append(f"Page {page_num+1}: Blend mode (lens/overlay effect)")
            except Exception:
                pass

            images = page.get_images(full=True)
            for img_info in images:
                img_xref = img_info[0]
                try:
                    img_obj = doc.xref_object(img_xref)
                    if "/SMask" in img_obj:
                        issues.append(f"Page {page_num+1}: Image with soft mask (drop shadow or transparency)")
                except Exception:
                    pass
    else:
        try:
            from PIL import Image
            with Image.open(input_path) as img:
                if img.mode in ("RGBA", "LA", "PA"):
                    alpha = np.array(img.split()[-1])
                    if np.any(alpha < 255):
                        non_opaque = np.sum(alpha < 255)
                        total = alpha.size
                        pct = round(non_opaque / total * 100, 1)
                        issues.append(f"Image has alpha channel with {pct}% semi-transparent pixels")
        except Exception:
            pass

    if issues:
        result["passed"] = False
        result["message"] = f"Found {len(issues)} transparency/effect issue(s) — must flatten for litho"
        result["details"] = "; ".join(issues[:5])
        result["severity"] = "HIGH"

    return result


def check_resolution(doc, img_bgr, dpi, file_type, input_path):
    """Check if artwork is 300 DPI or higher.
    
    For images (JPG/PNG): EXIF metadata DPI is often unreliable — phone cameras
    (especially iPhones) embed 72 or 144 DPI regardless of actual pixel density.
    We calculate the effective DPI at a common print size (A4) based on pixel
    dimensions, which gives a true measure of print quality.
    """
    result = {
        "id": "resolution",
        "name": "300 DPI Minimum",
        "passed": False,
        "message": "",
        "details": "",
        "fixType": "manual",
        "severity": "CRITICAL"
    }

    effective_dpi = dpi
    dpi_sources = []

    if file_type == "pdf" and doc is not None:
        page = doc[0]
        images = page.get_images(full=True)
        min_img_dpi = 999999

        for img_info in images:
            img_xref = img_info[0]
            try:
                base_img = doc.extract_image(img_xref)
                if base_img:
                    img_w = base_img.get("width", 0)
                    img_h = base_img.get("height", 0)

                    page_w_pt = page.rect.width
                    page_h_pt = page.rect.height

                    if page_w_pt > 0 and img_w > 0:
                        img_dpi_x = img_w / (page_w_pt / 72)
                        img_dpi_y = img_h / (page_h_pt / 72)
                        img_dpi = min(img_dpi_x, img_dpi_y)
                        min_img_dpi = min(min_img_dpi, img_dpi)
                        dpi_sources.append(f"Embedded image: {img_w}x{img_h}px = ~{img_dpi:.0f} DPI")
            except Exception:
                pass

        if min_img_dpi < 999999:
            effective_dpi = min_img_dpi
        else:
            effective_dpi = 300
            dpi_sources.append("Vector PDF — resolution independent (300+ DPI equivalent)")
    else:
        metadata_dpi = detect_dpi_from_image(input_path)
        try:
            from PIL import Image as _PILImg
            with _PILImg.open(input_path) as _tmp:
                orig_w, orig_h = _tmp.size
        except Exception:
            orig_h, orig_w = img_bgr.shape[:2]

        a4_w_in = 210 / 25.4
        a4_h_in = 297 / 25.4
        px_long = max(orig_w, orig_h)
        px_short = min(orig_w, orig_h)
        eff_dpi_long = px_long / a4_h_in
        eff_dpi_short = px_short / a4_w_in
        print_effective_dpi = min(eff_dpi_long, eff_dpi_short)

        is_phone_dpi = metadata_dpi in (72, 96, 144, 150, 180, 200)

        if is_phone_dpi and print_effective_dpi >= MIN_DPI:
            effective_dpi = print_effective_dpi
            dpi_sources.append(
                f"Image: {orig_w}x{orig_h}px (metadata: {metadata_dpi:.0f} DPI, "
                f"effective at A4: {print_effective_dpi:.0f} DPI — pixel count sufficient for print)"
            )
        elif not is_phone_dpi and metadata_dpi >= MIN_DPI:
            effective_dpi = metadata_dpi
            dpi_sources.append(f"Image: {orig_w}x{orig_h}px at {metadata_dpi:.0f} DPI")
        else:
            effective_dpi = max(metadata_dpi, print_effective_dpi)
            dpi_sources.append(
                f"Image: {orig_w}x{orig_h}px (metadata: {metadata_dpi:.0f} DPI, "
                f"effective at A4: {print_effective_dpi:.0f} DPI)"
            )

    if effective_dpi >= MIN_DPI:
        result["passed"] = True
        result["message"] = f"Resolution: {effective_dpi:.0f} DPI (minimum: {MIN_DPI} DPI)"
        result["severity"] = "PASS"
    else:
        result["message"] = f"Resolution too low: {effective_dpi:.0f} DPI (minimum: {MIN_DPI} DPI)"
        result["details"] = (
            f"Litho printing requires {MIN_DPI} DPI minimum for sharp output. "
            f"Current effective resolution is {effective_dpi:.0f} DPI. "
            f"Re-export from source at higher resolution, or use the Precision Resizer tool."
        )

    if dpi_sources:
        result["details"] = (result.get("details", "") + " | " + "; ".join(dpi_sources)).strip(" | ")

    return result


def check_print_readiness(doc, img_bgr, dpi, file_type, input_path):
    """Overall print readiness: fonts embedded, artwork centered, correct size."""
    result = {
        "id": "print_ready",
        "name": "Print Readiness",
        "passed": True,
        "message": "Artwork appears print-ready for lithographic standards",
        "details": "",
        "fixType": "auto",
        "severity": "PASS"
    }

    issues = []
    info = []

    if file_type == "pdf" and doc is not None:
        page = doc[0]
        media = page.rect
        w_mm = round(media.width * 25.4 / 72, 1)
        h_mm = round(media.height * 25.4 / 72, 1)
        info.append(f"Document: {w_mm} x {h_mm}mm")

        try:
            trim = page.trimbox
            if trim and trim != media:
                tw = round(trim.width * 25.4 / 72, 1)
                th = round(trim.height * 25.4 / 72, 1)
                info.append(f"Trim: {tw} x {th}mm")

                center_x_offset = abs((media.width / 2) - (trim.x0 + trim.width / 2))
                center_y_offset = abs((media.height / 2) - (trim.y0 + trim.height / 2))

                if center_x_offset > 2 or center_y_offset > 2:
                    cx_mm = round(center_x_offset * 25.4 / 72, 1)
                    cy_mm = round(center_y_offset * 25.4 / 72, 1)
                    issues.append(f"Artwork not centered on page (offset: {cx_mm}mm x {cy_mm}mm)")
        except Exception:
            pass

        fonts = doc.get_page_fonts(0, full=True)
        embedded_count = 0
        not_embedded = []
        for font in fonts:
            font_name = font[3] if len(font) > 3 else "Unknown"
            font_file = font[4] if len(font) > 4 else ""
            if font_file:
                embedded_count += 1
            else:
                not_embedded.append(font_name)

        if not_embedded:
            issues.append(f"{len(not_embedded)} font(s) not embedded: {', '.join(not_embedded[:3])}")
        elif fonts:
            info.append(f"{embedded_count} font(s) embedded")

        page_count = len(doc)
        if page_count > 1:
            info.append(f"{page_count} pages")

    else:
        h, w = img_bgr.shape[:2]
        w_mm = _px_to_mm(w, dpi)
        h_mm = _px_to_mm(h, dpi)
        info.append(f"Image: {w}x{h}px = {w_mm:.1f} x {h_mm:.1f}mm at {dpi:.0f} DPI")

        standard_sizes = [
            ("A6", 105, 148), ("A5", 148, 210), ("A4", 210, 297), ("A3", 297, 420),
            ("DL", 99, 210), ("Business Card", 90, 55),
        ]
        for name, sw, sh in standard_sizes:
            if (abs(w_mm - sw) < 15 and abs(h_mm - sh) < 15) or \
               (abs(w_mm - sh) < 15 and abs(h_mm - sw) < 15):
                info.append(f"Closest standard: {name} ({sw}x{sh}mm)")
                break

    if issues:
        result["passed"] = False
        result["message"] = f"{len(issues)} print readiness issue(s) found"
        result["details"] = " | ".join(issues) + (" | " + " | ".join(info) if info else "")
        result["severity"] = "HIGH"
    else:
        result["details"] = " | ".join(info) if info else ""

    return result


PROXY_MAX_PX = 1000

CROPBOX_NOT_IN_MEDIABOX_NEEDLE = "cropbox not in mediabox"


def strip_cropbox_not_in_mediabox_items(checks):
    """Remove checks whose messages reference healed CropBox/MediaBox noise (never surface to UI)."""
    if not checks:
        return checks
    out = []
    for c in checks:
        blob = f"{c.get('message', '')} {c.get('details', '')}".lower()
        if CROPBOX_NOT_IN_MEDIABOX_NEEDLE in blob:
            continue
        out.append(c)
    return out


def _make_proxy(img_bgr, dpi):
    h, w = img_bgr.shape[:2]
    if max(h, w) <= PROXY_MAX_PX:
        return img_bgr, dpi
    scale = PROXY_MAX_PX / max(h, w)
    new_w = int(round(w * scale))
    new_h = int(round(h * scale))
    proxy = cv2.resize(img_bgr, (new_w, new_h), interpolation=cv2.INTER_AREA)
    proxy_dpi = dpi * scale
    sys.stderr.write(
        f"[FAI] Quick check proxy: {w}x{h} -> {new_w}x{new_h} "
        f"(scale {scale:.2f}, dpi {dpi:.0f} -> {proxy_dpi:.0f})\n"
    )
    return proxy, proxy_dpi


def run_quick_check(input_path, file_type):
    """Main entry point: run all 5 quick checks and return results."""
    doc = None
    img_bgr = None
    dpi = 300.0

    try:
        if file_type == "pdf":
            try:
                if sanitize_pdf_geometry_inplace(input_path):
                    sys.stderr.write(
                        "[FAI] Ingest: PDF page geometry auto-healed before quick pre-flight analysis.\n"
                    )
            except Exception as _geom_err:
                sys.stderr.write(f"[FAI] Ingest geometry sanitize non-fatal: {_geom_err}\n")
            doc = fitz.open(input_path)
            page = doc[0]
            preview_dpi = 72
            mat = fitz.Matrix(preview_dpi / 72, preview_dpi / 72)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img_array = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, 3)
            img_bgr = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)
            del pix, img_array
            dpi = float(preview_dpi)
            img_bgr, dpi = _make_proxy(img_bgr, dpi)
        else:
            from PIL import Image as PILImage
            with PILImage.open(input_path) as pil_img:
                orig_dpi = detect_dpi_from_image(input_path)
                w, h = pil_img.size
                if max(w, h) > PROXY_MAX_PX:
                    pil_img.thumbnail((PROXY_MAX_PX, PROXY_MAX_PX), PILImage.LANCZOS)
                    scale = pil_img.size[0] / w
                    dpi = orig_dpi * scale
                    sys.stderr.write(
                        f"[FAI] Quick check proxy (PIL): {w}x{h} -> "
                        f"{pil_img.size[0]}x{pil_img.size[1]} "
                        f"(scale {scale:.2f}, dpi {orig_dpi:.0f} -> {dpi:.0f})\n"
                    )
                else:
                    dpi = orig_dpi
                img_rgb = np.array(pil_img.convert("RGB"))
                img_bgr = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR)
                del img_rgb

        artwork_size = detect_artwork_size(doc, img_bgr, dpi, file_type)

        checks = [
            check_bleed(doc, img_bgr, dpi, file_type),
            check_cmyk(doc, file_type, input_path),
            check_transparency(doc, file_type, input_path),
            check_resolution(doc, img_bgr, dpi, file_type, input_path),
            check_print_readiness(doc, img_bgr, dpi, file_type, input_path),
        ]

        checks = strip_cropbox_not_in_mediabox_items(checks)
        all_passed = all(c["passed"] for c in checks)

        return {
            "checks": checks,
            "allPassed": all_passed,
            "passCount": sum(1 for c in checks if c["passed"]),
            "failCount": sum(1 for c in checks if not c["passed"]),
            "artworkSize": artwork_size,
        }

    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return {"error": str(e)}
    finally:
        if doc:
            doc.close()


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(json.dumps({"error": "Usage: quick_check.py <input_path> <file_type> <result_file>"}))
        sys.exit(1)

    input_path = sys.argv[1]
    file_type = sys.argv[2]
    result_file = sys.argv[3]

    result = run_quick_check(input_path, file_type)
    try:
        with open(result_file, "w") as f:
            json.dump(result, f)
        sys.stderr.write(f"[FAI] Quick check result written to {result_file}\n")
        print(json.dumps({"ok": True, "resultFile": result_file}))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))
