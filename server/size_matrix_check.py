#!/usr/bin/env python3
"""Every product, both orientations, and the bleed and size cases Ian listed.

A press file is built for every product. A0–A2 keep their vectors and only the
5 mm bleed is drawn as strips, so a full 300 dpi plate is not allocated.
"""

from __future__ import annotations

import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))

MM = 72.0 / 25.4
PRESS_LIMIT_MM = 430.0
ROWS: list[dict] = []
FAILURES: list[str] = []

# Flyers in this app are A5. Posters in the live jobs are A5; A3 is the large poster sheet.
NOTES = {
    "a5": "flyer",
    "a5-landscape": "flyer landscape",
    "a3": "poster",
    "a3-landscape": "poster landscape",
}


def _products() -> list:
    from quick_print import _products

    rows = list(_products())
    rows.append({"id": "custom", "label": "Custom 180 × 120", "widthMm": 180, "heightMm": 120})
    return rows


def _record(product: str, case: str, ok: bool, detail: str) -> None:
    ROWS.append({"product": product, "case": case, "ok": ok, "detail": detail})
    mark = "PASS" if ok else "FAIL"
    print(f"{mark} {product} {case} {detail}", flush=True)
    if not ok:
        FAILURES.append(f"{product}:{case}")


def _write_pdf(path: str, trim_w: float, trim_h: float, kind: str, pages: int = 1) -> None:
    import pymupdf as fitz
    from PIL import Image

    bleed = {"noleed": 0.0, "canva": 2.5, "full5": 5.0, "bleed3": 3.0, "trimbox": 3.0, "raster": 0.0, "text": 0.0, "smalltext": 0.0}.get(kind, 0.0)
    uneven = kind == "uneven"
    if kind.startswith("aspect"):
        # Smaller than every catalog trim, so a skewed page is not another product plus bleed.
        scale = 0.04
        skew = {"aspect2": 1.01, "aspect8": 1.06, "aspect15": 1.15}[kind]
        page_w = trim_w * scale * skew
        page_h = trim_h * scale
        insets = (0.0, 0.0, 0.0, 0.0)
        real_box = False
    elif uneven:
        insets = (1.0, 4.0, 0.0, 5.0)  # left, right, top, bottom mm
        page_w = trim_w + insets[0] + insets[1]
        page_h = trim_h + insets[2] + insets[3]
        real_box = True
    else:
        insets = (bleed, bleed, bleed, bleed)
        page_w = trim_w + 2 * bleed
        page_h = trim_h + 2 * bleed
        real_box = kind == "trimbox"
    doc = fitz.open()
    folder = os.path.dirname(path)
    raster_path = ""
    if kind == "raster":
        raster_path = os.path.join(folder, "only.png")
        Image.new("RGB", (80, 48), (20, 90, 160)).save(raster_path)
    for index in range(pages):
        page = doc.new_page(width=page_w * MM, height=page_h * MM)
        if kind == "smalltext":
            _insert_small_black(page, trim_w, trim_h)
        else:
            page.draw_rect(page.rect, color=None, fill=(0.15, 0.35, 0.7))
        if raster_path:
            page.insert_image(page.rect, filename=raster_path)
        elif kind != "smalltext":
            font_file = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
            fontname = "helv"
            if os.path.exists(font_file):
                page.insert_font(fontname="DEJAVU", fontfile=font_file)
                fontname = "DEJAVU"
            page.insert_text(
                (page.rect.width * 0.4, page.rect.height * 0.55),
                f"P{index + 1}",
                fontsize=11,
                fontname=fontname,
                color=(1, 1, 1),
            )
        rect = page.rect
        page.set_mediabox(rect)
        page.set_cropbox(rect)
        if real_box:
            left, right, top, bottom = insets
            page.set_trimbox(fitz.Rect(left * MM, top * MM, rect.width - right * MM, rect.height - bottom * MM))
            page.set_bleedbox(rect)
        else:
            page.set_trimbox(rect)
            page.set_bleedbox(rect)
    doc.save(path)
    doc.close()


