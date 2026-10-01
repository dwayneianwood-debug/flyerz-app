"""One line for the app log so a laptop run shows which stack it actually loaded.

scipy and scikit-image are not used. If either imports, the log says yes, because
a host that has them must not be treated as the same machine as one that does not.
"""

from __future__ import annotations

import importlib.metadata
import locale
import os
import sys


def _version(dist: str) -> str:
    try:
        return importlib.metadata.version(dist)
    except importlib.metadata.PackageNotFoundError:
        return "missing"


def _present(name: str) -> str:
    try:
        __import__(name)
    except Exception:
        return "no"
    return "yes"


def _decimal_mark() -> str:
    """The mark the C library would use for printf. Restored afterwards."""
    try:
        previous = locale.setlocale(locale.LC_NUMERIC)
    except locale.Error:
        return "?"
    try:
        locale.setlocale(locale.LC_NUMERIC, "")
        mark = str(locale.localeconv().get("decimal_point") or ".")
    except locale.Error:
        mark = "."
    finally:
        try:
            locale.setlocale(locale.LC_NUMERIC, previous)
        except locale.Error:
            pass
    return mark


def _models() -> str:
    try:
        import rapidocr
    except Exception:
        return "none"
    folder = os.path.join(os.path.dirname(rapidocr.__file__), "models")
    names = sorted(name for name in os.listdir(folder) if name.endswith(".onnx")) if os.path.isdir(folder) else []
    return ",".join(names) if names else "none"


def _opencv() -> str:
    try:
        import cv2
    except Exception as exc:
        return f"missing ({exc.__class__.__name__})"
    return str(getattr(cv2, "__version__", "unknown"))


def describe() -> str:
    """Single log line. Safe to call before a job starts."""
    try:
        import rapidocr  # noqa: F401

        ocr = f"rapidocr {_version('rapidocr')}"
    except Exception:
        ocr = "unavailable"
    return (
        f"[env] python={sys.version.split()[0]} ocr={ocr} models={_models()} "
        f"numpy={_version('numpy')} opencv={_opencv()} "
        f"pillow={_version('pillow')} pymupdf={_version('pymupdf')} "
        f"scipy={_present('scipy')} skimage={_present('skimage')} "
        f"decimal={_decimal_mark()}"
    )


def main() -> None:
    print(describe())


if __name__ == "__main__":
    main()
