#!/usr/bin/env python3
import argparse
import sys
import os
import cv2
import numpy as np
from PIL import Image as PILImage, ImageDraw as PILImageDraw

BLEED_METHOD_LABELS = {
    "bgExtract": "Background Extract",
    "stretch": "Pixel-Drift Stretch",
    "mirror": "Mirror + Cross-Fade",
    "replicate": "Edge Replication",
    "upscale": "Upscale",
}

def render_pdf_page_to_image(pdf_path: str, max_dim: int = 1200) -> PILImage.Image:
    try:
        import fitz
        doc = fitz.open(pdf_path)
        page = doc[0]
        rect = page.rect
        scale = min(max_dim / rect.width, max_dim / rect.height, 2.0)
        mat = fitz.Matrix(scale, scale)
        pix = page.get_pixmap(matrix=mat, alpha=True)
        white_bg = PILImage.new("RGBA", (pix.width, pix.height), (255, 255, 255, 255))
        fg = PILImage.frombytes("RGBA", (pix.width, pix.height), pix.samples)
        white_bg.paste(fg, mask=fg.split()[3])
        img = white_bg.convert("RGB")
        doc.close()
        return img
    except Exception:
        return None

def _detect_is_pdf(file_path: str) -> bool:
    try:
        with open(file_path, "rb") as f:
            header = f.read(5)
        return header[:4] == b"%PDF" or header[:5] == b"%PDF-"
    except Exception:
        return False

def load_image(file_path: str, max_dim: int = 1200) -> PILImage.Image:
    if not file_path or not os.path.exists(file_path):
        return None
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".pdf" or (not ext and _detect_is_pdf(file_path)):
        return render_pdf_page_to_image(file_path, max_dim)
    try:
        img = PILImage.open(file_path).convert("RGB")
        w, h = img.size
        if max(w, h) > max_dim:
            scale = max_dim / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), PILImage.LANCZOS)
        return img
    except Exception:
        if _detect_is_pdf(file_path):
            return render_pdf_page_to_image(file_path, max_dim)
        return None

def generate_comparison(original_path: str, variant_path: str, output_path: str, method: str):
    orig_img = load_image(original_path)
    variant_img = load_image(variant_path)

    if orig_img is None and variant_img is None:
        sys.stderr.write("[FAI] Both original and variant images failed to load\n")
        sys.exit(1)

    if orig_img is None:
        orig_img = PILImage.new("RGB", variant_img.size, (40, 40, 40))
    if variant_img is None:
        variant_img = PILImage.new("RGB", orig_img.size, (40, 40, 40))

    max_h = max(orig_img.height, variant_img.height)
    target_h = min(max_h, 1200)
    
    def resize_to_height(img, target_h):
        w, h = img.size
        if h != target_h:
            scale = target_h / h
            img = img.resize((int(w * scale), int(h * scale)), PILImage.LANCZOS)
        return img

    orig_img = resize_to_height(orig_img, target_h)
    variant_img = resize_to_height(variant_img, target_h)

    orig_arr = np.array(orig_img)
    corr_arr = np.array(variant_img)

    cv2.putText(orig_arr, "UPLOADED (ORIGINAL)", (20, 50),
                cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 2, cv2.LINE_AA)

    label = BLEED_METHOD_LABELS.get(method, method.upper())
    cv2.putText(corr_arr, f"FIXED ({label.upper()})", (20, 50),
                cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2, cv2.LINE_AA)

    orig_labeled = PILImage.fromarray(orig_arr)
    corr_labeled = PILImage.fromarray(corr_arr)

    strip_w = orig_labeled.width + corr_labeled.width + 40
    strip_h = target_h + 20
    strip = PILImage.new("RGB", (strip_w, strip_h), (20, 20, 20))
    strip.paste(orig_labeled, (10, 10))
    strip.paste(corr_labeled, (orig_labeled.width + 30, 10))

    strip.save(output_path, quality=95)
    sys.stderr.write(f"[FAI] Dynamic comparison saved: {output_path} (method={method})\n")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--original", required=True)
    parser.add_argument("--variant", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--method", default="auto")
    args = parser.parse_args()
    generate_comparison(args.original, args.variant, args.output, args.method)

if __name__ == "__main__":
    main()
