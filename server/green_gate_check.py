#!/usr/bin/env python3
"""Checklist and the red client messages. Nothing is sent."""

from __future__ import annotations

import os
import tempfile

from PIL import Image

from green_gate import CUT_MESSAGE, MISSING_MESSAGE, PROPORTION_MESSAGE, assess, client_message
from quick_print import decide_light, make_print_ready


def fail(message: str) -> None:
    raise SystemExit(f"FAIL {message}")


def check(name: str, ok: bool, detail: str = "") -> None:
    if not ok:
        fail(f"{name} — {detail}")
    print(f"PASS {name}")


def _bled(path: str) -> None:
    import pymupdf as fitz

    mm = 72.0 / 25.4
    bleed = 5 * mm
    trim_w, trim_h = 148 * mm, 210 * mm
    doc = fitz.open()
    page = doc.new_page(width=trim_w + 2 * bleed, height=trim_h + 2 * bleed)
    page.draw_rect(page.rect, color=None, fill=(0.75, 0.08, 0.08))
    page.draw_rect(fitz.Rect(bleed, bleed, bleed + trim_w, bleed + trim_h), color=None, fill=(0.1, 0.55, 0.2))
    page.insert_text((bleed + 36, bleed + 80), "ALREADY BLEED", fontsize=28, fontname="helv", color=(1, 1, 1))
    trim = fitz.Rect(bleed, bleed, bleed + trim_w, bleed + trim_h)
    page.set_trimbox(trim)
    page.set_bleedbox(page.rect)
    page.set_cropbox(page.rect)
    doc.save(path)
    doc.close()


def test_messages() -> None:
    check("msg-cut", "cut line" in client_message("cut").lower() and client_message("cut") == CUT_MESSAGE)
    check("msg-missing", "missing" in MISSING_MESSAGE.lower())
    check("msg-shape", "proportions" in PROPORTION_MESSAGE.lower())
    check("msg-low", "300" in client_message("low-res"))
    shape = decide_light({"unextendable": True})
    check("shape-red", shape["light"] == "red" and shape["clientMessage"] == PROPORTION_MESSAGE, str(shape))
    blank = decide_light({"missingContent": True})
    check("missing-red", blank["light"] == "red" and blank["clientMessage"] == MISSING_MESSAGE, str(blank))
    cut = decide_light({
        "compiled": True,
        "enginePassed": True,
        "checklist": {
            "severity": "red",
            "clientMessage": CUT_MESSAGE,
            "items": [{"id": "safe", "label": "safe", "passed": False, "detail": "Text sits on the cut line (HELLO)."}],
        },
    })
    check("cut-red", cut["light"] == "red" and "cut line" in cut["clientMessage"].lower(), str(cut))
    amber = decide_light({
        "compiled": True,
        "enginePassed": True,
        "checklist": {
            "items": [
                {"id": "cmyk", "label": "CMYK", "passed": False, "detail": "The press picture is not CMYK."},
                {"id": "bleed", "label": "bleed", "passed": True, "detail": ""},
            ],
        },
    })
    check("checklist-amber", amber["light"] == "amber" and any("CMYK" in line for line in amber["reasons"]), str(amber))
    green = decide_light({
        "compiled": True,
        "enginePassed": True,
        "checklist": {"items": [{"id": "bleed", "label": "bleed", "passed": True, "detail": ""}]},
    })
    check("checklist-green", green["light"] == "green" and green["clientMessage"] == "", str(green))


def test_bled_pdf_explains_itself() -> None:
    folder = tempfile.mkdtemp(prefix="gate-bled-")
    path = os.path.join(folder, "already.pdf")
    _bled(path)
    report = assess(path, 148, 210, {})
    items = {item["id"]: item for item in report["items"]}
    check("bled-ids", {"bleed", "boxes", "cmyk", "fonts", "safe", "letters"} <= set(items), str(list(items)))
    check("bled-bleed", items["bleed"]["passed"] and items["boxes"]["passed"], str(items["bleed"]) + str(items["boxes"]))
    check("bled-not-cmyk", items["cmyk"]["passed"] is False, str(items["cmyk"]))
    check("bled-font", items["fonts"]["passed"] is False, str(items["fonts"]))
    check("bled-safe", items["safe"]["passed"] is True, str(items["safe"]))
    check("bled-not-red", report["severity"] != "red", str(report["severity"]))


def test_blank_and_wrong_shape() -> None:
    folder = tempfile.mkdtemp(prefix="gate-red-")
    white = os.path.join(folder, "blank.png")
    Image.new("RGB", (800, 1100), (255, 255, 255)).save(white)
    result = make_print_ready(white, os.path.join(folder, "out-blank"), 148, 210, "a5", "A5", filename="blank.png")
    check("blank-red", result["light"] == "red" and "missing" in result["clientMessage"].lower(), result.get("clientMessage"))
    check("blank-not-sent", "send" not in result["clientMessage"].lower() or "please send" in result["clientMessage"].lower())

    import pymupdf as fitz

    wrong = os.path.join(folder, "card.pdf")
    doc = fitz.open()
    page = doc.new_page(width=90 * 72 / 25.4, height=50 * 72 / 25.4)
    page.insert_text((20, 30), "WRONG SHAPE", fontsize=12, fontname="helv")
    doc.save(wrong)
    doc.close()
    shaped = make_print_ready(wrong, os.path.join(folder, "out-shape"), 148, 210, "a5", "A5", filename="card.pdf")
    check("shape-job-red", shaped["light"] == "red" and "proportions" in shaped["clientMessage"].lower(), shaped.get("clientMessage"))
    check("shape-no-auto", shaped.get("clientMessage") and "http" not in shaped["clientMessage"].lower())


def main() -> None:
    test_messages()
    test_bled_pdf_explains_itself()
    test_blank_and_wrong_shape()
    print("GREEN GATE CHECKS PASSED")


if __name__ == "__main__":
    main()
