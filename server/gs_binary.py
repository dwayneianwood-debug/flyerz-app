"""One Ghostscript lookup for every press script.

Unix installs expose ``gs``. Windows installers expose ``gswin64c`` or
``gswin32c``, often under ``C:\\Program Files\\gs\\*\\bin`` and not on PATH.
"""

from __future__ import annotations

import glob
import os
import shutil

# Optional overrides. None of these are required.
ENV_KEYS = ("GS_BIN", "GHOSTSCRIPT_PATH", "GS_PATH")
COMMAND_NAMES = ("gs", "gswin64c", "gswin32c", "gswin64")
WINDOWS_EXES = ("gswin64c.exe", "gswin32c.exe", "gswin64.exe")


def ghostscript_succeeded(returncode, output_path, min_bytes: int = 1) -> bool:
    """A Ghostscript run worked when it finished and the output file is real.

    The copyright banner and "errors were repaired" notes are informational.
    They are not a failure. A killed process (negative exit) is a failure
    even when a partial file was left behind. A non-zero exit with a
    non-empty file still counts: some builds exit 1 after that repair note.
    """
    if isinstance(returncode, bool) or not isinstance(returncode, int):
        return False
    if returncode < 0:
        return False
    try:
        return os.path.isfile(output_path) and os.path.getsize(output_path) >= min_bytes
    except OSError:
        return False


def find_gs_binary() -> str:
    for key in ENV_KEYS:
        raw = (os.environ.get(key) or "").strip().strip('"')
        if not raw:
            continue
        if os.path.isfile(raw) and os.access(raw, os.X_OK):
            return raw
        found = shutil.which(raw)
        if found:
            return found

    for name in COMMAND_NAMES:
        found = shutil.which(name)
        if found:
            return found

    for path in _windows_install_paths():
        if os.path.isfile(path):
            return path

    for path in sorted(glob.glob("/nix/store/*/bin/gs"), reverse=True):
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path

    return "gs"


def _windows_install_paths() -> list:
    roots = []
    for key in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        value = os.environ.get(key)
        if value:
            roots.append(value)
    roots.extend([r"C:\Program Files", r"C:\Program Files (x86)"])
    seen = []
    found = []
    for root in roots:
        if not root or root in seen:
            continue
        seen.append(root)
        for exe in WINDOWS_EXES:
            found.extend(glob.glob(os.path.join(root, "gs", "*", "bin", exe)))
    return sorted(found, reverse=True)
