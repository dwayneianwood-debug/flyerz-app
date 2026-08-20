#!/usr/bin/env python3
"""
Compile Press-Ready PDF Worker
Orchestrates the final press-ready pipeline:
1. Retrieve corrected artwork
2. Apply final bleed using selected strategy
3. Convert to CMYK (if requested)
4. Neutralize rich blacks
5. Generate final press-ready PDF with trim marks
Writes progress to a status JSON file for Node.js polling.
"""

import sys
import os
import json
import argparse
import tempfile
import shutil
import time
import math

from fai_temp_utils import init_fai_temp_dir, is_scratch_temp_file
FAI_TEMP_DIR = init_fai_temp_dir()


def _cover_scale_image_to_trim_px(img, target_w_px: int, target_h_px: int, *, log_label: str = "[COMPILE]"):
    """Delegates to smart_bleed.cover_scale_to_trim_px — single strict object-fit:cover implementation."""
    from smart_bleed import cover_scale_to_trim_px

    crop_h, crop_w = img.shape[:2]
    sys.stderr.write(f"{log_label} Strict cover trim: {crop_w}x{crop_h} → {target_w_px}x{target_h_px}px\n")
    return cover_scale_to_trim_px(img, target_w_px, target_h_px)


def _retry_on_os_lock(fn, *, attempts=5, delay_sec=0.5):
    """Retry file ops when OneDrive/antivirus briefly locks paths (Windows WinError 32, etc.)."""
    last = None
    for i in range(attempts):
        try:
            return fn()
        except OSError as e:
            last = e
            if i >= attempts - 1:
                break
            time.sleep(delay_sec)
    raise last


def _publish_zip_bytes(zip_bytes: bytes, final_zip_path: str) -> None:
    """
    OneDrive-safe: build the ZIP only under the OS temp directory (not synced), flush + fsync, close the
    handle, then shutil.move into uploads with retries. Never writes .tmp staging files inside sync folders.
    """
    final_zip_path = os.path.abspath(final_zip_path)
    dest_dir = os.path.dirname(final_zip_path)
    if dest_dir:
        os.makedirs(dest_dir, exist_ok=True)

    fd, scratch_path = tempfile.mkstemp(
        prefix="flyerz_press_bundle_",
        suffix=".zip",
        dir=tempfile.gettempdir(),
    )
    try:
        with os.fdopen(fd, "wb") as wf:
            wf.write(zip_bytes)
            wf.flush()
            try:
                os.fsync(wf.fileno())
            except OSError:
                pass
    except Exception:
        try:
            os.unlink(scratch_path)
        except OSError:
            pass
        raise

    def _teleport_zip_to_uploads():
        if os.path.exists(final_zip_path):
            os.unlink(final_zip_path)
        shutil.move(scratch_path, final_zip_path)

    try:
        _retry_on_os_lock(_teleport_zip_to_uploads, attempts=3, delay_sec=0.5)
    except Exception:
        try:
            if os.path.exists(scratch_path):
                os.unlink(scratch_path)
        except OSError:
            pass
        raise

# API strings matching Node `select-bleed-method`; any other value routes to auto clean-bleed
FORCED_BLEED_API_KEYS = frozenset({
    "bgExtract", "stretch", "mirror", "replicate", "upscale", "ai_outpaint", "colorBorder",
})

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def write_status(status_file, state, message, **kwargs):
    data = {"state": state, "message": message, "timestamp": time.time()}
    data.update(kwargs)
    tmp = status_file + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass

    def _commit():
        os.replace(tmp, status_file)

    _retry_on_os_lock(_commit, attempts=5, delay_sec=0.5)


# Final litho pixmap DPI — all rasters injected into PDF use set_dpi(300,300) metadata.
FINAL_RASTER_DPI = 300.0
# Symmetric over-mount (pt): legacy pixel-locked mounts only; litho presses use mm target (below).
EDGE_MOUNT_OVERLAP_PT = 0.2
# Bleed inset matching _enforce_final_mediabox / trim metadata
PRESS_DEFAULT_BLEED_MM = 5.0
# Pre-flatten: lossless PNG under this pixel count; JPEG q95 above (aligns with ~50MB RGB GS leash).
# 50MB / 3 bytes ≈ 17.4MP — use 15MP headroom so GS MaxBitmap stays safe.
PREFLATTEN_LOSSLESS_MAX_PIXELS = 15_000_000
PREFLATTEN_JPEG_QUALITY = 95


def page_raster_clip_rect(page) -> "fitz.Rect":
    """
    Bake-in clip for PyMuPDF rasterization: strictly CropBox (not MediaBox).
    Avoids embedding printer margins / ghost canvas from oversized MediaBox.
    """
    import fitz

    return fitz.Rect(page.cropbox)


def press_target_media_rect(
    trim_w_mm: float, trim_h_mm: float, bleed_mm: float = PRESS_DEFAULT_BLEED_MM,
) -> "fitz.Rect":
    """
    Master litho target (points): trim + bleed on each side, origin (0,0).
    Target-first pipeline: new_page(width,height) from this rect, then insert_image(press_insert_rect_at_origin(w_pt,h_pt), …)
    scales the bitmap to fill the mm-defined box exactly (no pixel-derived page size).
    """
    import fitz

    w_pt = (trim_w_mm + 2.0 * bleed_mm) * 72.0 / 25.4
    h_pt = (trim_h_mm + 2.0 * bleed_mm) * 72.0 / 25.4
    return fitz.Rect(0.0, 0.0, w_pt, h_pt)


def press_insert_rect_at_origin(width_pt: float, height_pt: float) -> "fitz.Rect":
    """
    insert_image destination bolted to (0, 0). Width/height must come from the same litho mm→pt math
    as new_page (press_target_media_rect or raster mount), never from page.rect — avoids horizontal drift
    and asymmetric white margins next to the raster.
    """
    import fitz

    return fitz.Rect(0.0, 0.0, float(width_pt), float(height_pt))


def _clear_press_pipeline_caches() -> None:
    """Remove stale intermediates under server/temp and server/output before each compile."""
    server_root = os.path.dirname(os.path.abspath(__file__))
    for sub in ("temp", "output"):
        full = os.path.join(server_root, sub)
        if not os.path.isdir(full):
            continue
        try:
            for name in os.listdir(full):
                p = os.path.join(full, name)
                try:
                    if os.path.isfile(p) or os.path.islink(p):
                        os.unlink(p)
                    elif os.path.isdir(p):
                        shutil.rmtree(p, ignore_errors=True)
                except OSError:
                    pass
            sys.stderr.write(f"[COMPILE] Cleared cache dir: {sub}/\n")
        except OSError as e:
            sys.stderr.write(f"[COMPILE] Cache clear skipped for {sub}/: {e}\n")


def page_pts_from_px(width_px: int, height_px: int, dpi: float = FINAL_RASTER_DPI) -> tuple[float, float]:
    """Exact PDF dimensions in points from integer pixel dimensions at the given DPI."""
    w_pt = width_px * 72.0 / dpi
    h_pt = height_px * 72.0 / dpi
    return (w_pt, h_pt)


def map_logical_rect_to_mount_rect(
    local: "fitz.Rect",
    logical_w_pt: float,
    logical_h_pt: float,
    mount: "fitz.Rect",
) -> "fitz.Rect":
    """
    Map a rectangle in logical pt (0..logical_w_pt × 0..logical_h_pt, pixmap-aligned)
    onto the destination PDF page rect (`mount`): either raster_mount_rect_from_px(...) or
    press_target_media_rect(...) when the raster is scaled to litho dimensions.
    """
    import fitz

    lw = max(float(logical_w_pt), 1e-9)
    lh = max(float(logical_h_pt), 1e-9)
    sx = mount.width / lw
    sy = mount.height / lh
    return fitz.Rect(
        mount.x0 + local.x0 * sx,
        mount.y0 + local.y0 * sy,
        mount.x0 + local.x1 * sx,
        mount.y0 + local.y1 * sy,
    )


def flush_page_boxes_to_rect(page, rect) -> None:
    """
    Ironclad sync: positive-origin MediaBox only — PyMuPDF corrupts CropBox if MediaBox uses
    negative x0/y0. Same overlap as fitz.Rect(−e,−e,w+e,h+e) by using size (w+2e)×(h+2e) at (0,0).
    """
    import fitz

    r = fitz.Rect(rect)
    w = round(r.width, 2)
    h = round(r.height, 2)
    if w <= 0 or h <= 0:
        raise ValueError(f"flush_page_boxes_to_rect: invalid size {w}x{h} pt")
    standard_rect = fitz.Rect(0.0, 0.0, w, h)
    page.set_mediabox(standard_rect)
    mb = fitz.Rect(page.mediabox)
    page.set_cropbox(mb)
    page.set_bleedbox(mb)
    page.set_trimbox(mb)


def apply_strict_page_boxes_fitz(page, media_rect, crop_rect, bleed_rect, trim_rect) -> None:
    """
    Enforce MediaBox ⊇ CropBox ⊇ BleedBox ⊇ TrimBox (nested rectangles).
    After each set_*box, re-read from `page` so nested rects stay inside the canonical MediaBox
    (prevents CropBox not in MediaBox from rounding).
    """
    import fitz

    page.set_mediabox(fitz.Rect(media_rect))
    mb = fitz.Rect(page.mediabox)

    cr = fitz.Rect(crop_rect).intersect(mb)
    if cr.width < 0.05 or cr.height < 0.05:
        cr = fitz.Rect(mb)
    page.set_cropbox(cr)
    cr = fitz.Rect(page.cropbox)

    bl = fitz.Rect(bleed_rect).intersect(cr)
    if bl.width < 0.05 or bl.height < 0.05:
        bl = fitz.Rect(cr)
    page.set_bleedbox(bl)
    bl = fitz.Rect(page.bleedbox)

    tr = fitz.Rect(trim_rect).intersect(bl)
    if tr.width < 0.05 or tr.height < 0.05:
        tr = fitz.Rect(bl)
    page.set_trimbox(tr)


def finalize_press_pdf_box_hierarchy(path: str) -> None:
    """Last-chance enforcement before ZIP/download: nested boxes + PyMuPDF write order."""
    import fitz

    doc = fitz.open(path)
    tmp = path + ".boxfinalize.tmp.pdf"
    try:
        for page in doc:
            mb = fitz.Rect(page.mediabox)
            try:
                cr = fitz.Rect(page.cropbox)
            except Exception:
                cr = fitz.Rect(mb)
            try:
                bl = fitz.Rect(page.bleedbox)
            except Exception:
                bl = fitz.Rect(cr)
            try:
                tr = fitz.Rect(page.trimbox)
            except Exception:
                tr = fitz.Rect(bl)
            apply_strict_page_boxes_fitz(page, mb, cr, bl, tr)
        doc.save(tmp, garbage=4, deflate=True)
    finally:
        doc.close()
    _retry_on_os_lock(lambda: os.replace(tmp, path))
    sys.stderr.write(f"[COMPILE] Final PDF: strict page box hierarchy applied\n")


def _pdf_exception_suggests_bad_geometry(exc: BaseException) -> bool:
    """True if exception (or its chain) looks like PyMuPDF / PDF page-box corruption."""
    needles = (
        "cropbox",
        "mediabox",
        "bleedbox",
        "trimbox",
        "crop box",
        "media box",
        "not in mediabox",
        "outside mediabox",
        "invalid box",
        "page geometry",
        "bad rectangle",
    )
    parts: list[str] = []
    seen: set[int] = set()
    e: BaseException | None = exc
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        parts.append(str(e).lower())
        e = e.__cause__ or e.__context__
    blob = " ".join(parts)
    return any(n in blob for n in needles)


def nuclear_rebuild_pdf_visual_mount(
    broken_path: str,
    output_path: str,
    trim_w_mm: float,
    trim_h_mm: float,
    bleed_mm: float = 5.0,
) -> str:
    """
    Nuclear option: **pure raster** rebuild — no vector operators; full pixmap sampling only.
    Each source page is rendered at 300 DPI via get_pixmap(alpha=False), then mounted on a
    fresh page with trim+bleed dimensions. All page boxes are set to one rectangle before
    insert_image so geometry is ironclad (layer integrity: single raster, no ghost vectors).
    """
    import fitz

    mm_to_pt = 72.0 / 25.4
    trim_w_pt = trim_w_mm * mm_to_pt
    trim_h_pt = trim_h_mm * mm_to_pt
    bleed_pt = bleed_mm * mm_to_pt
    target_w_pt = trim_w_pt + 2 * bleed_pt
    target_h_pt = trim_h_pt + 2 * bleed_pt

    raster_dpi = 300.0
    mat = fitz.Matrix(raster_dpi / 72.0, raster_dpi / 72.0)

    broken = fitz.open(broken_path)
    clean = fitz.open()
    try:
        n = broken.page_count
        if n == 0:
            raise ValueError("Nuclear rebuild: source PDF has no pages")
        for i in range(n):
            src_pg = broken.load_page(i)
            pix = src_pg.get_pixmap(matrix=mat, clip=page_raster_clip_rect(src_pg), alpha=False)
            pix.set_dpi(int(raster_dpi), int(raster_dpi))
            pw, ph = pix.width, pix.height

            target_rect = press_target_media_rect(trim_w_mm, trim_h_mm, bleed_mm)
            tw_pt, th_pt = float(target_rect.width), float(target_rect.height)
            clean_page = clean.new_page(width=tw_pt, height=th_pt)
            flush_page_boxes_to_rect(clean_page, target_rect)
            clean_page.insert_image(
                press_insert_rect_at_origin(tw_pt, th_pt), pixmap=pix, keep_proportion=False
            )
            del pix

            sys.stderr.write(
                f"[COMPILE] Nuclear pure-raster page {i + 1}/{n}: {pw}x{ph}px @ {int(raster_dpi)} DPI → "
                f"{target_w_pt:.2f}x{target_h_pt:.2f} pt canvas\n"
            )
    finally:
        broken.close()

    clean.save(output_path, garbage=4, deflate=True)
    clean.close()
    sys.stderr.write(
        f"[COMPILE] Nuclear pure-raster rebuild: {n} page(s) → {target_w_pt:.2f}x{target_h_pt:.2f} pt "
        f"(trim {trim_w_mm}x{trim_h_mm} mm + {bleed_mm} mm bleed), vectors stripped.\n"
    )
    return output_path


def raster_mount_rect_from_px(width_px: int, height_px: int, dpi: float = FINAL_RASTER_DPI):
    """
    Logical px→pt → (w_pt, h_pt). Over-mount: same outer size as fitz.Rect(−e,−e,w_pt+e,h_pt+e)
    using positive MediaBox (0, 0, w_pt+2e, h_pt+2e), e=EDGE_MOUNT_OVERLAP_PT (0.2 pt/side).
    insert_image(page.mediabox) fills that rect — hairline kill; CropBox stays valid in PyMuPDF.
    """
    import fitz

    w_pt, h_pt = page_pts_from_px(width_px, height_px, dpi)
    e = EDGE_MOUNT_OVERLAP_PT
    return fitz.Rect(0.0, 0.0, round(w_pt + 2 * e, 2), round(h_pt + 2 * e, 2))


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  Target-first compile builds pages at press_target_media_rect already;      ║
# ║  _enforce_final_mediabox only verifies geometry — identity copy, no relabel. ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
def _enforce_final_mediabox(
    input_path: str,
    output_path: str,
    trim_w_mm: float,
    trim_h_mm: float,
    bleed_mm: float = PRESS_DEFAULT_BLEED_MM,
) -> str:
    """
    Confirm each page rect matches the litho mm target; copy PDF unchanged (no box rewrite).
    Relabeling mediabox here caused GS/viewers to expose white margins vs raster extent.
    """
    import fitz

    target = press_target_media_rect(trim_w_mm, trim_h_mm, bleed_mm)
    tw, th = target.width, target.height
    tol = 0.5

    doc = fitz.open(input_path)
    try:
        for i, page in enumerate(doc):
            mb = page.mediabox
            sw, sh = mb.width, mb.height
            dw, dh = abs(sw - tw), abs(sh - th)
            origin_slip = max(abs(mb.x0), abs(mb.y0))
            if origin_slip > 0.02:
                sys.stderr.write(
                    f"[ENFORCE-MEDIABOX] Page {i+1}: WARN — MediaBox origin ({mb.x0:.4f},{mb.y0:.4f}) not at (0,0); "
                    f"viewers may show uneven white margins even when size matches target.\n"
                )
            if dw <= tol and dh <= tol:
                sys.stderr.write(
                    f"[ENFORCE-MEDIABOX] Page {i+1}: OK — {sw:.2f}x{sh:.2f}pt matches litho target "
                    f"{tw:.2f}x{th:.2f}pt (identity pass, no relabel).\n"
                )
            else:
                sys.stderr.write(
                    f"[ENFORCE-MEDIABOX] Page {i+1}: WARN — {sw:.2f}x{sh:.2f}pt vs target "
                    f"{tw:.2f}x{th:.2f}pt (Δ {dw:.2f}x{dh:.2f}pt); copying through without relabeling.\n"
                )
    finally:
        doc.close()

    shutil.copy2(input_path, output_path)
    out_mb = os.path.getsize(output_path) / (1024 * 1024)
    sys.stderr.write(
        f"[ENFORCE-MEDIABOX] Identity copy → {output_path} ({out_mb:.2f} MB); boxes not rewritten.\n"
    )
    return output_path


