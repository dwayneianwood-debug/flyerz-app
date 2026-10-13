"""Ian's bleed round. Real files, then every product and starting state.

Crops of all four edges are written under /opt/cursor/artifacts/bleed-crops/.
A row fails when a bleed edge does not continue the artwork, when a grey band
appears, or when a correct partial bleed is turned amber.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("VECTOR_REBUILD", "0")

from bleed_builder import check_page_array, check_press_pdf  # noqa: E402
from press_ready_engine import compile_raster_canvas, compile_vector_press, plate_exceeds_memory  # noqa: E402
from quick_print import decide_light, make_print_ready  # noqa: E402

MM = 72.0 / 25.4
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "ian_2026-10-13")
CROP_DIR = "/opt/cursor/artifacts/bleed-crops"
ROWS: list[dict] = []
KINDS = ("dark", "light", "gradient", "pattern", "photo")


def _products() -> list[dict]:
    import json

    with open(os.path.join(ROOT, "shared", "quick-print-products.json"), encoding="utf-8") as handle:
        return json.load(handle)


def _px(mm: float, dpi: float = 300.0) -> int:
    return max(1, int(round(float(mm) / 25.4 * float(dpi))))


def _add(kind: str, path: str, size: str, ok: bool, detail: str) -> None:
    ROWS.append({
        "kind": kind,
        "path": path,
        "size": size,
        "result": "PASS" if ok else "FAIL",
        "detail": detail,
    })
    print(f"[{'PASS' if ok else 'FAIL'}] {kind} | {path} | {size} | {detail}", flush=True)


def _art(height: int, width: int, kind: str) -> np.ndarray:
    y = np.linspace(0.0, 1.0, max(height, 1), dtype=np.float32)[:, None]
    x = np.linspace(0.0, 1.0, max(width, 1), dtype=np.float32)[None, :]
    if kind == "photo":
        tone = np.array([76, 92, 38], np.float32) * (1.0 - y[:, :, None]) + np.array([214, 176, 46], np.float32) * y[:, :, None]
        image = np.broadcast_to(tone, (height, width, 3)).copy()
    elif kind == "gradient":
        tone = np.array([48, 92, 64], np.float32) * (1.0 - x[:, :, None]) + np.array([214, 168, 48], np.float32) * x[:, :, None]
        image = np.broadcast_to(tone, (height, width, 3)).copy()
    elif kind == "pattern":
        # Vertical stripes. The outer 4 mm of each side is one colour, wider than
        # the sample, so a narrow page does not end on a 1 mm stripe.
        stripe = ((np.arange(width) // 96) % 2).astype(np.float32)
        tone = np.array([36, 78, 52], np.float32) * (1.0 - stripe[:, None]) + np.array([210, 168, 64], np.float32) * stripe[:, None]
        image = np.broadcast_to(tone[None, :, :], (height, width, 3)).copy()
        solid = min(_px(8.0), max(1, width // 3))
        image[:, :solid] = image[:, 0][:, None, :]
        image[:, -solid:] = image[:, -1][:, None, :]
    elif kind == "light":
        image = np.full((height, width, 3), (228, 224, 214), np.float32)
    else:
        image = np.full((height, width, 3), (18, 24, 16), np.float32)
    image = np.clip(np.round(image), 0, 255).astype(np.uint8)
    # A logo sits 8 mm inside, clear of the 5 mm bleed sample.
    logo = min(18, max(4, width // 12))
    x0 = max(0, width - _px(8.0) - logo)
    image[8:8 + logo, x0:x0 + logo] = (180, 40, 36)
    return image


def _with_bleed(trim_w: float, trim_h: float, extra_mm: float, kind: str, light_rim: bool = False) -> np.ndarray:
    tw, th = _px(trim_w), _px(trim_h)
    art = _art(th, tw, kind)
    extra = _px(extra_mm) if extra_mm > 0 else 0
    if extra < 1:
        return art
    canvas = np.pad(art, ((extra, extra), (extra, extra), (0, 0)), mode="edge")
    if light_rim:
        rim = (246, 220, 140)
        canvas[:, :extra] = rim
        canvas[:, -extra:] = rim
        canvas[:extra, :] = rim
        canvas[-extra:, :] = rim
        # The same light colour continues a millimetre inside the trim.
        into = min(_px(1.2), tw // 5, th // 5)
        canvas[extra:extra + into, :] = rim
        canvas[-(extra + into):-extra, :] = rim
        canvas[:, extra:extra + into] = rim
        canvas[:, -(extra + into):-extra] = rim
    return canvas


def _score_canvas(canvas: np.ndarray, bleed_px: int) -> dict:
    rgb = np.ascontiguousarray(canvas[:, :, :3])
    height, width = rgb.shape[:2]
    trim = (bleed_px, bleed_px, width - bleed_px, height - bleed_px)
    media = (0, 0, width, height)
    return check_page_array(rgb, trim, media, 300.0 / 72.0)


def _save_edges(rgb: np.ndarray, bleed_px: int, stem: str) -> list[str]:
    import time

    from PIL import Image

    os.makedirs(CROP_DIR, exist_ok=True)
    height, width = rgb.shape[:2]
    span = max(bleed_px + 4, int(round(bleed_px * 1.6)))
    windows = {
        "top": rgb[:span, :],
        "bottom": rgb[-span:, :],
        "left": rgb[:, :span],
        "right": rgb[:, -span:],
    }
    paths = []
    for side, crop in windows.items():
        view = crop
        along = 1 if side in ("top", "bottom") else 0
        if view.shape[along] > 900:
            start = max(0, view.shape[along] // 2 - 450)
            view = view[:, start:start + 900] if along == 1 else view[start:start + 900, :]
        path = os.path.join(CROP_DIR, f"{stem}-{side}.png")
        array = np.ascontiguousarray(view[:, :, :3])
        for attempt in range(4):
            try:
                Image.fromarray(array).save(path)
                break
            except OSError:
                if attempt == 3:
                    raise
                time.sleep(0.15 * (attempt + 1))
        paths.append(path)
    return paths


def _notes_ok(edges: list) -> bool:
    sides = {str(edge.get("side")) for edge in edges}
    if sides != {"top", "bottom", "left", "right"}:
        return False
    for edge in edges:
        note = str(edge.get("note") or "")
        if not note or ":" not in note:
            return False
    return True


def _grey_fraction(rgb: np.ndarray, bleed_px: int) -> float:
    outer_n = max(1, int(round(bleed_px * 0.64)))
    band = rgb[:, -outer_n:].astype(np.float32)
    grey = np.array([218, 215, 211], np.float32)
    return float((np.linalg.norm(band - grey, axis=2) < 12.0).mean())


def _pdf_rgb(path: str) -> tuple[np.ndarray, int]:
    import pymupdf as fitz

    doc = fitz.open(path)
    try:
        page = doc[0]
        long_pt = max(page.rect.width, page.rect.height, 1.0)
        dpi = min(120.0, 1400.0 * 72.0 / long_pt)
        dpi = max(72.0, dpi)
        pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72.0, dpi / 72.0), alpha=False, colorspace=fitz.csRGB)
        rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, 3).copy()
        bleed_px = max(1, int(round(5.0 / 25.4 * dpi)))
        return rgb, bleed_px
    finally:
        doc.close()


def _write_flat_pdf(path: str, trim_w: float, trim_h: float, extra_mm: float, colour: tuple[int, int, int]) -> None:
    import pymupdf as fitz

    w_pt = (trim_w + 2.0 * extra_mm) * MM
    h_pt = (trim_h + 2.0 * extra_mm) * MM
    doc = fitz.open()
    page = doc.new_page(width=w_pt, height=h_pt)
    page.draw_rect(page.rect, color=None, fill=tuple(channel / 255.0 for channel in colour))
    if extra_mm >= 0.4:
        inset = extra_mm * MM
        page.set_trimbox(fitz.Rect(inset, inset, w_pt - inset, h_pt - inset))
    doc.save(path)
    doc.close()


class IanBleedRoundTest(unittest.TestCase):
    def test_real_files_and_the_size_matrix(self):
        self._real_files()
        self._partial_light_edges()
        self._matrix()
        self._wrong_size_pipeline()
        failed = [row for row in ROWS if row["result"] != "PASS"]
        _write_table()
        self.assertFalse(failed, f"{len(failed)} bleed rows failed")

    def _real_files(self) -> None:
        folder = tempfile.mkdtemp(prefix="ian-bleed-")
        specs = [
            ("vob_original.pdf", 148.0, 210.0, "A5", True),
            ("7d28b408_Copy_of_A5_Leaflet_-_Our_Priorities_to_Fix_Joburg.pdf", 148.0, 210.0, "A5 double", True),
            ("d9c87a15_A5_3mm_bleed_no_trimbox.pdf", 148.0, 210.0, "A5 partial", True),
            ("karseell_A5_double_sided_3mm_trimbox.pdf", 148.0, 210.0, "A5 partial", True),
        ]
        for name, trim_w, trim_h, size, expect_ok in specs:
            src = os.path.join(FIXTURE, name)
            dest = os.path.join(folder, name + ".press.pdf")
            try:
                built = compile_vector_press(src, dest, trim_w, trim_h, 5.0)
                quality = check_press_pdf(dest, trim_w, trim_h, 5.0)
                rgb, bleed_px = _pdf_rgb(dest)
                crops = _save_edges(rgb, bleed_px, os.path.splitext(name)[0][:40])
                edges = (built.get("report") or {}).get("edges") or []
                grey = _grey_fraction(rgb, bleed_px)
                ok = bool(built.get("used")) and bool(quality.get("ok")) == expect_ok and grey < 0.01 and _notes_ok(edges)
                detail = f"dE { _worst_delta(quality) }; grey {grey:.3f}; {edges[0].get('note') if edges else ''} ; crops {len(crops)}"
                if not quality.get("ok"):
                    detail = f"{quality.get('note')}; {detail}"
            except Exception as exc:
                ok, detail = False, str(exc)[:240]
            _add("PDF", name, size, ok, detail)

        band = os.path.join(FIXTURE, "vob_press_285da1e_band.pdf")
        try:
            quality = check_press_pdf(band, 148.0, 210.0, 5.0)
            info = decide_light({"compiled": True, "enginePassed": True, "bleedQuality": quality})
            rgb, bleed_px = _pdf_rgb(band)
            _save_edges(rgb, bleed_px, "vob-band-reference")
            ok = (not quality.get("ok")) and info["light"] == "amber" and info["clientMessage"] == "" and os.path.isfile(band)
            detail = f"light {info['light']}; {quality.get('note')}"
        except Exception as exc:
            ok, detail = False, str(exc)[:240]
        _add("PDF bug reference", "vob_press_285da1e_band.pdf", "A5", ok, detail)

        # The raster plate is what a failed vector path builds. The white column must not become grey.
        try:
            import pymupdf as fitz

            doc = fitz.open(os.path.join(FIXTURE, "vob_original.pdf"))
            page = doc[0]
            pix = page.get_pixmap(matrix=fitz.Matrix(72.0 / 72.0, 72.0 / 72.0), alpha=False)
            rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, 3).copy()
            doc.close()
            canvas, report = compile_raster_canvas(rgb[:, :, ::-1], 148.0, 210.0, 5.0, None)
            view = canvas[:, :, :3][:, :, ::-1]
            bleed_px = _px(5.0)
            scored = _score_canvas(view, bleed_px)
            _save_edges(view, bleed_px, "vob-raster-plate")
            grey = _grey_fraction(view, bleed_px)
            edges = report.get("edges") or []
            ok = bool(scored.get("ok")) and grey < 0.01 and _notes_ok(edges)
            detail = f"dE {_worst_delta(scored)}; grey {grey:.3f}; {(edges[0].get('note') if edges else '')}"
        except Exception as exc:
            ok, detail = False, str(exc)[:240]
        _add("PDF raster plate", "vob_original.pdf", "A5", ok, detail)

        for name, trim_w, trim_h, label in (
            ("rosanna_olive_flyer.jpg", 148.0, 210.0, "A5"),
            ("urbanflex_gym_flyer.jpeg", 148.0, 210.0, "A5"),
        ):
            src = os.path.join(FIXTURE, name)
            try:
                result = make_print_ready(src, os.path.join(folder, name), trim_w, trim_h, "a5", "A5", filename=name)
                press = result.get("pressPath") or ""
                quality = result.get("bleedQuality") or {}
                if os.path.isfile(press):
                    rgb, bleed_px = _pdf_rgb(press)
                    _save_edges(rgb, bleed_px, os.path.splitext(name)[0][:40])
                    grey = _grey_fraction(rgb, bleed_px)
                else:
                    grey = 1.0
                edges = (result.get("pressEngine") or {}).get("edges") or []
                ok = bool(quality.get("ok")) and os.path.isfile(press) and grey < 0.12 and result.get("light") != "red"
                detail = f"light {result.get('light')}; dE {_worst_delta(quality)}; grey {grey:.3f}"
                if edges:
                    detail += f"; {edges[0].get('note')}"
            except Exception as exc:
                ok, detail = False, str(exc)[:240]
            _add("JPEG", name, label, ok, detail)

    def _partial_light_edges(self) -> None:
        folder = tempfile.mkdtemp(prefix="ian-partial-")
        sizes = (
            ("A3", 297.0, 420.0),
            ("A3 landscape", 420.0, 297.0),
            ("A4", 210.0, 297.0),
            ("A4 landscape", 297.0, 210.0),
            ("A5", 148.0, 210.0),
            ("A5 landscape", 210.0, 148.0),
        )
        for label, trim_w, trim_h in sizes:
            src = os.path.join(folder, label.replace(" ", "-") + ".pdf")
            dest = src + ".press.pdf"
            try:
                _write_light_rim(src, trim_w + 5.0, trim_h + 5.0, 2.5)
                built = compile_vector_press(src, dest, trim_w, trim_h, 5.0)
                quality = check_press_pdf(dest, trim_w, trim_h, 5.0)
                info = decide_light({"compiled": True, "enginePassed": True, "bleedQuality": quality})
                rgb, bleed_px = _pdf_rgb(dest)
                _save_edges(rgb, bleed_px, "partial-" + label.replace(" ", "-"))
                ok = bool(quality.get("ok")) and info["light"] == "green" and bool(built.get("used"))
                detail = f"light {info['light']}; dE {_worst_delta(quality)}"
            except Exception as exc:
                ok, detail = False, str(exc)[:240]
            _add("PDF partial 2.5 mm", "press engine", label, ok, detail)

    def _matrix(self) -> None:
        folder = tempfile.mkdtemp(prefix="ian-matrix-")
        for index, product in enumerate(_products()):
            trim_w = float(product["widthMm"])
            trim_h = float(product["heightMm"])
            kind = KINDS[index % len(KINDS)]
            label = str(product.get("label") or product["id"])
            large = plate_exceeds_memory(trim_w, trim_h)
            for state, extra, light in (
                ("none", 0.0, False),
                ("partial", 2.5, True),
                ("full", 5.0, False),
                ("over", 10.0, False),
            ):
                stem = f"{product['id']}-{state}"
                try:
                    if large:
                        src = os.path.join(folder, stem + ".pdf")
                        dest = src + ".press.pdf"
                        colour = (36, 92, 48) if kind != "light" else (228, 224, 214)
                        _write_flat_pdf(src, trim_w, trim_h, extra, colour)
                        built = compile_vector_press(src, dest, trim_w, trim_h, 5.0)
                        quality = check_press_pdf(dest, trim_w, trim_h, 5.0)
                        rgb, bleed_px = _pdf_rgb(dest)
                        edges = (built.get("report") or {}).get("edges") or []
                        ok = bool(built.get("used")) and bool(quality.get("ok")) and _notes_ok(edges)
                        detail = f"dE {_worst_delta(quality)}; {(edges[0].get('note') if edges else '')}"
                        if state == "over":
                            notes = " ".join(str(edge.get("note") or "") for edge in edges)
                            ok = ok and "trimmed to 5 mm" in notes
                        if state == "full":
                            ok = ok and bool((built.get("report") or {}).get("fullBleedKept"))
                    else:
                        image = _with_bleed(trim_w, trim_h, extra, kind, light_rim=light)
                        before = image.copy()
                        canvas, report = compile_raster_canvas(image, trim_w, trim_h, 5.0, 300.0 if extra else None)
                        view = np.ascontiguousarray(canvas[:, :, :3])
                        bleed_px = _px(5.0)
                        scored = _score_canvas(view, bleed_px)
                        edges = report.get("edges") or []
                        grey = _grey_fraction(view, bleed_px)
                        ok = bool(scored.get("ok")) and grey < 0.01 and _notes_ok(edges)
                        detail = f"dE {_worst_delta(scored)}; grey {grey:.3f}; {(edges[0].get('note') if edges else '')}"
                        if state == "full":
                            same = abs(int(view[view.shape[0] // 2, view.shape[1] // 2, 0]) - int(before[before.shape[0] // 2, before.shape[1] // 2, 0])) < 12
                            ok = ok and same and abs(view.shape[1] - _px(trim_w + 10.0)) <= 2
                        if state == "over":
                            notes = " ".join(str(edge.get("note") or "") for edge in edges)
                            ok = ok and "trimmed to 5 mm" in notes and abs(view.shape[1] - _px(trim_w + 10.0)) <= 2
                        rgb = view
                    _save_edges(rgb, bleed_px, stem)
                except Exception as exc:
                    ok, detail = False, str(exc)[:220]
                _add(f"synthetic {state} {kind}", product["id"], label, ok, detail)

    def _wrong_size_pipeline(self) -> None:
        from quick_print import _pixels_as_points_product

        for product in _products():
            width = float(product["widthMm"]) / 25.4 * 300.0
            height = float(product["heightMm"]) / 25.4 * 300.0
            found = _pixels_as_points_product(width, height)
            ok = bool(found) and found.get("id") == product["id"]
            _add("wrong size match", product["id"], str(product.get("label")), ok, found.get("id") if found else "none")

        folder = tempfile.mkdtemp(prefix="ian-wrong-")
        src = os.path.join(FIXTURE, "e6e73dae_A4_300px_as_points_wrong_size.pdf")
        try:
            result = make_print_ready(
                src, os.path.join(folder, "a4px"), 148.0, 210.0, "a5", "A5", filename="pixels.pdf",
            )
            press = result.get("pressPath") or ""
            quality = result.get("bleedQuality") or {}
            label = str(result.get("productLabel") or "")
            trim_w = float(result.get("trimW") or 0)
            trim_h = float(result.get("trimH") or 0)
            if os.path.isfile(press):
                rgb, bleed_px = _pdf_rgb(press)
                _save_edges(rgb, bleed_px, "e6e73dae-a4")
                grey = _grey_fraction(rgb, bleed_px)
            else:
                grey = 1.0
            ok = (
                abs(trim_w - 210.0) < 0.5 and abs(trim_h - 297.0) < 0.5
                and os.path.isfile(press) and bool(quality.get("ok")) and grey < 0.12
            )
            detail = f"{label} {trim_w:g}x{trim_h:g}; light {result.get('light')}; dE {_worst_delta(quality)}; grey {grey:.3f}"
        except Exception as exc:
            ok, detail = False, str(exc)[:240]
        _add("PDF pixels as points", "e6e73dae_A4_300px_as_points_wrong_size.pdf", "A4", ok, detail)


def _worst_delta(report: dict) -> str:
    scores = []
    pages = report.get("pages") or []
    if pages:
        for page in pages:
            for side in page.get("sides") or []:
                if side.get("delta") is not None:
                    scores.append(float(side["delta"]))
    else:
        for side in report.get("sides") or []:
            if side.get("delta") is not None:
                scores.append(float(side["delta"]))
    if not scores:
        return "n/a"
    return f"{max(scores):.2f}"


def _write_light_rim(path: str, page_w_mm: float, page_h_mm: float, trim_inset_mm: float) -> None:
    import io

    import pymupdf as fitz
    from PIL import Image

    w_pt = float(page_w_mm) * MM
    h_pt = float(page_h_mm) * MM
    pw = max(8, int(round(w_pt)))
    ph = max(8, int(round(h_pt)))
    rgb = np.empty((ph, pw, 3), np.uint8)
    rgb[:, :] = (80, 90, 30)
    rim = max(1, int(round((trim_inset_mm + 3.5) / 25.4 * 72.0)))
    rim = min(rim, pw // 4, ph // 4)
    rgb[:, :rim] = (246, 220, 140)
    rgb[:, -rim:] = (246, 220, 140)
    rgb[:rim, :] = (246, 220, 140)
    rgb[-rim:, :] = (246, 220, 140)
    buf = io.BytesIO()
    Image.fromarray(rgb, "RGB").save(buf, format="JPEG", quality=92)
    doc = fitz.open()
    page = doc.new_page(width=w_pt, height=h_pt)
    page.insert_image(page.rect, stream=buf.getvalue())
    inset = float(trim_inset_mm) * MM
    page.set_trimbox(fitz.Rect(inset, inset, w_pt - inset, h_pt - inset))
    doc.save(path)
    doc.close()


def _write_table() -> None:
    lines = ["| File | Path | Size | Result | Detail |", "| --- | --- | --- | --- | --- |"]
    for row in ROWS:
        detail = row["detail"].replace("|", "/").replace("\n", " ")
        lines.append(f"| {row['kind']} | {row['path']} | {row['size']} | {row['result']} | {detail} |")
    text = "\n".join(lines) + "\n"
    os.makedirs("/opt/cursor/artifacts", exist_ok=True)
    with open("/opt/cursor/artifacts/bleed-quality-table.txt", "w", encoding="utf-8") as handle:
        handle.write(text)
    print("\n" + text, flush=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
