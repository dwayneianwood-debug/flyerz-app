# Reference vector-text rebuild (Medella) — hand-built, QA-approved pipeline

This folder is the exact Python/shell pipeline that produced the approved press PDFs in
`tests/fixtures/medella/approved/`. It is a **reference to port** into the app's
"vector-text rebuild v2", not production code: paths are hard-coded to the original
work box (`/workspace/medwork`, `/workspace/medella_out/src_*.png`, `/workspace/medfonts/...`).
Swap those for fixture paths when porting.

Inputs: `tests/fixtures/medella/{flyer_front,flyer_back,card_front,card_back}.png`
(were `/workspace/medella_out/src_<side>.png`).
Outputs: `flyer_A5_front_back_press.pdf` (158×220 mm media, 148×210 trim, 5 mm bleed) and
`business_card_front_back_press.pdf` (100×60 media, 90×50 trim, 5 mm bleed), CMYK, fonts embedded.

Stack: Python 3.13, opencv-python, numpy, Pillow, fontTools, reportlab, rapidocr (onnxruntime),
pymupdf, Ghostscript 10 (CMYK conversion + ICC-accurate proofs). Upscaler: Real-ESRGAN x4 (Replicate).

## Step order

| # | Script | What it does |
|---|--------|--------------|
| 1 | `ocr.py` → `data/ocr.json` | RapidOCR on each original: text + 4-point quad per line. |
| 2 | `spec.py` | **Hand-corrected line list** (`lines(side)`): fixes OCR wording/accents (Chandré, en dash, phone), drops non-text (logo "Medella", icons), manual quad tweaks. This is the source of truth for wording. |
| 3 | `masks.py <sides>` | Per-line ink mask → `mask_<side>.png` + `meta_<side>.pkl` (ink box, ink colour, glyph mask). |
| 4 | `inpaint.py <sides>` | Erase text on the ORIGINAL-resolution image → `clean_<side>.png`. |
| 5 | (external) Real-ESRGAN x4 | `clean_<side>.png` → `up_<side>.orig.png` (4×). Outpainted/padded variants for bleed: `up_exp_flyer_front.png` (flyer front base, 16:9 expand), `up_exp_card_pad.png` (card front padded), `up_card_back.png`. |
| 6 | `colorfix4.py` | Colour restore of the upscale → `up_<side>.png` (see below). `colorfix.py/2/3` are earlier, superseded attempts kept for history. |
| 7 | `fonts.py`, `groups.py`, `match.py`, `sheet.py` | Candidate font pool (64 Google Fonts), role grouping (caps / body / italic / script), render-and-score matching; picks families. |
| 8 | `fit.py <sides>` → `data/fit_<side>.json` | Per line: font weight, size, tracking (cs), horizontal scale (hs), stroke, baseline origin, angle, colour class. |
| 9 | `harmonize.py` | One body size per paragraph block (width preserved). Called from `pdf.py`. |
| 10 | `bg.py` | Composites full-bleed background PNGs per side (art placement, bleed extension). |
| 11 | `pdf.py` | ReportLab: background image + vector text + vector ornaments, Trim/Bleed boxes, card-back redesign → `rgb_*.pdf`. |
| 12 | `tocmyk.sh in out` | Ghostscript → CMYK PDF (relative colorimetric + BPC, JPEG Q≈0.15, fonts embedded/subset). |
| 13 | `render.sh [hi]` | Ghostscript proofs 300 dpi (`q_*.png`), 600 dpi with `hi` (`h_*.png`). **Do not use pdftoppm for colour proofs** (wrong CMYK→RGB). |
| 14 | QA: `qa_ocr3.py`, `qa_overlap.py`, `qa_bg.py`, `tiles.py`, `cmp.py`, `zc.py` | Re-OCR diff, overlap/safe-area, blotch/ghost check, side-by-side tiles and zooms. |

`build.sh [hi]` = steps 11–13 + tiles (≈35 s). Full OCR QA ≈14 min.

## Exact parameters