def _insert_small_black(page, trim_w: float, trim_h: float) -> None:
    """A small rich-black block. The press ink repair turns that into K-only."""
    import io

    import pymupdf as fitz
    from PIL import Image

    dpi = 300
    width = max(8, int(round(trim_w / 25.4 * dpi)))
    height = max(8, int(round(trim_h / 25.4 * dpi)))
    arr = np.zeros((height, width, 4), np.uint8)
    y0 = min(height - 4, max(0, int(height * 0.45)))
    x0 = min(width - 4, max(0, int(width * 0.3)))
    arr[y0:y0 + 36, x0:x0 + 140] = (184, 178, 148, 171)
    buf = io.BytesIO()
    Image.fromarray(arr, mode="CMYK").save(buf, format="TIFF", compression="raw", dpi=(dpi, dpi))
    page.insert_image(page.rect, stream=buf.getvalue(), keep_proportion=False)


def _logic(product: dict, folder: str) -> None:
    from extra_checks import check_e1, check_e2
    from designer_assistant import _size_check
    from press_ready_engine import _vector_page_bleed
    from quick_print import _fit_page_to_trim, _match_product, _pdf_trim_mm, too_small
    import pymupdf as fitz

    pid = product["id"]
    trim_w = float(product["widthMm"])
    trim_h = float(product["heightMm"])
    note = NOTES.get(pid, "")
    expect_mode = {
        "noleed": "trim",
        "canva": "partial",
        "full5": "partial",
        "bleed3": "partial",
        "trimbox": "boxes",
        "uneven": "boxes",
        "raster": "trim",
        "text": "trim",
        "double": "trim",
    }
    expect_e1 = {
        "aspect2": "fixed",
        "aspect8": "warning",
        "aspect15": "failed",
    }
    cases = (
        "noleed", "canva", "full5", "bleed3", "trimbox", "uneven",
        "aspect2", "aspect8", "aspect15", "raster", "text", "double",
    )
    order = {"widthMm": trim_w, "heightMm": trim_h, "explicitSize": True}
    for kind in cases:
        pages = 2 if kind == "double" else 1
        path = os.path.join(folder, f"{pid}-{kind}.pdf")
        _write_pdf(path, trim_w, trim_h, "noleed" if kind in ("text", "double") else kind, pages)
        problems = []
        measured = _pdf_trim_mm(path)
        if kind.startswith("aspect"):
            found, _rotated = _match_product(*measured) if measured else (None, False)
            if found and found.get("id") not in (pid,):
                problems.append(f"matched {found.get('id')}")
            e1 = check_e1(path, order)
            if e1.get("status") != expect_e1[kind]:
                problems.append(f"E1 {e1.get('status')} {e1.get('detail', '')[:80]}")
            size = _size_check(path, trim_w, trim_h)
            if size.get("ok"):
                problems.append("6d called a wrong shape a match")
        else:
            if pid == "custom":
                fit = _fit_page_to_trim(*measured, trim_w, trim_h) if measured else None
                if kind in ("noleed", "raster", "text", "double"):
                    if not fit or fit["bleed"].get("kind") != "trim":
                        problems.append(f"custom fit {fit}")
                elif kind in ("canva", "full5", "bleed3"):
                    if not fit or fit["bleed"].get("kind") != "existing":
                        problems.append(f"custom bleed {fit}")
            else:
                found, rotated = _match_product(*measured) if measured else (None, False)
                if not found or found.get("id") != pid or rotated:
                    problems.append(f"product {None if not found else found.get('id')} rotated={rotated}")
            doc = fitz.open(path)
            try:
                _existing, mode = _vector_page_bleed(doc[0], trim_w, trim_h)
            finally:
                doc.close()
            if mode != expect_mode[kind]:
                problems.append(f"mode {mode}")
            e1 = check_e1(path, order)
            if e1.get("status") != "pass":
                problems.append(f"E1 {e1.get('status')} {e1.get('detail', '')[:80]}")
            size = _size_check(path, trim_w, trim_h)
            if not size.get("ok"):
                problems.append(f"6d {size.get('detail', '')[:80]}")
            if kind == "double":
                e2 = check_e2(path, {**order, "sides": "double"})
                if e2.get("status") != "pass":
                    problems.append(f"E2 {e2.get('status')} {e2.get('detail', '')[:80]}")
        _record(pid, kind + (f" ({note})" if note and kind == "noleed" else ""), not problems, "; ".join(problems))

    pixels_w = int(round(trim_w / 25.4 * 300))
    pixels_h = int(round(trim_h / 25.4 * 300))
    small = too_small(pixels_w, pixels_h, trim_w, trim_h)
    _record(pid, "rgb-pixels", not small, "300 dpi picture was called too small" if small else "")


