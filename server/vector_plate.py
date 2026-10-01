"""Erase lettering on the original picture, then place that clean plate.

The mask and the new type share one scale and one offset. Erasing after an
upscale, or mapping the type with a different crop, leaves the old letters
under the new ones.

A 1- or 2-digit line is not dilated: a wider mask eats the circle around a
step number. Gaps are filled by continuing the edge. Nothing is cropped and
nothing is left as a white bar.
"""

from __future__ import annotations

import os

import cv2
import numpy as np

SAFE_TEXT_MM = 3.0
EDGE_CLEARANCE_MM = 2.5
MIN_PPI = 400


def is_short_numeral(text: str) -> bool:
    raw = str(text or "").strip()
    return raw.isdigit() and len(raw) <= 2


def fit_placement(src_w: int, src_h: int, trim_w: float, trim_h: float, bleed_mm: float, boxes: list) -> tuple[float, float, float]:
    """Uniform scale in mm per source pixel, and the art origin on the media page.

    The whole picture stays inside the trim. When lettering on a flush edge
    would sit under 3 mm from the knife, that edge is pulled in by 2.5 mm and
    the gap is filled later by the edge extender.
    """
    src_w = max(1, int(src_w))
    src_h = max(1, int(src_h))
    scale = min(float(trim_w) / src_w, float(trim_h) / src_h)
    fit_w = float(trim_w)
    fit_h = float(trim_h)
    if src_h * scale >= float(trim_h) - 0.05 and _edge_mm(boxes, "y", scale, src_h) < SAFE_TEXT_MM:
        fit_h = float(trim_h) - 2.0 * EDGE_CLEARANCE_MM
    if src_w * scale >= float(trim_w) - 0.05 and _edge_mm(boxes, "x", scale, src_w) < SAFE_TEXT_MM:
        fit_w = float(trim_w) - 2.0 * EDGE_CLEARANCE_MM
    scale = min(fit_w / src_w, fit_h / src_h)
    art_w = src_w * scale
    art_h = src_h * scale
    media_w = float(trim_w) + 2.0 * float(bleed_mm)
    media_h = float(trim_h) + 2.0 * float(bleed_mm)
    return scale, (media_w - art_w) / 2.0, (media_h - art_h) / 2.0


def _edge_mm(boxes: list, axis: str, scale: float, span: int) -> float:
    if not boxes:
        return 1.0e9
    gaps = []
    for box in boxes:
        x0, y0, x1, y1 = [float(v) for v in box[:4]]
        if axis == "y":
            gaps.append(min(y0, span - y1))
        else:
            gaps.append(min(x0, span - x1))
    return max(0.0, min(gaps)) * scale


