#!/usr/bin/env python3
"""Partial-bleed vector type stays live, and RGB black type becomes 100K."""

from __future__ import annotations

import os
import tempfile

from client_file_audit import (
    _process_tokens,
    _tokenize,
    apply_vector_fixes,
    audit_pdf,
    live_text_press_problems,
)
from press_ready_engine import compile_vector_press, press_window_seam

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
MM = 72.0 / 25.4


def fail(message: str) -> None:
    raise SystemExit(f"FAIL {message}")


def check(name: str, ok: bool, detail: str = "") -> None:
    if not ok:
        fail(f"{name} — {detail}")
    print(f"PASS {name}")


def _vector_partial(path: str, bleed_mm: float, word: str) -> None:
    import pymupdf as fitz

    trim_w, trim_h = 148.0, 105.0
    page_w = (trim_w + 2 * bleed_mm) * MM
    page_h = (trim_h + 2 * bleed_mm) * MM
    doc = fitz.open()
    page = doc.new_page(width=page_w, height=page_h)
    page.draw_rect(page.rect, color=None, fill=(0.12, 0.28, 0.62))
    page.insert_font(fontname="DEJAVU", fontfile=FONT)
    page.insert_text((page_w * 0.2, page_h * 0.55), word, fontsize=28, fontname="DEJAVU", color=(1, 1, 1))
    rect = page.rect
    page.set_mediabox(rect)
    page.set_cropbox(rect)
    page.set_trimbox(rect)
    page.set_bleedbox(rect)
    doc.save(path)
    doc.close()


def _press_facts(path: str) -> dict:
    import pymupdf as fitz

    from client_file_audit import _page_covering_image

    doc = fitz.open(path)
    try:
        page = doc[0]
        text = page.get_text("text") or ""
        faces = []
        for font in page.get_fonts(full=True) or []:
            faces.append(str(font[3] if len(font) > 3 else "").lower())
        raw = page.read_contents().decode("latin1", "replace").lower()
        return {
            "text": text,
            "faces": faces,
            "covering": _page_covering_image(path),
            "raw": raw,
            "width": page.rect.width,
            "height": page.rect.height,
        }
    finally:
        doc.close()


def test_uneven_wide_gap_keeps_the_seam() -> None:
    """A shortfall wider than 2.5 mm must not be repainted into the artwork."""
    import pymupdf as fitz

    folder = tempfile.mkdtemp(prefix="vector-uneven-")
    src = os.path.join(folder, "uneven.pdf")
    trim_w, trim_h = 148.0, 105.0
    left, right, top, bottom = 1.0, 4.0, 0.0, 5.0
    page_w = (trim_w + left + right) * MM
    page_h = (trim_h + top + bottom) * MM
    doc = fitz.open()
    page = doc.new_page(width=page_w, height=page_h)
    page.draw_rect(page.rect, color=None, fill=(0.15, 0.35, 0.7))
    page.insert_font(fontname="DEJAVU", fontfile=FONT)
    page.insert_text((page_w * 0.4, page_h * 0.55), "P1", fontsize=11, fontname="DEJAVU", color=(1, 1, 1))
    rect = page.rect
    page.set_mediabox(rect)
    page.set_cropbox(rect)
    page.set_trimbox(fitz.Rect(left * MM, top * MM, rect.width - right * MM, rect.height - bottom * MM))
    page.set_bleedbox(rect)
    doc.save(src)
    doc.close()
    out = os.path.join(folder, "press.pdf")
    result = compile_vector_press(src, out, trim_w, trim_h, 5)
    facts = _press_facts(out) if os.path.exists(out) else {}
    seams = press_window_seam(src, out) if os.path.exists(out) else []
    worst = max((float(row.get("page_max") or 0) for row in seams), default=99)
    check("uneven-used", bool(result.get("used")) and (result.get("report") or {}).get("existingBleed") is True, str(result.get("reason")))
    check("uneven-once", str(facts.get("text") or "").count("P1") == 1, str(facts.get("text")))
    check("uneven-face", "dejavu" in " ".join(facts.get("faces") or []) and "helv" not in " ".join(facts.get("faces") or []), str(facts.get("faces")))
    check("uneven-no-plate", facts.get("covering") is False, str(facts.get("covering")))
    check("uneven-seam", bool(seams) and worst < 5.0, f"{worst:.2f}")


def test_partial_bleed_keeps_live_type() -> None:
    folder = tempfile.mkdtemp(prefix="vector-bleed-")
    for bleed, word in ((2.5, "MARKET"), (3.0, "POSTER")):
        src = os.path.join(folder, f"src-{bleed}.pdf")
        out = os.path.join(folder, f"press-{bleed}.pdf")
        _vector_partial(src, bleed, word)
        result = compile_vector_press(src, out, 148, 105, 5)
        report = result.get("report") or {}
        facts = _press_facts(out) if os.path.exists(out) else {}
        problems = live_text_press_problems(src, out) if os.path.exists(out) else ["missing"]
        seams = press_window_seam(src, out) if os.path.exists(out) else []
        worst = max((float(row.get("page_max") or 0) for row in seams), default=99)
        text = str(facts.get("text") or "")
        faces = " ".join(facts.get("faces") or [])
        label = f"{bleed:g}mm"
        check(f"{label}-used", bool(result.get("used")) and report.get("status") == "ready", str(report.get("status")) + " " + str(report.get("reason")))
        check(f"{label}-once", text.lower().count(word.lower()) == 1, text)
        check(f"{label}-face", "dejavu" in faces and "helv" not in faces and "nimbus" not in faces, faces)
        check(f"{label}-no-plate", facts.get("covering") is False, str(facts.get("covering")))
        check(f"{label}-gate", problems == [], str(problems))
        check(f"{label}-seam", bool(seams) and worst < 5, f"{worst}")
        width = float(facts.get("width") or 0) * 25.4 / 72.0
        height = float(facts.get("height") or 0) * 25.4 / 72.0
        check(f"{label}-size", abs(width - 158) < 1.5 and abs(height - 115) < 1.5, f"{width:.1f}x{height:.1f}")