def _press_size(path: str) -> tuple[float, float]:
    import pymupdf as fitz

    doc = fitz.open(path)
    try:
        box = doc[0].mediabox
        return box.width * 25.4 / 72.0, box.height * 25.4 / 72.0
    finally:
        doc.close()


def _page_text(path: str) -> str:
    import pymupdf as fitz

    doc = fitz.open(path)
    try:
        return doc[0].get_text("text") or ""
    finally:
        doc.close()


def _full_plate(path: str) -> bool:
    import pymupdf as fitz

    doc = fitz.open(path)
    try:
        page = doc[0]
        for info in page.get_image_info() or []:
            box = info.get("bbox") or (0, 0, 0, 0)
            if (box[2] - box[0]) > page.rect.width * 0.5 and (box[3] - box[1]) > page.rect.height * 0.5:
                return True
        return False
    finally:
        doc.close()


def _child_peak_mb() -> str:
    """High-water RSS of this process.

    Linux keeps ru_maxrss across exec, so a child would otherwise report the
    parent's peak. VmHWM is this process only.
    """
    return (
        "def _peak_mb():\n"
        "    try:\n"
        "        for line in open('/proc/self/status', encoding='ascii'):\n"
        "            if line.startswith('VmHWM:'):\n"
        "                return round(int(line.split()[1]) / 1024.0, 1)\n"
        "    except OSError:\n"
        "        pass\n"
        "    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1)\n"
    )


