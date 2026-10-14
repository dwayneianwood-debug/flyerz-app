#!/usr/bin/env python3
"""One-step press file for sales.

Decides the product fit and 5mm bleed without asking. AI raster artwork is
set as vector type (vector text rebuild v2). If that check fails, the picture
is only enlarged, the original lettering stays, and the job is marked amber.
The press PDF for every other file is built by the existing compile script.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import time
import subprocess
import sys
import tempfile
from typing import Optional

BLEED_MM = 5.0
UPSCALE_AMBER = 3.0
ASPECT_AMBER = 0.12
OCR_AMBER = 0.55
MAX_HONEST_UPSCALE = 4.0
MIN_DPI_AFTER_UPSCALE = 150.0
MM_TO_PT = 72.0 / 25.4

OFFICE_EXT = {".doc", ".docx", ".ppt", ".pptx"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp"}
VECTOR_EXT = {".pdf", ".ai", ".eps"}

OFFICE_MESSAGE = (
    "Word and PowerPoint files can't be printed as they are. "
    "Please export it as a PDF and send that to us."
)
EPS_MESSAGE = (
    "This EPS file can't be printed as it is. "
    "Please send a PDF with the fonts embedded or outlined."
)
CORRUPT_MESSAGE = (
    "We could not open this file. It may be damaged, or it was saved in a program we cannot read. "
    "Please send it again as a PDF, JPG, or PNG."
)
COMPILE_MESSAGE = (
    "We could not build a press file from this. Please send a PDF or a clear JPG or PNG of the artwork."
)


def _products() -> list:
    path = os.path.join(os.path.dirname(__file__), "..", "shared", "quick-print-products.json")
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _product_by_id(product_id: str) -> Optional[dict]:
    for product in _products():
        if product.get("id") == product_id:
            return product
    return None


def _cover_scale(src_w: int, src_h: int, trim_w: float, trim_h: float) -> float:
    target_w = max(1.0, float(trim_w) / 25.4 * 300.0)
    target_h = max(1.0, float(trim_h) / 25.4 * 300.0)
    return max(target_w / max(1, src_w), target_h / max(1, src_h))


def too_small(src_w: int, src_h: int, trim_w: float, trim_h: float) -> bool:
    scale = _cover_scale(src_w, src_h, trim_w, trim_h)
    if scale <= 0:
        return True
    after = (300.0 / scale) * MAX_HONEST_UPSCALE
    return after < MIN_DPI_AFTER_UPSCALE


def too_small_message(label: str, trim_w: float, trim_h: float) -> str:
    need_w = int(math.ceil(float(trim_w) / 25.4 * 300.0))
    need_h = int(math.ceil(float(trim_h) / 25.4 * 300.0))
    return (
        f"Thanks for the picture. It is too small to print sharply on {label}, even if we enlarge it. "
        f"Please send a larger file. For {label} that is about {need_w} × {need_h} pixels, "
        "or a PDF exported from the design program."
    )


def decide_light(facts: dict) -> dict:
    """Traffic light. Amber is only for a real risk. Red means no press file."""
    kind = facts.get("kind")
    if kind == "office":
        return {"light": "red", "reasons": [OFFICE_MESSAGE], "clientMessage": OFFICE_MESSAGE}
    if kind in ("corrupt", "unsupported") or facts.get("readable") is False:
        return {"light": "red", "reasons": ["The file could not be read."], "clientMessage": CORRUPT_MESSAGE}
    if facts.get("tooSmall"):
        return {
            "light": "red",
            "reasons": ["The picture is far too small to print at this size."],
            "clientMessage": facts.get("tooSmallMessage") or too_small_message("this size", 148, 210),
        }
    if facts.get("unextendable"):
        from green_gate import client_message

        return {
            "light": "red",
            "reasons": ["The picture's shape cannot be extended to this print size."],
            "clientMessage": client_message("proportions"),
        }
    if facts.get("plateSkipped"):
        reason = str(facts.get("plateReason") or "").strip() or "The press plate was not rendered."
        return {"light": "amber", "reasons": [reason], "clientMessage": ""}
    if facts.get("missingContent"):
        from green_gate import client_message

        return {
            "light": "red",
            "reasons": ["The page looks empty, so some of the artwork is missing."],
            "clientMessage": client_message("missing"),
        }
    if not facts.get("compiled"):
        return {
            "light": "red",
            "reasons": [facts.get("compileError") or "A press file could not be built."],
            "clientMessage": COMPILE_MESSAGE,
        }

    reasons = []
    if facts.get("ocrDoubtful"):
        reasons.append(
            str(facts.get("ocrDoubtfulReason") or "").strip()
            or "Some marks did not look like real words, so they were left unchanged. Glance at the picture."
        )
    if facts.get("ocrLow"):
        reasons.append("The rebuilt text was hard to read, so check the spelling before it is printed.")
    if facts.get("textNearTrim"):
        reasons.append("Some text is still very close to the trim after the safe-zone shrink.")
    upscale = float(facts.get("upscale") or 1)
    if upscale >= UPSCALE_AMBER:
        reasons.append(f"The picture was enlarged {upscale:.1f} times to reach print size. Glance at fine detail.")
    shape_note = str(facts.get("shapeNote") or "").strip()
    if shape_note:
        reasons.insert(0, shape_note)
    elif facts.get("aspectExtended") and float(facts.get("aspectDelta") or 0) >= ASPECT_AMBER:
        reasons.append("The picture was a different shape, so the edges were extended. Glance at those edges.")
    # The checklist already says whether leftover pictures are sharp enough.
    if facts.get("vectorAmber") and not facts.get("checklist"):
        note = str(facts.get("vectorAmberReason") or "").strip()
        if note and note not in reasons:
            reasons.append(note)
    if facts.get("contentPullAmber"):
        note = str(facts.get("contentPullNote") or "").strip() or (
            "Some text or a logo is still inside the 3 mm safe zone. It was brought in by 2 mm. Please check it before printing."
        )
        if note not in reasons:
            reasons.append(note)
    if facts.get("enginePassed") is False:
        note = str(facts.get("engineReason") or "").strip() or "The press check flagged this file."
        if "live text that was already embedded" in note.lower():
            return {
                "light": "red",
                "reasons": [note],
                "clientMessage": note,
                "checklistRed": True,
            }
        if note not in reasons:
            reasons.append(note)
    checklist = facts.get("checklist") if isinstance(facts.get("checklist"), dict) else None
    if checklist:
        for item in checklist.get("items") or []:
            if item.get("passed"):
                continue
            detail = str(item.get("detail") or item.get("label") or "").strip()
            if detail and detail not in reasons:
                reasons.append(detail)
        if checklist.get("severity") == "red":
            from green_gate import client_message

            return {
                "light": "red",
                "reasons": reasons or ["This file cannot go to press as it is."],
                "clientMessage": str(checklist.get("clientMessage") or client_message("cut")),
            }
    twenty = facts.get("twentyFive") if isinstance(facts.get("twentyFive"), dict) else None
    extra_checks = facts.get("extraChecks") if isinstance(facts.get("extraChecks"), dict) else None
    from extra_checks import worse_light

    combined = worse_light(twenty, extra_checks) if (twenty or extra_checks) else None
    if combined and combined.get("light") == "red":
        extra = [line for line in (combined.get("reasons") or []) if line not in reasons]
        return {
            "light": "red",
            "reasons": extra + reasons,
            "clientMessage": str(combined.get("clientMessage") or ""),
            "checklistRed": True,
        }
    if combined and combined.get("light") == "amber":
        for line in combined.get("reasons") or []:
            if line and line not in reasons:
                reasons.append(line)
    quality = facts.get("bleedQuality") if isinstance(facts.get("bleedQuality"), dict) else None
    if quality and quality.get("ok") is False:
        note = str(quality.get("note") or "").strip() or (
            "The bleed does not continue the artwork. Check that edge before printing."
        )
        if note not in reasons:
            reasons.append(note)
    if reasons:
        return {"light": "amber", "reasons": reasons, "clientMessage": ""}
    return {"light": "green", "reasons": [], "clientMessage": ""}


def _eps_font_wording(info: dict, facts: dict) -> dict:
    """An EPS that cannot go to press as it is asks for a PDF. No Type 3 jargon."""
    extra = facts.get("extraChecks") if isinstance(facts.get("extraChecks"), dict) else {}
    twenty = facts.get("twentyFive") if isinstance(facts.get("twentyFive"), dict) else {}
    rows = list(extra.get("checks") or []) + list(twenty.get("checks") or [])
    font_row = False
    for row in rows:
        num = str(row.get("num") or row.get("id") or "")
        detail = str(row.get("detail") or "")
        status = str(row.get("status") or "")
        if status in ("pass", "passed", "auto", "fixed"):
            continue
        low = detail.lower()
        if num in ("4", "E6", "extra_E6") or "type 3" in low or "type3" in low or "not embedded" in low or "pictures of the letters" in low:
            font_row = True
            break
    blob = " ".join(str(item) for item in (info.get("reasons") or [])) + " " + str(info.get("clientMessage") or "")
    if not font_row and "type 3" not in blob.lower() and "type3" not in blob.lower():
        return info
    reasons = []
    for line in info.get("reasons") or []:
        text = str(line)
        if "type 3" in text.lower() or "type3" in text.lower():
            text = EPS_MESSAGE
        if text and text not in reasons:
            reasons.append(text)
    if EPS_MESSAGE not in reasons:
        reasons.insert(0, EPS_MESSAGE)
    info = dict(info)
    info["reasons"] = reasons
    info["clientMessage"] = EPS_MESSAGE
    return info


def _safe_name(name: str) -> str:
    base = os.path.basename(name or "artwork").replace("\x00", "")
    return base[:180] or "artwork"


def _ext(path: str, filename: str) -> str:
    return os.path.splitext(filename or path)[1].lower()


def _to_bgr(img):
    import cv2
    import numpy as np

    if img is None:
        return None
    if img.ndim == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if img.shape[2] == 4:
        alpha = img[:, :, 3:4].astype(np.float32) / 255.0
        bgr = img[:, :, :3].astype(np.float32)
        white = np.full_like(bgr, 255)
        return (bgr * alpha + white * (1.0 - alpha)).astype(np.uint8)
    return img[:, :, :3].copy()


def _read_image(path: str):
    import os

    import cv2
    from large_job import LARGE_JOB_BYTES

    try:
        huge = os.path.getsize(path) >= LARGE_JOB_BYTES
    except OSError:
        huge = False
    if huge:
        return _thumbnail_bgr(path, 3600)
    return _to_bgr(cv2.imread(path, cv2.IMREAD_UNCHANGED))


def _thumbnail_bgr(path: str, limit: int):
    """A large picture is reduced while it is opened, so the full bitmap is not the working file."""
    import cv2
    import numpy as np
    from PIL import Image

    previous = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = max(int(previous or 0), 400_000_000)
    try:
        with Image.open(path) as image:
            image.draft("RGB", (limit, limit))
            image.thumbnail((limit, limit), Image.Resampling.LANCZOS)
            rgb = np.array(image.convert("RGB"))
    finally:
        Image.MAX_IMAGE_PIXELS = previous
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def _header_long(path: str) -> int:
    try:
        from PIL import Image

        with Image.open(path) as image:
            return max(image.size)
    except Exception:
        return 0


def _write_working_image(src: str, dest: str, limit: int) -> str:
    """Save a smaller copy. The press path then places this instead of the full file."""
    from PIL import Image

    previous = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = max(int(previous or 0), 400_000_000)
    try:
        with Image.open(src) as image:
            image.draft("RGB", (limit, limit))
            image.thumbnail((limit, limit), Image.Resampling.LANCZOS)
            image.convert("RGB").save(dest, format="JPEG", quality=90, dpi=(300, 300))
    finally:
        Image.MAX_IMAGE_PIXELS = previous
    return dest


def _tame_raster(img, trim_w: float, trim_h: float, nbytes: int):
    """Shrink a screenshot or a huge file before OCR and the bleed extend."""
    import cv2
    from large_job import cap_shape, working_limit

    if img is None:
        return None, False
    height, width = img.shape[:2]
    limit = working_limit(height, width, trim_w, trim_h, nbytes)
    new_h, new_w = cap_shape(height, width, limit)
    if new_h == height and new_w == width:
        return img, False
    return cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA), True


def _place_large_image(src: str, dest: str, trim_w: float, trim_h: float) -> None:
    """Place a poster picture on the trim. Cover scale, then the press path adds the bleed strips."""
    import pymupdf as fitz
    from PIL import Image

    doc = fitz.open()
    page = doc.new_page(width=trim_w * MM_TO_PT, height=trim_h * MM_TO_PT)
    # Cover: the picture fills the trim. Reading the header avoids a full-plate decode.
    with Image.open(src) as image:
        src_w, src_h = image.size
    cover = max(page.rect.width / max(src_w, 1), page.rect.height / max(src_h, 1))
    width = src_w * cover
    height = src_h * cover
    x0 = (page.rect.width - width) / 2.0
    y0 = (page.rect.height - height) / 2.0
    page.insert_image(fitz.Rect(x0, y0, x0 + width, y0 + height), filename=src, keep_proportion=False)
    doc.save(dest)
    doc.close()


def _write_png(img, path: str) -> None:
    import cv2
    from PIL import Image

    ok = cv2.imwrite(path, img)
    if not ok:
        raise RuntimeError("could not write the working picture")
    with Image.open(path) as im:
        im.save(path, format="PNG", dpi=(300, 300))


def _pdf_trim_mm(path: str) -> Optional[tuple]:
    import pymupdf as fitz

    doc = fitz.open(path)
    try:
        if doc.page_count < 1:
            return None
        page = doc[0]
        box = page.trimbox if page.trimbox.width > 2 and page.trimbox.height > 2 else page.mediabox
        return (box.width * 25.4 / 72.0, box.height * 25.4 / 72.0)
    finally:
        doc.close()


def _fit_page_to_trim(page_w: float, page_h: float, trim_w: float, trim_h: float, tolerance: float = 2.5):
    """The page as this trim, in either orientation, including bleed already on the page."""
    from press_ready_engine import inferred_side_bleed

    direct = inferred_side_bleed(page_w, page_h, trim_w, trim_h, trim_tol=tolerance)
    if direct:
        return {"trimW": float(trim_w), "trimH": float(trim_h), "rotated": False, "bleed": direct}
    swapped = inferred_side_bleed(page_w, page_h, trim_h, trim_w, trim_tol=tolerance)
    if swapped:
        return {"trimW": float(trim_h), "trimH": float(trim_w), "rotated": True, "bleed": swapped}
    return None


def _product_for_trim(trim_w: float, trim_h: float):
    for product in _products():
        if abs(float(product["widthMm"]) - trim_w) <= 0.2 and abs(float(product["heightMm"]) - trim_h) <= 0.2:
            return product
    return None


def incoming_bleed_line(bleed: dict) -> tuple[str, str]:
    """One sentence for bleed already on the page, and a short story tag.

    A page with no trim box and more than 5 mm on a side is over-bleed.
    It gets the trim sentence only. It does not also say a shortfall was added
    or that the file already had exactly 5 mm.
    """
    sides = [float((bleed or {}).get(name) or 0) for name in ("left", "right", "top", "bottom")]
    most = max(sides) if sides else 0.0
    if (bleed or {}).get("kind") == "existing" and most > BLEED_MM + 0.6:
        return (
            f"The page already had about {most:.1f} mm of bleed. Extra bleed is trimmed to 5 mm.",
            "over",
        )
    if (bleed or {}).get("kind") == "existing":
        return (
            f"The page is the trim plus about {float(bleed.get('left') or 0):.1f} mm of bleed already. "
            "That bleed is kept and only the shortfall is added.",
            "partial",
        )
    return ("The PDF already fits this product, so the page was kept and sent to the press engine.", "fit")


def extra_bleed_lines(
    over_incoming: bool,
    full_kept: bool,
    existing: bool,
    press_ok: bool,
    trimmed: bool = False,
) -> tuple[bool, list[str]]:
    """Sentences after the per-edge note. Extra bleed keeps the single trim sentence."""
    if over_incoming or trimmed:
        return bool(full_kept or existing), []
    if full_kept:
        return True, ["This file already had 5 mm bleed. That bleed was kept and was not added again."]
    if existing:
        return True, []
    if press_ok:
        return False, ["Automatic bleed added the 5 mm edge. Bleed was not stacked on an existing 5 mm."]
    return False, []


def _apply_cat_edit(path: str, output_dir: str, decisions: list, request: str) -> str:
    """One time, date, or number change before the press file is built."""
    try:
        from artwork_edits import confirm, propose

        key = "print-flow-" + os.path.basename(output_dir or "job")
        proposed = propose(path, request, key, os.path.join(output_dir, "edit-preview"))
        if not isinstance(proposed, dict) or not proposed.get("ok"):
            reply = str((proposed or {}).get("reply") or "") if isinstance(proposed, dict) else ""
            decisions.append(reply or "The small text edit could not be applied. The press file was made from the original artwork.")
            return path
        dest = os.path.join(output_dir, "edited-source.pdf")
        done = confirm(key, dest)
        if done.get("ok") and os.path.isfile(dest) and os.path.getsize(dest) > 100:
            decisions.append("The time, date, or number you asked for was changed before the press file was made.")
            return dest
    except Exception:
        pass
    decisions.append("The small text edit could not be applied. The press file was made from the original artwork.")
    return path


def _apply_replicate_edit(path: str, output_dir: str, decisions: list, trim_w: float, trim_h: float) -> tuple[str, bool]:
    """One Replicate picture pass. A missing token leaves the artwork as sent."""
    token = ""
    try:
        from ai_enhancements import _get_replicate_token

        token = (_get_replicate_token() or "").strip()
    except Exception:
        token = ""
    if not token:
        decisions.append(
            "Replicate is not set up on this computer, so the artwork was left as you sent it and the press file was still made."
        )
        return path, False
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"):
        decisions.append(
            "Replicate is ready. This file stays on the press path, so the picture was not sent out for a separate edit."
        )
        return path, False
    try:
        from ai_upscale import apply_ai_upscale

        dest = os.path.join(output_dir, "replicate-edit.png")
        enlarged = apply_ai_upscale(path, {
            "trim_w_mm": trim_w,
            "trim_h_mm": trim_h,
            "bleed_mm": 0,
            "output_path": dest,
            "max_scale": 4,
            "skip_below_ppi": 75,
        })
        enhanced = str((enlarged or {}).get("enhanced_path") or "")
        if enhanced and os.path.exists(enhanced) and not (enlarged or {}).get("used_original"):
            if str((enlarged or {}).get("provider") or "") == "replicate":
                decisions.append("The picture was edited with Replicate before the press file was made.")
            else:
                decisions.append(
                    "Replicate did not return a picture, so a local enlargement was used before the press file was made."
                )
            return enhanced, True
    except Exception:
        pass
    decisions.append("The Replicate edit could not be applied. The press file was made from the original artwork.")
    return path, False


def _write_before(src: str, dest: str) -> bool:
    """A small picture of the file Ian sent, for the before side of the result."""
    try:
        import cv2

        ext = os.path.splitext(src)[1].lower()
        image = None
        if ext in (".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"):
            image = cv2.imread(src, cv2.IMREAD_COLOR)
        else:
            import numpy as np
            import pymupdf as fitz

            from page_zoom import adjusted_zoom

            doc = fitz.open(src)
            try:
                if doc.page_count < 1:
                    return False
                page = doc[0]
                zoom = adjusted_zoom(page.rect.width, page.rect.height, 1.0)
                pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False, colorspace=fitz.csRGB)
                if max(pix.w, pix.h) > 900 and max(pix.w, pix.h) > 0:
                    zoom = zoom * (900.0 / max(pix.w, pix.h))
                    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False, colorspace=fitz.csRGB)
                rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3]
                image = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
            finally:
                doc.close()
        if image is None:
            return False
        height, width = image.shape[:2]
        scale = 900.0 / max(height, width, 1)
        if scale < 1:
            image = cv2.resize(image, (max(1, int(width * scale)), max(1, int(height * scale))), interpolation=cv2.INTER_AREA)
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        return bool(cv2.imwrite(dest, image))
    except Exception:
        return False


def _seed_pixel_page_ink(press_path: str) -> None:
    """Remember ink from a small render so the checklist does not decode the whole plate."""
    try:
        import pymupdf as fitz

        from extra_checks import _sampled_ink, remember_press_ink

        doc = fitz.open(press_path)
        try:
            if doc.page_count < 1:
                return
            facts = {"max_tac": 0.0, "k90": 0, "k_only": 0, "small_rich": False, "small_k": False}
            _sampled_ink(doc[0], 0.5, facts)
            facts["small_k"] = facts["k_only"] > 30
            facts["small_rich"] = bool(facts["small_rich"] or (facts["k90"] > 30 and facts["k_only"] == 0))
            remember_press_ink(press_path, facts)
        finally:
            doc.close()
    except Exception:
        return


def press_saved_for_rebleed(press_ok: bool, light: str = "green") -> bool:
    """A finished press file stays available for change-bleed, including a red light."""
    return bool(press_ok) and str(light or "") in ("green", "amber", "red")


def _names_edges(line: str) -> bool:
    low = str(line or "").lower()
    return low.startswith("bleed:") and sum(side in low for side in ("top", "bottom", "left", "right")) >= 2


def _record_bleed_note(decisions: list, engine, extra_edges=None) -> None:
    """Put the per-edge automatic method on every card that built or kept bleed."""
    from press_ready_engine import bleed_method_sentence

    edges = []
    sentence = ""
    if isinstance(engine, dict):
        sentence = str(engine.get("bleedNote") or "")
        edges = list(engine.get("edges") or [])
    if not edges and extra_edges:
        edges = list(extra_edges)
    if not _names_edges(sentence):
        built = bleed_method_sentence(edges)
        if built:
            sentence = built
    if not _names_edges(sentence):
        for line in decisions:
            if _names_edges(line):
                sentence = str(line)
                break
    if not sentence:
        return
    if isinstance(engine, dict):
        engine["bleedNote"] = sentence
    replaced = False
    for index, line in enumerate(decisions):
        if str(line).startswith("Bleed:"):
            decisions[index] = sentence
            replaced = True
            break
    if not replaced and sentence not in decisions:
        decisions.append(sentence)


def _remember_product(decisions: list, product: dict, trim_w: float, trim_h: float) -> None:
    line = f"Product set to {product.get('label')} ({trim_w:g} × {trim_h:g} mm)."
    for index, existing in enumerate(decisions):
        if str(existing).startswith("Product set to "):
            decisions[index] = line
            return
    decisions.append(line)


def _match_product(width_mm: float, height_mm: float, tolerance: float = 2.5):
    """Best catalog size. An unrotated landscape product wins over turning the portrait one.

    Equal bleed on both axes wins over a nearer trim that only explains one axis.
    A 90 × 50 card with 2.5 mm all round is 95 × 55, which is also close to the
    90 × 55 card. The even margin is the partial bleed. A real TrimBox is read
    before this, so an uneven edge is not guessed here.
    """
    best = None
    for product in _products():
        fit = _fit_page_to_trim(width_mm, height_mm, float(product["widthMm"]), float(product["heightMm"]), tolerance)
        if not fit:
            continue
        bleed = fit["bleed"]
        uniform = abs(float(bleed["left"]) - float(bleed["top"]))
        extra = abs(width_mm - fit["trimW"]) + abs(height_mm - fit["trimH"])
        kind_rank = 0 if bleed.get("kind") == "trim" else 1
        rank = (1 if fit["rotated"] else 0, kind_rank, round(uniform, 3), extra)
        if best is None or rank < best[0]:
            best = (rank, product, fit["rotated"])
    if not best:
        return None, False
    product, rotated = best[1], best[2]
    if rotated:
        twin = _product_for_trim(float(product["heightMm"]), float(product["widthMm"]))
        if twin:
            return twin, False
    return product, rotated


def _page_points(path: str):
    import pymupdf as fitz

    doc = fitz.open(path)
    try:
        if doc.page_count < 1:
            return None
        page = doc[0]
        return (float(page.rect.width), float(page.rect.height))
    finally:
        doc.close()


def pixels_as_points_trim(path: str):
    """Catalog product when a PDF page is a 300 dpi pixel count stored as points."""
    if os.path.splitext(path)[1].lower() != ".pdf":
        return None
    points = _page_points(path)
    if not points:
        return None
    return _pixels_as_points_product(*points)


def _pixels_as_points_product(width_pt: float, height_pt: float):
    """Acrobat sometimes stores a 300 dpi picture's pixel counts as the page size.

    2480 × 3508 pt is an A4 picture, not an 875 mm sheet.
    """
    best = None
    for product in _products():
        expect_w = float(product["widthMm"]) / 25.4 * 300.0
        expect_h = float(product["heightMm"]) / 25.4 * 300.0
        if abs(float(width_pt) - expect_w) <= 8.0 and abs(float(height_pt) - expect_h) <= 8.0:
            err = abs(float(width_pt) - expect_w) + abs(float(height_pt) - expect_h)
            if best is None or err < best[0]:
                best = (err, product)
    return best[1] if best else None


def _render_pdf_image(path: str, native: bool = False):
    import numpy as np
    import pymupdf as fitz
    from large_job import file_bytes, is_large_job

    doc = fitz.open(path)
    try:
        page = doc[0]
        long_pt = max(page.rect.width, page.rect.height, 1)
        cap = 2400.0 if is_large_job(file_bytes(path)) else 4500.0
        # A pixels-as-points page is already the 300 dpi grid. Zoom 1 reads those pixels.
        zoom = 1.0 if native else min(300.0 / 72.0, cap / long_pt)
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
        return arr[:, :, :3][:, :, ::-1].copy()
    finally:
        doc.close()


def _prepare_vector(path: str, ext: str) -> tuple:
    if ext not in (".ai", ".eps"):
        return path, ""
    from illustrator_intake import prepare_illustrator_file

    prepared = prepare_illustrator_file(path, ext)
    if not prepared.get("success"):
        return "", "This Illustrator file could not be opened. In Illustrator, save a PDF copy and send that."
    return prepared.get("pdfPath") or path, ""


def _rotate_to_product(img, trim_w: float, trim_h: float, decisions: list) -> tuple:
    import cv2

    height, width = img.shape[:2]
    src = width / float(height)
    target = float(trim_w) / float(trim_h)
    swap = float(trim_h) / float(trim_w)
    delta = abs(src - target) / target
    swap_delta = abs(src - swap) / swap
    if swap_delta < 0.08 and swap_delta + 0.01 < delta:
        turned = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
        decisions.append("The picture was turned on its side so it matches the product. It was not stretched.")
        return turned, True
    return img, False


# Cover-fit may crop at most this much on an edge. A wider gap is contained.
COVER_CROP_MM = 2.0


def aspect_fit(src_w: int, src_h: int, trim_w: float, trim_h: float) -> dict:
    """Cover when the crop is within 2 mm per edge. Otherwise contain, centred.

    gapX and gapY are the empty margin inside the trim on one edge, in millimetres.
    """
    src_w = max(1, int(src_w))
    src_h = max(1, int(src_h))
    trim_w = float(trim_w)
    trim_h = float(trim_h)
    cover = max(trim_w / src_w, trim_h / src_h)
    crop_x = max(0.0, (src_w * cover - trim_w) / 2.0)
    crop_y = max(0.0, (src_h * cover - trim_h) / 2.0)
    if max(crop_x, crop_y) <= COVER_CROP_MM + 0.05:
        return {"mode": "cover", "cropX": crop_x, "cropY": crop_y, "gapX": 0.0, "gapY": 0.0}
    contain = min(trim_w / src_w, trim_h / src_h)
    gap_x = max(0.0, (trim_w - src_w * contain) / 2.0)
    gap_y = max(0.0, (trim_h - src_h * contain) / 2.0)
    if max(gap_x, gap_y) <= 0.2:
        return {"mode": "match", "cropX": 0.0, "cropY": 0.0, "gapX": 0.0, "gapY": 0.0}
    return {"mode": "contain", "cropX": 0.0, "cropY": 0.0, "gapX": gap_x, "gapY": gap_y}


def shape_extension_note(label: str, gap_x: float, gap_y: float) -> str:
    """Plain amber line when the picture was contained and the background extended."""
    wide = float(gap_x or 0) > 0.4
    tall = float(gap_y or 0) > 0.4
    if wide and tall:
        where = "around the edges"
    elif wide:
        where = "at the sides"
    elif tall:
        where = "at the top and bottom"
    else:
        return ""
    name = str(label or "this size").strip() or "this size"
    return f"Your picture is a different shape from {name}, so we extended the background {where}"


def _shape_note_from(payload, label: str) -> str:
    shape = payload.get("shape") if isinstance(payload, dict) else None
    if not isinstance(shape, dict) or shape.get("mode") != "contain":
        return ""
    return shape_extension_note(label, shape.get("gapX") or 0, shape.get("gapY") or 0)


def _shape_note_for(label, vector_built, engine, pending_shape, footer_layout: bool) -> str:
    """The centred contain note. A footer poster keeps its own layout sentence."""
    note = _shape_note_from(vector_built if isinstance(vector_built, dict) else {}, label)
    if note:
        return note
    note = _shape_note_from(engine if isinstance(engine, dict) else {}, label)
    if note:
        return note
    if footer_layout or not isinstance(pending_shape, dict):
        return ""
    return _shape_note_from({"shape": pending_shape}, label)


# A shape just inside the old 64px window must not be mirrored out. The orange-disc
# check sits at row 40, so the reflected rim stays shallower than that.
EDGE_SAMPLE_PX = 5
MIRROR_RIM_PX = 28
# Pads shorter than this (the 5 mm bleed, the orange-disc test) keep the shallow rim.
# A tall gap mirrors a block as tall as the gap. A 28px rim smears into stripes.
TALL_PAD_PX = 120
# The blur at the join is a soft continuation, then it climbs over 15–20 mm.
TALL_BLUR_S0 = 3.0
TALL_BLUR_SMAX = 80.0
TALL_RAMP_MM = 18.0
# The blend steps into the picture, and stops 2 mm short of text or a logo.
TALL_OVERLAP_MM = 7.0
TALL_CLEAR_MM = 2.0
# Lettering stays this far inside the trim when a short page is lengthened.
SAFE_ZONE_MM = 4.0
EDGE_LOWPASS_FRACTION = 0.04
# Canva leaves a 1px pure-white column. Skip that sliver before the colour sample.
EDGE_SLIVER_PX = 2


def _lowpass_along(row, fraction: float = EDGE_LOWPASS_FRACTION):
    """Blur one row along its length. `row` is (width, 3)."""
    import cv2
    import numpy as np

    width = int(row.shape[0])
    sigma = max(1.0, float(fraction) * width)
    blurred = cv2.GaussianBlur(np.ascontiguousarray(row.reshape(1, width, 3)), (0, 0), sigma)
    return blurred.reshape(width, 3)


def _reflect_from_edge(edge_band, pad: int):
    """Mirror a rim. Row 0 of the result is the edge row, touching the picture."""
    import numpy as np

    rim = int(edge_band.shape[0])
    if rim <= 1:
        return np.repeat(edge_band[:1], pad, axis=0)
    period = 2 * (rim - 1)
    distance = np.arange(pad, dtype=np.int32)
    pos = distance % period
    pos = np.where(pos < rim, pos, period - pos)
    src = rim - 1 - pos
    return edge_band[src]


def _blur_level(image, sigma: float, strong: bool):
    """Gaussian. A strong extension blurs a smaller copy so sigma 80 stays cheap."""
    import cv2

    if sigma <= 0.05:
        return image
    if strong and sigma >= 12.0:
        factor = min(8.0, max(2.0, float(sigma) / 8.0))
        small_w = max(8, int(round(image.shape[1] / factor)))
        small_h = max(8, int(round(image.shape[0] / factor)))
        small = cv2.resize(image, (small_w, small_h), interpolation=cv2.INTER_AREA)
        small = cv2.GaussianBlur(small, (0, 0), float(sigma) / factor)
        return cv2.resize(small, (image.shape[1], image.shape[0]), interpolation=cv2.INTER_LINEAR)
    return cv2.GaussianBlur(image, (0, 0), float(sigma))


def _progressive_blur(strip, smax: float, reach: float, s0: float = 0.0, strong: bool = False):
    """Blur grows with distance from row 0. Far rows lose the repeated rim."""
    import numpy as np

    count = int(strip.shape[0])
    base = np.ascontiguousarray(strip.astype(np.float32))
    if count == 0 or smax <= 0.05:
        return base
    s0 = float(max(0.0, s0))
    smax = float(max(float(smax), s0))
    ladder = [0.0, 2.0, 5.0, 8.0, 12.0, 18.0, 25.0, 35.0, 50.0, 65.0, 80.0]
    sigs = [value for value in ladder if s0 - 0.05 <= value <= smax + 0.05]
    if not sigs or sigs[0] > s0 + 0.05:
        sigs.insert(0, s0)
    if sigs[-1] < smax - 0.05:
        sigs.append(smax)
    if len(sigs) < 2:
        sigs.append(sigs[-1] + 1.0)
    levels = [base if sigma <= 0.05 else _blur_level(base, sigma, strong) for sigma in sigs]
    if count == 1:
        return levels[-1]
    distance = np.arange(count, dtype=np.float32)
    span = max(float(reach), 1.0)
    amount = np.clip(distance / span, 0.0, 1.0) ** 1.05
    target = s0 + amount * (smax - s0)
    edges = np.asarray(sigs, np.float32)
    index = np.clip(np.searchsorted(edges, target, side="right") - 1, 0, len(sigs) - 2)
    lo = edges[index]
    hi = edges[index + 1]
    alpha = ((target - lo) / np.maximum(hi - lo, 1e-6)).astype(np.float32)
    out = np.empty_like(base)
    for step in range(len(sigs) - 1):
        chosen = np.where(index == step)[0]
        if chosen.size == 0:
            continue
        weight = alpha[chosen][:, None, None]
        out[chosen] = levels[step][chosen] * (1.0 - weight) + levels[step + 1][chosen] * weight
    # A shallow pad keeps the real edge pixel. A tall pad is already blurred at s0.
    if s0 <= 0.05:
        out[0] = base[0]
    return out


def _mm_px(mm: float, px_per_mm: float, minimum: int = 1) -> int:
    return max(int(minimum), int(round(float(mm) * float(px_per_mm))))


def _smoothstep(t):
    """Zero slope at both ends, so a blend does not leave a new edge."""
    import numpy as np

    x = np.clip(np.asarray(t, np.float32), 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


def _first_ink_row(art, px_per_mm: float, from_end: bool = False):
    """Distance from this edge to the first text or logo line.

    A photograph has edge energy on every row. Lettering is a step up from
    the rows above it, held for about half a millimetre. None means this
    edge has no such line inside the search.
    """
    import cv2
    import numpy as np

    if art is None or getattr(art, "size", 0) == 0 or art.shape[0] < 8:
        return None
    gray = cv2.cvtColor(art, cv2.COLOR_BGR2GRAY)
    if from_end:
        gray = gray[::-1]
    plane = gray.astype(np.float32)
    horizontal = np.abs(cv2.Sobel(plane, cv2.CV_32F, 1, 0, ksize=3)).mean(axis=1)
    vertical = np.abs(cv2.Sobel(plane, cv2.CV_32F, 0, 1, ksize=3)).mean(axis=1)
    energy = np.maximum(horizontal, vertical)
    sigma = max(0.8, 0.30 * float(px_per_mm))
    smooth = cv2.GaussianBlur(energy.reshape(1, -1), (0, 0), sigma).ravel()
    window = _mm_px(6.0, px_per_mm, 5)
    need = _mm_px(0.45, px_per_mm, 2)
    search = min(int(smooth.size), _mm_px(24.0, px_per_mm, need + window))
    run = 0
    for y in range(search):
        lo = max(0, y - window)
        if y - lo < max(4, window // 3):
            run = 0
            continue
        bg = float(np.percentile(smooth[lo:y], 35))
        if float(smooth[y]) > max(42.0, bg * 2.5) and float(smooth[y]) > bg + 18.0:
            run += 1
            if run >= need:
                return int(y - need + 1)
        else:
            run = 0
    return None


def _join_overlap(art, px_per_mm: float, from_end: bool = False) -> int:
    """How far the blend may enter the picture: 6–8 mm, but 2 mm clear of type."""
    target = _mm_px(TALL_OVERLAP_MM, px_per_mm, 4)
    clear = _mm_px(TALL_CLEAR_MM, px_per_mm, 2)
    ink = _first_ink_row(art, px_per_mm, from_end=from_end)
    if ink is None:
        return min(target, max(0, int(art.shape[0]) // 6))
    return max(0, min(target, int(ink) - clear))


def _protect_rows(art, px_per_mm: float, from_end: bool = False) -> int:
    """Edge rows kept out of the inpaint, so the join is not a filled grey line."""
    ink = _first_ink_row(art, px_per_mm, from_end=from_end)
    clear = _mm_px(TALL_CLEAR_MM, px_per_mm, 2)
    if ink is None:
        return min(clear * 2, max(0, int(art.shape[0]) // 6))
    return max(0, min(int(art.shape[0]) - 1, int(ink) - 1))


def _scrub_marks(band, protect_px: int = 0, protect_end: bool = False):
    """Inpaint lettering and logos on a copy used only to grow the page.

    The art itself is not changed. A flat field and a wide colour bar are left,
    because those are the colour the extension is supposed to continue.
    `protect_px` rows against the picture stay real, so the fill cannot paint
    a light line on the join.
    """
    import cv2
    import numpy as np

    height, width = band.shape[:2]
    if height < 12 or width < 12:
        return band
    scale = 1.0
    work = band
    long_edge = max(height, width)
    if long_edge > 900:
        scale = 900.0 / float(long_edge)
        work = cv2.resize(
            band,
            (max(8, int(round(width * scale))), max(8, int(round(height * scale)))),
            interpolation=cv2.INTER_AREA,
        )
    gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)
    short = min(work.shape[:2])
    # Compact panels and lettering. A full-height colour bar is wider than this and stays.
    object_px = int(max(31, min(short * 0.36, 201)))
    if object_px % 2 == 0:
        object_px += 1
    object_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (object_px, object_px))
    opened = cv2.morphologyEx(work, cv2.MORPH_OPEN, object_kernel)
    closed = cv2.morphologyEx(work, cv2.MORPH_CLOSE, object_kernel)
    background = (opened.astype(np.float32) + closed.astype(np.float32)) * 0.5
    object_delta = np.max(np.abs(work.astype(np.float32) - background), axis=2)
    kernel_px = int(max(9, min(31, round(short * 0.04))))
    if kernel_px % 2 == 0:
        kernel_px += 1
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_px, kernel_px))
    black = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, kernel)
    white = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, kernel)
    marks = np.zeros(gray.shape, np.uint8)
    marks[(black > 20) | (white > 20) | (object_delta > 22)] = 255
    column_frac = (marks > 0).mean(axis=0)
    marks[:, column_frac > 0.65] = 0
    if int(marks.max()) == 0:
        return band
    marks = cv2.dilate(marks, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11)))
    if protect_px > 0:
        prot = max(1, int(round(float(protect_px) * scale)))
        prot = min(prot, marks.shape[0] - 1)
        if protect_end:
            marks[-prot:] = 0
        else:
            marks[:prot] = 0
    if int(marks.max()) == 0:
        return band
    keep = (marks == 0).astype(np.float32)
    sigma = 18.0
    num = cv2.GaussianBlur(work.astype(np.float32) * keep[..., None], (0, 0), sigma)
    den = cv2.GaussianBlur(keep, (0, 0), sigma)[..., None]
    filled = num / np.maximum(den, 1e-3)
    out = work.astype(np.float32)
    painted = marks > 0
    out[painted] = filled[painted]
    out = np.clip(np.rint(out), 0, 255).astype(np.uint8)
    if scale != 1.0:
        out = cv2.resize(out, (width, height), interpolation=cv2.INTER_LINEAR)
    return out


def _feather_to_edge(pad, edge_row, at_end: bool, depth: int = 36):
    """Blend the seam onto a horizontally blurred real edge so the join is not a line."""
    import numpy as np

    if pad.shape[0] < 4:
        return pad
    edge = _lowpass_along(np.asarray(edge_row, np.float32), fraction=0.08)
    depth = min(int(depth), int(pad.shape[0]))
    out = pad.astype(np.float32)
    for dist in range(depth):
        y = (out.shape[0] - 1 - dist) if at_end else dist
        weight = (dist / float(depth)) ** 0.85
        out[y] = edge * (1.0 - weight) + out[y] * weight
    return np.clip(np.rint(out), 0, 255).astype(np.uint8)


def _mirror_extend(band, pad: int, forward: bool, strong: bool = False, ramp_px: int | None = None, protect_px: int = 0):
    """Reflect the rim and blur it more the further it sits from the picture.

    A short pad reflects the outer 28px. A tall pad reflects a block as tall as
    the gap (tiled when the picture is shorter than the gap), with the lettering
    painted out of that copy first. The blur starts near sigma 3 on the join
    and reaches its full width over about 18 mm. `band` row 0 is the edge when
    forward is false.
    """
    import numpy as np

    width = int(band.shape[1])
    if pad <= 0:
        return np.zeros((0, width, 3), np.uint8)
    if strong:
        source = _scrub_marks(band, protect_px=protect_px, protect_end=forward)
    else:
        source = band
    work = np.ascontiguousarray(source.astype(np.float32))
    if not forward:
        work = work[::-1]
    reflected = _reflect_from_edge(work, pad)
    if strong:
        reach = float(ramp_px if ramp_px else max(int(pad), 1))
        blurred = _progressive_blur(reflected, TALL_BLUR_SMAX, reach, s0=TALL_BLUR_S0, strong=True)
    else:
        reach = float(min(max(pad - 1, 1), 110))
        smax = float(min(56.0, max(7.0, pad * 0.14)))
        blurred = _progressive_blur(reflected, smax, reach, s0=0.0, strong=False)
    out = np.clip(np.rint(blurred), 0, 255).astype(np.uint8)
    if not forward:
        out = out[::-1]
    return out


def _match_join_colour(pad, art, at_end: bool, depth: int):
    """Per-column low-pass so the rows against the picture share its edge colour.

    A filled mirror can leave one light row. The correction is full on the join
    and fades, so it does not repaint the rest of the extension.
    """
    import numpy as np

    rows = min(3, int(pad.shape[0]), int(art.shape[0]))
    if rows < 1 or pad.shape[1] != art.shape[1]:
        return pad
    if at_end:
        ext = pad[-rows:].astype(np.float32).mean(axis=0)
        edge = art[:rows].astype(np.float32).mean(axis=0)
    else:
        ext = pad[:rows].astype(np.float32).mean(axis=0)
        edge = art[-rows:].astype(np.float32).mean(axis=0)
    delta = _lowpass_along(edge - ext, fraction=0.025)
    out = pad.astype(np.float32)
    depth = min(int(depth), int(out.shape[0]))
    if depth < 1:
        return pad
    span = float(max(depth - 1, 1))
    for dist in range(depth):
        fade = float(1.0 - _smoothstep(dist / span))
        y = (out.shape[0] - 1 - dist) if at_end else dist
        out[y] = out[y] + delta * fade
    return np.clip(np.rint(out), 0, 255).astype(np.uint8)


def _feather_into_art(pad, art, overlap: int, at_end: bool):
    """Smoothstep the extension into the picture. The far end of the ramp is the real art."""
    import numpy as np

    overlap = int(min(max(int(overlap), 0), int(pad.shape[0]), int(art.shape[0])))
    if overlap < 2 or pad.shape[1] != art.shape[1]:
        return art
    out = np.array(art, copy=True)
    original = art.astype(np.float32)
    soft = pad.astype(np.float32)
    for dist in range(overlap):
        alpha = float(_smoothstep((dist + 0.5) / float(overlap)))
        if at_end:
            mixed = soft[-(dist + 1)] * (1.0 - alpha) + original[dist] * alpha
            out[dist] = np.clip(np.rint(mixed), 0, 255).astype(np.uint8)
        else:
            mixed = soft[dist] * (1.0 - alpha) + original[-(dist + 1)] * alpha
            out[-(dist + 1)] = np.clip(np.rint(mixed), 0, 255).astype(np.uint8)
    return out


def vertical_streaks_dominate(band) -> bool:
    """True when an extension is vertical colour stripes.

    Vertical streaks have strong horizontal gradient energy, almost no change
    down a column, and column means that jump from one x to the next. A soft
    wash, a flat field and a real gradient do not.
    """
    import cv2
    import numpy as np

    if band is None or getattr(band, "ndim", 0) != 3:
        return False
    if band.shape[0] < 24 or band.shape[1] < 24:
        return False
    gray = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY).astype(np.float32)
    if gray.shape[0] > 80:
        gray = gray[: gray.shape[0] - 36]
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    energy_x = float(np.mean(gx * gx))
    energy_y = float(np.mean(gy * gy)) + 1e-6
    ratio = energy_x / energy_y
    var_y = float(np.mean(np.var(gray, axis=0)))
    columns = gray.mean(axis=0)
    sigma = max(8.0, 0.06 * float(columns.size))
    slow = cv2.GaussianBlur(columns.reshape(1, -1), (0, 0), sigma).ravel()
    var_x = float(np.var(columns - slow))
    return bool(ratio >= 6.0 and var_y <= 30.0 and var_x >= 40.0)


def _luma(bgr):
    import numpy as np

    image = bgr.astype(np.float32)
    return image[..., 0] * 0.114 + image[..., 1] * 0.587 + image[..., 2] * 0.299


def _orient_join(image, join: int, side: str):
    """Put the extension above row `join`, which is the first row of the picture."""
    import numpy as np

    side = str(side or "top")
    if side == "top":
        return image, int(join)
    if side == "bottom":
        flipped = image[::-1]
        return flipped, int(image.shape[0] - int(join))
    turned = np.ascontiguousarray(np.transpose(image, (1, 0, 2)))
    if side == "left":
        return turned, int(join)
    flipped = turned[::-1]
    return flipped, int(turned.shape[0] - int(join))


def seam_metrics(image, join: int, side: str = "top", px_per_mm: float | None = None) -> dict:
    """Row-to-row luminance step at a large-block join, and a thin-band check.

    The join step is the mean absolute change across the seam, ±3 rows.
    It fails when that step is more than twice the larger of the median step
    inside the picture and inside the extension, or when the join/reference
    ratio is above 0.4. A one-row light or dark band fails the row-mean check.
    """
    import numpy as np

    work, seam = _orient_join(image, join, side)
    luma = _luma(work)
    height = int(luma.shape[0])
    ppm = float(px_per_mm) if px_per_mm else (300.0 / 25.4)
    gap = _mm_px(TALL_OVERLAP_MM + 2.0, ppm, 8)
    slab = _mm_px(12.0, ppm, 12)
    seam = int(max(3, min(height - 4, int(seam))))

    window = luma[seam - 3:seam + 4]
    join_step = float(np.mean(np.abs(np.diff(window, axis=0))))

    def median_step(start: int, stop: int) -> float:
        start = int(max(0, start))
        stop = int(min(height, stop))
        if stop - start < 4:
            return 0.0
        steps = np.abs(np.diff(luma[start:stop], axis=0)).mean(axis=1)
        return float(np.median(steps))

    art_step = median_step(seam + gap, seam + gap + slab)
    ext_step = median_step(seam - gap - slab, seam - gap)
    reference = max(art_step, ext_step)
    step_limit = 2.0 * reference
    ratio = join_step / max(reference, 1e-6)
    ok_step = bool(join_step <= step_limit + 0.05)
    # A smeared pad can stay under 2× and still show a hard line. 0.4 is the
    # accepted join (0.165) with room, and it rejects the 0.899 colour pad.
    # A smooth gradient has a reference step of about 0, so join/reference
    # explodes on a 0.02 step the 2× rule already accepts. The cap applies
    # once the picture has real texture, as on the poster.
    ok_ratio = True if reference < 0.5 else bool(ratio <= 0.4 + 1e-9)

    means = luma.mean(axis=1)
    band_dev = float(max(
        abs(means[seam] - 0.5 * (means[seam - 2] + means[seam + 2])),
        abs(means[seam - 1] - 0.5 * (means[seam - 3] + means[seam + 1])),
    ))
    art_lo = min(height - 2, seam + gap)
    art_hi = min(height, art_lo + slab)
    jumps = np.abs(np.diff(means[art_lo:art_hi])) if art_hi - art_lo >= 4 else np.array([0.0])
    typical = float(np.median(jumps)) if jumps.size else 0.0
    band_limit = max(3.0, 2.0 * typical)
    ok_band = bool(band_dev <= band_limit + 0.05)
    return {
        "joinStep": round(join_step, 3),
        "artStep": round(art_step, 3),
        "extStep": round(ext_step, 3),
        "stepLimit": round(step_limit, 3),
        "stepRatio": round(ratio, 3),
        "bandDev": round(band_dev, 3),
        "bandLimit": round(band_limit, 3),
        "okStep": ok_step,
        "okRatio": ok_ratio,
        "okBand": ok_band,
        "ok": bool(ok_step and ok_band and ok_ratio),
    }


def _sliver_row(edge, inner) -> bool:
    """A near-white or anomalous edge row. A smooth gradient is neither."""
    import numpy as np

    edge_f = np.asarray(edge, np.float32)
    inner_f = np.asarray(inner, np.float32)
    if edge_f.ndim != 2 or edge_f.shape[0] < 1 or inner_f.shape != edge_f.shape:
        return False
    white = np.min(edge_f, axis=-1) >= 245.0
    if float(np.mean(white)) >= 0.8:
        return True
    dist = np.linalg.norm(edge_f - inner_f, axis=-1)
    return bool(float(np.median(dist)) >= 36.0 and float(edge_f.std()) < 12.0)


def _drop_edge_sliver(work):
    """Row -1 is the outer edge. Skip up to 2 near-white or anomalous rows.

    The reference is the row just inside that window, so two matching fringe
    pixels are still skipped when they differ from the artwork.
    """
    height = int(work.shape[0])
    if height < EDGE_SLIVER_PX + 2:
        return work
    anchor = work[-(EDGE_SLIVER_PX + 1)]
    drop = 0
    while drop < EDGE_SLIVER_PX:
        if not _sliver_row(work[-(drop + 1)], anchor):
            break
        drop += 1
    if drop:
        return work[: height - drop]
    return work


def _colour_pad(band, pad: int, forward: bool):
    """Continue one edge from its own colour. Nothing from inside the picture is copied out.

    A mirrored photograph, blurred on a coarse grid, stretches into blocky streaks.
    The colour here is a blur along the rim, plus a little grain that is the same
    in every direction, so it cannot become a stripe.
    `band` row 0 is the picture edge when forward is false.
    """
    import numpy as np

    width = int(band.shape[1])
    if pad <= 0:
        return np.zeros((0, width, 3), np.uint8)
    work = np.ascontiguousarray(band.astype(np.float32))
    if not forward:
        work = work[::-1]
    # A 1px Canva white column (or a 2px fringe) must not become the bleed colour.
    # A column that is white on only some rows is still that pale line.
    work = _drop_edge_sliver(work)
    if int(work.shape[0]) >= 2 and int(work.shape[1]) >= 1:
        from bleed_builder import colors_past_fringe

        oriented = np.ascontiguousarray(np.transpose(work[:, :, :3], (1, 0, 2))[:, ::-1])
        cleaned = colors_past_fringe(oriented)
        work = np.array(work, copy=True)
        work[-1, :, :3] = cleaned
    height = int(work.shape[0])
    depth = min(5, height)
    strip = work[-depth:]
    median = np.median(strip, axis=0)
    distance = np.linalg.norm(strip - median[None, :, :], axis=2)
    mad = float(np.median(distance)) if distance.size else 0.0
    thresh = max(36.0, mad * 4.0)
    outlier = distance > thresh
    cleaned = strip.copy()
    if outlier.any():
        filled = np.repeat(median[None, :, :], depth, axis=0)
        cleaned[outlier] = filled[outlier]
    edge = _lowpass_along(cleaned[-1], EDGE_LOWPASS_FRACTION)
    broad = _lowpass_along(edge, 0.15)
    broad_far = _lowpass_along(_lowpass_along(cleaned[0], EDGE_LOWPASS_FRACTION), 0.15)
    slope = np.clip((broad - broad_far) / float(max(depth - 1, 1)), -0.35, 0.35)
    steps = np.arange(1, pad + 1, dtype=np.float32)[:, None, None]
    fade = np.exp(-steps / 80.0)
    ease = np.clip((steps - 4.0) / 24.0, 0.0, 1.0)
    continued = edge[None, :, :] * (1.0 - ease) + broad[None, :, :] * ease
    continued = continued + slope[None, :, :] * steps * fade
    inlier = ~outlier
    pair = inlier[:, :-1] & inlier[:, 1:] if width > 1 else inlier
    sigma = np.zeros(3, np.float32)
    if width > 1:
        delta = cleaned[:, 1:, :] - cleaned[:, :-1, :]
        for channel in range(3):
            vals = delta[:, :, channel][pair]
            sigma[channel] = float(np.std(vals) / np.sqrt(2.0)) if vals.size else 0.0
    sigma = np.clip(sigma, 0.0, 8.0)
    rng = np.random.default_rng((depth * 10007 + width * 17 + pad) % (2**32))
    grain = rng.normal(0.0, 1.0, (pad, width, 3)).astype(np.float32) * sigma.reshape(1, 1, 3)
    true_edge = work[-1]
    seam = np.clip(steps / 4.0, 0.0, 1.0)
    grain_in = np.clip(steps / 4.0, 0.0, 1.0)
    mixed = true_edge[None, :, :] * (1.0 - seam) + continued * seam
    out = np.clip(np.rint(mixed + grain * grain_in), 0, 255).astype(np.uint8)
    if not forward:
        out = out[::-1]
    return out


def _extend_vertical(img, top: int, bottom: int, px_per_mm: float | None = None, protect_art: bool = False):
    import numpy as np

    ppm = float(px_per_mm) if px_per_mm else (300.0 / 25.4)
    parts = []
    height = img.shape[0]
    core = img if img.dtype == np.uint8 else np.clip(img, 0, 255).astype(np.uint8)
    art = core
    ramp = _mm_px(TALL_RAMP_MM, ppm, 8)
    match_depth = _mm_px(6.0, ppm, 4)
    sample = min(5, height)

    def strong_pad(forward: bool, pad: int, block: int):
        """The accepted tall join: blur starts near sigma 3 and feathers into the art."""
        nonlocal art
        source = core[-block:] if forward else core[:block]
        protect = _protect_rows(core, ppm, from_end=forward)
        overlap = _join_overlap(core, ppm, from_end=forward)
        if art is core:
            art = np.ascontiguousarray(core)
        built = _mirror_extend(
            source, pad, forward=forward, strong=True, ramp_px=ramp, protect_px=protect,
        )
        # The pad's first row touches the picture when the pad is below it.
        at_end = not forward
        built = _match_join_colour(built, art, at_end=at_end, depth=max(match_depth, overlap))
        # A shape gap must not be blended back into the artwork. The poster join still is.
        if not protect_art:
            art = _feather_into_art(built, art, overlap, at_end=at_end)
        return built

    def short_pad(forward: bool, pad: int):
        """A short side stays a flat continuation. A mirrored 28px rim becomes stripes."""
        source = core[-sample:] if forward else core[:sample]
        return _colour_pad(source, pad, forward=forward)

    top_part = None
    bottom_part = None
    if top:
        if top >= TALL_PAD_PX:
            top_part = strong_pad(False, top, min(top, height))
        else:
            top_part = short_pad(False, top)
    if bottom:
        if bottom >= TALL_PAD_PX:
            bottom_part = strong_pad(True, bottom, min(bottom, height))
        else:
            bottom_part = short_pad(True, bottom)
    if top_part is not None:
        parts.append(top_part)
    parts.append(art)
    if bottom_part is not None:
        parts.append(bottom_part)
    if len(parts) == 1:
        return parts[0]
    return np.concatenate(parts, axis=0)


def _extend_edges(img, top: int, bottom: int, left: int, right: int, px_per_mm: float | None = None, protect_art: bool = False):
    """Grow a picture without copying one row of pixels down the page."""
    import numpy as np

    out = img
    if top or bottom:
        out = _extend_vertical(out, top, bottom, px_per_mm=px_per_mm, protect_art=protect_art)
    if left or right:
        turned = np.ascontiguousarray(np.transpose(out, (1, 0, 2)))
        turned = _extend_vertical(turned, left, right, px_per_mm=px_per_mm, protect_art=protect_art)
        out = np.ascontiguousarray(np.transpose(turned, (1, 0, 2)))
    return out


def _bottom_bar_row(img) -> int | None:
    """Row where a flat contact band starts, or None when the bottom is just the picture.

    The band is a solid colour with the address painted on it, stopped by a rule
    or a change of colour. A flat page, a gradient and a photograph are not a band.
    """
    import numpy as np

    height, width = img.shape[:2]
    if height < 48 or width < 32:
        return None
    tail_n = min(10, height // 5)
    tail = img[-tail_n:].astype(np.float32)
    # Std across the row. A solid footer is near zero; channel differences are not.
    if float(tail.std(axis=1).mean()) > 10.0:
        return None
    colour = np.median(tail.reshape(-1, 3), axis=0)
    distance = np.linalg.norm(img.astype(np.float32) - colour.reshape(1, 1, 3), axis=2)
    frac = (distance < 60.0).mean(axis=1)
    if float(frac[-tail_n:].mean()) < 0.92:
        return None
    max_run = int(height * 0.28)
    min_run = max(8, int(height * 0.04))
    max_gap = max(8, int(height * 0.03))
    last_good = height - 1
    gap = 0
    stop = max(-1, height - 1 - max_run - max_gap)
    for y in range(height - 1, stop, -1):
        if frac[y] >= 0.40:
            last_good = y
            gap = 0
            continue
        # A gold rule or a photo edge. Do not step across it into the picture.
        if frac[y] < 0.32:
            break
        gap += 1
        if gap > max_gap:
            break
    start = int(last_good)
    run = height - start
    if run < min_run or run > max_run:
        return None
    above = frac[max(0, start - run):start]
    if above.size and float(np.mean(above)) > 0.72:
        return None
    return start


def _soften_art_rim(canvas, top: int, left: int, art_h: int, art_w: int, include_top: bool = True):
    """Feather only the outer rim of the picture into a blur. The contact band stays sharp."""
    import cv2
    import numpy as np

    top_depth = min(32, max(0, art_h // 8))
    side_depth = min(12, max(0, art_w // 10))
    if top_depth < 4 and side_depth < 4:
        return canvas
    y0, x0 = int(top), int(left)
    y1, x1 = y0 + int(art_h), x0 + int(art_w)
    art = canvas[y0:y1, x0:x1]
    if art.size == 0:
        return canvas
    blurred = cv2.GaussianBlur(art, (0, 0), 7)
    alpha = np.zeros(art.shape[:2], np.float32)
    if include_top and top_depth >= 4:
        ramp = np.linspace(0.62, 0.0, top_depth, dtype=np.float32)
        alpha[:top_depth] = np.maximum(alpha[:top_depth], ramp[:, None])
    if side_depth >= 4:
        ramp = np.linspace(0.45, 0.0, side_depth, dtype=np.float32)
        alpha[:, :side_depth] = np.maximum(alpha[:, :side_depth], ramp[None, :])
        alpha[:, -side_depth:] = np.maximum(alpha[:, -side_depth:], ramp[::-1][None, :])
    mixed = art.astype(np.float32) * (1.0 - alpha[..., None]) + blurred.astype(np.float32) * alpha[..., None]
    out = canvas.copy()
    out[y0:y1, x0:x1] = np.clip(np.rint(mixed), 0, 255).astype(np.uint8)
    return out


def _restitch(canvas, top: int, left: int, art_h: int, art_w: int, reach: int = 8, include_top: bool = True, include_sides: bool = True):
    """Pull a short pad's seam onto the picture. A tall blend already owns its join."""
    import numpy as np

    out = canvas
    y0, x0 = int(top), int(left)
    y1, x1 = y0 + int(art_h), x0 + int(art_w)
    if y0 <= 0 or x1 <= x0:
        return out
    if include_top:
        edge = out[y0, x0:x1].astype(np.float32)
        for dist in range(1, reach + 1):
            y = y0 - dist
            if y < 0:
                break
            weight = dist / float(reach + 1)
            row = out[y, x0:x1].astype(np.float32)
            out[y, x0:x1] = np.clip(edge * (1.0 - weight) + row * weight, 0, 255).astype(np.uint8)
    if not include_sides:
        return out
    for dist in range(1, reach + 1):
        weight = dist / float(reach + 1)
        x = x0 - dist
        if x >= 0:
            edge_col = out[y0:y1, x0].astype(np.float32)
            col = out[y0:y1, x].astype(np.float32)
            out[y0:y1, x] = np.clip(edge_col * (1.0 - weight) + col * weight, 0, 255).astype(np.uint8)
        x = x1 - 1 + dist
        if x < out.shape[1]:
            edge_col = out[y0:y1, x1 - 1].astype(np.float32)
            col = out[y0:y1, x].astype(np.float32)
            out[y0:y1, x] = np.clip(edge_col * (1.0 - weight) + col * weight, 0, 255).astype(np.uint8)
    return out


