#!/usr/bin/env python3
"""Checks and fixes for the file the client actually uploaded.

Fonts are read from the original PDF (pikepdf FontFile streams, pdffonts when
it is installed). Type 3 fonts are allowed. Ghostscript must not be the thing
that decides a missing font is fine.

Hairlines, black ink, and spot colours are fixed in the vector content before
a press file is built. Resolution uses the worst picture that actually prints,
ignoring soft masks and pictures smaller than about 2% of the page.
"""

from __future__ import annotations

import math
import os
import re
import shutil
import subprocess

HAIRLINE_PT = 0.25
SMALL_TEXT_PT = 18.0
LARGE_PT = 56.0
TAC_LIMIT = 3.0
DPI_FLOOR = 75.0
RICH = (0.40, 0.30, 0.30, 1.0)
MIN_IMAGE_AREA = 0.02
FONT_ASK = "Please export with fonts embedded or outlined."
_TOKEN = re.compile(
    r"(-?\d+\.?\d*(?:[eE][+-]?\d+)?)"
    r"|(/[^\s\[\]<>()]+)"
    r"|([A-Za-z*'\"]+)"
    r"|(\[)"
    r"|(\])"
    r"|(<[0-9A-Fa-f\s]*>)"
    r"|(<<)"
    r"|(>>)"
    r"|(\((?:[^()\\]|\\.|\((?:[^()\\]|\\.)*\))*\))"
)
_SEP = re.compile(rb"/Separation\s*/([^\s\[\]<>/]+)")
_DEVICEN = re.compile(rb"/DeviceN\s*\[([^\]]*)\]")
_PROCESS = {"cyan", "magenta", "yellow", "black"}
_SUBSTITUTES = ("nimbus", "liberation", "texgyre", "urw")
_FONT_SUBTYPES = {
    "/Type0", "/Type1", "/MMType1", "/Type3", "/TrueType",
    "/CIDFontType0", "/CIDFontType2",
}


def _bare(name: str) -> str:
    text = str(name or "").strip().lstrip("/")
    if "+" in text:
        text = text.split("+", 1)[1]
    return text


def _tokenize(data: str) -> list[str]:
    return [match.group(0) for match in _TOKEN.finditer(data)]


def _is_op(token: str) -> bool:
    return bool(token) and not token.startswith("/") and bool(re.match(r"^[A-Za-z*'\"]+$", token))


def _num(token: str) -> float | None:
    try:
        return float(token)
    except (TypeError, ValueError):
        return None


def _scale(matrix: list[float]) -> float:
    if len(matrix) < 4:
        return 1.0
    area = abs(matrix[0] * matrix[3] - matrix[1] * matrix[2])
    return math.sqrt(area) if area > 1e-9 else 1.0


def _near_cmyk(c: float, m: float, y: float, k: float) -> bool:
    if min(c, m, y, k) >= 0.95:
        return True
    if c <= 0.08 and m <= 0.08 and y <= 0.08 and k >= 0.85:
        return True
    if k >= 0.8 and max(c, m, y) <= 0.55 and (max(c, m, y) - min(c, m, y)) <= 0.2:
        return True
    return False


def _rich_cmyk(c: float, m: float, y: float, k: float) -> bool:
    return k >= 0.5 and (c + m + y) >= 0.15 and not (c <= 0.08 and m <= 0.08 and y <= 0.08)


def _near_rgb(r: float, g: float, b: float) -> bool:
    return max(r, g, b) <= 0.12 and (max(r, g, b) - min(r, g, b)) <= 0.06


def _cap_tac(c: float, m: float, y: float, k: float) -> tuple[float, float, float, float]:
    total = c + m + y + k
    if total <= TAC_LIMIT + 0.001:
        return c, m, y, k
    extra = total - TAC_LIMIT
    cmy = c + m + y
    if cmy <= 1e-6:
        return 0.0, 0.0, 0.0, min(k, 1.0)
    scale = max(0.0, cmy - extra) / cmy
    return c * scale, m * scale, y * scale, min(k, 1.0)


def _next_rect(tokens: list[str], index: int, ctm: list[float]) -> tuple[float, float] | None:
    """The next rectangle, in points. Colour often comes before the rect."""
    pending: list[str] = []
    limit = min(len(tokens), index + 40)
    for tok in tokens[index + 1 : limit]:
        if tok == "re" and len(pending) >= 4:
            width = _num(pending[-2])
            height = _num(pending[-1])
            if width is None or height is None:
                return None
            scale = _scale(ctm)
            return abs(float(width)) * scale, abs(float(height)) * scale
        if _is_op(tok):
            return None
        pending.append(tok)
    return None


def _zone(level: float, ink: tuple[float, float, float, float] = RICH) -> tuple[float, float, float, float, bool] | None:
    """Ian's three zones. Over 70% is rich black with knockout. 30–69% overprints."""
    level = max(0.0, min(1.0, level))
    if level > 0.70:
        return (*ink, False)
    if level >= 0.30:
        return (0.0, 0.0, 0.0, level, True)
    if level >= 0.05:
        return (0.0, 0.0, 0.0, level, False)
    return None


