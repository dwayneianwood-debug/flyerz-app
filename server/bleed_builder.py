"""One bleed sample and one press-file check for every upload.

Quick-print, the review compile, and image uploads all call this module.
A short light sliver (Canva's 1px white column, including the band it becomes
after a low-resolution scale) is not the colour that is continued. A real
light border that keeps going is left as it is. The 1px pixel-drift radius
is unchanged: the sliver is stepped over before that sample.
"""

from __future__ import annotations

import os

import numpy as np

# About 4.4 mm at 300 dpi. Wide enough for a low-resolution JPEG block and a
# page that is a fraction of a millimetre short, and short of a real border.
SLIVER_CAP_PX = 52
# CIE76. The press file's 5 mm bleed has to stay this close to the artwork.
BLEED_DELTA_E = 10.0
_SAMPLE_PX = 3


def _is_light(pixels: np.ndarray) -> np.ndarray:
    """True where a pixel is paper, white, or a light grey fringe.

    RGB light is a high mean. DeviceCMYK light is little ink. A JPEG halo
    beside yellow keeps a little yellow ink and is still paper, not artwork.
    """
    values = np.asarray(pixels, np.float32)
    if values.shape[-1] >= 4:
        plate = values[..., :4]
        ink = plate.sum(axis=-1)
        pale = (plate[..., 0] < 45.0) & (plate[..., 1] < 45.0) & (plate[..., 3] < 20.0) & (ink < 170.0)
        return (ink < 80.0) | pale
    return values[..., :3].mean(axis=-1) > 186.0


def fringe_depths(strip: np.ndarray) -> np.ndarray:
    """How many outer columns are a light sliver, per row.

    Column 0 is the outer edge. A light run that still has artwork behind it
    inside the cap is skipped. A light run that fills the cap is the artwork
    (a real pale border) and stays.
    """
    image = np.asarray(strip)
    if image.ndim == 2:
        image = image[:, :, None]
    count = int(image.shape[0])
    width = int(image.shape[1])
    depths = np.zeros(count, np.int32)
    if count < 1 or width < 4:
        return depths
    cap = min(SLIVER_CAP_PX, width - 3)
    if cap < 1:
        return depths
    anchor_at = min(width - 1, cap)
    anchor_end = min(width, anchor_at + _SAMPLE_PX)
    anchor = np.median(image[:, anchor_at:anchor_end].astype(np.float32), axis=1)
    window = image[:, :cap].astype(np.float32)
    light = _is_light(window)
    # A 1px white column scaled up becomes a ramp, not pure paper. It is still
    # lighter than the artwork behind it. A pale border that fills the cap is
    # the same colour as the anchor, so the distance test leaves it alone.
    if window.shape[-1] >= 4:
        lighter = (anchor[:, None, :4].sum(axis=-1) - window[..., :4].sum(axis=-1)) >= 28.0
    else:
        lighter = (window[..., :3].mean(axis=-1) - anchor[:, None, :3].mean(axis=-1)) >= 18.0
    distance = np.linalg.norm(window - anchor[:, None, :], axis=2)
    fringe = (light | lighter) & (distance >= 36.0)
    ink = ~fringe
    has_ink = ink.any(axis=1)
    first = np.argmax(ink, axis=1).astype(np.int32)
    leading = fringe[:, 0] & has_ink
    depths[leading] = first[leading]
    return depths


def colors_past_fringe(strip: np.ndarray) -> np.ndarray:
    """One colour per row, read from the artwork just past a light sliver."""
    image = np.asarray(strip)
    if image.ndim == 2:
        image = image[:, :, None]
    count, width, channels = image.shape
    if count < 1 or width < 1:
        return np.zeros((max(count, 0), channels), np.uint8)
    depths = fringe_depths(image)
    out = np.empty((count, channels), np.float32)
    for index in range(count):
        start = min(int(depths[index]), width - 1)
        end = min(width, start + _SAMPLE_PX)
        out[index] = np.median(image[index, start:end].astype(np.float32), axis=0)
    return np.clip(np.round(out), 0, 255).astype(np.uint8)