def _layout_with_bar(img, trim_w: float, trim_h: float):
    """Lengthen a footer poster. The band stays at the bottom, 4 mm inside the trim.

    Side pads of the same 4 mm keep flush lettering off the knife. The original
    rectangle is not scaled. Extra height above the picture is a mirrored blur.
    """
    height, width = img.shape[:2]
    if float(trim_w) <= 2.0 * SAFE_ZONE_MM + 1.0:
        return None
    canvas_w = int(round(width * float(trim_w) / (float(trim_w) - 2.0 * SAFE_ZONE_MM)))
    canvas_h = int(round(canvas_w * float(trim_h) / float(trim_w)))
    if canvas_w <= width + 2 or canvas_h <= height + 4:
        return None
    side_left = (canvas_w - width) // 2
    side_right = canvas_w - width - side_left
    bottom = max(1, int(round(SAFE_ZONE_MM * canvas_w / float(trim_w))))
    extra = canvas_h - height
    if extra <= bottom + 4:
        return None
    top = extra - bottom
    px_per_mm = float(canvas_w) / float(trim_w)
    vertical = _extend_edges(img, top, bottom, 0, 0, px_per_mm=px_per_mm)
    fitted = _extend_edges(vertical, 0, 0, side_left, side_right, px_per_mm=px_per_mm)
    if fitted.shape[0] != canvas_h or fitted.shape[1] != canvas_w:
        return None
    # A tall pad already feathered its join. The 4 mm side pads are still short.
    tall_top = top >= TALL_PAD_PX
    tall_side = side_left >= TALL_PAD_PX or side_right >= TALL_PAD_PX
    fitted = _soften_art_rim(fitted, top, side_left, height, width, include_top=not tall_top)
    fitted = _restitch(
        fitted, top, side_left, height, width,
        include_top=not tall_top, include_sides=not tall_side,
    )
    return fitted