def erase_text(bgr: np.ndarray, lines: list, marks: list | None = None) -> tuple[np.ndarray, list, list]:
    """Telea on the original pixels. Returns the clean plate, kept lines, skipped lines."""
    height, width = bgr.shape[:2]
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    gold = (hsv[:, :, 0] >= 10) & (hsv[:, :, 0] <= 38) & (hsv[:, :, 1] > 60) & (hsv[:, :, 2] > 90)
    erase = np.zeros((height, width), np.uint8)
    spare = np.zeros((height, width), np.uint8)
    kept = []
    skipped = []
    meta = []
    for line in lines:
        if _sits_in_badge(line, marks):
            skipped.append({
                **line,
                "mode": "raster",
                "font": "",
                "kept_on_purpose": True,
                "reason": "Lettering inside a circle stayed in the picture.",
            })
            continue
        quad = _quad(line, width, height)
        built = _line_mask(bgr, lab, gold, quad, str(line.get("text") or ""), height, width)
        if built is None:
            skipped.append({
                **line,
                "mode": "raster",
                "font": "",
                "reason": "The letters could not be separated from the picture.",
            })
            continue
        ink_box, colour, glyph, tight, ornament = built
        line["ink"] = ink_box
        line["ink_hex"] = _bgr_hex(colour)
        line["color"] = line["ink_hex"]
        if ornament is not None and int(ornament.max()) > 0:
            line["kept_ornament"] = ornament
            spare = cv2.bitwise_or(spare, ornament)
        kept.append(line)
        meta.append({
            "line": line, "ink": ink_box, "glyph": glyph, "tight": tight,
            "quad": quad, "ornament": ornament,
        })
    if meta:
        _disentangle(meta)
        for item in meta:
            if item.get("changed"):
                _rebuild_tight(item, height, width)
            item["line"]["ink"] = item["ink"]
            erase |= item["tight"]
    marks = list(marks or [])
    bullets = np.zeros((height, width), np.uint8)
    for mark in marks:
        if mark.get("kind") != "bullet":
            continue
        cx, cy = int(round(mark["cx"])), int(round(mark["cy"]))
        radius = max(2, int(round(float(mark["radius"]))) + 1)
        cv2.circle(bullets, (cx, cy), radius, 255, -1)
        cv2.circle(erase, (cx, cy), radius, 255, -1)
    if int(erase.max()) == 0:
        return bgr.copy(), [], skipped
    protect = _badge_protect(marks, height, width)
    spare_halo = cv2.dilate(spare, np.ones((13, 13), np.uint8)) if int(spare.max()) > 0 else None
    # A 1- or 2-digit line is not dilated: a wider mask eats the circle around a step number.
    owned = _owned_masks(meta, height, width, protect, spare_halo)
    mask = np.zeros((height, width), np.uint8)
    for part in owned:
        mask |= part
    mask |= bullets
    guard = _foreign_ink(bgr, mask, meta, bullets, protect, spare_halo)
    if int(guard.max()) > 0:
        mask[guard > 0] = 0
        for part in owned:
            part[guard > 0] = 0
    if int(mask.max()) == 0:
        return bgr.copy(), [], skipped
    painted = _inpaint(bgr, mask, meta, protect)
    if int(spare.max()) > 0:
        painted[spare > 0] = bgr[spare > 0]
    _stamp_halos(bgr, painted, meta, owned, bullets, spare_halo)
    return painted, kept, skipped


def _owned_masks(meta: list, height: int, width: int, protect: np.ndarray, spare_halo: np.ndarray | None) -> list:
    """The pixels each line asked to clear, after the small dilate and the keeps."""
    kernel = np.ones((3, 5), np.uint8)
    owned = []
    for item in meta:
        if is_short_numeral(item["line"].get("text")):
            part = item["tight"].copy()
        else:
            part = cv2.dilate(item["tight"], kernel)
        if spare_halo is not None:
            part[spare_halo > 0] = 0
        if int(protect.max()) > 0:
            part[protect > 0] = 0
        owned.append(part)
    return owned


def _foreign_ink(bgr, mask, meta, bullets, protect, spare_halo) -> np.ndarray:
    """Lettering that belongs to no erased line. The mask must not eat it.

    A neighbour's inpaint used to smear a script line the reader never saw.
    Those pixels stay. The fringe of a line we did mean to erase does not.
    """
    guard = np.zeros(mask.shape, np.uint8)
    if int(mask.max()) == 0 or not meta:
        return guard
    height, width = mask.shape
    zone = np.zeros((height, width), np.uint8)
    for item in meta:
        x0, y0, x1, y1 = [int(v) for v in item["ink"]]
        zone[max(0, y0):min(height, y1), max(0, x0):min(width, x1)] = 255
        rect = item["line"].get("rect")
        if rect and len(rect) >= 4:
            x, y, bw, bh = [int(round(float(v))) for v in rect[:4]]
            zone[max(0, y):min(height, y + max(1, bh)), max(0, x):min(width, x + max(1, bw))] = 255
    expanded = cv2.dilate(zone, np.ones((9, 9), np.uint8))
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    k = 21 if min(height, width) > 40 else 9
    bg = cv2.medianBlur(gray, k)
    ink = cv2.absdiff(gray, bg) > 18
    ink[expanded > 0] = False
    ink[mask == 0] = False
    if int(bullets.max()) > 0:
        ink[bullets > 0] = False
    if protect is not None and int(protect.max()) > 0:
        ink[protect > 0] = False
    if spare_halo is not None:
        ink[spare_halo > 0] = False
    if int(ink.sum()) < 8:
        return guard
    count, labels, stats, _cent = cv2.connectedComponentsWithStats(ink.astype(np.uint8), 8)
    for index in range(1, count):
        area = int(stats[index, cv2.CC_STAT_AREA])
        comp_h = int(stats[index, cv2.CC_STAT_HEIGHT])
        comp_w = int(stats[index, cv2.CC_STAT_WIDTH])
        if area < 8 or comp_h < 6 or comp_w < 2:
            continue
        if comp_h > height * 0.45 and comp_w > width * 0.45:
            continue
        guard[labels == index] = 255
    if int(guard.max()) == 0:
        return guard
    return cv2.dilate(guard, np.ones((3, 3), np.uint8))


