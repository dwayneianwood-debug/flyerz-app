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


def replicate_settings_path() -> str:
    override = (os.environ.get("REPLICATE_SETTINGS_PATH") or "").strip()
    if override:
        return os.path.abspath(override)
    return os.path.join(os.path.dirname(_HERE), "data", "replicate-settings.json")


def replicate_switch_on() -> bool:
    """The in-app Replicate On / Off switch. Read from the file on every call. Default On."""
    import json

    try:
        with open(replicate_settings_path(), "r", encoding="utf-8") as handle:
            parsed = json.load(handle)
        if isinstance(parsed, dict) and parsed.get("enabled") is False:
            return False
    except Exception:
        pass
    return True


def esrgan_enabled() -> bool:
    """Real-ESRGAN through Replicate. Never when the switch is Off.

    Inside a quick-print job, FLYERZ_REPLICATE_TICK says whether the user ticked an AI box:
    ticked means on (switch permitting), unticked means never. With no tick setting
    (other tools and checks) the old VECTOR_ESRGAN opt-in applies.
    VECTOR_SKIP_ESRGAN=1 still forces the local plate.
    """
    if (os.environ.get("VECTOR_SKIP_ESRGAN") or "").strip().lower() in _ON:
        return False
    if not replicate_switch_on():
        return False
    tick = os.environ.get("FLYERZ_REPLICATE_TICK")
    if tick is not None and tick != "":
        return tick.strip().lower() in _ON
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
    """Faces for a sharp overlay. Bundled Liberation only, so Linux and Windows match."""
    bold_face = os.path.join(FONT_DIR, "LiberationSans-Bold.ttf")
    regular = os.path.join(FONT_DIR, "LiberationSans-Regular.ttf")
    return [bold_face, regular] if bold else [regular, bold_face]


def bundled_sans(bold: bool = True) -> str:
    name = "LiberationSans-Bold.ttf" if bold else "LiberationSans-Regular.ttf"
    return os.path.join(FONT_DIR, name)


def pin_c_locale() -> dict:
    """Pin this process to a dot decimal before any float formatting or Ghostscript spawn.

    Ian's laptop locale uses a comma. LC_ALL hides LC_NUMERIC from child processes,
    so the parent locale is recorded and then cleared.
    """
    import locale

    seen = {
        "LC_ALL": os.environ.get("LC_ALL") or "",
        "LC_NUMERIC": os.environ.get("LC_NUMERIC") or "",
    }
    os.environ.pop("LC_ALL", None)
    os.environ["LC_NUMERIC"] = "C"
    try:
        locale.setlocale(locale.LC_NUMERIC, "C")
        seen["pinned"] = locale.setlocale(locale.LC_NUMERIC)
    except locale.Error as exc:
        seen["pinned"] = f"failed:{exc}"
    return seen


def c_numeric_env(base: dict | None = None) -> dict:
    """Environment for potrace and Ghostscript.

    LC_ALL wins over LC_NUMERIC, so a parent locale of en-ZA (decimal comma on
    Windows) would make printf and strtod disagree with the dotted arguments we
    pass. Dropping LC_ALL and pinning LC_NUMERIC to C keeps SVG and PDF numbers
    on a dot. PYTHONUTF8 on the parent still covers file names.
    """
    env = dict(os.environ if base is None else base)
    env.pop("LC_ALL", None)
    env["LC_NUMERIC"] = "C"
    return env
