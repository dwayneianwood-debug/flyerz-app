"""Files the press scripts open on Windows and on Linux.

ICC profiles are bundled under server/assets/icc. Fonts used for overlays
and checks are the Liberation faces already in server/fonts. Potrace is
looked up on PATH, then in C:\\Flyerz\\tools\\potrace, then via POTRACE_PATH.
"""

from __future__ import annotations

import glob
import os
import shutil

_HERE = os.path.dirname(os.path.abspath(__file__))
ICC_DIR = os.path.join(_HERE, "assets", "icc")
FONT_DIR = os.path.join(_HERE, "fonts")

_ON = {"1", "on", "true", "yes"}


def esrgan_enabled() -> bool:
    """Real-ESRGAN is opt-in. The trace plate is local Lanczos unless this is set.

    VECTOR_SKIP_ESRGAN=1 still forces the local plate, even when VECTOR_ESRGAN is set.
    """
    if (os.environ.get("VECTOR_SKIP_ESRGAN") or "").strip().lower() in _ON:
        return False
    return (os.environ.get("VECTOR_ESRGAN") or "").strip().lower() in _ON


def icc_file(name: str) -> str:
    """A CMYK or sRGB profile. Env override, then the bundled copy, then Ghostscript."""
    specific = ""
    lowered = name.lower()
    if "cmyk" in lowered:
        specific = (os.environ.get("CMYK_ICC") or "").strip().strip('"')
    elif "srgb" in lowered or lowered.startswith("rgb"):
        specific = (os.environ.get("SRGB_ICC") or "").strip().strip('"')
    candidates = []
    if specific:
        candidates.append(specific)
    override_dir = (os.environ.get("ICC_PROFILE_DIR") or os.environ.get("GS_ICC_DIR") or "").strip().strip('"')
    if override_dir:
        candidates.append(os.path.join(override_dir, name))
    candidates.append(os.path.join(ICC_DIR, name))
    for folder in _ghostscript_icc_dirs():
        candidates.append(os.path.join(folder, name))
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    raise FileNotFoundError(f"ICC profile {name} was not found. Expected it in {ICC_DIR}.")


def _ghostscript_icc_dirs() -> list:
    """Install folders, including C:\\Program Files\\gs\\gs*\\iccprofiles. No fixed Linux path."""
    folders = []
    try:
        from gs_binary import find_gs_binary

        binary = find_gs_binary()
    except Exception:
        binary = ""
    if binary and os.path.isabs(binary):
        root = os.path.dirname(os.path.dirname(binary))
        folders.append(os.path.join(root, "iccprofiles"))
        folders.append(os.path.join(root, "share", "color", "icc", "ghostscript"))
    roots = []
    for key in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        value = os.environ.get(key)
        if value:
            roots.append(value)
    roots.append(os.path.join("C:\\", "Program Files"))
    seen = set()
    for root in roots:
        if not root or root in seen:
            continue
        seen.add(root)
        folders.extend(glob.glob(os.path.join(root, "gs", "gs*", "iccprofiles")))
    return folders


def find_potrace() -> str:
    """Potrace CLI. POTRACE_PATH may be the program or the folder that contains it."""
    raw = (os.environ.get("POTRACE_PATH") or "").strip().strip('"')
    if raw:
        found = _potrace_in(raw)
        if found:
            return found
    for folder in (
        os.path.join("C:\\", "Flyerz", "tools", "potrace"),
        os.path.join("C:\\", "Flyerz", "tools"),
    ):
        found = _potrace_in(folder)
        if found:
            return found
    which = shutil.which("potrace") or shutil.which("potrace.exe")
    return which or "potrace"


def _potrace_in(raw: str) -> str:
    if os.path.isdir(raw):
        for name in ("potrace.exe", "potrace"):
            candidate = os.path.join(raw, name)
            if os.path.isfile(candidate):
                return candidate
        return ""
    if os.path.isfile(raw):
        return raw
    which = shutil.which(raw)
    return which or ""


def overlay_fonts(bold: bool) -> list:
    """Faces for a sharp overlay. Bundled Liberation, then the Windows font folder."""
    bold_face = os.path.join(FONT_DIR, "LiberationSans-Bold.ttf")
    regular = os.path.join(FONT_DIR, "LiberationSans-Regular.ttf")
    paths = [bold_face, regular] if bold else [regular, bold_face]
    windir = (os.environ.get("WINDIR") or os.environ.get("SystemRoot") or "").strip()
    if windir:
        fonts = os.path.join(windir, "Fonts")
        arial_bold = os.path.join(fonts, "arialbd.ttf")
        arial = os.path.join(fonts, "arial.ttf")
        calibri = os.path.join(fonts, "calibri.ttf")
        if bold:
            paths.extend([arial_bold, arial, calibri])
        else:
            paths.extend([arial, arial_bold, calibri])
    return paths


def bundled_sans(bold: bool = True) -> str:
    name = "LiberationSans-Bold.ttf" if bold else "LiberationSans-Regular.ttf"
    return os.path.join(FONT_DIR, name)