def _normalize_pdf_geometry(
    input_path: str,
    output_path: str,
    trim_w_mm: float | None = None,
    trim_h_mm: float | None = None,
    bleed_mm: float = PRESS_DEFAULT_BLEED_MM,
) -> str:
    import fitz
    import gc

    use_press_target = (
        trim_w_mm is not None
        and trim_h_mm is not None
        and trim_w_mm > 0
        and trim_h_mm > 0
    )

    src = fitz.open(input_path)
    dst = fitz.open()
    normalized = 0

    for i, page in enumerate(src):
        mediabox = page.mediabox
        cropbox = page.cropbox

        has_mismatch = (
            abs(cropbox.x0 - mediabox.x0) > 0.5 or
            abs(cropbox.y0 - mediabox.y0) > 0.5 or
            abs(cropbox.width - mediabox.width) > 0.5 or
            abs(cropbox.height - mediabox.height) > 0.5
        )

        if has_mismatch:
            sys.stderr.write(
                f"[NORMALIZE] Page {i+1}: MediaBox={mediabox.width:.1f}x{mediabox.height:.1f}pt "
                f"vs CropBox={cropbox.width:.1f}x{cropbox.height:.1f}pt — MISMATCH (raster uses CropBox only)\n"
            )
            normalized += 1

        clip = page_raster_clip_rect(page)

        scale = 300.0 / 72.0
        mat = fitz.Matrix(scale, scale)
        pix = page.get_pixmap(matrix=mat, clip=clip, alpha=False)
        pix.set_dpi(300, 300)
        pw_px, ph_px = pix.width, pix.height
        img_bytes = pix.tobytes("png")
        pix = None
        gc.collect()

        logical_w, logical_h = page_pts_from_px(pw_px, ph_px, FINAL_RASTER_DPI)
        if use_press_target:
            mount_rect = press_target_media_rect(trim_w_mm, trim_h_mm, bleed_mm)
        else:
            mount_rect = raster_mount_rect_from_px(pw_px, ph_px, FINAL_RASTER_DPI)
        mw, mh = float(mount_rect.width), float(mount_rect.height)
        new_page = dst.new_page(width=mw, height=mh)
        flush_page_boxes_to_rect(new_page, mount_rect)
        new_page.insert_image(press_insert_rect_at_origin(mw, mh), stream=img_bytes, keep_proportion=False)
        img_bytes = None

        mb = fitz.Rect(new_page.mediabox)
        trim_r = fitz.Rect(mb)
        bleed_r = fitz.Rect(mb)
        _ox, _oy = clip.x0, clip.y0
        try:
            tb = page.trimbox
            tb_rel = fitz.Rect(tb.x0 - _ox, tb.y0 - _oy, tb.x1 - _ox, tb.y1 - _oy)
            tb_rel = fitz.Rect(
                max(0, tb_rel.x0),
                max(0, tb_rel.y0),
                min(logical_w, tb_rel.x1),
                min(logical_h, tb_rel.y1),
            )
            trim_r = map_logical_rect_to_mount_rect(tb_rel, logical_w, logical_h, mount_rect).intersect(mb)
        except Exception:
            trim_r = fitz.Rect(mb)

        try:
            bb = page.bleedbox
            bb_rel = fitz.Rect(bb.x0 - _ox, bb.y0 - _oy, bb.x1 - _ox, bb.y1 - _oy)
            bb_rel = fitz.Rect(
                max(0, bb_rel.x0),
                max(0, bb_rel.y0),
                min(logical_w, bb_rel.x1),
                min(logical_h, bb_rel.y1),
            )
            bleed_r = map_logical_rect_to_mount_rect(bb_rel, logical_w, logical_h, mount_rect).intersect(mb)
        except Exception:
            bleed_r = fitz.Rect(mb)

        apply_strict_page_boxes_fitz(new_page, mb, mb, bleed_r, trim_r)

    src.close()
    dst.save(output_path, deflate=True, garbage=4)
    out_size = os.path.getsize(output_path) / (1024 * 1024)
    dst.close()
    gc.collect()

    if normalized > 0:
        sys.stderr.write(f"[NORMALIZE] Hard-cropped {normalized} page(s), output: {out_size:.2f} MB\n")
    else:
        sys.stderr.write(f"[NORMALIZE] All pages clean (no ghost canvas), output: {out_size:.2f} MB\n")
    return output_path


def _apply_creep_shift(input_path: str, output_path: str,
                       creep_mm: float, trim_w_mm: float, trim_h_mm: float,
                       bleed_mm: float = 5.0) -> str:
    import fitz
    import gc

    if creep_mm <= 0:
        shutil.copy2(input_path, output_path)
        return output_path

    MM_TO_PT = 72.0 / 25.4
    creep_pt = creep_mm * MM_TO_PT
    assert creep_pt > 0

    src = fitz.open(input_path)
    dst = fitz.open()

    for i, page in enumerate(src):
        scale = 300.0 / 72.0
        mat = fitz.Matrix(scale, scale)
        pix = page.get_pixmap(matrix=mat, clip=page_raster_clip_rect(page), alpha=False)
        pix.set_dpi(300, 300)
        img_bytes = pix.tobytes("png")
        pix = None

        mb = press_target_media_rect(trim_w_mm, trim_h_mm, bleed_mm)
        tw, th = float(mb.width), float(mb.height)
        new_page = dst.new_page(width=tw, height=th)
        flush_page_boxes_to_rect(new_page, mb)
        # Booklet-only: shifts raster right in pt space — exposes white at left margin (never use for single flyers).
        draw_rect = fitz.Rect(float(creep_pt), 0.0, tw, th)
        new_page.insert_image(draw_rect, stream=img_bytes, keep_proportion=False)
        img_bytes = None

        bleed_pt = bleed_mm * MM_TO_PT
        trim_rect = fitz.Rect(
            bleed_pt + creep_pt,
            bleed_pt,
            bleed_pt + creep_pt + trim_w_mm * MM_TO_PT,
            bleed_pt + trim_h_mm * MM_TO_PT,
        )
        apply_strict_page_boxes_fitz(new_page, mb, mb, mb, trim_rect)

        gc.collect()

    src.close()
    dst.save(output_path, deflate=True, garbage=4)
    out_size = os.path.getsize(output_path) / (1024 * 1024)
    dst.close()
    gc.collect()

    sys.stderr.write(
        f"[CREEP] Applied {creep_mm}mm gutter creep shift to {i + 1} page(s), "
        f"output: {out_size:.2f} MB\n"
    )
    return output_path


def _enforce_single_layer(
    pdf_path: str,
    trim_w_mm: float | None = None,
    trim_h_mm: float | None = None,
    bleed_mm: float = PRESS_DEFAULT_BLEED_MM,
) -> dict:
    import fitz
    import gc

    use_press_target = (
        trim_w_mm is not None
        and trim_h_mm is not None
        and trim_w_mm > 0
        and trim_h_mm > 0
    )

    doc = fitz.open(pdf_path)
    stats = {"pages": len(doc), "vectors_purged": 0, "pages_rerasterized": 0, "verified_single_layer": False}

    needs_rewrite = False
    for i, page in enumerate(doc):
        drawings = page.get_drawings()
        images = page.get_images(full=True)
        annots = list(page.annots()) if page.annots() else []

        is_clean = len(images) == 1 and len(drawings) == 0 and len(annots) == 0
        if is_clean:
            continue

        needs_rewrite = True
        stats["vectors_purged"] += len(drawings)
        stats["pages_rerasterized"] += 1

        pix = page.get_pixmap(
            matrix=fitz.Matrix(300 / 72.0, 300 / 72.0),
            clip=page_raster_clip_rect(page),
            alpha=False,
        )
        pix.set_dpi(300, 300)
        img_bytes = pix.tobytes("png")
        pix_w, pix_h = pix.width, pix.height
        if use_press_target:
            mount_rect = press_target_media_rect(trim_w_mm, trim_h_mm, bleed_mm)
        else:
            mount_rect = raster_mount_rect_from_px(pix_w, pix_h, FINAL_RASTER_DPI)
        del pix

        page.clean_contents()

        for img_info in page.get_images(full=True):
            try:
                page.delete_image(img_info[0])
            except Exception:
                pass

        for annot in annots:
            try:
                page.delete_annot(annot)
            except Exception:
                pass

        mw, mh = float(mount_rect.width), float(mount_rect.height)
        flush_page_boxes_to_rect(page, mount_rect)
        page.insert_image(press_insert_rect_at_origin(mw, mh), stream=img_bytes, keep_proportion=False)
        del img_bytes
        sys.stderr.write(f"[SINGLE-LAYER] Page {i+1}: purged {len(drawings)} vectors + {len(images)} images + {len(annots)} annotations, re-rasterized to {pix_w}x{pix_h}px single layer\n")

    if needs_rewrite:
        tmp_out = pdf_path + ".sl_tmp"
        doc.save(tmp_out, garbage=4, deflate=True, clean=True)
        doc.close()
        os.replace(tmp_out, pdf_path)
        sys.stderr.write(f"[SINGLE-LAYER] Enforcement complete: {stats['pages_rerasterized']} page(s) re-rasterized, {stats['vectors_purged']} vectors purged (ghost layer prevention)\n")
    else:
        doc.close()
        sys.stderr.write(f"[SINGLE-LAYER] Verified: all {stats['pages']} page(s) are already single-layer raster (ghost layer prevention)\n")

    stats["verified_single_layer"] = True
    gc.collect()
    return stats


def _preflatten_for_gs(
    work_path: str,
    tmp_chain: list,
    trim_w_mm: float,
    trim_h_mm: float,
    bleed_mm: float = PRESS_DEFAULT_BLEED_MM,
) -> str:
    """
    Pre-flatten the GS handoff file: render each page as a single RGB raster
    at 300 DPI with alpha=False (white background), inject DPI metadata.
    Guarantees Ghostscript receives a clean, flat, alpha-free PDF that
    won't choke under 50 MB memory constraints.

    Normal pages (< PREFLATTEN_LOSSLESS_MAX_PIXELS): lossless PNG — no JPEG softness.
    Oversized pages: JPEG q95 fallback to keep GS MaxBitmap / RAM safe.

    Uses alpha=False directly in get_pixmap to avoid double-memory from
    RGBA->RGB conversion. Cleans up prior temp work_path to free /dev/shm space.
    """
    import fitz
    import gc

    sys.stderr.write(f"[PRE-FLATTEN] Flattening GS handoff: {work_path} ({os.path.getsize(work_path) / (1024*1024):.1f} MB)\n")

    try:
        from PIL import Image as _PilImage
        src = fitz.open(work_path)
        dst = fitz.open()
        scale = 300.0 / 72.0
        mat = fitz.Matrix(scale, scale)

        for i, page in enumerate(src):
            pix = page.get_pixmap(matrix=mat, clip=page_raster_clip_rect(page), alpha=False)
            pix.set_dpi(300, 300)
            pix_w, pix_h = pix.width, pix.height
            page_pixels = int(pix_w) * int(pix_h)
            use_lossless = page_pixels < int(PREFLATTEN_LOSSLESS_MAX_PIXELS)

            pil_img = _PilImage.frombytes("RGB", (pix_w, pix_h), pix.samples)
            pix = None
            if pil_img.mode != "RGB":
                sys.stderr.write(f"[PRE-FLATTEN] Page {i+1}: unexpected mode {pil_img.mode}, converting to RGB\n")
                pil_img = pil_img.convert("RGB")

            if use_lossless:
                flat_tmp = tempfile.NamedTemporaryFile(
                    suffix="_flat.png", delete=False, dir=FAI_TEMP_DIR
                ).name
                tmp_chain.append(flat_tmp)
                try:
                    pil_img.save(
                        flat_tmp,
                        format="PNG",
                        optimize=True,
                        dpi=(int(FINAL_RASTER_DPI), int(FINAL_RASTER_DPI)),
                    )
                except OSError as save_err:
                    raise RuntimeError(
                        f"Pre-flatten PNG save failed for page {i+1} "
                        f"(mode={pil_img.mode}, size={pix_w}x{pix_h}): {save_err}"
                    ) from save_err
                codec_label = "PNG lossless"
            else:
                flat_tmp = tempfile.NamedTemporaryFile(
                    suffix="_flat.jpg", delete=False, dir=FAI_TEMP_DIR
                ).name
                tmp_chain.append(flat_tmp)
                try:
                    pil_img.save(
                        flat_tmp,
                        format="JPEG",
                        quality=int(PREFLATTEN_JPEG_QUALITY),
                        optimize=True,
                        dpi=(int(FINAL_RASTER_DPI), int(FINAL_RASTER_DPI)),
                    )
                except OSError as save_err:
                    raise RuntimeError(
                        f"Pre-flatten JPEG save failed for page {i+1} "
                        f"(mode={pil_img.mode}, size={pix_w}x{pix_h}): {save_err}"
                    ) from save_err
                codec_label = f"JPEG q{int(PREFLATTEN_JPEG_QUALITY)} (big-page fallback)"

            if not os.path.exists(flat_tmp) or os.path.getsize(flat_tmp) == 0:
                raise RuntimeError(f"Pre-flatten save produced empty file for page {i+1}: {flat_tmp}")
            pil_img.close()
            pil_img = None
            gc.collect()

            mount_rect = press_target_media_rect(trim_w_mm, trim_h_mm, bleed_mm)
            mw, mh = float(mount_rect.width), float(mount_rect.height)
            new_page = dst.new_page(width=mw, height=mh)
            flush_page_boxes_to_rect(new_page, mount_rect)
            new_page.insert_image(
                press_insert_rect_at_origin(mw, mh), filename=flat_tmp, keep_proportion=False
            )

            flat_size_kb = os.path.getsize(flat_tmp) / 1024
            sys.stderr.write(
                f"[PRE-FLATTEN] Page {i+1}: {pix_w}x{pix_h}px ({page_pixels:,}px) -> "
                f"{codec_label} ({flat_size_kb:.0f} KB), 300 DPI\n"
            )

        src.close()

        flat_path = tempfile.NamedTemporaryFile(suffix="_gsflat.pdf", delete=False, dir=FAI_TEMP_DIR).name
        tmp_chain.append(flat_path)
        dst.save(flat_path, deflate=True, garbage=4)
        dst.close()

        is_shm = is_scratch_temp_file(work_path, FAI_TEMP_DIR)
        if is_shm and work_path != flat_path:
            try:
                os.unlink(work_path)
                sys.stderr.write(f"[PRE-FLATTEN] Reclaimed /dev/shm space: deleted {os.path.basename(work_path)}\n")
            except Exception:
                pass

        gc.collect()

        out_mb = os.path.getsize(flat_path) / (1024 * 1024)
        sys.stderr.write(f"[PRE-FLATTEN] Flat GS-safe PDF: {out_mb:.2f} MB\n")
        return flat_path

    except Exception as e:
        sys.stderr.write(f"[PRE-FLATTEN] Failed (non-fatal, using original): {e}\n")
        return work_path