def _image_bleed_for_product(path: str, trim_w: float, trim_h: float):
    """Centred bleed already in a raster, from its embedded DPI. None when the tag is missing."""
    if os.path.splitext(path)[1].lower() not in IMAGE_EXT:
        return None
    try:
        import numpy as np
        from PIL import Image

        from press_ready_engine import _existing_image_bleed

        with Image.open(path) as image:
            dpi = image.info.get("dpi") or (0, 0)
            dpi_x = float((dpi[0] if isinstance(dpi, tuple) else dpi) or 0)
            if dpi_x < 150:
                return None
            rgb = np.asarray(image.convert("RGB"))
        return _existing_image_bleed(np.ascontiguousarray(rgb[:, :, ::-1]), dpi_x, float(trim_w), float(trim_h))
    except Exception:
        return None


def _extend_to_product(img, trim_w: float, trim_h: float, source_path: str, decisions: list) -> tuple:
    """Keep the whole picture. Fill a shape gap by mirroring the rim, not by smearing one pixel.

    A full-page mirror would repeat the artwork. Copying the outer pixel draws
    stripes. A contact band is left at the bottom of the page.
    """
    from ai_artwork import ratios_differ

    del source_path
    height, width = img.shape[:2]
    src = width / float(max(height, 1))
    target = float(trim_w) / float(trim_h)
    delta = abs(src - target) / target if target else 0
    if not ratios_differ(width, height, trim_w, trim_h):
        decisions.append("The picture already matches the product shape, so nothing was cropped or stretched.")
        return img, False, delta
    if src > target and _bottom_bar_row(img) is not None:
        fitted = _layout_with_bar(img, trim_w, trim_h)
        if fitted is not None:
            decisions.append(
                "The picture was a different shape from the product. The contact band was kept at the bottom, 4 mm inside the trim. The extra space continues the picture with a soft edge. It was not stretched and the picture was not copied."
            )
            return fitted, True, delta
    if src > target:
        new_h = max(height, int(round(width / target)))
        pad = new_h - height
        top = pad // 2
        ppm = float(new_h) / float(trim_h) if trim_h else None
        fitted = _extend_edges(img, top, pad - top, 0, 0, px_per_mm=ppm, protect_art=True)
    else:
        new_w = max(width, int(round(height * target)))
        pad = new_w - width
        left = pad // 2
        ppm = float(new_w) / float(trim_w) if trim_w else None
        fitted = _extend_edges(img, 0, 0, left, pad - left, px_per_mm=ppm, protect_art=True)
    decisions.append(
        "The picture was a different shape from the product. The whole picture was kept and the gap was filled by continuing the edge colour and texture. It was not stretched and the picture was not copied."
    )
    return fitted, True, delta


