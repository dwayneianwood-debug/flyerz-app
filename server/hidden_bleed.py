"""
Hidden bleed recovery.

Many CorelDRAW / Canva / Illustrator PDFs carry artwork that runs past the page
edge but is cut off by the MediaBox at export. When it reaches the full bleed on
every side we can simply grow the page boxes (MediaBox/BleedBox = trim + bleed,
TrimBox = original page) instead of faking bleed. Nothing is re-rendered, so
vectors stay vectors.

Feature flag: FLYERZ_HIDDEN_BLEED=1 (default off). Every entry point that the app
calls checks hidden_bleed_enabled() first, so with the flag off nothing changes.

CLI:
  python hidden_bleed.py detect  <pdf> [--bleed-mm 5]
  python hidden_bleed.py recover <in.pdf> <out.pdf> [--bleed-mm 5]
  python hidden_bleed.py preview <pdf> <out_dir> [--bleed-mm 5]
All commands print one JSON object.
"""
from __future__ import annotations

import base64
import json
import os
import sys

import numpy as np

try:
    import pymupdf as fitz
except ImportError:  # older PyMuPDF
    import fitz

MM = 72.0 / 25.4
SIDES = ("top", "bottom", "left", "right")
DEFAULT_BLEED_MM = 5.0          # house rule: same 5 mm as compile_press_pdf.PRESS_DEFAULT_BLEED_MM
TOLERANCE_MM = 0.25             # rounding slack from exporters
CLIP_EDGE_MM = 0.5              # a clip within this of the page edge counts as "clipped at the edge"
MAX_PAGES = 4                   # same page cap the quick checker uses
# Same thresholds as press_ready_engine._bitmap_problems: band white (>245) where artwork is not (<230).
SLIVER_WHITE = 245
SLIVER_ART = 230
SLIVER_MAX_FRACTION = 0.03
VERIFY_DPI = 36
DRAW_KINDS = ("fill-path", "stroke-path", "fill-image", "fill-shade", "fill-text", "stroke-text",
              "fill-imgmask")

FRIENDLY_FOUND = ("Good news: your design already has extra background around the edge, "
                  "so we'll use that.")


def hidden_bleed_enabled() -> bool:
    return os.environ.get("FLYERZ_HIDDEN_BLEED", "").strip().lower() in ("1", "true", "yes", "on")


def _has_bleed_boxes(page) -> bool:
    """True when the file already declares bleed (TrimBox inset inside MediaBox)."""
    try:
        media, trim = page.mediabox, page.trimbox
    except Exception:
        return False
    return abs(trim.width - media.width) >= 1.5 or abs(trim.height - media.height) >= 1.5


def _art_rect(page) -> "fitz.Rect":
    art = fitz.Rect()
    for kind, box in page.get_bboxlog():
        if kind.startswith(DRAW_KINDS):
            r = fitz.Rect(box)
            if r.is_valid and not r.is_empty:
                art |= r
    return art


def _clipped_sides(page, page_rect) -> set:
    """Sides where a clip path sits on the page edge (content cannot reach past it)."""
    tol = CLIP_EDGE_MM * MM
    sides = set()
    try:
        drawings = page.get_drawings(extended=True)
    except Exception:
        return sides
    for d in drawings:
        if d.get("type") != "clip":
            continue
        s = d.get("scissor")
        if s is None:
            continue
        s = fitz.Rect(s)
        if abs(s.y0 - page_rect.y0) <= tol:
            sides.add("top")
        if abs(s.y1 - page_rect.y1) <= tol:
            sides.add("bottom")
        if abs(s.x0 - page_rect.x0) <= tol:
            sides.add("left")
        if abs(s.x1 - page_rect.x1) <= tol:
            sides.add("right")
    return sides


