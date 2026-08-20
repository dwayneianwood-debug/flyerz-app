#!/usr/bin/env python3
"""
Stress-test PDF generator for Flyerz Artwork Intelligence.
Generates a single-page A4 PDF with deliberate prepress violations:
  - 15mm white margins on all sides (false margins)
  - RGB (0,0,0) background = 400% TIC when converted to CMYK naively
  - Low-res 72 DPI image stretched across the page
  - 8pt rich black text (will trigger dual-black neutralization)
  - Transparent yellow circle lens overlay (transparency)
"""

import os
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.colors import Color
from reportlab.pdfgen import canvas
from PIL import Image
import numpy as np
import tempfile


OUTPUT_FILE = "stress_test.pdf"

PAGE_W, PAGE_H = A4

MARGIN = 15 * mm


def make_low_res_image():
    arr = np.zeros((50, 80, 3), dtype=np.uint8)
    for y in range(50):
        for x in range(80):
            arr[y, x] = [
                int(128 + 127 * np.sin(x * 0.15)),
                int(64 + 191 * (x / 80)),
                int(200 - 150 * (y / 50)),
            ]
    img = Image.fromarray(arr, "RGB")
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    img.save(tmp.name, dpi=(72, 72))
    return tmp.name


def main():
    c = canvas.Canvas(OUTPUT_FILE, pagesize=A4)
    c.setTitle("Flyerz Stress Test — 400% TIC + 72 DPI + Transparency")
    c.setAuthor("make_test.py")

    c.setFillColor(Color(1, 1, 1))
    c.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)

    content_x = MARGIN
    content_y = MARGIN
    content_w = PAGE_W - 2 * MARGIN
    content_h = PAGE_H - 2 * MARGIN

    c.setFillColor(Color(0, 0, 0))
    c.rect(content_x, content_y, content_w, content_h, fill=1, stroke=0)

    c.setFillColor(Color(1, 1, 1))
    c.setFont("Helvetica-Bold", 14)
    c.drawString(content_x + 8 * mm, content_y + content_h - 12 * mm, "PREPRESS STRESS TEST")

    c.setFont("Helvetica", 9)
    c.drawString(content_x + 8 * mm, content_y + content_h - 20 * mm,
                 "Background: RGB (0,0,0) = 400% TIC if converted naively to CMYK")

    img_path = make_low_res_image()
    try:
        img_x = content_x + 10 * mm
        img_y = content_y + content_h - 110 * mm
        img_w = content_w - 20 * mm
        img_h = 80 * mm
        c.drawImage(img_path, img_x, img_y, width=img_w, height=img_h)

        c.setFillColor(Color(1, 1, 1, alpha=0.7))
        c.setFont("Helvetica-Bold", 10)
        c.drawString(img_x + 5 * mm, img_y + img_h - 10 * mm,
                     "72 DPI image stretched to 150mm wide (should trigger AI upscale)")
    finally:
        os.unlink(img_path)

    c.setFillColor(Color(0, 0, 0))
    c.setFont("Helvetica-Bold", 8)

    text_x = content_x + 10 * mm
    text_y = img_y - 15 * mm
    lines = [
        "STRESS TEST: This 8pt text is set in RGB (0,0,0) — 400% TIC rich black.",
        "On a litho press this causes registration halos. Engine must fix to 100% K overprint.",
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!@#$%^&*()",
        "Fine serifs and hairlines at 8pt stress-test the sharpening pipeline.",
        "Expected fix: C=0% M=0% Y=0% K=100% with Overprint ON for all fine text.",
    ]
    for i, line in enumerate(lines):
        c.drawString(text_x, text_y - (i * 11), line)

    c.saveState()
    c.setFillColor(Color(1, 0.92, 0, alpha=0.45))
    lens_x = content_x + content_w * 0.7
    lens_y = content_y + content_h * 0.45
    lens_r = 35 * mm
    c.circle(lens_x, lens_y, lens_r, fill=1, stroke=0)

    c.setFillColor(Color(0, 0, 0, alpha=0.6))
    c.setFont("Helvetica-Bold", 8)
    c.drawCentredString(lens_x, lens_y - 3, "TRANSPARENT LENS")
    c.drawCentredString(lens_x, lens_y - 12, "(needs flattening)")
    c.restoreState()

    c.setFillColor(Color(0.5, 0.5, 0.5))
    c.setFont("Helvetica", 6)
    c.drawString(MARGIN + 2 * mm, MARGIN - 8 * mm,
                 "make_test.py | 15mm margins | 72 DPI image | RGB(0,0,0) = 400% TIC | 8pt rich black text | Transparent yellow lens")

    c.save()
    file_size = os.path.getsize(OUTPUT_FILE)
    print(f"Created: {OUTPUT_FILE} ({file_size:,} bytes)")
    print(f"Page size: {PAGE_W/mm:.0f} x {PAGE_H/mm:.0f} mm (A4)")
    print(f"Margins: 15mm on all sides")
    print("Stress elements:")
    print("  1. 15mm white margins on all sides (false margins for stripping)")
    print("  2. RGB (0,0,0) background = 400% TIC (needs clamping to 200% TIC)")
    print("  3. 72 DPI image stretched to 150mm (needs AI upscale to 300 DPI)")
    print("  4. 8pt rich black text (needs K-only neutralization + overprint)")
    print("  5. Transparent yellow circle lens (needs 600 DPI supersampled flattening)")


if __name__ == "__main__":
    main()
