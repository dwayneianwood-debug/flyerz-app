#!/usr/bin/env python3
"""
Background removal for artwork files using OpenCV GrabCut + edge detection.
Produces a transparent PNG suitable for the prepress pipeline.
"""

import sys
import os
import json
import numpy as np
import cv2
from PIL import Image

def remove_background(input_path: str, output_path: str) -> dict:
    img = cv2.imread(input_path, cv2.IMREAD_UNCHANGED)
    if img is None:
        return {"success": False, "error": f"Could not read image: {input_path}"}

    if len(img.shape) == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)

    if len(img.shape) == 3 and img.shape[2] == 4:
        bgr = img[:, :, :3]
    else:
        bgr = img

    h, w = bgr.shape[:2]

    max_dim = 1500
    scale = 1.0
    if max(h, w) > max_dim:
        scale = max_dim / max(h, w)
        small = cv2.resize(bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    else:
        small = bgr.copy()

    sh, sw = small.shape[:2]

    mask = np.zeros((sh, sw), np.uint8)
    margin = max(2, int(min(sh, sw) * 0.02))
    mask[:] = cv2.GC_PR_BGD
    mask[margin:sh-margin, margin:sw-margin] = cv2.GC_PR_FGD

    center_margin_y = int(sh * 0.15)
    center_margin_x = int(sw * 0.15)
    mask[center_margin_y:sh-center_margin_y, center_margin_x:sw-center_margin_x] = cv2.GC_FGD

    bgd_model = np.zeros((1, 65), np.float64)
    fgd_model = np.zeros((1, 65), np.float64)

    rect = (margin, margin, sw - 2*margin, sh - 2*margin)

    try:
        cv2.grabCut(small, mask, rect, bgd_model, fgd_model, 5, cv2.GC_INIT_WITH_MASK)
    except cv2.error as e:
        return {"success": False, "error": f"GrabCut failed: {str(e)}"}

    fg_mask = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_OPEN, kernel, iterations=1)

    fg_mask = cv2.GaussianBlur(fg_mask, (3, 3), 0)

    if scale < 1.0:
        fg_mask = cv2.resize(fg_mask, (w, h), interpolation=cv2.INTER_LINEAR)
        _, fg_mask = cv2.threshold(fg_mask, 127, 255, cv2.THRESH_BINARY)

    b, g, r = cv2.split(bgr)
    result = cv2.merge([b, g, r, fg_mask])

    cv2.imwrite(output_path, result)

    if not os.path.exists(output_path):
        return {"success": False, "error": "Output file was not created"}

    return {
        "success": True,
        "outputPath": output_path,
        "width": w,
        "height": h,
        "fileSize": os.path.getsize(output_path),
    }


def main():
    if len(sys.argv) < 3:
        print(json.dumps({"success": False, "error": "Usage: remove_bg.py <input> <output>"}))
        sys.exit(1)

    input_path = sys.argv[1]
    output_path = sys.argv[2]

    if not os.path.exists(input_path):
        print(json.dumps({"success": False, "error": f"Input file not found: {input_path}"}))
        sys.exit(1)

    result = remove_background(input_path, output_path)
    print(json.dumps(result))
    sys.exit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