def repair_light_fringe(image: np.ndarray) -> np.ndarray:
    """Replace a short light rim with the artwork directly inside it.

    The trim's real edge is what later bleed samples, including the 1px
    pixel-drift read. A pale border that continues is not rewritten.
    """
    if image is None or getattr(image, "ndim", 0) != 3:
        return image
    if image.shape[0] < 8 or image.shape[1] < 8:
        return image
    out = np.array(image, copy=True)

    def paint(view: np.ndarray) -> None:
        depths = fringe_depths(view)
        if int(depths.max()) < 1:
            return
        rows = np.arange(view.shape[0])
        source = view[rows, np.clip(depths, 0, view.shape[1] - 1)]
        for index in range(view.shape[0]):
            depth = int(depths[index])
            if depth > 0:
                view[index, :depth] = source[index]

    paint(out)
    paint(out[:, ::-1])
    turned = np.transpose(out, (1, 0, 2))
    paint(turned)
    paint(turned[:, ::-1])
    return out


def _delta_e(left, right) -> float:
    """CIE76 between two mean sRGB colours. Matches the press seam check."""
    channel = np.asarray(left, dtype=np.float64) / 255.0
    other = np.asarray(right, dtype=np.float64) / 255.0

    def lab(rgb):
        def linear(value):
            return np.where(value <= 0.04045, value / 12.92, ((value + 0.055) / 1.055) ** 2.4)

        red, green, blue = linear(np.asarray(rgb, np.float64))
        x = red * 0.4124564 + green * 0.3575761 + blue * 0.1804375
        y = red * 0.2126729 + green * 0.7151522 + blue * 0.0721750
        z = red * 0.0193339 + green * 0.1191920 + blue * 0.9503041
        white = (0.95047, 1.0, 1.08883)

        def pivot(value):
            return np.where(value > 0.008856, np.cbrt(value), 7.787 * value + 16.0 / 116.0)

        fx, fy, fz = (pivot(x / white[0]), pivot(y / white[1]), pivot(z / white[2]))
        return np.array([116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz)])

    gap = lab(channel) - lab(other)
    return float(np.sqrt(np.sum(gap * gap)))


def _mean_rgb(pixels: np.ndarray) -> np.ndarray:
    """Robust colour of a band. One white sliver column does not move it."""
    flat = np.asarray(pixels, np.float32).reshape(-1, 3)
    if flat.size < 3:
        return np.zeros(3, np.float32)
    if flat.shape[0] > 8:
        light = flat.mean(axis=1) > 186.0
        if light.any() and not light.all():
            flat = flat[~light]
    return np.median(flat, axis=0)


def _side_report(name: str, art: np.ndarray, bleed: np.ndarray, outer: np.ndarray, seam: np.ndarray) -> dict:
    """art, bleed, outer and seam are RGB samples. art is already past a sliver."""
    art_mean = _mean_rgb(art)
    bleed_mean = _mean_rgb(bleed)
    outer_mean = _mean_rgb(outer)
    seam_mean = _mean_rgb(seam)
    delta = _delta_e(art_mean, bleed_mean)
    outer_delta = _delta_e(art_mean, outer_mean)
    seam_delta = _delta_e(art_mean, seam_mean)
    flags = []
    if bleed.size < 3:
        flags.append("missing")
    else:
        light = float(bleed_mean.mean()) > 186.0 and float(art_mean.mean()) < 170.0
        flat = float(np.std(bleed.astype(np.float32))) < 18.0
        if light and flat:
            flags.append("light-band")
        if seam_delta > BLEED_DELTA_E:
            flags.append("seam")
        if max(delta, outer_delta) > BLEED_DELTA_E:
            flags.append("mismatch")
    score = max(delta, outer_delta, seam_delta)
    if "missing" in flags:
        score = max(score, 100.0)
    return {
        "side": name,
        "delta": round(float(score), 2),
        "ok": not flags,
        "flags": flags,
    }


def _orient_bleed(band: np.ndarray, trim_first: bool) -> np.ndarray:
    """Column 0 touches the trim. The last column is the page edge."""
    if band is None or band.size == 0 or band.ndim != 3:
        return np.zeros((0, 0, 3), np.uint8)
    return band if trim_first else band[:, ::-1]


