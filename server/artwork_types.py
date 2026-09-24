"""File-type allow-list shared with the TypeScript client and server.

The lists live in shared/artwork-types.json. Illustrator .ai and .eps are
vector artwork and follow the PDF path.
"""

import json
import os

_CATALOG_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "shared", "artwork-types.json")
)

with open(_CATALOG_PATH, "r", encoding="utf-8") as _catalog_file:
    _CATALOG = json.load(_catalog_file)

UPLOAD_EXTENSIONS = tuple(_CATALOG["uploadExtensions"])
PRINT_TOOL_EXTENSIONS = tuple(_CATALOG["printToolExtensions"])
VECTOR_EXTENSIONS = frozenset(_CATALOG["vectorExtensions"])
RASTER_EXTENSIONS = frozenset(_CATALOG["rasterExtensions"])
VECTOR_TYPES = frozenset(_CATALOG["vectorTypes"])
ILLUSTRATOR_TYPES = frozenset(_CATALOG["illustratorTypes"])


def _with_dot(value: str) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return ""
    return raw if raw.startswith(".") else f".{raw}"


def _bare(value: str) -> str:
    raw = (value or "").strip().lower().replace("\\", "/").split("/")[-1]
    if "." not in raw:
        return raw
    return raw.rsplit(".", 1)[-1]


def is_vector_extension(ext: str) -> bool:
    return _with_dot(ext) in VECTOR_EXTENSIONS


def is_raster_extension(ext: str) -> bool:
    return _with_dot(ext) in RASTER_EXTENSIONS


def is_vector_type(file_type: str) -> bool:
    return _bare(file_type) in VECTOR_TYPES


def is_illustrator_type(file_type: str) -> bool:
    return _bare(file_type) in ILLUSTRATOR_TYPES


def input_kind(ext: str) -> str:
    """How the press compiler should open this extension: pdf, image, or unsupported."""
    if is_vector_extension(ext):
        return "pdf"
    if is_raster_extension(ext):
        return "image"
    return "unsupported"
