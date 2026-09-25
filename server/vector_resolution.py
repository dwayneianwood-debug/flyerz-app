"""Effective DPI of placed rasters on a vector page.

Vector-only pages have no pixel resolution. A placed image is measured from
the rectangle it occupies on the page, not from the page size. Missing linked
images (OPI stubs) are left to the link check.
"""


def _is_missing_link(doc, xref: int) -> bool:
    try:
        raw = doc.xref_object(int(xref)) or ""
    except Exception:
        return False
    return "/OPI" in raw


def placed_raster_samples(doc):
    """Return {dpi, width, height, page} for each real placed image."""
    samples = []
    for page_index, page in enumerate(doc):
        try:
            infos = page.get_image_info(xrefs=True) or []
        except Exception:
            infos = []
        for info in infos:
            xref = int(info.get("xref") or 0)
            if xref and _is_missing_link(doc, xref):
                continue
            bbox = info.get("bbox")
            if not bbox or len(bbox) < 4:
                continue
            disp_w = abs(float(bbox[2]) - float(bbox[0]))
            disp_h = abs(float(bbox[3]) - float(bbox[1]))
            if disp_w < 0.5 or disp_h < 0.5:
                continue
            width = int(info.get("width") or 0)
            height = int(info.get("height") or 0)
            if xref:
                try:
                    extracted = doc.extract_image(xref)
                except Exception:
                    continue
                if not extracted or not extracted.get("image"):
                    continue
                width = int(extracted.get("width") or width)
                height = int(extracted.get("height") or height)
            if width <= 0 or height <= 0:
                continue
            dpi = min(width / (disp_w / 72.0), height / (disp_h / 72.0))
            samples.append({
                "dpi": dpi,
                "width": width,
                "height": height,
                "page": page_index + 1,
            })
    return samples


def minimum_placed_dpi(doc):
    """Lowest placed-image DPI, or None when the document is vector-only."""
    samples = placed_raster_samples(doc)
    if not samples:
        return None
    return min(sample["dpi"] for sample in samples)