def check_page_array(rgb: np.ndarray, trim_box, media_box, scale: float) -> dict:
    """Score one rendered page. trim_box and media_box are pixmap pixels, y down."""
    image = np.asarray(rgb)
    height, width = image.shape[:2]
    tx0, ty0, tx1, ty1 = [int(round(float(value))) for value in trim_box]
    mx0, my0, mx1, my1 = [int(round(float(value))) for value in media_box]
    tx0, ty0 = max(0, min(width, tx0)), max(0, min(height, ty0))
    tx1, ty1 = max(0, min(width, tx1)), max(0, min(height, ty1))
    mx0, my0 = max(0, min(width, mx0)), max(0, min(height, my0))
    mx1, my1 = max(0, min(width, mx1)), max(0, min(height, my1))
    # SLIVER_CAP_PX is a 300 dpi count. scale is pixmap pixels per PDF point.
    search = max(4, int(round(SLIVER_CAP_PX * float(scale) * 72.0 / 300.0)))
    inset = max(1, int(round(0.4 / 25.4 * float(scale) * 72.0)))

    def art_reference(strip: np.ndarray) -> np.ndarray:
        """Column 0 touches the trim. Skip a light sliver, then keep the artwork.

        The sliver is not always the first column. The seam cover can already
        have replaced that pixel and left the rest of the white column one
        pixel further in. A pale border that fills the sample cap stays.
        """
        if strip.size < 3 or getattr(strip, "ndim", 0) != 3 or strip.shape[1] < 1:
            return np.zeros((0, 3), np.uint8)
        want = max(2, inset)
        light = _is_light(strip)
        cap = min(SLIVER_CAP_PX, int(strip.shape[1]))
        kept = []
        for index in range(strip.shape[0]):
            row = strip[index]
            flags = light[index]
            chosen = []
            for col in range(int(row.shape[0])):
                if col < cap and bool(flags[col]):
                    inner = flags[col + 1:cap]
                    if inner.size and bool((~inner).any()):
                        continue
                chosen.append(row[col])
                if len(chosen) >= want:
                    break
            if chosen:
                kept.append(np.stack(chosen).reshape(-1, 3))
        if not kept:
            return np.zeros((0, 3), np.uint8)
        return np.concatenate(kept, axis=0)

    def score(name: str, art: np.ndarray, bleed: np.ndarray) -> dict:
        bleed = _orient_bleed(bleed, True)
        columns = int(bleed.shape[1]) if bleed.ndim == 3 else 0
        outer_n = max(1, int(round(columns * 0.64))) if columns else 1
        seam_n = max(1, min(inset, columns)) if columns else 1
        return _side_report(
            name,
            art_reference(art),
            bleed,
            bleed[:, -outer_n:] if columns else bleed,
            bleed[:, :seam_n] if columns else bleed,
        )

    def span(start: int, end: int) -> tuple[int, int]:
        length = max(1, end - start)
        margin = max(1, length // 12)
        low = start + margin
        high = max(low + 1, end - margin)
        return low, high

    y0, y1 = span(ty0, ty1)
    x0, x1 = span(tx0, tx1)
    sides = [
        score(
            "right",
            image[y0:y1, max(0, tx1 - search):tx1][:, ::-1],
            image[y0:y1, tx1:mx1],
        ),
        score(
            "left",
            image[y0:y1, tx0:min(width, tx0 + search)],
            image[y0:y1, mx0:tx0][:, ::-1],
        ),
        score(
            "bottom",
            np.transpose(image[max(0, ty1 - search):ty1, x0:x1], (1, 0, 2))[:, ::-1],
            np.transpose(image[ty1:my1, x0:x1], (1, 0, 2)),
        ),
        score(
            "top",
            np.transpose(image[ty0:min(height, ty0 + search), x0:x1], (1, 0, 2)),
            np.transpose(image[my0:ty0, x0:x1], (1, 0, 2))[:, ::-1],
        ),
    ]
    return {"ok": all(side["ok"] for side in sides), "sides": sides}


def staff_note(report: dict) -> str:
    """The sentence staff see. Empty when the bleed matches."""
    if not report or report.get("ok"):
        return ""
    worst = []
    for page in report.get("pages") or []:
        for side in page.get("sides") or []:
            if side.get("ok"):
                continue
            label = side["side"]
            if int(page.get("page") or 1) > 1 or len(report.get("pages") or []) > 1:
                label = f"page {page.get('page')} {label}"
            flags = ",".join(side.get("flags") or [])
            worst.append(f"{label} ΔE {float(side.get('delta') or 0):.0f}" + (f" ({flags})" if flags else ""))
    detail = "; ".join(worst) or "the edge does not match"
    return (
        f"The bleed does not continue the artwork ({detail}). "
        "Check that edge before printing."
    )


def _render_dpi(width_pt: float, height_pt: float) -> float:
    long_pt = max(float(width_pt), float(height_pt), 1.0)
    dpi = min(120.0, 1400.0 * 72.0 / long_pt)
    return max(72.0, dpi)


def check_press_pdf(path: str, trim_w_mm: float, trim_h_mm: float, bleed_mm: float = 5.0) -> dict:
    """Render the press PDF and compare each side's bleed with the art inside the trim."""
    import pymupdf as fitz

    if not path or not os.path.isfile(path):
        report = {"ok": False, "pages": [], "note": ""}
        report["note"] = "The bleed check could not read the press file. Check the edges before printing."
        return report
    doc = fitz.open(path)
    pages = []
    try:
        if doc.page_count < 1:
            report = {"ok": False, "pages": [], "note": "The bleed check could not read the press file. Check the edges before printing."}
            return report
        for index, page in enumerate(doc):
            dpi = _render_dpi(page.rect.width, page.rect.height)
            scale = dpi / 72.0
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False, colorspace=fitz.csRGB)
            rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3]
            trim = page.trimbox if page.trimbox.width > 2 and page.trimbox.height > 2 else None
            if trim is None:
                # No trim box: the product trim sits one bleed inside the media.
                bleed_pt = float(bleed_mm) * 72.0 / 25.4
                trim_w = float(trim_w_mm) * 72.0 / 25.4
                trim_h = float(trim_h_mm) * 72.0 / 25.4
                trim = fitz.Rect(bleed_pt, bleed_pt, bleed_pt + trim_w, bleed_pt + trim_h)
            media = page.rect
            scored = check_page_array(
                rgb,
                (trim.x0 * scale, trim.y0 * scale, trim.x1 * scale, trim.y1 * scale),
                (media.x0 * scale, media.y0 * scale, media.x1 * scale, media.y1 * scale),
                scale,
            )
            pages.append({"page": index + 1, "ok": scored["ok"], "sides": scored["sides"]})
    finally:
        doc.close()
    report = {"ok": all(page["ok"] for page in pages) if pages else False, "pages": pages, "note": ""}
    report["note"] = staff_note(report)
    return report