def _text_near_trim(report: dict) -> bool:
    rescue = (report or {}).get("rescue") or {}
    note = str(rescue.get("note") or "")
    if "may still sit" in note:
        return True
    try:
        percent = float(rescue.get("percent") or 0)
    except (TypeError, ValueError):
        percent = 0
    return bool(rescue.get("applied")) and percent >= 2.9


def _boxes_mm(path: str) -> dict:
    import pymupdf as fitz

    doc = fitz.open(path)
    try:
        page = doc[0]
        media = page.mediabox
        trim = page.trimbox
        return {
            "mediaWidthMm": round(media.width * 25.4 / 72.0, 2),
            "mediaHeightMm": round(media.height * 25.4 / 72.0, 2),
            "trimWidthMm": round(trim.width * 25.4 / 72.0, 2),
            "trimHeightMm": round(trim.height * 25.4 / 72.0, 2),
        }
    finally:
        doc.close()


def _stamp_fogra39(path: str) -> None:
    """Tag a finished press file with the coated FOGRA39 output intent."""
    try:
        import pikepdf

        from generate_icc_profile import get_icc_profile_path

        with open(get_icc_profile_path(), "rb") as handle:
            icc_data = handle.read()
        pdf = pikepdf.open(path, allow_overwriting_input=True)
        try:
            icc_stream = pikepdf.Stream(pdf, icc_data)
            icc_stream["/N"] = 4
            pdf.Root["/OutputIntents"] = pikepdf.Array([
                pikepdf.Dictionary({
                    "/Type": pikepdf.Name("/OutputIntent"),
                    "/S": pikepdf.Name("/GTS_PDFX"),
                    "/OutputConditionIdentifier": pikepdf.String("FOGRA39"),
                    "/RegistryName": pikepdf.String("http://www.color.org"),
                    "/Info": pikepdf.String("Coated FOGRA39 (ISO 12647-2:2004)"),
                    "/DestOutputProfile": icc_stream,
                })
            ])
            pdf.save(path)
        finally:
            pdf.close()
    except Exception:
        return