def _stamp_halos(source, painted, meta, owned, bullets, spare_halo) -> None:
    """Remember every pixel this line's erase changed, so a fallback can put it all back."""
    delta = np.abs(painted.astype(np.int16) - source.astype(np.int16)).sum(axis=2)
    changed = delta > 15
    if int(bullets.max()) > 0:
        changed[cv2.dilate(bullets, np.ones((5, 5), np.uint8)) > 0] = False
    if spare_halo is not None:
        changed[spare_halo > 0] = False
    height, width = changed.shape
    owner = np.full((height, width), -1, np.int16)
    best = np.full((height, width), 1.0e6, np.float32)
    reach = 16.0
    for index, part in enumerate(owned):
        if int(part.max()) == 0:
            continue
        inv = np.where(part > 0, np.uint8(0), np.uint8(255))
        dist = cv2.distanceTransform(inv, cv2.DIST_L2, 3)
        take = changed & (dist <= reach) & (dist < best)
        best = np.where(take, dist, best)
        owner = np.where(take, index, owner)
    for index, item in enumerate(meta):
        part = owned[index]
        halo = ((owner == index) | (part > 0))
        if spare_halo is not None:
            halo = halo & (spare_halo == 0)
        item["line"]["cleared_mask"] = part
        item["line"]["cleared_halo"] = halo.astype(np.uint8) * 255


def place_plate(clean: np.ndarray, boxes: list, trim_w: float, trim_h: float, bleed_mm: float, ppi: int = MIN_PPI) -> dict:
    """Resize the clean plate and extend the edges. The mapper is this same fit."""
    src_h, src_w = clean.shape[:2]
    scale_mm, off_x_mm, off_y_mm = fit_placement(src_w, src_h, trim_w, trim_h, bleed_mm, boxes)
    ppm = float(ppi) / 25.4
    media_w = max(1, int(round((float(trim_w) + 2.0 * float(bleed_mm)) * ppm)))
    media_h = max(1, int(round((float(trim_h) + 2.0 * float(bleed_mm)) * ppm)))
    paste_x = int(round(off_x_mm * ppm))
    paste_y = int(round(off_y_mm * ppm))
    art_x1 = int(round((off_x_mm + src_w * scale_mm) * ppm))
    art_y1 = int(round((off_y_mm + src_h * scale_mm) * ppm))
    art_w = max(1, min(media_w - paste_x, art_x1 - paste_x))
    art_h = max(1, min(media_h - paste_y, art_y1 - paste_y))
    paste_x = max(0, min(paste_x, media_w - 1))
    paste_y = max(0, min(paste_y, media_h - 1))
    art_w = max(1, min(art_w, media_w - paste_x))
    art_h = max(1, min(art_h, media_h - paste_y))
    enlarged, provider = _enlarge(clean, art_w, art_h)
    from quick_print import _extend_edges

    image = _extend_edges(
        enlarged,
        paste_y,
        media_h - paste_y - art_h,
        paste_x,
        media_w - paste_x - art_w,
        px_per_mm=ppm,
    )
    if image.shape[0] != media_h or image.shape[1] != media_w:
        image = cv2.resize(image, (media_w, media_h), interpolation=cv2.INTER_LANCZOS4)
    px_per_src = ((art_w / float(src_w)) + (art_h / float(src_h))) / 2.0

    def mapper(x: float, y: float) -> tuple[float, float]:
        return paste_x + (float(x) / float(src_w)) * art_w, paste_y + (float(y) / float(src_h)) * art_h

    return {
        "image": image,
        "provider": provider,
        "map": mapper,
        "px_per_src": px_per_src,
        "scale_mm": scale_mm,
        "off_mm": (off_x_mm, off_y_mm),
        "ppi": ppi,
    }


