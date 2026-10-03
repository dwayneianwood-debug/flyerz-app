"""Local OCR for AI Rebuild.

rapidocr-onnxruntime 1.4.4 refuses Python 3.13 and 3.14. The newer ``rapidocr``
package is a py3-none wheel and works with onnxruntime's Python 3.14 Windows
wheels. The older package remains a fallback when it is already installed.
"""

from __future__ import annotations

_ENGINE = None
_ENGINE_NAME = ""


def _as_list(value):
    if value is None:
        return []
    return list(value)


def rows_from_output(result):
    """Normalise the old list result and the newer RapidOCROutput."""
    if isinstance(result, tuple) and result and isinstance(result[0], list):
        return result[0] or []
    txts = getattr(result, "txts", None)
    if txts is None and isinstance(result, dict):
        txts = result.get("txts")
    if txts is None:
        return []
    if isinstance(result, dict):
        boxes = _as_list(result.get("boxes"))
        scores = _as_list(result.get("scores"))
    else:
        boxes = _as_list(getattr(result, "boxes", None))
        scores = _as_list(getattr(result, "scores", None))
    rows = []
    for index, text in enumerate(txts):
        box = boxes[index] if index < len(boxes) else [[0, 0], [1, 0], [1, 1], [0, 1]]
        score = float(scores[index]) if index < len(scores) else 1.0
        rows.append([box, str(text or ""), score])
    return rows


def _load():
    errors = []
    try:
        from rapidocr import RapidOCR
        return RapidOCR(), "rapidocr"
    except Exception as exc:
        errors.append(f"rapidocr: {exc}")
    try:
        from rapidocr_onnxruntime import RapidOCR
        return RapidOCR(), "rapidocr_onnxruntime"
    except Exception as exc:
        errors.append(f"rapidocr_onnxruntime: {exc}")
    detail = "; ".join(str(item)[:120] for item in errors) or "no OCR package"
    raise ImportError(f"Local OCR is not installed ({detail}).")


def engine_name() -> str:
    return _ENGINE_NAME


def _gate_image(bgr):
    """Big enough that a word stays one word, small enough that the short side is not blown up to 736."""
    import cv2

    height, width = bgr.shape[:2]
    short = min(height, width)
    long = max(height, width)
    if short < 1 or long < 1:
        return bgr
    scale = 640.0 / float(short) if short < 640 else 1.0
    # 736 is the detector's own long-side limit, so recognition crops are not larger than detection.
    if long * scale > 736.0:
        scale = 736.0 / float(long)
    if abs(scale - 1.0) < 0.02:
        return bgr
    return cv2.resize(
        bgr,
        (max(1, int(round(width * scale))), max(1, int(round(height * scale)))),
        interpolation=cv2.INTER_CUBIC,
    )


def local_rows(bgr, fast=False):
    """Read one picture.

    fast is the text-gate strip. Upright type skips the angle check, and the
    detector must not enlarge a thin crop until its short side is 736. The
    page read leaves both on.
    """
    global _ENGINE, _ENGINE_NAME
    if _ENGINE is None:
        _ENGINE, _ENGINE_NAME = _load()
    if not fast:
        return rows_from_output(_ENGINE(bgr))
    image = _gate_image(bgr)
    detector = _ENGINE.text_det
    previous = detector.limit_type
    detector.limit_type = "max"
    try:
        rows = rows_from_output(_ENGINE(image, use_cls=False))
    finally:
        detector.limit_type = previous
    if image is bgr or not rows:
        return rows
    sx = float(bgr.shape[1]) / float(image.shape[1])
    sy = float(bgr.shape[0]) / float(image.shape[0])
    scaled = []
    for item in rows:
        box = [[float(point[0]) * sx, float(point[1]) * sy] for point in item[0]]
        score = item[2] if len(item) > 2 else 1.0
        scaled.append([box, item[1], score])
    return scaled
