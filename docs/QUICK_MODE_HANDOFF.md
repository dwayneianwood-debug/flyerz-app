# Quick mode handoff

Branch: `cursor/artwork-preview-lightbox-e73c`. The feature is finished on this branch.

## Done

- Home link and `/print-ready` page: upload one or many files, pick A6, A5, A4, A3, DL, business card 90×50 or 90×55, or a custom size, optional quantity and notes, one button.
- Pipeline in `server/quick_print.py` calls the existing compile script with Automatic bleed at 5 mm. It does not edit the press engine. AI Rebuild runs only when the original file is detected as AI raster.
- A picture that does not match the product is kept whole. The gap is filled by repeating the outer pixel (`BORDER_REPLICATE`, 1 pixel). The picture is not stretched and is not mirrored into a stack of copies.
- Traffic lights: green download plus proof, amber with the reason and a one-click approve, red for Word/PowerPoint, unreadable files, and pictures that stay too small after a 4× enlarge, with a client message to copy.
- Client proof PDF/PNG with a pink trim line. Batch result cards. Drop folder is off until a full folder path is saved; no new `.env` key; Input / Output / Needs-attention, no admin rights. Job list can filter to amber and red that are not yet approved.

## Checks on 29 Sep 2026 (after the edge-fit fix)

- `npm run test:quick-print`: passed. Word is red with no press file. The 1024 flyer on A5 has a press file and 5 mm bleed. The already-bled PDF stays green and the bleed is not doubled. The wide image is amber, portrait, both end bars kept, one square green mark.
- `npm run test:bleed-matrix`: 76 passed, 0 failed, 72s.
- Manual styles recompiled from `/tmp/bleed-baseline/photo.png` and `flat.png`: 14 files, 0 differences against `baseline.json`.
- `python3 server/ai_rebuild_check.py`: passed, including the press-PDF visual checks (panel and photo Delta E 0.00).
- `tsc --noEmit`: only the four existing errors (`optimize-worker-client.ts` TS2802, `fileProcessor.ts` TS2339, `storage.ts` TS2307, `taskQueue.ts` TS2802).

## Wide-image centre mark

Closed. An earlier check measured a tall strip because a red bar was counted as green and a mirror pad repeated the banner. Quick mode now copies only the outer pixel. The rendered page has one green square (about 66×67 px, ratio 0.99). The press engine was not changed for this.

## Looked at

- Existing-bleed page: red ring, green trim, the words ALREADY BLEED.
- Flyer press page and proof: orange circle, gold panel, MARKET DAY and SATURDAY. The proof has a pink trim line.
- Wide page: red bar, blue bar, one green square, flat grey above and below from the edge copy.