def test_gate_fails_doubled_or_substituted() -> None:
    import pymupdf as fitz

    folder = tempfile.mkdtemp(prefix="vector-gate-")
    src = os.path.join(folder, "src.pdf")
    _vector_partial(src, 3.0, "MARKET")
    doubled = os.path.join(folder, "doubled.pdf")
    doc = fitz.open()
    page = doc.new_page(width=200, height=200)
    page.insert_font(fontname="DEJAVU", fontfile=FONT)
    page.insert_text((20, 40), "MARKET", fontsize=18, fontname="DEJAVU", color=(0, 0, 0))
    page.insert_text((20, 80), "MARKET", fontsize=18, fontname="DEJAVU", color=(0, 0, 0))
    doc.save(doubled)
    doc.close()
    problems = live_text_press_problems(src, doubled)
    check("gate-doubled", any("repeats" in item for item in problems), str(problems))

    swapped = os.path.join(folder, "helv.pdf")
    doc = fitz.open()
    page = doc.new_page(width=200, height=200)
    page.insert_text((20, 40), "MARKET", fontsize=18, fontname="helv", color=(0, 0, 0))
    doc.save(swapped)
    doc.close()
    problems = live_text_press_problems(src, swapped)
    check("gate-substituted", any("substituted" in item for item in problems), str(problems))

    bare = os.path.join(folder, "bare.pdf")
    doc = fitz.open()
    page = doc.new_page(width=200, height=200)
    page.insert_text((20, 40), "MARKET", fontsize=18, fontname="helv", color=(0, 0, 0))
    doc.save(bare)
    doc.close()
    check("gate-skips-unembedded", live_text_press_problems(bare, swapped) == [], str(live_text_press_problems(bare, swapped)))


def test_rgb_text_becomes_k_only() -> None:
    import pikepdf
    import pymupdf as fitz

    tokens = _tokenize(
        "0 0 0 rg 0 0 400 300 re f\n"
        "0 0 0 rg BT /F1 24 Tf (BLACK) Tj ET\n"
        "0.05 0.04 0.05 rg BT /F1 20 Tf (NEAR) Tj ET\n"
        "0.1 0.2 0.8 rg 10 10 30 30 re f\n"
    )
    rewritten, found = _process_tokens(tokens, True)
    text = " ".join(rewritten or [])
    check("rgb-text-counted", int(found.get("rgbTextBlack") or 0) >= 2 and int(found.get("rgbSmallBlack") or 0) == 0, str(found))
    check("rgb-text-100k", text.count("0.0000 0.0000 0.0000 1.0000 k") >= 2, text)
    check("rgb-large-stays-rich", "0.4 0.3 0.3 1.0000 k" in text or "0.4000 0.3000 0.3000 1.0000 k" in text, text)
    check("rgb-blue-stays", "0.1000 0.2000 0.8000 rg" in text or "0.1 0.2 0.8 rg" in text, text)

    folder = tempfile.mkdtemp(prefix="rgb-k-")
    src = os.path.join(folder, "rgb.pdf")
    doc = fitz.open()
    page = doc.new_page(width=148 * MM, height=105 * MM)
    page.draw_rect(page.rect, color=None, fill=(0.1, 0.25, 0.7))
    page.insert_font(fontname="DEJAVU", fontfile=FONT)
    page.insert_text((40, 80), "BLACK", fontsize=24, fontname="DEJAVU", color=(0, 0, 0))
    page.insert_text((40, 120), "NEAR", fontsize=22, fontname="DEJAVU", color=(0.04, 0.04, 0.05))
    doc.save(src)
    doc.close()
    black = audit_pdf(src)["black"]
    check("rgb-audit", int(black.get("rgbTextBlack") or 0) >= 1 and black.get("needsFix") is False, str(black))
    fixed = os.path.join(folder, "fixed.pdf")
    apply_vector_fixes(src, fixed)
    from client_file_audit import _page_streams

    held = pikepdf.open(fixed)
    raw = b"\n".join(_page_streams(held, held.pages[0]))
    held.close()
    check("rgb-fixed-k", b"0.0000 0.0000 0.0000 1.0000 k" in raw and b"0.4000 0.3000 0.3000 1" not in raw, raw[:500])
    out = os.path.join(folder, "press.pdf")
    result = compile_vector_press(src, out, 148, 105, 5)
    report = result.get("report") or {}
    facts = _press_facts(out) if os.path.exists(out) else {}
    raw_press = str(facts.get("raw") or "")
    k_only = "0 0 0 1 k" in raw_press or "0.0000 0.0000 0.0000 1" in raw_press
    check("rgb-press-k", bool(result.get("used")) and k_only and report.get("status") == "ready", raw_press[:400] + " " + str(report.get("reason")))
    check("rgb-press-once", str(facts.get("text") or "").lower().count("black") == 1, str(facts.get("text")))
    check("rgb-press-face", "dejavu" in " ".join(facts.get("faces") or []), str(facts.get("faces")))


def main() -> None:
    test_gate_fails_doubled_or_substituted()
    test_rgb_text_becomes_k_only()
    test_partial_bleed_keeps_live_type()
    test_uneven_wide_gap_keeps_the_seam()
    print("vector-bleed-text OK")


if __name__ == "__main__":
    main()
