#!/usr/bin/env python3
"""Checks for hidden bleed recovery (hidden_bleed.py). Run: python3 server/hidden_bleed_check.py"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pymupdf as fitz

import hidden_bleed as hb

MM = 72.0 / 25.4
A5 = (148 * MM, 210 * MM)


def fail(message: str) -> None:
    raise SystemExit(f"FAIL {message}")


def check(name: str, ok: bool, detail: str = "") -> None:
    if not ok:
        fail(f"{name} — {detail}")
    print(f"PASS {name}")


def _pdf(path: str, content: str, size=A5) -> str:
    """One A5 page with a raw content stream in PDF coordinates (origin bottom-left)."""
    doc = fitz.open()
    page = doc.new_page(width=size[0], height=size[1])
    page.insert_text((60, 120), "Flyerz hidden bleed", fontsize=18, fontname="helv")
    xref = doc.get_new_xref()
    doc.update_object(xref, "<<>>")
    doc.update_stream(xref, content.encode("latin-1"))
    contents = page.get_contents()
    arr = " ".join(f"{x} 0 R" for x in [xref] + contents)
    doc.xref_set_key(page.xref, "Contents", f"[{arr}]")
    doc.save(path)
    doc.close()
    return path


def _bg(left=6, right=6, top=6, bottom=6) -> str:
    w, h = A5
    x0, y0 = -left * MM, -bottom * MM
    x1, y1 = w + right * MM, h + top * MM
    return (f"q 0.05 0.55 0.85 rg {x0:.3f} {y0:.3f} {x1 - x0:.3f} {y1 - y0:.3f} re f "
            f"0.95 0.7 0.1 rg {x0:.3f} {y0:.3f} {x1 - x0:.3f} {(y1 - y0) / 3:.3f} re f Q ")


def make_samples(folder: str) -> dict:
    os.makedirs(folder, exist_ok=True)
    w, h = A5
    s = {}
    # (a) CorelDRAW-style: background runs 6 mm past every edge, cut off by the MediaBox.
    s["a"] = _pdf(os.path.join(folder, "a_corel_hidden_6mm.pdf"),
                  _bg() + f"q 1 1 1 rg {w/2:.2f} {h/2:.2f} m {w/2+60:.2f} {h/2:.2f} l {w/2:.2f} {h/2+60:.2f} l f Q")
    # (b) no hidden bleed: background stops at the page.
    s["b"] = _pdf(os.path.join(folder, "b_no_hidden_bleed.pdf"), _bg(0, 0, 0, 0))
    # (c) clipped exactly at the page edge (artwork exists but a clip path hides it).
    s["c"] = _pdf(os.path.join(folder, "c_clipped_at_edge.pdf"),
                  f"q 0 0 {w:.3f} {h:.3f} re W n " + _bg() + "Q")
    # (d) hidden bleed on two sides only.
    s["d"] = _pdf(os.path.join(folder, "d_two_sides.pdf"), _bg(6, 6, 0, 0))
    # (e) pure vector art: paths, a stroked hairline and a curve; no images at all.
    s["e"] = _pdf(os.path.join(folder, "e_vector_only.pdf"),
                  _bg(7, 7, 7, 7) + "q 0 0 0 RG 0.6 w 20 20 m 200 300 l S "
                  f"0.2 0.2 0.2 rg 100 400 m 150 520 250 520 300 400 c f Q")
    return s


def main() -> None:
    folder = os.environ.get("HIDDEN_BLEED_SAMPLES") or tempfile.mkdtemp(prefix="hidden-bleed-")
    s = make_samples(folder)

    a = hb.detect_hidden_bleed(s["a"])
    check("a-found", a["found"] and a["recoverable"], str(a))
    check("a-per-side-6mm", all(5.8 <= v <= 6.2 for v in a["perSideMm"].values()), str(a["perSideMm"]))

    b = hb.detect_hidden_bleed(s["b"])
    check("b-none", not b["found"] and not b["recoverable"], str(b))

    c = hb.detect_hidden_bleed(s["c"])
    check("c-clipped-not-recoverable", not c["recoverable"] and "clipped" in c["reason"], str(c))

    d = hb.detect_hidden_bleed(s["d"])
    check("d-two-sides-found-not-recoverable", d["found"] and not d["recoverable"], str(d))
    check("d-sides", d["perSideMm"]["left"] >= 5.8 and d["perSideMm"]["top"] == 0, str(d["perSideMm"]))

    # Recovery: boxes, no re-render.
    out_dir = tempfile.mkdtemp(prefix="hidden-bleed-out-")
    for key in ("a", "e"):
        out = os.path.join(out_dir, f"{key}_rec.pdf")
        rec = hb.recover_hidden_bleed(s[key], out)
        check(f"{key}-recovered", rec["recovered"], str(rec))
        doc = fitz.open(out)
        page = doc[0]
        m, t = page.mediabox, page.trimbox
        check(f"{key}-media-trim+10", abs(m.width / MM - 158) < 0.05 and abs(m.height / MM - 220) < 0.05,
              f"{m.width / MM:.2f}x{m.height / MM:.2f}")
        check(f"{key}-trim-original", abs(t.width / MM - 148) < 0.05 and abs(t.height / MM - 210) < 0.05,
              f"{t.width / MM:.2f}x{t.height / MM:.2f}")
        check(f"{key}-bleedbox", abs(page.bleedbox.width - m.width) < 0.01, str(page.bleedbox))
        check(f"{key}-no-images", len(page.get_images()) == 0, str(page.get_images()))
        check(f"{key}-vectors-kept", len(page.get_drawings()) >= 2, str(len(page.get_drawings())))
        doc.close()
        # The press pipeline's existing-bleed detector sees exactly 5 mm (so it keeps, not doubles).
        from compile_press_pdf import page_existing_bleed
        doc = fitz.open(out)
        eb = page_existing_bleed(doc[0])
        doc.close()
        check(f"{key}-existing-bleed-5mm", eb is not None and all(abs(eb[x] - 5) < 0.05 for x in hb.SIDES), str(eb))
        again = hb.detect_hidden_bleed(out)
        check(f"{key}-not-detected-twice", not again["recoverable"], str(again))

    for key in ("b", "c", "d"):
        rec = hb.recover_hidden_bleed(s[key], os.path.join(out_dir, f"{key}_rec.pdf"))
        check(f"{key}-not-recovered", not rec["recovered"], str(rec))

    # Feature flag: off by default => the pipeline helper hands back the original file untouched.
    os.environ.pop("FLYERZ_HIDDEN_BLEED", None)
    path, rep = hb.maybe_recover_for_pipeline(s["a"], out_dir)
    check("flag-off-noop", path == s["a"] and not rep["recovered"], str(rep))
    check("flag-off-no-finding", hb.quick_check_finding(s["a"]) is None)
    os.environ["FLYERZ_HIDDEN_BLEED"] = "1"
    try:
        path, rep = hb.maybe_recover_for_pipeline(s["a"], out_dir, "ignore")
        check("flag-on-ignore", path == s["a"], str(rep))
        path, rep = hb.maybe_recover_for_pipeline(s["a"], out_dir, "use")
        check("flag-on-use", path != s["a"] and rep["recovered"], str(rep))
        path, rep = hb.maybe_recover_for_pipeline(s["c"], out_dir, "use")
        check("flag-on-fallback", path == s["c"] and not rep["recovered"], str(rep))
        path, rep = hb.maybe_recover_for_pipeline(os.path.join(out_dir, "missing.pdf"), out_dir, "use")
        check("flag-on-error-fallback", not rep["recovered"], str(rep))
        f = hb.quick_check_finding(s["a"])
        check("finding-friendly", f["recoverable"] and f["message"].startswith("Good news"), str(f.get("message")))
        check("finding-previews", f["previews"]["use"].startswith("data:image/png") and "ignore" in f["previews"])
    finally:
        os.environ.pop("FLYERZ_HIDDEN_BLEED", None)
    print("ALL HIDDEN BLEED CHECKS PASSED")


if __name__ == "__main__":
    main()