def _compile_measured(src: str, out: str, trim_w: float, trim_h: float) -> dict:
    """Compile in a child process so the peak RSS is this sheet, not the suite."""
    import json
    import subprocess

    code = (
        "import json, resource, time\n"
        "from press_ready_engine import compile_vector_press\n"
        + _child_peak_mb()
        + f"t0 = time.perf_counter()\n"
        f"result = compile_vector_press({src!r}, {out!r}, {trim_w}, {trim_h}, 5)\n"
        "peak = _peak_mb()\n"
        "report = result.get('report') or {}\n"
        "print(json.dumps({\n"
        "  'used': bool(result.get('used')),\n"
        "  'seconds': round(time.perf_counter() - t0, 2),\n"
        "  'peakMb': peak,\n"
        "  'existing': bool(report.get('existingBleed')),\n"
        "  'large': bool(report.get('largeFormat')),\n"
        "  'reason': str(report.get('reason') or '')[:120],\n"
        "}))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=os.path.dirname(__file__),
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    payload = {}
    for line in reversed((proc.stdout or "").splitlines()):
        if line.startswith("{"):
            payload = json.loads(line)
            break
    if not payload:
        payload = {"used": False, "seconds": 0, "peakMb": 0, "reason": (proc.stderr or "")[-180:]}
    return payload


def _compile_large(product: dict, folder: str, kind: str) -> None:
    from press_ready_engine import press_window_seam

    pid = product["id"]
    trim_w = float(product["widthMm"])
    trim_h = float(product["heightMm"])
    src = os.path.join(folder, f"{pid}-{kind}.pdf")
    if not os.path.exists(src):
        _write_pdf(src, trim_w, trim_h, kind)
    out = os.path.join(folder, f"{pid}-{kind}-press.pdf")
    measured = _compile_measured(src, out, trim_w, trim_h)
    problems = []
    if not measured.get("used") or not os.path.exists(out):
        problems.append(measured.get("reason") or "no press file")
    else:
        width, height = _press_size(out)
        if abs(width - (trim_w + 10)) > 1.5 or abs(height - (trim_h + 10)) > 1.5:
            problems.append(f"press {width:.1f}x{height:.1f}")
        if "P1" not in _page_text(out):
            problems.append("vector text lost")
        if _full_plate(out):
            problems.append("full plate")
        if not measured.get("large"):
            problems.append("not large-format")
        if float(measured.get("peakMb") or 0) > 450:
            problems.append(f"peak {measured.get('peakMb')}MB")
        if float(measured.get("seconds") or 0) > 60:
            problems.append(f"time {measured.get('seconds')}s")
        if kind == "canva":
            if not measured.get("existing"):
                problems.append("existing bleed not kept")
            seams = press_window_seam(src, out) or []
            worst = max((float(row.get("page_max") or 0) for row in seams), default=99)
            if not seams or worst >= 5:
                problems.append(f"seam {worst:.2f}")
    detail = "; ".join(problems) or f"{measured.get('seconds')}s peak {measured.get('peakMb')}MB"
    _record(pid, f"press-{kind}", not problems, detail)


def _compile_case(product: dict, folder: str, kind: str) -> None:
    from press_ready_engine import compile_vector_press, press_window_seam

    pid = product["id"]
    trim_w = float(product["widthMm"])
    trim_h = float(product["heightMm"])
    src = os.path.join(folder, f"{pid}-{kind}.pdf")
    if not os.path.exists(src):
        _write_pdf(src, trim_w, trim_h, kind)
    out = os.path.join(folder, f"{pid}-{kind}-press.pdf")
    result = compile_vector_press(src, out, trim_w, trim_h, 5)
    problems = []
    if not result.get("used") or not os.path.exists(out):
        problems.append("no press file")
    else:
        width, height = _press_size(out)
        if abs(width - (trim_w + 10)) > 1.5 or abs(height - (trim_h + 10)) > 1.5:
            problems.append(f"press {width:.1f}x{height:.1f}")
        if kind in ("canva", "bleed3", "trimbox", "uneven", "full5"):
            seams = press_window_seam(src, out) or []
            worst = max((float(row.get("page_max") or 0) for row in seams), default=99)
            if not seams or worst >= 5:
                problems.append(f"seam {worst:.2f}")
            report = result.get("report") or {}
            if not report.get("existingBleed"):
                problems.append("existing bleed not kept")
        if kind == "smalltext":
            from client_file_audit import repair_cmyk_images
            from extra_checks import press_ink_facts

            # The full press compile does this after the plate is built.
            repair_cmyk_images(out, text_only=True)
            facts = press_ink_facts(out)
            if not facts.get("small_k"):
                problems.append(f"k-only {facts.get('k_only')}")
            if float(facts.get("max_tac") or 0) > 300.5:
                problems.append(f"tac {float(facts.get('max_tac') or 0):.1f}")
    _record(pid, f"press-{kind}", not problems, "; ".join(problems))


def _card_reasons(folder: str) -> None:
    from quick_print import make_print_ready

    banned = ("cannot be extended", "cut line", "not cmyk", "traceback", "could not be read", "already had 5 mm")
    for kind in ("noleed", "canva", "uneven"):
        src = os.path.join(folder, f"card-90x50-{kind}.pdf")
        if not os.path.exists(src):
            _write_pdf(src, 90, 50, kind)
        result = make_print_ready(
            src,
            os.path.join(folder, f"card-{kind}"),
            90,
            50,
            "card-90x50",
            "Business card 90 × 50",
            filename=os.path.basename(src),
            detect_size=False,
        )
        reasons = " ".join(str(item) for item in list(result.get("reasons") or []) + list(result.get("decisions") or []))
        problems = []
        if result.get("productId") != "card-90x50":
            problems.append(f"product {result.get('productId')}")
        if str(result.get("light") or "") == "red":
            problems.append("red")
        lowered = reasons.lower()
        for phrase in banned:
            if phrase in lowered:
                problems.append(phrase)
        press = result.get("pressPath") or ""
        if press and os.path.exists(press):
            width, height = _press_size(press)
            if abs(width - 100) > 1.5 or abs(height - 60) > 1.5:
                problems.append(f"press {width:.1f}x{height:.1f}")
        else:
            problems.append("no press file")
        if kind != "noleed" and not result.get("existingBleedKept"):
            problems.append("existing bleed not kept")
        _record("card-90x50", f"reasons-{kind}", not problems, "; ".join(problems) or str(result.get("light")))


def _rgb_card(folder: str) -> None:
    import cv2
    from quick_print import make_print_ready

    path = os.path.join(folder, "card-rgb.png")
    image = np.zeros((int(round(50 / 25.4 * 300)), int(round(90 / 25.4 * 300)), 3), np.uint8)
    image[:, :] = (40, 80, 200)
    cv2.imwrite(path, image)
    result = make_print_ready(
        path,
        os.path.join(folder, "rgb"),
        90,
        50,
        "card-90x50",
        "Business card 90 × 50",
        filename="card-rgb.png",
        detect_size=False,
    )
    problems = []
    if result.get("productId") != "card-90x50":
        problems.append(f"product {result.get('productId')}")
    if str(result.get("light") or "") == "red":
        problems.append("red " + " ".join(result.get("reasons") or [])[:120])
    press = result.get("pressPath") or ""
    if not press or not os.path.exists(press):
        problems.append("no press file")
    else:
        width, height = _press_size(press)
        if abs(width - 100) > 1.5 or abs(height - 60) > 1.5:
            problems.append(f"press {width:.1f}x{height:.1f}")
        from extra_checks import press_ink_facts

        facts = press_ink_facts(press)
        if float(facts.get("max_tac") or 0) > 300.5:
            problems.append(f"tac {float(facts.get('max_tac') or 0):.1f}")
    _record("card-90x50", "rgb-image", not problems, "; ".join(problems))


def _write_table() -> None:
    out = "/opt/cursor/artifacts/size_matrix.txt"
    os.makedirs(os.path.dirname(out), exist_ok=True)
    lines = ["product\tcase\tresult\tdetail"]
    for row in ROWS:
        lines.append(f"{row['product']}\t{row['case']}\t{'pass' if row['ok'] else 'FAIL'}\t{row['detail']}")
    text = "\n".join(lines) + "\n"
    with open(out, "w", encoding="utf-8") as handle:
        handle.write(text)


def run() -> list[str]:
    folder = tempfile.mkdtemp(prefix="size-matrix-")
    products = _products()
    for product in products:
        _logic(product, folder)
    compile_ids = {
        "a6", "a6-landscape", "a5", "a7", "dl", "card-90x50", "card-50x90", "custom",
    }
    for product in products:
        long_side = max(float(product["widthMm"]), float(product["heightMm"]))
        if long_side > PRESS_LIMIT_MM:
            _compile_large(product, folder, "noleed")
            _compile_large(product, folder, "canva")
            continue
        if product["id"] not in compile_ids and not str(product["id"]).startswith("card"):
            # Every remaining sheet at or under A3 still gets the partial-bleed compile.
            if long_side > 297 and product["id"] not in ("a3", "a3-landscape"):
                continue
        _compile_case(product, folder, "canva")
    for product in products:
        if product["id"] in ("card-90x50", "a6-landscape", "a5", "custom"):
            _compile_case(product, folder, "uneven")
            _compile_case(product, folder, "full5")
    _compile_case({"id": "card-90x50", "widthMm": 90, "heightMm": 50}, folder, "smalltext")
    _card_reasons(folder)
    _rgb_card(folder)
    _write_table()
    return list(FAILURES)


def main() -> None:
    failed = run()
    print(f"size matrix {len(ROWS) - len(failed)} pass, {len(failed)} fail")
    if failed:
        raise SystemExit(f"{len(failed)} failed: {', '.join(failed[:12])}")


if __name__ == "__main__":
    main()
