#!/usr/bin/env python3
"""
PyMuPDF-only repair of PDF page boxes (ingest + smart_bleed shared).
CropBox / TrimBox / BleedBox must stay within MediaBox to avoid pre-flight and pipeline errors.
"""
import json
import os
import sys

import fitz  # PyMuPDF


def aggressive_sanitize_open_document_boxes(doc: "fitz.Document") -> None:
    """
    Call immediately after fitz.open(...) before get_pixmap / transforms.
    Forces CropBox inside MediaBox and sets BleedBox/TrimBox to match CropBox so PyMuPDF
    does not raise \"CropBox not in MediaBox\" during raster strategies (e.g. stretch).
    Write order: mediabox → cropbox → bleedbox → trimbox.
    """
    for i in range(doc.page_count):
        page = doc.load_page(i)
        try:
            mb = fitz.Rect(page.mediabox)
        except Exception:
            continue
        try:
            cr = fitz.Rect(page.cropbox)
        except Exception:
            cr = fitz.Rect(mb)
        try:
            if not mb.contains(cr):
                cr = fitz.Rect(mb)
                sys.stderr.write(
                    f"[GEOM-SANITIZE] Page {i + 1}: CropBox not in MediaBox — aligned Crop/Bleed/Trim to MediaBox "
                    f"({mb.width:.2f}x{mb.height:.2f} pt).\n"
                )
        except Exception:
            cr = fitz.Rect(mb)
        try:
            page.set_mediabox(mb)
            page.set_cropbox(cr)
            page.set_bleedbox(cr)
            page.set_trimbox(cr)
        except Exception as ex:
            sys.stderr.write(f"[GEOM-SANITIZE] Page {i + 1}: aggressive box reset failed (non-fatal): {ex}\n")


def _pdf_rect_fully_inside_mediabox(r: "fitz.Rect", mb: "fitz.Rect", tol: float = 0.05) -> bool:
    """True if r is non-degenerate and fully contained in mb (PDF box sanity)."""
    try:
        if r.width <= 0.05 or r.height <= 0.05:
            return False
    except Exception:
        return False
    return (
        r.x0 >= mb.x0 - tol
        and r.y0 >= mb.y0 - tol
        and r.x1 <= mb.x1 + tol
        and r.y1 <= mb.y1 + tol
    )


def sanitize_pdf_box_geometry(input_path: str, output_path: str) -> bool:
    """
    Repair CropBox vs MediaBox and clamp TrimBox/BleedBox to MediaBox.
    Saves to output_path only when changes were applied.
    """
    doc = fitz.open(input_path)
    modified = False
    tol = 0.05

    try:
        for i in range(doc.page_count):
            page = doc.load_page(i)
            mb = fitz.Rect(page.mediabox)

            try:
                cb = fitz.Rect(page.cropbox)
            except Exception:
                cb = fitz.Rect(mb)

            if not _pdf_rect_fully_inside_mediabox(cb, mb, tol):
                page.set_cropbox(mb)
                modified = True
                sys.stderr.write(
                    f"[GEOM-SANITIZE] Page {i + 1}: CropBox invalid or outside MediaBox — CropBox := MediaBox "
                    f"({mb.width:.2f}x{mb.height:.2f} pt).\n"
                )

            try:
                tb = fitz.Rect(page.trimbox)
            except Exception:
                tb = fitz.Rect(mb)

            if not _pdf_rect_fully_inside_mediabox(tb, mb, tol):
                fixed_tb = tb.intersect(mb)
                if fixed_tb.width <= 0.05 or fixed_tb.height <= 0.05:
                    fixed_tb = fitz.Rect(mb)
                page.set_trimbox(fixed_tb)
                modified = True
                sys.stderr.write(f"[GEOM-SANITIZE] Page {i + 1}: TrimBox clamped to MediaBox intersection.\n")

            try:
                bb = fitz.Rect(page.bleedbox)
            except Exception:
                bb = fitz.Rect(mb)

            if not _pdf_rect_fully_inside_mediabox(bb, mb, tol):
                fixed_bb = bb.intersect(mb)
                if fixed_bb.width <= 0.05 or fixed_bb.height <= 0.05:
                    fixed_bb = fitz.Rect(mb)
                page.set_bleedbox(fixed_bb)
                modified = True
                sys.stderr.write(f"[GEOM-SANITIZE] Page {i + 1}: BleedBox clamped to MediaBox intersection.\n")

        if modified:
            doc.save(output_path, garbage=4, deflate=True)
    finally:
        doc.close()

    return modified


def sanitize_pdf_geometry_inplace(target_path: str) -> bool:
    """Write sanitized PDF over ``target_path`` when fixes apply; returns True if modified."""
    tmp = target_path + ".__geom_sanitize__.pdf"
    try:
        if sanitize_pdf_box_geometry(target_path, tmp):
            os.replace(tmp, target_path)
            return True
        if os.path.isfile(tmp):
            os.unlink(tmp)
        return False
    except Exception:
        if os.path.isfile(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise


def main() -> None:
    if len(sys.argv) < 2:
        print(json.dumps({"ok": False, "error": "Usage: pdf_geometry_sanitize.py <pdf_path>"}))
        sys.exit(1)
    pdf_path = sys.argv[1]
    try:
        modified = sanitize_pdf_geometry_inplace(pdf_path)
        print(json.dumps({"ok": True, "modified": modified}))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
