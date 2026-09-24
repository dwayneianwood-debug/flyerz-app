#!/usr/bin/env python3
"""
Adobe Illustrator (.ai) and EPS intake.

PDF-compatible .ai files (saved with "Create PDF Compatible File") are PDF
bytes. They are detected by signature and passed through unchanged so vectors,
fonts, spot colours, and artboards stay intact.

Legacy PostScript .ai files and .eps files are distilled to PDF with
Ghostscript (EPS uses -dEPSCrop). Memory leashes match the rest of the
prepress pipeline: BufferSpace and MaxBitmap at 50MB, one rendering thread,
temp files under FAI_TEMP_DIR.

Files that are neither PDF nor readable PostScript return a customer-facing
re-save message instead of a generic parser error.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import glob as globmod

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fai_temp_utils import init_fai_temp_dir

try:
    import contextlib
    import io
    _fitz_import_log = io.StringIO()
    with contextlib.redirect_stdout(_fitz_import_log):
        import fitz
except ImportError:
    fitz = None

FAI_TEMP_DIR = init_fai_temp_dir()

# Immutable prepress memory leashes. Do not raise these.
GS_NUM_RENDERING_THREADS = 1
GS_BUFFER_SPACE = 50000000
GS_MAX_BITMAP = 50000000
GS_BAND_BUFFER_SPACE = 50000000

ILLUSTRATOR_RESAVE_MESSAGE = (
    'This Illustrator file can\'t be read. It was saved without PDF compatibility, '
    'or it has no PostScript artwork we can open. In Adobe Illustrator choose '
    'File > Save As > Illustrator and tick "Create PDF Compatible File", '
    "or export as PDF/X-1a, then upload that file."
)

PDF_MAGIC = b"%PDF-"
DOS_EPS_MAGIC = b"\xC5\xD0\xD3\xC6"
_SEP_NAME = re.compile(rb"/Separation\s*/([^\s\[\]<>/]+)")
_OPI_BLOCK = re.compile(rb"/OPI\b.{0,500}", re.DOTALL)
_NAME_IN_PARENS = re.compile(rb"\((?:\\.|[^)\\])*\)")


def find_gs_binary() -> str:
    gs_path = shutil.which("gs")
    if gs_path:
        return gs_path
    nix_matches = globmod.glob("/nix/store/*/bin/gs")
    for p in sorted(nix_matches, reverse=True):
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return "gs"


def ghostscript_pdf_command(input_path: str, output_path: str, *, eps_crop: bool) -> list:
    """pdfwrite command. Vectors stay vectors; memory caps match the rest of FAI."""
    cmd = [
        find_gs_binary(),
        "-q",
        "-o", output_path,
        "-sDEVICE=pdfwrite",
        "-dNOPAUSE",
        "-dBATCH",
        "-dCompatibilityLevel=1.4",
        "-dAutoRotatePages=/None",
        f"-dNumRenderingThreads={GS_NUM_RENDERING_THREADS}",
        f"-dBufferSpace={GS_BUFFER_SPACE}",
        f"-dMaxBitmap={GS_MAX_BITMAP}",
        f"-dBandBufferSpace={GS_BAND_BUFFER_SPACE}",
        "-dBandHeight=0",
    ]
    if eps_crop:
        cmd.append("-dEPSCrop")
    cmd.extend([
        "-c",
        "<< /MaxBitmap 50000000 /BufferSize 50000000 >> setuserparams "
        "<< /HWResolution [300 300] >> setpagedevice",
        "-f",
        input_path,
    ])
    return cmd


def _read_head(path: str, n: int = 8192) -> bytes:
    with open(path, "rb") as f:
        data = f.read(n)
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    return data


def _is_private_illustrator_stub(path: str, head: bytes) -> bool:
    """
    Modern .ai saved with PDF compatibility off is a short PostScript header,
    an early %%EOF, then private binary. Ghostscript cannot draw that artwork.
    """
    eof_at = head.find(b"%%EOF")
    if eof_at < 0:
        return False
    try:
        size = os.path.getsize(path)
    except OSError:
        return False
    # A real EPS/PS file ends at %%EOF (only a little trailing whitespace).
    if size - eof_at < 64:
        return False
    preamble = head[:eof_at]
    illustrator = (
        b"%AI5_FileFormat" in preamble
        or b"%%AI8_CreatorVersion" in preamble
        or b"Adobe Illustrator" in preamble
    )
    if not illustrator:
        return False
    with open(path, "rb") as f:
        f.seek(eof_at + len(b"%%EOF"))
        tail = f.read(256)
    stripped = tail.lstrip()
    if stripped.startswith(b"%") or b"showpage" in tail:
        return False
    return True


def _fitz_can_open_pdf(path: str) -> bool:
    if fitz is None:
        return False
    try:
        doc = fitz.open(path)
    except Exception:
        return False
    try:
        if getattr(doc, "is_pdf", True) and doc.page_count > 0:
            _ = doc[0].rect
            return True
        return False
    except Exception:
        return False
    finally:
        doc.close()


def classify_artwork(path: str) -> str:
    """
    Return 'pdf', 'postscript', or 'unreadable' from file bytes.
    Extension is ignored on purpose.
    """
    head = _read_head(path, 8192)
    if not head:
        return "unreadable"
    window = head[:1024]
    if PDF_MAGIC in window:
        return "pdf" if _fitz_can_open_pdf(path) else "unreadable"
    is_ps = (
        head.startswith(b"%!PS")
        or head.startswith(b"%!Adobe")
        or head.startswith(DOS_EPS_MAGIC)
        or b"EPSF-" in head[:256]
    )
    if is_ps:
        if _is_private_illustrator_stub(path, head):
            return "unreadable"
        return "postscript"
    if _fitz_can_open_pdf(path):
        return "pdf"
    return "unreadable"


def _pdf_page_count(path: str) -> int:
    if fitz is None:
        raise RuntimeError("PyMuPDF is required to read Illustrator PDF files.")
    doc = fitz.open(path)
    try:
        if doc.page_count < 1:
            raise RuntimeError("PDF has no pages")
        _ = doc[0].rect
        return int(doc.page_count)
    finally:
        doc.close()


def _pdf_has_marks(path: str) -> bool:
    if fitz is None:
        return os.path.getsize(path) > 200
    doc = fitz.open(path)
    try:
        if doc.page_count < 1:
            return False
        for page in doc:
            if (page.get_text() or "").strip():
                return True
            if page.get_images():
                return True
            try:
                if page.get_drawings():
                    return True
            except Exception:
                pass
            try:
                contents = page.read_contents() or b""
            except Exception:
                contents = b""
            if len(contents) > 16:
                return True
        return False
    finally:
        doc.close()


def _gs_env() -> dict:
    env = os.environ.copy()
    tmp = FAI_TEMP_DIR
    try:
        os.makedirs(tmp, exist_ok=True)
        probe = os.path.join(tmp, ".tmpdir_probe")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.unlink(probe)
        env["TMPDIR"] = tmp
        if os.name == "nt":
            env["TEMP"] = tmp
            env["TMP"] = tmp
    except Exception:
        pass
    return env


def convert_postscript_to_pdf(input_path: str, output_path: str, *, eps_crop: bool) -> None:
    """Distill PostScript/EPS to a vector PDF. Raises RuntimeError on failure."""
    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
    cmd = ghostscript_pdf_command(input_path, output_path, eps_crop=eps_crop)
    stderr_log = tempfile.NamedTemporaryFile(
        suffix="_gs_stderr.log", delete=False, mode="w", dir=FAI_TEMP_DIR
    ).name
    try:
        with open(stderr_log, "w", encoding="utf-8", errors="replace") as stderr_f:
            result = subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=stderr_f,
                timeout=120,
                env=_gs_env(),
            )
        stderr_tail = ""
        try:
            with open(stderr_log, "r", encoding="utf-8", errors="replace") as f:
                stderr_tail = f.read()[-800:]
        except Exception:
            pass
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Ghostscript timed out while converting the Illustrator/EPS file.") from exc
    finally:
        try:
            os.unlink(stderr_log)
        except Exception:
            pass

    if result.returncode != 0 or not os.path.exists(output_path) or os.path.getsize(output_path) < 64:
        sys.stderr.write(f"[FAI] Illustrator GS convert failed: {stderr_tail}\n")
        raise RuntimeError("Ghostscript could not convert this file to PDF.")
    if not _pdf_has_marks(output_path):
        sys.stderr.write(f"[FAI] Illustrator GS convert produced an empty PDF: {stderr_tail}\n")
        raise RuntimeError("Converted PDF has no artwork.")


def _should_eps_crop(path: str, source_ext: str) -> bool:
    ext = (source_ext or "").lower()
    if not ext.startswith("."):
        ext = f".{ext}" if ext else ""
    if ext == ".eps":
        return True
    head = _read_head(path, 512)
    return b"EPSF-" in head


def prepare_illustrator_file(input_path: str, source_ext: str) -> dict:
    """
    Make input_path readable as a PDF in place.
    PDF-compatible bytes are left untouched. PostScript is replaced with a PDF.
    """
    if not os.path.isfile(input_path):
        return {"success": False, "code": "illustrator_resave", "error": ILLUSTRATOR_RESAVE_MESSAGE}

    ext = (source_ext or os.path.splitext(input_path)[1] or "").lower()
    if ext and not ext.startswith("."):
        ext = "." + ext

    kind = classify_artwork(input_path)
    if kind == "unreadable":
        return {"success": False, "code": "illustrator_resave", "error": ILLUSTRATOR_RESAVE_MESSAGE}

    if kind == "pdf":
        try:
            pages = _pdf_page_count(input_path)
        except Exception as exc:
            sys.stderr.write(f"[FAI] PDF-compatible Illustrator open failed: {exc}\n")
            return {"success": False, "code": "illustrator_resave", "error": ILLUSTRATOR_RESAVE_MESSAGE}
        return {
            "success": True,
            "kind": "pdf",
            "pageCount": pages,
            "outputPath": input_path,
            "preservedVectors": True,
            "sourceExt": ext,
        }

    eps_crop = _should_eps_crop(input_path, ext)
    out_tmp = tempfile.NamedTemporaryFile(suffix="_ai_intake.pdf", delete=False, dir=FAI_TEMP_DIR).name
    try:
        convert_postscript_to_pdf(input_path, out_tmp, eps_crop=eps_crop)
        pages = _pdf_page_count(out_tmp)
        shutil.copyfile(out_tmp, input_path)
    except Exception as exc:
        sys.stderr.write(f"[FAI] PostScript intake failed: {exc}\n")
        return {"success": False, "code": "illustrator_resave", "error": ILLUSTRATOR_RESAVE_MESSAGE}
    finally:
        try:
            os.unlink(out_tmp)
        except Exception:
            pass

    return {
        "success": True,
        "kind": "postscript",
        "pageCount": pages,
        "outputPath": input_path,
        "preservedVectors": True,
        "epsCrop": eps_crop,
        "sourceExt": ext,
    }


def extract_pdf_page(input_path: str, output_path: str, page_index: int) -> dict:
    """Copy one artboard/page into its own PDF, keeping vectors and colours."""
    if fitz is None:
        return {"success": False, "error": "PyMuPDF is required to choose an artboard."}
    doc = fitz.open(input_path)
    try:
        if page_index < 0 or page_index >= doc.page_count:
            return {
                "success": False,
                "error": f"Artboard {page_index + 1} is not in this file ({doc.page_count} artboard(s)).",
            }
        out = fitz.open()
        try:
            out.insert_pdf(doc, from_page=page_index, to_page=page_index)
            out.save(output_path, garbage=4, deflate=True)
        finally:
            out.close()
    finally:
        doc.close()
    return {"success": True, "outputPath": output_path, "pageIndex": page_index, "pageCount": 1}


def render_preview_png(pdf_path: str, png_path: str, page_index: int = 0) -> dict:
    if fitz is None:
        return {"success": False, "error": "PyMuPDF is required to preview this file."}
    doc = fitz.open(pdf_path)
    try:
        if doc.page_count < 1:
            return {"success": False, "error": ILLUSTRATOR_RESAVE_MESSAGE}
        if page_index < 0 or page_index >= doc.page_count:
            page_index = 0
        page = doc[page_index]
        longest = max(float(page.rect.width), float(page.rect.height), 1.0)
        zoom = max(1.0, min(3.0, 2400.0 / longest))
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        pix.save(png_path)
        return {
            "success": True,
            "previewPath": png_path,
            "width": pix.width,
            "height": pix.height,
            "pageCount": doc.page_count,
            "page": page_index,
        }
    finally:
        doc.close()


def _decode_pdf_name(raw: bytes) -> str:
    text = raw.decode("latin1", errors="replace")
    if text.startswith("/"):
        text = text[1:]
    out = []
    i = 0
    while i < len(text):
        if text[i] == "#" and i + 2 < len(text):
            try:
                out.append(chr(int(text[i + 1:i + 3], 16)))
                i += 3
                continue
            except ValueError:
                pass
        out.append(text[i])
        i += 1
    return "".join(out).strip()


def _font_is_embedded(font) -> bool:
    """PyMuPDF reports a missing font file as ext 'n/a' or '' and stream xref 0."""
    ext = str(font[1] if len(font) > 1 and font[1] is not None else "").strip().lower()
    stream = 0
    if len(font) > 6:
        try:
            stream = int(font[6] or 0)
        except (TypeError, ValueError):
            stream = 0
    if stream > 0:
        return True
    if ext in ("", "n/a", "none", "-", "not embedded"):
        return False
    return True


def _check_fonts(doc) -> dict:
    missing = []
    seen = set()
    total = 0
    for page in doc:
        try:
            fonts = page.get_fonts(full=True)
        except Exception:
            fonts = []
        for font in fonts:
            total += 1
            name = font[3] if len(font) > 3 and font[3] else "Unknown"
            if _font_is_embedded(font):
                continue
            if name not in seen:
                seen.add(name)
                missing.append(name)
    result = {
        "id": "illustrator_fonts",
        "name": "Fonts Embedded or Outlined",
        "passed": True,
        "message": "",
        "details": "",
        "fixType": "manual",
        "severity": "PASS",
    }
    if total == 0:
        result["message"] = "No live fonts on the artboard — text is outlined, or this file has no text."
        result["details"] = "Outlined type is safe for litho. Live fonts must be embedded or converted to outlines in Illustrator."
        return result
    if missing:
        shown = ", ".join(missing[:5])
        result["passed"] = False
        result["severity"] = "HIGH"
        result["message"] = f"{len(missing)} font(s) are not embedded or outlined: {shown}"
        result["details"] = (
            "In Illustrator, embed the fonts (Type > Find Font, or File > Save with fonts embedded) "
            "or convert the text to outlines (Type > Create Outlines) before uploading."
        )
        return result
    result["message"] = f"{total} font(s) embedded — type is safe for press."
    return result


def _check_spots(raw: bytes) -> dict:
    names = []
    seen = set()
    for match in _SEP_NAME.finditer(raw):
        name = _decode_pdf_name(match.group(1))
        key = name.upper()
        if not name or key in seen:
            continue
        if key in ("ALL", "NONE", "CYAN", "MAGENTA", "YELLOW", "BLACK"):
            continue
        seen.add(key)
        names.append(name)
    result = {
        "id": "illustrator_spots",
        "name": "Spot / Pantone Colours",
        "passed": True,
        "message": "No spot or Pantone colours — process CMYK only.",
        "details": "",
        "fixType": "manual",
        "severity": "PASS",
    }
    if not names:
        return result
    shown = ", ".join(names[:6])
    extra = f" (+{len(names) - 6} more)" if len(names) > 6 else ""
    result["passed"] = False
    result["severity"] = "MANUAL_REVIEW"
    result["message"] = f"Spot or Pantone colour(s) found: {shown}{extra}"
    result["details"] = (
        "Spot plates are kept so the press can print them as extra inks. "
        "If this job is process-CMYK only, convert those swatches to CMYK in Illustrator before uploading."
    )
    return result


def _check_linked_images(doc, raw: bytes) -> dict:
    missing = []
    seen = set()
    for match in _OPI_BLOCK.finditer(raw):
        block = match.group(0)
        for name_match in _NAME_IN_PARENS.finditer(block):
            raw_name = name_match.group(0)[1:-1]
            name = raw_name.decode("latin1", errors="replace").replace("\\", "")
            if name and name not in seen:
                seen.add(name)
                missing.append(name)
    if doc is not None:
        for page in doc:
            try:
                images = page.get_images(full=True)
            except Exception:
                images = []
            for img in images:
                xref = img[0]
                try:
                    info = doc.extract_image(xref)
                    if info and info.get("image"):
                        continue
                except Exception:
                    pass
                label = f"image {xref}"
                if label not in seen:
                    seen.add(label)
                    missing.append(label)
    result = {
        "id": "illustrator_links",
        "name": "Linked Images",
        "passed": True,
        "message": "Images are embedded. No missing linked files.",
        "details": "",
        "fixType": "manual",
        "severity": "PASS",
    }
    if not missing:
        return result
    shown = ", ".join(missing[:4])
    result["passed"] = False
    result["severity"] = "HIGH"
    result["message"] = f"Linked image(s) missing from the file: {shown}"
    result["details"] = (
        "Illustrator is pointing at an image that is not embedded. "
        "In the Links panel, embed the image (or relink it), then save again with "
        '"Create PDF Compatible File" ticked.'
    )
    return result


def illustrator_audit_checks(doc, raw: bytes, page_count: int) -> list:
    """Flags that matter for Illustrator files. Bleed and CMYK stay in quick_check."""
    checks = [
        _check_fonts(doc),
        _check_spots(raw),
        _check_linked_images(doc, raw),
    ]
    if page_count > 1:
        checks.append({
            "id": "illustrator_artboards",
            "name": "Artboards",
            "passed": True,
            "message": (
                f"{page_count} artboards found. Each one is kept as a page — "
                "use the page picker to review them, or choose one before processing."
            ),
            "details": "Illustrator artboards are imported as PDF pages so fonts, spot colours, and vectors stay intact.",
            "fixType": "manual",
            "severity": "PASS",
        })
    return checks


def _fail(message: str) -> dict:
    return {"success": False, "code": "illustrator_resave", "error": message or ILLUSTRATOR_RESAVE_MESSAGE}


def main() -> int:
    if len(sys.argv) < 2:
        print(json.dumps(_fail("Usage: illustrator_intake.py <prepare|extract|preview> ...")))
        return 0

    command = sys.argv[1]
    try:
        if command == "prepare":
            if len(sys.argv) < 3:
                print(json.dumps(_fail("Usage: illustrator_intake.py prepare <input> [ext]")))
                return 0
            ext = sys.argv[3] if len(sys.argv) > 3 else os.path.splitext(sys.argv[2])[1]
            print(json.dumps(prepare_illustrator_file(sys.argv[2], ext)))
            return 0
        if command == "extract":
            if len(sys.argv) < 5:
                print(json.dumps({"success": False, "error": "Usage: illustrator_intake.py extract <input> <output> <pageIndex>"}))
                return 0
            print(json.dumps(extract_pdf_page(sys.argv[2], sys.argv[3], int(sys.argv[4]))))
            return 0
        if command == "preview":
            if len(sys.argv) < 4:
                print(json.dumps({"success": False, "error": "Usage: illustrator_intake.py preview <pdf> <png> [pageIndex]"}))
                return 0
            page = int(sys.argv[4]) if len(sys.argv) > 4 else 0
            print(json.dumps(render_preview_png(sys.argv[2], sys.argv[3], page)))
            return 0
        if command == "audit":
            if len(sys.argv) < 3:
                print(json.dumps({"success": False, "error": "Usage: illustrator_intake.py audit <pdf>"}))
                return 0
            if fitz is None:
                print(json.dumps({"success": False, "error": "PyMuPDF is required to audit this file."}))
                return 0
            doc = fitz.open(sys.argv[2])
            try:
                with open(sys.argv[2], "rb") as raw_f:
                    raw = raw_f.read()
                checks = illustrator_audit_checks(doc, raw, doc.page_count)
                print(json.dumps({"success": True, "checks": checks, "pageCount": doc.page_count}))
            finally:
                doc.close()
            return 0
        if command == "classify":
            if len(sys.argv) < 3:
                print(json.dumps(_fail("Usage: illustrator_intake.py classify <input>")))
                return 0
            print(json.dumps({"success": True, "kind": classify_artwork(sys.argv[2])}))
            return 0
        print(json.dumps(_fail(f"Unknown command: {command}")))
        return 0
    except Exception as exc:
        sys.stderr.write(f"[FAI] illustrator_intake crashed: {exc}\n")
        print(json.dumps(_fail(ILLUSTRATOR_RESAVE_MESSAGE)))
        return 0


if __name__ == "__main__":
    sys.exit(main())