def _compile_native_pixels(src: str, output_pdf: str, trim_w: float, trim_h: float) -> dict:
    """Build a pixels-as-points page in this process. The full compile starts another Python."""
    from press_ready_engine import compile_vector_press

    built = compile_vector_press(src, output_pdf, trim_w, trim_h, BLEED_MM)
    if not built.get("used"):
        return {"success": False}
    engine = dict(built.get("report") or {})
    _stamp_fogra39(output_pdf)
    bleed_quality = None
    try:
        from bleed_builder import check_press_pdf

        bleed_quality = check_press_pdf(output_pdf, trim_w, trim_h, BLEED_MM)
    except Exception as exc:
        bleed_quality = {
            "ok": False,
            "pages": [],
            "note": (
                "The bleed check could not read the press file "
                f"({str(exc)[:120]}). Check the edges before printing."
            ),
        }
    if isinstance(bleed_quality, dict) and not bleed_quality.get("ok"):
        engine["passed"] = False
        engine["status"] = "needs-attention"
        engine["reason"] = bleed_quality.get("note") or "The bleed does not continue the artwork."
    return {"success": True, "pressEngine": engine, "bleedQuality": bleed_quality}


def _compile(src: str, output_pdf: str, trim_w: float, trim_h: float) -> dict:
    script = os.path.join(os.path.dirname(__file__), "compile_press_pdf.py")
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    status = tempfile.NamedTemporaryFile(prefix="quick-status-", suffix=".json", delete=False)
    result = tempfile.NamedTemporaryFile(prefix="quick-result-", suffix=".json", delete=False)
    status.close()
    result.close()
    cmd = [
        sys.executable,
        script,
        "--input", src,
        "--output", output_pdf,
        "--strategy", "auto",
        "--color-space", "cmyk",
        "--trim-w", str(trim_w),
        "--trim-h", str(trim_h),
        "--bleed-mm", str(BLEED_MM),
        "--status-file", status.name,
        "--result-file", result.name,
        "--base-name", "quick-print",
        "--creep-mm", "0",
    ]
    try:
        proc = subprocess.run(
            cmd,
            cwd=repo,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=240,
            env={
                **__import__("host_paths", fromlist=["c_numeric_env"]).c_numeric_env(),
                "PYTHONUNBUFFERED": "1",
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUTF8": "1",
            },
        )
        payload = {}
        if os.path.exists(result.name):
            try:
                with open(result.name, "r", encoding="utf-8") as handle:
                    payload = json.load(handle)
            except Exception:
                payload = {}
        payload["returncode"] = proc.returncode
        if proc.returncode != 0 and not payload.get("error"):
            tail = (proc.stderr or "")[-500:]
            payload["error"] = tail or "The press compile did not finish."
        return payload
    finally:
        for path in (status.name, result.name):
            try:
                os.unlink(path)
            except OSError:
                pass


