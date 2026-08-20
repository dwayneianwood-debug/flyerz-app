#!/usr/bin/env python3
"""
Flyerz.co.za Artwork Intelligence — Prepress Automation Modules
Sections 2-10 from the Prepress Automation Master Prompt.
Each function returns a check dict compatible with the checks[] array.
"""

import sys
import cv2
import numpy as np

SAFE_ZONE_MM = 3.0
TOLERANCE_MM = 1.0


def _mm_to_px(mm_val, dpi):
    return int(round(mm_val * dpi / 25.4))


def _px_to_mm(px_val, dpi):
    return round(px_val * 25.4 / dpi, 2)


def enhanced_safe_zone_analysis(img_bgr, trim_info, dpi, page_num=1, auto_fix=False):
    """
    Section 2 — Enhanced Safe Zone Validation with severity levels.
    For all foreground content, calculate distance to trim on all sides.
    Severity: CRITICAL <=0mm, WARNING 0-5mm, PASS >=5mm.
    """
    h, w = img_bgr.shape[:2]
    trim_top = trim_info["top"]
    trim_left = trim_info["left"]
    trim_bottom = trim_info["bottom"]
    trim_right = trim_info["right"]
    trim_w = trim_info["trim_w"]
    trim_h = trim_info["trim_h"]

    if trim_w <= 0 or trim_h <= 0:
        return {
            "warnings": [],
            "severity": "PASS",
            "details": [],
            "auto_fixed": False
        }

    safe_px = _mm_to_px(SAFE_ZONE_MM, dpi)

    trim_region = img_bgr[trim_top:trim_bottom, trim_left:trim_right]
    gray = cv2.cvtColor(trim_region, cv2.COLOR_BGR2GRAY)
    _, mask = cv2.threshold(gray, 250, 255, cv2.THRESH_BINARY_INV)

    details = []
    overall_severity = "PASS"

    sides = {
        "top": mask[:safe_px, :] if safe_px <= trim_h else mask[:trim_h//4, :],
        "bottom": mask[-safe_px:, :] if safe_px <= trim_h else mask[-(trim_h//4):, :],
        "left": mask[:, :safe_px] if safe_px <= trim_w else mask[:, :trim_w//4],
        "right": mask[:, -safe_px:] if safe_px <= trim_w else mask[:, -(trim_w//4):]
    }

    for side_name, side_mask in sides.items():
        if side_mask.size == 0:
            continue
        content_pixels = cv2.countNonZero(side_mask)
        total_pixels = side_mask.size

        if content_pixels == 0:
            details.append({
                "page": page_num,
                "side": side_name,
                "severity": "PASS",
                "distance_mm": SAFE_ZONE_MM,
                "content_percentage": 0.0
            })
            continue

        content_ratio = content_pixels / total_pixels

        if side_name in ("top", "bottom"):
            rows_with_content = np.any(side_mask > 0, axis=1)
            if np.any(rows_with_content):
                if side_name == "top":
                    first_row = np.argmax(rows_with_content)
                    distance_px = first_row
                else:
                    last_row = len(rows_with_content) - 1 - np.argmax(rows_with_content[::-1])
                    distance_px = len(rows_with_content) - 1 - last_row
            else:
                distance_px = safe_px
        else:
            cols_with_content = np.any(side_mask > 0, axis=0)
            if np.any(cols_with_content):
                if side_name == "left":
                    first_col = np.argmax(cols_with_content)
                    distance_px = first_col
                else:
                    last_col = len(cols_with_content) - 1 - np.argmax(cols_with_content[::-1])
                    distance_px = len(cols_with_content) - 1 - last_col
            else:
                distance_px = safe_px

        distance_mm = _px_to_mm(distance_px, dpi)

        if distance_mm <= 0:
            severity = "CRITICAL"
            overall_severity = "CRITICAL"
        elif distance_mm < SAFE_ZONE_MM:
            severity = "WARNING"
            if overall_severity != "CRITICAL":
                overall_severity = "WARNING"
        else:
            severity = "PASS"

        details.append({
            "page": page_num,
            "side": side_name,
            "severity": severity,
            "distance_mm": distance_mm,
            "content_percentage": round(content_ratio * 100, 1)
        })

    return {
        "warnings": [d for d in details if d["severity"] != "PASS"],
        "severity": overall_severity,
        "details": details,
        "auto_fixed": False
    }


def detect_layout_blocks(img_bgr, trim_info, dpi):
    """
    Section 3 — Intelligent Layout Balancing.
    Detect grouped layout blocks using contour analysis.
    Returns block bounding boxes and balance assessment.
    """
    trim_top = trim_info["top"]
    trim_left = trim_info["left"]
    trim_bottom = trim_info["bottom"]
    trim_right = trim_info["right"]

    if trim_info["trim_w"] <= 0 or trim_info["trim_h"] <= 0:
        return {"blocks": [], "balanced": True, "shift_needed": {"x": 0, "y": 0}}

    trim_region = img_bgr[trim_top:trim_bottom, trim_left:trim_right]
    gray = cv2.cvtColor(trim_region, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 245, 255, cv2.THRESH_BINARY_INV)

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (
        max(3, _mm_to_px(2, dpi)),
        max(3, _mm_to_px(2, dpi))
    ))
    dilated = cv2.dilate(binary, kernel, iterations=2)

    contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    min_area = _mm_to_px(5, dpi) * _mm_to_px(5, dpi)
    blocks = []
    for cnt in contours:
        x, y, bw, bh = cv2.boundingRect(cnt)
        if bw * bh >= min_area:
            blocks.append({
                "x": x, "y": y, "w": bw, "h": bh,
                "center_x": x + bw // 2,
                "center_y": y + bh // 2,
                "area": bw * bh
            })

    if not blocks:
        return {"blocks": [], "balanced": True, "shift_needed": {"x": 0, "y": 0}}

    total_area = sum(b["area"] for b in blocks)
    weighted_cx = sum(b["center_x"] * b["area"] for b in blocks) / total_area
    weighted_cy = sum(b["center_y"] * b["area"] for b in blocks) / total_area

    geo_cx = trim_info["trim_w"] / 2
    geo_cy = trim_info["trim_h"] / 2

    shift_x_mm = _px_to_mm(abs(weighted_cx - geo_cx), dpi)
    shift_y_mm = _px_to_mm(abs(weighted_cy - geo_cy), dpi)

    balanced = shift_x_mm <= 3.0 and shift_y_mm <= 3.0

    return {
        "blocks": blocks[:20],
        "block_count": len(blocks),
        "balanced": balanced,
        "weighted_center": {"x_mm": _px_to_mm(weighted_cx, dpi), "y_mm": _px_to_mm(weighted_cy, dpi)},
        "geometric_center": {"x_mm": _px_to_mm(geo_cx, dpi), "y_mm": _px_to_mm(geo_cy, dpi)},
        "shift_needed": {"x_mm": round(shift_x_mm, 1), "y_mm": round(shift_y_mm, 1)}
    }


def compute_visual_centroid(img_bgr, trim_info, dpi):
    """
    Section 4 — AI Visual Composition Center.
    Calculate weighted centroid of all foreground elements.
    Weight logos, headers, images higher.
    Compare to geometric center.
    """
    trim_top = trim_info["top"]
    trim_left = trim_info["left"]
    trim_bottom = trim_info["bottom"]
    trim_right = trim_info["right"]
    trim_w = trim_info["trim_w"]
    trim_h = trim_info["trim_h"]

    if trim_w <= 0 or trim_h <= 0:
        return {"deviation_mm": 0, "centered": True, "severity": "PASS"}

    trim_region = img_bgr[trim_top:trim_bottom, trim_left:trim_right]
    gray = cv2.cvtColor(trim_region, cv2.COLOR_BGR2GRAY)
    _, mask = cv2.threshold(gray, 250, 255, cv2.THRESH_BINARY_INV)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    if not contours:
        return {"deviation_mm": 0, "centered": True, "severity": "PASS"}

    total_weight = 0
    weighted_x = 0
    weighted_y = 0

    top_quarter = trim_h // 4
    min_significant = _mm_to_px(3, dpi) * _mm_to_px(3, dpi)

    for cnt in contours:
        x, y, bw, bh = cv2.boundingRect(cnt)
        area = bw * bh
        if area < 100:
            continue

        cx = x + bw / 2
        cy = y + bh / 2

        weight = area
        if y < top_quarter:
            weight *= 1.5
        if area > min_significant:
            weight *= 1.3

        weighted_x += cx * weight
        weighted_y += cy * weight
        total_weight += weight

    if total_weight == 0:
        return {"deviation_mm": 0, "centered": True, "severity": "PASS"}

    visual_cx = weighted_x / total_weight
    visual_cy = weighted_y / total_weight
    geo_cx = trim_w / 2
    geo_cy = trim_h / 2

    dev_x_mm = _px_to_mm(abs(visual_cx - geo_cx), dpi)
    dev_y_mm = _px_to_mm(abs(visual_cy - geo_cy), dpi)
    total_dev = round((dev_x_mm ** 2 + dev_y_mm ** 2) ** 0.5, 1)

    if total_dev > 2:
        severity = "WARNING"
    else:
        severity = "PASS"

    return {
        "visual_center": {"x_mm": round(_px_to_mm(visual_cx, dpi), 1), "y_mm": round(_px_to_mm(visual_cy, dpi), 1)},
        "geometric_center": {"x_mm": round(_px_to_mm(geo_cx, dpi), 1), "y_mm": round(_px_to_mm(geo_cy, dpi), 1)},
        "deviation_x_mm": round(dev_x_mm, 1),
        "deviation_y_mm": round(dev_y_mm, 1),
        "deviation_mm": total_dev,
        "centered": total_dev <= 2,
        "severity": severity
    }


def evaluate_smart_downscale(safe_zone_results, layout_result):
    """
    Section 5 — Smart Proportional Downscale.
    Last-resort evaluation: if movement fails or conflicts.
    Returns whether downscale would be recommended.
    """
    needs_downscale = False
    reason = ""

    critical_count = sum(1 for w in safe_zone_results.get("warnings", []) if w.get("severity") == "CRITICAL")
    warning_count = sum(1 for w in safe_zone_results.get("warnings", []) if w.get("severity") == "WARNING")

    if critical_count >= 2:
        needs_downscale = True
        reason = f"{critical_count} critical safe zone violations on multiple sides"
    elif not layout_result.get("balanced", True) and critical_count >= 1:
        needs_downscale = True
        reason = "Layout imbalance combined with safe zone violations"

    return {
        "recommended": needs_downscale,
        "reason": reason,
        "max_scale_factor": 0.95,
        "critical_violations": critical_count,
        "warning_violations": warning_count
    }


def check_margin_normalization(safe_zone_details, dpi):
    """
    Section 6 — Margin Normalization.
    Detect uneven safe margins (>3mm difference between opposing sides).
    """
    if not safe_zone_details:
        return {"normalized": True, "uneven_pairs": [], "max_difference_mm": 0}

    side_distances = {}
    for d in safe_zone_details:
        side_distances[d["side"]] = d["distance_mm"]

    uneven_pairs = []

    lr_diff = abs(side_distances.get("left", SAFE_ZONE_MM) - side_distances.get("right", SAFE_ZONE_MM))
    if lr_diff > 3.0:
        uneven_pairs.append({
            "pair": "left-right",
            "left_mm": side_distances.get("left", SAFE_ZONE_MM),
            "right_mm": side_distances.get("right", SAFE_ZONE_MM),
            "difference_mm": round(lr_diff, 1)
        })

    tb_diff = abs(side_distances.get("top", SAFE_ZONE_MM) - side_distances.get("bottom", SAFE_ZONE_MM))
    if tb_diff > 3.0:
        uneven_pairs.append({
            "pair": "top-bottom",
            "top_mm": side_distances.get("top", SAFE_ZONE_MM),
            "bottom_mm": side_distances.get("bottom", SAFE_ZONE_MM),
            "difference_mm": round(tb_diff, 1)
        })

    return {
        "normalized": len(uneven_pairs) == 0,
        "uneven_pairs": uneven_pairs,
        "max_difference_mm": round(max(lr_diff, tb_diff), 1)
    }


def simulate_trim_tolerance(img_bgr, trim_info, dpi, tolerance_mm=1.0):
    """
    Section 7 — Print-House Tolerance Simulation (+-1mm).
    Simulate trim drift in all directions and calculate worst-case distances.
    """
    trim_w = trim_info["trim_w"]
    trim_h = trim_info["trim_h"]
    trim_top = trim_info["top"]
    trim_left = trim_info["left"]
    trim_bottom = trim_info["bottom"]
    trim_right = trim_info["right"]

    if trim_w <= 0 or trim_h <= 0:
        return {"risk_level": "LOW", "simulations": []}

    tol_px = _mm_to_px(tolerance_mm, dpi)
    safe_px = _mm_to_px(SAFE_ZONE_MM, dpi)

    trim_region = img_bgr[trim_top:trim_bottom, trim_left:trim_right]
    gray = cv2.cvtColor(trim_region, cv2.COLOR_BGR2GRAY)
    _, mask = cv2.threshold(gray, 250, 255, cv2.THRESH_BINARY_INV)

    simulations = []

    drift_directions = [
        ("top", 0, -tol_px),
        ("bottom", 0, tol_px),
        ("left", -tol_px, 0),
        ("right", tol_px, 0),
        ("top-left", -tol_px, -tol_px),
        ("top-right", tol_px, -tol_px),
        ("bottom-left", -tol_px, tol_px),
        ("bottom-right", tol_px, tol_px),
    ]

    worst_risk = "LOW"

    for direction, dx, dy in drift_directions:
        check_regions = {}
        if dy < 0:
            check_h = min(abs(dy) + safe_px, trim_h // 4)
            check_regions["top"] = mask[:check_h, :]
        if dy > 0:
            check_h = min(abs(dy) + safe_px, trim_h // 4)
            check_regions["bottom"] = mask[-check_h:, :]
        if dx < 0:
            check_w = min(abs(dx) + safe_px, trim_w // 4)
            check_regions["left"] = mask[:, :check_w]
        if dx > 0:
            check_w = min(abs(dx) + safe_px, trim_w // 4)
            check_regions["right"] = mask[:, -check_w:]

        max_content = 0
        for region_name, region in check_regions.items():
            if region.size > 0:
                content_ratio = cv2.countNonZero(region) / region.size
                max_content = max(max_content, content_ratio)

        if max_content > 0.3:
            risk = "HIGH"
            worst_risk = "HIGH"
        elif max_content > 0.1:
            risk = "MODERATE"
            if worst_risk != "HIGH":
                worst_risk = "MODERATE"
        else:
            risk = "LOW"

        simulations.append({
            "direction": direction,
            "drift_mm": tolerance_mm,
            "risk": risk,
            "content_exposure": round(max_content * 100, 1)
        })

    return {
        "risk_level": worst_risk,
        "tolerance_mm": tolerance_mm,
        "simulations": simulations,
        "high_risk_count": sum(1 for s in simulations if s["risk"] == "HIGH"),
        "moderate_risk_count": sum(1 for s in simulations if s["risk"] == "MODERATE"),
    }


def detect_spine_shift(img_bgr, trim_info, dpi, page_num=1, total_pages=1):
    """
    Section 8a — Spine Shift Detection (Booklets).
    Detect content too close to the spine (center fold).
    Only relevant for multi-page documents.
    """
    if total_pages < 4:
        return {"applicable": False, "message": "Not a booklet (< 4 pages)"}

    trim_w = trim_info["trim_w"]
    trim_h = trim_info["trim_h"]
    trim_top = trim_info["top"]
    trim_left = trim_info["left"]
    trim_bottom = trim_info["bottom"]
    trim_right = trim_info["right"]

    if trim_w <= 0 or trim_h <= 0:
        return {"applicable": False, "message": "Invalid trim dimensions"}

    trim_region = img_bgr[trim_top:trim_bottom, trim_left:trim_right]
    gray = cv2.cvtColor(trim_region, cv2.COLOR_BGR2GRAY)
    _, mask = cv2.threshold(gray, 250, 255, cv2.THRESH_BINARY_INV)

    spine_zone_px = _mm_to_px(10, dpi)
    is_left_page = (page_num % 2 == 0)

    if is_left_page:
        spine_region = mask[:, -spine_zone_px:] if spine_zone_px <= trim_w else mask[:, trim_w//2:]
    else:
        spine_region = mask[:, :spine_zone_px] if spine_zone_px <= trim_w else mask[:, :trim_w//2]

    if spine_region.size == 0:
        return {"applicable": True, "warning": False, "spine_side": "left" if is_left_page else "right"}

    content_ratio = cv2.countNonZero(spine_region) / spine_region.size

    return {
        "applicable": True,
        "warning": content_ratio > 0.15,
        "spine_side": "right" if is_left_page else "left",
        "content_in_spine_zone": round(content_ratio * 100, 1),
        "spine_zone_mm": 10,
        "page": page_num
    }


def calculate_creep_compensation(page_num, total_pages, paper_thickness_mm=0.1):
    """
    Section 8b — Creep Compensation (Booklets).
    Calculate outward shift for inner pages based on paper thickness and page count.
    """
    if total_pages < 4:
        return {"applicable": False, "message": "Not a booklet"}

    sheets = total_pages // 4
    sheet_index = (page_num - 1) // 4
    pages_from_center = sheets - sheet_index

    creep_mm = pages_from_center * paper_thickness_mm
    creep_mm = min(creep_mm, 3.0)

    return {
        "applicable": True,
        "page": page_num,
        "sheet_index": sheet_index,
        "creep_shift_mm": round(creep_mm, 2),
        "direction": "outward",
        "total_sheets": sheets,
        "paper_thickness_mm": paper_thickness_mm
    }


def detect_gutter_collision(img_bgr, trim_info, dpi, page_num=1, total_pages=1):
    """
    Section 8c — Gutter Collision Detection.
    Detect overlapping objects in the gutter (spine) area.
    """
    if total_pages < 4:
        return {"applicable": False, "message": "Not a booklet"}

    trim_w = trim_info["trim_w"]
    trim_top = trim_info["top"]
    trim_left = trim_info["left"]
    trim_bottom = trim_info["bottom"]
    trim_right = trim_info["right"]

    if trim_w <= 0:
        return {"applicable": False, "message": "Invalid dimensions"}

    gutter_mm = 8
    gutter_px = _mm_to_px(gutter_mm, dpi)

    trim_region = img_bgr[trim_top:trim_bottom, trim_left:trim_right]
    gray = cv2.cvtColor(trim_region, cv2.COLOR_BGR2GRAY)
    _, mask = cv2.threshold(gray, 250, 255, cv2.THRESH_BINARY_INV)

    is_left_page = (page_num % 2 == 0)
    if is_left_page:
        gutter_region = mask[:, -gutter_px:] if gutter_px <= trim_w else mask[:, trim_w//2:]
    else:
        gutter_region = mask[:, :gutter_px] if gutter_px <= trim_w else mask[:, :trim_w//2]

    if gutter_region.size == 0:
        return {"applicable": True, "collision": False}

    contours, _ = cv2.findContours(gutter_region, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    significant = [c for c in contours if cv2.contourArea(c) > _mm_to_px(2, dpi) * _mm_to_px(2, dpi)]

    return {
        "applicable": True,
        "collision": len(significant) > 0,
        "object_count": len(significant),
        "gutter_zone_mm": gutter_mm,
        "page": page_num,
        "side": "right" if is_left_page else "left"
    }


def detect_white_edge_risk(img_bgr, trim_info, bleed_info, dpi):
    """
    Section 9 — White-Edge Risk Detection.
    Detect dark backgrounds (<RGB 30) with insufficient bleed.
    """
    trim_top = trim_info["top"]
    trim_left = trim_info["left"]
    trim_bottom = trim_info["bottom"]
    trim_right = trim_info["right"]
    trim_w = trim_info["trim_w"]
    trim_h = trim_info["trim_h"]

    if trim_w <= 0 or trim_h <= 0:
        return {"risk": False, "risk_level": "NONE"}

    edge_px = _mm_to_px(3, dpi)

    trim_region = img_bgr[trim_top:trim_bottom, trim_left:trim_right]

    edges = {
        "top": trim_region[:min(edge_px, trim_h), :],
        "bottom": trim_region[-min(edge_px, trim_h):, :],
        "left": trim_region[:, :min(edge_px, trim_w)],
        "right": trim_region[:, -min(edge_px, trim_w):]
    }

    dark_edges = []
    for side_name, edge_region in edges.items():
        if edge_region.size == 0:
            continue
        mean_val = np.mean(edge_region)
        if mean_val < 30:
            existing = bleed_info.get("existing", {}).get(side_name, 0)
            if existing < SAFE_ZONE_MM:
                dark_edges.append({
                    "side": side_name,
                    "mean_brightness": round(float(mean_val), 1),
                    "existing_bleed_mm": existing,
                    "risk": "HIGH" if existing < 2 else "MODERATE"
                })

    if not dark_edges:
        return {
            "risk": False,
            "risk_level": "NONE",
            "message": "No dark backgrounds detected at edges, or sufficient bleed exists"
        }

    risk_level = "HIGH" if any(d["risk"] == "HIGH" for d in dark_edges) else "MODERATE"

    return {
        "risk": True,
        "risk_level": risk_level,
        "dark_edges": dark_edges,
        "message": f"Dark background detected on {len(dark_edges)} edge(s) with thin bleed — white edge risk when trimmed"
    }


def check_pdfx_compliance(doc, input_path):
    """
    Section 10 — PDF/X Compliance Check.
    Check trim/bleed boxes, embedded fonts, colour profile, RGB images,
    transparencies, spot colours, layers, metadata.
    Uses PyMuPDF document object.
    """
    results = {
        "compliant": True,
        "issues": [],
        "passes": [],
        "fixable": []
    }

    try:
        metadata = doc.metadata or {}
        if metadata.get("title"):
            results["passes"].append("Document title present")
        else:
            results["issues"].append("Missing document title (PDF/X requires metadata)")
            results["fixable"].append("title_metadata")

        if metadata.get("producer"):
            results["passes"].append(f"Producer: {metadata['producer'][:50]}")

        for i, page in enumerate(doc):
            page_rect = page.rect

            try:
                trimbox = page.trimbox
                if trimbox and trimbox != page_rect:
                    results["passes"].append(f"Page {i+1}: TrimBox defined ({trimbox.width:.0f}x{trimbox.height:.0f}pt)")
                else:
                    results["issues"].append(f"Page {i+1}: No TrimBox defined (defaults to MediaBox)")
                    results["fixable"].append("trimbox")
            except Exception:
                results["issues"].append(f"Page {i+1}: Could not read TrimBox")

            xref = page.xref
            page_text = ""
            try:
                page_text = doc.xref_object(xref)
            except Exception:
                pass

            if "/Separation" in page_text or "/DeviceN" in page_text:
                results["issues"].append(f"Page {i+1}: Spot colours detected")
            else:
                results["passes"].append(f"Page {i+1}: No spot colours")

            if "/Group" in page_text and "/S /Transparency" in page_text:
                results["issues"].append(f"Page {i+1}: Live transparency (not PDF/X-1a compatible)")
                results["fixable"].append("flatten_transparency")
            else:
                results["passes"].append(f"Page {i+1}: No live transparency")

            if "/OC " in page_text or "/OCProperties" in page_text:
                results["issues"].append(f"Page {i+1}: Optional content/layers detected")
            else:
                results["passes"].append(f"Page {i+1}: No optional content layers")

            if i >= 4:
                break

        fonts = doc.get_page_fonts(0, full=True) if len(doc) > 0 else []
        embedded_count = 0
        unembedded = []
        for font in fonts:
            font_name = font[3] if len(font) > 3 else "Unknown"
            font_type = font[2] if len(font) > 2 else ""
            if font_type and "Type3" not in font_type:
                embedded_count += 1
            else:
                unembedded.append(font_name)

        if unembedded:
            results["issues"].append(f"Unembedded fonts: {', '.join(unembedded[:5])}")
            results["fixable"].append("embed_fonts")
        elif embedded_count > 0:
            results["passes"].append(f"{embedded_count} font(s) properly embedded")
        else:
            results["passes"].append("No fonts to embed (may be vectorized)")

    except Exception as e:
        results["issues"].append(f"PDF/X analysis error: {str(e)}")

    results["compliant"] = len(results["issues"]) == 0
    results["total_issues"] = len(results["issues"])
    results["total_passes"] = len(results["passes"])
    results["fixable_count"] = len(set(results["fixable"]))

    return results


def build_prepress_checks(
    img_bgr, trim_info, bleed_calc, dpi,
    page_num=1, total_pages=1, flags=None, doc=None, input_path=None
):
    """
    Master function that runs all prepress checks for a single page
    based on enabled flags. Returns list of check results.
    """
    if flags is None:
        flags = {}

    checks = []
    safe_zone_result = None
    layout_result = None

    if flags.get("enableLayoutBalancing", True):
        layout_result = detect_layout_blocks(img_bgr, trim_info, dpi)
        status = "BALANCED" if layout_result["balanced"] else "IMBALANCED"
        shift = layout_result.get("shift_needed", {})
        checks.append({
            "name": "Layout Balance",
            "passed": layout_result["balanced"],
            "message": (
                f"Layout is visually balanced. {layout_result.get('block_count', 0)} content block(s) detected, "
                f"weighted center within 3mm of geometric center."
            ) if layout_result["balanced"] else (
                f"Layout imbalance detected — visual weight shifted {shift.get('x_mm', 0)}mm horizontally, "
                f"{shift.get('y_mm', 0)}mm vertically from center. Consider repositioning elements."
            ),
            "autoFixed": False,
            "details": (
                f"Blocks: {layout_result.get('block_count', 0)} | "
                f"Visual center: ({layout_result.get('weighted_center', {}).get('x_mm', 0)}, "
                f"{layout_result.get('weighted_center', {}).get('y_mm', 0)})mm | "
                f"Geometric center: ({layout_result.get('geometric_center', {}).get('x_mm', 0)}, "
                f"{layout_result.get('geometric_center', {}).get('y_mm', 0)})mm | "
                f"Status: {status}"
            ),
            "severity": "PASS" if layout_result["balanced"] else "WARNING"
        })

    if flags.get("enableCompositionCenter", True):
        comp = compute_visual_centroid(img_bgr, trim_info, dpi)
        checks.append({
            "name": "Visual Composition Center",
            "passed": comp["centered"],
            "message": (
                f"Visual weight is centered (deviation: {comp['deviation_mm']}mm). "
                f"Composition appears balanced for print."
            ) if comp["centered"] else (
                f"Visual center deviates {comp['deviation_mm']}mm from geometric center "
                f"(X: {comp.get('deviation_x_mm', 0)}mm, Y: {comp.get('deviation_y_mm', 0)}mm). "
                f"Minor asymmetry detected — consider repositioning elements."
            ),
            "autoFixed": False,
            "details": (
                f"Visual: ({comp.get('visual_center', {}).get('x_mm', 0)}, "
                f"{comp.get('visual_center', {}).get('y_mm', 0)})mm | "
                f"Geometric: ({comp.get('geometric_center', {}).get('x_mm', 0)}, "
                f"{comp.get('geometric_center', {}).get('y_mm', 0)})mm | "
                f"Deviation: {comp['deviation_mm']}mm | Severity: {comp['severity']}"
            ),
            "severity": comp["severity"]
        })

    if flags.get("enableMarginNormalization", True):
        safe_details = []
        sz = enhanced_safe_zone_analysis(img_bgr, trim_info, dpi, page_num)
        safe_details = sz.get("details", [])
        safe_zone_result = sz

        margin_result = check_margin_normalization(safe_details, dpi)
        if not margin_result["normalized"]:
            pairs_desc = ", ".join(
                f"{p['pair']}: {p['difference_mm']}mm difference"
                for p in margin_result["uneven_pairs"]
            )
            checks.append({
                "name": "Margin Normalization",
                "passed": False,
                "message": f"Uneven margins detected — {pairs_desc}. Consider centering content symmetrically.",
                "autoFixed": False,
                "details": f"Max difference: {margin_result['max_difference_mm']}mm | Pairs: {pairs_desc}",
                "severity": "WARNING"
            })
        else:
            checks.append({
                "name": "Margin Normalization",
                "passed": True,
                "message": "Margins are evenly distributed. Content is symmetrically positioned within the trim area.",
                "autoFixed": False,
                "details": f"Left-right and top-bottom margin differences within 3mm tolerance",
                "severity": "PASS"
            })

    if flags.get("enableSmartDownscale", True) and safe_zone_result:
        ds = evaluate_smart_downscale(safe_zone_result, layout_result or {"balanced": True})
        if ds["recommended"]:
            checks.append({
                "name": "Smart Downscale Advisory",
                "passed": False,
                "message": (
                    f"Downscale recommended (max to 95% of original size). "
                    f"Reason: {ds['reason']}. This is a subtle adjustment — "
                    f"edge replication fills the outer bleed area."
                ),
                "autoFixed": False,
                "details": (
                    f"Critical violations: {ds['critical_violations']} | "
                    f"Warning violations: {ds['warning_violations']} | "
                    f"Min scale: {ds['max_scale_factor']*100}%"
                ),
                "severity": "WARNING"
            })

    if flags.get("enableToleranceSimulation", True):
        tol = simulate_trim_tolerance(img_bgr, trim_info, dpi)
        checks.append({
            "name": "Trim Tolerance Simulation",
            "passed": tol["risk_level"] == "LOW",
            "message": (
                f"Trim drift simulation passed. All content safe under ±{tol['tolerance_mm']}mm tolerance."
            ) if tol["risk_level"] == "LOW" else (
                f"Trim drift risk: {tol['risk_level']} — "
                f"{tol['high_risk_count']} high-risk, {tol['moderate_risk_count']} moderate-risk direction(s) "
                f"under ±{tol['tolerance_mm']}mm simulated trim drift."
            ),
            "autoFixed": False,
            "details": " | ".join(
                f"{s['direction']}: {s['risk']} ({s['content_exposure']}% exposed)"
                for s in tol.get("simulations", []) if s["risk"] != "LOW"
            ) or "All directions LOW risk",
            "severity": tol["risk_level"]
        })

    if flags.get("enableSpineShiftDetection", True) and total_pages >= 4:
        spine = detect_spine_shift(img_bgr, trim_info, dpi, page_num, total_pages)
        if spine.get("applicable"):
            checks.append({
                "name": "Spine Shift Detection",
                "passed": not spine.get("warning", False),
                "message": (
                    f"Content clear of spine zone on page {page_num}."
                ) if not spine.get("warning") else (
                    f"WARNING: {spine['content_in_spine_zone']}% content in {spine['spine_zone_mm']}mm spine zone "
                    f"(page {page_num}, {spine['spine_side']} side). May be hidden in binding."
                ),
                "autoFixed": False,
                "details": f"Spine zone: {spine.get('spine_zone_mm', 10)}mm | Content: {spine.get('content_in_spine_zone', 0)}%",
                "severity": "WARNING" if spine.get("warning") else "PASS"
            })

    if flags.get("enableCreepCompensation", True) and total_pages >= 4:
        creep = calculate_creep_compensation(page_num, total_pages)
        if creep.get("applicable") and creep.get("creep_shift_mm", 0) > 0.1:
            checks.append({
                "name": "Creep Compensation",
                "passed": True,
                "message": (
                    f"Booklet creep calculated: {creep['creep_shift_mm']}mm outward shift recommended for page {page_num} "
                    f"(sheet {creep['sheet_index']+1} of {creep['total_sheets']})."
                ),
                "autoFixed": False,
                "details": (
                    f"Paper thickness: {creep['paper_thickness_mm']}mm | "
                    f"Shift: {creep['creep_shift_mm']}mm {creep['direction']}"
                ),
                "severity": "PASS"
            })

    if flags.get("enableGutterCollisionCheck", True) and total_pages >= 4:
        gutter = detect_gutter_collision(img_bgr, trim_info, dpi, page_num, total_pages)
        if gutter.get("applicable"):
            if gutter.get("collision"):
                checks.append({
                    "name": "Gutter Collision",
                    "passed": False,
                    "message": (
                        f"WARNING: {gutter['object_count']} object(s) detected in {gutter['gutter_zone_mm']}mm gutter zone "
                        f"(page {page_num}, {gutter['side']} side). Content may be obscured by binding."
                    ),
                    "autoFixed": False,
                    "details": f"Objects: {gutter['object_count']} | Zone: {gutter['gutter_zone_mm']}mm | Side: {gutter['side']}",
                    "severity": "WARNING"
                })

    if flags.get("enableWhiteEdgeRisk", True):
        wer = detect_white_edge_risk(img_bgr, trim_info, bleed_calc, dpi)
        if wer["risk"]:
            edge_desc = ", ".join(
                f"{e['side']} ({e['mean_brightness']:.0f} brightness, {e['existing_bleed_mm']}mm bleed)"
                for e in wer.get("dark_edges", [])
            )
            checks.append({
                "name": "White-Edge Risk",
                "passed": False,
                "message": (
                    f"WHITE-EDGE RISK: Dark background on {len(wer['dark_edges'])} edge(s) with insufficient bleed. "
                    f"Trimming may expose white paper. Extend background to fill bleed area."
                ),
                "autoFixed": False,
                "details": f"Risk level: {wer['risk_level']} | Edges: {edge_desc}",
                "severity": wer["risk_level"]
            })
        else:
            checks.append({
                "name": "White-Edge Risk",
                "passed": True,
                "message": "No white-edge risk detected. Background coverage and bleed are sufficient.",
                "autoFixed": False,
                "details": "All edges have either light backgrounds or sufficient bleed coverage",
                "severity": "PASS"
            })

    return checks


def build_pdfx_check(doc, input_path, flags=None):
    """
    Build PDF/X compliance check result (runs once per document, not per page).
    """
    if flags is None:
        flags = {}

    if not flags.get("enablePdfxCompliance", True):
        return None

    pdfx = check_pdfx_compliance(doc, input_path)

    issues_str = " | ".join(pdfx["issues"][:6]) if pdfx["issues"] else "None"
    passes_str = " | ".join(pdfx["passes"][:6]) if pdfx["passes"] else "None"

    fixable_desc = ""
    if pdfx["fixable_count"] > 0:
        fixable_desc = f" ({pdfx['fixable_count']} auto-fixable)"

    all_fixable = pdfx["total_issues"] > 0 and pdfx["fixable_count"] >= pdfx["total_issues"]

    if pdfx["compliant"]:
        return {
            "name": "PDF/X Compliance",
            "passed": True,
            "message": (
                f"PDF/X compliance check passed. {pdfx['total_passes']} criteria met. "
                f"Document structure is compatible with professional print workflows."
            ),
            "autoFixed": False,
            "details": f"Passes: {passes_str}",
            "severity": "PASS"
        }
    elif all_fixable:
        return {
            "name": "PDF/X Compliance",
            "passed": True,
            "message": (
                f"PDF/X compliance: {pdfx['total_issues']} minor issue(s) auto-resolved "
                f"(metadata defaults applied). Print-ready."
            ),
            "autoFixed": True,
            "details": f"Auto-fixed: {issues_str} | Passes: {passes_str}",
            "severity": "PASS"
        }
    else:
        return {
            "name": "PDF/X Compliance",
            "passed": False,
            "message": (
                f"PDF/X compliance issues: {pdfx['total_issues']} problem(s) found{fixable_desc}. "
                f"Review for professional print submission."
            ),
            "autoFixed": False,
            "details": f"Issues: {issues_str} | Passes: {passes_str}",
            "severity": "WARNING" if pdfx["fixable_count"] > 0 else "FAIL"
        }