def _black_replacement(
    c: float, m: float, y: float, k: float, small_text: bool, large: bool, registration: bool,
    ink: tuple[float, float, float, float] = RICH,
) -> tuple[float, float, float, float, bool] | None:
    """Text under 18 pt stays 100K. Small items go K-only, then the zones. Large dark areas go rich black."""
    total = c + m + y + k
    near = _near_cmyk(c, m, y, k) or registration
    if total > TAC_LIMIT + 0.001 and max(c, m, y, k) >= 0.80:
        return (*ink, False)
    if small_text and near:
        return (0.0, 0.0, 0.0, 1.0, True)
    if not near:
        if total > TAC_LIMIT + 0.001:
            return (*ink, False)
        return None
    if large:
        level = k if c <= 0.08 and m <= 0.08 and y <= 0.08 else max(c, m, y, k)
        if level > 0.70 or registration:
            return (*ink, False)
        return _zone(level, ink)
    return _zone(max(c, m, y, k), ink)


def _fmt(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".") if value != int(value) else f"{value:.4f}"


def _process_tokens(
    tokens: list[str],
    rewrite: bool,
    spots: dict[str, list[float]] | None = None,
    rich: tuple[float, float, float, float] | None = None,
) -> tuple[list[str] | None, dict]:
    """Read a content stream. When rewrite is set, thicken hairlines and fix black."""
    spots = spots or {}
    ink = tuple(rich) if rich else RICH
    found = {
        "hairlines": 0,
        "minStroke": None,
        "registration": False,
        "peakTac": 0.0,
        "richSmallText": 0,
        "largeKOnly": False,
        "blackFixes": 0,
    }
    new: list[str] = []
    stack: list[str] = []
    ctm = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
    gstack: list[list[float]] = []
    width = 1.0
    in_text = False
    font_size = 12.0
    tm = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
    fill_cs = ""
    stroke_cs = ""
    skip_ei = False
    changed = False
    overprint = False

    def font_pt() -> float:
        return abs(font_size) * _scale(tm) * _scale(ctm)

    def emit_ops(operands: list[str], operator: str) -> None:
        new.extend(operands)
        new.append(operator)

    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if skip_ei:
            if rewrite:
                new.append(tok)
            if tok.strip() == "EI":
                skip_ei = False
            i += 1
            continue
        if tok == "BI":
            if rewrite:
                new.extend(stack)
                new.append(tok)
            stack = []
            i += 1
            while i < len(tokens) and tokens[i].strip() != "ID":
                if rewrite:
                    new.append(tokens[i])
                i += 1
            if i < len(tokens):
                if rewrite:
                    new.append(tokens[i])
                i += 1
            skip_ei = True
            continue
        if not _is_op(tok):
            stack.append(tok)
            i += 1
            continue
        op = tok
        replaced = False
        if op == "q":
            gstack.append(list(ctm) + [width])
        elif op == "Q" and gstack:
            saved = gstack.pop()
            ctm = saved[:6]
            width = saved[6]
        elif op == "cm" and len(stack) >= 6:
            nums = [_num(item) for item in stack[-6:]]
            if all(item is not None for item in nums):
                a, b, c, d, e, f = [float(item) for item in nums]
                na = ctm[0] * a + ctm[2] * b
                nb = ctm[1] * a + ctm[3] * b
                nc = ctm[0] * c + ctm[2] * d
                nd = ctm[1] * c + ctm[3] * d
                ne = ctm[0] * e + ctm[2] * f + ctm[4]
                nf = ctm[1] * e + ctm[3] * f + ctm[5]
                ctm = [na, nb, nc, nd, ne, nf]
        elif op == "w" and stack:
            raw = _num(stack[-1])
            if raw is not None:
                width = abs(raw) * _scale(ctm)
                if found["minStroke"] is None or width < found["minStroke"]:
                    found["minStroke"] = width
                if rewrite and width < HAIRLINE_PT - 1e-4:
                    scale = _scale(ctm) or 1.0
                    stack = stack[:-1] + [f"{(HAIRLINE_PT / scale):.4f}"]
                    changed = True
        elif op == "BT":
            in_text = True
            tm = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
        elif op == "ET":
            in_text = False
        elif op == "Tm" and len(stack) >= 6:
            nums = [_num(item) for item in stack[-6:]]
            if all(item is not None for item in nums):
                tm = [float(item) for item in nums]
        elif op == "Tf" and len(stack) >= 2:
            size = _num(stack[-1])
            if size is not None:
                font_size = abs(size)
        elif op in ("cs", "CS") and stack and stack[-1].startswith("/"):
            if op == "cs":
                fill_cs = stack[-1][1:]
            else:
                stroke_cs = stack[-1][1:]
        elif op in ("S", "s", "B", "b", "B*", "b*"):
            if width < HAIRLINE_PT - 1e-4:
                found["hairlines"] += 1

        small_text = in_text and font_pt() < SMALL_TEXT_PT
        rect = _next_rect(tokens, i, ctm) if op in ("k", "K", "rg", "RG", "g", "G") else None
        large = bool(rect) and rect[0] >= LARGE_PT and rect[1] >= LARGE_PT and not in_text
        overprint = False
        if op in ("k", "K") and len(stack) >= 4:
            vals = [_num(item) for item in stack[-4:]]
            if all(item is not None for item in vals):
                c, m, y, k = [float(item) for item in vals]
                found["peakTac"] = max(found["peakTac"], (c + m + y + k) * 100.0)
                registration = min(c, m, y, k) >= 0.95
                if registration:
                    found["registration"] = True
                if _rich_cmyk(c, m, y, k) and small_text:
                    found["richSmallText"] += 1
                k_only = c <= 0.02 and m <= 0.02 and y <= 0.02 and k >= 0.85
                if k_only and large:
                    found["largeKOnly"] = True
                fixed = _black_replacement(c, m, y, k, small_text, large, registration, ink) if rewrite else None
                if fixed is not None:
                    nc, nm, ny, nk, overprint = fixed
                    if (nc, nm, ny, nk) != (c, m, y, k) or overprint:
                        prefix = stack[:-4]
                        new.extend(prefix)
                        if overprint:
                            new.extend(["/FAI_OP_ON", "gs"])
                        new.extend([_fmt(nc), _fmt(nm), _fmt(ny), _fmt(nk), op])
                        stack = []
                        changed = True
                        replaced = True
                        found["blackFixes"] += 1
                        overprint = False
        elif op in ("rg", "RG", "g", "G") and stack:
            rgb = op in ("rg", "RG")
            need = 3 if rgb else 1
            if len(stack) >= need:
                vals = [_num(item) for item in stack[-need:]]
                if all(item is not None for item in vals):
                    if rgb:
                        r, g, b = [float(item) for item in vals]
                        near = _near_rgb(r, g, b)
                    else:
                        gray = float(vals[0])
                        near = gray <= 0.12
                    if rewrite and near:
                        k_op = "k" if op in ("rg", "g") else "K"
                        prefix = stack[:-need]
                        darkness = 1.0 - (max(r, g, b) if rgb else gray)
                        fixed = _black_replacement(0.0, 0.0, 0.0, max(darkness, 0.85), small_text, large, False, ink)
                        if fixed is not None:
                            nc, nm, ny, nk, overprint = fixed
                            new.extend(prefix)
                            if overprint:
                                new.extend(["/FAI_OP_ON", "gs"])
                            new.extend([_fmt(nc), _fmt(nm), _fmt(ny), _fmt(nk), k_op])
                            if large and nk >= 0.85 and nc <= 0.02:
                                found["largeKOnly"] = True
                            stack = []
                            changed = True
                            replaced = True
                            found["blackFixes"] += 1
        elif op in ("scn", "SCN") and stack:
            space = fill_cs if op == "scn" else stroke_cs
            tint = _num(stack[-1])
            color = spots.get(space) if space else None
            if color and tint is not None and len(color) >= 4:
                if rewrite:
                    prefix = stack[:-1]
                    new.extend(prefix)
                    scaled = [max(0.0, min(1.0, channel * float(tint))) for channel in color[:4]]
                    nc, nm, ny, nk = _cap_tac(*scaled)
                    k_op = "k" if op == "scn" else "K"
                    new.extend([_fmt(nc), _fmt(nm), _fmt(ny), _fmt(nk), k_op])
                    stack = []
                    changed = True
                    replaced = True
        if not replaced:
            if rewrite:
                emit_ops(stack, op)
            stack = []
        i += 1
    if rewrite:
        new.extend(stack)
    if rewrite and not changed:
        return None, found
    return (new if rewrite else None), found