def _set_boxes(doc, page, bleed_pt: float) -> None:
    """
    MediaBox/CropBox/BleedBox = [0 0 W+2b H+2b], TrimBox = [b b W+b H+b].
    The page content is shifted by a single `cm` translation wrapped around the
    existing content streams (no re-render, vectors untouched). Keeping the
    origin at 0,0 matches what compile_press_pdf.page_existing_bleed expects.
    """
    m = page.mediabox
    w, h = m.width, m.height
    tx, ty = bleed_pt - m.x0, bleed_pt - m.y0
    arr = lambda x0, y0, x1, y1: f"[{x0:.4f} {y0:.4f} {x1:.4f} {y1:.4f}]"
    full = arr(0, 0, w + 2 * bleed_pt, h + 2 * bleed_pt)
    trim = arr(bleed_pt, bleed_pt, w + bleed_pt, h + bleed_pt)
    contents = page.get_contents()
    pre, post = doc.get_new_xref(), None
    doc.update_object(pre, "<<>>")
    doc.update_stream(pre, f"q 1 0 0 1 {tx:.4f} {ty:.4f} cm\n".encode())
    post = doc.get_new_xref()
    doc.update_object(post, "<<>>")
    doc.update_stream(post, b"\nQ\n")
    doc.xref_set_key(page.xref, "Contents", "[" + " ".join(f"{x} 0 R" for x in [pre, *contents, post]) + "]")
    doc.xref_set_key(page.xref, "MediaBox", full)
    doc.xref_set_key(page.xref, "CropBox", full)
    doc.xref_set_key(page.xref, "BleedBox", full)
    doc.xref_set_key(page.xref, "TrimBox", trim)
    if doc.xref_get_key(page.xref, "ArtBox")[0] != "null":
        doc.xref_set_key(page.xref, "ArtBox", trim)
    # Annotations (links etc.) follow the artwork.
    for annot_xref in [a[0] for a in page.annot_xrefs()] if hasattr(page, "annot_xrefs") else []:
        kind, val = doc.xref_get_key(annot_xref, "Rect")
        if kind == "array":
            nums = [float(v) for v in val.strip("[]").split()]
            if len(nums) == 4:
                doc.xref_set_key(annot_xref, "Rect", arr(nums[0] + tx, nums[1] + ty, nums[2] + tx, nums[3] + ty))


def _band_slivers(doc_bytes: bytes, page_index: int, bleed_mm: float, dpi: int = VERIFY_DPI) -> dict:
    """Render only the expanded page at low dpi and look for white slivers in the bleed band."""
    tmp = fitz.open("pdf", doc_bytes)
    try:
        page = tmp[page_index]
        _set_boxes(tmp, page, bleed_mm * MM)
        page = tmp[page_index]
        pix = page.get_pixmap(dpi=dpi, alpha=False, colorspace=fitz.csRGB)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3]
    finally:
        tmp.close()
    b = max(1, int(round(bleed_mm / 25.4 * dpi)))
    h, w = img.shape[:2]
    out = {}
    if h <= 2 * b + 2 or w <= 2 * b + 2:
        return {s: 0.0 for s in SIDES}
    inner = slice(b + 1, None)
    # The outermost pixel row/column is a partly covered pixel when the page size is not a whole
    # number of pixels, so it reads white even for artwork that reaches the edge exactly. Skip it.
    bands = {
        "top": (img[1:b, b:w - b], img[b + 1, b:w - b]),
        "bottom": (img[h - b:h - 1, b:w - b], img[h - b - 2, b:w - b]),
        "left": (img[b:h - b, 1:b].transpose(1, 0, 2), img[b:h - b, b + 1]),
        "right": (img[b:h - b, w - b:w - 1].transpose(1, 0, 2), img[b:h - b, w - b - 2]),
    }
    del inner
    for side, (band, edge) in bands.items():
        band_white = (band.min(axis=2) > SLIVER_WHITE).any(axis=0)   # any white pixel across the band depth
        edge_art = edge.min(axis=1) < SLIVER_ART
        cols = int(edge_art.sum())
        out[side] = float((band_white & edge_art).sum()) / cols if cols else 0.0
    return out


