"""Sampling for a large upload.

A 200 MB picture is checked from a small render of the press plate.
The full image is not decoded again for the proof or the ink sample.
"""

from __future__ import annotations

LARGE_JOB_BYTES = 80 * 1024 * 1024
PROOF_ZOOM = 1.35
SAMPLE_LONG_PX = 1200.0


def is_large_job(file_bytes: int) -> bool:
    return int(file_bytes or 0) >= LARGE_JOB_BYTES


def proof_zoom(long_pt: float, file_bytes: int) -> float:
    """1.35 on a normal file. A large file stays near 1200 px on the long edge."""
    zoom = PROOF_ZOOM
    long_pt = max(float(long_pt or 0), 1.0)
    if not is_large_job(file_bytes):
        return zoom
    return min(zoom, SAMPLE_LONG_PX / long_pt)


# A phone screenshot is much taller than it is wide. A print file is not.
SCREEN_RATIO = 2.2
HUGE_EDGE_PX = 8000
# The heavy raster path (OCR, edge extend, the press plate) stays on this long edge.
FAST_LONG_PX = 3600


def working_limit(height: int, width: int, trim_w: float, trim_h: float, file_bytes_n: int) -> int:
    """How long the working picture may be before the press file is built.

    A normal photo is left as it is. A very tall screenshot, or a file of
    80 MB or more, is reduced to about the print size so the job can finish.
    """
    long = max(int(height or 0), int(width or 0), 1)
    short = max(1, min(int(height or 1), int(width or 1)))
    plate = max(
        (float(trim_w or 0) + 10.0) / 25.4 * 300.0,
        (float(trim_h or 0) + 10.0) / 25.4 * 300.0,
        1600.0,
    )
    skinny = long / float(short) >= SCREEN_RATIO
    huge = long >= HUGE_EDGE_PX or is_large_job(file_bytes_n)
    if not skinny and not huge:
        return long
    if skinny:
        return int(min(long, max(plate, 1800.0)))
    return int(min(long, min(max(plate, 1800.0), float(FAST_LONG_PX))))


def cap_shape(height: int, width: int, limit: int) -> tuple[int, int]:
    long = max(int(height or 0), int(width or 0), 1)
    limit = max(1, int(limit or long))
    if long <= limit:
        return int(height), int(width)
    scale = limit / float(long)
    return max(1, int(round(height * scale))), max(1, int(round(width * scale)))


def file_bytes(path: str) -> int:
    import os

    try:
        return int(os.path.getsize(path))
    except OSError:
        return 0