def _page_streams(pdf, page) -> list[bytes]:
    import pikepdf

    contents = page.get("/Contents")
    if contents is None:
        return []
    if isinstance(contents, pikepdf.Array):
        parts = []
        for ref in contents:
            try:
                parts.append(pdf.get_object(ref).read_bytes())
            except Exception:
                continue
        return parts
    try:
        return [contents.read_bytes()]
    except Exception:
        return []


def _write_page_stream(pdf, page, data: bytes) -> None:
    import pikepdf

    stream = pikepdf.Stream(pdf, data)
    page["/Contents"] = stream


def _ensure_overprint(pdf, page) -> None:
    import pikepdf

    resources = page.get("/Resources")
    if resources is None:
        resources = pikepdf.Dictionary()
        page["/Resources"] = resources
    ext = resources.get("/ExtGState")
    if ext is None:
        ext = pikepdf.Dictionary()
        resources["/ExtGState"] = ext
    if "/FAI_OP_ON" not in ext:
        ext["/FAI_OP_ON"] = pikepdf.Dictionary({
            "/Type": pikepdf.Name("/ExtGState"),
            "/OP": True,
            "/op": True,
            "/OPM": 1,
        })


def _spot_map(pdf) -> dict[str, list[float]]:
    """Resource name -> CMYK at tint 1, for Separation spaces we can convert."""
    import pikepdf

    found: dict[str, list[float]] = {}

    def color_at(func, tint: float) -> list[float] | None:
        try:
            ftype = int(func.get("/FunctionType"))
        except Exception:
            return None
        if ftype == 2:
            c0 = [float(item) for item in func.get("/C0", [])]
            c1 = [float(item) for item in func.get("/C1", [])]
            power = float(func.get("/N", 1) or 1)
            if len(c0) < 4 or len(c1) < 4:
                return None
            mix = tint ** power
            return [c0[i] + mix * (c1[i] - c0[i]) for i in range(4)]
        return None

    def walk(resources) -> None:
        if resources is None:
            return
        spaces = resources.get("/ColorSpace") or {}
        try:
            items = list(spaces.items())
        except Exception:
            items = []
        for key, value in items:
            try:
                if isinstance(value, pikepdf.Array) and str(value[0]) == "/Separation":
                    alt = value[2] if len(value) > 2 else None
                    if str(alt) == "/DeviceCMYK" and len(value) > 3:
                        color = color_at(value[3], 1.0)
                        if color:
                            found[str(key).lstrip("/")] = color
            except Exception:
                continue
            try:
                if str(value.get("/Subtype", "")) == "/Form":
                    walk(value.get("/Resources"))
            except Exception:
                continue
        xobjects = resources.get("/XObject") or {}
        try:
            copies = list(xobjects.values())
        except Exception:
            copies = []
        for value in copies:
            try:
                if str(value.get("/Subtype", "")) == "/Form":
                    walk(value.get("/Resources"))
            except Exception:
                continue

    for page in pdf.pages:
        walk(page.get("/Resources"))
    return found