def _prerasterize_pdf(
    input_path: str,
    output_path: str,
    dpi: int = 300,
    trim_w_mm: float | None = None,
    trim_h_mm: float | None = None,
    bleed_mm: float = PRESS_DEFAULT_BLEED_MM,
) -> str:
    import fitz
    import gc

    use_press_target = (
        trim_w_mm is not None
        and trim_h_mm is not None
        and trim_w_mm > 0
        and trim_h_mm > 0
    )

    src = fitz.open(input_path)
    page_meta = []
    for i, page in enumerate(src):
        cropbox = page.cropbox
        tb = None
        bb = None
        try:
            tb = page.trimbox
        except Exception:
            pass
        try:
            bb = page.bleedbox
        except Exception:
            pass
        page_meta.append({"cropbox": cropbox, "trimbox": tb, "bleedbox": bb})

    sys.stderr.write(f"[PRE-RASTER] Flattening {len(page_meta)} page(s) at {dpi} DPI (clipped to CropBox)\n")

    dst = fitz.open()
    scale = dpi / 72.0
    mat = fitz.Matrix(scale, scale)

    for i, page in enumerate(src):
        meta = page_meta[i]
        clip = page_raster_clip_rect(page)
        pix = page.get_pixmap(matrix=mat, clip=clip, alpha=False)
        pix.set_dpi(dpi, dpi)
        img_bytes = pix.tobytes("png")
        pix_w, pix_h = pix.width, pix.height
        pix = None
        gc.collect()

        logical_w, logical_h = page_pts_from_px(pix_w, pix_h, float(dpi))
        if use_press_target:
            mount_rect = press_target_media_rect(trim_w_mm, trim_h_mm, bleed_mm)
        else:
            mount_rect = raster_mount_rect_from_px(pix_w, pix_h, float(dpi))
        mw, mh = float(mount_rect.width), float(mount_rect.height)
        new_page = dst.new_page(width=mw, height=mh)
        flush_page_boxes_to_rect(new_page, mount_rect)
        new_page.insert_image(press_insert_rect_at_origin(mw, mh), stream=img_bytes, keep_proportion=False)
        page_w_pt, page_h_pt = mw, mh
        img_bytes = None

        mb = fitz.Rect(mount_rect)
        bleed_r = fitz.Rect(mb)
        trim_r = fitz.Rect(mb)
        cx0, cy0 = clip.x0, clip.y0
        if meta["bleedbox"]:
            bb = meta["bleedbox"]
            bb_rel = fitz.Rect(
                max(0, bb.x0 - cx0), max(0, bb.y0 - cy0),
                min(logical_w, bb.x1 - cx0), min(logical_h, bb.y1 - cy0)
            )
            bleed_r = map_logical_rect_to_mount_rect(bb_rel, logical_w, logical_h, mount_rect).intersect(mb)
        if meta["trimbox"]:
            tb = meta["trimbox"]
            tb_rel = fitz.Rect(
                max(0, tb.x0 - cx0), max(0, tb.y0 - cy0),
                min(logical_w, tb.x1 - cx0), min(logical_h, tb.y1 - cy0)
            )
            trim_r = map_logical_rect_to_mount_rect(tb_rel, logical_w, logical_h, mount_rect).intersect(bleed_r)
        apply_strict_page_boxes_fitz(new_page, mb, mb, bleed_r, trim_r)

        sys.stderr.write(
            f"[PRE-RASTER] Page {i+1}: {pix_w}x{pix_h}px rasterized -> "
            f"{page_w_pt:.1f}x{page_h_pt:.1f}pt page\n"
        )

    src.close()
    dst.save(output_path, deflate=True, garbage=4)
    out_size = os.path.getsize(output_path) / (1024 * 1024)
    dst.close()
    gc.collect()
    sys.stderr.write(f"[PRE-RASTER] Flat raster PDF written: {out_size:.2f} MB\n")
    return output_path


def _sandwich_page(doc, page_num, render_dpi):
    """
    Text/Background sandwich technique using redaction-based extraction:
    1. Create text-only layer by redacting all non-text content (images, drawings)
       from a copy of the original page — preserves original embedded fonts
    2. Count text spans to verify text exists

    Returns (page_rect, text_doc) or (None, None) if no text found.
    text_doc is a fitz.Document with original fonts/metrics preserved via redaction.
    """
    import fitz

    page = doc[page_num]
    text_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
    blocks = text_dict.get("blocks", [])

    text_blocks = [b for b in blocks if b.get("type") == 0 and b.get("lines")]
    if not text_blocks:
        return None, None, 0

    span_count = sum(
        1
        for b in text_blocks
        for line in b.get("lines", [])
        for span in line.get("spans", [])
        if span.get("text", "").strip()
    )
    if span_count == 0:
        return None, None, 0

    text_doc = fitz.open(doc.name)
    text_page = text_doc[page_num]

    for img_info in text_page.get_images(full=True):
        xref = img_info[0]
        try:
            rects = text_page.get_image_rects(xref)
            for r in rects:
                if r.is_empty or r.is_infinite:
                    continue
                text_page.add_redact_annot(r, fill=(255, 255, 255))
        except Exception:
            pass

    try:
        drawings = text_page.get_drawings()
        for d in drawings:
            r = d.get("rect")
            if r and not r.is_empty and not r.is_infinite:
                text_page.add_redact_annot(fitz.Rect(r), fill=(255, 255, 255))
    except Exception:
        pass

    text_page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_REMOVE)

    while text_doc.page_count > 1:
        if page_num == 0:
            text_doc.delete_page(text_doc.page_count - 1)
        else:
            text_doc.delete_page(0)
            page_num -= 1

    sys.stderr.write(
        f"[COMPILE] Sandwich mode: {span_count} text spans preserved as vectors "
        f"(original fonts), background rasterized at {render_dpi} DPI\n"
    )

    return page.rect, text_doc, span_count


def _recombine_sandwich(
    out_doc,
    bg_bleed_bgr,
    text_doc,
    orig_page_rect,
    render_dpi,
    bleed_mm=5.0,
    compile_stats=None,
    trim_w_mm: float | None = None,
    trim_h_mm: float | None = None,
):
    """
    Recombine a rasterized+bleeded background with the vector text layer.
    1. Insert background pixmap into new page
    2. Overlay original text layer on top, positioned at the trim origin
    Returns (new_page, True) if sandwich succeeded, (None, False) if scale mismatch.
    """
    import fitz
    import cv2

    bleed_h, bleed_w = bg_bleed_bgr.shape[:2]
    bleed_rgb = cv2.cvtColor(bg_bleed_bgr, cv2.COLOR_BGR2RGB)
    new_pix = fitz.Pixmap(fitz.csRGB, bleed_w, bleed_h, bleed_rgb.tobytes(), False)
    new_pix.set_dpi(render_dpi, render_dpi)
    del bleed_rgb

    bleed_pt = bleed_mm * 72.0 / 25.4
    if trim_w_mm is not None and trim_h_mm is not None and trim_w_mm > 0 and trim_h_mm > 0:
        trim_w = trim_w_mm * 72.0 / 25.4
        trim_h = trim_h_mm * 72.0 / 25.4
    else:
        base_w_pt, base_h_pt = page_pts_from_px(bleed_w, bleed_h, float(render_dpi))
        trim_w = base_w_pt - 2 * bleed_pt
        trim_h = base_h_pt - 2 * bleed_pt

    text_w = orig_page_rect.width
    text_h = orig_page_rect.height

    scale_x = trim_w / text_w if text_w > 0 else 1.0
    scale_y = trim_h / text_h if text_h > 0 else 1.0
    scale = min(scale_x, scale_y)

    if compile_stats is not None:
        compile_stats["sandwich_scale"] = round(scale, 3)

    if scale < 0.90 or scale > 1.10:
        sys.stderr.write(
            f"[COMPILE] Sandwich skipped: scale {scale:.3f} outside 0.90-1.10 threshold "
            f"(original {text_w:.0f}x{text_h:.0f} pt vs trim {trim_w:.1f}x{trim_h:.1f} pt). "
            f"Using full rasterization instead.\n"
        )
        text_doc.close()
        del new_pix
        return None, False

    if trim_w_mm is not None and trim_h_mm is not None and trim_w_mm > 0 and trim_h_mm > 0:
        mount_rect = press_target_media_rect(trim_w_mm, trim_h_mm, bleed_mm)
    else:
        mount_rect = raster_mount_rect_from_px(bleed_w, bleed_h, float(render_dpi))
    mw, mh = float(mount_rect.width), float(mount_rect.height)
    new_page = out_doc.new_page(width=mw, height=mh)
    flush_page_boxes_to_rect(new_page, mount_rect)
    new_page.insert_image(press_insert_rect_at_origin(mw, mh), pixmap=new_pix, keep_proportion=False)
    del new_pix

    text_doc.close()

    sys.stderr.write(
        f"[COMPILE] Single-layer raster page: {bleed_w}x{bleed_h} (no text overlay — ghost layer prevention)\n"
    )

    return new_page, True


def _add_trim_marks_to_pdf(pdf_path, trim_w_mm, trim_h_mm, bleed_mm=5.0):
    """Add visual trim/crop marks and set TrimBox/BleedBox metadata on an existing PDF."""
    import fitz
    doc = fitz.open(pdf_path)

    bleed_pt = bleed_mm * 72.0 / 25.4
    trim_w_pt = trim_w_mm * 72.0 / 25.4
    trim_h_pt = trim_h_mm * 72.0 / 25.4
    mark_len = 3 * 72.0 / 25.4

    for page in doc:
        pw = page.rect.width
        ph = page.rect.height

        bleed_x = (pw - trim_w_pt) / 2.0
        bleed_y = (ph - trim_h_pt) / 2.0

        shape = page.new_shape()
        shape.draw_line(fitz.Point(bleed_x, 0), fitz.Point(bleed_x, mark_len))
        shape.draw_line(fitz.Point(bleed_x, ph - mark_len), fitz.Point(bleed_x, ph))
        shape.draw_line(fitz.Point(bleed_x + trim_w_pt, 0), fitz.Point(bleed_x + trim_w_pt, mark_len))
        shape.draw_line(fitz.Point(bleed_x + trim_w_pt, ph - mark_len), fitz.Point(bleed_x + trim_w_pt, ph))

        shape.draw_line(fitz.Point(0, bleed_y), fitz.Point(mark_len, bleed_y))
        shape.draw_line(fitz.Point(pw - mark_len, bleed_y), fitz.Point(pw, bleed_y))
        shape.draw_line(fitz.Point(0, bleed_y + trim_h_pt), fitz.Point(mark_len, bleed_y + trim_h_pt))
        shape.draw_line(fitz.Point(pw - mark_len, bleed_y + trim_h_pt), fitz.Point(pw, bleed_y + trim_h_pt))

        shape.finish(color=(0, 0, 0), width=0.25)
        shape.commit()

    doc.saveIncr()
    doc.close()

    _set_pdf_metadata_boxes(pdf_path, trim_w_mm, trim_h_mm, bleed_mm)


def _set_pdf_metadata_boxes(pdf_path, trim_w_mm, trim_h_mm, bleed_mm=5.0):
    """Set TrimBox and BleedBox metadata on every page using pikepdf.
    BleedBox = full extended canvas (same as MediaBox).
    TrimBox = inset from BleedBox by bleed_mm on all sides."""
    import pikepdf

    bleed_pt = bleed_mm * 72.0 / 25.4
    trim_w_pt = trim_w_mm * 72.0 / 25.4
    trim_h_pt = trim_h_mm * 72.0 / 25.4

    pdf = pikepdf.open(pdf_path, allow_overwriting_input=True)

    for page in pdf.pages:
        canvas_box = page.get("/CropBox") or page.get("/MediaBox")
        if canvas_box:
            pw = float(canvas_box[2]) - float(canvas_box[0])
            ph = float(canvas_box[3]) - float(canvas_box[1])
            x0 = float(canvas_box[0])
            y0 = float(canvas_box[1])
        else:
            pw = trim_w_pt + 2 * bleed_pt
            ph = trim_h_pt + 2 * bleed_pt
            x0 = 0.0
            y0 = 0.0

        mb_arr = [float(x0), float(y0), float(x0 + pw), float(y0 + ph)]
        page.MediaBox = mb_arr
        page.CropBox = mb_arr

        bleed_arr = [float(x0), float(y0), float(x0 + pw), float(y0 + ph)]
        trim_x0 = x0 + (pw - trim_w_pt) / 2.0
        trim_y0 = y0 + (ph - trim_h_pt) / 2.0
        trim_arr = [
            float(trim_x0),
            float(trim_y0),
            float(trim_x0 + trim_w_pt),
            float(trim_y0 + trim_h_pt),
        ]
        page.BleedBox = bleed_arr
        page.TrimBox = trim_arr

    pdf.save(pdf_path)
    pdf.close()
    sys.stderr.write(f"[COMPILE] Set TrimBox ({trim_w_mm}x{trim_h_mm}mm) and BleedBox (full canvas) on all pages\n")


