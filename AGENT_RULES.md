# FLYERZ ARCHITECTURAL CONSTITUTION
## Anti-Regression Shield — Read Before Every Task

> **MANDATE**: Any AI agent working on this codebase MUST silently read this file
> in full before beginning any task. These laws exist because every single one of
> them was learned from a production-breaking regression. Violating any law will
> reintroduce OOM crashes, broken previews, or UI deadlocks.

---

## LAW 1: The Ghostscript RAM Cap

**Problem solved**: Ghostscript OOM crashes (exit code -9, SIGKILL) on the
constrained Replit VM.

**Absolute constraints — NEVER modify these values:**

| Parameter              | Value       | Location                    |
|------------------------|-------------|-----------------------------|
| `NumRenderingThreads`  | `1`         | `server/smart_bleed.py`     |
| `MaxBitmap`            | `50000000`  | `server/smart_bleed.py`     |
| `BufferSpace`          | `50000000`  | `server/smart_bleed.py`     |
| `BandBufferSpace`      | `50000000`  | `server/smart_bleed.py`     |
| PostScript setuserparams | `<< /MaxBitmap 50000000 >> setuserparams` | `server/smart_bleed.py` |
| DPI ceiling            | `300`       | Both `smart_bleed.py` and `compile_press_pdf.py` |

**Additional rules:**
- `gc.collect()` MUST run before every Ghostscript subprocess spawn.
- `_cap_pdf_image_dpi()` MUST downsample embedded images to 300 DPI before any GS call.
- Handoff file size MUST be logged before every GS subprocess; files >50MB trigger a warning.
- The raster-first failsafe in `smart_bleed.py` MUST remain: if direct PDF bleed fails,
  rasterize the page and process as an image.

**What will break if violated**: Ghostscript will consume all available RAM and get
killed by the OOM reaper (exit -9). The entire job fails with no output.

---

## LAW 2: The Crop Choke Point

**Problem solved**: "After" previews and bleed variant thumbnails showed the
uncropped original with white borders instead of the user's cropped selection.

**Absolute rules:**

1. **Never process an uncropped original file if crop coordinates exist.**
   - In `server/fileProcessor.ts`: when `hasCropCoords` is true, the original
     high-res file is passed with crop coordinates to `smart_bleed.py`. The crop
     is applied inside the Python pipeline, not by pre-cropping on disk.
   - In `server/compile_press_pdf.py`: the compile route receives crop coords
     via `--crop-x/y/w/h` args and applies them to the original source at
     lines 638-662. This preserves maximum resolution.

2. **Preview generators MUST use the cropped intermediate, not the original.**
   - In `apply_smart_bleed_to_image()`: `comparison_before_source` MUST be
     `pre_bleed_path` (the cropped+scaled intermediate saved at line 4072),
     NOT `input_path`.
   - `generate_signoff_comparison()` and `generate_bleed_report_proof()` MUST
     receive `comparison_before_source` as their first argument.
   - `generate_bleed_variants()` receives `img` which is already cropped. Do NOT
     change this to read from `input_path`.

3. **PDF variant generation MUST use the corrected output.**
   - In `apply_smart_bleed_to_pdf()`: `_run_variants()` MUST open
     `output_path` (corrected PDF with crop applied), NOT `input_path`.

4. **Crop coordinates are percentage-based (0-1 range).**
   - Frontend (`manual-crop.tsx`) emits fractions via `getCropAsPercentages()`.
   - Both `smart_bleed.py` and `compile_press_pdf.py` detect `<=1.0` values
     and multiply by image dimensions. Never assume pixel values.

**What will break if violated**: Users will see their uncropped original (with
white borders or wrong content) in the "After" comparison, bleed option
thumbnails, and bleed report proof. The final ZIP PDF will also be wrong.

---

## LAW 3: The Gatekeeper Bypass

**Problem solved**: The UI entered an infinite loop where subjective layout
warnings (Layout Balance, Visual Composition Center, Margin Normalization)
kept the "Fix Everything" button visible after auto-fix, causing users to
click it repeatedly with no effect.

**Absolute rules:**

1. **The `autoFixApplied` flag MUST exist in `job-details.tsx`.**
   - Set to `true` after `processJob.mutate` succeeds.
   - Reset to `false` only in `resetJobState()`.
   - Once `autoFixApplied` is true, Phase 1 (the "Fix Everything" panel)
     MUST NOT render.

2. **The `{!isFastTrack && (` guard MUST remain on the Phase 1 conditional.**
   - This prevents Fast Track jobs from snapping back to Phase 1.
   - NEVER remove or modify this guard.

3. **Subjective layout warnings MUST NOT block the Auto-Fix button.**
   - These checks are informational. They pass automatically when downscale
     or safe-zone auto-fix is applied (see Scaling Cap in replit.md).
   - The `computedPhase` logic must advance to Phase 2+ after auto-fix
     regardless of subjective check status.

4. **Cache-busting MUST fire on processJob success.**
   - `proofRefreshKey`, `comparisonRefreshKey`, `bleedPreview`, and error
     states MUST all reset in the `onSuccess` callback.

**What will break if violated**: Users get trapped in a "ghost loop" — they
click Fix Everything, it processes, but the UI snaps back to Phase 1 because
subjective checks still show as "failed". The button never disappears.

---

## LAW 4: The MediaBox Freeze

**Problem solved**: Compiled PDFs had incorrect dimensions because the MediaBox
was derived from the source file instead of the user-selected target format.

**`_enforce_final_mediabox()` in `compile_press_pdf.py` is FROZEN.**

- NEVER modify this function.
- MediaBox ALWAYS equals `trim_width + 10mm` x `trim_height + 10mm`
  (5mm bleed on all sides).
- TrimBox ALWAYS equals the exact trim dimensions from the UI.
- BleedBox ALWAYS spans the full MediaBox.
- This applies universally to every format: A5, A4, A3, DL, Business Card,
  and custom sizes.

**What will break if violated**: Final PDFs will have wrong page sizes. The
printer's RIP will misinterpret bleed boundaries, causing white edges or
cropped content on press.

---

## LAW 5: The Forbidden List

These specific changes are FORBIDDEN under all circumstances:

| Forbidden Action | Reason |
|---|---|
| Modify `_enforce_final_mediabox()` | Law 4 — MediaBox dimensions are locked |
| Remove `{!isFastTrack && (` UI guard | Law 3 — prevents ghost loop |
| Touch `_ft_actual_bleed_mm` | Fast Track bleed math is calibrated |
| Change 5mm bleed constant | Industry standard, hardcoded across pipeline |
| Modify ZIP cache logic | Prevents stale downloads |
| Increase GS `MaxBitmap` above 50MB | Law 1 — OOM prevention |
| Add `NumRenderingThreads > 1` | Law 1 — single-thread RAM safety |
| Pass `input_path` to comparison/proof in image pipeline | Law 2 — must use `comparison_before_source` |

---

## VALIDATION MANDATE

Before declaring ANY task complete, run:

```bash
python3 server/test_pipeline.py
```

**The task is NOT complete until this script returns 100% passed.**

This script performs a full end-to-end pipeline test: creates a test PDF,
applies mock crop coordinates, runs the 25-point intelligence scan, generates
bleed, compiles the final CMYK PDF, and asserts correct dimensions, file
integrity, and architecture compliance.

---

## DIAGNOSTIC SUITE

For comprehensive tracing across all 9 pipeline stages (54 checks):

```bash
python3 run_full_diagnostic.py
```

This traces every parameter handoff from frontend request through to final PDF.
