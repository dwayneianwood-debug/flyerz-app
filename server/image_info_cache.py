"""Avoid decoding a press plate just to learn an image xref.

PyMuPDF's get_image_info(xrefs=True) builds a full pixmap so it can match an
MD5. A page that is one picture already knows that xref from get_images().
A page that is queried again keeps the last answer until its image list changes.
"""

from __future__ import annotations

_INSTALLED = False


def install_image_info_cache() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    import pymupdf

    original = pymupdf.Page.get_image_info

    def cached(page, hashes: bool = False, xrefs: bool = False):
        if not xrefs:
            return original(page, hashes=hashes, xrefs=False)
        try:
            images = page.get_images() or []
        except Exception:
            images = []
        token = tuple(int(item[0]) for item in images)
        slot = getattr(page, "_flyerz_xinfo", None)
        if isinstance(slot, tuple) and slot[0] == token:
            return [dict(row) for row in slot[1]]
        if len(images) == 1:
            info = original(page, hashes=False, xrefs=False) or []
            if len(info) == 1:
                row = dict(info[0])
                row["xref"] = int(images[0][0])
                result = [row]
                try:
                    page._flyerz_xinfo = (token, result)
                except Exception:
                    pass
                return [dict(row)]
        result = [dict(row) for row in (original(page, hashes=hashes, xrefs=True) or [])]
        try:
            page._flyerz_xinfo = (token, result)
        except Exception:
            pass
        return [dict(row) for row in result]

    pymupdf.Page.get_image_info = cached
    _INSTALLED = True