def _write_proof(press_path: str, png_path: str, pdf_path: str, caption: str) -> list:
    """One proof picture per press page. Page 1 keeps the original png name."""
    import pymupdf as fitz

    src = fitz.open(press_path)
    paths = []
    try:
        proof = fitz.open()
        footer = 32
        stem, ext = os.path.splitext(png_path)
        for index, page in enumerate(src):
            out = proof.new_page(width=page.rect.width, height=page.rect.height + footer)
            out.show_pdf_page(page.rect, src, index)
            trim = page.trimbox
            out.draw_rect(trim, color=(0.86, 0.05, 0.45), width=1.4)
            out.insert_text(
                (14, page.rect.height + 20),
                caption[:180],
                fontsize=8,
                fontname="helv",
                color=(0.15, 0.15, 0.15),
            )
            page_png = png_path if index == 0 else f"{stem}-{index + 1}{ext}"
            from large_job import file_bytes, proof_zoom

            zoom = proof_zoom(max(page.rect.width, page.rect.height), file_bytes(press_path))
            pix = out.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            pix.save(page_png)
            paths.append(page_png)
        proof.save(pdf_path, deflate=True, garbage=4)
        proof.close()
        return paths
    finally:
        src.close()


def _blank(light_info: dict, decisions: list, product: dict, quantity, notes: str) -> dict:
    return {
        "light": light_info["light"],
        "reasons": light_info["reasons"],
        "decisions": decisions,
        "clientMessage": light_info["clientMessage"],
        "pressPath": "",
        "proofPng": "",
        "proofPdf": "",
        "bleedMm": BLEED_MM,
        "existingBleedKept": False,
        "productId": product.get("id"),
        "productLabel": product.get("label"),
        "trimW": product.get("widthMm"),
        "trimH": product.get("heightMm"),
        "quantity": quantity,
        "notes": notes,
        "upscale": 0,
        "pressEngine": None,
        "enginePassed": False,
    }