def _strip_spots(pdf) -> int:
    import pikepdf

    removed = 0

    def walk(resources) -> None:
        nonlocal removed
        if resources is None:
            return
        spaces = resources.get("/ColorSpace")
        if isinstance(spaces, pikepdf.Dictionary):
            for key, value in list(spaces.items()):
                try:
                    kind = str(value[0]) if isinstance(value, pikepdf.Array) and len(value) else ""
                except Exception:
                    kind = ""
                if kind in ("/Separation", "/DeviceN"):
                    spaces[key] = pikepdf.Name("/DeviceCMYK")
                    removed += 1
        xobjects = resources.get("/XObject") or {}
        try:
            copies = list(xobjects.values())
        except Exception:
            copies = []
        for value in copies:
            try:
                if str(value.get("/Subtype", "")) == "/Form":
                    walk(value.get("/Resources"))
            except Exception:
                continue

    for page in pdf.pages:
        walk(page.get("/Resources"))
    return removed


def spot_names(path: str) -> list[str]:
    """Separation and DeviceN names anywhere in the file, including streams."""
    import pikepdf

    chunks = []
    try:
        with open(path, "rb") as handle:
            chunks.append(handle.read())
    except OSError:
        return []
    try:
        pdf = pikepdf.open(path)
    except Exception:
        pdf = None
    if pdf is not None:
        try:
            for obj in pdf.objects:
                if isinstance(obj, pikepdf.Stream):
                    try:
                        chunks.append(obj.read_bytes())
                    except Exception:
                        continue
        finally:
            pdf.close()
    raw = b"\n".join(chunks)
    names: list[str] = []
    seen = set()

    def add(name: str) -> None:
        clean = name.lstrip("/").strip()
        if not clean:
            return
        key = clean.lower()
        if key in seen or key in ("none", "all", "registration"):
            return
        seen.add(key)
        names.append(clean)

    for match in _SEP.finditer(raw):
        add(match.group(1).decode("latin1", "replace"))
    for match in _DEVICEN.finditer(raw):
        inner = match.group(1).decode("latin1", "replace")
        extras = []
        for part in re.findall(r"/([^\s/\[\]]+)", inner):
            if part.lower() not in _PROCESS:
                extras.append(part)
        if extras:
            add("+".join(extras))
    return names


def _decode_name(raw: str) -> str:
    text = str(raw or "").lstrip("/")
    if text.startswith("\ufeff"):
        text = text[1:]
    return text or "Unnamed font"


def _font_file(descriptor) -> bool:
    if descriptor is None:
        return False
    return any(key in descriptor for key in ("/FontFile", "/FontFile2", "/FontFile3"))


def _walk_fonts(pdf) -> list[dict]:
    import pikepdf

    rows = []
    seen = set()

    def add(font, depth: int = 0) -> None:
        if font is None or depth > 4:
            return
        try:
            ident = font.objgen
        except Exception:
            ident = id(font)
        if ident in seen:
            return
        seen.add(ident)
        subtype = str(font.get("/Subtype", ""))
        if subtype not in _FONT_SUBTYPES and str(font.get("/Type", "")) != "/Font":
            return
        base = _decode_name(str(font.get("/BaseFont", font.get("/Name", "Unnamed"))))
        descriptor = font.get("/FontDescriptor")
        font_name = ""
        if descriptor is not None:
            font_name = _decode_name(str(descriptor.get("/FontName", "")))
        embedded = subtype == "/Type3" or _font_file(descriptor)
        if subtype == "/Type0":
            embedded = True
            for child in font.get("/DescendantFonts") or []:
                add(child, depth + 1)
                try:
                    child_desc = child.get("/FontDescriptor")
                    if not _font_file(child_desc) and str(child.get("/Subtype", "")) != "/Type3":
                        embedded = False
                except Exception:
                    embedded = False
        rows.append({
            "name": base or font_name or "Unnamed font",
            "subtype": subtype,
            "embedded": embedded,
            "fontName": font_name,
            "type3": subtype == "/Type3",
        })

    def walk(resources, depth: int = 0) -> None:
        if resources is None or depth > 6:
            return
        fonts = resources.get("/Font") or {}
        try:
            values = list(fonts.values())
        except Exception:
            values = []
        for value in values:
            try:
                add(value)
            except Exception:
                continue
        xobjects = resources.get("/XObject") or {}
        try:
            copies = list(xobjects.values())
        except Exception:
            copies = []
        for value in copies:
            try:
                if str(value.get("/Subtype", "")) == "/Form":
                    walk(value.get("/Resources"), depth + 1)
            except Exception:
                continue

    for page in pdf.pages:
        walk(page.get("/Resources"))
    return rows