def detect_hidden_bleed(pdf_path: str, bleed_mm: float = DEFAULT_BLEED_MM, verify: bool = True,
                        max_pages: int = MAX_PAGES) -> dict:
    """Return {found, recoverable, perSideMm, reason, pages:[...]} without a full render."""
    result = {"found": False, "recoverable": False, "bleedMm": bleed_mm,
              "perSideMm": {s: 0.0 for s in SIDES}, "reason": "", "pages": []}
    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:
        result["reason"] = f"Could not open the PDF: {exc}"
        return result
    try:
        if not doc.is_pdf or len(doc) == 0:
            result["reason"] = "Not a PDF."
            return result
        doc_bytes = doc.tobytes() if verify else b""
        need = bleed_mm - TOLERANCE_MM
        mins = {s: None for s in SIDES}
        all_ok = True
        any_found = False
        for i in range(min(len(doc), max_pages)):
            page = doc[i]
            info = {"page": i + 1, "perSideMm": {s: 0.0 for s in SIDES}, "recoverable": False, "reason": ""}
            if _has_bleed_boxes(page):
                info["reason"] = "The file already has bleed boxes; the existing-bleed path keeps them."
            elif page.rotation % 360:
                info["reason"] = "Rotated pages are left to the normal bleed options."
            elif abs(page.cropbox.width - page.mediabox.width) > 1.5 or abs(page.cropbox.height - page.mediabox.height) > 1.5:
                info["reason"] = "The page has a CropBox smaller than its MediaBox."
            else:
                pr = page.rect
                art = _art_rect(page)
                if art.is_empty:
                    info["reason"] = "Nothing is drawn on the page."
                else:
                    per = {
                        "top": max(0.0, (pr.y0 - art.y0) / MM),
                        "bottom": max(0.0, (art.y1 - pr.y1) / MM),
                        "left": max(0.0, (pr.x0 - art.x0) / MM),
                        "right": max(0.0, (art.x1 - pr.x1) / MM),
                    }
                    clipped = _clipped_sides(page, pr)
                    for s in clipped:
                        if per[s] > 0:
                            per[s] = 0.0
                    info["perSideMm"] = {s: round(v, 2) for s, v in per.items()}
                    info["clippedSides"] = sorted(clipped)
                    reach = [s for s in SIDES if per[s] >= need]
                    if reach:
                        any_found = True
                    if len(reach) < 4:
                        short = [s for s in SIDES if s not in reach]
                        if clipped & set(short):
                            info["reason"] = "The artwork is clipped exactly at the page edge on " + ", ".join(sorted(clipped & set(short))) + "."
                        else:
                            info["reason"] = f"Hidden artwork reaches {bleed_mm:g} mm only on " + (", ".join(reach) or "no sides") + "."
                    elif verify:
                        slivers = _band_slivers(doc_bytes, i, bleed_mm)
                        info["sliverFraction"] = {s: round(v, 3) for s, v in slivers.items()}
                        bad = [s for s, v in slivers.items() if v > SLIVER_MAX_FRACTION]
                        if bad:
                            info["reason"] = "White slivers would show in the bleed on " + ", ".join(bad) + "."
                        else:
                            info["recoverable"] = True
                            info["reason"] = "Artwork carries on past every edge."
                    else:
                        info["recoverable"] = True
                        info["reason"] = "Artwork carries on past every edge (not render-verified)."
            for s in SIDES:
                v = info["perSideMm"][s]
                mins[s] = v if mins[s] is None else min(mins[s], v)
            all_ok = all_ok and info["recoverable"]
            result["pages"].append(info)
        result["perSideMm"] = {s: round(mins[s] or 0.0, 2) for s in SIDES}
        result["found"] = any_found
        result["recoverable"] = bool(all_ok and result["pages"]) and len(doc) <= max_pages
        if len(doc) > max_pages:
            result["reason"] = f"Only the first {max_pages} pages were checked, so hidden bleed is not used automatically."
        elif result["recoverable"]:
            result["reason"] = "Artwork carries on past every edge on every page."
        else:
            result["reason"] = next((p["reason"] for p in result["pages"] if not p["recoverable"]), "")
        return result
    except Exception as exc:
        result["reason"] = f"Hidden bleed check failed: {exc}"
        result["recoverable"] = False
        return result
    finally:
        doc.close()


def friendly_finding(info: dict) -> dict:
    """Shape for the quick check / customer report."""
    per = info.get("perSideMm") or {}
    if info.get("recoverable"):
        message = FRIENDLY_FOUND
    elif info.get("found"):
        message = ("Your design has some extra background past the edge, but not all the way round, "
                   "so we'll add the bleed for you instead.")
    else:
        message = ""
    return {"found": bool(info.get("found")), "recoverable": bool(info.get("recoverable")),
            "perSideMm": per, "reason": info.get("reason", ""), "message": message}


def recover_hidden_bleed(pdf_in: str, pdf_out: str, bleed_mm: float = DEFAULT_BLEED_MM,
                         info: dict | None = None) -> dict:
    """Grow MediaBox/BleedBox to trim + bleed and set TrimBox to the original page. No re-render."""
    try:
        info = info or detect_hidden_bleed(pdf_in, bleed_mm)
        if not info.get("recoverable"):
            return {"recovered": False, "reason": info.get("reason", "Not recoverable."), "detect": info}
        doc = fitz.open(pdf_in)
        try:
            trim_mm = [round(doc[0].mediabox.width / MM, 2), round(doc[0].mediabox.height / MM, 2)]
            b = bleed_mm * MM
            for page in doc:
                _set_boxes(doc, page, b)
            doc.save(pdf_out, garbage=0, deflate=False)
        finally:
            doc.close()
        return {"recovered": True, "output": pdf_out, "trimMm": trim_mm, "reason": info.get("reason", ""), "detect": info}
    except Exception as exc:
        return {"recovered": False, "reason": f"Recovery failed: {exc}"}


