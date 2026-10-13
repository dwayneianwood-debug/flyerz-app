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

# 0.5 mm at 300 dpi. A 1 px white column at 72 ppi becomes about 4 device
# pixels. Only that pale line is stepped over. A real light border, including
# a correct 2.5 mm bleed, is the artwork and is continued.
SLIVER_CAP_PX = 6
PALE_MM = 0.5
MAX_PULL_MM = 2.0
# CIE76. A flat band has to stay this close. A textured continuation can sit a
# little further off without being a different colour painted into the bleed.
BLEED_DELTA_E = 10.0
_HARD_DELTA_E = 22.0
_FLAT_STD = 18.0
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


def _is_paper(pixels: np.ndarray) -> np.ndarray:
    """A pale or white edge line, not a light shirt or a yellow border.

    Column 0 of a Canva photo is often pure white. A thin grey halo is the
    same line. Artwork that is merely lighter than the inside of the page
    is not paper, and it is the colour the bleed has to continue.
    """
    values = np.asarray(pixels, np.float32)
    if values.shape[-1] >= 4:
        plate = values[..., :4]
        return plate.sum(axis=-1) < 18.0
    rgb = values[..., :3]
    mean = rgb.mean(axis=-1)
    chroma = rgb.max(axis=-1) - rgb.min(axis=-1)
    return (mean > 246.0) | ((mean > 206.0) & (chroma < 14.0))


def _ink_amount(image: np.ndarray) -> np.ndarray:
    """Higher means more colour. CMYK uses total ink. RGB uses darkness."""
    values = np.asarray(image, np.float32)
    if values.shape[-1] >= 4:
        return values[..., :4].sum(axis=-1)
    return 255.0 - values[..., :3].mean(axis=-1)


def fringe_depths(strip: np.ndarray) -> np.ndarray:
    """How many outer columns are a pale line, per row. Never more than 0.5 mm.

    Column 0 is the outer edge. A pale run is skipped only while artwork sits
    inside the cap. A pale border that fills the cap is the artwork.

    A JPEG or Ghostscript halo is not pure paper: it keeps a little of the
    colour beside it. That run is still a sliver when the pixel just inside
    the cap is plainly darker. The depth never goes past the cap.
    """
    image = np.asarray(strip)
    if image.ndim == 2:
        image = image[:, :, None]
    count = int(image.shape[0])
    width = int(image.shape[1])
    depths = np.zeros(count, np.int32)
    if count < 1 or width < 2:
        return depths
    cap = min(SLIVER_CAP_PX, width - 1)
    if cap < 1:
        return depths
    look = min(cap + 2, width - 1)
    window = image[:, :look].astype(np.float32)
    paper = np.zeros((count, look), dtype=bool)
    paper[:, :cap] = _is_paper(window[:, :cap])
    amount = _ink_amount(window)
    inner = amount[:, -1]
    halo = amount + 120.0 < inner[:, None]
    fringe = paper | halo
    ink = ~fringe
    has_ink = ink.any(axis=1)
    first = np.argmax(ink, axis=1).astype(np.int32)
    leading = fringe[:, 0] & has_ink
    depths[leading] = np.minimum(first[leading], cap)
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


def _row_colors(strip: np.ndarray) -> np.ndarray:
    """One RGB colour per row, past a pale line. Column 0 is the outer edge."""
    colors = colors_past_fringe(strip)
    if colors.shape[-1] >= 4:
        plate = colors[:, :4].astype(np.float32) / 255.0
        red = (1.0 - plate[:, 0]) * (1.0 - plate[:, 3])
        green = (1.0 - plate[:, 1]) * (1.0 - plate[:, 3])
        blue = (1.0 - plate[:, 2]) * (1.0 - plate[:, 3])
        return np.clip(np.round(np.stack([red, green, blue], axis=1) * 255.0), 0, 255).astype(np.uint8)
    return colors[:, :3]