def _substituted(row: dict) -> bool:
    if row.get("type3") or not row.get("embedded"):
        return False
    base = _bare(row.get("name") or "").lower()
    face = _bare(row.get("fontName") or "").lower()
    if not base or not face or base == face:
        return False
    face_sub = any(tag in face for tag in _SUBSTITUTES)
    base_sub = any(tag in base for tag in _SUBSTITUTES)
    return face_sub and not base_sub


def _missing_glyphs(path: str) -> dict[str, int]:
    import pymupdf as fitz

    missing: dict[str, int] = {}
    doc = fitz.open(path)
    try:
        for page in doc:
            type3 = set()
            for font in page.get_fonts(full=True) or []:
                if len(font) > 2 and "Type3" in str(font[2] or ""):
                    type3.add(_bare(str(font[3] if len(font) > 3 else "")))
            try:
                traces = page.get_texttrace() or []
            except Exception:
                traces = []
            for span in traces:
                face = _bare(str(span.get("font") or ""))
                if face in type3:
                    continue
                for char in span.get("chars") or ():
                    if not isinstance(char, (tuple, list)) or len(char) < 2:
                        continue
                    try:
                        ucs = int(char[0])
                        gid = int(char[1])
                    except (TypeError, ValueError):
                        continue
                    if ucs <= 32 or gid > 0:
                        continue
                    missing[face or "Unnamed font"] = missing.get(face or "Unnamed font", 0) + 1
    finally:
        doc.close()
    return missing


