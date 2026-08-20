#!/usr/bin/env python3
"""
Optional Text Clear-up (OCR → human approve/edit → sharp overlay).

Additive only: does nothing unless explicitly invoked via CLI/API.
Does not alter bleed, compile, or other enhancement pipelines.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request

from fai_temp_utils import init_fai_temp_dir

FAI_TEMP_DIR = init_fai_temp_dir()
if not os.path.isdir(FAI_TEMP_DIR):
    try:
        os.makedirs(FAI_TEMP_DIR, exist_ok=True)
    except Exception:
        FAI_TEMP_DIR = tempfile.gettempdir()

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_TIMEOUT_S = 60
MAX_VISION_EDGE = 1600
# Soft-text Laplacian threshold on candidate text patches (lower = blurrier)
_BLUR_VAR_THRESHOLD = 85.0
_MIN_TEXT_PATCHES = 3


def _get_gemini_key() -> str:
    return os.environ.get("GEMINI_API_KEY", "").strip()


def _load_bgr(path: str):
    import cv2

    img = cv2.imread(path, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"Could not read image: {path}")
    return img


def _resize_for_vision(bgr, max_edge: int = MAX_VISION_EDGE):
    import cv2

    h, w = bgr.shape[:2]
    edge = max(h, w)
    if edge <= max_edge:
        return bgr, 1.0
    scale = max_edge / float(edge)
    nw = max(1, int(round(w * scale)))
    nh = max(1, int(round(h * scale)))
    return cv2.resize(bgr, (nw, nh), interpolation=cv2.INTER_AREA), scale


def _bgr_to_jpeg_b64(bgr, quality: int = 85) -> tuple[str, str]:
    import cv2

    ok, buf = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    if not ok:
        raise ValueError("Failed to encode vision JPEG")
    return "image/jpeg", base64.b64encode(buf.tobytes()).decode("ascii")


def _call_gemini_json(bgr, prompt: str, max_output_tokens: int = 4096) -> tuple:
    key = _get_gemini_key()
    if not key:
        return None, "GEMINI_API_KEY not configured"

    vision_bgr, _ = _resize_for_vision(bgr)
    mime, b64 = _bgr_to_jpeg_b64(vision_bgr)
    url = f"{GEMINI_API_URL}/models/gemini-2.0-flash:generateContent?key={key}"
    payload = json.dumps(
        {
            "contents": [
                {
                    "parts": [
                        {"text": prompt},
                        {"inline_data": {"mime_type": mime, "data": b64}},
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.0,
                "maxOutputTokens": int(max_output_tokens),
                "responseMimeType": "application/json",
            },
        }
    ).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=GEMINI_TIMEOUT_S) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:240]
        return None, f"Gemini API error {e.code}: {body}"
    except Exception as e:
        msg = str(e)
        if "timed out" in msg.lower() or "timeout" in msg.lower():
            return None, "AI service is busy, please try again"
        return None, f"Gemini request failed: {msg[:200]}"

    try:
        text_out = raw["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text_out), None
    except Exception as e:
        try:
            text_out = raw["candidates"][0]["content"]["parts"][0]["text"]
            return {"raw_text": text_out}, None
        except Exception:
            return None, f"Could not parse Gemini response: {str(e)[:200]}"


def detect_blurry_text(input_path: str) -> dict:
    """
    Decide whether optional Text Clear-up should be offered.
    Prefer Gemini when configured; always compute a local OpenCV heuristic too.
    """
    import cv2
    import numpy as np

    t0 = time.time()
    try:
        bgr = _load_bgr(input_path)
    except Exception as e:
        return {
            "success": False,
            "blurry_text": False,
            "offer_clearup": False,
            "message": str(e)[:200],
        }

    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    # Candidate text patches: high local contrast via adaptive threshold density
    thr = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 11
    )
    thr = cv2.morphologyEx(thr, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    n_labels, labels, stats, _ = cv2.connectedComponentsWithStats(thr, connectivity=8)
    h, w = gray.shape[:2]
    blur_scores = []
    for i in range(1, n_labels):
        x, y, bw, bh, area = stats[i]
        if area < 80 or bw < 12 or bh < 8:
            continue
        if bw > w * 0.9 or bh > h * 0.35:
            continue
        aspect = bw / float(max(bh, 1))
        if aspect < 0.8 or aspect > 40:
            continue
        pad = 2
        x0, y0 = max(0, x - pad), max(0, y - pad)
        x1, y1 = min(w, x + bw + pad), min(h, y + bh + pad)
        patch = gray[y0:y1, x0:x1]
        if patch.size < 40:
            continue
        blur_scores.append(float(cv2.Laplacian(patch, cv2.CV_64F).var()))

    soft_patches = sum(1 for s in blur_scores if s < _BLUR_VAR_THRESHOLD)
    local_blurry = soft_patches >= _MIN_TEXT_PATCHES and (
        soft_patches / max(len(blur_scores), 1) >= 0.35
    )

    gemini_blurry = None
    gemini_reason = None
    gemini_err = None
    if _get_gemini_key():
        parsed, gemini_err = _call_gemini_json(
            bgr,
            (
                "You are a prepress inspector. Decide if this print artwork has soft/blurry/"
                "AI-generated or heavily compressed TEXT that would look unsharp when printed "
                "at litho quality. Ignore photos that are intentionally soft. Focus on body "
                "copy, prices, logos with letters, and small type.\n"
                'Return JSON only: {"blurry_text": true|false, "confidence": 0-1, '
                '"reason": "short"}'
            ),
            max_output_tokens=256,
        )
        if parsed and isinstance(parsed, dict):
            gemini_blurry = bool(parsed.get("blurry_text"))
            gemini_reason = str(parsed.get("reason") or "")[:240]

    # Offer when either signal says soft text (prefer Gemini when present)
    if gemini_blurry is not None:
        offer = bool(gemini_blurry)
        reason = gemini_reason or ("Soft/AI text detected" if offer else "Text appears adequately sharp")
    else:
        offer = bool(local_blurry)
        reason = (
            f"Local soft-text patches: {soft_patches}/{len(blur_scores)}"
            if offer
            else "No strong soft-text signal"
        )
        if gemini_err:
            reason = f"{reason} (Gemini unavailable: {gemini_err[:80]})"

    elapsed = round(time.time() - t0, 2)
    return {
        "success": True,
        "blurry_text": offer,
        "offer_clearup": offer,
        "confidence": 0.75 if gemini_blurry is not None else 0.55,
        "reason": reason,
        "local_soft_patches": soft_patches,
        "local_patch_count": len(blur_scores),
        "gemini_used": gemini_blurry is not None,
        "elapsed_s": elapsed,
        "message": reason,
    }


def ocr_text_blocks(input_path: str) -> dict:
    """OCR text blocks with normalized bboxes. Exact transcription — no grammar fixes."""
    t0 = time.time()
    try:
        bgr = _load_bgr(input_path)
    except Exception as e:
        return {"success": False, "blocks": [], "message": str(e)[:200]}

    if not _get_gemini_key():
        return {
            "success": False,
            "blocks": [],
            "message": "GEMINI_API_KEY required for Text Clear-up OCR",
        }

    prompt = (
        "OCR this print flyer/artwork. Extract EVERY readable text block.\n"
        "CRITICAL RULES:\n"
        "- Transcribe EXACTLY what you see (same spelling, numbers, punctuation).\n"
        "- Do NOT fix grammar or spelling. Do NOT invent missing letters.\n"
        "- If unsure, use your best literal reading.\n"
        "- Skip pure decorative shapes with no letters.\n"
        "For each block return:\n"
        '  id (string), text (string), bbox [x,y,w,h] as fractions 0-1 of full image '
        "(x,y = top-left), "
        'color_hex approximate text color like "#111111", '
        'align "left"|"center"|"right", '
        'bold true|false\n'
        'Return JSON: {"blocks":[...]}'
    )
    parsed, err = _call_gemini_json(bgr, prompt, max_output_tokens=8192)
    if err:
        return {"success": False, "blocks": [], "message": err}

    raw_blocks = []
    if isinstance(parsed, dict):
        raw_blocks = parsed.get("blocks") or []
        if not raw_blocks and parsed.get("raw_text"):
            return {
                "success": False,
                "blocks": [],
                "message": "OCR returned unparsable text — try again",
            }

    blocks = []
    for i, b in enumerate(raw_blocks):
        if not isinstance(b, dict):
            continue
        text = str(b.get("text") or "").strip()
        if not text:
            continue
        bbox = b.get("bbox") or b.get("box") or [0, 0, 0, 0]
        try:
            x, y, bw, bh = [float(v) for v in bbox[:4]]
        except Exception:
            continue
        # clamp
        x = min(max(x, 0.0), 0.99)
        y = min(max(y, 0.0), 0.99)
        bw = min(max(bw, 0.01), 1.0 - x)
        bh = min(max(bh, 0.008), 1.0 - y)
        color = str(b.get("color_hex") or "#111111")
        if not color.startswith("#"):
            color = "#111111"
        align = str(b.get("align") or "left").lower()
        if align not in ("left", "center", "right"):
            align = "left"
        blocks.append(
            {
                "id": str(b.get("id") or f"b{i+1}"),
                "text": text,
                "bbox": [round(x, 4), round(y, 4), round(bw, 4), round(bh, 4)],
                "color_hex": color[:7],
                "align": align,
                "bold": bool(b.get("bold", False)),
                "include": True,
            }
        )

    elapsed = round(time.time() - t0, 2)
    return {
        "success": True,
        "blocks": blocks,
        "block_count": len(blocks),
        "elapsed_s": elapsed,
        "message": f"OCR found {len(blocks)} text block(s). Edit any errors before applying.",
    }


def _parse_hex(color_hex: str) -> tuple[int, int, int]:
    c = (color_hex or "#111111").lstrip("#")
    if len(c) != 6:
        return (17, 17, 17)
    try:
        return tuple(int(c[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore
    except Exception:
        return (17, 17, 17)


def _fill_bg_color(rgb, x0, y0, x1, y1) -> tuple[int, int, int]:
    import numpy as np

    h, w = rgb.shape[:2]
    # sample a ring just outside the box
    pad = 3
    xa, ya = max(0, x0 - pad), max(0, y0 - pad)
    xb, yb = min(w, x1 + pad), min(h, y1 + pad)
    ring = []
    for yy in range(ya, yb):
        for xx in range(xa, xb):
            if x0 <= xx < x1 and y0 <= yy < y1:
                continue
            ring.append(rgb[yy, xx])
    if not ring:
        return (255, 255, 255)
    med = np.median(np.asarray(ring), axis=0)
    return (int(med[0]), int(med[1]), int(med[2]))


def apply_text_overlay(input_path: str, blocks: list) -> dict:
    """
    Cover included text boxes with local background and draw sharp PIL text.
    Uses ONLY the provided block text strings (human-approved).
    """
    from PIL import Image, ImageDraw, ImageFont
    import cv2
    import numpy as np

    t0 = time.time()
    try:
        bgr = _load_bgr(input_path)
    except Exception as e:
        return {"success": False, "message": str(e)[:200]}

    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    pil = Image.fromarray(rgb)
    draw = ImageDraw.Draw(pil)
    H, W = rgb.shape[:2]

    font_candidates = [
        r"C:\Windows\Fonts\arialbd.ttf",
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\calibri.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]

    def load_font(size: int, bold: bool):
        paths = font_candidates if bold else list(reversed(font_candidates[:2])) + font_candidates[2:]
        for p in paths:
            if os.path.isfile(p):
                try:
                    return ImageFont.truetype(p, size=max(8, size))
                except Exception:
                    continue
        return ImageFont.load_default()

    applied = 0
    for b in blocks or []:
        if not isinstance(b, dict):
            continue
        if b.get("include") is False:
            continue
        text = str(b.get("text") or "")
        if not text.strip():
            continue
        bbox = b.get("bbox") or [0, 0, 0, 0]
        try:
            fx, fy, fw, fh = [float(v) for v in bbox[:4]]
        except Exception:
            continue
        x0 = int(round(fx * W))
        y0 = int(round(fy * H))
        x1 = int(round((fx + fw) * W))
        y1 = int(round((fy + fh) * H))
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(W, max(x0 + 2, x1)), min(H, max(y0 + 2, y1))
        box_w, box_h = x1 - x0, y1 - y0
        if box_w < 4 or box_h < 4:
            continue

        bg = _fill_bg_color(np.asarray(pil), x0, y0, x1, y1)
        draw.rectangle([x0, y0, x1, y1], fill=bg)

        color = _parse_hex(str(b.get("color_hex") or "#111111"))
        bold = bool(b.get("bold", False))
        align = str(b.get("align") or "left").lower()

        # Fit font size to box height, then shrink to width
        font_size = max(10, int(box_h * 0.78))
        font = load_font(font_size, bold)
        for _ in range(24):
            tb = draw.textbbox((0, 0), text, font=font)
            tw, th = tb[2] - tb[0], tb[3] - tb[1]
            if tw <= box_w * 0.98 and th <= box_h * 0.95:
                break
            font_size = max(8, font_size - 1)
            font = load_font(font_size, bold)

        tb = draw.textbbox((0, 0), text, font=font)
        tw, th = tb[2] - tb[0], tb[3] - tb[1]
        if align == "center":
            tx = x0 + max(0, (box_w - tw) // 2)
        elif align == "right":
            tx = x0 + max(0, box_w - tw)
        else:
            tx = x0 + 1
        ty = y0 + max(0, (box_h - th) // 2)
        draw.text((tx, ty), text, fill=color, font=font)
        applied += 1

    out_rgb = np.asarray(pil)
    out_bgr = cv2.cvtColor(out_rgb, cv2.COLOR_RGB2BGR)
    base = os.path.splitext(os.path.basename(input_path))[0]
    out_path = os.path.join(FAI_TEMP_DIR, f"{base}_text_clearup.png")
    cv2.imwrite(out_path, out_bgr)
    elapsed = round(time.time() - t0, 2)
    return {
        "success": True,
        "enhanced_path": out_path,
        "blocks_applied": applied,
        "elapsed_s": elapsed,
        "message": f"Applied sharp overlay to {applied} text block(s).",
        "original_preserved": True,
    }


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(json.dumps({"error": "Usage: text_clearup.py <detect|ocr|apply> <input_path> [blocks_json]"}))
        sys.exit(1)
    action = sys.argv[1]
    input_path = sys.argv[2]
    if not os.path.exists(input_path):
        print(json.dumps({"error": f"Input file not found: {input_path}"}))
        sys.exit(1)
    if action == "detect":
        result = detect_blurry_text(input_path)
    elif action == "ocr":
        result = ocr_text_blocks(input_path)
    elif action == "apply":
        blocks = json.loads(sys.argv[3]) if len(sys.argv) > 3 else []
        if isinstance(blocks, dict):
            blocks = blocks.get("blocks") or []
        result = apply_text_overlay(input_path, blocks)
    else:
        result = {"error": f"Unknown action: {action}"}
        sys.exit(1)
    print(json.dumps(result))