def attach_result(result: dict, bleed_opts: dict | None = None) -> dict:
    """Record the check on a review-path result. A failure is a staff check, not a silent pass."""
    if not isinstance(result, dict):
        return result
    opts = bleed_opts or {}
    path = str(result.get("correctedPath") or "")
    if not path.lower().endswith(".pdf") or not os.path.isfile(path):
        return result
    trim_w = float(opts.get("targetWidth") or opts.get("trimWidth") or 0)
    trim_h = float(opts.get("targetHeight") or opts.get("trimHeight") or 0)
    bleed = float(opts.get("adjustableBleedSize") or opts.get("bleedMm") or 5)
    try:
        quality = check_press_pdf(path, trim_w, trim_h, bleed)
    except Exception as exc:
        quality = {
            "ok": False,
            "pages": [],
            "note": f"The bleed check could not read the press file ({str(exc)[:120]}). Check the edges before printing.",
        }
    result["bleedQuality"] = quality
    if not quality.get("ok"):
        checks = result.setdefault("checks", [])
        checks.append({
            "name": "Bleed quality",
            "passed": False,
            "autoFixed": False,
            "message": quality.get("note") or "The bleed does not continue the artwork. Check that edge before printing.",
            "details": "Staff note. The press file is still available.",
        })
    return result