def _pdffonts_unembedded(path: str) -> list[str] | None:
    binary = shutil.which("pdffonts")
    if not binary:
        return None
    try:
        completed = subprocess.run([binary, path], capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    lines = [line for line in (completed.stdout or "").splitlines() if line.strip()]
    if len(lines) < 3:
        return []
    names = []
    for line in lines[2:]:
        parts = line.split()
        if len(parts) < 5:
            continue
        # name may contain spaces; emb is the yes/no before sub.
        emb = ""
        for token in parts:
            if token.lower() in ("yes", "no"):
                emb = token.lower()
                break
        if emb == "no":
            names.append(parts[0])
    return names


def font_report(path: str) -> dict:
    import pikepdf

    problems = []
    type3 = []
    checked = False
    try:
        pdf = pikepdf.open(path)
    except Exception:
        return {"checked": False, "problems": [], "type3": [], "embedded": 0}
    try:
        checked = True
        rows = _walk_fonts(pdf)
    finally:
        pdf.close()
    missing = {}
    try:
        missing = _missing_glyphs(path)
    except Exception:
        missing = {}
    seen = set()
    embedded = 0
    for row in rows:
        name = row["name"] or "Unnamed font"
        if row.get("type3"):
            type3.append(name)
            continue
        reason = ""
        if not row.get("embedded"):
            reason = "not embedded"
        elif _substituted(row):
            reason = "substituted"
        if reason and name not in seen:
            seen.add(name)
            problems.append({"name": name, "reason": reason})
        elif row.get("embedded"):
            embedded += 1
    for name, count in missing.items():
        if count <= 0 or name in seen:
            continue
        if any(name == _bare(row["name"]) and row.get("type3") for row in rows):
            continue
        seen.add(name)
        problems.append({"name": name, "reason": "missing glyphs"})
    external = _pdffonts_unembedded(path)
    if external:
        for name in external:
            if name not in seen and "Type3" not in name:
                seen.add(name)
                problems.append({"name": name, "reason": "not embedded"})
    return {"checked": checked, "problems": problems, "type3": type3, "embedded": embedded}


def _image_rows(path: str) -> list[dict]:
    import pymupdf as fitz

    rows = []
    doc = fitz.open(path)
    try:
        for index, page in enumerate(doc):
            page_area = abs(float(page.rect.width) * float(page.rect.height)) or 1.0
            full = page.get_images(full=True) or []
            masks = set()
            labels = {}
            for item in full:
                if len(item) > 1 and int(item[1] or 0) > 0:
                    masks.add(int(item[1]))
                if len(item) > 7 and item[7]:
                    labels[int(item[0])] = str(item[7])
            try:
                info = page.get_image_info(xrefs=True) or []
            except Exception:
                info = []
            for image in info:
                xref = int(image.get("xref") or 0)
                if xref and xref in masks:
                    continue
                bbox = image.get("bbox")
                if not bbox or len(bbox) < 4:
                    continue
                area = abs((float(bbox[2]) - float(bbox[0])) * (float(bbox[3]) - float(bbox[1])))
                if area / page_area < MIN_IMAGE_AREA:
                    continue
                width_in = max(0.01, (float(bbox[2]) - float(bbox[0])) / 72.0)
                height_in = max(0.01, (float(bbox[3]) - float(bbox[1])) / 72.0)
                px_w = float(image.get("width") or 0)
                px_h = float(image.get("height") or 0)
                if px_w < 2 or px_h < 2:
                    continue
                ppi = min(px_w / width_in, px_h / height_in)
                rows.append({
                    "name": labels.get(xref) or f"image {xref or len(rows) + 1}",
                    "ppi": round(ppi, 1),
                    "page": index + 1,
                    "xref": xref,
                    "area": area / page_area,
                })
    finally:
        doc.close()
    return rows


def _vector_black(path: str) -> dict:
    import pikepdf

    summary = {
        "checked": False,
        "registration": False,
        "peakTac": 0.0,
        "richSmallText": 0,
        "largeKOnly": False,
        "hairlines": 0,
        "minStroke": None,
    }
    try:
        pdf = pikepdf.open(path)
    except Exception:
        return summary
    try:
        summary["checked"] = True
        for page in pdf.pages:
            parts = _page_streams(pdf, page)
            if not parts:
                continue
            text = b"\n".join(parts).decode("latin1", "replace")
            _new, found = _process_tokens(_tokenize(text), False)
            summary["registration"] = summary["registration"] or found["registration"]
            summary["peakTac"] = max(summary["peakTac"], found["peakTac"])
            summary["richSmallText"] += found["richSmallText"]
            summary["largeKOnly"] = summary["largeKOnly"] or found["largeKOnly"]
            summary["hairlines"] += found["hairlines"]
            if found["minStroke"] is not None:
                if summary["minStroke"] is None or found["minStroke"] < summary["minStroke"]:
                    summary["minStroke"] = found["minStroke"]
            if b"/Separation /All" in b"\n".join(parts) or b"/Separation /Registration" in b"\n".join(parts):
                summary["registration"] = True
    finally:
        pdf.close()
    summary["tacOver"] = summary["peakTac"] > 300.5
    summary["needsFix"] = bool(
        summary["registration"] or summary["tacOver"] or summary["richSmallText"] or summary["largeKOnly"] or summary["hairlines"]
    )
    return summary


def _resolution(path: str, trim_w: float | None, trim_h: float | None) -> dict:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp"):
        return _image_file_resolution(path, trim_w, trim_h)
    try:
        rows = _image_rows(path)
    except Exception:
        return {"checked": False, "worst": None, "images": [], "amber": False, "severe": False}
    low = [row for row in rows if row["ppi"] + 1 < 300]
    worst = min((row["ppi"] for row in rows), default=None)
    return {
        "checked": True,
        "worst": worst,
        "images": low,
        "amber": bool(low) and (worst or 0) >= DPI_FLOOR,
        "severe": worst is not None and worst < DPI_FLOOR and bool(low),
    }


def _image_file_resolution(path: str, trim_w: float | None, trim_h: float | None) -> dict:
    if not trim_w or not trim_h:
        return {"checked": False, "worst": None, "images": [], "amber": False, "severe": False}
    try:
        from PIL import Image
        with Image.open(path) as image:
            px_w, px_h = image.size
    except Exception:
        return {"checked": False, "worst": None, "images": [], "amber": False, "severe": False}
    ppi = min(px_w / (float(trim_w) / 25.4), px_h / (float(trim_h) / 25.4))
    name = os.path.basename(path)
    row = {"name": name, "ppi": round(ppi, 1), "page": 1}
    low = ppi + 1 < 300
    return {
        "checked": True,
        "worst": round(ppi, 1),
        "images": [row] if low else [],
        "amber": low and ppi >= DPI_FLOOR,
        "severe": low and ppi < DPI_FLOOR,
    }


def audit_pdf(path: str, trim_w: float | None = None, trim_h: float | None = None) -> dict:
    """Read the original file. Never raises."""
    ext = os.path.splitext(path)[1].lower()
    empty = {
        "path": path,
        "isPdf": False,
        "fonts": {"checked": False, "problems": [], "type3": [], "embedded": 0},
        "hairlines": {"checked": False, "count": 0, "minPt": None},
        "black": {"checked": False, "registration": False, "tacOver": False, "peakTac": 0.0, "richSmallText": 0, "largeKOnly": False, "needsFix": False},
        "resolution": {"checked": False, "worst": None, "images": [], "amber": False, "severe": False},
        "spots": {"checked": False, "names": []},
        "pages": 0,
    }
    if ext in (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp"):
        empty["resolution"] = _resolution(path, trim_w, trim_h)
        empty["fonts"] = {"checked": True, "problems": [], "type3": [], "embedded": 0, "picture": True}
        empty["hairlines"] = {"checked": True, "count": 0, "minPt": None, "picture": True}
        empty["black"] = {**empty["black"], "checked": True, "picture": True}
        empty["spots"] = {"checked": True, "names": []}
        empty["pages"] = 1
        return empty
    if not os.path.isfile(path):
        return empty
    try:
        import pikepdf
        pdf = pikepdf.open(path)
        pages = len(pdf.pages)
        pdf.close()
    except Exception:
        return empty
    fonts = font_report(path)
    black = _vector_black(path)
    spots = spot_names(path)
    return {
        "path": path,
        "isPdf": True,
        "pages": pages,
        "fonts": fonts,
        "hairlines": {
            "checked": black.get("checked", False),
            "count": int(black.get("hairlines") or 0),
            "minPt": black.get("minStroke"),
        },
        "black": black,
        "resolution": _resolution(path, trim_w, trim_h),
        "spots": {"checked": True, "names": spots},
    }


def low_res_sentence(resolution: dict) -> str:
    from green_gate import LOW_RES_MESSAGE

    images = resolution.get("images") or []
    if not images:
        return ""
    bits = [f"{row['name']} at {row['ppi']:.0f} ppi" for row in images[:6]]
    return f"{LOW_RES_MESSAGE} The low-resolution pictures are {', '.join(bits)}."


def font_sentence(fonts: dict) -> str:
    problems = fonts.get("problems") or []
    if not problems:
        return ""
    bits = [f"{row['name']} ({row['reason']})" for row in problems[:8]]
    return (
        "Thanks for the artwork. These fonts need attention: "
        + ", ".join(bits)
        + f". {FONT_ASK}"
    )


def combined_client_message(audit: dict, extra: list[str] | None = None) -> str:
    """Every real problem, in one note. Amber-only files still get their own lines."""
    parts = []
    font_line = font_sentence(audit.get("fonts") or {})
    if font_line:
        parts.append(font_line)
    resolution = audit.get("resolution") or {}
    if resolution.get("severe") or resolution.get("amber"):
        sentence = low_res_sentence(resolution)
        if sentence:
            parts.append(sentence)
    for line in extra or []:
        text = str(line or "").strip()
        if text and text not in parts:
            parts.append(text)
    spots = (audit.get("spots") or {}).get("names") or []
    if spots:
        parts.append(
            "Spot colours (" + ", ".join(spots[:6]) + ") were found. "
            "The press file converts them to CMYK."
        )
    hair = audit.get("hairlines") or {}
    if hair.get("checked") and int(hair.get("count") or 0) > 0:
        parts.append(
            f"Thin lines under {HAIRLINE_PT:.2f} pt were found ({int(hair['count'])}). "
            f"They are raised to {HAIRLINE_PT:.2f} pt in the press file."
        )
    black = audit.get("black") or {}
    if black.get("checked") and (black.get("registration") or black.get("tacOver") or black.get("richSmallText") or black.get("largeKOnly")):
        bits = []
        if black.get("registration"):
            bits.append("registration black")
        if black.get("tacOver"):
            bits.append(f"ink about {black.get('peakTac', 0):.0f}%")
        if black.get("richSmallText"):
            bits.append("rich black on text under 18 pt")
        if black.get("largeKOnly"):
            bits.append("a large area of black with no other ink")
        parts.append("Black ink needs a press fix: " + ", ".join(bits) + ".")
    return "\n\n".join(parts)


def apply_vector_fixes(src: str, dest: str, rich: tuple[float, float, float, float] | None = None) -> dict:
    """Thicken hairlines, fix black, and convert spot tints in the content stream."""
    import pikepdf

    result = {"hairlines": 0, "blackFixes": 0, "spots": 0, "rewritten": False}
    pdf = pikepdf.open(src)
    try:
        spots = _spot_map(pdf)
        for page in pdf.pages:
            parts = _page_streams(pdf, page)
            if not parts:
                continue
            original = b"\n".join(parts)
            text = original.decode("latin1", "replace")
            tokens = _tokenize(text)
            rewritten, found = _process_tokens(tokens, True, spots, rich)
            result["hairlines"] += int(found.get("hairlines") or 0)
            result["blackFixes"] += int(found.get("blackFixes") or 0)
            if not rewritten:
                continue
            data = " ".join(rewritten).encode("latin1", "replace")
            if len(data) < max(32, int(len(original) * 0.4)):
                continue
            if b"/FAI_OP_ON" in data:
                _ensure_overprint(pdf, page)
            _write_page_stream(pdf, page, data)
            result["rewritten"] = True
        result["spots"] = _strip_spots(pdf)
        if result["spots"]:
            result["rewritten"] = True
        same = os.path.abspath(src) == os.path.abspath(dest)
        target = dest + ".writing.pdf" if same else dest
        pdf.save(target)
    finally:
        pdf.close()
    if os.path.abspath(src) == os.path.abspath(dest):
        os.replace(dest + ".writing.pdf", dest)
    return result


def repair_cmyk_images(path: str) -> dict:
    """Large near-black picture areas become rich black. Small marks stay 100K. Ink stays at or under 300%."""
    import pikepdf
    from PIL import Image
    import io
    import numpy as np

    changed = 0
    pdf = pikepdf.open(path)
    try:
        masks = set()
        for page in pdf.pages:
            xobjects = (page.get("/Resources") or {}).get("/XObject") or {}
            try:
                values = list(xobjects.values())
            except Exception:
                values = []
            for value in values:
                try:
                    smask = value.get("/SMask")
                    if smask is not None:
                        masks.add(smask.objgen)
                except Exception:
                    continue
        for page in pdf.pages:
            xobjects = (page.get("/Resources") or {}).get("/XObject") or {}
            try:
                items = list(xobjects.items())
            except Exception:
                items = []
            page_w = float(page.mediabox[2] - page.mediabox[0]) or 1.0
            for _key, value in items:
                try:
                    if str(value.get("/Subtype", "")) != "/Image":
                        continue
                    if value.objgen in masks:
                        continue
                    if str(value.get("/ColorSpace", "")) != "/DeviceCMYK":
                        continue
                    raw = value.read_bytes()
                    width = int(value.get("/Width") or 0)
                    height = int(value.get("/Height") or 0)
                    if width > 0 and height > 0 and len(raw) == width * height * 4:
                        arr = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 4).copy()
                    else:
                        image = Image.open(io.BytesIO(raw))
                        if image.mode != "CMYK":
                            continue
                        arr = np.asarray(image).copy()
                    if arr.ndim != 3 or arr.shape[2] < 4:
                        continue
                    dpi = 72.0 * (arr.shape[1] / page_w)
                    if _repair_array(arr, dpi):
                        fresh = pikepdf.Stream(pdf, arr[:, :, :4].tobytes())
                        fresh["/Type"] = pikepdf.Name("/XObject")
                        fresh["/Subtype"] = pikepdf.Name("/Image")
                        fresh["/Width"] = width
                        fresh["/Height"] = height
                        fresh["/ColorSpace"] = pikepdf.Name("/DeviceCMYK")
                        fresh["/BitsPerComponent"] = 8
                        xobjects[pikepdf.Name(str(_key))] = fresh
                        changed += 1
                except Exception:
                    continue
        if changed:
            tmp = path + ".ink.pdf"
            pdf.save(tmp)
            os.replace(tmp, path)
    finally:
        pdf.close()
    return {"images": changed}


def _repair_array(arr, dpi: float) -> bool:
    import cv2
    import numpy as np

    c = arr[:, :, 0].astype(np.float32)
    m = arr[:, :, 1].astype(np.float32)
    y = arr[:, :, 2].astype(np.float32)
    k = arr[:, :, 3].astype(np.float32)
    changed = False
    total = c + m + y + k
    over = total > (TAC_LIMIT * 255.0 + 1)
    if int(over.sum()) > 0:
        extra = total - TAC_LIMIT * 255.0
        cmy = c + m + y
        scale = np.ones_like(cmy)
        good = cmy > 1
        scale[good] = np.clip((cmy[good] - extra[good]) / cmy[good], 0, 1)
        c[over] *= scale[over]
        m[over] *= scale[over]
        y[over] *= scale[over]
        changed = True
    chroma = np.maximum(np.maximum(c, m), y) - np.minimum(np.minimum(c, m), y)
    dark = ((k >= 210) & (c <= 40) & (m <= 40) & (y <= 40))
    dark |= (c >= 240) & (m >= 240) & (y >= 240) & (k >= 240)
    dark |= (k >= 150) & (c >= 40) & (m >= 40) & (y >= 40) & (chroma <= 30)
    if int(dark.sum()) < 16:
        if changed:
            arr[:, :, 0] = np.clip(c, 0, 255).astype(np.uint8)
            arr[:, :, 1] = np.clip(m, 0, 255).astype(np.uint8)
            arr[:, :, 2] = np.clip(y, 0, 255).astype(np.uint8)
            arr[:, :, 3] = np.clip(k, 0, 255).astype(np.uint8)
        return changed
    count, labels, stats, _centroids = cv2.connectedComponentsWithStats(dark.astype(np.uint8), 8)
    text_px = max(8.0, 18.0 / 72.0 * max(dpi, 72.0))
    rich = np.array([round(RICH[0] * 255), round(RICH[1] * 255), round(RICH[2] * 255), 255], np.uint8)
    k_only = np.array([0, 0, 0, 255], np.uint8)
    for label in range(1, count):
        _x, _y, width, height, area = stats[label]
        if area < 12:
            continue
        mask = labels == label
        if height <= text_px and width <= text_px * 24:
            arr[mask] = k_only
        else:
            arr[mask] = rich
        changed = True
    return changed


def prepare_original(src: str, dest: str | None = None) -> dict:
    """Audit the original, then write a vector-fixed copy when something can be fixed."""
    audit = audit_pdf(src)
    target = dest or (src + ".prepared.pdf")
    fixes = {"hairlines": 0, "blackFixes": 0, "spots": 0, "rewritten": False, "images": 0}
    needs = bool(
        (audit.get("hairlines") or {}).get("count")
        or (audit.get("black") or {}).get("needsFix")
        or (audit.get("spots") or {}).get("names")
    )
    if audit.get("isPdf") and needs:
        fixes = apply_vector_fixes(src, target)
        image_fix = repair_cmyk_images(target)
        fixes["images"] = image_fix.get("images", 0)
    elif dest:
        shutil.copyfile(src, dest)
    return {"audit": audit, "fixes": fixes, "path": target if (needs or dest) else src}