### Mask (masks.py)
- Per line: quad polygon dilated by kernel `(2*pad+1) × 5`, `pad = clamp(round(0.25*line_h), 3, 6)` px. Two-digit-or-less numerals (step numbers in circles) are **not** dilated.
- Background = median Lab inside quad; text candidate = ΔE(Lab) > 26 from bg; text colour = median of the top 40 % ΔE pixels; keep pixel if closer to text colour than to bg ×1.1.
- Gold pixels (HSV h 10–38, s>60, v>90) are excluded unless the text itself is gold (protects gold rules/ornaments).
- Drop specks: components < 2 px, long thin (w>8h and h<0.25·line_h) rule fragments, and components with <30 % inside the quad.
- Erase mask = keep dilated 5×5 (3×3 for numerals), clipped to the dilated quad.

### Inpaint (inpaint.py)
- Mask dilated 3×3, `cv2.inpaint(..., radius=5, INPAINT_TELEA)` on the original resolution.
- Add grain: Gaussian noise σ_blur 0.8, scaled to std 1.2, feathered by 5×5 blurred mask.
- Residual pass: pixels ΔE>28 from 9-px median inside line boxes (±4 px v, ±2 px h), components ≤12 px, dilate 5×5, Telea radius 4. Skip numerals.
- Bria/AI erasers were rejected (ghost scribbles). Telea at source res, THEN upscale, gave the cleanest result.

### Colour restore (colorfix4.py `fix`)
ESRGAN shifts gold warmer and green lighter and adds chroma fringing. Fix in Lab:
- L = ESRGAN L + low-frequency residual: `GaussianBlur(L_src − L_downsampled_up, σ=0.8)` upscaled bicubic (Lgain 1.0).
- a,b = **bicubic-upscaled source chroma**, blurred σ = 0.35×scale.
- `fix_affine` variant for padded/expanded images (ref→up affine, edge-feathered mask). Card-front padded art: chroma-only correction to keep the mottled texture.

### Fit (fit.py)
- Glyph-by-glyph render (no kerning, same as ReportLab) at 4× (`Z=4`). Size from ink height of the original line, then:
  - caps: tracking only, cs ∈ [−0.04, 0.7]·size;
  - body/italic: cs ∈ [−0.03, 0.05]·size then hs ∈ [0.92, 1.08];
  - script: hs ∈ [0.80, 1.15], stroke candidates 0–12 (¼ src px).
  - Never exceed original ink width (+1 %).
- Weight choice = best ink-area match (|log area ratio|); "majority caps weight" per colour class; `LIGHT_CAPS_W={'flyer_back':500}` (white caps on green), `BODY_W={'flyer_back':400}`.
- `disentangle()` reassigns mask components to the nearest quad where boxes overlap. `INK_CLIP={'flyer_front:93':631}` (footer line ink box clipped before the heart).
- Colour: fixed hand-sampled palette per class (green / navy_dark / navy_mid / gold / neutral / light) — dynamic snapping was inaccurate.

### PDF (pdf.py)
- Placement: page_mm = off + src_px·s. flyer_front `s=205/1536`, `off=((158−1024s)/2, 7.5)` (art height reduced 207→205 mm for trim clearance); flyer_back `s=148/1054`, `off=(5,(220−1492s)/2)`; card_front `s=50/992`, `off=((100−1586s)/2, 5)` (art height = trim height, nothing cut; sides filled from padded expand).
- Background raster: flyers 400 ppi, cards 600 ppi.
- CMYK: sRGB→Ghostscript default_cmyk.icc, relative colorimetric + BPC; neutrals K-only with **K=1.0 when L<0.35** (otherwise "black" prints grey). `COLOR_OVERRIDE` darkens two greens that proofed light: flyer_front green → RGB(8,30,20), card_front green → RGB(8,32,12).
- Fonts are re-named per weight with fontTools (`press_path` → `/medfonts/press/*`), because static instances cut from a variable font share internal names and ReportLab/Ghostscript collapse them into one weight.
- Thickening (stroke = factor × font size, text render mode 2):
  - `BODY_THICKEN` Crimson Text Regular (non-light): flyer_front 0.013, flyer_back 0.005; flyer_front "Why choose" list (ids 76–90) 0.022.
  - `SCRIPT_THICKEN` flyer_front small dark Parisienne (<12 pt) 0.012.
