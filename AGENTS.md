# Flyerz.co.za - Cloud Agent & Development Guide
## Local Development & Startup
* **Start Dev Server:** `SKIP_GATEKEEPER=1 PORT=5000 npm run dev`
* **Environment Dependencies:** Requires Ghostscript (10.02.1) and `libzbar0` installed on the host/container system for the PDF compilation and QR barcode pipeline to function correctly.
---
## Immutable Development & Prepress Rules
### 1. Memory Safety (The 8GB Replit Limit)
* Never suggest code that removes Ghostscript memory leashes.
* `BufferSpace` and `MaxBitmap` must always be capped at **50MB**.
* `NumRenderingThreads` must remain at **1**.
### 2. Prepress Precision
* **Bleed:** Always use a **1-pixel sampling radius** for Edge Replication / Pixel-Drift.
* **DPI:** All outputs must have **300 DPI** metadata forcefully injected via PIL, PyMuPDF, and Ghostscript (`-dHWResolution=300`).
* **Color:** Preserve Rich Black using `-dBlackPtComp=1`, `KPreserve=2`, and **Relative Colorimetric** rendering intent.
### 3. Layer Integrity
* Ensure final PDF outputs are flattened to **one single image layer per page**.
* Purge all "Ghost Layers" or original vector elements before final compilation.
### 4. Zero Regression Policy
* Every new feature or fix **MUST** include a corresponding automated test.
* Before any deployment, verify that all existing regression tests pass successfully.
### 5. The "No Crop" Route
* Always ensure the "No Crop Needed" button bypasses the UI but correctly prepares the backend data structure (full-page `crop_box`) to prevent "Document Closed" errors.