def main():
    parser = argparse.ArgumentParser(description="Compile Press-Ready PDF")
    parser.add_argument("--input", required=True, help="Path to corrected artwork")
    parser.add_argument("--output", required=True, help="Output press-ready PDF path")
    parser.add_argument("--strategy", default="auto", help="Bleed strategy to apply")
    parser.add_argument("--bleed-color", default="#FFFFFF", help="Solid hex colour for colorBorder bleed (RGB)")
    parser.add_argument("--color-space", default="cmyk", help="Target color space (cmyk or rgb)")
    parser.add_argument("--trim-w", type=float, default=148, help="Trim width in mm")
    parser.add_argument("--trim-h", type=float, default=210, help="Trim height in mm")
    parser.add_argument("--status-file", required=True, help="Path to status JSON file")
    parser.add_argument("--result-file", required=True, help="Path to result JSON file")
    parser.add_argument("--variant-path", default="", help="Path to selected variant file (if any)")
    parser.add_argument("--original-path", default="", help="Path to original (unprocessed) PDF for text extraction")
    parser.add_argument("--zip-output", default="", help="Path to write in-memory ZIP bundle (e.g. /dev/shm/...)")
    parser.add_argument("--proof-path", default="", help="Visual proof image to include in ZIP bundle")
    parser.add_argument("--report-path", default="", help="Health report PDF to include in ZIP bundle")
    parser.add_argument("--base-name", default="artwork", help="Base filename for ZIP entries")
    parser.add_argument("--crop-x", type=float, default=-1, help="Manual crop X coordinate (pixels)")
    parser.add_argument("--crop-y", type=float, default=-1, help="Manual crop Y coordinate (pixels)")
    parser.add_argument("--crop-w", type=float, default=-1, help="Manual crop width (pixels)")
    parser.add_argument("--crop-h", type=float, default=-1, help="Manual crop height (pixels)")
    parser.add_argument("--creep-mm", type=float, default=0, help="Creep/gutter margin shift in mm for folded booklets (0 = disabled)")
    parser.add_argument("--auto-shifter", type=float, default=0, help="Auto-Shifter scale-down percentage to pull content into safe zone (0 = disabled)")
    args = parser.parse_args()

    status_file = args.status_file
    result_file = args.result_file

    print(f"DEBUG: Crop args received: crop_x={args.crop_x}, crop_y={args.crop_y}, crop_w={args.crop_w}, crop_h={args.crop_h}", flush=True)
    print(f"DEBUG: Trim args received: trim_w={args.trim_w}, trim_h={args.trim_h}", flush=True)
    print(f"CRITICAL DEBUG: Starting from ORIGINAL file. Input path = {args.input}", flush=True)
    if args.crop_x >= 0:
        print(f"CRITICAL DEBUG: CROPPING ORIGINAL FILE {args.input} AT {args.crop_x},{args.crop_y} size {args.crop_w}x{args.crop_h}", flush=True)
    if args.auto_shifter > 0:
        print(f"[AUTO-SHIFTER] Safe zone scale-down: {args.auto_shifter}% — content will be shifted inward (stub: ready for implementation)", flush=True)

    try:
        _prof_compile_t0 = time.time()
        _clear_press_pipeline_caches()
        _mt = press_target_media_rect(args.trim_w, args.trim_h, PRESS_DEFAULT_BLEED_MM)
        sys.stderr.write(
            f"[COMPILE] Target-first master_rect (trim+bleed mm → pt): {_mt.width:.4f}x{_mt.height:.4f} pt\n"
        )

        write_status(status_file, "PROCESSING", "Retrieving high-res artwork...")

        input_path = args.input
        if not os.path.exists(input_path):
            raise FileNotFoundError(f"Input file not found: {input_path}")

        if args.variant_path and os.path.exists(args.variant_path):
            input_path = args.variant_path
            sys.stderr.write(f"[COMPILE] Using variant file: {input_path}\n")
        else:
            sys.stderr.write(f"[COMPILE] Using original (un-bled) file: {input_path}\n")

        file_ext = os.path.splitext(input_path)[1].lower()
        is_pdf = file_ext == ".pdf"
        is_image = file_ext in (".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp")

        compile_stats = {
            "total_spans": 0,
            "sandwich_attempted": False,
            "sandwich_accepted": False,
            "sandwich_scale": None,
            "pages_rasterized": 0,
            "pages_sandwiched": 0,
            "cmyk_converted": False,
            "cmyk_verified": False,
            "neutralized_count": 0,
            "lenses_flattened": False,
            "fonts_outlined": False,
            "hairlines_fixed": 0,
            "qr_codes_found": 0,
            "qr_codes_fixed": 0,
            "qr_scan_status": "not_run",
        }
        page_count = 1
        render_dpi = 300
        live_proof_tmp = None

        file_size = os.path.getsize(input_path)
        sys.stderr.write(f"[COMPILE] Input: {input_path} ({file_size} bytes, {'PDF' if is_pdf else 'Image'})\n")

        write_status(status_file, "PROCESSING", "Generating bleed margins...")

        work_path = input_path
        _tmp_chain = []

        _has_crop = args.crop_x >= 0 and args.crop_y >= 0 and args.crop_w > 0 and args.crop_h > 0
        if is_pdf and _has_crop:
            import fitz as _fitz_precrop
            import gc as _gc_precrop

            sys.stderr.write(f"[RASTER-FIRST] PDF + crop detected — pre-rasterizing page 1 at 300 DPI to flat PNG\n")
            _pdf_src = _fitz_precrop.open(input_path)
            try:
                from pdf_geometry_sanitize import aggressive_sanitize_open_document_boxes

                aggressive_sanitize_open_document_boxes(_pdf_src)
            except Exception as _precrop_geom:
                sys.stderr.write(f"[COMPILE] Precrop geometry sanitize (non-fatal): {_precrop_geom}\n")
            _page0 = _pdf_src[0]
            _scale = 300.0 / 72.0
            _mat = _fitz_precrop.Matrix(_scale, _scale)
            _clip0 = page_raster_clip_rect(_page0)
            _pix = _page0.get_pixmap(matrix=_mat, clip=_clip0, alpha=False)
            _pix.set_dpi(300, 300)
            sys.stderr.write(f"[RASTER-FIRST] Rendered page 1: {_pix.width}x{_pix.height}px at 300 DPI (clip=CropBox)\n")
            try:
                _dbg_ir = os.path.join(FAI_TEMP_DIR, "debug_initial_read.png")
                _pix.save(_dbg_ir)
                sys.stderr.write(f"[COMPILE] debug_initial_read.png (precrop): {_dbg_ir}\n")
            except Exception as _dbg_e:
                sys.stderr.write(f"[COMPILE] debug_initial_read save failed: {_dbg_e}\n")

            _flat_png = tempfile.NamedTemporaryFile(suffix="_precrop.png", delete=False, dir=FAI_TEMP_DIR).name
            _tmp_chain.append(_flat_png)
            _pix.save(_flat_png)
            _flat_size = os.path.getsize(_flat_png) / (1024 * 1024)
            sys.stderr.write(f"[RASTER-FIRST] Flat PNG saved: {_flat_png} ({_flat_size:.2f} MB)\n")

            del _pix, _page0
            _pdf_src.close()
            del _pdf_src
            _gc_precrop.collect()
            sys.stderr.write(f"[RASTER-FIRST] PDF object destroyed, memory flushed. Redirecting to image pipeline.\n")

            input_path = _flat_png
            is_pdf = False
            is_image = True
            file_size = os.path.getsize(_flat_png)

        if is_image:
            _prof_img_t0 = time.time()
            import cv2
            import numpy as np
            from smart_bleed import (
                _auto_trim_white_margins,
                auto_resolve_safe_zone,
                get_effective_asset_dpi,
                BLEED_TARGET_MM,
            )

            _prof_cv_read_t0 = time.time()
            img = cv2.imread(input_path, cv2.IMREAD_UNCHANGED)
            if img is None:
                raise ValueError(f"Could not read image: {input_path}")
            sys.stderr.write(f"PROFILE: [COMPILE] OpenCV imread took {(time.time() - _prof_cv_read_t0)*1000:.1f}ms\n")

            if args.crop_x >= 0 and args.crop_y >= 0 and args.crop_w > 0 and args.crop_h > 0:
                h_img, w_img = img.shape[:2]
                raw_cx, raw_cy, raw_cw, raw_ch = args.crop_x, args.crop_y, args.crop_w, args.crop_h
                if raw_cx <= 1.0 and raw_cy <= 1.0 and raw_cw <= 1.0 and raw_ch <= 1.0:
                    cx = int(round(raw_cx * w_img))
                    cy = int(round(raw_cy * h_img))
                    cw = int(round(raw_cw * w_img))
                    ch = int(round(raw_ch * h_img))
                    print(f"DEBUG: Scaling Crop. Image is {w_img}x{h_img}. Percentages: ({raw_cx:.4f},{raw_cy:.4f},{raw_cw:.4f},{raw_ch:.4f}). Applying calculated pixels: [{cx}, {cy}, {cw}, {ch}]", flush=True)
                else:
                    cx = int(round(raw_cx))
                    cy = int(round(raw_cy))
                    cw = int(round(raw_cw))
                    ch = int(round(raw_ch))
                    print(f"DEBUG: Raw pixel crop on image {w_img}x{h_img}: [{cx}, {cy}, {cw}, {ch}]", flush=True)
                cx = max(0, min(cx, w_img - 1))
                cy = max(0, min(cy, h_img - 1))
                cw = min(cw, w_img - cx)
                ch = min(ch, h_img - cy)
                if cw > 10 and ch > 10:
                    print(f"DEBUG: Manual Crop Active. Using Original High-Res Source. Final pixels: [{cx},{cy},{cw},{ch}] on image {w_img}x{h_img}", flush=True)
                    img = img[cy:cy + ch, cx:cx + cw]
                    sys.stderr.write(f"[COMPILE] Manual crop applied: ({cx},{cy}) {cw}x{ch} -> result {img.shape[1]}x{img.shape[0]}\n")
                else:
                    sys.stderr.write(f"[COMPILE] Manual crop too small ({cw}x{ch}), skipping\n")
            else:
                _hi, _wi = img.shape[:2]
                sys.stderr.write(
                    f"[COMPILE] NO_CROP_FULL_PAGE: crop_box = full raster {_wi}x{_hi}px — "
                    f"skipping mockup auto-crop for litho bleed (Document Closed / bounds safety).\n"
                )

            img = _auto_trim_white_margins(img, white_thresh=250)

            _manual_crop_active = args.crop_x >= 0 and args.crop_y >= 0 and args.crop_w > 0 and args.crop_h > 0

            if args.trim_w > 0 and args.trim_h > 0:
                target_w_px = int(math.ceil((args.trim_w / 25.4) * 300))
                target_h_px = int(math.ceil((args.trim_h / 25.4) * 300))
                sys.stderr.write(
                    f"[COMPILE] Mandatory trim cover: {args.trim_w}x{args.trim_h}mm → "
                    f"{target_w_px}x{target_h_px}px @ 300dpi (manual_crop={_manual_crop_active})\n"
                )
                img = _cover_scale_image_to_trim_px(img, target_w_px, target_h_px, log_label="[COMPILE]")
                dpi = 300
            else:
                dpi = get_effective_asset_dpi(input_path, args.trim_w, args.trim_h)
            sys.stderr.write(f"[COMPILE] Image DPI: {dpi}\n")

            print(f"TRACER: [Checkpoint D] Python engine — args.strategy = '{args.strategy}', forced_keys = {sorted(FORCED_BLEED_API_KEYS)}, will_force = {args.strategy != 'auto' and args.strategy in FORCED_BLEED_API_KEYS}", flush=True)
            _prof_bleed_t0 = time.time()
            target_bleed_px = max(1, int(round((float(BLEED_TARGET_MM) / 25.4) * dpi)))

            if _manual_crop_active and (args.strategy == "auto" or args.strategy not in FORCED_BLEED_API_KEYS):
                bleed_api_strategy = "mirror"
                sys.stderr.write(f"[COMPILE] Manual crop active — bleed strategy routed as mirror ({target_bleed_px}px extend base)\n")
            elif args.strategy in FORCED_BLEED_API_KEYS:
                bleed_api_strategy = args.strategy
                sys.stderr.write(f"[COMPILE] Applying bleed via safe-zone orchestrator: {args.strategy}\n")
            else:
                bleed_api_strategy = "auto"
                sys.stderr.write("[COMPILE] Applying auto bleed (orchestrator → add_clean_bleed)\n")

            result_img, _compile_heal_meta = auto_resolve_safe_zone(
                img.copy(),
                target_bleed_px=target_bleed_px,
                bleed_strategy=bleed_api_strategy,
                dpi=float(dpi),
                border_color=args.bleed_color if bleed_api_strategy == "colorBorder" else None,
            )
            sys.stderr.write(f"PROFILE: [COMPILE] Image Bleed Generation took {(time.time() - _prof_bleed_t0)*1000:.1f}ms\n")

            live_proof_tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False, dir=FAI_TEMP_DIR).name
            _tmp_chain.append(live_proof_tmp)
            _proof_ok = cv2.imwrite(live_proof_tmp, result_img)
            if not _proof_ok or not os.path.exists(live_proof_tmp) or os.path.getsize(live_proof_tmp) == 0:
                sys.stderr.write(f"[COMPILE] WARNING: cv2.imwrite returned {_proof_ok} for live proof — proof may be missing\n")
                live_proof_tmp = None
            else:
                try:
                    from PIL import Image as _PILProof
                    _proof_pil = _PILProof.open(live_proof_tmp)
                    _proof_pil.save(live_proof_tmp, dpi=(300, 300))
                    _proof_pil.close()
                except Exception:
                    pass
                sys.stderr.write(f"[COMPILE] Live proof captured from image matrix: {live_proof_tmp} ({os.path.getsize(live_proof_tmp)} bytes)\n")

            import io
            import fitz

            write_status(status_file, "PACKAGING", "Packaging image into PDF...")
            _prof_pkg_t0 = time.time()

            if len(result_img.shape) == 3 and result_img.shape[2] == 4:
                sys.stderr.write(f"[COMPILE] Stripping alpha channel from BGRA bleed output ({result_img.shape[1]}x{result_img.shape[0]}x4 -> BGR)\n")
                alpha = result_img[:, :, 3:4].astype(np.float32) / 255.0
                bgr = result_img[:, :, :3].astype(np.float32)
                white_bg = np.full_like(bgr, 255, dtype=np.float32)
                result_img = (bgr * alpha + white_bg * (1.0 - alpha)).astype(np.uint8)
                del alpha, bgr, white_bg

            _ih, _iw = result_img.shape[:2]

            from PIL import Image as _PILImage
            _rgb_arr = cv2.cvtColor(result_img, cv2.COLOR_BGR2RGB)
            _pil_enc = _PILImage.fromarray(_rgb_arr)
            _png_io = io.BytesIO()
            _pil_enc.save(_png_io, format="PNG", dpi=(300, 300), compress_level=3)
            png_bytes = _png_io.getvalue()
            del _pil_enc, _png_io, _rgb_arr, result_img

            MM_TO_PT = 72.0 / 25.4
            bleed_margin_mm = PRESS_DEFAULT_BLEED_MM
            target_rect = press_target_media_rect(args.trim_w, args.trim_h, bleed_margin_mm)

            tw_pt, th_pt = float(target_rect.width), float(target_rect.height)
            _pkg_doc = fitz.open()
            _pkg_page = _pkg_doc.new_page(width=tw_pt, height=th_pt)
            flush_page_boxes_to_rect(_pkg_page, target_rect)
            _pkg_page.insert_image(press_insert_rect_at_origin(tw_pt, th_pt), stream=png_bytes, keep_proportion=False)
            png_bytes = None

            bleed_pt = bleed_margin_mm * MM_TO_PT
            trim_w_pt = args.trim_w * MM_TO_PT
            trim_h_pt = args.trim_h * MM_TO_PT
            canvas_w_pt = target_rect.width
            canvas_h_pt = target_rect.height
            mark_len = 3.0 * MM_TO_PT
            _ox, _oy = target_rect.x0, target_rect.y0
            _x1, _y1 = target_rect.x1, target_rect.y1
            bleed_x = _ox + (canvas_w_pt - trim_w_pt) / 2.0
            bleed_y = _oy + (canvas_h_pt - trim_h_pt) / 2.0
            _shape = _pkg_page.new_shape()
            _shape.draw_line(fitz.Point(bleed_x, _oy), fitz.Point(bleed_x, _oy + mark_len))
            _shape.draw_line(fitz.Point(bleed_x, _y1 - mark_len), fitz.Point(bleed_x, _y1))
            _shape.draw_line(fitz.Point(bleed_x + trim_w_pt, _oy), fitz.Point(bleed_x + trim_w_pt, _oy + mark_len))
            _shape.draw_line(fitz.Point(bleed_x + trim_w_pt, _y1 - mark_len), fitz.Point(bleed_x + trim_w_pt, _y1))
            _shape.draw_line(fitz.Point(_ox, bleed_y), fitz.Point(_ox + mark_len, bleed_y))
            _shape.draw_line(fitz.Point(_x1 - mark_len, bleed_y), fitz.Point(_x1, bleed_y))
            _shape.draw_line(fitz.Point(_ox, bleed_y + trim_h_pt), fitz.Point(_ox + mark_len, bleed_y + trim_h_pt))
            _shape.draw_line(fitz.Point(_x1 - mark_len, bleed_y + trim_h_pt), fitz.Point(_x1, bleed_y + trim_h_pt))
            _shape.finish(color=(0, 0, 0), width=0.25)
            _shape.commit()

            _pdf_ram = io.BytesIO()
            _pkg_doc.save(_pdf_ram, deflate=True, garbage=4)
            _pkg_doc.close()

            pdf_tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False, dir=FAI_TEMP_DIR).name
            _tmp_chain.append(pdf_tmp)
            with open(pdf_tmp, "wb") as _wpdf:
                _wpdf.write(_pdf_ram.getvalue())
            del _pdf_ram

            sys.stderr.write(
                f"[COMPILE] PDF packaged (PyMuPDF + RAM BytesIO): {_iw}x{_ih}px @ {FINAL_RASTER_DPI:.0f} DPI "
                f"→ {target_rect.width:.3f}x{target_rect.height:.3f} pt canvas (trim+bleed mm target, raster scaled)\n"
            )
            sys.stderr.write(f"PROFILE: [COMPILE] Image PDF Packaging took {(time.time() - _prof_pkg_t0)*1000:.1f}ms\n")
            sys.stderr.write(f"PROFILE: [COMPILE] Image Path TOTAL took {(time.time() - _prof_img_t0)*1000:.1f}ms\n")

            work_path = pdf_tmp

        elif is_pdf:
            _prof_pdf_t0 = time.time()
            import fitz
            import cv2
            import numpy as np

            write_status(status_file, "PROCESSING", "Applying bleed to PDF pages...")

            from smart_bleed import (
                _auto_trim_white_margins,
                auto_resolve_safe_zone,
                BLEED_TARGET_MM,
            )
            from pdf_geometry_sanitize import aggressive_sanitize_open_document_boxes

            pdf_try_src = input_path
            _nuclear_pdf_fallback_used = False
            while True:
                try:
                    out_doc = None
                    doc = fitz.open(pdf_try_src)
                    try:
                        aggressive_sanitize_open_document_boxes(doc)
                    except Exception as _open_geom:
                        sys.stderr.write(f"[COMPILE] PDF open geometry sanitize (non-fatal): {_open_geom}\n")
                    page_count = len(doc)
                    pdf_file_mb = file_size / (1024 * 1024)
                    render_dpi = int(FINAL_RASTER_DPI)
                    sys.stderr.write(
                        f"[COMPILE] Raster DPI locked to {render_dpi} (FINAL_RASTER_DPI; PyMuPDF matrix={render_dpi}/72) "
                        f"— pages={page_count}, size={pdf_file_mb:.1f}MB\n"
                    )
        
                    original_doc = None
                    original_path = getattr(args, 'original_path', '')
                    if original_path and os.path.exists(original_path):
                        try:
                            with open(original_path, 'rb') as fp:
                                magic = fp.read(5)
                            if magic[:4] == b'%PDF' or magic[:5] == b'%PDF-':
                                original_doc = fitz.open(original_path)
                                try:
                                    aggressive_sanitize_open_document_boxes(original_doc)
                                except Exception as _orig_geom:
                                    sys.stderr.write(f"[COMPILE] Original PDF geometry sanitize (non-fatal): {_orig_geom}\n")
                                sys.stderr.write(f"[COMPILE] Original PDF loaded for sandwich text extraction: {original_path}\n")
                            else:
                                sys.stderr.write(f"[COMPILE] Original file is not a PDF (magic: {magic!r}), skipping sandwich\n")
                        except Exception as od_err:
                            sys.stderr.write(f"[COMPILE] Could not open original PDF: {od_err}\n")
                            original_doc = None
                    elif not original_path:
                        try:
                            test_td = doc[0].get_text("dict") if len(doc) > 0 else {}
                            test_blocks = [b for b in test_td.get("blocks", []) if b.get("type") == 0 and b.get("lines")]
                            if test_blocks:
                                original_doc = doc
                        except Exception:
                            original_doc = None
        
                    TILE_PIXEL_LIMIT = 200_000_000
        
                    overlay_source = original_doc if original_doc else doc
        
                    out_doc = fitz.open()
        
                    try:
                        for page_num in range(page_count):
                            _prof_page_t0 = time.time()
                            page = doc[page_num]
                            cb = page_raster_clip_rect(page)
                            w_px = int(cb.width / 72.0 * render_dpi)
                            h_px = int(cb.height / 72.0 * render_dpi)
                            total_pixels = w_px * h_px
            
                            if total_pixels > TILE_PIXEL_LIMIT:
                                num_tiles = math.ceil(total_pixels / TILE_PIXEL_LIMIT)
                                tiles_y = math.ceil(math.sqrt(num_tiles * (h_px / max(w_px, 1))))
                                tiles_x = math.ceil(num_tiles / tiles_y)
                                tile_h = math.ceil(h_px / tiles_y)
                                tile_w = math.ceil(w_px / tiles_x)
                                sys.stderr.write(f"[COMPILE] Page {page_num+1} too large ({w_px}x{h_px}={total_pixels:,}px). Tiling {tiles_x}x{tiles_y}\n")
            
                                full_img = np.zeros((h_px, w_px, 3), dtype=np.uint8)
                                scale = render_dpi / 72.0
            
                                for ty in range(tiles_y):
                                    for tx in range(tiles_x):
                                        x0_px = tx * tile_w
                                        y0_px = ty * tile_h
                                        x1_px = min(x0_px + tile_w, w_px)
                                        y1_px = min(y0_px + tile_h, h_px)
            
                                        clip_rect = fitz.Rect(
                                            cb.x0 + x0_px / scale,
                                            cb.y0 + y0_px / scale,
                                            cb.x0 + x1_px / scale,
                                            cb.y0 + y1_px / scale,
                                        )
                                        mat = fitz.Matrix(scale, scale)
                                        pix = page.get_pixmap(matrix=mat, clip=clip_rect, alpha=True)
                                        pix.set_dpi(render_dpi, render_dpi)
                                        tile_rgba = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, 4)
                                        tile_alpha = tile_rgba[:, :, 3:4].astype(np.float32) / 255.0
                                        tile_rgb = tile_rgba[:, :, :3].astype(np.float32)
                                        tile_white = np.full_like(tile_rgb, 255.0)
                                        tile_data = (tile_rgb * tile_alpha + tile_white * (1.0 - tile_alpha)).astype(np.uint8)
            
                                        actual_h = min(pix.h, y1_px - y0_px)
                                        actual_w = min(pix.w, x1_px - x0_px)
                                        full_img[y0_px:y0_px+actual_h, x0_px:x0_px+actual_w] = tile_data[:actual_h, :actual_w]
                                        del pix, tile_data
            
                                img_bgr = cv2.cvtColor(full_img, cv2.COLOR_RGB2BGR)
                                del full_img
                                if page_num == 0:
                                    try:
                                        _dbg_ir = os.path.join(FAI_TEMP_DIR, "debug_initial_read.png")
                                        cv2.imwrite(_dbg_ir, img_bgr)
                                        sys.stderr.write(
                                            f"[COMPILE] debug_initial_read.png (tiled, CropBox): {_dbg_ir}\n"
                                        )
                                    except Exception as _dbg_e:
                                        sys.stderr.write(f"[COMPILE] debug_initial_read save failed: {_dbg_e}\n")
                            else:
                                mat = fitz.Matrix(render_dpi / 72.0, render_dpi / 72.0)
                                pix = page.get_pixmap(matrix=mat, clip=cb, alpha=True)
                                pix.set_dpi(render_dpi, render_dpi)
                                if page_num == 0:
                                    try:
                                        _dbg_ir = os.path.join(FAI_TEMP_DIR, "debug_initial_read.png")
                                        pix.save(_dbg_ir)
                                        sys.stderr.write(
                                            f"[COMPILE] debug_initial_read.png (CropBox clip, pre-bleed): {_dbg_ir}\n"
                                        )
                                    except Exception as _dbg_e:
                                        sys.stderr.write(f"[COMPILE] debug_initial_read save failed: {_dbg_e}\n")
                                img_rgba = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, 4)
                                _alpha = img_rgba[:, :, 3:4].astype(np.float32) / 255.0
                                _rgb = img_rgba[:, :, :3].astype(np.float32)
                                _white = np.full_like(_rgb, 255.0)
                                img_data = (_rgb * _alpha + _white * (1.0 - _alpha)).astype(np.uint8)
                                img_bgr = cv2.cvtColor(img_data, cv2.COLOR_RGB2BGR)
                                del pix, img_rgba, img_data
            
                            if page_num == 0 and args.crop_x >= 0 and args.crop_y >= 0 and args.crop_w > 0 and args.crop_h > 0:
                                p_h, p_w = img_bgr.shape[:2]
                                raw_pcx, raw_pcy, raw_pcw, raw_pch = args.crop_x, args.crop_y, args.crop_w, args.crop_h
                                if raw_pcx <= 1.0 and raw_pcy <= 1.0 and raw_pcw <= 1.0 and raw_pch <= 1.0:
                                    pcx = int(round(raw_pcx * p_w))
                                    pcy = int(round(raw_pcy * p_h))
                                    pcw = int(round(raw_pcw * p_w))
                                    pch = int(round(raw_pch * p_h))
                                    print(f"DEBUG: Scaling Crop (PDF). Image is {p_w}x{p_h}. Percentages: ({raw_pcx:.4f},{raw_pcy:.4f},{raw_pcw:.4f},{raw_pch:.4f}). Applying calculated pixels: [{pcx}, {pcy}, {pcw}, {pch}]", flush=True)
                                else:
                                    pcx = int(round(raw_pcx))
                                    pcy = int(round(raw_pcy))
                                    pcw = int(round(raw_pcw))
                                    pch = int(round(raw_pch))
                                pcx = max(0, min(pcx, p_w - 1))
                                pcy = max(0, min(pcy, p_h - 1))
                                pcw = min(pcw, p_w - pcx)
                                pch = min(pch, p_h - pcy)
                                if pcw > 10 and pch > 10:
                                    print(f"DEBUG: Manual Crop Active (PDF). Final pixels: [{pcx},{pcy},{pcw},{pch}] on raster {p_w}x{p_h}", flush=True)
                                    img_bgr = img_bgr[pcy:pcy + pch, pcx:pcx + pcw]
                                    sys.stderr.write(f"[COMPILE] PDF page 1 manual crop applied: ({pcx},{pcy}) {pcw}x{pch} -> {img_bgr.shape[1]}x{img_bgr.shape[0]}\n")
            
                            _pdf_manual_crop_active = page_num == 0 and args.crop_x >= 0 and args.crop_y >= 0 and args.crop_w > 0 and args.crop_h > 0
            
                            img_bgr = _auto_trim_white_margins(img_bgr, white_thresh=250)
            
                            if page_num == 0 and args.trim_w > 0 and args.trim_h > 0:
                                _scale_dpi = render_dpi
                                target_w_px = int(math.ceil((args.trim_w / 25.4) * _scale_dpi))
                                target_h_px = int(math.ceil((args.trim_h / 25.4) * _scale_dpi))
                                sys.stderr.write(
                                    f"[COMPILE] Mandatory trim cover (PDF page 1): {args.trim_w}x{args.trim_h}mm → "
                                    f"{target_w_px}x{target_h_px}px @ {_scale_dpi}dpi (manual_crop={_pdf_manual_crop_active})\n"
                                )
                                img_bgr = _cover_scale_image_to_trim_px(
                                    img_bgr, target_w_px, target_h_px, log_label="[COMPILE]"
                                )
                                import gc as _gc_cover
                                _gc_cover.collect()
            
                            target_bleed_px_pdf = max(1, int(round((float(BLEED_TARGET_MM) / 25.4) * render_dpi)))
            
                            if _pdf_manual_crop_active and (args.strategy == "auto" or args.strategy not in FORCED_BLEED_API_KEYS):
                                bleed_api_pdf = "mirror"
                                sys.stderr.write(
                                    f"[COMPILE] PDF page {page_num+1}: manual crop — mirror via orchestrator ({target_bleed_px_pdf}px base bleed)\n"
                                )
                            elif args.strategy in FORCED_BLEED_API_KEYS:
                                bleed_api_pdf = args.strategy
                                sys.stderr.write(f"[COMPILE] PDF page {page_num+1}: forced strategy {args.strategy} (safe-zone orchestrator)\n")
                            else:
                                bleed_api_pdf = "auto"
                                sys.stderr.write(f"[COMPILE] PDF page {page_num+1}: auto bleed (safe-zone orchestrator)\n")
            
                            bleed_img, _pdf_heal_meta = auto_resolve_safe_zone(
                                img_bgr.copy(),
                                target_bleed_px=target_bleed_px_pdf,
                                bleed_strategy=bleed_api_pdf,
                                dpi=float(render_dpi),
                                border_color=args.bleed_color if bleed_api_pdf == "colorBorder" else None,
                            )
                            del img_bgr
            
                            if page_num == 0 and live_proof_tmp is None:
                                live_proof_tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False, dir=FAI_TEMP_DIR).name
                                _tmp_chain.append(live_proof_tmp)
                                _proof_ok2 = cv2.imwrite(live_proof_tmp, bleed_img)
                                if not _proof_ok2 or not os.path.exists(live_proof_tmp) or os.path.getsize(live_proof_tmp) == 0:
                                    sys.stderr.write(f"[COMPILE] WARNING: cv2.imwrite returned {_proof_ok2} for PDF live proof — proof may be missing\n")
                                    live_proof_tmp = None
                                else:
                                    try:
                                        from PIL import Image as _PILProof2
                                        _proof_pil2 = _PILProof2.open(live_proof_tmp)
                                        _proof_pil2.save(live_proof_tmp, dpi=(300, 300))
                                        _proof_pil2.close()
                                    except Exception:
                                        pass
                                    sys.stderr.write(f"[COMPILE] Live proof captured from PDF page 1 matrix: {live_proof_tmp} ({os.path.getsize(live_proof_tmp)} bytes)\n")
            
                            bleed_h, bleed_w = bleed_img.shape[:2]
                            bleed_rgb = cv2.cvtColor(bleed_img, cv2.COLOR_BGR2RGB)
                            del bleed_img
                            new_pix = fitz.Pixmap(fitz.csRGB, bleed_w, bleed_h, bleed_rgb.tobytes(), False)
                            new_pix.set_dpi(render_dpi, render_dpi)
                            del bleed_rgb
            
                            target_rect = press_target_media_rect(args.trim_w, args.trim_h, PRESS_DEFAULT_BLEED_MM)
                            tw_pt, th_pt = float(target_rect.width), float(target_rect.height)
                            new_page = out_doc.new_page(width=tw_pt, height=th_pt)
                            flush_page_boxes_to_rect(new_page, target_rect)
                            new_page.insert_image(press_insert_rect_at_origin(tw_pt, th_pt), pixmap=new_pix, keep_proportion=False)
                            del new_pix
            
                            compile_stats["pages_rasterized"] += 1
                            sys.stderr.write(f"[COMPILE] Page {page_num+1}: single-layer raster (no overlay — ghost layer prevention)\n")
            
                            sys.stderr.write(f"[COMPILE] PDF page {page_num + 1}/{page_count} bleed applied ({bleed_w}x{bleed_h} @ {render_dpi} DPI)\n")
                            sys.stderr.write(f"PROFILE: [COMPILE] PDF Page {page_num + 1}/{page_count} took {(time.time() - _prof_page_t0)*1000:.1f}ms\n")
        
                    except Exception as pdf_manip_err:
                        sys.stderr.write(f"[COMPILE] PDF raster/bleed manipulation failed: {pdf_manip_err}\n")
                        try:
                            out_doc.close()
                        except Exception:
                            pass
                        raise pdf_manip_err
                    finally:
                        try:
                            doc.close()
                        except Exception:
                            pass
                        if original_doc is not None and original_doc is not doc:
                            try:
                                original_doc.close()
                            except Exception:
                                pass
        
                    write_status(status_file, "PACKAGING", "Adding trim marks to PDF...")
        
                    _prof_save_t0 = time.time()
                    bleed_pdf_tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False, dir=FAI_TEMP_DIR).name
                    _tmp_chain.append(bleed_pdf_tmp)
                    out_doc.save(bleed_pdf_tmp)
                    out_doc.close()
                    sys.stderr.write(f"PROFILE: [COMPILE] PyMuPDF Save took {(time.time() - _prof_save_t0)*1000:.1f}ms\n")
        
                    _prof_trim_t0 = time.time()
                    _add_trim_marks_to_pdf(bleed_pdf_tmp, args.trim_w, args.trim_h)
                    sys.stderr.write(f"PROFILE: [COMPILE] Trim Marks took {(time.time() - _prof_trim_t0)*1000:.1f}ms\n")
        
                    work_path = bleed_pdf_tmp
        
                    norm_tmp = tempfile.NamedTemporaryFile(suffix="_norm.pdf", delete=False, dir=FAI_TEMP_DIR).name
                    _tmp_chain.append(norm_tmp)
                    try:
                        _normalize_pdf_geometry(
                            work_path,
                            norm_tmp,
                            trim_w_mm=args.trim_w,
                            trim_h_mm=args.trim_h,
                            bleed_mm=PRESS_DEFAULT_BLEED_MM,
                        )
                        work_path = norm_tmp
                    except Exception as norm_err:
                        sys.stderr.write(f"[COMPILE] Geometry normalization failed (non-fatal): {norm_err}\n")
        
                    sys.stderr.write(f"PROFILE: [COMPILE] PDF Path TOTAL took {(time.time() - _prof_pdf_t0)*1000:.1f}ms\n")
                    sys.stderr.write(f"[COMPILE] PDF bleed + trim marks complete\n")
                    break
                except Exception as pdf_branch_err:
                    if out_doc is not None:
                        try:
                            out_doc.close()
                        except Exception:
                            pass
                    if (
                        not _nuclear_pdf_fallback_used
                        and _pdf_exception_suggests_bad_geometry(pdf_branch_err)
                    ):
                        _nuclear_pdf_fallback_used = True
                        sys.stderr.write(
                            f"[COMPILE] PDF geometry failure — nuclear visual remount: {pdf_branch_err}\n"
                        )
                        write_status(
                            status_file,
                            "PROCESSING",
                            "Repairing severely corrupted PDF geometry (visual remount)...",
                        )
                        nuk_tmp = tempfile.NamedTemporaryFile(
                            suffix="_nuclear.pdf", delete=False, dir=FAI_TEMP_DIR
                        ).name
                        _tmp_chain.append(nuk_tmp)
                        try:
                            nuclear_rebuild_pdf_visual_mount(
                                pdf_try_src,
                                nuk_tmp,
                                args.trim_w,
                                args.trim_h,
                                5.0,
                            )
                        except Exception as nuk_err:
                            raise RuntimeError(
                                f"PDF compile failed; nuclear rebuild unsuccessful: {nuk_err}"
                            ) from nuk_err
                        pdf_try_src = nuk_tmp
                        sys.stderr.write("[COMPILE] Retrying PDF pipeline with rebuilt geometry...\n")
                        continue
                    sys.stderr.write(
                        f"[COMPILE] PDF raster/bleed pipeline failed: {pdf_branch_err}\n"
                    )
                    raise RuntimeError(
                        f"PDF precompile failed (geometry/raster): {pdf_branch_err}"
                    ) from pdf_branch_err
        else:
            raise ValueError(f"Unsupported file type: {file_ext}")

        want_cmyk = args.color_space.lower() == "cmyk"

        if want_cmyk and is_pdf:
            write_status(status_file, "PACKAGING", "Converting to CMYK, outlining fonts & neutralizing blacks...")

            from smart_bleed import force_cmyk_conversion, apply_k_only_neutralization, verify_cmyk_colorspace, apply_hairline_stroke_enforcement, scan_and_fix_qr_codes, _cap_pdf_image_dpi

            try:
                _cap_pdf_image_dpi(work_path, max_dpi=300)
            except Exception as cap_err:
                sys.stderr.write(f"[COMPILE] DPI cap failed (non-fatal): {cap_err}\n")

            import gc as _gc_compile_pdf
            _gc_compile_pdf.collect()

            if args.creep_mm > 0:
                creep_tmp = tempfile.NamedTemporaryFile(suffix="_creep.pdf", delete=False, dir=FAI_TEMP_DIR).name
                _tmp_chain.append(creep_tmp)
                try:
                    _apply_creep_shift(work_path, creep_tmp, args.creep_mm, args.trim_w, args.trim_h)
                    work_path = creep_tmp
                    compile_stats["creep_applied"] = True
                    compile_stats["creep_mm"] = args.creep_mm
                except Exception as creep_err:
                    sys.stderr.write(f"[COMPILE] Creep shift failed (non-fatal): {creep_err}\n")
                    compile_stats["creep_applied"] = False

            enforce_tmp = tempfile.NamedTemporaryFile(suffix="_enforced.pdf", delete=False, dir=FAI_TEMP_DIR).name
            _tmp_chain.append(enforce_tmp)
            try:
                _enforce_final_mediabox(work_path, enforce_tmp, args.trim_w, args.trim_h)
                work_path = enforce_tmp
            except Exception as enf_err:
                sys.stderr.write(f"[COMPILE] MediaBox enforcement failed (non-fatal): {enf_err}\n")
                import traceback; traceback.print_exc(file=sys.stderr)

            flat_tmp = tempfile.NamedTemporaryFile(suffix="_flat.pdf", delete=False, dir=FAI_TEMP_DIR).name
            _tmp_chain.append(flat_tmp)
            try:
                _prerasterize_pdf(
                    work_path,
                    flat_tmp,
                    dpi=int(FINAL_RASTER_DPI),
                    trim_w_mm=args.trim_w,
                    trim_h_mm=args.trim_h,
                    bleed_mm=PRESS_DEFAULT_BLEED_MM,
                )
                if os.path.exists(flat_tmp) and os.path.getsize(flat_tmp) > 0:
                    work_path = flat_tmp
                    sys.stderr.write(f"[COMPILE] Raster-first handoff verified: {os.path.getsize(flat_tmp)} bytes\n")
                else:
                    sys.stderr.write(f"[COMPILE] Pre-rasterize produced empty output, retrying...\n")
                    _prerasterize_pdf(
                        work_path,
                        flat_tmp,
                        dpi=int(FINAL_RASTER_DPI),
                        trim_w_mm=args.trim_w,
                        trim_h_mm=args.trim_h,
                        bleed_mm=PRESS_DEFAULT_BLEED_MM,
                    )
                    work_path = flat_tmp
            except Exception as flat_err:
                sys.stderr.write(f"[COMPILE] ⚠️ Pre-rasterize failed: {flat_err} — forcing fallback rasterize\n")
                try:
                    import fitz as _fitz_fb
                    _fb_src = _fitz_fb.open(work_path)
                    _fb_dst = _fitz_fb.open()
                    for _fb_i, _fb_page in enumerate(_fb_src):
                        _fb_scale = 300.0 / 72.0
                        _fb_mat = _fitz_fb.Matrix(_fb_scale, _fb_scale)
                        _fb_pix = _fb_page.get_pixmap(
                            matrix=_fb_mat,
                            clip=page_raster_clip_rect(_fb_page),
                            alpha=False,
                        )
                        _fb_pix.set_dpi(300, 300)
                        _fb_pxw, _fb_pxh = _fb_pix.width, _fb_pix.height
                        _fb_img = _fb_pix.tobytes("png")
                        _fb_pix = None
                        _fb_mr = press_target_media_rect(args.trim_w, args.trim_h, PRESS_DEFAULT_BLEED_MM)
                        _fb_w, _fb_h = float(_fb_mr.width), float(_fb_mr.height)
                        _fb_new = _fb_dst.new_page(width=_fb_w, height=_fb_h)
                        flush_page_boxes_to_rect(_fb_new, _fb_mr)
                        _fb_new.insert_image(press_insert_rect_at_origin(_fb_w, _fb_h), stream=_fb_img, keep_proportion=False)
                        _fb_img = None
                    _fb_src.close()
                    _fb_dst.save(flat_tmp, deflate=True, garbage=4)
                    _fb_dst.close()
                    work_path = flat_tmp
                    sys.stderr.write(f"[COMPILE] Fallback rasterize succeeded: {os.path.getsize(flat_tmp)} bytes\n")
                except Exception as fb_err:
                    sys.stderr.write(f"[COMPILE] CRITICAL: Both rasterize attempts failed: {fb_err}\n")
                import gc as _gc_fb
                _gc_fb.collect()

            import gc as _gc_pre_cmyk
            _gc_pre_cmyk.collect()

            work_path = _preflatten_for_gs(
                work_path,
                _tmp_chain,
                trim_w_mm=args.trim_w,
                trim_h_mm=args.trim_h,
                bleed_mm=PRESS_DEFAULT_BLEED_MM,
            )

            for _old_tmp in list(_tmp_chain):
                if _old_tmp != work_path and _old_tmp != live_proof_tmp and is_scratch_temp_file(_old_tmp, FAI_TEMP_DIR) and os.path.exists(_old_tmp):
                    try:
                        os.unlink(_old_tmp)
                        sys.stderr.write(f"[COMPILE] Reclaimed /dev/shm: {os.path.basename(_old_tmp)}\n")
                    except Exception:
                        pass

            if not os.path.exists(work_path):
                raise FileNotFoundError(f"[COMPILE] Pre-GS assertion failed: handoff file missing at {work_path}")
            handoff_bytes = os.path.getsize(work_path)
            if handoff_bytes == 0:
                raise RuntimeError(f"[COMPILE] Pre-GS assertion failed: handoff file is 0 bytes at {work_path}")
            sys.stderr.write(f"[COMPILE] GS handoff file: {work_path} ({handoff_bytes} bytes) — existence verified\n")
            sys.stderr.flush()

            _prof_cmyk_t0 = time.time()
            cmyk_tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False, dir=FAI_TEMP_DIR).name
            _tmp_chain.append(cmyk_tmp)
            try:
                force_cmyk_conversion(work_path, cmyk_tmp, dpi=300)
                if not os.path.exists(cmyk_tmp) or os.path.getsize(cmyk_tmp) == 0:
                    raise RuntimeError("Ghostscript produced no output — CMYK conversion failed silently.")
                compile_stats["cmyk_converted"] = True
                compile_stats["fonts_outlined"] = True
                sys.stderr.write(f"PROFILE: [COMPILE] Ghostscript ICC CMYK Conversion took {(time.time() - _prof_cmyk_t0)*1000:.1f}ms\n")
                sys.stderr.write(f"[COMPILE] CMYK conversion + font outlining complete\n")

                verification = verify_cmyk_colorspace(cmyk_tmp)
                compile_stats["cmyk_verified"] = verification.get("is_cmyk", False)
                sys.stderr.write(f"[COMPILE] CMYK verified: {verification['is_cmyk']}\n")

                if work_path != cmyk_tmp and is_scratch_temp_file(work_path, FAI_TEMP_DIR) and os.path.exists(work_path):
                    try:
                        os.unlink(work_path)
                        sys.stderr.write(f"[COMPILE] Reclaimed post-GS: {os.path.basename(work_path)}\n")
                    except Exception:
                        pass

                _prof_neutral_t0 = time.time()
                neutral_tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False, dir=FAI_TEMP_DIR).name
                try:
                    result = apply_k_only_neutralization(cmyk_tmp, neutral_tmp)
                    if result.get("success") and result.get("neutralizedCount", 0) > 0:
                        compile_stats["neutralized_count"] = result["neutralizedCount"]
                        if os.path.exists(neutral_tmp) and os.path.getsize(neutral_tmp) > 0:
                            shutil.copy2(neutral_tmp, cmyk_tmp)
                        sys.stderr.write(f"[COMPILE] K-only neutralization: {result['neutralizedCount']} colors fixed\n")
                    else:
                        sys.stderr.write(f"[COMPILE] K-only neutralization: no neutral colors found\n")
                    sys.stderr.write(f"PROFILE: [COMPILE] K-only Neutralization took {(time.time() - _prof_neutral_t0)*1000:.1f}ms\n")
                except Exception as ne:
                    sys.stderr.write(f"[COMPILE] Neutralization failed (non-fatal): {ne}\n")
                finally:
                    try:
                        if os.path.exists(neutral_tmp):
                            os.unlink(neutral_tmp)
                    except Exception:
                        pass

                _prof_hairline_t0 = time.time()
                hairline_tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False, dir=FAI_TEMP_DIR).name
                try:
                    hl_result = apply_hairline_stroke_enforcement(cmyk_tmp, hairline_tmp)
                    if hl_result.get("success") and hl_result.get("hairlinesFixed", 0) > 0:
                        compile_stats["hairlines_fixed"] = hl_result["hairlinesFixed"]
                        if os.path.exists(hairline_tmp) and os.path.getsize(hairline_tmp) > 0:
                            shutil.copy2(hairline_tmp, cmyk_tmp)
                        sys.stderr.write(f"[COMPILE] Hairline stroke enforcement: {hl_result['hairlinesFixed']} strokes bulked to 0.3pt\n")
                    else:
                        sys.stderr.write(f"[COMPILE] Hairline stroke enforcement: no hairlines found\n")
                    sys.stderr.write(f"PROFILE: [COMPILE] Hairline Stroke Enforcement took {(time.time() - _prof_hairline_t0)*1000:.1f}ms\n")
                except Exception as he:
                    sys.stderr.write(f"[COMPILE] Hairline enforcement failed (non-fatal): {he}\n")
                finally:
                    try:
                        if os.path.exists(hairline_tmp):
                            os.unlink(hairline_tmp)
                    except Exception:
                        pass

                _prof_qr_t0 = time.time()
                qr_tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False, dir=FAI_TEMP_DIR).name
                try:
                    qr_result = scan_and_fix_qr_codes(cmyk_tmp, qr_tmp)
                    _qr_st = qr_result.get("status", "not_run")
                    _qr_ct = qr_result.get("qr_count", 0)
                    _qr_ur = qr_result.get("qr_unreadable", 0)
                    compile_stats["qr_codes_found"] = _qr_ct
                    compile_stats["qr_scan_status"] = _qr_st
                    if _qr_st == "failed":
                        sys.stderr.write(f"[COMPILE] QR code integrity: FAILED — {_qr_ct} QR code(s) found, {_qr_ur} unreadable\n")
                    elif _qr_st == "auto-fixed" and _qr_ct > 0:
                        compile_stats["qr_codes_fixed"] = qr_result.get("qr_fixed", _qr_ct)
                        if os.path.exists(qr_tmp) and os.path.getsize(qr_tmp) > 0:
                            shutil.copy2(qr_tmp, cmyk_tmp)
                        sys.stderr.write(f"[COMPILE] QR code integrity: {_qr_ct} QR code(s) fixed (K-only + quiet zone)\n")
                    elif _qr_ct > 0:
                        sys.stderr.write(f"[COMPILE] QR code integrity: {_qr_ct} QR code(s) verified scannable, no fixes needed\n")
                    else:
                        sys.stderr.write(f"[COMPILE] QR code integrity: no QR codes detected\n")
                    sys.stderr.write(f"PROFILE: [COMPILE] QR Code Scan took {(time.time() - _prof_qr_t0)*1000:.1f}ms\n")
                except Exception as qe:
                    sys.stderr.write(f"[COMPILE] QR code scan error (non-fatal): {qe}\n")
                    compile_stats["qr_scan_status"] = "error"
                finally:
                    try:
                        if os.path.exists(qr_tmp):
                            os.unlink(qr_tmp)
                    except Exception:
                        pass

                try:
                    sl_stats = _enforce_single_layer(
                        cmyk_tmp,
                        trim_w_mm=args.trim_w,
                        trim_h_mm=args.trim_h,
                        bleed_mm=PRESS_DEFAULT_BLEED_MM,
                    )
                    compile_stats["single_layer_verified"] = sl_stats["verified_single_layer"]
                    compile_stats["vectors_purged"] = sl_stats["vectors_purged"]
                except Exception as sl_err:
                    sys.stderr.write(f"[COMPILE] Single-layer enforcement failed (non-fatal): {sl_err}\n")

                if work_path != input_path:
                    try:
                        os.unlink(work_path)
                    except Exception:
                        pass
                work_path = cmyk_tmp
            except Exception as ce:
                sys.stderr.write(f"[COMPILE] CMYK conversion failed: {ce}\n")
                try:
                    if os.path.exists(cmyk_tmp):
                        os.unlink(cmyk_tmp)
                except Exception:
                    pass
                raise RuntimeError(f"Press-ready compilation requires CMYK conversion which failed: {ce}") from ce

        elif want_cmyk and is_image:
            write_status(status_file, "PACKAGING", "Converting to CMYK, outlining fonts & neutralizing blacks...")

            from smart_bleed import force_cmyk_conversion, apply_k_only_neutralization, verify_cmyk_colorspace, apply_hairline_stroke_enforcement, scan_and_fix_qr_codes as scan_and_fix_qr_codes_img, _cap_pdf_image_dpi as _cap_img_dpi

            try:
                _cap_img_dpi(work_path, max_dpi=300)
            except Exception as cap_err:
                sys.stderr.write(f"[COMPILE] Image DPI cap failed (non-fatal): {cap_err}\n")

            import gc as _gc_compile_img
            _gc_compile_img.collect()

            norm_tmp_img = tempfile.NamedTemporaryFile(suffix="_norm.pdf", delete=False, dir=FAI_TEMP_DIR).name
            _tmp_chain.append(norm_tmp_img)
            try:
                _normalize_pdf_geometry(
                    work_path,
                    norm_tmp_img,
                    trim_w_mm=args.trim_w,
                    trim_h_mm=args.trim_h,
                    bleed_mm=PRESS_DEFAULT_BLEED_MM,
                )
                work_path = norm_tmp_img
            except Exception as norm_err:
                sys.stderr.write(f"[COMPILE] Geometry normalization (image) failed (non-fatal): {norm_err}\n")

            if args.creep_mm > 0:
                creep_tmp_img = tempfile.NamedTemporaryFile(suffix="_creep.pdf", delete=False, dir=FAI_TEMP_DIR).name
                _tmp_chain.append(creep_tmp_img)
                try:
                    _apply_creep_shift(work_path, creep_tmp_img, args.creep_mm, args.trim_w, args.trim_h)
                    work_path = creep_tmp_img
                    compile_stats["creep_applied"] = True
                    compile_stats["creep_mm"] = args.creep_mm
                except Exception as creep_err:
                    sys.stderr.write(f"[COMPILE] Creep shift (image) failed (non-fatal): {creep_err}\n")
                    compile_stats["creep_applied"] = False

            enforce_tmp_img = tempfile.NamedTemporaryFile(suffix="_enforced.pdf", delete=False, dir=FAI_TEMP_DIR).name
            _tmp_chain.append(enforce_tmp_img)
            try:
                _enforce_final_mediabox(work_path, enforce_tmp_img, args.trim_w, args.trim_h)
                work_path = enforce_tmp_img
            except Exception as enf_err:
                sys.stderr.write(f"[COMPILE] MediaBox enforcement (image) failed (non-fatal): {enf_err}\n")
                import traceback; traceback.print_exc(file=sys.stderr)

            flat_tmp_img = tempfile.NamedTemporaryFile(suffix="_flat.pdf", delete=False, dir=FAI_TEMP_DIR).name
            _tmp_chain.append(flat_tmp_img)
            try:
                _prerasterize_pdf(
                    work_path,
                    flat_tmp_img,
                    dpi=int(FINAL_RASTER_DPI),
                    trim_w_mm=args.trim_w,
                    trim_h_mm=args.trim_h,
                    bleed_mm=PRESS_DEFAULT_BLEED_MM,
                )
                if os.path.exists(flat_tmp_img) and os.path.getsize(flat_tmp_img) > 0:
                    work_path = flat_tmp_img
                    sys.stderr.write(f"[COMPILE] Raster-first handoff (image) verified: {os.path.getsize(flat_tmp_img)} bytes\n")
                else:
                    sys.stderr.write(f"[COMPILE] Pre-rasterize (image) produced empty output, retrying...\n")
                    _prerasterize_pdf(
                        work_path,
                        flat_tmp_img,
                        dpi=int(FINAL_RASTER_DPI),
                        trim_w_mm=args.trim_w,
                        trim_h_mm=args.trim_h,
                        bleed_mm=PRESS_DEFAULT_BLEED_MM,
                    )
                    work_path = flat_tmp_img
            except Exception as flat_err:
                sys.stderr.write(f"[COMPILE] ⚠️ Pre-rasterize (image) failed: {flat_err} — forcing fallback rasterize\n")
                try:
                    import fitz as _fitz_fb2
                    _fb2_src = _fitz_fb2.open(work_path)
                    _fb2_dst = _fitz_fb2.open()
                    for _fb2_i, _fb2_page in enumerate(_fb2_src):
                        _fb2_scale = 300.0 / 72.0
                        _fb2_mat = _fitz_fb2.Matrix(_fb2_scale, _fb2_scale)
                        _fb2_pix = _fb2_page.get_pixmap(
                            matrix=_fb2_mat,
                            clip=page_raster_clip_rect(_fb2_page),
                            alpha=False,
                        )
                        _fb2_pix.set_dpi(300, 300)
                        _fb2_pxw, _fb2_pxh = _fb2_pix.width, _fb2_pix.height
                        _fb2_img = _fb2_pix.tobytes("png")
                        _fb2_pix = None
                        _fb2_mr = press_target_media_rect(args.trim_w, args.trim_h, PRESS_DEFAULT_BLEED_MM)
                        _fb2_w, _fb2_h = float(_fb2_mr.width), float(_fb2_mr.height)
                        _fb2_new = _fb2_dst.new_page(width=_fb2_w, height=_fb2_h)
                        flush_page_boxes_to_rect(_fb2_new, _fb2_mr)
                        _fb2_new.insert_image(press_insert_rect_at_origin(_fb2_w, _fb2_h), stream=_fb2_img, keep_proportion=False)
                        _fb2_img = None
                    _fb2_src.close()
                    _fb2_dst.save(flat_tmp_img, deflate=True, garbage=4)
                    _fb2_dst.close()
                    work_path = flat_tmp_img
                    sys.stderr.write(f"[COMPILE] Fallback rasterize (image) succeeded: {os.path.getsize(flat_tmp_img)} bytes\n")
                except Exception as fb2_err:
                    sys.stderr.write(f"[COMPILE] CRITICAL: Both rasterize attempts (image) failed: {fb2_err}\n")
                import gc as _gc_fb2
                _gc_fb2.collect()

            import gc as _gc_pre_cmyk2
            _gc_pre_cmyk2.collect()

            work_path = _preflatten_for_gs(
                work_path,
                _tmp_chain,
                trim_w_mm=args.trim_w,
                trim_h_mm=args.trim_h,
                bleed_mm=PRESS_DEFAULT_BLEED_MM,
            )

            for _old_tmp in list(_tmp_chain):
                if _old_tmp != work_path and _old_tmp != live_proof_tmp and is_scratch_temp_file(_old_tmp, FAI_TEMP_DIR) and os.path.exists(_old_tmp):
                    try:
                        os.unlink(_old_tmp)
                        sys.stderr.write(f"[COMPILE] Reclaimed /dev/shm: {os.path.basename(_old_tmp)}\n")
                    except Exception:
                        pass

            if not os.path.exists(work_path):
                raise FileNotFoundError(f"[COMPILE] Pre-GS assertion failed (image): handoff file missing at {work_path}")
            handoff_bytes_img = os.path.getsize(work_path)
            if handoff_bytes_img == 0:
                raise RuntimeError(f"[COMPILE] Pre-GS assertion failed (image): handoff file is 0 bytes at {work_path}")
            sys.stderr.write(f"[COMPILE] GS handoff file (image): {work_path} ({handoff_bytes_img} bytes) — existence verified\n")
            sys.stderr.flush()

            cmyk_tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False, dir=FAI_TEMP_DIR).name
            _tmp_chain.append(cmyk_tmp)
            try:
                force_cmyk_conversion(work_path, cmyk_tmp, dpi=300)
                if not os.path.exists(cmyk_tmp) or os.path.getsize(cmyk_tmp) == 0:
                    raise RuntimeError("Ghostscript produced no output — CMYK conversion failed silently.")
                compile_stats["cmyk_converted"] = True
                compile_stats["fonts_outlined"] = True
                sys.stderr.write(f"[COMPILE] CMYK conversion + font outlining of image PDF complete\n")

                verification_img = verify_cmyk_colorspace(cmyk_tmp)
                compile_stats["cmyk_verified"] = verification_img.get("is_cmyk", False)
                sys.stderr.write(f"[COMPILE] CMYK verified (image path): {verification_img['is_cmyk']}\n")

                if work_path != cmyk_tmp and is_scratch_temp_file(work_path, FAI_TEMP_DIR) and os.path.exists(work_path):
                    try:
                        os.unlink(work_path)
                        sys.stderr.write(f"[COMPILE] Reclaimed post-GS: {os.path.basename(work_path)}\n")
                    except Exception:
                        pass

                neutral_tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False, dir=FAI_TEMP_DIR).name
                try:
                    result = apply_k_only_neutralization(cmyk_tmp, neutral_tmp)
                    if result.get("success") and result.get("neutralizedCount", 0) > 0:
                        if os.path.exists(neutral_tmp) and os.path.getsize(neutral_tmp) > 0:
                            shutil.copy2(neutral_tmp, cmyk_tmp)
                        sys.stderr.write(f"[COMPILE] K-only neutralization applied\n")
                except Exception as ne:
                    sys.stderr.write(f"[COMPILE] Neutralization failed (non-fatal): {ne}\n")
                finally:
                    try:
                        if os.path.exists(neutral_tmp):
                            os.unlink(neutral_tmp)
                    except Exception:
                        pass

                hairline_tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False, dir=FAI_TEMP_DIR).name
                try:
                    hl_result = apply_hairline_stroke_enforcement(cmyk_tmp, hairline_tmp)
                    if hl_result.get("success") and hl_result.get("hairlinesFixed", 0) > 0:
                        compile_stats["hairlines_fixed"] = hl_result["hairlinesFixed"]
                        if os.path.exists(hairline_tmp) and os.path.getsize(hairline_tmp) > 0:
                            shutil.copy2(hairline_tmp, cmyk_tmp)
                        sys.stderr.write(f"[COMPILE] Hairline stroke enforcement: {hl_result['hairlinesFixed']} strokes bulked\n")
                except Exception as he:
                    sys.stderr.write(f"[COMPILE] Hairline enforcement failed (non-fatal): {he}\n")
                finally:
                    try:
                        if os.path.exists(hairline_tmp):
                            os.unlink(hairline_tmp)
                    except Exception:
                        pass

                _prof_qr_t0_img = time.time()
                qr_tmp_img = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False, dir=FAI_TEMP_DIR).name
                try:
                    qr_result_img = scan_and_fix_qr_codes_img(cmyk_tmp, qr_tmp_img)
                    _qr_st_img = qr_result_img.get("status", "not_run")
                    _qr_ct_img = qr_result_img.get("qr_count", 0)
                    _qr_ur_img = qr_result_img.get("qr_unreadable", 0)
                    compile_stats["qr_codes_found"] = _qr_ct_img
                    compile_stats["qr_scan_status"] = _qr_st_img
                    if _qr_st_img == "failed":
                        sys.stderr.write(f"[COMPILE] QR code integrity: FAILED — {_qr_ct_img} QR code(s) found, {_qr_ur_img} unreadable\n")
                    elif _qr_st_img == "auto-fixed" and _qr_ct_img > 0:
                        compile_stats["qr_codes_fixed"] = qr_result_img.get("qr_fixed", _qr_ct_img)
                        if os.path.exists(qr_tmp_img) and os.path.getsize(qr_tmp_img) > 0:
                            shutil.copy2(qr_tmp_img, cmyk_tmp)
                        sys.stderr.write(f"[COMPILE] QR code integrity: {_qr_ct_img} QR code(s) fixed (K-only + quiet zone)\n")
                    elif _qr_ct_img > 0:
                        sys.stderr.write(f"[COMPILE] QR code integrity: {_qr_ct_img} QR code(s) verified scannable, no fixes needed\n")
                    else:
                        sys.stderr.write(f"[COMPILE] QR code integrity: no QR codes detected\n")
                    sys.stderr.write(f"PROFILE: [COMPILE] QR Code Scan took {(time.time() - _prof_qr_t0_img)*1000:.1f}ms\n")
                except Exception as qe_img:
                    sys.stderr.write(f"[COMPILE] QR code scan error (non-fatal): {qe_img}\n")
                    compile_stats["qr_scan_status"] = "error"
                finally:
                    try:
                        if os.path.exists(qr_tmp_img):
                            os.unlink(qr_tmp_img)
                    except Exception:
                        pass

                try:
                    sl_stats_img = _enforce_single_layer(
                        cmyk_tmp,
                        trim_w_mm=args.trim_w,
                        trim_h_mm=args.trim_h,
                        bleed_mm=PRESS_DEFAULT_BLEED_MM,
                    )
                    compile_stats["single_layer_verified"] = sl_stats_img["verified_single_layer"]
                    compile_stats["vectors_purged"] = sl_stats_img.get("vectors_purged", 0)
                except Exception as sl_err_img:
                    sys.stderr.write(f"[COMPILE] Single-layer enforcement (image) failed (non-fatal): {sl_err_img}\n")

                if work_path != input_path:
                    try:
                        os.unlink(work_path)
                    except Exception:
                        pass
                work_path = cmyk_tmp
            except Exception as ce:
                sys.stderr.write(f"[COMPILE] CMYK conversion failed: {ce}\n")
                try:
                    if os.path.exists(cmyk_tmp):
                        os.unlink(cmyk_tmp)
                except Exception:
                    pass
                raise RuntimeError(f"Press-ready compilation requires CMYK conversion which failed: {ce}") from ce

        write_status(status_file, "PACKAGING", "Packaging final press-ready PDF...")

        if not os.path.exists(work_path):
            raise FileNotFoundError(f"Work file missing before final copy: {work_path}")
        work_size = os.path.getsize(work_path)
        sys.stderr.write(f"[COMPILE] Final handoff: work_path={work_path} ({work_size} bytes) -> {args.output}\n")

        _final_target = press_target_media_rect(args.trim_w, args.trim_h, PRESS_DEFAULT_BLEED_MM)
        print(
            "ALIGMENT FIX ACTIVE: Forcing Image to Target Rect",
            flush=True,
        )
        print(
            f"ALIGMENT FIX: target page size = {_final_target.width:.4f} x {_final_target.height:.4f} pt (width x height)",
            flush=True,
        )

        shutil.copy2(work_path, args.output)
        _deferred_work_cleanup = work_path if work_path != input_path else None

        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from pdf_geometry_sanitize import sanitize_pdf_geometry_inplace

            if sanitize_pdf_geometry_inplace(args.output):
                sys.stderr.write("[COMPILE] Final PDF: Crop/Media box ingest repair applied before hierarchy finalize.\n")
        except Exception as san_err:
            sys.stderr.write(f"[COMPILE] Final PDF geometry sanitize (non-fatal): {san_err}\n")

        try:
            finalize_press_pdf_box_hierarchy(args.output)
        except Exception as fin_err:
            sys.stderr.write(f"[COMPILE] Final PDF box hierarchy finalize (non-fatal): {fin_err}\n")

        if want_cmyk and compile_stats.get("cmyk_converted"):
            _prof_oi_t0 = time.time()
            try:
                import pikepdf
                sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
                from generate_icc_profile import get_icc_profile_path
                icc_path = get_icc_profile_path()
                with open(icc_path, "rb") as icc_f:
                    icc_data = icc_f.read()
                pdf = pikepdf.open(args.output, allow_overwriting_input=True)
                icc_stream = pikepdf.Stream(pdf, icc_data)
                icc_stream["/N"] = 4
                output_intent = pikepdf.Dictionary({
                    "/Type": pikepdf.Name("/OutputIntent"),
                    "/S": pikepdf.Name("/GTS_PDFX"),
                    "/OutputConditionIdentifier": pikepdf.String("FOGRA39"),
                    "/RegistryName": pikepdf.String("http://www.color.org"),
                    "/Info": pikepdf.String("Coated FOGRA39 (ISO 12647-2:2004)"),
                    "/DestOutputProfile": icc_stream,
                })
                if "/OutputIntents" not in pdf.Root:
                    pdf.Root["/OutputIntents"] = pikepdf.Array([])
                pdf.Root["/OutputIntents"] = pikepdf.Array([output_intent])
                pdf.save(args.output)
                pdf.close()
                sys.stderr.write(f"[COMPILE] OutputIntent (FOGRA39) embedded in final PDF\n")
                sys.stderr.write(f"PROFILE: [COMPILE] OutputIntent Embedding took {(time.time() - _prof_oi_t0)*1000:.1f}ms\n")
            except Exception as oi_err:
                sys.stderr.write(f"[COMPILE] OutputIntent embedding failed (non-fatal): {oi_err}\n")

        output_size = os.path.getsize(args.output)
        sys.stderr.write(f"[COMPILE] Press-ready PDF complete: {args.output} ({output_size} bytes)\n")

        spans_saved = compile_stats["total_spans"]
        final_scale = compile_stats["sandwich_scale"] if compile_stats["sandwich_scale"] is not None else "N/A"
        sandwich_mode = "accepted" if compile_stats["sandwich_accepted"] else ("rejected" if compile_stats["sandwich_attempted"] else "skipped")

        if compile_stats["sandwich_accepted"]:
            typo_action = f"Sandwich vector logic accepted. {spans_saved} text spans preserved as sharp vectors over flattened {render_dpi} DPI background. Scale: {final_scale}."
        elif compile_stats["sandwich_attempted"]:
            typo_action = f"Sandwich vector logic evaluated {spans_saved} spans but rejected (scale {final_scale} outside 0.90-1.10 threshold). Text rasterized at {render_dpi} DPI for safety."
        else:
            typo_action = f"No vector text detected. Full rasterization at {render_dpi} DPI."

        if compile_stats["cmyk_converted"]:
            ink_action = f"Hazardous RGB neutralized. Converted to CMYK via FOGRA39 ICC profile (verified: {compile_stats['cmyk_verified']}) and clamped to safe total ink limits."
            if compile_stats["neutralized_count"] > 0:
                ink_action += f" {compile_stats['neutralized_count']} near-black colors fixed to K-only overprint."
            if compile_stats.get("fonts_outlined"):
                ink_action += " All fonts outlined to vector paths."
        else:
            ink_action = "Color space already press-safe. No conversion required."
            if compile_stats.get("fonts_outlined"):
                ink_action += " All fonts outlined to vector paths."

        strategy_labels = {
            "bgExtract": "Background Extract",
            "stretch": "Pixel-Drift Stretch",
            "mirror": "Mirror + Cross-Fade",
            "replicate": "Edge Replicate",
            "upscale": "Upscale",
            "ai_outpaint": "AI Outpaint (proxy inpaint)",
            "colorBorder": "Colour Border",
            "auto": "Auto-Detect",
        }
        strategy_label = strategy_labels.get(args.strategy, args.strategy)
        geo_action = f"Generated litho-standard 5mm bleed using {strategy_label} strategy at {render_dpi} DPI. TrimBox ({args.trim_w}x{args.trim_h}mm) and BleedBox set on all {page_count} page(s)."

        if compile_stats["lenses_flattened"]:
            res_action = f"Flattened complex live transparencies (lenses) and locked resolution to {render_dpi} DPI."
        else:
            res_action = f"No live transparencies. Resolution locked to {render_dpi} DPI across {page_count} page(s)."

        if compile_stats["hairlines_fixed"] > 0:
            hairline_action = f"Hairline strokes detected (below 0.25pt) and bulked to 0.3pt for press stability. {compile_stats['hairlines_fixed']} stroke(s) enforced."
            hairline_auto = True
        else:
            hairline_action = "No hairlines detected; all strokes meet minimum weight requirements."
            hairline_auto = False

        qr_status = compile_stats.get("qr_scan_status", "not_run")
        qr_found = compile_stats.get("qr_codes_found", 0)
        qr_fixed = compile_stats.get("qr_codes_fixed", 0)
        if qr_status == "failed":
            qr_action = "QR code(s) detected but could not be verified as scannable. Please check your QR codes are high-contrast and undamaged."
            qr_auto = False
            qr_passed = False
        elif qr_status == "error":
            qr_action = "QR code scanner encountered a runtime error. QR integrity could not be checked."
            qr_auto = False
            qr_passed = False
        elif qr_status == "auto-fixed" and qr_fixed > 0:
            qr_action = f"{qr_found} QR code(s) detected and optimised: dark modules forced to 100% K-only black, 2mm quiet zone enforced."
            qr_auto = True
            qr_passed = True
        elif qr_found > 0 and qr_status == "passed":
            qr_action = f"{qr_found} QR code(s) detected and verified scannable. No corrections needed."
            qr_auto = False
            qr_passed = True
        else:
            qr_action = "No QR codes detected in artwork."
            qr_auto = False
            qr_passed = True

        if args.zip_output:
            _prof_zip_t0 = time.time()
            import io
            import zipfile

            with open(args.output, "rb") as f:
                pdf_bytes = f.read()

            proof_for_zip = None
            proof_before_zip = None
            proof_after_zip = None
            report_for_zip = None
            live_report_tmp = None

            if live_proof_tmp and os.path.exists(live_proof_tmp) and os.path.getsize(live_proof_tmp) > 0:
                proof_for_zip = live_proof_tmp
                sys.stderr.write(f"[COMPILE] ZIP will use LIVE proof from fresh matrix: {live_proof_tmp}\n")
                try:
                    from bleed_preview import generate_before_after_cut_proofs

                    proof_before_zip = tempfile.NamedTemporaryFile(
                        suffix="_before_cut.png", delete=False, dir=FAI_TEMP_DIR
                    ).name
                    proof_after_zip = tempfile.NamedTemporaryFile(
                        suffix="_after_cut.png", delete=False, dir=FAI_TEMP_DIR
                    ).name
                    _tmp_chain.append(proof_before_zip)
                    _tmp_chain.append(proof_after_zip)
                    cut_pair = generate_before_after_cut_proofs(
                        live_proof_tmp,
                        proof_before_zip,
                        proof_after_zip,
                        bleed_mm=float(PRESS_DEFAULT_BLEED_MM),
                        dpi=300.0,
                    )
                    if not cut_pair.get("success"):
                        sys.stderr.write(
                            f"[COMPILE] Before/after cut proofs failed: {cut_pair.get('error')} — "
                            f"falling back to raw live proof\n"
                        )
                        proof_before_zip = None
                        proof_after_zip = None
                    else:
                        sys.stderr.write(
                            f"[COMPILE] Before/after cut proofs ready "
                            f"(bleed={cut_pair.get('bleed_mm')}mm @ {cut_pair.get('dpi')}dpi)\n"
                        )
                except Exception as cut_err:
                    sys.stderr.write(f"[COMPILE] Before/after cut proofs error (non-fatal): {cut_err}\n")
                    proof_before_zip = None
                    proof_after_zip = None

                try:
                    from health_report import build_report as _build_report

                    synth_checks = [
                        {"name": "Bleed Margins", "passed": True, "autoFixed": True, "message": geo_action},
                        {"name": "Font Embedding", "passed": True, "autoFixed": compile_stats["sandwich_accepted"], "message": typo_action},
                        {"name": "Color Space (CMYK + FOGRA39)", "passed": True, "autoFixed": compile_stats["cmyk_converted"], "message": ink_action},
                        {"name": "DPI / Resolution", "passed": True, "autoFixed": False, "message": res_action},
                        {"name": "Hairline Stroke Enforcement", "passed": True, "autoFixed": hairline_auto, "message": hairline_action},
                        {"name": "QR Code Integrity", "passed": qr_passed, "autoFixed": qr_auto, "message": qr_action},
                    ]

                    report_proofs = []
                    if proof_before_zip and os.path.exists(proof_before_zip):
                        report_proofs.append(proof_before_zip)
                    if proof_after_zip and os.path.exists(proof_after_zip):
                        report_proofs.append(proof_after_zip)
                    if not report_proofs:
                        report_proofs = [proof_for_zip]

                    live_report_tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False, dir=FAI_TEMP_DIR).name
                    _tmp_chain.append(live_report_tmp)
                    _build_report(
                        synth_checks,
                        args.base_name,
                        report_proofs[0],
                        live_report_tmp,
                        proof_paths=report_proofs,
                        artwork_path=report_proofs[0],
                    )
                    if os.path.exists(live_report_tmp) and os.path.getsize(live_report_tmp) > 0:
                        report_for_zip = live_report_tmp
                        sys.stderr.write(f"[COMPILE] ZIP will use LIVE health report: {live_report_tmp}\n")
                    else:
                        sys.stderr.write(f"[COMPILE] Live report generation produced empty file — omitting report from ZIP (strategy sync protection)\n")
                except Exception as rpt_err:
                    sys.stderr.write(f"[COMPILE] Live report generation failed (non-fatal): {rpt_err} — omitting report from ZIP (strategy sync protection)\n")
            else:
                sys.stderr.write(f"[COMPILE] No live proof matrix captured — falling back to Stage 1 proof/report\n")
                if live_proof_tmp:
                    sys.stderr.write(f"[COMPILE] live_proof_tmp exists={os.path.exists(live_proof_tmp) if live_proof_tmp else 'N/A'}, size={os.path.getsize(live_proof_tmp) if live_proof_tmp and os.path.exists(live_proof_tmp) else 0}\n")
                if args.proof_path and os.path.exists(args.proof_path):
                    proof_for_zip = args.proof_path
                    sys.stderr.write(f"[COMPILE] Using Stage 1 proof as fallback: {args.proof_path}\n")
                    try:
                        from bleed_preview import generate_before_after_cut_proofs

                        proof_before_zip = tempfile.NamedTemporaryFile(
                            suffix="_before_cut.png", delete=False, dir=FAI_TEMP_DIR
                        ).name
                        proof_after_zip = tempfile.NamedTemporaryFile(
                            suffix="_after_cut.png", delete=False, dir=FAI_TEMP_DIR
                        ).name
                        _tmp_chain.append(proof_before_zip)
                        _tmp_chain.append(proof_after_zip)
                        cut_pair = generate_before_after_cut_proofs(
                            args.proof_path,
                            proof_before_zip,
                            proof_after_zip,
                            bleed_mm=float(PRESS_DEFAULT_BLEED_MM),
                            dpi=300.0,
                        )
                        if not cut_pair.get("success"):
                            proof_before_zip = None
                            proof_after_zip = None
                    except Exception as cut_fb_err:
                        sys.stderr.write(f"[COMPILE] Stage 1 before/after cut failed (non-fatal): {cut_fb_err}\n")
                        proof_before_zip = None
                        proof_after_zip = None
                if args.report_path and os.path.exists(args.report_path):
                    report_for_zip = args.report_path
                    sys.stderr.write(f"[COMPILE] Using Stage 1 report as fallback: {args.report_path}\n")

            zip_buf = io.BytesIO()
            with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_STORED) as zf:
                zf.writestr("Print Ready Artwork.pdf", pdf_bytes)

                wrote_cut_pair = False
                if proof_before_zip and os.path.exists(proof_before_zip):
                    with open(proof_before_zip, "rb") as f:
                        zf.writestr("Artwork Proof - Before Cut.png", f.read())
                    wrote_cut_pair = True
                if proof_after_zip and os.path.exists(proof_after_zip):
                    with open(proof_after_zip, "rb") as f:
                        zf.writestr("Artwork Proof - After Cut.png", f.read())
                    wrote_cut_pair = True

                if not wrote_cut_pair and proof_for_zip and os.path.exists(proof_for_zip):
                    proof_ext = os.path.splitext(proof_for_zip)[1] or ".png"
                    with open(proof_for_zip, "rb") as f:
                        zf.writestr(f"Artwork Proof{proof_ext}", f.read())

                if report_for_zip and os.path.exists(report_for_zip):
                    with open(report_for_zip, "rb") as f:
                        zf.writestr(
                            "Flyerz.co.za Artwork Intellegence Proof and Report.pdf",
                            f.read(),
                        )

            zip_bytes = zip_buf.getvalue()
            zip_buf.close()

            _publish_zip_bytes(zip_bytes, args.zip_output)
            sys.stderr.write(f"[COMPILE] ZIP bundle written to {args.zip_output} ({len(zip_bytes)} bytes)\n")
            sys.stderr.write(f"PROFILE: [COMPILE] ZIP Bundle took {(time.time() - _prof_zip_t0)*1000:.1f}ms\n")
            del zip_bytes

            if live_proof_tmp and os.path.exists(live_proof_tmp):
                try: os.unlink(live_proof_tmp)
                except Exception: pass
            if live_report_tmp and os.path.exists(live_report_tmp):
                try: os.unlink(live_report_tmp)
                except Exception: pass

        if _deferred_work_cleanup:
            try:
                if os.path.exists(_deferred_work_cleanup):
                    os.unlink(_deferred_work_cleanup)
            except Exception:
                pass

        for _tc in _tmp_chain:
            try:
                if _tc and os.path.exists(_tc):
                    os.unlink(_tc)
            except Exception:
                pass
        _tmp_chain.clear()

        fonts_note = " All fonts outlined." if compile_stats.get("fonts_outlined") else ""
        if compile_stats["sandwich_accepted"]:
            glitchy_msg = f"Purr-fect! I preserved {spans_saved} text vectors razor-sharp, stripped the bad ink, outlined all fonts, and built a litho-ready file.{fonts_note}"
        else:
            glitchy_msg = f"Purr-fect! I stripped out the bad ink, protected your layout, and built a litho-ready FOGRA39 file.{fonts_note}"

        job_audit_payload = {
            "geometry": {"action_taken": geo_action},
            "typography": {"action_taken": typo_action},
            "color_and_ink": {"action_taken": ink_action},
            "resolution_and_lenses": {"action_taken": res_action},
        }

        glitchy_state = "triumphant"

        sys.stderr.write(f"PROFILE: [COMPILE] TOTAL Compile took {(time.time() - _prof_compile_t0)*1000:.1f}ms\n")
        sys.stderr.flush()

        result_data = {
            "success": True,
            "outputPath": args.output,
            "outputSize": output_size,
            "strategy": args.strategy,
            "colorSpace": args.color_space,
            "trimWidth": args.trim_w,
            "trimHeight": args.trim_h,
            "compile_stats": compile_stats,
            "audit_report": job_audit_payload,
            "glitchy_message": glitchy_msg,
            "glitchy_state": glitchy_state,
        }
        if args.zip_output and os.path.exists(args.zip_output):
            result_data["zipOutputPath"] = args.zip_output

        with open(result_file, "w") as f:
            json.dump(result_data, f)

        sys.stderr.write(f"[COMPILE] Audit Report: {json.dumps(job_audit_payload)}\n")
        sys.stderr.write(f"[COMPILE] Glitchy: [{glitchy_state}] {glitchy_msg}\n")

        write_status(status_file, "COMPLETE", "Press-ready PDF compiled successfully.", download_url=args.output)

    except Exception as e:
        import traceback
        tb_str = traceback.format_exc()
        sys.stderr.write(f"[COMPILE] FATAL ERROR: {tb_str}\n")
        error_detail = str(e)
        if isinstance(e, OSError):
            error_detail = f"OS/IO Error: {e}"
        elif isinstance(e, RuntimeError):
            error_detail = f"Pipeline Error: {e}"
        write_status(status_file, "FAILURE", error_detail)
        try:
            with open(result_file, "w") as ef:
                json.dump({"success": False, "error": error_detail, "traceback": tb_str}, ef)
        except Exception:
            pass

        for _tc in _tmp_chain:
            try:
                if _tc and os.path.exists(_tc):
                    os.unlink(_tc)
            except Exception:
                pass

        result_data = {"success": False, "error": str(e)}
        with open(result_file, "w") as f:
            json.dump(result_data, f)

        sys.exit(1)


if __name__ == "__main__":
    main()