def map_rect(box, placed: dict, shape) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = [float(v) for v in box[:4]]
    ax, ay = placed["map"](x0, y0)
    bx, by = placed["map"](x1, y1)
    height, width = shape[:2]
    mx = int(round(min(ax, bx)))
    my = int(round(min(ay, by)))
    mw = max(2, int(round(abs(bx - ax))))
    mh = max(2, int(round(abs(by - ay))))
    mx = max(0, min(mx, width - 1))
    my = max(0, min(my, height - 1))
    mw = max(2, min(mw, width - mx))
    mh = max(2, min(mh, height - my))
    return mx, my, mw, mh


def _quad(line: dict, width: int, height: int) -> np.ndarray:
    raw = line.get("quad")
    if raw and len(raw) >= 4:
        return np.array([[float(p[0]), float(p[1])] for p in raw[:4]], np.float32)
    rect = line.get("rect") or (0, 0, 8, 8)
    x, y, bw, bh = [float(v) for v in rect[:4]]
    return np.array([[x, y], [x + bw, y], [x + bw, y + bh], [x, y + bh]], np.float32)


def _line_mask(bgr, lab, gold, quad, text, height, width):
    line_h = max(np.linalg.norm(quad[3] - quad[0]), np.linalg.norm(quad[2] - quad[1]), 1.0)
    pad = min(6, max(3, int(round(line_h * 0.25))))
    digit = is_short_numeral(text)
    x0, y0, x1, y1 = _bounds(quad, pad if not digit else 2, width, height)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    origin = np.array([x0, y0], np.float32)
    local = quad - origin
    roi_h, roi_w = y1 - y0, x1 - x0
    poly = np.zeros((roi_h, roi_w), np.uint8)
    cv2.fillPoly(poly, [local.round().astype(np.int32)], 1)
    core0 = poly.copy()
    if not digit:
        poly = cv2.dilate(poly, np.ones((2 * pad + 1, 5), np.uint8))
    if int(core0.sum()) < 4:
        return None
    lab_roi = lab[y0:y1, x0:x1]
    bg = np.median(lab_roi[core0 > 0], axis=0)
    dist = np.linalg.norm(lab_roi - bg, axis=2)
    text_px = (dist > 26) & (poly > 0)
    samples = dist[text_px]
    if samples.size == 0:
        return None
    threshold = np.percentile(samples, 60)
    ink_lab = np.median(lab_roi[text_px & (dist >= threshold)], axis=0)
    toward_ink = np.linalg.norm(lab_roi - ink_lab, axis=2)
    text_px = text_px & (toward_ink < dist * 1.1)
    ink_bgr = cv2.cvtColor(np.uint8([[ink_lab]]), cv2.COLOR_LAB2BGR)[0, 0]
    ink_hsv = cv2.cvtColor(np.uint8([[ink_bgr]]), cv2.COLOR_BGR2HSV)[0, 0]
    if not (12 <= int(ink_hsv[0]) <= 38 and int(ink_hsv[1]) > 60):
        text_px = text_px & ~gold[y0:y1, x0:x1]
    count, labels, stats, _cent = cv2.connectedComponentsWithStats(text_px.astype(np.uint8), 8)
    keep = np.zeros((roi_h, roi_w), np.bool_)
    for index in range(1, count):
        area = int(stats[index, cv2.CC_STAT_AREA])
        comp_w = int(stats[index, cv2.CC_STAT_WIDTH])
        comp_h = int(stats[index, cv2.CC_STAT_HEIGHT])
        component = labels == index
        inside = float(core0[component].mean()) if area else 0.0
        cy = int(stats[index, cv2.CC_STAT_TOP] + comp_h / 2)
        cx = int(stats[index, cv2.CC_STAT_LEFT] + comp_w / 2)
        cy = min(roi_h - 1, max(0, cy))
        cx = min(roi_w - 1, max(0, cx))
        small_ok = area <= 60 and core0[cy, cx] > 0
        thin = comp_w > 8 * max(comp_h, 1) and comp_h < 0.25 * line_h
        if area >= 2 and not thin and (inside >= 0.3 or small_ok):
            keep |= component
    if int(keep.sum()) < 2:
        return None
    ornament = _ornament_components(keep, text, line_h)
    if int(ornament.max()) > 0:
        keep = keep & (ornament == 0)
        if int(keep.sum()) < 2:
            return None
    colour = _core_bgr(bgr[y0:y1, x0:x1], keep)
    ys, xs = np.where(keep)
    gy0, gy1 = int(ys.min()), int(ys.max()) + 1
    gx0, gx1 = int(xs.min()), int(xs.max()) + 1
    glyph = keep[gy0:gy1, gx0:gx1]
    kernel = (3, 3) if digit else (5, 5)
    local = cv2.dilate(glyph.astype(np.uint8), np.ones(kernel, np.uint8))
    local = (local & poly[gy0:gy1, gx0:gx1]).astype(np.uint8) * 255
    full = np.zeros((height, width), np.uint8)
    full[y0 + gy0:y0 + gy1, x0 + gx0:x0 + gx1] = local
    page_ornament = np.zeros((height, width), np.uint8)
    if int(ornament.max()) > 0:
        page_ornament[y0:y1, x0:x1] = ornament
        halo = cv2.dilate(ornament, np.ones((13, 13), np.uint8))
        full[y0:y1, x0:x1][halo > 0] = 0
    return (x0 + gx0, y0 + gy0, x0 + gx1, y0 + gy1), colour, glyph, full, page_ornament


