# Bleed round spec — Ian's rules (approved 13 Oct 2026, "SO NOW IMPLEMENT MY PLAN")

THIS IS THE ONE ROUND. Test on the real files in `tests/fixtures/ian_2026-10-13/`. Do not report done without passing crops of all 4 bleed edges for every file.

## 1. Bleed amount
5 mm on ALL products: A0–A7, DL, 90×50, 90×55 and custom — portrait and landscape, single, double-sided and multi-page.

## 2. Starting bleed
| Incoming file | Action |
|---|---|
| No bleed | Add 5 mm |
| Partial bleed (e.g. 2.5 mm) | Keep it and add only the shortfall |
| Full 5 mm bleed | Pass through unchanged |
| Over-bleed (up to 15 mm vector / 25 mm raster) | **NEW:** trim to 5 mm and note it on the card |
| Wrong size | Match the nearest product |

## 3. Edge step order (every edge, every route)
1. Detect important content (text, logos, photo subjects, artwork) inside the 3 mm safe zone.
2. Bring it in with an even scale, **max 2 mm per edge**. This replaces the 1–3% (`press_ready_engine._rescue`) and the 4% / 0.96 governor (`smart_bleed._pre_bleed_safe_zone_uniform_shrink`) and the elastic squeeze (`composite_ghost_frame_pullback`) for this purpose. If 2 mm is not enough → AMBER with a polite note; the download must stay available.
3. Remove a thin pale/white edge line (e.g. Canva's 1 px white column), **max 0.5 mm**.
4. Fit exactly to the trim (fix pages that are ~0.1 mm short, e.g. Canva 419.25 pt = 147.9 mm).
5. Build the bleed from the CLEAN edge.
6. Self-check each bleed edge against the adjacent art. Redo a mismatched edge with mirror; if it still mismatches → AMBER. **The fix must be in generation** (`_paint_cmyk_edge` and all painters), not detection only. (Previous rounds 8b75807 and 285da1e only added detection; the VOB press PDF was byte-for-byte the same band.)

## 4. Painters
All fill methods must use the cleaned edge: the automatic CHAIN (`press_ready_engine.py` CHAIN/_methods_for/_paint/extend_trim), `_paint_cmyk_edge`, `_paint_mirrored_strips`, `_paint_vector_strips`, `_paint_large_strips`, `_paint_bleed_matching`, and the 9 staff-selectable methods (bgExtract, stretch, mirror, replicate, upscale, ai_outpaint, colourBorder, gradient_extrapolate, frequency_separated).

## 5. Card note
When bleed is chosen automatically, the card states the method used per edge (top/bottom/left/right).

## 6. Routes
- Quick-print PDF / AI / EPS
- Raster: JPG, PNG, TIFF, BMP (add .tif/.tiff/.bmp to the upload accept list `shared/artwork-types.json` uploadExtensions/printToolExtensions)
- Review/compile path including rasterized PDFs (`compile_press_pdf.py` / `auto_resolve_safe_zone`)
- Word / PPT stays politely refused.

## 7. No regressions
- 25-point checklist and E1–E9
- K-only black
- A6 seams 0.03 / 0.70
- 2.5 mm partial-bleed files stay GREEN (8b75807 wrongly turned them amber — must not happen)
- Already-correct files byte-identical or visually unchanged
- Posters (CMYK green, RGB red), downloads, red/amber card download, Proceed to Download
- Cat edits, two jobs at once, timings no slower than 18649bd
- No dependence on locally installed fonts
- Polite wording everywhere

## 8. Acceptance
- The real VOB (`vob_original.pdf`) has no band on ANY edge — show crops (`vob_press_285da1e_band.pdf` shows the current 3.2 mm grey band on the right, RGB ≈ 218,215,211).
- Every file in `tests/fixtures/ian_2026-10-13/` passes, plus a synthetic matrix: every size × none / partial / full / over / wrong-size, with near-edge text, logos and photos on dark, light, gradient and pattern backgrounds — crops of all 4 edges for each.

## Test set (tests/fixtures/ian_2026-10-13/)
| File | Type | Page size | Pages | Bleed |
|---|---|---|---|---|
| vob_original.pdf | Canva PDF | 147.9 × 210.1 mm | 1 | none (trim = media; 1 px white right column) |
| vob_press_285da1e_band.pdf | app output (reference of the bug) | 158 × 220 mm | 1 | 5 mm, grey band right |
| 7d28b408_Copy_of_A5_Leaflet_-_Our_Priorities_to_Fix_Joburg.pdf | Canva PDF | 147.9 × 210.1 mm | 2 (double-sided) | none |
| d9c87a15_A5_3mm_bleed_no_trimbox.pdf | PDF | 154 × 216 mm | 1 | 3 mm, no TrimBox |
| karseell_A5_double_sided_3mm_trimbox.pdf | ReportLab PDF | 154 × 216 mm, TrimBox 148 × 210 | 2 (double-sided) | 3 mm with TrimBox |
| e6e73dae_A4_300px_as_points_wrong_size.pdf | Acrobat image-conversion PDF | 2480 × 3508 pt (875 × 1238 mm; A4 pixels as points) | 1 | none — wrong size, must match nearest product |
| rosanna_olive_flyer.jpg | JPEG | 1024 × 1536 px, no DPI | – | none (raster, 2:3 aspect) |
| urbanflex_gym_flyer.jpeg | JPEG | 1024 × 1536 px, no DPI | – | none (raster, 2:3 aspect) |
