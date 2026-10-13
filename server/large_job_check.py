#!/usr/bin/env python3
"""A large job is sampled. A normal page keeps the 1.35 proof zoom."""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))

from large_job import LARGE_JOB_BYTES, cap_shape, is_large_job, proof_zoom, working_limit


def main() -> int:
    failures = []
    small = proof_zoom(433.5, 8_000_000)
    if abs(small - 1.35) > 1e-9:
        failures.append(f"A6 proof zoom {small}")
    wide = proof_zoom(2400, 2_000_000)
    if abs(wide - 1.35) > 1e-9:
        failures.append(f"normal long page {wide}")
    huge = proof_zoom(2400, 200 * 1024 * 1024)
    if huge * 2400 > 1200.01:
        failures.append(f"200 MB zoom still full size {huge}")
    if not is_large_job(200 * 1024 * 1024) or is_large_job(LARGE_JOB_BYTES - 1):
        failures.append("size gate")

    import pymupdf as fitz

    from extra_checks import _sampled_ink

    doc = fitz.open()
    page = doc.new_page(width=300, height=200)
    page.draw_rect(page.rect, color=(0, 0, 0, 1), fill=(0, 0, 0, 1), width=0)
    facts = {"max_tac": 0.0, "k90": 0, "k_only": 0}
    _sampled_ink(page, proof_zoom(300, 200 * 1024 * 1024), facts)
    doc.close()
    if facts["k_only"] <= 0 and facts["max_tac"] <= 0:
        failures.append(f"sample saw no ink {facts}")
    with tempfile.NamedTemporaryFile(suffix=".pdf") as handle:
        handle.write(b"%PDF")
        handle.flush()
        os.truncate(handle.name, LARGE_JOB_BYTES + 10)
        from large_job import file_bytes

        if not is_large_job(file_bytes(handle.name)):
            failures.append("truncated file not large")
    normal_h, normal_w = cap_shape(*[int(x) for x in (6000, 4000)], working_limit(6000, 4000, 210, 297, 2_000_000))
    if (normal_h, normal_w) != (6000, 4000):
        failures.append(f"normal photo was reduced {normal_h}x{normal_w}")
    shot_h, shot_w = cap_shape(15444, 1080, working_limit(15444, 1080, 148, 210, 4_000_000))
    if shot_h > 4000 or shot_w > 4000 or shot_h >= 15444:
        failures.append(f"screenshot stayed tall {shot_h}x{shot_w}")
    huge_h, huge_w = cap_shape(12000, 9000, working_limit(12000, 9000, 420, 594, 200 * 1024 * 1024))
    if max(huge_h, huge_w) > 3600:
        failures.append(f"200 MB working edge {huge_h}x{huge_w}")
    # The working copy of a scrolling screenshot is short on the narrow side.
    # Print size is judged on the uploaded pixels, which still cover an A5.
    from quick_print import too_small

    if too_small(1080, 15444, 148, 210):
        failures.append("15444px screenshot judged too small")
    if not too_small(shot_w, shot_h, 148, 210):
        failures.append(f"capped screenshot {shot_w}x{shot_h} should not be the size we judge")
    if failures:
        print("FAIL " + "; ".join(failures))
        return 1
    print("large job sample pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