def _ornament_components(keep: np.ndarray, text: str, line_h: float) -> np.ndarray:
    """A compact mark at the end of a line, such as a heart, stays in the picture.

    Letter strokes and a dot the words already mention are not ornaments.
    """
    ornament = np.zeros(keep.shape, np.uint8)
    count, labels, stats, _cent = cv2.connectedComponentsWithStats((keep > 0).astype(np.uint8), 8)
    comps = []
    for index in range(1, count):
        x, _y, width, height, area = [int(v) for v in stats[index][:5]]
        if area < 8 or width < 2 or height < 2:
            continue
        comps.append((index, x, width, height, area))
    if len(comps) < 2 or line_h <= 0:
        return ornament
    comps.sort(key=lambda item: item[1])
    gaps = [right[1] - (left[1] + left[2]) for left, right in zip(comps, comps[1:])]
    mark = ".,;:!?·•∙⋅-–—\"'"

    def symbol(comp, gap) -> bool:
        _index, _x, width, height, _area = comp
        if gap < max(3.0, line_h * 0.18):
            return False
        if width > line_h * 1.45 or height > line_h * 1.7:
            return False
        return max(width, height) / float(min(width, height)) <= 2.6

    tail = str(text or "").rstrip()
    if not (tail and tail[-1] in mark):
        typical = float(np.median(gaps[:-1])) if len(gaps) > 1 else float(gaps[-1])
        if symbol(comps[-1], gaps[-1]) and gaps[-1] >= max(typical * 1.35, max(3.0, line_h * 0.18)):
            ornament[labels == comps[-1][0]] = 255
    head = str(text or "").lstrip()
    if not (head and head[0] in mark) and len(comps) >= 2:
        typical = float(np.median(gaps[1:])) if len(gaps) > 1 else float(gaps[0])
        if symbol(comps[0], gaps[0]) and gaps[0] >= max(typical * 1.35, max(3.0, line_h * 0.18)):
            ornament[labels == comps[0][0]] = 255
    return ornament


def _bounds(quad: np.ndarray, pad: int, width: int, height: int) -> tuple[int, int, int, int]:
    xs = quad[:, 0]
    ys = quad[:, 1]
    x0 = max(0, int(np.floor(xs.min())) - pad)
    y0 = max(0, int(np.floor(ys.min())) - pad)
    x1 = min(width, int(np.ceil(xs.max())) + pad + 1)
    y1 = min(height, int(np.ceil(ys.max())) + pad + 1)
    return x0, y0, x1, y1


