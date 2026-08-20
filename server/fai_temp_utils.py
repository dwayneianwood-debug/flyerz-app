"""
Shared Flyerz temp directory resolution — Windows/macOS/Linux compatible.
Replit/Linux used /dev/shm; on Windows that path must never be required for correctness.
"""
from __future__ import annotations

import os
import sys
import tempfile


def init_fai_temp_dir(*, verbose_stderr: bool = False) -> str:
    env = (os.environ.get("FAI_TEMP_DIR") or "").strip()
    if env:
        ap = os.path.abspath(env)
        os.makedirs(ap, exist_ok=True)
        if verbose_stderr:
            sys.stderr.write(f"[FAI] Temp storage: FAI_TEMP_DIR={ap}\n")
        return ap

    # Linux RAM disk — skip entirely on Windows (no /dev/shm)
    if sys.platform != "win32":
        shm_path = "/dev/shm/flyerz_tmp"
        try:
            os.makedirs(shm_path, exist_ok=True)
            probe = os.path.join(shm_path, ".probe")
            with open(probe, "w") as f:
                f.write("ok")
            os.unlink(probe)
            if verbose_stderr:
                sys.stderr.write("[FAI] Temp storage: /dev/shm (RAM-backed)\n")
            return shm_path
        except Exception:
            pass

    try:
        tdir = os.path.join(tempfile.gettempdir(), "flyerz_tmp")
        os.makedirs(tdir, exist_ok=True)
        probe = os.path.join(tdir, ".probe")
        with open(probe, "w") as f:
            f.write("ok")
        os.unlink(probe)
        if verbose_stderr:
            sys.stderr.write(f"[FAI] Temp storage: system temp ({tdir})\n")
        return tdir
    except Exception:
        pass

    disk_path = os.path.abspath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fai_temp_processing")
    )
    os.makedirs(disk_path, exist_ok=True)
    if verbose_stderr:
        sys.stderr.write(f"[FAI] Temp storage: disk ({disk_path})\n")
    return disk_path


def is_scratch_temp_file(path: str, fai_root: str) -> bool:
    """
    True if path is under the Flyerz temp root or (on non-Windows) legacy /dev/shm scratch.
    Used to reclaim intermediate PDFs without relying on '/dev/shm' alone (Windows has no shm).
    """
    try:
        p = os.path.normcase(os.path.normpath(os.path.abspath(path)))
        fr = os.path.normcase(os.path.normpath(os.path.abspath(fai_root)))
        sep = os.sep
        if p.startswith(fr + sep) or p == fr:
            return True
        if sys.platform != "win32":
            legacy = os.path.normcase("/dev/shm")
            if p.startswith(legacy + sep) or p == legacy:
                return True
    except Exception:
        return False
    return False
