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


def local_rows(bgr, fast=False):
    """Read one picture.

    fast is the text-gate strip. Those crops are already letter-sized, so the
    detector must not enlarge the short side, and upright type skips the
    angle check. The page read leaves both on.
    """
    global _ENGINE, _ENGINE_NAME
    if _ENGINE is None:
        _ENGINE, _ENGINE_NAME = _load()
    if not fast:
        return rows_from_output(_ENGINE(bgr))
    detector = _ENGINE.text_det
    previous = detector.limit_type
    detector.limit_type = "max"
    try:
        return rows_from_output(_ENGINE(bgr, use_cls=False))
    finally:
        detector.limit_type = previous