- `WEIGHT_OVERRIDE` flyer_front:17 tagline → EB Garamond Italic 400 (width kept via hs).
- `HEAD_SWAP` flyer_back condition headings (navy caps, ids 8–63) → **EB Garamond SemiBold**, one common size = median(Cinzel size)·0.70/0.65·0.97, cs clipped to [−0.45, 0.5], hs absorbs the rest. Cinzel's wide "&" made these look cramped.
- `GROUP_SET` flyer_back bottom strip (78–80) → Cinzel 600, one size, width via tracking.
- `ITALIC_PREFIX` flyer_front:73 "Invest" in Crimson Italic.
- Raster fixes: `FF_HEART_FIX` inpaint boxes, `FF_CLONE` (copy neighbouring gold-rule segment over erase specks; Telea made grey squares there), `FB_FIX`. Vector redraws: `FF_HEARTS`, `FF_DOTS` (gold bullets Ø5.6 src px, RGB 205,152,70).

### Fonts per role (Google Fonts, OFL)
| Role | Font |
|---|---|
| Caps headings / labels | Cinzel 500 / 600 / 700 |
| Flyer back condition headings | EB Garamond SemiBold 600 |
| Body | Crimson Text Regular (Italic for lead-ins) |
| Taglines | EB Garamond Italic 400 |
| Script | Parisienne Regular (closest free match; some capitals S/L/B differ) |

### Card back redesign (pdf.py `card_back_page`)
100×60 mm page, coords below are trim-relative mm (90×50). Background = original card back art with blotch smoothing (`bg.card_back_clean`) and gold edge line removed from outer 1.8 mm.
- Title "LIVE BLOOD ANALYSIS": Cinzel 700, 9.6 pt, tracking 1.1, centred, baseline y=8.9.
- Divider: ornament cropped from original (src 706–888 × 80–124) at y=12.2, width 11.5; gold rules 0.45 pt from x 23 to centre±6.05.
- Script "A wide range of conditions": Parisienne 17.5 pt, stroke 0.25 pt, baseline y=19.9.
- 5 icons (from flyer back upscale, cream re-tinted), Ø8.6 mm at y=27.0; x spread so labels fill 4.2–85.8 mm with equal gaps (≈2.3 mm).
- Labels two lines, Cinzel 700, **7.0 pt**, tracking 0.05, baselines 35.0 / 37.95: IMMUNE/SUPPORT, ENERGY &/FATIGUE, SKIN/CONDITIONS, DIGESTIVE/HEALTH, HORMONE/BALANCE.
- Bottom "DETECT • BALANCE • HEAL • LIVE BETTER": Cinzel 600, 7.0 pt, tracking 0.9, gap 4.2 mm with filled gold vector hearts 1.25 mm, baseline 44.6.
- Min text 7 pt; nearest text 4.24 mm from trim (safe area 3 mm).

## Lessons from QA (what broke and the fix)
1. **Ghost/doubled text** comes from erasing after upscaling or with AI erasers. Erase at source resolution (Telea r=5 + residual pass), then upscale the clean plate. Never overlay vector text on an un-erased raster.
2. **Damaged numbers**: don't dilate masks for 1–2 digit numerals (3×3 only, no quad dilation, skip residual pass), or the circle around them gets eaten.
3. Gold rules/bullets get clipped by text masks: exclude gold from non-gold text masks; redraw small ornaments (dots, hearts) as vector; patch specks by cloning adjacent rule pixels, not inpainting.
4. Upscalers shift colour: take chroma from the bicubic source (colorfix4), luminance detail from ESRGAN.
5. Bleed: mirror/replicate borders streak. Extend from an edge band with horizontal blur + grain (flyer front), soften outside the art (flyer back), mirror art rows for the card front. Remove any frame lines sitting in the outer bleed.
6. Font weight collisions: give every static weight a unique internal name before embedding.
7. Neutral dark text must be K=100, not 1−luminance.
8. Fit per line from ink boxes is noisy: harmonize sizes per paragraph and per heading set, keep widths with tracking/hs, and clip tracking so word spaces don't collapse.
9. Proof with Ghostscript + ICC; pdftoppm misrenders CMYK.
10. OCR on script fonts is unreliable even on the original; verify script lines by PDF text extraction (whitespace-insensitive, tracked caps extract with spaces) plus zoomed visual compare.

## data/
Snapshot of intermediate JSON from the approved run: `ocr.json`, `fit_<side>.json` (all fitted line parameters), `exp_*.json`, `cardback_layout.json`.
Large intermediates (upscales, clean plates, masks, renders) are intentionally not committed.
