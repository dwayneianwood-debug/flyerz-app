# Quick mode handoff

Branch: `cursor/artwork-preview-lightbox-e73c`. WIP commit is the one whose message starts `WIP quick mode:`.

## Done

- Home link and `/print-ready` page: upload one or many files, pick A6, A5, A4, A3, DL, business card 90×50 or 90×55, or a custom size, optional quantity and notes, one button.
- Pipeline in `server/quick_print.py` calls the existing compile script with Automatic bleed at 5 mm. It does not edit the press engine. AI Rebuild runs only when the original file is detected as AI raster. Shape mismatches are mirror-extended, not stretched.
- Traffic lights: green download plus proof, amber with the reason and a one-click approve, red for Word/PowerPoint, unreadable files, and pictures that stay too small after a 4× enlarge, with a client message to copy.
- Client proof PDF/PNG with a pink trim line. Batch result cards. Drop folder is off until a full folder path is saved; no new `.env` key; Input / Output / Needs-attention, no admin rights. Job list can filter to amber and red that are not yet approved.
- Tests added: `server/quickPrint.test.ts`, `client/src/pages/print-ready-view.test.tsx`, `server/quick_print_check.py` (`npm run test:quick-print`).
- Last full `python3 server/quick_print_check.py` run printed `ALL QUICK PRINT CHECKS PASSED`, including the Word red case, the 1024 flyer on A5, the already-bled PDF (bleed not doubled), and the wide image.
- In that same session, `npm run test:bleed-matrix` was 76 passed, 0 failed, and `python3 server/ai_rebuild_check.py` passed, including the visual press-PDF checks. Type-check stayed at the four known errors.

## Left

- Re-run the 14 manual bleed styles and confirm they are still byte-identical to `/tmp/bleed-baseline/baseline.json`. That compare was not finished after this feature.
- Start the dev server and save screenshots of the quick-mode page and a finished result card.
- Look again at the rendered press PDFs under `/opt/cursor/artifacts/` (`quick-print-existing-bleed.png`, `quick-print-ai-flyer.png`, `quick-print-ai-flyer-proof.png`, `quick-print-wrong-aspect.png`) and update PR #10. The proof and the existing-bleed page looked right. The flyer render contains the orange circle and the words MARKET DAY and SATURDAY.

## Open issue: wide-image centre mark

`test_wide` in `server/quick_print_check.py` failed once with `wide-not-stretched — 68x539 ratio 0.13`.

The centre mark is a green square on a 1800×700 banner. The check treated every “green” pixel as one box. Two things made that box a tall strip: uint8 overflow (`red + 20` wrapped, so the red end bar counted as green), and mirror-extend repeats the picture when the pad is taller than the source, so more than one green square is stacked. The largest blob by itself was about 29×30 px (ratio about 0.97).

The check was changed to compare colours as int16 and to measure only the largest connected green blob. The following run passed `wide-not-stretched`. It is still worth a human look at `quick-print-wrong-aspect.png`: the page is portrait, both end bars are present, and the green mark should be a square, not a stretched column. If the repeated mirrors make the sales proof look wrong, the fit step needs a change. Do not change the press engine to do that.
