#!/usr/bin/env python3
"""Press-PDF bleed matrix. Builds fixtures, compiles them, and checks the file a printer would get."""

import os
import shutil
import subprocess
import sys
import tempfile

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bleed_preview import draw_bleed_preview
from colour_border import sample_edge_cmyk
from smart_bleed import choose_automatic_bleed_api

MM = 72.0 / 25.4
ROOT = os.path.dirname(os.path.abspath(__file__))
COMPILE = os.path.join(ROOT, "compile_press_pdf.py")
FOLDER = tempfile.mkdtemp(prefix="bleed-matrix-")
RESULTS = []
CORNERS = []


def check(group, name, ok, detail=""):
    RESULTS.append((group, name, bool(ok), detail))
    mark = "PASS" if ok else "FAIL"
    line = f"{mark}  {group}  {name}"
    if detail and not ok:
        line += f"  — {detail}"
    print(line, flush=True)


def save_image(path, bgr, dpi=300):
    from PIL import Image
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    image = Image.fromarray(rgb)
    image.save(path, dpi=(dpi, dpi), quality=90)


def marker(img):
    height, width = img.shape[:2]
    y, x = height // 2, width // 2
    img[y - 10:y + 10, x - 10:x + 10] = (255, 0, 255)
    return img


def make_photo(width, height):
    rng = np.random.default_rng(7)
    img = rng.integers(25, 210, (height, width, 3), dtype=np.uint8)
    img = cv2.GaussianBlur(img, (0, 0), 1.1)
    return marker(img)


def make_flat_text():
    img = np.full((700, 900, 3), (40, 70, 190), np.uint8)
    font = cv2.FONT_HERSHEY_SIMPLEX
    cv2.putText(img, "OPEN DAY", (12, 48), font, 1.4, (255, 255, 255), 3, cv2.LINE_AA)
    cv2.putText(img, "SALE", (12, 680), font, 1.6, (255, 255, 255), 3, cv2.LINE_AA)
    cv2.putText(img, "FLY", (8, 360), font, 1.2, (255, 255, 255), 3, cv2.LINE_AA)
    cv2.putText(img, "ZA", (780, 360), font, 1.2, (255, 255, 255), 3, cv2.LINE_AA)
    return marker(img)


def make_pdf(path, width_mm, height_mm, trim_inset_mm=0, outer=(20, 20, 220), inner=(220, 40, 40)):
    import pymupdf
    doc = pymupdf.open()
    page = doc.new_page(width=width_mm * MM, height=height_mm * MM)
    page.draw_rect(page.rect, color=None, fill=(outer[2] / 255, outer[1] / 255, outer[0] / 255))
    if trim_inset_mm > 0:
        inset = trim_inset_mm * MM
        trim = pymupdf.Rect(inset, inset, page.rect.width - inset, page.rect.height - inset)
        page.draw_rect(trim, color=None, fill=(inner[2] / 255, inner[1] / 255, inner[0] / 255))
        page.set_trimbox(trim)
        page.set_bleedbox(page.rect)
    else:
        page.insert_text((24, 40), "Vector artwork", fontsize=18, color=(1, 1, 1))
        mark = pymupdf.Rect(page.rect.width / 2 - 12, page.rect.height / 2 - 12, page.rect.width / 2 + 12, page.rect.height / 2 + 12)
        page.draw_rect(mark, color=None, fill=(1, 0, 1))
    doc.save(path)
    doc.close()


def compile_pdf(src, strategy, trim_w, trim_h, bleed, name, extra=None, env=None):
    out = os.path.join(FOLDER, name + ".pdf")
    cmd = [
        sys.executable, COMPILE,
        "--input", src, "--output", out,
        "--strategy", strategy,
        "--color-space", "cmyk",
        "--trim-w", str(trim_w), "--trim-h", str(trim_h),
        "--bleed-mm", str(bleed),
        "--status-file", os.path.join(FOLDER, name + ".status.json"),
        "--result-file", os.path.join(FOLDER, name + ".result.json"),
        "--base-name", name,
    ]
    if extra:
        cmd.extend(extra)
    run_env = os.environ.copy()
    run_env.pop("GEMINI_API_KEY", None)
    if env:
        run_env.update(env)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=240, env=run_env)
    return proc, out