def _disentangle(meta: list) -> None:
    """Give overlapping ink back to the nearer line."""
    for item in meta:
        others = []
        ax0, ay0, ax1, ay1 = item["ink"]
        for other in meta:
            if other is item:
                continue
            bx0, by0, bx1, by1 = other["ink"]
            if min(ax1, bx1) - max(ax0, bx0) > 0 and min(ay1, by1) - max(ay0, by0) > 0:
                others.append(other)
        if not others:
            continue
        glyph = item["glyph"]
        count, labels, _stats, cents = cv2.connectedComponentsWithStats(glyph.astype(np.uint8), 8)
        if count <= 2:
            continue
        keep = np.zeros_like(glyph, np.bool_)
        ox, oy = item["ink"][0], item["ink"][1]
        for index in range(1, count):
            centre = cents[index] + np.array([ox, oy], np.float32)
            if all(_quad_distance(centre, item["quad"]) <= _quad_distance(centre, other["quad"]) for other in others):
                keep |= labels == index
        if int(keep.sum()) < 2:
            continue
        ys, xs = np.where(keep)
        item["glyph"] = keep[int(ys.min()):int(ys.max()) + 1, int(xs.min()):int(xs.max()) + 1]
        item["ink"] = (int(xs.min()) + ox, int(ys.min()) + oy, int(xs.max()) + 1 + ox, int(ys.max()) + 1 + oy)
        item["changed"] = True


def _rebuild_tight(item: dict, height: int, width: int) -> None:
    digit = is_short_numeral(item["line"].get("text"))
    kernel = (3, 3) if digit else (5, 5)
    local = cv2.dilate(item["glyph"].astype(np.uint8), np.ones(kernel, np.uint8))
    full = np.zeros((height, width), np.uint8)
    x0, y0, _x1, _y1 = [int(v) for v in item["ink"]]
    hh, ww = local.shape[:2]
    y1 = min(height, y0 + hh)
    x1 = min(width, x0 + ww)
    if y1 <= y0 or x1 <= x0:
        item["tight"] = full
        return
    full[y0:y1, x0:x1] = (local[:y1 - y0, :x1 - x0] > 0).astype(np.uint8) * 255
    item["tight"] = full


def _quad_distance(point, quad) -> float:
    quad = np.array(quad, np.float32)
    start = (quad[0] + quad[3]) / 2.0
    end = (quad[1] + quad[2]) / 2.0
    direction = end - start
    length = float(np.linalg.norm(direction)) or 1.0
    normal = np.array([-direction[1], direction[0]], np.float32) / length
    height = (
        float(np.linalg.norm(quad[3] - quad[0])) + float(np.linalg.norm(quad[2] - quad[1]))
    ) / 2.0 or 1.0
    return abs(float(np.dot(point - start, normal))) / height


def _inpaint(src: np.ndarray, mask: np.ndarray, meta: list, protect: np.ndarray | None = None) -> np.ndarray:
    mask_u8 = ((mask > 0).astype(np.uint8)) * 255
    painted = cv2.inpaint(src, mask_u8, 5, cv2.INPAINT_TELEA).astype(np.float32)
    rng = np.random.default_rng(3)
    noise = cv2.GaussianBlur(rng.normal(0, 1, src.shape[:2]).astype(np.float32), (0, 0), 0.8)
    noise = noise / (float(noise.std()) + 1e-6) * 1.2
    feather = cv2.GaussianBlur(mask_u8.astype(np.float32) / 255.0, (5, 5), 0)[..., None]
    painted = np.clip(painted + noise[..., None] * feather, 0, 255).astype(np.uint8)
    residual = _residual_mask(painted, meta)
    if protect is not None:
        residual[protect > 0] = 0
    if int(residual.max()) == 0:
        return painted
    return cv2.inpaint(painted, residual, 4, cv2.INPAINT_TELEA)


def _residual_mask(clean: np.ndarray, meta: list) -> np.ndarray:
    lab = cv2.cvtColor(clean, cv2.COLOR_BGR2LAB).astype(np.float32)
    median = cv2.cvtColor(cv2.medianBlur(clean, 9), cv2.COLOR_BGR2LAB).astype(np.float32)
    odd = np.linalg.norm(lab - median, axis=2) > 28
    zone = np.zeros(odd.shape, np.uint8)
    height, width = odd.shape
    for item in meta:
        if is_short_numeral(item["line"].get("text")):
            continue
        x0, y0, x1, y1 = [int(v) for v in item["ink"]]
        zone[max(0, y0 - 4):min(height, y1 + 4), max(0, x0 - 2):min(width, x1 + 2)] = 1
    odd = odd & (zone > 0)
    count, labels, stats, _cent = cv2.connectedComponentsWithStats(odd.astype(np.uint8), 8)
    specks = np.zeros(odd.shape, np.uint8)
    for index in range(1, count):
        if int(stats[index, cv2.CC_STAT_AREA]) <= 12:
            specks[labels == index] = 255
    if int(specks.max()) == 0:
        return specks
    return cv2.dilate(specks, np.ones((5, 5), np.uint8))