def make_print_ready(
    src_path: str,
    output_dir: str,
    trim_w: float,
    trim_h: float,
    product_id: str,
    product_label: str,
    filename: str = "",
    quantity=None,
    notes: str = "",
    detect_size: bool = False,
    options: dict | None = None,
) -> dict:
    os.makedirs(output_dir, exist_ok=True)
    locale_seen = {}
    try:
        from host_paths import pin_c_locale

        locale_seen = pin_c_locale() or {}
    except Exception:
        pass
    display = _safe_name(filename or os.path.basename(src_path))
    decisions = [
        "Sales quick mode decided this file on its own. Nobody was asked a question.",
        "Bleed is 5 mm on every side.",
    ]
    product = {"id": product_id, "label": product_label, "widthMm": trim_w, "heightMm": trim_h}
    ext = _ext(src_path, display)

    if detect_size and ext in VECTOR_EXT | IMAGE_EXT:
        try:
            if ext in IMAGE_EXT:
                img = _read_image(src_path)
                if img is not None:
                    # Pixel size is not a trim size. Leave the default product.
                    pass
            else:
                opened, prep_error = _prepare_vector(src_path, ext) if ext in (".ai", ".eps") else (src_path, "")
                if opened and not prep_error:
                    measured = _pdf_trim_mm(opened)
                    if measured:
                        found, rotated = _match_product(*measured)
                        if found and rotated:
                            twin = _product_for_trim(float(found["heightMm"]), float(found["widthMm"]))
                            if twin:
                                found = twin
                            else:
                                found = {
                                    **found,
                                    "id": f"{found.get('id')}-landscape",
                                    "label": f"{found.get('label')} landscape",
                                    "widthMm": found["heightMm"],
                                    "heightMm": found["widthMm"],
                                }
                        if found:
                            product = found
                            trim_w = float(found["widthMm"])
                            trim_h = float(found["heightMm"])
                            decisions.append(f"The file's trim matches {found['label']}, so that size was used.")
        except Exception:
            pass

    decisions.insert(3, f"Product set to {product.get('label')} ({trim_w:g} × {trim_h:g} mm).")
    if quantity:
        decisions.append(f"Quantity noted: {quantity}.")
    if notes:
        decisions.append(f"Note from sales: {notes[:240]}")

    if ext in OFFICE_EXT:
        info = decide_light({"kind": "office"})
        return _finish(_blank(info, decisions, product, quantity, notes), output_dir)

    if ext not in IMAGE_EXT | VECTOR_EXT:
        info = decide_light({"kind": "unsupported", "readable": False})
        return _finish(_blank(info, decisions, product, quantity, notes), output_dir)

    if not os.path.exists(src_path) or os.path.getsize(src_path) < 16:
        info = decide_light({"kind": "corrupt", "readable": False})
        return _finish(_blank(info, decisions, product, quantity, notes), output_dir)

    work_path = src_path
    raster = None
    native_pixels = False
    aspect_extended = False
    aspect_delta = 0.0
    pending_shape = None
    footer_layout = False
    upscale = 1.0
    dpi_mode = ""
    dpi_detail = ""
    existing_kept = False
    over_incoming = False
    ocr_low = False
    ocr_doubtful = False
    ocr_doubtful_reason = ""
    vector_built = None
    engine = {}
    vector_source = None
    upscale_job = None
    lettering_note = "The original lettering is kept."
    chosen = options or {}
    skip_upscale = bool(chosen.get("skipUpscale"))
    concerns = [str(line) for line in (chosen.get("concerns") or []) if str(line).strip()]

    try:
        from press_ready_engine import plate_exceeds_memory

        source_ext = ext if ext in (".ai", ".eps") else ""
        if ext in (".ai", ".eps"):
            opened, prep_error = _prepare_vector(src_path, ext)
            if prep_error or not opened:
                info = decide_light({"kind": "corrupt", "readable": False})
                if ext == ".eps":
                    info["clientMessage"] = EPS_MESSAGE
                    info["reasons"] = [EPS_MESSAGE]
                else:
                    info["clientMessage"] = prep_error or CORRUPT_MESSAGE
                    info["reasons"] = ["This Illustrator file could not be opened."]
                return _finish(_blank(info, decisions, product, quantity, notes), output_dir)
            work_path = opened
            ext = ".pdf"
            decisions.append("The Illustrator file was read onto the normal PDF path.")

        request = str(chosen.get("editRequest") or "").strip()
        if request:
            work_path = _apply_cat_edit(work_path, output_dir, decisions, request)
        if chosen.get("replicateEdit"):
            work_path, replicate_done = _apply_replicate_edit(work_path, output_dir, decisions, trim_w, trim_h)
            if replicate_done:
                skip_upscale = True
        elif skip_upscale:
            decisions.append("Upscale with AI was left off, so the original picture was kept.")

        live_type = False
        if ext == ".pdf":
            from ai_rebuild import _pdf_has_live_type
            from large_job import file_bytes, is_large_job

            points = _page_points(work_path)
            pixel_product = _pixels_as_points_product(*points) if points else None
            if pixel_product is not None:
                # This wins over a trim that is just the page points read as millimetres.
                product = pixel_product
                trim_w = float(pixel_product["widthMm"])
                trim_h = float(pixel_product["heightMm"])
                _remember_product(decisions, product, trim_w, trim_h)
                decisions.append(
                    f"The page stores a picture's pixels as points, so it was matched to {pixel_product.get('label')}."
                )
                native_pixels = True
            # Live text and vector drawings stay as they are. A raster page can still be traced.
            live_type = _pdf_has_live_type(work_path)
            # A ~200 MB picture-PDF used to be killed at 300 s and never finished.
            # The working picture is a smaller render. Live type stays a PDF.
            if is_large_job(file_bytes(work_path)) and not live_type:
                try:
                    raster = _render_pdf_image(work_path, native=native_pixels)
                except Exception:
                    raster = None
                if raster is not None:
                    ext = ".png"
                    decisions.append(
                        "This file is large, so it was prepared from a smaller working picture. The press file is still built."
                    )
        if ext == ".pdf":
            measured = _pdf_trim_mm(work_path)
            fit = None
            if not native_pixels and measured:
                fit = _fit_page_to_trim(*measured, trim_w, trim_h)
            if fit and fit["rotated"]:
                turned = _product_for_trim(fit["trimW"], fit["trimH"])
                if turned:
                    product = turned
                else:
                    product = {
                        **product,
                        "widthMm": fit["trimW"],
                        "heightMm": fit["trimH"],
                        "label": f"{product.get('label')} landscape",
                    }
                trim_w = fit["trimW"]
                trim_h = fit["trimH"]
                _remember_product(decisions, product, trim_w, trim_h)
                decisions.append("The page matches this product when it is turned the other way, so that orientation was used.")
            if measured and not native_pixels:
                decisions.append(
                    f"The PDF trim is {measured[0]:.1f} × {measured[1]:.1f} mm."
                )
            if native_pixels:
                decisions.append("The page was sent straight to the press engine at the matched size.")
            elif fit:
                line, story = incoming_bleed_line(fit["bleed"])
                if story == "over":
                    over_incoming = True
                decisions.append(line)
            elif live_type:
                decisions.append(
                    "This PDF has live type and the wrong proportions. The edges cannot be extended without losing that type."
                )
                info = decide_light({"unextendable": True})
                return _finish(_blank(info, decisions, product, quantity, notes), output_dir)
            else:
                decisions.append(
                    "The PDF shape does not match the product. The page was placed whole and the edges were extended. It was not stretched."
                )
                if plate_exceeds_memory(trim_w, trim_h):
                    decisions.append("Large format keeps the vectors. Only the bleed edge is drawn.")
                else:
                    from large_job import file_bytes, is_large_job

                    raster = _render_pdf_image(work_path, native=native_pixels)
                    if is_large_job(file_bytes(work_path)):
                        decisions.append(
                            "This file is large, so it was prepared from a smaller working picture. The press file is still built."
                        )
                    ext = ".png"
        if plate_exceeds_memory(trim_w, trim_h) and ext in IMAGE_EXT:
            from large_job import HUGE_EDGE_PX, file_bytes, is_large_job

            place_src = work_path
            if is_large_job(file_bytes(work_path)) or _header_long(work_path) >= HUGE_EDGE_PX:
                place_src = _write_working_image(work_path, os.path.join(output_dir, "large-working.jpg"), 3600)
                decisions.append(
                    "This picture is very large, so a smaller working copy was placed before the bleed edge was added."
                )
            placed = os.path.join(output_dir, "large-placed.pdf")
            _place_large_image(place_src, placed, trim_w, trim_h)
            work_path = placed
            ext = ".pdf"
            raster = None
            decisions.append("Large format keeps the picture as placed. Only the 5 mm bleed edge is added.")
        if ext in IMAGE_EXT or raster is not None:
            if raster is None:
                raster = _read_image(work_path)
            if raster is not None:
                from large_job import file_bytes

                # A tall screenshot is reduced so the edge extend can finish.
                # Sharpness is still judged on the pixels the customer sent.
                orig_h, orig_w = raster.shape[:2]
                raster, reduced = _tame_raster(raster, trim_w, trim_h, file_bytes(src_path))
                if reduced:
                    decisions.append(
                        "The picture was very large, so the working copy was reduced before the press file was built. It is still placed at 300 DPI."
                    )
            if raster is None:
                info = decide_light({"kind": "corrupt", "readable": False})
                return _finish(_blank(info, decisions, product, quantity, notes), output_dir)
            src_h, src_w = raster.shape[:2]
            if float(raster.mean()) > 250 and float(raster.std()) < 4:
                info = decide_light({"missingContent": True})
                return _finish(_blank(info, decisions, product, quantity, notes), output_dir)
            judge_w, judge_h = (orig_w, orig_h) if reduced else (src_w, src_h)
            upscale = _cover_scale(judge_w, judge_h, trim_w, trim_h)
            if too_small(judge_w, judge_h, trim_w, trim_h):
                info = decide_light({
                    "tooSmall": True,
                    "tooSmallMessage": too_small_message(str(product.get("label") or "this size"), trim_w, trim_h),
                })
                return _finish(_blank(info, decisions, product, quantity, notes), output_dir)
            from ai_rebuild import assess

            # Assess the original file. Extending the shape must not hide an AI-sized picture.
            assess_path = src_path if _ext(src_path, display) in IMAGE_EXT else work_path
            verdict = assess(assess_path, trim_w, trim_h, BLEED_MM)
            detected = bool(verdict.get("detected"))
            raster, _turned = _rotate_to_product(raster, trim_w, trim_h, decisions)
            picture = raster
            pending_shape = aspect_fit(picture.shape[1], picture.shape[0], trim_w, trim_h)
            # Extra bleed on a 300 dpi raster is the trim plus a ring. Extending the
            # shape would treat that ring as the wrong aspect and the trim would be lost.
            kept_source_bleed = None
            if ext in IMAGE_EXT:
                kept_source_bleed = _image_bleed_for_product(src_path, trim_w, trim_h)
            over_raster = False
            if kept_source_bleed:
                over_raster = max(float(kept_source_bleed[side]) for side in ("left", "right", "top", "bottom")) > BLEED_MM + 0.6
            if over_raster:
                decisions.append("The picture already had more than 5 mm of bleed. Extra bleed is trimmed to 5 mm.")
                aspect_extended = False
                aspect_delta = 0.0
                vector_source = raster
                upscale_job = None
            else:
                kept_source_bleed = None
                # A contact band is laid out first so the trace sits on that page and
                # the band stays at the bottom. A plain side pad is not part of the
                # read: the smaller scale was dropping small marks such as a card's "7".
                raster, aspect_extended, aspect_delta = _extend_to_product(raster, trim_w, trim_h, work_path, decisions)
                footer_layout = bool(aspect_extended and _bottom_bar_row(picture) is not None)
                if footer_layout:
                    vector_source = raster
                else:
                    vector_source = picture
                if not skip_upscale:
                    try:
                        from ai_upscale import start_plate_upscale

                        upscale_job = start_plate_upscale(vector_source, trim_w, trim_h)
                    except Exception as exc:
                        sys.stderr.write(f"[AI-UPSCALE] not started ({str(exc)[:160]})\n")
                        upscale_job = None
                fitted_path = os.path.join(output_dir, "fitted.png")
                _write_png(raster, fitted_path)
                work_path = fitted_path
            from vector_text_v2 import font_substitution_enabled, vector_rebuild_enabled

            fonts_on = font_substitution_enabled()
            rebuild_on = vector_rebuild_enabled()
            lettering_blocks = None
            has_lettering = False
            outer_ocr_s = 0.0
            # Font mode on an AI file reads the page itself. Every other raster is read once here.
            if (not over_raster) and rebuild_on and not live_type and not (fonts_on and detected):
                try:
                    from vector_text_v2 import read_blocks
                    from vector_trace import is_lettering

                    ocr_started = time.perf_counter()
                    found = read_blocks(vector_source, extra=False)
                    outer_ocr_s = time.perf_counter() - ocr_started
                    has_lettering = any(
                        isinstance(block, dict) and is_lettering(str(block.get("text") or ""))
                        for block in (found or [])
                    )
                    if not fonts_on:
                        lettering_blocks = found
                except Exception:
                    has_lettering = False
                    lettering_blocks = None
            trace_raster = (not over_raster) and rebuild_on and not live_type and (detected or has_lettering)
            if trace_raster:
                if detected and fonts_on:
                    decisions.append("This looks like AI-generated artwork. The words are set as vector type.")
                elif detected:
                    decisions.append("This looks like AI-generated artwork. The lettering is traced as vector shapes.")
                elif fonts_on:
                    decisions.append("This raster has lettering. The words are set as vector type.")
                else:
                    decisions.append("This raster has lettering. The lettering is traced as vector shapes.")
                try:
                    from vector_text_v2 import rebuild_fitted

                    vector_built = rebuild_fitted(
                        vector_source,
                        trim_w,
                        trim_h,
                        os.path.join(output_dir, "press.pdf"),
                        progress=_mark,
                        blocks=None if fonts_on else lettering_blocks,
                        ocr_s=outer_ocr_s if lettering_blocks is not None else None,
                        upscale=upscale_job,
                    )
                except Exception as exc:
                    vector_built = {
                        "ok": False,
                        "amber": True,
                        "reason": f"Vector type failed ({str(exc)[:140]}). The original lettering was kept.",
                        "decisions": [],
                    }
                if vector_built.get("ok"):
                    work_path = os.path.join(output_dir, "press.pdf")
                    if vector_built.get("mode") == "trace":
                        lettering_note = "The lettering was traced as vector shapes."
                    else:
                        lettering_note = "The lettering was set as vector type."
                    aspect_extended = False
                    decisions = [line for line in decisions if "gap was filled" not in line]
                    decisions.append(
                        "The whole picture was placed with one scale. Any gap was filled by continuing the edge."
                    )
                    for line in vector_built.get("decisions") or []:
                        decisions.append(str(line))
                    if vector_built.get("amber"):
                        lettering_note = vector_built.get("reason") or lettering_note
                else:
                    lettering_note = str(vector_built.get("reason") or "The vector check failed, so the original lettering was kept.")
                    decisions.append(lettering_note)
            elif detected and not rebuild_on:
                decisions.append("Vector type is switched off. The original lettering is kept and the picture is enlarged.")
            elif live_type:
                decisions.append("This file already has live type, so the lettering was left as it is.")
            else:
                decisions.append("This was not treated as AI artwork. The original lettering is kept.")
            if over_raster:
                work_path = src_path
            elif skip_upscale:
                pass
            elif not (vector_built and vector_built.get("ok")):
                _mark("enlarging", lettering_note)
                upscaled_path = os.path.join(output_dir, "upscaled.png")
                source_ppi = 300.0 / max(float(upscale), 1e-6)
                try:
                    from ai_upscale import apply_ai_upscale

                    if source_ppi < 75:
                        dpi_mode = "under"
                        dpi_detail = f"About {source_ppi:.0f} ppi. Under 75 ppi, so the picture was not enhanced."
                        enlarged = {"used_original": True, "message": dpi_detail}
                    else:
                        enlarged = apply_ai_upscale(fitted_path, {
                            "trim_w_mm": trim_w,
                            "trim_h_mm": trim_h,
                            "bleed_mm": 0,
                            "output_path": upscaled_path,
                            "max_scale": 4,
                            "skip_below_ppi": 75,
                        })
                        if enlarged.get("used_original"):
                            dpi_mode = "under" if source_ppi < 75 else ""
                        elif float(upscale) > 4:
                            dpi_mode = "capped"
                            dpi_detail = f"About {source_ppi:.0f} ppi was enlarged by 4× and is still under 300 ppi."
                        elif float(upscale) > 1.05:
                            dpi_mode = "upscaled"
                            dpi_detail = f"About {source_ppi:.0f} ppi was enlarged toward 300 ppi, and not by more than 4×."
                except Exception as exc:
                    enlarged = {"used_original": True, "message": str(exc)[:160]}
                if enlarged.get("enhanced_path") and os.path.exists(str(enlarged.get("enhanced_path"))) and not enlarged.get("used_original"):
                    work_path = str(enlarged["enhanced_path"])
                    provider = str(enlarged.get("provider") or "basic")
                    if provider == "replicate":
                        decisions.append("The picture was enlarged with Real-ESRGAN. The original lettering was kept.")
                    else:
                        decisions.append("The picture was enlarged with Lanczos on this computer. The original lettering was kept.")
                else:
                    decisions.append("The original picture was kept and will be placed at 300 DPI. The original lettering was kept.")
            if vector_built and vector_built.get("ok"):
                if upscale >= 1.15:
                    decisions.append(
                        f"The original picture needs about {upscale:.1f}× to reach print size. The press picture is at least 400 PPI."
                    )
                else:
                    decisions.append("The original picture is already sharp enough. The press picture is at least 400 PPI.")
            elif upscale >= 1.15:
                decisions.append(f"The original picture needs about {upscale:.1f}× to reach 300 DPI at this size. The press file is built at 300 DPI.")
            else:
                decisions.append("The original picture is already sharp enough for 300 DPI at this size.")
    except Exception as exc:
        info = decide_light({"kind": "corrupt", "readable": False})
        decisions.append(f"The file could not be prepared ({str(exc)[:140]}).")
        return _finish(_blank(info, decisions, product, quantity, notes), output_dir)
    finally:
        if upscale_job is not None:
            try:
                upscale_job.cancel()
            except Exception:
                pass

    press_path = os.path.join(output_dir, "press.pdf")
    _mark("press", lettering_note)
    ink_report = None
    vector_ok = bool(vector_built and vector_built.get("ok") and os.path.exists(press_path) and os.path.getsize(press_path) > 1000)
    plate_ink = ((vector_built or {}).get("qa") or {}).get("plate_ink") if vector_ok else None
    if vector_ok and isinstance(plate_ink, dict):
        ink_report = {
            "images": 1 if plate_ink.get("changed") else 0,
            "rewritten": 1 if plate_ink.get("changed") else 0,
            "why": "in-plate" if plate_ink.get("changed") else "in-plate-unchanged",
            "painted": plate_ink.get("painted"),
            "dark": plate_ink.get("dark"),
            "locale": locale_seen,
        }
        sys.stderr.write("[INK] " + json.dumps(ink_report) + "\n")
    elif vector_ok:
        try:
            from client_file_audit import repair_cmyk_images

            # The traced page is already the press file. Small black type in that plate has to be K-only.
            ink_report = repair_cmyk_images(press_path, text_only=True)
        except Exception as exc:
            ink_report = {"images": 0, "rewritten": 0, "why": "raised", "error": str(exc)[:300]}
            sys.stderr.write(f"[INK] rewrite failed: {exc}\n")
    if vector_ok:
        compiled = {
            "success": True,
            "pressEngine": {
                "passed": True,
                "headline": "Vector type",
                "reason": "",
                "existingBleed": False,
            },
        }
        engine = compiled["pressEngine"]
        press_ok = True
        built_edges = (vector_built or {}).get("edges") if isinstance(vector_built, dict) else None
        if built_edges:
            engine["edges"] = built_edges
        if isinstance(vector_built, dict) and isinstance(vector_built.get("shape"), dict):
            engine["shape"] = vector_built.get("shape")
        decisions.append("Automatic bleed added the 5 mm edge. The trim sits 5 mm inside that edge.")
        _record_bleed_note(decisions, engine, built_edges)
    else:
        compiled = {}
        if native_pixels and ext == ".pdf":
            compiled = _compile_native_pixels(work_path, press_path, trim_w, trim_h)
        if not compiled.get("success"):
            compiled = _compile(work_path, press_path, trim_w, trim_h)
        engine = compiled.get("pressEngine") or {}
        press_ok = bool(compiled.get("success") and os.path.exists(press_path) and os.path.getsize(press_path) > 1000)
        edges = list(engine.get("edges") or []) if isinstance(engine, dict) else []
        trimmed = any(
            str(edge.get("method") or "") == "trimmed" or "trimmed to 5" in str(edge.get("note") or "").lower()
            for edge in edges
            if isinstance(edge, dict)
        )
        kept_now, extra_lines = extra_bleed_lines(
            over_incoming,
            bool(isinstance(engine, dict) and engine.get("fullBleedKept")),
            bool(isinstance(engine, dict) and engine.get("existingBleed")),
            press_ok,
            trimmed,
        )
        if kept_now:
            existing_kept = True
        for line in extra_lines:
            decisions.append(line)
        built_edges = (vector_built or {}).get("edges") if isinstance(vector_built, dict) else None
        _record_bleed_note(decisions, engine, built_edges)
    if isinstance(engine, dict) and engine.get("headline"):
        decisions.append(f"Press engine: {engine.get('headline')}.")

    shape_note = _shape_note_for(product.get("label"), vector_built, engine, pending_shape, footer_layout)
    if shape_note and shape_note not in decisions:
        decisions.append(shape_note)
    facts = {
        "kind": "image",
        "readable": True,
        "tooSmall": False,
        "compiled": press_ok,
        "compileError": str(compiled.get("error") or "")[:180],
        "ocrLow": ocr_low,
        "ocrDoubtful": ocr_doubtful,
        "ocrDoubtfulReason": ocr_doubtful_reason,
        "textNearTrim": _text_near_trim(engine if isinstance(engine, dict) else {}),
        "upscale": upscale,
        "aspectExtended": aspect_extended,
        "aspectDelta": aspect_delta,
        "shapeNote": shape_note,
        "enginePassed": bool(engine.get("passed")) if press_ok else False,
        "contentPullAmber": bool((engine.get("rescue") or {}).get("amber")) if isinstance(engine, dict) else False,
        "contentPullNote": str(((engine.get("rescue") or {}).get("note")) if isinstance(engine, dict) else "") or "",
        "engineReason": (engine.get("reason") if isinstance(engine, dict) else "") or "",
        "vectorAmber": bool(vector_built and vector_built.get("amber")),
        "vectorAmberReason": str((vector_built or {}).get("reason") or ""),
    }
    checklist = None
    if press_ok:
        try:
            from green_gate import assess

            checklist = assess(press_path, trim_w, trim_h, {
                "upscale": upscale,
                "textGate": (vector_built or {}).get("text_gate") or [],
                "edge": (vector_built or {}).get("edge") or {},
                "sourceBgr": vector_source,
                "placement": (vector_built or {}).get("placement") or {},
                "sourcePath": src_path,
            })
            black_row = next((item for item in (checklist.get("items") or []) if item.get("id") == "black"), None)
            if black_row and black_row.get("passed"):
                decisions.append("Black ink was checked on the original file.")
            elif black_row:
                decisions.append(str(black_row.get("detail") or "Black ink was corrected for press."))
            facts["checklist"] = checklist
        except Exception:
            checklist = None
    if native_pixels and press_ok and os.path.isfile(press_path):
        # The plate is one A4 picture. A full-pixel ink walk decodes it again
        # and is what pushed this make past half a minute on a laptop.
        _seed_pixel_page_ink(press_path)
    if os.path.isfile(src_path):
        try:
            from twenty_five import assess as assess_points
            from twenty_five import light_from_checks, settle_checks

            raw_points = assess_points(src_path, trim_w, trim_h)
            point_audit = (engine.get("clientAudit") if isinstance(engine, dict) else None) or {}
            settled = settle_checks(raw_points.get("checks") or [], {
                "bleed": bool(press_ok),
                "cmyk": bool(press_ok),
                "flattened": bool(press_ok),
                "black": bool(point_audit.get("black")),
                "hair": int(point_audit.get("hairlines") or 0) > 0,
                "spots": bool(point_audit.get("spots")),
                "dpi": dpi_mode,
                "dpiDetail": dpi_detail,
            })
            if press_ok:
                try:
                    from extra_checks import press_ink_facts

                    ink = press_ink_facts(press_path)
                    if isinstance(ink_report, dict):
                        ink_report["k_only"] = int(ink.get("k_only") or 0)
                        ink_report["max_tac"] = round(float(ink.get("max_tac") or 0), 1)
                        if str(ink_report.get("why") or "").startswith("in-plate") and ink_report["k_only"] <= 0 and not ink.get("small_k"):
                            ink_report["why"] = "in-plate-not-in-file"
                    for index, row in enumerate(settled):
                        if str(row.get("num")) != "2b":
                            continue
                        if ink.get("small_rich") or float(ink.get("max_tac") or 0) > 300.5:
                            detail = (
                                f"Black text in the press file is still four-colour"
                                f" and total ink is about {float(ink.get('max_tac') or 0):.0f}%."
                            )
                            settled[index] = {**row, "status": "warning", "pass": False, "detail": detail, "label": f"2b. {row.get('name')}: {detail}"}
                        elif ink.get("small_k") and float(ink.get("max_tac") or 0) <= 300.5:
                            detail = "Black text under 18 pt in the press file is K-only, and total ink is within 300%."
                            settled[index] = {**row, "status": "passed", "pass": True, "detail": detail, "label": f"2b. {row.get('name')}: {detail}"}
                        break
                except Exception:
                    pass
            derived = light_from_checks(settled)
            facts["twentyFive"] = {**derived, "checks": settled}
        except Exception:
            pass
        try:
            from extra_checks import assess_extras

            extra_report = assess_extras(
                src_path,
                press_path if press_ok else "",
                {
                    "widthMm": trim_w,
                    "heightMm": trim_h,
                    "explicitSize": True,
                    "cmykOnly": True,
                },
                apply=False,
            )
            facts["extraChecks"] = extra_report
        except Exception:
            pass
    if press_ok and os.path.isfile(press_path):
        cached_bleed = compiled.get("bleedQuality") if isinstance(compiled, dict) else None
        if isinstance(cached_bleed, dict) and "ok" in cached_bleed:
            # The compile already measured the edge. A second read decodes the plate again.
            facts["bleedQuality"] = cached_bleed
        else:
            try:
                from bleed_builder import check_press_pdf

                facts["bleedQuality"] = check_press_pdf(press_path, trim_w, trim_h, BLEED_MM)
            except Exception as exc:
                facts["bleedQuality"] = {
                    "ok": False,
                    "pages": [],
                    "note": (
                        "The bleed check could not read the press file "
                        f"({str(exc)[:120]}). Check the edges before printing."
                    ),
                }
        if facts["bleedQuality"].get("note"):
            decisions.append(str(facts["bleedQuality"]["note"]))
    info = decide_light(facts)
    if source_ext == ".eps":
        info = _eps_font_wording(info, facts)
    result = _blank(info, decisions, product, quantity, notes)
    result["checklist"] = list((checklist or {}).get("items") or [])
    result["prepressChecks"] = list((facts.get("twentyFive") or {}).get("checks") or [])
    result["extraChecks"] = list((facts.get("extraChecks") or {}).get("checks") or [])
    result["upscale"] = round(upscale, 3)
    result["existingBleedKept"] = existing_kept
    result["enginePassed"] = facts["enginePassed"]
    result["pressEngine"] = engine if isinstance(engine, dict) else None
    if facts.get("bleedQuality"):
        result["bleedQuality"] = facts["bleedQuality"]
    if press_saved_for_rebleed(press_ok, str(info.get("light") or "")):
        # A red light still has a press file. Change-bleed reads that file.
        result["pressPath"] = press_path
        try:
            boxes = _boxes_mm(press_path)
            result.update(boxes)
        except Exception:
            pass
        _mark("proof", lettering_note)
        proof_png = os.path.join(output_dir, "proof.png")
        proof_pdf = os.path.join(output_dir, "proof.pdf")
        try:
            proof_pages = _write_proof(
                press_path,
                proof_png,
                proof_pdf,
                "Proof. The pink line is the trim. This is not the press file.",
            )
            result["proofPng"] = proof_pages[0] if proof_pages else proof_png
            result["proofPaths"] = proof_pages
            result["pageCount"] = len(proof_pages) or 1
            result["proofPdf"] = proof_pdf
            decisions.append("A client proof with the trim line was made. No approval step is required.")
        except Exception as exc:
            decisions.append(f"The press file is ready. The proof picture could not be drawn ({str(exc)[:80]}).")
    elif not press_ok:
        result["pressPath"] = ""
    result["decisions"] = decisions
    bleed_note = str(engine.get("bleedNote") or "") if isinstance(engine, dict) else ""
    if not bleed_note:
        for line in decisions:
            if str(line).startswith("Bleed:"):
                bleed_note = str(line)
                break
    result["bleedNote"] = bleed_note
    before_path = os.path.join(output_dir, "before.png")
    if _write_before(src_path, before_path):
        result["beforePng"] = before_path
    try:
        from customer_message import customer_message

        if result.get("pressPath"):
            if not any("cmyk" in str(line).lower() for line in decisions):
                decisions.append("The press file was converted to CMYK.")
            _record_bleed_note(decisions, engine if isinstance(engine, dict) else {}, None)
            result["decisions"] = decisions
            worry = list(concerns)
            if info.get("light") in ("amber", "red"):
                for line in info.get("reasons") or []:
                    text = str(line)
                    if text and text not in worry:
                        worry.append(text)
            note = str(engine.get("bleedNote") or "") if isinstance(engine, dict) else ""
            if not _names_edges(note):
                for line in decisions:
                    if _names_edges(line):
                        note = str(line)
                        break
            result["bleedNote"] = note or result.get("bleedNote") or ""
            result["customerMessage"] = customer_message(decisions, worry, note)
        else:
            result["customerMessage"] = str(info.get("clientMessage") or "")
    except Exception:
        result["customerMessage"] = ""
    if ink_report:
        result["inkRepair"] = {
            "rewritten": int(ink_report.get("images") or ink_report.get("rewritten") or 0),
            "why": str(ink_report.get("why") or ink_report.get("error") or ""),
            "k_only": int(ink_report.get("k_only") or 0),
            "max_tac": ink_report.get("max_tac"),
            "skips": ink_report.get("skips") or [],
            "locale": ink_report.get("locale") or {},
        }
    result["letteringNote"] = lettering_note
    if vector_built:
        result["vectorText"] = {
            "ok": bool(vector_built.get("ok")),
            "amber": bool(vector_built.get("amber")),
            "elapsed_s": vector_built.get("elapsed_s"),
            "timings": vector_built.get("timings") or {},
            "provider": vector_built.get("provider") or "",
            "vectorLines": vector_built.get("vector_lines") or 0,
            "rasterLines": vector_built.get("raster_lines") or 0,
            "qa": vector_built.get("qa") or {},
            "lines": vector_built.get("lines") or [],
            "textGate": vector_built.get("text_gate") or [],
            "sourceGuard": bool(vector_built.get("source_guard")),
            "edge": vector_built.get("edge") or {},
            "placement": vector_built.get("placement") or {},
        }
    return _finish(result, output_dir)


