# Flyerz.co.za Artwork Intelligence

## Overview
Flyerz.co.za Artwork Intelligence (FAI) is a full-stack application designed to automatically audit and correct PDF, JPG, PNG, DOCX, and PPTX files to meet professional litho-printing standards. Its primary purpose is to validate artwork against a comprehensive set of print rules and then apply automated fixes, ensuring files are print-ready. The project aims to provide a robust prepress automation suite that streamlines the printing workflow, reduces manual errors, and improves efficiency for print service providers and their clients. This leads to reduced manual errors, improved efficiency, and higher customer satisfaction in the printing industry.

## User Preferences
Not specified.

## System Architecture
The application features a modern full-stack architecture focused on prepress automation and user experience.

### UI/UX Decisions
The frontend, built with React, TypeScript, Vite, Tailwind CSS, shadcn/ui, and TanStack Query, provides an installable Progressive Web App (PWA). It includes a multi-phase wizard for file processing, review, and download, offering visual feedback for bleed, safe zones, and crop lines, and configurable print settings. It features an animated cat mascot, Glitchy, for dynamic feedback and pre-flight summaries, and a dark-mode audit dashboard with grouped categories and scanning animations. Additional UI components include an interactive 3D Trim Inspector (`GuillotineCutModal`) for visualizing bleed trimming and an AR Proofing view (`/ar-proof/:jobId`) using `<model-viewer>` for digital twin preview with gloss/matte toggles.

### Technical Implementations
- **Frontend**: React, TypeScript, Vite, Tailwind CSS, shadcn/ui, TanStack Query.
- **Backend**: Express.js with TypeScript.
- **Database**: PostgreSQL with Drizzle ORM.
- **Python Processing Layer**: Utilizes OpenCV, PyMuPDF (fitz), pikepdf, Pillow, and NumPy for image and document manipulation.
- **System Tools**: Ghostscript is used for CMYK conversion and font handling, with strict memory management (50MB limits). Speed-optimized with DCTEncode (JPEGQ=95), AlphaBits=1, and TMPDIR on RAM-disk. Pre-flatten converts pages to JPEG q85 before GS handoff.
- **Deployment**: Uses a VM for persistent resources and fast performance. Temporary files are routed to `/dev/shm/flyerz_tmp` (RAM-backed) in production for zero disk I/O latency. GS subprocess TMPDIR also set to RAM-disk with disk fallback.

### Feature Specifications
- **Prepress Automation Suite**: Performs 21 automated checks across 11 prepress sections, including adaptive per-side bleed extension, auto-crop mockup bounding box, precision resizer + CMYK engine, AI resolution enhancement, supersampled lens flattening, and an emergency raster pipeline. It ensures `True Litho Standards` with ICC profiles, font outlining, and `Hairline Stroke Enforcement`.
- **Prepress Output**: Provides `Bleed & Cut Line Preview` and compiles `Press-Ready PDF` with selected bleed strategies, CMYK conversion, rich black neutralization, trim marks, and metadata. The `_enforce_final_mediabox()` function ensures every compiled PDF's MediaBox matches the UI-selected format dimensions plus 5mm bleed using **cover scaling** (`max()` of x/y scale factors) — never contain/fit-inside which would leave white borders. Cover scaling runs unconditionally for all inputs (image and PDF paths) when trim dimensions are provided, not just for manual crops. `_enforce_single_layer()` purges all vector elements, leaving one raster image layer per page.
- **Visual Proof & Artwork Health Report**: Generates screen-optimized visual proofs and a branded PDF health report with "UPLOADED vs FIXED" comparisons.
- **Correction Tools**: Includes `ResizeAudit`, `Remove Background` (OpenCV GrabCut-based), and a `Manual Crop & Downscale Tool`. An `Auto-Shifter` (for safe zone violations) and `Gutter Genius (Creep)` are also implemented.
- **AI Enhancements**: A module (`server/ai_enhancements.py`) provides 12 non-destructive functions, including `denoise`, `sharpen_logos`, `background_remove`, `expand_background`, `spell_check`, `identify_fonts`, `test_design_style`, `tac_limit`, and `trapping`. These integrate with Replicate and Gemini APIs for advanced image and text processing.
- **Workflow & Efficiency**: Supports `Asynchronous Pre-Compilation & Instant Download`, `Share Report & Files`, `Batch Processing`, `Fast Track / One-Click Approve`, and a `Concurrency Queue`. A `Background Janitor` cleans up temporary files.
- **Print Standards Compliance**: DPI metadata is pinned at 300, and Color intent is set to Relative Colorimetric + KPreserve=2. A `Dry-Time Calculator` provides warnings for high TAC values.

## External Dependencies
- **PostgreSQL**: Primary database.
- **OpenCV**: Python library for computer vision.
- **PyMuPDF (fitz)**: Python library for PDF processing.
- **pikepdf**: Python library for PDF manipulation.
- **Pillow**: Python Imaging Library for image processing.
- **NumPy**: Python library for numerical computing.
- **Ghostscript**: System utility for PostScript and PDF interpretation, CMYK conversion, and font handling.
- **Replicate**: AI API for image enhancements (e.g., denoise, sharpen, background removal).
- **Gemini API**: AI API for text and vision tasks (e.g., spell check, font identification).
- **Resend**: Email sending service.
- **ReportLab**: Python library for generating PDF documents (used for the 25-Point Check Guide).
- **qrcode.react**: React component for generating QR codes.

## preserveBleed Safety Net
When `preserveBleed=True`, the pipeline bypasses `scale_fill` center cropping to preserve existing bleed margins. To prevent oversized raw images from crashing Ghostscript's 50MB memory wall:
- **Client-side**: Images are pre-compressed to JPEG q85, alpha flattened to white, and capped at 4000px max dimension before upload.
- **Server-side** (`smart_bleed.py`): Images are proportionally downscaled to `(target + 2×bleed + 15% headroom)` at 300 DPI if they exceed sane dimensions. Alpha is flattened to white. This runs unconditionally before `_constrain_to_max_px` and bleed processing.
- **Error surfacing**: All GS "no output" and "empty output" errors now include the GS stderr content for diagnostics.