def _enlarge(clean: np.ndarray, art_w: int, art_h: int) -> tuple[np.ndarray, str]:
    """Lanczos, then a light colour match. Real-ESRGAN only when VECTOR_ESRGAN=1."""
    from host_paths import esrgan_enabled

    token = ""
    if esrgan_enabled():
        try:
            from ai_enhancements import _get_replicate_token

            token = (_get_replicate_token() or "").strip()
        except Exception:
            token = ""
    if token:
        enhanced = _esrgan(clean)
        if enhanced is not None:
            resized = cv2.resize(enhanced, (art_w, art_h), interpolation=cv2.INTER_LANCZOS4)
            return _colorfix(resized, clean), "Real-ESRGAN"
    resized = cv2.resize(clean, (art_w, art_h), interpolation=cv2.INTER_LANCZOS4)
    return _colorfix(resized, clean), "Lanczos"


def _esrgan(clean: np.ndarray):
    import tempfile

    try:
        from ai_upscale import apply_ai_upscale
    except Exception:
        return None
    folder = tempfile.mkdtemp(prefix="vector-esrgan-")
    src = os.path.join(folder, "src.png")
    try:
        cv2.imwrite(src, clean)
        result = apply_ai_upscale(src, {
            "trim_w_mm": 148,
            "trim_h_mm": 210,
            "bleed_mm": 0,
            "output_path": os.path.join(folder, "up.png"),
        })
    except Exception:
        return None
    path = str((result or {}).get("enhanced_path") or "")
    if not path or not os.path.exists(path) or result.get("used_original"):
        return None
    if str(result.get("provider") or "") not in ("replicate", "stub"):
        return None
    loaded = cv2.imread(path, cv2.IMREAD_COLOR)
    return loaded if loaded is not None else None


