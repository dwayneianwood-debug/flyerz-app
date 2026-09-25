#!/usr/bin/env python3
"""Checks for AI-artwork detection, defaults, fit, text warning, and the health-report note."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

import cv2
import numpy as np
from PIL import Image, ImageDraw, PngImagePlugin

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai_artwork import (  # noqa: E402
    apply_artwork_fit,
    check_text,
    cover_crop_box,
    detect_ai_artwork,
    plan_artwork,
    save_png_300,
    set_expand_fn,
    set_spell_checker,
)
from colour_border import sample_edge_cmyk  # noqa: E402


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("OK  " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""))
    if not ok:
        raise SystemExit(name)


def _png(path: str, bgr: np.ndarray, dpi=None, text: str = "") -> None:
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    image = Image.fromarray(rgb)
    extra = {}
    info = PngImagePlugin.PngInfo()
    if text:
        info.add_text("parameters", text)
        extra["pnginfo"] = info
    if dpi:
        extra["dpi"] = dpi
    image.save(path, format="PNG", **extra)


def test_detection_and_defaults() -> None:
    folder = tempfile.mkdtemp()
    square = np.zeros((1024, 1024, 3), np.uint8)
    square[:, :] = (40, 20, 220)  # vivid red in BGR, flat edge
    cv2.putText(square, "OPENING SOON", (80, 520), cv2.FONT_HERSHEY_SIMPLEX, 2.2, (255, 255, 255), 4)
    square_path = os.path.join(folder, "chatgpt-square.png")
    _png(square_path, square)

    portrait = np.zeros((1536, 1024, 3), np.uint8)
    portrait[:, :] = (180, 40, 20)
    portrait_path = os.path.join(folder, "portrait.png")
    _png(portrait_path, portrait, dpi=(72, 72))

    wide = np.random.default_rng(3).integers(0, 255, (1024, 1792, 3), dtype=np.uint8)
    wide_path = os.path.join(folder, "wide.png")
    _png(wide_path, wide)

    photo = np.random.default_rng(4).integers(0, 255, (3000, 4000, 3), dtype=np.uint8)
    photo_path = os.path.join(folder, "photo.png")
    _png(photo_path, photo, dpi=(72, 72))

    tagged = np.zeros((600, 800, 3), np.uint8)
    tagged[:, :] = (20, 80, 20)
    tagged_path = os.path.join(folder, "tagged.png")
    _png(tagged_path, tagged, dpi=(300, 300), text="Made with Midjourney v6")

    found = detect_ai_artwork(square_path)
    check("detect-1024", found["detected"] and found["no_bleed"], str(found["reasons"]))
    check("detect-1536", detect_ai_artwork(portrait_path)["detected"])
    check("detect-1792", detect_ai_artwork(wide_path)["detected"])
    check("ignore-photo", not detect_ai_artwork(photo_path)["detected"])
    tagged_found = detect_ai_artwork(tagged_path)
    check("detect-metadata", tagged_found["detected"] and any("midjourney" in r for r in tagged_found["reasons"]), str(tagged_found["reasons"]))

    def spell_flag(_path: str) -> dict:
        return {
            "success": True,
            "stub": False,
            "errors_found": [{"word": "SOON", "suggestion": "soon", "language": "en-ZA"}],
        }

    set_spell_checker(spell_flag)
    try:
        plan = plan_artwork(square_path, 148, 210)
    finally:
        set_spell_checker(None)

    check("default-crop", plan["mismatch"] and plan["fit"] == "crop")
    check("flat-bleed", plan["bleed"] == "colourBorder" and plan["edge_flat"], plan["bleed"])
    check("enhance-on", plan["enhance"] and plan["effective_dpi"] < 300, str(plan["effective_dpi"]))
    check("bright", plan["bright"] and "duller" in plan["bright_message"])
    check("text-warn", plan["text_status"] == "warning" and plan["blocked"] is False, plan["text_message"])
    check("note", plan["note"].startswith("AI-generated artwork detected.") and "not stopped" in plan["note"] and "CMYK" in plan["note"])
    edge = sample_edge_cmyk(cv2.imread(square_path))
    # The drawn text does not sit on the 1px ring, so the edge stays the flat red.
    check("edge-1px", abs(plan["edge"]["m"] - edge[1]) < 1 and abs(plan["edge"]["y"] - edge[2]) < 1, str(plan["edge"]))

    photo_edges = plan_artwork(wide_path, 210, 99, {"reuse_text": {"text_status": "clear", "text_message": "Text check found nothing suspicious.", "text_warnings": []}})
    check("photo-bleed", photo_edges["bleed"] == "mirror", photo_edges["bleed"])
    check("dl-mismatch", photo_edges["mismatch"])

    box_mid = cover_crop_box(1024, 1024, 148, 210, 0.5)
    box_side = cover_crop_box(1024, 1024, 148, 210, 0)
    check("crop-moves", box_side["x"] < box_mid["x"] and box_mid["axis"] == "x")
    check("safe-zone", abs(box_mid["safe_y"] - (5 / 210)) < 0.001)


def test_fit_and_text_never_blocks() -> None:
    folder = tempfile.mkdtemp()
    img = np.zeros((1536, 1024, 3), np.uint8)
    img[:, :] = (30, 30, 30)
    img[700:836, 400:624] = (0, 255, 0)
    path = os.path.join(folder, "src.png")
    cv2.imwrite(path, img)

    cropped, _ = apply_artwork_fit(img, 148, 210, "crop", 0.5)
    ratio = cropped.shape[1] / cropped.shape[0]
    check("crop-ratio", abs(ratio - (148 / 210)) < 0.02, f"{cropped.shape[1]}x{cropped.shape[0]}")

    extended, info = apply_artwork_fit(
        img, 210, 99, "extend", source_path=path,
        expand_fn=lambda _p: {"success": True, "stub": True, "enhanced_path": path},
    )
    check("extend-local", info["expand"] == "local" and abs((extended.shape[1] / extended.shape[0]) - (210 / 99)) < 0.02)

    bigger = np.zeros((200, 200, 3), np.uint8)
    bigger[:, :] = (10, 10, 200)
    bigger_path = os.path.join(folder, "big.png")
    cv2.imwrite(bigger_path, bigger)

    def fake_expand(_p: str) -> dict:
        return {"success": True, "stub": False, "enhanced_path": bigger_path}

    grown, info = apply_artwork_fit(img, 210, 99, "extend", source_path=path, expand_fn=fake_expand)
    check("extend-ai", info["expand"] == "ai" and grown.shape[0] == 200)

    bordered, _ = apply_artwork_fit(img, 210, 99, "border", border_cmyk=(0, 0, 0, 100))
    y0 = (bordered.shape[0] - img.shape[0]) // 2
    x0 = (bordered.shape[1] - img.shape[1]) // 2
    kept = tuple(int(v) for v in bordered[y0 + 8, x0 + 8])
    check("border-keeps-artwork", kept == (30, 30, 30), str(kept))
    check("border-edge", tuple(int(v) for v in bordered[0, 0]) == (0, 0, 0))

    def boom(_path: str) -> dict:
        raise RuntimeError("gemini down")

    failed = check_text(path, boom)
    check("text-fail-open", failed["blocked"] is False and failed["text_status"] == "unavailable")

    proof = os.path.join(folder, "proof.png")
    save_png_300(img, proof)
    with Image.open(proof) as saved:
        dpi = saved.info.get("dpi")
    check("preview-300", dpi is not None and round(dpi[0]) == 300 and round(dpi[1]) == 300, str(dpi))


def test_compile_note() -> None:
    folder = tempfile.mkdtemp()
    src = os.path.join(folder, "art.png")
    art = np.full((180, 180, 3), (20, 40, 200), np.uint8)
    cv2.imwrite(src, art)
    dest = os.path.join(folder, "press.pdf")
    status = os.path.join(folder, "status.json")
    result_path = os.path.join(folder, "result.json")
    note = "AI-generated artwork detected. Auto-applied: crop to fit, with the safe zone shown; Mirror bleed; CMYK conversion for litho print. Possible text problem: 'SOON'. The job was not stopped."
    cmd = [
        sys.executable,
        os.path.join(os.path.dirname(__file__), "compile_press_pdf.py"),
        "--input", src,
        "--output", dest,
        "--strategy", "mirror",
        "--color-space", "cmyk",
        "--trim-w", "50",
        "--trim-h", "70",
        "--status-file", status,
        "--result-file", result_path,
        "--base-name", "ai-artwork-check",
        "--ai-artwork-fit", "crop",
        "--ai-artwork-offset", "0.5",
        "--ai-artwork-note", note,
    ]
    proc = subprocess.run(cmd, cwd=os.path.dirname(os.path.dirname(__file__)), capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        check("compile", False, (proc.stderr or proc.stdout or "")[-1500:])
    with open(result_path, encoding="utf-8") as handle:
        payload = json.load(handle)
    resolution = payload["audit_report"]["resolution_and_lenses"]["action_taken"]
    check("report-lists-auto", "AI-generated artwork detected" in resolution and "not stopped" in resolution, resolution[:280])
    check("pdf-written", os.path.exists(dest) and os.path.getsize(dest) > 1000)

    bleed_src = open(os.path.join(os.path.dirname(__file__), "smart_bleed.py"), encoding="utf-8").read()
    check("gs-leash", "BufferSpace=50000000" in bleed_src and "MaxBitmap=50000000" in bleed_src and "NumRenderingThreads=1" in bleed_src)


def main() -> None:
    test_detection_and_defaults()
    test_fit_and_text_never_blocks()
    test_compile_note()
    print("all ai artwork checks passed")


if __name__ == "__main__":
    main()