def inspect(path, trim_w, trim_h, bleed, expect_center=True, allow_white_edge=False, bleed_ring=None):
    import pymupdf
    doc = pymupdf.open(path)
    page = doc[0]
    media = page.mediabox
    trim = page.trimbox
    bleed_box = page.bleedbox
    notes = []
    ok = True

    def want(cond, text):
        nonlocal ok
        if not cond:
            ok = False
            notes.append(text)

    expect_media_w = (trim_w + 2 * bleed) * MM
    expect_media_h = (trim_h + 2 * bleed) * MM
    expect_trim_w = trim_w * MM
    expect_trim_h = trim_h * MM
    want(abs(media.width - expect_media_w) < 1.8, f"media w {media.width:.1f} != {expect_media_w:.1f}")
    want(abs(media.height - expect_media_h) < 1.8, f"media h {media.height:.1f} != {expect_media_h:.1f}")
    want(abs(trim.width - expect_trim_w) < 1.8, f"trim w {trim.width:.1f} != {expect_trim_w:.1f}")
    want(abs(trim.height - expect_trim_h) < 1.8, f"trim h {trim.height:.1f} != {expect_trim_h:.1f}")
    want(abs(bleed_box.width - media.width) < 1.8 and abs(bleed_box.height - media.height) < 1.8, "bleed box is not the full page")
    inset = trim.x0 - media.x0
    want(abs(inset - bleed * MM) < 1.8, f"trim inset {inset / MM:.2f}mm")

    images = page.get_images(full=True)
    want(len(images) == 1, f"{len(images)} images")
    if images:
        info = doc.extract_image(images[0][0])
        want(info.get("colorspace") == 4, f"colorspace {info.get('colorspace')}")
        page_in = media.width / 72.0
        dpi = info["width"] / page_in if page_in else 0
        want(abs(dpi - 300) < 15, f"dpi {dpi:.1f}")
        want(info.get("smask") in (0, None), "soft mask")

    scale = 72.0 / 72.0
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if arr.shape[2] == 4:
        arr = arr[:, :, :3]

    def at(x_pt, y_pdf):
        x = int(np.clip((x_pt - media.x0) / media.width * (arr.shape[1] - 1), 1, arr.shape[1] - 2))
        y = int(np.clip((media.y1 - y_pdf) / media.height * (arr.shape[0] - 1), 1, arr.shape[0] - 2))
        return arr[y, x]

    corners = [arr[1, 1], arr[1, -2], arr[-2, 1], arr[-2, -2]]
    if not allow_white_edge:
        white = [c.tolist() for c in corners if int(c.min()) > 245]
        want(not white, f"white corner {white[:1]}")

    if expect_center:
        center = at((trim.x0 + trim.x1) / 2, (trim.y0 + trim.y1) / 2)
        # Magenta marker survives CMYK as a red-blue pixel, not grey or white.
        want(int(center[0]) > 140 and int(center[2]) > 140 and int(center[1]) < 140, f"center {center.tolist()}")

    if bleed_ring is not None:
        # 2mm outside the trim, still inside the bleed. PDF y-up: above the trim is higher y.
        sample = at((trim.x0 + trim.x1) / 2, trim.y1 + 2 * MM)
        want(int(sample[0]) > 160 and int(sample[1]) < 90 and int(sample[2]) < 90, f"bleed ring {sample.tolist()}")

    # Cut line sits on the same inset as the TrimBox. Drawn on a blank page so a red bleed cannot hide it.
    blank = np.full_like(arr, 40)
    preview = draw_bleed_preview(cv2.cvtColor(blank, cv2.COLOR_RGB2BGR), bleed, 72.0)
    red = preview[:, :, 2] > 180
    cut_cols = np.where(red[preview.shape[0] // 2])[0]
    if len(cut_cols):
        left_cut = cut_cols[0] / preview.shape[1] * media.width
        want(abs(left_cut - trim.x0) < 4.0, f"cut line {left_cut:.1f} vs trim {trim.x0:.1f}")
    else:
        want(False, "no cut line")

    doc.close()
    corner_img = np.vstack([
        np.hstack([arr[:36, :36], arr[:36, -36:]]),
        np.hstack([arr[-36:, :36], arr[-36:, -36:]]),
    ])
    return ok, "; ".join(notes), corner_img


def run_case(group, name, src, strategy, trim_w, trim_h, bleed, extra=None, env=None, **inspect_kwargs):
    proc, out = compile_pdf(src, strategy, trim_w, trim_h, bleed, name, extra=extra, env=env)
    if proc.returncode != 0 or not os.path.exists(out):
        tail = (proc.stderr or proc.stdout or "")[-400:].replace("\n", " ")
        check(group, name, False, tail)
        return None
    import json
    with open(os.path.join(FOLDER, name + ".result.json"), encoding="utf-8") as handle:
        result = json.load(handle)
    ok, detail, corner = inspect(out, trim_w, trim_h, bleed, **inspect_kwargs)
    geo = (((result.get("audit_report") or {}).get("geometry") or {}).get("action_taken") or "")
    if f"{bleed:g}mm" not in geo and f"{float(bleed):.1f}" not in geo:
        ok = False
        detail = (detail + "; " if detail else "") + "health text missing bleed size"
    if abs(float(result.get("bleedMm") or -1) - float(bleed)) > 0.05:
        ok = False
        detail = (detail + "; " if detail else "") + f"result bleed {result.get('bleedMm')}"
    check(group, name, ok, detail)
    if corner is not None:
        CORNERS.append((f"{group} {name}", corner))
    return result


def main():
    photo = os.path.join(FOLDER, "photo.jpg")
    save_image(photo, make_photo(900, 600))
    flat = os.path.join(FOLDER, "flat.png")
    save_image(flat, make_flat_text())
    ai_square = os.path.join(FOLDER, "ai-1024.png")
    save_image(ai_square, make_photo(1024, 1024), dpi=72)
    ai_portrait = os.path.join(FOLDER, "ai-1024x1536.png")
    save_image(ai_portrait, make_photo(1024, 1536), dpi=72)
    ai_landscape = os.path.join(FOLDER, "ai-1792x1024.png")
    save_image(ai_landscape, make_photo(1792, 1024), dpi=72)
    mismatch = os.path.join(FOLDER, "mismatch.png")
    save_image(mismatch, make_photo(800, 800), dpi=72)

    # Already 5mm bleed around a 90x50 card, at 300 DPI. Outer ring is red, trim is blue, center is magenta.
    bleed_px = int(round(5 / 25.4 * 300))
    trim_w_px = int(round(90 / 25.4 * 300))
    trim_h_px = int(round(50 / 25.4 * 300))
    already = np.full((trim_h_px + 2 * bleed_px, trim_w_px + 2 * bleed_px, 3), (20, 20, 220), np.uint8)
    already[bleed_px:-bleed_px, bleed_px:-bleed_px] = (220, 40, 40)
    already = marker(already)
    already_path = os.path.join(FOLDER, "already-bleed.jpg")
    save_image(already_path, already, dpi=300)

    vector = os.path.join(FOLDER, "vector.pdf")
    make_pdf(vector, 148, 210, trim_inset_mm=0)
    vector_bleed = os.path.join(FOLDER, "vector-bleed.pdf")
    make_pdf(vector_bleed, 100, 60, trim_inset_mm=5, outer=(20, 20, 220), inner=(220, 40, 40))
    raster_pdf = os.path.join(FOLDER, "raster.pdf")
    import pymupdf
    raster_doc = pymupdf.open()
    raster_page = raster_doc.new_page(width=90 * MM, height=50 * MM)
    png_bytes_path = os.path.join(FOLDER, "raster-src.png")
    save_image(png_bytes_path, make_photo(600, 400))
    raster_page.insert_image(raster_page.rect, filename=png_bytes_path)
    raster_doc.save(raster_pdf)
    raster_doc.close()

    illustrator = os.path.join(FOLDER, "campaign.ai")
    ai_doc = pymupdf.open()
    ai_page = ai_doc.new_page(width=200, height=120)
    ai_page.draw_rect(ai_page.rect, color=None, fill=(0.1, 0.4, 0.8))
    ai_page.insert_text((20, 60), "Illustrator", fontsize=16, color=(1, 1, 1))
    ai_page.draw_rect(pymupdf.Rect(88, 48, 112, 72), color=None, fill=(1, 0, 1))
    ai_doc.save(illustrator)
    ai_doc.close()

    from illustrator_intake import prepare_illustrator_file
    eps_path = os.path.join(FOLDER, "logo.eps")
    with open(eps_path, "wb") as handle:
        handle.write(b"""%!PS-Adobe-3.0 EPSF-3.0
%%BoundingBox: 0 0 200 100
%%HiResBoundingBox: 0.000 0.000 200.000 100.000
%%LanguageLevel: 2
%%Pages: 1
%%EndComments
0 0 1 0 setcmykcolor
0 0 200 100 rectfill
1 0 1 setrgbcolor
80 30 40 40 rectfill
0 0 0 1 setcmykcolor
/Helvetica findfont 18 scalefont setfont
10 40 moveto (EPS artwork) show
showpage
%%EOF
""")
    prepared = prepare_illustrator_file(eps_path, ".eps")
    check("input", "eps prepares", prepared.get("success") is True, str(prepared.get("error")))

    # Automatic choice, before any compile.
    photo_choice = choose_automatic_bleed_api(cv2.imread(photo), 300)
    flat_choice = choose_automatic_bleed_api(cv2.imread(flat), 300)
    check("automatic", "photo is mirror or extract", photo_choice in ("mirror", "bgExtract"), photo_choice)
    check("automatic", "flat text is not mirror", flat_choice != "mirror", flat_choice)
    check("automatic", "flat text is border or extract", flat_choice in ("colourBorder", "bgExtract"), flat_choice)

    styles = [
        ("mirror", None),
        ("bgExtract", None),
        ("stretch", None),
        ("replicate", None),
        ("upscale", None),
        ("ai_outpaint", None),
        ("colourBorder", ["--border-c", "0", "--border-m", "100", "--border-y", "100", "--border-k", "0", "--border-label", "Red"]),
        ("auto", None),
    ]
    for strategy, extra in styles:
        run_case("style", strategy, photo, strategy, 90, 50, 5, extra=extra)

    run_case(
        "style", "ai_outpaint no credit", photo, "ai_outpaint", 90, 50, 5,
        env={"REPLICATE_API_TOKEN": "no-credit", "GEMINI_API_KEY": ""},
    )
    edge = sample_edge_cmyk(cv2.imread(flat))
    run_case(
        "style", "colour border rich black", flat, "colourBorder", 90, 50, 5,
        extra=["--border-c", "40", "--border-m", "30", "--border-y", "30", "--border-k", "100", "--border-label", "Rich Black"],
        allow_white_edge=True,
    )
    run_case(
        "style", "colour border custom", flat, "colourBorder", 90, 50, 5,
        extra=["--border-c", "10", "--border-m", "80", "--border-y", "0", "--border-k", "0", "--border-label", "Custom"],
    )
    run_case(
        "style", "colour border match edge", flat, "colourBorder", 90, 50, 5,
        extra=["--border-c", str(edge[0]), "--border-m", str(edge[1]), "--border-y", str(edge[2]), "--border-k", str(edge[3]), "--border-label", "Match edge"],
    )

    for bleed in (3, 10):
        run_case("size", f"mirror {bleed}mm", photo, "mirror", 90, 50, bleed)
        run_case("size", f"auto {bleed}mm", photo, "auto", 90, 50, bleed)

    pages = [("DL", 99, 210), ("A5", 148, 210), ("A4", 210, 297), ("A3", 297, 420)]
    for label, width, height in pages:
        run_case("page", f"{label} mirror 5mm", photo, "mirror", width, height, 5)

    inputs = [
        ("ai 1024", ai_square),
        ("ai 1024x1536", ai_portrait),
        ("ai 1792x1024", ai_landscape),
        ("flat text", flat),
        ("vector pdf", vector),
        ("raster pdf", raster_pdf),
        ("illustrator ai", illustrator),
        ("eps", eps_path),
        ("shape mismatch", mismatch),
    ]
    for label, src in inputs:
        run_case("input", f"{label} auto", src, "auto", 90, 50, 5)

    run_case(
        "input", "already has 5mm bleed", already_path, "mirror", 90, 50, 5,
        expect_center=True, bleed_ring=(20, 20, 220),
    )
    run_case(
        "input", "vector pdf keeps its bleed", vector_bleed, "mirror", 90, 50, 5,
        expect_center=False, bleed_ring=(20, 20, 220),
    )
    # Asking for 10mm must add 5mm, not another full 5mm on top of a zoomed page.
    run_case(
        "input", "vector bleed topped up to 10mm", vector_bleed, "mirror", 90, 50, 10,
        expect_center=False, bleed_ring=(20, 20, 220),
    )

    # One health-report PDF uses the same bleed sentence as the press file.
    mirror_result = os.path.join(FOLDER, "style-mirror.result.json")
    # The case name becomes the file via `name` which is the second part. run_case uses `name` as the file.
    # Files are `{name}.result.json` and name was the strategy for the style group... run_case name is the `name` argument.
    # I passed name as the strategy string for the first loop via run_case(..., name is the 2nd positional which is `name` param).
    # def run_case(group, name, ...) so file is mirror.result.json not style-mirror.
    report_src = os.path.join(FOLDER, "mirror.result.json")
    if os.path.exists(report_src):
        import json
        from health_report import build_report
        with open(report_src, encoding="utf-8") as handle:
            payload = json.load(handle)
        message = payload["audit_report"]["geometry"]["action_taken"]
        report_path = os.path.join(FOLDER, "health.pdf")
        build_report(
            [{"name": "Bleed Margins", "passed": True, "autoFixed": True, "message": message}],
            "photo.jpg",
            photo,
            report_path,
        )
        import pymupdf
        report = pymupdf.open(report_path)
        text = "".join(page.get_text() for page in report)
        report.close()
        check("report", "health report states 5mm", "5mm" in text, text[:180])

    sheet = build_contact_sheet(CORNERS)
    art_dir = "/opt/cursor/artifacts"
    os.makedirs(art_dir, exist_ok=True)
    dest = os.path.join(art_dir, "bleed-corner-contact-sheet.png")
    if sheet is not None:
        cv2.imwrite(dest, sheet)
        print(f"CONTACT {dest}", flush=True)

    failed = [row for row in RESULTS if not row[2]]
    print(f"\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed, folder {FOLDER}")
    if failed:
        sys.exit(1)


def build_contact_sheet(items):
    if not items:
        return None
    cell_w, cell_h = 150, 110
    cols = 6
    rows = int(np.ceil(len(items) / cols))
    sheet = np.full((rows * cell_h, cols * cell_w, 3), 245, np.uint8)
    for index, (label, corner) in enumerate(items):
        row, col = divmod(index, cols)
        thumb = cv2.resize(corner, (cell_w - 8, cell_h - 28), interpolation=cv2.INTER_NEAREST)
        y = row * cell_h + 22
        x = col * cell_w + 4
        sheet[y:y + thumb.shape[0], x:x + thumb.shape[1]] = cv2.cvtColor(thumb, cv2.COLOR_RGB2BGR)
        cv2.putText(sheet, label[:22], (x, row * cell_h + 14), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (20, 20, 20), 1, cv2.LINE_AA)
    return sheet


if __name__ == "__main__":
    main()
