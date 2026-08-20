# AGENTS.md

## Cursor Cloud specific instructions

### What this app is
Flyerz.co.za Artwork Intelligence (FAI): a full-stack prepress auditor for PDF/JPG/PNG/DOCX/PPTX artwork. There is **one** dev process — an Express API that runs Vite in middleware mode for the React client. The heavy prepress work is done by Python scripts in `server/*.py` (PyMuPDF, OpenCV, pikepdf, Pillow, reportlab) that shell out to **Ghostscript**. Root `main.py` is just a stub; do not run it as the web app.

### Running the dev server (non-obvious)
- Start with `SKIP_GATEKEEPER=1 PORT=5000 npm run dev`, then open `http://127.0.0.1:5000/`.
- **Why `SKIP_GATEKEEPER=1`:** after the server starts listening, `server/index.ts` runs a Python gatekeeper (`server/pre_deploy_check.py`, which runs `server/test_pipeline.py`). If it fails, the Node process **exits**. The committed code currently fails 8 gatekeeper/pipeline assertions (TrimBox geometry expects trim size but the compiler emits full MediaBox, plus a few source-string architecture checks). These are pre-existing repo issues, unrelated to environment setup — so use the built-in `SKIP_GATEKEEPER=1` escape hatch to run the app locally.
- `DEV.md` / `start_dev.ps1` are Windows/PowerShell only. On this Linux VM just use `npm run dev` directly.
- No PostgreSQL is required. Despite `server/db.ts` (Postgres/Drizzle) it is not imported at runtime; job storage uses Node's built-in `node:sqlite` at `data/flyerz.sqlite` (auto-created). `drizzle-kit` / `DATABASE_URL` are not needed for the web app.

### Missing gitignored asset (will break the UI if absent)
`attached_assets/` is gitignored and not in the repo, but the client imports `@assets/flyerz_logo.png` in `client/src/components/layout.tsx`. If that file is missing, Vite throws an unresolved-import error that can crash the dev process. The update script recreates a placeholder from the committed `client/public/icon-192x192.png` when it's missing.

### Lint / test / build
- Typecheck/lint: `npm run check` (runs `tsc`). It currently reports pre-existing type errors (e.g. `node:sqlite` has no bundled types, a couple of `wouter` route prop and `downlevelIteration` errors). The dev server runs via `tsx`, which does not typecheck, so it runs regardless.
- Python pipeline tests: `python3 server/test_pipeline.py` (end-to-end) and `python3 run_full_diagnostic.py`. Both require Ghostscript + the Python prepress libs.
- Production build: `npm run build` (Vite client + esbuild server → `dist/`); `npm start` runs the production bundle. Not needed for day-to-day dev.

### System dependencies
Ghostscript (`gs`) and `libzbar0` (for `pyzbar`) are required and are baked into the environment snapshot. The prepress Python libraries are installed into the user site-packages that `python3` resolves; the server spawns `python3` by default (override with `PYTHON_BIN`).