def maybe_recover_for_pipeline(pdf_in: str, out_dir: str, choice: str = "use",
                               bleed_mm: float = DEFAULT_BLEED_MM) -> tuple[str, dict]:
    """
    Pipeline helper. Returns (path_to_use, report). Never raises: any failure returns pdf_in.
    Does nothing unless FLYERZ_HIDDEN_BLEED=1 and choice is 'use'.
    """
    report = {"enabled": hidden_bleed_enabled(), "choice": choice, "recovered": False}
    if not report["enabled"] or choice == "ignore":
        return pdf_in, report
    try:
        out_dir = os.path.abspath(out_dir)  # a relative dir broke the later open() when cwd differed
        os.makedirs(out_dir, exist_ok=True)
        base = os.path.splitext(os.path.basename(pdf_in))[0]
        out = os.path.join(out_dir, f"{base}_hidden_bleed.pdf")
        rec = recover_hidden_bleed(pdf_in, out, bleed_mm)
        report.update({"recovered": bool(rec.get("recovered")), "reason": rec.get("reason", "")})
        if rec.get("recovered"):
            return out, report
    except Exception as exc:
        report["reason"] = f"Recovery failed: {exc}"
    return pdf_in, report


# ---------------------------------------------------------------- previews

def _render_bgr(page, dpi: float):
    import cv2
    pix = page.get_pixmap(dpi=dpi, alpha=False, colorspace=fitz.csRGB)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3]
    return cv2.cvtColor(img.copy(), cv2.COLOR_RGB2BGR)


def edge_previews(pdf_path: str, bleed_mm: float = DEFAULT_BLEED_MM, dpi: float = 40,
                  out_dir: str | None = None) -> dict:
    """
    Two small previews of page 1 with the trim line drawn on (via bleed_preview.draw_bleed_preview):
      use    - the page grown by the hidden bleed (real artwork in the band)
      ignore - the visible page only; the band is hatched to show "your bleed options fill this"
    Returns data URLs, and writes PNGs when out_dir is given.
    """
    import cv2
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from bleed_preview import draw_bleed_preview

    src = fitz.open(pdf_path)
    try:
        data = src.tobytes()
    finally:
        src.close()
    tmp = fitz.open("pdf", data)
    try:
        page = tmp[0]
        before = _render_bgr(page, dpi)
        _set_boxes(tmp, page, bleed_mm * MM)
        after = _render_bgr(tmp[0], dpi)
    finally:
        tmp.close()
    b = max(1, int(round(bleed_mm / 25.4 * dpi)))
    pad = cv2.copyMakeBorder(before, b, b, b, b, cv2.BORDER_CONSTANT, value=(235, 235, 235))
    hatch = pad.copy()
    for k in range(-pad.shape[0], pad.shape[1], 8):
        cv2.line(hatch, (k, 0), (k + pad.shape[0], pad.shape[0]), (200, 200, 200), 1)
    mask = np.ones(pad.shape[:2], bool)
    mask[b:-b, b:-b] = False
    pad[mask] = hatch[mask]
    use_img = draw_bleed_preview(after, bleed_mm, dpi)
    ignore_img = draw_bleed_preview(pad, bleed_mm, dpi)
    out = {}
    for name, img in (("use", use_img), ("ignore", ignore_img)):
        ok, buf = cv2.imencode(".png", img)
        if not ok:
            continue
        out[name] = "data:image/png;base64," + base64.b64encode(buf.tobytes()).decode("ascii")
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
            path = os.path.join(out_dir, f"{os.path.splitext(os.path.basename(pdf_path))[0]}_{name}.png")
            cv2.imwrite(path, img)
            out[name + "Path"] = path
    return out


def quick_check_finding(pdf_path: str, bleed_mm: float = DEFAULT_BLEED_MM, with_previews: bool = True) -> dict | None:
    """Used by quick_check.py / quick_print_check paths. None when the flag is off."""
    if not hidden_bleed_enabled():
        return None
    info = detect_hidden_bleed(pdf_path, bleed_mm)
    finding = friendly_finding(info)
    if finding["recoverable"] and with_previews:
        try:
            finding["previews"] = edge_previews(pdf_path, bleed_mm)
        except Exception as exc:
            finding["previewError"] = str(exc)
    finding["options"] = ["use", "ignore"] if finding["recoverable"] else []
    finding["defaultChoice"] = "use" if finding["recoverable"] else "ignore"
    return finding


def main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["detect", "recover", "preview"])
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--bleed-mm", type=float, default=DEFAULT_BLEED_MM)
    a = ap.parse_args(argv)
    if a.cmd == "detect":
        out = detect_hidden_bleed(a.paths[0], a.bleed_mm)
    elif a.cmd == "recover":
        out = recover_hidden_bleed(a.paths[0], a.paths[1], a.bleed_mm)
    else:
        out = edge_previews(a.paths[0], a.bleed_mm, out_dir=a.paths[1])
        out = {k: v for k, v in out.items() if k.endswith("Path")}
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