def _mark(stage: str, note: str = "") -> None:
    try:
        from job_progress import write_progress_from_env

        write_progress_from_env(stage, note)
    except Exception:
        pass


def _finish(result: dict, output_dir: str) -> dict:
    _mark("done", str(result.get("letteringNote") or "Finished."))
    result_path = os.path.join(output_dir, "result.json")
    with open(result_path, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, ensure_ascii=False)
    lines = [f"Result: {str(result.get('light') or '').upper()}"]
    for line in result.get("decisions") or []:
        lines.append(f"- {line}")
    for line in result.get("reasons") or []:
        lines.append(f"- Look: {line}")
    if result.get("clientMessage"):
        lines.append("")
        lines.append(result["clientMessage"])
    with open(os.path.join(output_dir, "decisions.txt"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return result


def _force_utf8_stdio() -> None:
    """The customer note uses × and –. A chunked pipe must see real UTF-8."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8")
        except Exception:
            pass


def main() -> None:
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(description="Make one file print-ready")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--trim-w", type=float, default=148)
    parser.add_argument("--trim-h", type=float, default=210)
    parser.add_argument("--product-id", default="a5")
    parser.add_argument("--product-label", default="A5")
    parser.add_argument("--filename", default="")
    parser.add_argument("--quantity", type=int, default=0)
    parser.add_argument("--notes", default="")
    parser.add_argument("--detect-size", action="store_true")
    parser.add_argument("--progress-file", default="")
    parser.add_argument("--result", default="")
    parser.add_argument("--edit-request", default="")
    parser.add_argument("--replicate-edit", action="store_true")
    parser.add_argument("--skip-upscale", action="store_true")
    parser.add_argument("--concern", action="append", default=[])
    args = parser.parse_args()
    if args.progress_file:
        os.environ["JOB_PROGRESS_FILE"] = args.progress_file
        _mark("fitting", "Fitting the picture to the product.")
    if args.product_id and args.product_id != "auto":
        chosen = _product_by_id(args.product_id)
        if chosen:
            args.trim_w = float(chosen["widthMm"])
            args.trim_h = float(chosen["heightMm"])
            args.product_label = chosen["label"]
    result = make_print_ready(
        args.input,
        args.output_dir,
        args.trim_w,
        args.trim_h,
        args.product_id,
        args.product_label,
        filename=args.filename,
        quantity=args.quantity or None,
        notes=args.notes or "",
        detect_size=bool(args.detect_size or args.product_id == "auto"),
        options={
            "editRequest": args.edit_request or "",
            "replicateEdit": bool(args.replicate_edit),
            "skipUpscale": bool(args.skip_upscale),
            "concerns": list(args.concern or []),
        },
    )
    text = json.dumps(result, ensure_ascii=False)
    if args.result:
        with open(args.result, "w", encoding="utf-8") as handle:
            handle.write(text)
    sys.stdout.write(text + "\n")


if __name__ == "__main__":
    main()