def edge_row_delta(art_strip: np.ndarray, bleed_strip: np.ndarray) -> float:
    """90th percentile CIE76. Art column 0 is the trim edge. Bleed column 0 touches the trim."""
    art = _row_colors(art_strip)
    bleed_img = np.asarray(bleed_strip)
    if bleed_img.ndim == 2:
        bleed_img = bleed_img[:, :, None]
    count = min(art.shape[0], bleed_img.shape[0])
    if count < 4 or bleed_img.shape[1] < 1:
        return 0.0
    outer_n = max(1, int(round(bleed_img.shape[1] * 0.64)))
    outer = bleed_img[:count, -outer_n:]
    if outer.shape[-1] >= 4:
        plate = outer[..., :4].astype(np.float32) / 255.0
        red = (1.0 - plate[..., 0]) * (1.0 - plate[..., 3])
        green = (1.0 - plate[..., 1]) * (1.0 - plate[..., 3])
        blue = (1.0 - plate[..., 2]) * (1.0 - plate[..., 3])
        outer_rgb = np.clip(np.round(np.stack([red, green, blue], axis=-1) * 255.0), 0, 255)
    else:
        outer_rgb = outer[..., :3]
    bleed = np.median(outer_rgb, axis=1)
    step = max(1, count // 80)
    scores = [_delta_e(art[index], bleed[index]) for index in range(0, count, step)]
    return float(np.percentile(scores, 90)) if scores else 0.0


def finish_raster_bleed(canvas: np.ndarray, bleed_px: int) -> tuple[np.ndarray, dict]:
    """Self-check a raster bleed. A mismatched edge is mirrored from the cleaned art.

    The canvas is trim in the middle and bleed_px around it. Returns the canvas
    and the method used on each edge.
    """
    out = np.ascontiguousarray(canvas)
    if out.ndim != 3 or out.shape[2] < 3 or bleed_px < 1:
        return out, {side: "replicate" for side in ("top", "bottom", "left", "right")}
    height, width = out.shape[:2]
    methods = {}

    def side_views(name: str):
        cap = SLIVER_CAP_PX + 4
        if name == "right":
            return out[:, width - bleed_px - cap: width - bleed_px][:, ::-1], out[:, width - bleed_px:]
        if name == "left":
            return out[:, bleed_px: bleed_px + cap], out[:, :bleed_px][:, ::-1]
        if name == "bottom":
            return (
                np.transpose(out[height - bleed_px - cap: height - bleed_px, :], (1, 0, 2))[:, ::-1],
                np.transpose(out[height - bleed_px:, :], (1, 0, 2)),
            )
        return (
            np.transpose(out[bleed_px: bleed_px + cap, :], (1, 0, 2)),
            np.transpose(out[:bleed_px, :], (1, 0, 2))[:, ::-1],
        )

    def paint_mirror(name: str) -> None:
        span = min(bleed_px, max(2, (width if name in ("left", "right") else height) // 5))
        if name == "right":
            src = out[:, width - bleed_px - span: width - bleed_px]
            block = src[:, ::-1]
            if block.shape[1] < bleed_px:
                extra = np.repeat(block[:, :1], bleed_px - block.shape[1], axis=1)
                block = np.concatenate([block, extra], axis=1)
            out[:, width - bleed_px:] = block[:, :bleed_px]
        elif name == "left":
            src = out[:, bleed_px: bleed_px + span]
            block = src[:, ::-1]
            if block.shape[1] < bleed_px:
                extra = np.repeat(block[:, -1:], bleed_px - block.shape[1], axis=1)
                block = np.concatenate([extra, block], axis=1)
            out[:, :bleed_px] = block[:, -bleed_px:]
        elif name == "bottom":
            src = out[height - bleed_px - span: height - bleed_px, :]
            block = src[::-1, :]
            if block.shape[0] < bleed_px:
                extra = np.repeat(block[:1, :], bleed_px - block.shape[0], axis=0)
                block = np.concatenate([block, extra], axis=0)
            out[height - bleed_px:, :] = block[:bleed_px, :]
        else:
            src = out[bleed_px: bleed_px + span, :]
            block = src[::-1, :]
            if block.shape[0] < bleed_px:
                extra = np.repeat(block[-1:, :], bleed_px - block.shape[0], axis=0)
                block = np.concatenate([extra, block], axis=0)
            out[:bleed_px, :] = block[-bleed_px:, :]

    for name in ("top", "bottom", "left", "right"):
        art, bleed = side_views(name)
        if art.size == 0 or bleed.size == 0 or art.shape[1] < 1 or bleed.shape[1] < 1:
            methods[name] = "replicate"
            continue
        delta = edge_row_delta(art[:, :, :3], bleed[:, :, :3])
        if delta > BLEED_DELTA_E:
            paint_mirror(name)
            art, bleed = side_views(name)
            again = edge_row_delta(art[:, :, :3], bleed[:, :, :3]) if art.size and bleed.size else delta
            methods[name] = "mirror" if again <= _HARD_DELTA_E else "mirror-amber"
            methods[name + "Delta"] = round(again, 2)
        else:
            methods[name] = "replicate"
            methods[name + "Delta"] = round(delta, 2)
    return out, methods


# A 1 px white column at 72 ppi blooms to about 10 device pixels after a
# cover scale. Look that far for the real colour, still inside a 2.5 mm border.
_BLOOM_PX = SLIVER_CAP_PX + 10


def repair_light_fringe(image: np.ndarray) -> np.ndarray:
    """Replace a short light rim with the artwork just inside the bloom.

    The trim's real edge is what later bleed samples, including the 1px
    pixel-drift read. Only the outer 0.5 mm is rewritten. A pale border
    that still fills that look is not rewritten.
    """
    if image is None or getattr(image, "ndim", 0) != 3:
        return image
    if image.shape[0] < 8 or image.shape[1] < 8:
        return image
    out = np.array(image, copy=True)

    def paint(view: np.ndarray) -> None:
        width = int(view.shape[1])
        look = min(_BLOOM_PX, width - 1)
        if look < 2:
            return
        reference = view[:, look].astype(np.int16)
        window = view[:, :look].astype(np.int16)
        diff = np.max(np.abs(window - reference[:, None, :]), axis=-1)
        close = diff <= 24
        has_art = close.any(axis=1)
        first = np.argmax(close, axis=1)
        for index in range(view.shape[0]):
            if not has_art[index]:
                continue
            start = int(first[index])
            if start < 1:
                continue
            depth = min(start, SLIVER_CAP_PX)
            view[index, :depth] = view[index, look]

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
    """Robust colour of a band. One white sliver column does not move it.

    A light edge that is most of the band is the artwork. Only a few light
    pixels are dropped.
    """
    flat = np.asarray(pixels, np.float32).reshape(-1, 3)
    if flat.size < 3:
        return np.zeros(3, np.float32)
    if flat.shape[0] > 8:
        light = flat.mean(axis=1) > 186.0
        if light.any() and not light.all() and float(light.mean()) < 0.15:
            flat = flat[~light]
    return np.median(flat, axis=0)


def _std_rgb(pixels: np.ndarray) -> float:
    flat = np.asarray(pixels, np.float32).reshape(-1, 3)
    if flat.size < 3:
        return 0.0
    return float(flat.std())


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
        art_level = float(art_mean.mean())
        bleed_std = _std_rgb(bleed)
        outer_std = _std_rgb(outer)
        # A flat light band is the grey/white strip, including one that fills
        # only the outer bleed. A continuation can differ by dE 10–19 and
        # still be the artwork. A real miss sits near 40.
        light = float(bleed_mean.mean()) > 186.0 and art_level < 170.0 and bleed_std < _FLAT_STD
        outer_light = float(outer_mean.mean()) > 186.0 and art_level < 170.0 and outer_std < _FLAT_STD
        if light or (outer_light and outer_delta > BLEED_DELTA_E):
            flags.append("light-band")
        if max(delta, outer_delta) > _HARD_DELTA_E:
            flags.append("mismatch")
        if seam_delta > _HARD_DELTA_E:
            flags.append("seam")
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
            # ASCII. A Windows console raises UnicodeEncodeError on the delta glyph.
            worst.append(f"{label} dE {float(side.get('delta') or 0):.0f}" + (f" ({flags})" if flags else ""))
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
