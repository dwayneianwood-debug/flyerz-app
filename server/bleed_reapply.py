"""Re-apply only the 5 mm bleed on a press file that is already made.

The trim pixels stay the ones already in the file. The outer ring is covered
with the new bleed. OCR, the 25-point list, and the source pipeline do not run.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys

METHOD_WORDS = {
    "mirror": "mirrored",
    "replicate": "flat colour",
    "colourBorder": "flat colour",
    "bgExtract": "flat colour",
    "stretch": "pixel-drift stretch",
    "gradient_extrapolate": "continued gradient",
    "frequency_separated": "continued pattern",
    "upscale": "upscaled",
    "ai_outpaint": "outpainted",
}

ALLOWED = set(METHOD_WORDS) | {"auto"}


def bleed_sentence(method: str) -> str:
    words = METHOD_WORDS.get(method, "flat colour")
    return f"Bleed: top/bottom/left/right {words}."


def _trim_rect(page):
    import pymupdf as fitz

    media = page.mediabox
    trim = page.trimbox
    if trim is None or trim.width < 2 or trim.height < 2 or trim.width >= media.width - 0.8 or trim.height >= media.height - 0.8:
        inset = 5.0 * 72.0 / 25.4
        trim = fitz.Rect(media.x0 + inset, media.y0 + inset, media.x1 - inset, media.y1 - inset)
    trim = fitz.Rect(
        max(media.x0, trim.x0),
        max(media.y0, trim.y0),
        min(media.x1, trim.x1),
        min(media.y1, trim.y1),
    )
    return media, trim


def _paint_ring(plane, method: str, bleed_px: int):
    """Draw a new ring around a copy of the trim. The copy is not written back."""
    import cv2

    from smart_bleed import _apply_forced_strategy_bleed, choose_automatic_bleed_api

    chosen = method if method in ALLOWED else "auto"
    used = choose_automatic_bleed_api(plane, 300.0) if chosen == "auto" else chosen
    border = None
    if used == "colourBorder":
        from colour_border import sample_edge_cmyk

        border = sample_edge_cmyk(plane)
    out = _apply_forced_strategy_bleed(
        plane,
        used,
        bleed_px,
        300.0,
        border_cmyk=border,
        allow_cloud=used == "ai_outpaint",
    )
    expect_h = plane.shape[0] + 2 * bleed_px
    expect_w = plane.shape[1] + 2 * bleed_px
    if out is None or getattr(out, "size", 0) == 0 or out.shape[0] < expect_h or out.shape[1] < expect_w:
        style = cv2.BORDER_REFLECT_101 if used == "mirror" else cv2.BORDER_REPLICATE
        out = cv2.copyMakeBorder(plane, bleed_px, bleed_px, bleed_px, bleed_px, style)
    if out.shape[0] != expect_h or out.shape[1] != expect_w:
        # The ring may be resized. The trim crop inside `plane` is never resized.
        ring = out
        canvas = cv2.copyMakeBorder(plane, bleed_px, bleed_px, bleed_px, bleed_px, cv2.BORDER_REPLICATE)
        top = ring[: min(bleed_px, ring.shape[0])]
        if top.size:
            canvas[:bleed_px] = cv2.resize(top, (expect_w, bleed_px), interpolation=cv2.INTER_LINEAR)
        bottom = ring[max(0, ring.shape[0] - bleed_px):]
        if bottom.size:
            canvas[-bleed_px:] = cv2.resize(bottom, (expect_w, bleed_px), interpolation=cv2.INTER_LINEAR)
        left = ring[:, : min(bleed_px, ring.shape[1])]
        if left.size:
            canvas[:, :bleed_px] = cv2.resize(left, (bleed_px, expect_h), interpolation=cv2.INTER_LINEAR)
        right = ring[:, max(0, ring.shape[1] - bleed_px):]
        if right.size:
            canvas[:, -bleed_px:] = cv2.resize(right, (bleed_px, expect_h), interpolation=cv2.INTER_LINEAR)
        canvas[bleed_px:-bleed_px, bleed_px:-bleed_px] = plane
        out = canvas
    return out[:, :, :3], used


def _insert_cmyk(page, rect, bgr) -> None:
    import cv2
    import numpy as np
    import pymupdf as fitz
    from PIL import Image

    if rect.width < 0.2 or rect.height < 0.2 or bgr is None or bgr.size == 0:
        return
    rgb = cv2.cvtColor(bgr[:, :, :3], cv2.COLOR_BGR2RGB)
    cmyk = np.asarray(Image.fromarray(rgb).convert("CMYK"))
    pix = fitz.Pixmap(fitz.csCMYK, cmyk.shape[1], cmyk.shape[0], cmyk.tobytes(), 0)
    page.insert_image(rect, pixmap=pix, keep_proportion=False)


def reapply_bleed(press_path: str, strategy: str, trim_w: float, trim_h: float, output_dir: str) -> dict:
    import cv2
    import numpy as np
    import pymupdf as fitz

    os.makedirs(output_dir, exist_ok=True)
    press_out = os.path.join(output_dir, "press.pdf")
    if os.path.abspath(press_path) != os.path.abspath(press_out):
        shutil.copyfile(press_path, press_out)
    doc = fitz.open(press_out)
    try:
        page = doc[0]
        media, trim = _trim_rect(page)
        zoom = 300.0 / 72.0
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=trim, alpha=False, colorspace=fitz.csRGB)
        rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3].copy()
        if rgb.size == 0:
            raise RuntimeError("The press file has no trim to keep.")
        plane = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        # The ring thickness is the real gap on this page, not a resized trim.
        bleed_px = max(1, int(round(min(trim.x0 - media.x0, trim.y0 - media.y0, media.x1 - trim.x1, media.y1 - trim.y1) * zoom)))
        out, method = _paint_ring(plane, strategy, bleed_px)
        # Strips sit strictly outside the trim. The trim content is not rewritten.
        _insert_cmyk(page, fitz.Rect(media.x0, media.y0, media.x1, trim.y0), out[:bleed_px, :, :])
        _insert_cmyk(page, fitz.Rect(media.x0, trim.y1, media.x1, media.y1), out[-bleed_px:, :, :])
        mid = out[bleed_px:-bleed_px] if out.shape[0] > 2 * bleed_px else out
        _insert_cmyk(page, fitz.Rect(media.x0, trim.y0, trim.x0, trim.y1), mid[:, :bleed_px])
        _insert_cmyk(page, fitz.Rect(trim.x1, trim.y0, media.x1, trim.y1), mid[:, -bleed_px:])
        # Incremental save keeps the original image streams. The trim is not re-encoded.
        doc.saveIncr()
    finally:
        doc.close()

    from quick_print import _write_proof

    proof_png = os.path.join(output_dir, "proof.png")
    proof_pdf = os.path.join(output_dir, "proof.pdf")
    _write_proof(press_out, proof_png, proof_pdf, "Proof. The pink line is the trim. This is not the press file.")
    sentence = bleed_sentence(method)
    return {"ok": True, "bleedNote": sentence, "method": method, "pressPath": press_out, "proofPng": proof_png}


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-apply bleed on a finished press file")
    parser.add_argument("--press", required=True)
    parser.add_argument("--strategy", default="auto")
    parser.add_argument("--trim-w", type=float, required=True)
    parser.add_argument("--trim-h", type=float, required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    try:
        result = reapply_bleed(args.press, args.strategy, args.trim_w, args.trim_h, args.output_dir)
    except Exception as exc:
        sys.stdout.write(json.dumps({"ok": False, "error": str(exc)[:200]}) + "\n")
        raise SystemExit(1)
    sys.stdout.write(json.dumps(result) + "\n")


if __name__ == "__main__":
    main()