def _colorfix(up: np.ndarray, ref: np.ndarray) -> np.ndarray:
    """Chroma from the clean plate, luminance detail from the upscale."""
    factor = up.shape[1] / float(ref.shape[1])
    ref_lab = cv2.cvtColor(ref, cv2.COLOR_BGR2LAB).astype(np.float32)
    up_lab = cv2.cvtColor(up, cv2.COLOR_BGR2LAB).astype(np.float32)
    big = cv2.resize(ref_lab, (up.shape[1], up.shape[0]), interpolation=cv2.INTER_CUBIC)
    chroma = cv2.GaussianBlur(big[:, :, 1:], (0, 0), max(0.3, factor * 0.35))
    small = cv2.resize(up, (ref.shape[1], ref.shape[0]), interpolation=cv2.INTER_AREA)
    small_l = cv2.cvtColor(small, cv2.COLOR_BGR2LAB).astype(np.float32)[:, :, 0]
    residual = cv2.GaussianBlur(ref_lab[:, :, 0] - small_l, (0, 0), 0.8)
    lifted = cv2.resize(residual, (up.shape[1], up.shape[0]), interpolation=cv2.INTER_CUBIC)
    merged = np.dstack([up_lab[:, :, 0] + lifted, chroma[:, :, 0], chroma[:, :, 1]])
    return cv2.cvtColor(np.clip(merged, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)


def find_badges(bgr: np.ndarray) -> list:
    """Filled circles that hold a mark. The mark stays in the picture."""
    height, width = bgr.shape[:2]
    if min(height, width) < 40:
        return []
    gray = cv2.medianBlur(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY), 5)
    max_r = max(28, min(48, min(height, width) // 12))
    found = cv2.HoughCircles(
        gray, cv2.HOUGH_GRADIENT, dp=1.2, minDist=28,
        param1=70, param2=24, minRadius=12, maxRadius=max_r,
    )
    if found is None:
        return []
    badges = []
    for circle in found[0]:
        cx, cy, radius = [float(v) for v in circle]
        if not _badge_disk(bgr, cx, cy, radius):
            continue
        if any(abs(cx - old["cx"]) < 8 and abs(cy - old["cy"]) < 8 for old in badges):
            continue
        badges.append({"cx": cx, "cy": cy, "radius": radius})
    return badges


def _badge_disk(bgr: np.ndarray, cx: float, cy: float, radius: float) -> bool:
    """A flat filled disk, with a mark in the middle, sitting on a different ground.

    A leaf can trip the circle finder. A real badge has an even ring of colour
    and a hard edge against whatever is outside it.
    """
    height, width = bgr.shape[:2]
    radius = float(radius)
    if radius < 12:
        return False
    if cx - radius < 2 or cy - radius < 2 or cx + radius > width - 3 or cy + radius > height - 3:
        return False
    pad = int(radius * 1.45) + 2
    x0 = max(0, int(cx) - pad)
    y0 = max(0, int(cy) - pad)
    x1 = min(width, int(cx) + pad + 1)
    y1 = min(height, int(cy) + pad + 1)
    roi = bgr[y0:y1, x0:x1]
    yy, xx = np.ogrid[:roi.shape[0], :roi.shape[1]]
    dist = np.sqrt((xx - (cx - x0)) ** 2 + (yy - (cy - y0)) ** 2)
    ring = (dist >= radius * 0.50) & (dist <= radius * 0.82)
    inner = dist <= max(3.0, radius * 0.38)
    outer = (dist >= radius * 1.08) & (dist <= radius * 1.32)
    ring_px = roi[ring]
    inner_px = roi[inner]
    outer_px = roi[outer]
    if ring_px.shape[0] < 20 or inner_px.shape[0] < 8 or outer_px.shape[0] < 20:
        return False
    ring_mean = ring_px.astype(np.float32).mean(axis=0)
    if float(ring_px.astype(np.float32).std(axis=0).mean()) > 18:
        return False
    inner_gap = float(np.linalg.norm(inner_px.astype(np.float32).mean(axis=0) - ring_mean))
    edge_gap = float(np.linalg.norm(outer_px.astype(np.float32).mean(axis=0) - ring_mean))
    return inner_gap > 36 and edge_gap > 80


def _sits_in_badge(line: dict, marks: list | None) -> bool:
    """True when this OCR box is a mark inside a filled circle, not a line beside one."""
    rect = (line or {}).get("rect")
    if not rect or not marks:
        return False
    x, y, bw, bh = [float(v) for v in rect[:4]]
    if bw <= 0 or bh <= 0:
        return False
    cx = x + bw / 2.0
    cy = y + bh / 2.0
    for mark in marks:
        if mark.get("kind") != "badge":
            continue
        radius = float(mark.get("radius") or 0)
        if radius <= 0:
            continue
        dx = cx - float(mark.get("cx") or 0)
        dy = cy - float(mark.get("cy") or 0)
        if max(bw, bh) <= radius * 1.7 and dx * dx + dy * dy <= (radius * 0.9) ** 2:
            return True
    return False


def _badge_protect(marks: list | None, height: int, width: int) -> np.ndarray:
    protect = np.zeros((height, width), np.uint8)
    for mark in marks or []:
        if mark.get("kind") != "badge":
            continue
        cx, cy = int(round(float(mark["cx"]))), int(round(float(mark["cy"])))
        radius = max(2, int(round(float(mark["radius"]))) + 2)
        cv2.circle(protect, (cx, cy), radius, 255, -1)
    return protect


def _core_bgr(image: np.ndarray, keep: np.ndarray) -> np.ndarray:
    """Median of the stroke body. The fringe against the ground is left out."""
    pixels = image[keep]
    if pixels.shape[0] < 4:
        return np.array([0, 0, 0], np.float32)
    dist = cv2.distanceTransform((keep > 0).astype(np.uint8), cv2.DIST_L2, 3)
    peak = float(dist.max()) if dist.size else 0.0
    if peak < 0.8:
        return np.median(pixels, axis=0)
    core = (keep > 0) & (dist >= peak * 0.45)
    if int(np.count_nonzero(core)) < 4:
        return np.median(pixels, axis=0)
    return np.median(image[core], axis=0)


def _bgr_hex(colour) -> str:
    blue, green, red = [int(np.clip(round(float(v)), 0, 255)) for v in colour[:3]]
    return f"#{red:02x}{green:02x}{blue:02x}"
