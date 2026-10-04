#!/usr/bin/env python3
"""The 25-point list reports only checks that ran."""

from __future__ import annotations

import os
import tempfile

from client_file_audit_check import _press_problems
from twenty_five import POINTS, assess

FAILURES = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("PASS " if ok else "FAIL ") + name + (f" {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def main() -> None:
    folder = tempfile.mkdtemp(prefix="twenty-")
    path = os.path.join(folder, "bad.pdf")
    _press_problems(path)
    report = assess(path, 148, 210)
    rows = report["checks"]
    check("count", len(rows) == 25 and len(POINTS) == 25, str(len(rows)))
    check("order", [row["num"] for row in rows] == [num for num, _name in POINTS], str([row["num"] for row in rows]))
    by_num = {row["num"]: row for row in rows}
    fonts = by_num["4"]
    check("font-failed", fonts["status"] == "failed" and fonts["pass"] is False and "Helvetica" in fonts["detail"], fonts["detail"])
    hair = by_num["2h"]
    check("hair-honest", hair["status"] == "warning" and "not thickened" in hair["detail"], hair["detail"])
    black = by_num["2b"]
    check("black-warning", black["status"] == "warning" and "registration" in black["detail"], black["detail"])
    spots = by_num["16"]
    check("spots-named", "Pantone-123" in spots["detail"] and spots["pass"] is False, spots["detail"])
    resolution = by_num["3"]
    check("dpi-ran", resolution["status"] in ("passed", "warning", "failed"), resolution["detail"])
    for row in rows:
        if row["pass"]:
            check(f"claimed-{row['num']}", row["status"] in ("passed", "auto") and "did not run" not in row["detail"].lower(), row["detail"])
        if row["status"] == "skipped":
            check(f"skip-{row['num']}", row["pass"] is False, row["detail"])
    missing = assess(os.path.join(folder, "gone.pdf"))
    check("missing-file", len(missing["checks"]) == 25 and all(row["status"] == "skipped" and row["pass"] is False for row in missing["checks"]))

    from twenty_five import light_from_checks, run_checklist

    red = light_from_checks([
        {"num": "4", "name": "Font Embedding", "status": "failed", "detail": "Helvetica is not embedded."},
        {"num": "3", "name": "Resolution / DPI Validation", "status": "warning", "detail": "About 150 ppi."},
    ])
    check("light-red", red["light"] == "red" and "Helvetica" in red["clientMessage"] and "150" not in red["clientMessage"], red["clientMessage"])
    amber = light_from_checks([
        {"num": "3", "name": "Resolution / DPI Validation", "status": "warning", "detail": "About 150 ppi."},
        {"num": "1", "name": "Bleed Detection & Correction", "status": "auto", "detail": "Bleed was added."},
    ])
    check("light-amber", amber["light"] == "amber" and amber["clientMessage"] == "", str(amber))
    green = light_from_checks([
        {"num": "1", "name": "Bleed Detection & Correction", "status": "passed", "detail": "Bleed is 5 mm."},
        {"num": "2h", "name": "Hairline Stroke Enforcement", "status": "auto", "detail": "Raised to 0.3 pt."},
    ])
    check("light-green", green["light"] == "green" and green["clientMessage"] == "", str(green))

    fixed = run_checklist(path, 148, 210, apply=True)
    fixed_rows = {row["num"]: row for row in fixed["checks"]}
    check("hair-auto", fixed_rows["2h"]["status"] == "auto" and "0.3" in fixed_rows["2h"]["detail"], fixed_rows["2h"]["detail"])
    check("font-still-failed", fixed_rows["4"]["status"] == "failed" and "Helvetica" in fixed_rows["4"]["detail"], fixed_rows["4"]["detail"])
    check("count-after-fix", len(fixed["checks"]) == 25, str(len(fixed["checks"])))
    import pikepdf
    fixed_pdf = fixed.get("fixedPath") or ""
    check("fixed-file", bool(fixed_pdf) and os.path.isfile(fixed_pdf), fixed_pdf)
    if fixed_pdf and os.path.isfile(fixed_pdf):
        pdf = pikepdf.open(fixed_pdf)
        stream = b"\n".join(page.get("/Contents").read_bytes() for page in pdf.pages)
        pdf.close()
        text = stream.decode("latin1", "replace")
        check("stroke-0.3", "0.3000" in text or "0.30 " in text, text[:500])

    from ai_upscale import choose_upscale_plan

    low = choose_upscale_plan(80, 80, 210, 297, 0, max_scale=4, skip_below_ppi=75)
    check("under-75-not-upscaled", low.get("skipped_low") is True and low["applied_scale"] == 1, str(low))
    mid = choose_upscale_plan(400, 400, 127, 127, 0, max_scale=4, skip_below_ppi=75)
    check("mid-upscaled", mid.get("skipped_low") is False and 1.5 <= float(mid["applied_scale"]) <= 4.001, str(mid))
    huge = choose_upscale_plan(100, 100, 200, 200, 0, max_scale=4)
    check("cap-4x", float(huge["applied_scale"]) <= 4.001 and float(huge["needed_factor"]) > 4, str(huge))

    from client_file_audit import upscale_soft_images, audit_pdf
    from PIL import Image
    import pymupdf as fitz

    soft_png = os.path.join(folder, "soft.png")
    Image.new("RGB", (80, 80), (20, 40, 200)).save(soft_png)
    low_doc = fitz.open()
    low_page = low_doc.new_page(width=200, height=200)
    low_page.insert_image(fitz.Rect(20, 20, 180, 180), filename=soft_png)
    low_path = os.path.join(folder, "low.pdf")
    low_doc.save(low_path)
    low_doc.close()
    low_up = upscale_soft_images(low_path)
    check("image-under-75", low_up["changed"] == 0 and low_up["skippedLow"] == 1 and audit_pdf(low_path)["resolution"]["worst"] == 36.0, str(low_up))
    mid_png = os.path.join(folder, "mid.png")
    Image.new("RGB", (120, 120), (200, 20, 20)).save(mid_png)
    mid_doc = fitz.open()
    mid_page = mid_doc.new_page(width=144, height=144)
    mid_page.insert_image(fitz.Rect(10, 10, 82, 82), filename=mid_png)
    mid_path = os.path.join(folder, "mid.pdf")
    mid_doc.save(mid_path)
    mid_doc.close()
    mid_up = upscale_soft_images(mid_path)
    mid_worst = audit_pdf(mid_path)["resolution"]["worst"]
    check("image-4x-cap", mid_up["changed"] == 1 and mid_worst is not None and mid_worst >= 299, str(mid_up) + str(mid_worst))

    from designer_assistant import inspect_artwork

    looked = inspect_artwork(path, 148, 210)
    reply = looked["reply"]
    check("rulebook-points", all(f"{num}. {name}" in reply for num, name in POINTS), reply[:500])
    check("rulebook-fallback", looked["provider"] == "rules", looked["provider"])
    check("offers-fixes", any(item["id"] == "fix-black" for item in looked["actions"]), str(looked["actions"]))
    if FAILURES:
        raise SystemExit(f"{len(FAILURES)} failed: {', '.join(FAILURES)}")
    print("twenty five checks passed")


if __name__ == "__main__":
    main()
