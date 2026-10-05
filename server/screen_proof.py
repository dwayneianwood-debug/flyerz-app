"""First before-proof. PyMuPDF only, so a cold process does not load OpenCV.

The job page asks for this picture as soon as the original file is saved. Importing
the prepress module pulled in OpenCV and then composited an alpha pixmap in Python,
which on Windows was the minute before the first picture appeared.
"""

from __future__ import annotations

import sys
import time


def write_screen_proof(pdf_path: str, output_png_path: str, dpi: int = 144, only_page: int | None = None) -> dict:
    """Write a 144 dpi RGB proof. only_page is 1-based; None writes every page."""
    import os

    importing = time.perf_counter()
    import pymupdf as fitz
    import_s = time.perf_counter() - importing
    sys.stderr.write(f"[PROOF] import {import_s:.2f}s\n")

    started = time.perf_counter()
    opened = time.perf_counter()
    doc = fitz.open(pdf_path)
    open_s = time.perf_counter() - opened
    sys.stderr.write(f"[PROOF] open {open_s:.2f}s\n")
    try:
        total = len(doc)
        if total < 1:
            return {"success": False, "error": "PDF has no pages", "proofPaths": []}
        base, ext = os.path.splitext(output_png_path)
        if only_page is not None and (only_page < 1 or only_page > total):
            return {"success": False, "error": f"Page {only_page} is outside 1-{total}", "proofPaths": [], "pageCount": total}
        wanted = [only_page] if only_page else list(range(1, total + 1))
        paths = []
        for page_num in wanted:
            page_started = time.perf_counter()
            if total == 1 and only_page is None:
                page_output = output_png_path
            else:
                page_output = f"{base}{page_num}{ext or '.png'}"
            page = doc[page_num - 1]
            # Opaque RGB composites transparency in C. An alpha pixmap plus a numpy mix was the slow part.
            pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72.0, dpi / 72.0), colorspace=fitz.csRGB, alpha=False)
            draw_s = time.perf_counter() - page_started
            pix.set_dpi(dpi, dpi)
            pix.save(page_output)
            save_s = time.perf_counter() - page_started - draw_s
            paths.append(page_output)
            sys.stderr.write(
                f"[PROOF] page {page_num}/{total} {pix.width}x{pix.height} "
                f"draw {draw_s:.2f}s save {save_s:.2f}s open {open_s:.2f}s\n"
            )
            del pix
        sys.stderr.write(f"[PROOF] done {time.perf_counter() - started:.2f}s pages {len(paths)}/{total}\n")
        return {
            "success": True,
            "proofPath": paths[0],
            "proofPaths": paths,
            "pageCount": total,
            "isBlank": False,
        }
    finally:
        doc.close()


def main() -> int:
    if len(sys.argv) < 3:
        sys.stderr.write("usage: screen_proof.py INPUT OUTPUT [PAGE]\n")
        return 2
    page = int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3] else None
    result = write_screen_proof(sys.argv[1], sys.argv[2], dpi=144, only_page=page)
    if not result.get("success"):
        sys.stderr.write(str(result.get("error") or "proof failed") + "\n")
        return 1
    sys.stdout.write(f"PAGES {int(result.get('pageCount') or 0)}\n")
    sys.stdout.write("FILE " + str(result.get("proofPath") or "") + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
