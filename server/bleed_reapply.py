"""Re-apply only the 5 mm bleed on a press file that is already made.

The trim is kept. OCR, the 25-point list, and the source pipeline are not run again.
"""

from __future__ import annotations

import argparse
import json
import os
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


def reapply_bleed(press_path: str, strategy: str, trim_w: float, trim_h: float, output_dir: str) -> dict:
    import cv2
    import numpy as np
    import pymupdf as fitz

    from press_ready_engine import write_cmyk_pdf
    from smart_bleed import _apply_forced_strategy_bleed, choose_automatic_bleed_api

    chosen = strategy if strategy in ALLOWED else "auto"
    doc = fitz.open(press_path)
    try:
        page = doc[0]
        zoom = 300.0 / 72.0
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False, colorspace=fitz.csRGB)
        rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3].copy()
        media = page.mediabox
        trim = page.trimbox
        if trim.width < 2 or trim.height < 2 or trim.width >= media.width - 1:
            inset = 5.0 * 72.0 / 25.4
            trim = fitz.Rect(media.x0 + inset, media.y0 + inset, media.x1 - inset, media.y1 - inset)
        x0 = max(0, int(round((trim.x0 - page.rect.x0) * zoom)))
        y0 = max(0, int(round((trim.y0 - page.rect.y0) * zoom)))
        x1 = min(rgb.shape[1], int(round((trim.x1 - page.rect.x0) * zoom)))
        y1 = min(rgb.shape[0], int(round((trim.y1 - page.rect.y0) * zoom)))
        crop = rgb[y0:y1, x0:x1]
    finally:
        doc.close()
    if crop.size == 0:
        raise RuntimeError("The press file has no trim to keep.")
    plane = cv2.cvtColor(crop, cv2.COLOR_RGB2BGR)
    trim_w_px = max(1, int(round(float(trim_w) / 25.4 * 300.0)))
    trim_h_px = max(1, int(round(float(trim_h) / 25.4 * 300.0)))
    bleed_px = max(1, int(round(5.0 / 25.4 * 300.0)))
    if plane.shape[1] != trim_w_px or plane.shape[0] != trim_h_px:
        plane = cv2.resize(plane, (trim_w_px, trim_h_px), interpolation=cv2.INTER_AREA)
    method = choose_automatic_bleed_api(plane, 300.0) if chosen == "auto" else chosen
    border = None
    if method == "colourBorder":
        from colour_border import sample_edge_cmyk

        border = sample_edge_cmyk(plane)
    out = _apply_forced_strategy_bleed(
        plane,
        method,
        bleed_px,
        300.0,
        border_cmyk=border,
        allow_cloud=method == "ai_outpaint",
    )
    expect_h = trim_h_px + 2 * bleed_px
    expect_w = trim_w_px + 2 * bleed_px
    if out is None or out.size == 0:
        raise RuntimeError("The bleed could not be drawn again.")
    if out.shape[0] != expect_h or out.shape[1] != expect_w:
        out = cv2.resize(out[:, :, :3], (expect_w, expect_h), interpolation=cv2.INTER_CUBIC)
    os.makedirs(output_dir, exist_ok=True)
    press_out = os.path.join(output_dir, "press.pdf")
    temporary = press_out + ".rebleed.tmp"
    write_cmyk_pdf(out[:, :, :3], temporary, float(trim_w), float(trim_h), 5.0)
    os.replace(temporary, press_out)
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
