#!/usr/bin/env python3
"""
Flyerz.co.za Artwork Intelligence — System Intelligence PDF (HTML + ReportLab).
Generates the dark-mode System Intelligence guide; not the legacy Prepress checklist PDF.
"""

import sys
import json
import os
from datetime import datetime

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor, white, black
from reportlab.lib.styles import ParagraphStyle
import math
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, HRFlowable,
    Table, TableStyle, PageBreak, Flowable,
)
from reportlab.lib.enums import TA_CENTER

PAGE_W, PAGE_H = A4
MARGIN = 18 * mm

BRAND_BLUE = HexColor("#0474fc")
BRAND_CYAN = HexColor("#00cc88")
BRAND_RED = HexColor("#ff2500")
PAGE_BG = HexColor("#0a0e18")
CARD_BG = HexColor("#16213e")
CARD_BORDER = HexColor("#334155")
TEXT_PRIMARY = HexColor("#f1f5f9")
TEXT_MUTED = HexColor("#94a3b8")
DARK = TEXT_PRIMARY
MUTED = TEXT_MUTED
LIGHT_BG = CARD_BG
GREEN = HexColor("#16a34a")
AMBER = HexColor("#d97706")
RED = HexColor("#dc2626")

# DASHBOARD_RULES_COPY: keep in sync with client/src/pages/dashboard-rules.tsx (PHASES, hero, ShrinkBleedDiagram).
# ENGINE_SAFE_ZONE_SPEC: mirrors server/smart_bleed.py validate_safe_zone / auto_resolve_safe_zone / SAFE_ZONE_MM.
DASHBOARD_RULES_COPY = {
    "panel_tag": "System Intelligence",
    "panel_title": "25-Point Check — How the engine thinks",
    "panel_intro": (
        "Same <strong>25-point litho audit</strong> you see inside the "
        "Artwork Intelligence Report—delivered as live automation: Radar, Auto-Heal shrink/bleed, "
        "melt-based AI outpaint, and vector-safe PDF surgery."
    ),
    "diagram_heading": "Shrink & Re-Bleed motion",
    "diagram_flow": (
        "validate_safe_zone hit → img.copy() → INTER_AREA micro-shrink (30 px/side) → "
        "bleed_px_effective = base + 30 → bleed engine re-runs → litho receives a full halo again."
    ),
    "diagram_before_title": "Before",
    "diagram_before_body": (
        "Trim canvas locked • live type hugging the danger band • risk on guillotine drift."
    ),
    "diagram_mid_label": "Auto-heal",
    "diagram_after_title": "After",
    "diagram_after_body": (
        "Tighter content island • extended bleed • edge melt / strategy fill restores press-safe coverage."
    ),
    "footer_note": "Dark console styling matches the live Artwork Intelligence Report panels.",
}

ENGINE_SAFE_ZONE_SPEC = {
    "safe_zone_mm": 3.0,
    "shrink_px_per_side": 30,
    "shrink_total_per_axis_px": 60,
    "bleed_boost_px_on_violation": 30,
    "resize": "cv2.INTER_AREA",
    "work_buffer": "img_bgr.copy() before any resize or bleed",
    "validate": "validate_safe_zone — median-adaptive threshold per 3 mm edge strip, MORPH_OPEN 3×3",
    "resolve": (
        "auto_resolve_safe_zone — Elastic Anchor via composite_ghost_frame_pullback when typography violates "
        "SAFE_ZONE_MM; outward bleed via pixel_drift_bleed_expand (3px seam feather); no canvas shrink"
    ),
}

sTitle = ParagraphStyle("Title", fontName="Helvetica-Bold", fontSize=22,
                         textColor=TEXT_PRIMARY, spaceAfter=4, alignment=TA_CENTER,
                         leading=26)
sSubtitle = ParagraphStyle("Subtitle", fontName="Helvetica", fontSize=11,
                            textColor=TEXT_MUTED, spaceAfter=14, alignment=TA_CENTER,
                            leading=14)
sSectionHead = ParagraphStyle("SectionHead", fontName="Helvetica-Bold", fontSize=14,
                               textColor=BRAND_CYAN, spaceBefore=16, spaceAfter=6,
                               leading=18)
sCheckName = ParagraphStyle("CheckName", fontName="Helvetica-Bold", fontSize=11,
                             textColor=TEXT_PRIMARY, spaceAfter=2, leading=14)
sCheckNum = ParagraphStyle("CheckNum", fontName="Helvetica-Bold", fontSize=9,
                            textColor=white, alignment=TA_CENTER)
sBody = ParagraphStyle("Body", fontName="Helvetica", fontSize=9.5,
                        textColor=TEXT_PRIMARY, spaceAfter=3, leading=13)
sBodyMuted = ParagraphStyle("BodyMuted", fontName="Helvetica", fontSize=9,
                             textColor=TEXT_MUTED, spaceAfter=2, leading=12)
sLabel = ParagraphStyle("Label", fontName="Helvetica-Bold", fontSize=9,
                         textColor=BRAND_BLUE, spaceAfter=1, leading=12)
sOutcome = ParagraphStyle("Outcome", fontName="Helvetica", fontSize=8.5,
                           textColor=TEXT_PRIMARY, leading=11)
sFooter = ParagraphStyle("Footer", fontName="Helvetica", fontSize=7.5,
                          textColor=TEXT_MUTED, alignment=TA_CENTER, leading=10)
sIntro = ParagraphStyle("Intro", fontName="Helvetica", fontSize=10,
                         textColor=TEXT_PRIMARY, spaceAfter=8, leading=14, alignment=TA_CENTER)
sBrand = ParagraphStyle("Brand", fontName="Helvetica-Bold", fontSize=8,
                         textColor=BRAND_CYAN, alignment=TA_CENTER, spaceAfter=2,
                         leading=10)


def _paint_dark_page(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(PAGE_BG)
    canvas.rect(0, 0, doc.pagesize[0], doc.pagesize[1], fill=1, stroke=0)
    canvas.restoreState()


class BrandIconFlowable(Flowable):
    """Tiny vectorglyph for intelligence channel rows (Helvetica-safe, no emoji fonts)."""

    def __init__(self, kind: str, size: float = 14):
        self.kind = kind
        self._size = size

    def wrap(self, aW, aH):
        return self._size, self._size

    def draw(self):
        c = self.canv
        s = self._size
        p = 1.25
        c.saveState()
        c.setStrokeColor(BRAND_CYAN)
        c.setFillColor(BRAND_CYAN)
        c.setLineWidth(0.7)
        c.setLineJoin(1)
        c.setLineCap(1)
        k = self.kind

        if k == "radar":
            c.circle(s / 2, s / 2, s / 2 - p, stroke=1, fill=0)
            for ang in (-0.2, 0.6, 1.3):
                c.line(s / 2, s / 2, s / 2 + (s * 0.32) * math.cos(ang),
                       s / 2 + (s * 0.32) * math.sin(ang))
        elif k == "droplet":
            path = c.beginPath()
            path.moveTo(s / 2, s - p)
            path.curveTo(s - p, s * 0.55, s - p, s * 0.32, s / 2, p * 1.5)
            path.curveTo(p, s * 0.32, p, s * 0.55, s / 2, s - p)
            c.drawPath(path, stroke=1, fill=0)
        elif k == "shield":
            path = c.beginPath()
            path.moveTo(s / 2, s - p)
            path.lineTo(s - p, s * 0.58)
            path.lineTo(s - p, s * 0.18)
            path.lineTo(p, s * 0.18)
            path.lineTo(p, s * 0.58)
            path.close()
            c.drawPath(path, stroke=1, fill=0)
        elif k == "chip":
            c.rect(p, s * 0.28, s - 2 * p, s * 0.44, stroke=1, fill=0)
            c.line(s * 0.35, p, s * 0.35, s - p)
            c.line(s * 0.65, p, s * 0.65, s - p)
        elif k == "vector":
            c.rect(p * 1.2, p * 1.2, s * 0.35, s * 0.35, stroke=1, fill=0)
            c.rect(s * 0.45, s * 0.42, s * 0.4, s * 0.4, stroke=1, fill=0)
        elif k == "layers":
            for off, alpha in [(0, 1), (1.8, 0.65), (3.6, 0.45)]:
                c.saveState()
                c.setStrokeAlpha(alpha)
                c.rect(p + off, p + off, s - 2 * p - off, s - 2 * p - off, stroke=1, fill=0)
                c.restoreState()
        elif k == "crop":
            c.setLineWidth(1)
            c.rect(p * 2, p * 2, s - 4 * p, s - 4 * p, stroke=1, fill=0)
            c.line(p * 2, p * 2, p * 2 + s * 0.28, p * 2)
            c.line(p * 2, p * 2, p * 2, p * 2 + s * 0.28)
        elif k == "bleed":
            cx, cy = s / 2, s / 2
            for dx, dy in [(0, 1), (0, -1), (1, 0), (-1, 0)]:
                c.line(cx - dx * s * 0.08, cy - dy * s * 0.08,
                       cx + dx * s * 0.36, cy + dy * s * 0.36)
        elif k == "layout":
            c.line(s / 2, p, s / 2, s - p)
            c.line(p, s / 2, s - p, s / 2)
            c.circle(s / 2, s / 2, s * 0.22, stroke=1, fill=0)
        elif k == "book":
            c.line(s / 2, p, s / 2, s - p)
            c.rect(s / 2 + 1.5, p * 2, s / 2 - p * 2, s - 4 * p, stroke=1, fill=0)
        else:
            c.circle(s / 2, s / 2, s / 4, stroke=1, fill=1)

        c.restoreState()


class ShrinkBleedDiagramFlowable(Flowable):
    """Shrink & Re-Bleed motion — copy aligned with dashboard-rules ShrinkBleedDiagram."""

    def __init__(self, width: float):
        self._tw = width
        self._h = 102

    def wrap(self, aW, aH):
        self.width = min(self._tw, aW)
        return self.width, self._h

    def draw(self):
        dc = DASHBOARD_RULES_COPY
        canv = self.canv
        w, h = getattr(self, "width", self._tw), self._h
        canv.saveState()
        canv.setFillColor(CARD_BG)
        canv.setStrokeColor(CARD_BORDER)
        canv.roundRect(0, 0, w, h, 4, stroke=1, fill=1)
        canv.setFont("Helvetica-Bold", 8)
        canv.setFillColor(HexColor("#4ade80"))
        canv.drawString(8, h - 12, dc["diagram_heading"])
        canv.setFont("Helvetica", 6.5)
        canv.setFillColor(TEXT_MUTED)
        flow = dc["diagram_flow"]
        canv.drawString(8, h - 24, flow[: min(92, len(flow))])
        if len(flow) > 92:
            canv.drawString(8, h - 32, flow[92: min(184, len(flow))])
        y_body = h - 44

        x0, y0, bw, bh = 10, 8, (w - 40) / 2, 46
        canv.setStrokeColor(BRAND_RED)
        canv.setFillColor(HexColor("#2a1a24"))
        canv.rect(x0, y0, bw, bh, stroke=1, fill=1)
        canv.setFillColor(HexColor("#fecaca"))
        canv.setFont("Helvetica-Bold", 7)
        canv.drawString(x0 + 4, y0 + bh - 10, dc["diagram_before_title"])
        canv.setFont("Helvetica", 6)
        canv.setFillColor(TEXT_MUTED)
        before_lines = dc["diagram_before_body"]
        canv.drawString(x0 + 4, y0 + 20, before_lines[:70])
        if len(before_lines) > 70:
            canv.drawString(x0 + 4, y0 + 12, before_lines[70:140])

        cx = x0 + bw + 4
        canv.setStrokeColor(BRAND_BLUE)
        canv.setFillColor(BRAND_BLUE)
        canv.setFont("Helvetica-Bold", 9)
        canv.drawCentredString(cx + 10, y0 + bh / 2 + 4, "\u2192")
        canv.setFont("Helvetica", 5.5)
        canv.setFillColor(TEXT_MUTED)
        canv.drawCentredString(cx + 10, y0 + bh / 2 - 8, dc["diagram_mid_label"].upper())

        x1 = x0 + bw + 28
        canv.setStrokeColor(BRAND_CYAN)
        canv.setFillColor(HexColor("#0f2840"))
        canv.rect(x1, y0, bw, bh, stroke=1, fill=1)
        canv.setFillColor(HexColor("#bbf7d0"))
        canv.setFont("Helvetica-Bold", 7)
        canv.drawString(x1 + 4, y0 + bh - 10, dc["diagram_after_title"])
        canv.setFont("Helvetica", 6)
        canv.setFillColor(TEXT_MUTED)
        after_lines = dc["diagram_after_body"]
        canv.drawString(x1 + 4, y0 + 20, after_lines[:70])
        if len(after_lines) > 70:
            canv.drawString(x1 + 4, y0 + 12, after_lines[70:140])

        canv.restoreState()


ICON_BY_CHECK = {}
for _icon, keys in (
    ("bleed", ("1",)),
    ("droplet", ("2", "2b")),
    ("chip", ("3", "3b")),
    ("vector", ("4", "5")),
    ("layers", ("6",)),
    ("crop", ("6b", "6c", "6d", "6e")),
    ("shield", ("6f", "16", "17", "2h")),
    ("radar", ("7", "10")),
    ("layout", ("8", "9", "11", "12")),
    ("book", ("13", "14", "15")),
):
    for _k in keys:
        ICON_BY_CHECK[_k] = _icon


def icon_for_check(num_val) -> str:
    return ICON_BY_CHECK.get(str(num_val), "chip")


def build_system_intelligence_phase_story(usable_w: float):
    s_phase = ParagraphStyle(
        "PhaseTitle", fontName="Helvetica-Bold", fontSize=11,
        textColor=BRAND_BLUE, spaceBefore=8, spaceAfter=4, leading=14,
    )
    s_phase_body = ParagraphStyle(
        "PhaseBody", fontName="Helvetica", fontSize=9,
        textColor=TEXT_MUTED, spaceAfter=6, leading=12,
    )
    blocks = []

    blocks.append(Paragraph(
        "<b>How System Intelligence is organised</b> &mdash; three live layers before the granular "
        "25-point audit. Same litho channels you see in the dark console; narrated as live automation.",
        ParagraphStyle("IntelHead", parent=sIntro, textColor=BRAND_CYAN, fontSize=10.5, fontName="Helvetica-Bold")
    ))
    blocks.append(Spacer(1, 4))

    phases = (
        ("Phase 1: The Non-Destructive Core",
         "Zero-destructive sizing: we never <b>squish</b> artwork with anamorphic scaling. Trim preparation uses "
         "proportional <b>cover</b> fills and crop-box math only; print masters stay on a strict <b>1:1 pixel fidelity</b> rule "
         "unless you explicitly steer crop or trim. "
         "<b>50 MB memory leash</b> on the server rejects oversized uncompressed arrays fast so ultra–high-resolution "
         "jobs fail loud instead of swapping or corrupting litho proofs. "
         "<b>Vector preservation</b>: incorrect PDF boundaries are corrected by mathematically "
         "<b>redefining MediaBox / CropBox / TrimBox</b> with PyMuPDF &mdash; vectors stay crisp; we do not rasterize pages "
         "just to &ldquo;fix&rdquo; geometry."),
        ("Phase 2: Safe Zone Intelligence",
         "<b>Shrink &amp; Re-Bleed (Auto-Heal):</b> When <b>validate_safe_zone()</b> flags content inside the "
         f"<b>{ENGINE_SAFE_ZONE_SPEC['safe_zone_mm']} mm</b> inner strips derived at runtime DPI, "
         "<b>auto_resolve_safe_zone()</b> works from <b>img_bgr.copy()</b>, applies <b>30 px/side</b> "
         f"<b>{ENGINE_SAFE_ZONE_SPEC['resize']}</b> downscale (canvas loses {ENGINE_SAFE_ZONE_SPEC['shrink_total_per_axis_px']} px per axis), "
         f"then raises bleed to <b>base + {ENGINE_SAFE_ZONE_SPEC['bleed_boost_px_on_violation']} px</b> before the bleed engine re-runs. "
         "<b>Canny radar:</b> 30 px outer bands score high-frequency edge density so typography and logo energy show up before the guillotine."),
        ("Phase 3: Smart Bleed Engine",
         "<b>AI Outpaint (Melt):</b> the <b>Content-Aware Abstraction</b> path reflects and fuses border pixels, "
         "applies a controlled Gaussian melt, injects subtle <b>litho grain</b>, and feathers alpha so extended areas "
         "match print texture instead of looking like a flat clone. "
         "<b>Pixel-Drift precision</b> uses a <b>1-pixel sampling radius</b> on edge replication / stretch paths so "
         "inks drift outward naturally &mdash; no backward text and no kaleidoscope seams."),
    )
    phase_row_icons = ("shield", "radar", "droplet")
    for idx, (title, body) in enumerate(phases):
        tbl = Table(
            [[BrandIconFlowable(phase_row_icons[idx], 13), Paragraph(f"<b>{title}</b>", s_phase)]],
            colWidths=[16, usable_w - 16],
        )
        tbl.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (1, 0), (1, 0), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))
        blocks.append(tbl)
        blocks.append(Paragraph(body, s_phase_body))

    legend_icons = Table(
        [
            [
                BrandIconFlowable("radar", 12),
                Paragraph("<font size=\"7\" color=\"#94a3b8\">Safe zone / radar</font>", sOutcome),
                BrandIconFlowable("droplet", 12),
                Paragraph("<font size=\"7\" color=\"#94a3b8\">Ink / CMYK family</font>", sOutcome),
                BrandIconFlowable("shield", 12),
                Paragraph("<font size=\"7\" color=\"#94a3b8\">Memory &amp; integrity</font>", sOutcome),
            ],
        ],
        colWidths=[14, usable_w / 6, 14, usable_w / 6, 14, usable_w / 3],
    )
    legend_icons.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
    ]))
    blocks.append(Spacer(1, 4))
    blocks.append(legend_icons)
    return blocks


CHECKS = [
    {
        "section": "CORE PRINT COMPLIANCE",
        "color": BRAND_BLUE,
        "checks": [
            {
                "num": 1,
                "name": "Bleed Detection & Correction",
                "what": "Bleed is the extra area of artwork that extends beyond the final trim edge. Without it, slight cutting variations leave white borders on the finished product. This check analyses all four sides of your artwork to determine if bleed is present and sufficient.",
                "looks_for": "The engine examines each edge independently using a two-stage pipeline:\n\n"
                    "<b>Stage 1 \u2014 Auto-Crop False Margins:</b> Removes fake white/black borders (exceeding 0.5% of image area) using threshold detection at 250/255 brightness.\n\n"
                    "<b>Stage 2 \u2014 Content-Aware Five-Strategy Extension:</b> Each edge is independently analysed using a priority-based detection system.\n\n"
                    "<b>Priority 1 \u2014 Text/Logo Detection (15px zone):</b> Canny edge detection + contour analysis scans a 15-pixel strip at each edge for text-like shapes. If detected \u2192 <b>Background Extract</b> \u2014 samples the dominant background colour and extends only the background outward, keeping text and logos untouched.\n\n"
                    "<b>Priority 2 \u2014 High Complexity (StdDev > 35.0):</b> Uses <b>Pixel-Drift Stretching</b> \u2014 a strict <b>1-pixel sampling radius</b> pulls edge energy outward with controlled drift so nothing reads backwards and kaleidoscope artefacts never form.\n\n"
                    "<b>Priority 3 \u2014 Medium Complexity (StdDev 15.0\u201335.0):</b> Uses <b>Mirror + Cross-Fade</b> \u2014 mirror reflection with 8px overlap + 10-pixel alpha blend at the junction. Best for grass, sky, and blurred backgrounds.\n\n"
                    "<b>Priority 4 \u2014 Low Complexity (StdDev \u2264 15.0):</b> Uses pure <b>Edge Replication</b> \u2014 stretches the last row/column outward for seamless solid colour continuation.\n\n"
                    "<b>Priority 5 \u2014 Upscale:</b> Uses <b>Proportional Upscale</b> \u2014 enlarges the entire artwork proportionally to fill the bleed area, preserving global composition. Best for full-bleed photos and illustrations where edge content should scale naturally.\n\n"
                    "<b>Priority 6 \u2014 AI Outpaint (Content-Aware Abstraction / Melt):</b> Builds a synthetic bleed by reflecting border pixels, melting them with a tight Gaussian pass, feathering alpha, and injecting subtle <b>litho grain</b> so the healed band matches press texture instead of looking like a pasted strip.\n\n"
                    "<b>User-Selectable Variants:</b> Multiple bleed strategies (including AI Outpaint) are generated during processing. In the Review phase, you can compare variants side-by-side and pick the winner for your artwork.",
                "autofix": "Fully automatic. Extends each edge by the configured bleed amount (default 5 mm) using the optimal method for each edge. The engine keeps Pixel-Drift sampling at a <b>single-pixel radius</b>, offers the <b>melted</b> AI Outpaint path where needed, and never introduces backward text. Override by choosing a variant in Review.",
                "outcomes": [
                    ("\u2705 Passed", "Artwork already has correct bleed on all sides."),
                    ("\u2728 Auto-Fixed", "Bleed was missing or insufficient and has been added automatically."),
                    ("\u26a0\ufe0f Failed", "Bleed could not be added (extremely rare \u2014 e.g. corrupt image data)."),
                ]
            },
            {
                "num": 2,
                "name": "CMYK Colour Space Conversion",
                "what": "Litho printing uses CMYK (Cyan, Magenta, Yellow, Key/Black) inks. Artwork designed in RGB (screen colours) must be converted to CMYK to ensure accurate colour reproduction on paper. Without conversion, vibrant screen blues and greens can appear dull or shifted in print.",
                "looks_for": "Analyses the file\u2019s embedded colour profile and pixel data to determine if it uses RGB, CMYK, or another colour space. For PDFs, inspects the colour space declarations on each page.",
                "autofix": "Fully automatic. Uses Ghostscript with vibrant colour-preserving settings:\n\n"
                    "\u2022 <b>Perceptual Rendering Intent</b> (-dRenderIntent=0): Maps the full RGB gamut into CMYK proportionally, keeping colours as punchy as possible rather than clipping out-of-gamut tones.\n"
                    "\u2022 <b>Black Point Compensation</b> (-dBlackPointCompensation=true): Prevents shadow detail from being crushed during conversion \u2014 dark areas stay rich instead of going muddy.\n"
                    "\u2022 <b>K-Channel Preservation</b> (-dKPreserve=1): Protects existing K-only elements (text, line art) from being re-separated into CMYK during conversion.\n"
                    "\u2022 <b>ICC Profile Respect</b>: Embedded ICC profiles in the artwork are honoured during conversion. The engine does NOT override embedded profiles, ensuring professionally colour-managed files convert accurately.\n"
                    "\u2022 <b>FOGRA39 OutputIntent</b>: The final press PDF is tagged with the FOGRA39 (ISO Coated v2) ICC profile as the PDF/X OutputIntent, ensuring consistent colour reproduction on coated paper across any press that supports the standard.\n\n"
                    "The /prepress PDF settings ensure maximum image quality with no downsampling.",
                "outcomes": [
                    ("\u2705 Passed", "Artwork is already in CMYK colour space."),
                    ("\u2728 Auto-Fixed", "Converted from RGB to CMYK automatically."),
                    ("\u26a0\ufe0f Failed", "Colour space could not be converted (Office sources must export through the PDF litho path)."),
                ]
            },
            {
                "num": "2b",
                "name": "CMYK Black Optimisation (K-Only Rules)",
                "what": "In litho printing, black ink (K) behaviour is critical. Poorly managed blacks cause registration errors (fuzzy text), colour shifting on neutral tones, and excessive ink on press. This suite of rules analyses every black element in your artwork and applies professional K-channel optimisation to ensure clean, sharp, press-stable blacks.",
                "looks_for": "The engine applies seven interconnected black rules:\n\n"
                    "<b>Rule 1 \u2014 Size-Aware Dual-Threshold K-Only Neutralisation:</b> Scans text, vectors, and strokes for neutral tones (greys) using a two-stage detection system with tightened thresholds to protect intentional designer colour tints:\n"
                    "\u2022 <b>Absolute Spread Test:</b> The difference between the highest and lowest CMY channel must be \u2264 0.04 (4%). Tightened from 8% to better protect warm/cool tints the client designed on purpose.\n"
                    "\u2022 <b>Relative Spread Test:</b> The spread divided by the average CMY value must be \u2264 0.15 (15%). Tightened from 19% for the same reason.\n"
                    "A colour must pass BOTH tests to be classified as neutral. Neutral CMYK is then converted to pure K using: effective_k = max(C, M, Y, K) \u2014 the highest channel value becomes the K-only value.\n\n"
                    "<b>Rule 2 \u2014 Size-Aware Processing:</b> Not all neutral blacks should be stripped to K-only. The engine now differentiates by element type and size:\n"
                    "\u2022 <b>Text below 18pt (inside BT/ET blocks):</b> Always converted to K-only with overprint ON \u2014 small text must be single-ink for sharp registration on press. Text 18pt and above follows the standard size-aware rules below.\n"
                    "\u2022 <b>Stroke operators (K):</b> Always converted to K-only \u2014 thin lines and outlines need single-ink sharpness.\n"
                    "\u2022 <b>Small fill elements (<56pt / ~20mm):</b> Converted to K-only \u2014 small graphics benefit from single-ink clarity.\n"
                    "\u2022 <b>Large fill elements (\u226556pt / ~20mm):</b> Keep original rich black (CMYK mix) \u2014 large solid backgrounds and panels look dull as pure K. Rich black gives them visual depth and vibrancy.\n"
                    "The size threshold of 56 PostScript points (~20mm) is applied by scanning ahead in the PDF content stream for rectangle operators (re) and checking their width and height. Both dimensions must exceed 56pt for the element to be classified as large.\n\n"
                    "<b>Rule 3 \u2014 RGB Neutral Detection:</b> For elements still in RGB, neutrality is detected when R, G, and B channels are within 3% absolute spread of each other. These follow the same size-aware rules above.\n\n"
                    "<b>Rule 4 \u2014 Three-Zone Overprint System:</b> For elements that ARE converted to K-only, the K percentage determines overprint behaviour:\n"
                    "\u2022 Deep Black (K > 70%): Converted to Press-Safe Rich Black (C40/M30/Y30/K100 = 200% TIC) with Overprint OFF (Knockout). This gives large dark areas visual depth and density without relying on overprint trapping, which can cause unpredictable results on different press configurations.\n"
                    "\u2022 Dark Grey (K 30\u201369%): Matched K value with Overprint ON \u2014 maintains tone while preventing white halos.\n"
                    "\u2022 Light Grey (K 5\u201329%): Matched K value with Overprint OFF (Knockout) \u2014 prevents tint contamination on light tones.\n"
                    "\u2022 Near-white (K < 5%): Left unchanged.\n\n"
                    "<b>Rule 5 \u2014 Overprint State Injection:</b> Two PDF Extended Graphics States are injected into the document resources: /FAI_OP_ON (OP=true, op=true, OPM=1) and /FAI_OP_OFF (OP=false, op=false, OPM=0). OPM=1 means underlying CMYK channels show through where the top colour has 0% in those channels. These states are created using pikepdf.Boolean() for correct PDF boolean encoding.\n\n"
                    "<b>Rule 6 \u2014 Total Ink Coverage (TAC/TIC):</b> Two-stage TIC management:\n"
                    "\u2022 <b>Stage A \u2014 In-stream Rich Black Clamping:</b> During K-only neutralisation, any neutral element with TIC exceeding 300% (C+M+Y+K > 3.0) and effective K \u2265 80% is clamped to Press-Safe Rich Black (40C/30M/30Y/100K = 200% TIC). This prevents muddy blacks from reaching the press.\n"
                    "\u2022 <b>Stage B \u2014 Ghostscript UCR/GCR:</b> During CMYK conversion, the /prepress profile applies Under Colour Removal (UCR) and Grey Component Replacement (GCR) to keep remaining total ink below safe press limits (typically 300\u2013340%), preventing ink pooling, slow drying, and set-off.\n\n"
                    "<b>Rule 7 \u2014 Registration Black & Spot Colour Detection:</b> Flags any Separation or DeviceN colour spaces (spot colours) that could produce registration black (100% C + 100% M + 100% Y + 100% K = 400% TAC). Registration black is never safe for print and is flagged as a critical issue.",
                "autofix": "Fully automatic for PDF files. Size-aware K-Only Neutralisation, the Three-Zone Overprint System, and Overprint State Injection are applied during PDF content stream processing. TAC/TIC management is handled by Ghostscript during CMYK conversion. The engine tokenises PDF content streams, tracks text blocks (BT/ET), scans ahead for element dimensions, and rewrites colour operators (k, K, rg, RG, g, G) in-place. Images are excluded from K-only processing. Large solid fills (>56pt/~20mm) keep their rich black CMYK for visual depth; text and small elements are stripped to K-only for sharpness.",
                "outcomes": [
                    ("\u2705 Passed", "Black handling is already correct \u2014 no neutral tones detected in CMY channels."),
                    ("\u2728 Auto-Fixed", "Neutral tones stripped to K-only and overprint states applied to prevent colour shifting on press."),
                    ("\u26a0\ufe0f Warning", "Spot colours or registration black detected \u2014 attention gate on console."),
                ]
            },
            {
                "num": 3,
                "name": "Resolution / DPI Validation",
                "what": "Print quality depends on having enough pixels per inch (DPI). Images below 300 DPI may appear soft when printed at litho quality. This check calculates the effective DPI based on the actual pixel dimensions of your artwork relative to the target print size (e.g. A4, A5) \u2014 not from unreliable file metadata.",
                "looks_for": "For images (JPG/PNG): Detects whether the file\u2019s EXIF metadata DPI is a known phone/screen value (72, 96, 144, 150, 180, 200 DPI \u2014 common in iPhone, Android, and scanner output). When phone metadata is detected, the check bypasses it and calculates <b>effective DPI at A4 print size</b> based on actual pixel dimensions:\n\n"
                    "effective_dpi = min(pixel_width \u00f7 (210mm \u00f7 25.4), pixel_height \u00f7 (297mm \u00f7 25.4))\n\n"
                    "This means a typical iPhone photo (4032\u00d73024 pixels) calculates to ~345 DPI at A4 \u2014 well above the 300 DPI minimum \u2014 even though its metadata says 144 DPI. For professionally exported files (metadata \u2265300 DPI), the metadata value is trusted directly.\n\n"
                    "For PDFs: Scans embedded images using page.get_images() and calculates per-image DPI as image_pixel_width \u00f7 (page_width_pt \u00f7 72). Vector-only PDFs are automatically classified as 300+ DPI equivalent.",
                "autofix": "When DPI is between 75 and 299, the engine automatically applies intelligent upscaling to boost resolution to 300+ DPI. Uses bicubic interpolation with multi-stage post-processing (sharpening, contrast enhancement, noise reduction) for print-quality results. Below 75 DPI, the source is too low for enhancement. The DPI check updates to reflect the enhanced resolution after upscaling.",
                "outcomes": [
                    ("\u2705 Passed", "Effective DPI is 300 or above \u2014 high quality for litho print."),
                    ("\u2728 AI Enhanced", "DPI was below 300 but has been intelligently upscaled to print-ready resolution."),
                    ("\u26a0\ufe0f Warning", "DPI is below 75 \u2014 too low for enhancement. Supply a higher-resolution source file."),
                ]
            },
            {
                "num": "3b",
                "name": "AI Resolution Enhancement",
                "what": "Low-resolution artwork (below 300 DPI) is automatically enhanced using intelligent upscaling. The engine reconstructs detail, sharpens text edges, enhances local contrast, and reduces noise \u2014 producing output that is significantly sharper than basic upscaling.",
                "looks_for": "Checks the effective DPI after the initial resolution validation. If DPI is between 75 and 299, the AI upscaler is triggered. The engine:\n\n"
                    "\u2022 Calculates the minimum scale factor needed to reach 300 DPI (capped at 4\u00d7).\n"
                    "\u2022 Applies an OOM guard: if the upscaled pixel count would exceed 200 million pixels, the scale is proportionally reduced.\n"
                    "\u2022 Applies bicubic interpolation for high-quality base upscale.\n"
                    "\u2022 Runs multi-stage post-processing: unsharp mask sharpening, CLAHE local contrast enhancement, bilateral noise reduction, and final sharpening pass.\n"
                    "\u2022 Recalculates effective DPI after enhancement to verify the target was reached.",
                "autofix": "Fully automatic. Runs during processing and the enhanced image is used for all subsequent steps (bleed extension, CMYK conversion, proof generation). The original file is preserved for comparison. After enhancement, the DPI check is updated to reflect the new resolution.",
                "outcomes": [
                    ("\u2705 Not Needed", "Image already meets 300 DPI minimum \u2014 no AI enhancement applied."),
                    ("\u2728 AI Enhanced", "Resolution boosted from source DPI to 300+ DPI using intelligent upscaling."),
                    ("\u26a0\ufe0f Skipped", "Source DPI is below 75 \u2014 too degraded for enhancement. A higher-resolution source is required."),
                ]
            },
            {
                "num": 4,
                "name": "Font Embedding",
                "what": "When fonts are not embedded in a PDF, the printer\u2019s system substitutes them with default fonts, causing text to look completely different \u2014 wrong spacing, wrong style, wrong weight. Embedding ensures your exact typefaces travel with the file.",
                "looks_for": "Scans the PDF\u2019s font resources to verify every font used is fully embedded (not just referenced by name). Checks for subset embedding vs full embedding, and flags any fonts that rely on system availability.",
                "autofix": "For PDFs processed through the Ghostscript pipeline, fonts are re-embedded during the CMYK conversion step. During final press compilation, the engine additionally <b>outlines all fonts to vector paths</b> \u2014 converting every glyph into resolution-independent curves. This eliminates all font dependency issues at the RIP stage and ensures the printed text is pixel-identical to the design, regardless of what fonts are installed on the press operator\u2019s system. Office DOCX/PPTX routes surface embedding state as telemetry until a vector PDF export is supplied.",
                "outcomes": [
                    ("\u2705 Passed", "All fonts are properly embedded."),
                    ("\u2728 Auto-Fixed", "Fonts were re-embedded during processing."),
                    ("\u26a0\ufe0f Failed", "Some fonts could not be embedded \u2014 re-export PDF with embedded fonts enabled."),
                ]
            },
            {
                "num": 5,
                "name": "Image Embedding",
                "what": "Design files sometimes reference external images by file path rather than embedding the actual pixel data. When sent to a printer, these linked images are missing, leaving blank spaces or low-resolution placeholders.",
                "looks_for": "Checks whether all images in the document are stored as embedded raster data within the file itself, rather than as external file references. Applies primarily to PDF and Office formats.",
                "autofix": "During the processing pipeline, all referenced images are resolved and embedded into the output file. The rasterisation step for complex PDFs inherently embeds all visual content.",
                "outcomes": [
                    ("\u2705 Passed", "All images are embedded in the file."),
                    ("\u2728 Auto-Fixed", "External image references were resolved and embedded."),
                    ("\u26a0\ufe0f Failed", "Referenced images could not be found or embedded."),
                ]
            },
            {
                "num": 6,
                "name": "Transparency, Lens & Drop Shadow Flattening",
                "what": "Transparency effects \u2014 including drop shadows, outer/inner glow, lens effects, feathered edges, and semi-transparent overlays \u2014 are the most common cause of print failures. Litho presses and many RIPs (Raster Image Processors) cannot process live transparency, producing dark boxes, missing elements, or colour shifts. This check uses a multi-layered detection and intelligent selective flattening system to handle every type of transparency.",
                "looks_for": "The engine applies three layers of transparency detection:\n\n"
                    "<b>Layer 1 \u2014 Object-Level Scan:</b> Uses PyMuPDF to inspect every page for /Group with /Transparency dictionaries and every embedded image for /SMask (soft mask) or /Mask entries. These are the PDF structures that encode drop shadows, lens effects, feathered edges, and alpha blending.\n\n"
                    "<b>Layer 2 \u2014 Raw Byte Analysis:</b> Scans the first 50,000 bytes of the PDF for transparency markers: /ca (fill opacity), /CA (stroke opacity), /SMask, /BM / (blending mode), /Group, and /Type /Group. This catches transparency that may not be visible in the page tree.\n\n"
                    "<b>Layer 3 \u2014 Blending Mode Detection:</b> Specifically identifies /BM /Multiply, /BM /Screen, and /BM /Overlay \u2014 the blending modes most commonly used in lens effects, shadow overlays, and gradient blends that cause the worst print failures.\n\n"
                    "Once detected, four distinct flattening pipelines are available:\n\n"
                    "<b>Pipeline 1 \u2014 Emergency Raster (Full Page):</b> Triggers when transparency is combined with RGB colourspace, OR when 3 or more SMask layers are detected. Bypasses Ghostscript entirely and rasterises the entire PDF to a 300 DPI bitmap via PyMuPDF. This is the nuclear option for files with heavy lens effects, multiple overlapping drop shadows, or complex alpha compositing.\n\n"
                    "<b>Pipeline 2 \u2014 Selective Page Flattening:</b> When a document has mixed complexity (some pages with drop shadows, others without), only pages exceeding 500 vector paths are rasterised to 300 DPI bitmaps. Simple pages keep their vectors for maximum quality.\n\n"
                    "<b>Pipeline 3 \u2014 Complexity Pre-Flatten:</b> When total vector paths across all pages exceed 500 (common in files with hundreds of small shadow elements), the entire document is pre-flattened to bitmaps before bleed processing.\n\n"
                    "<b>Pipeline 4 \u2014 Ghostscript PDF 1.3 Baking:</b> For moderately transparent PDFs, transparency is flattened during CMYK conversion by targeting PDF 1.3 compatibility (-dCompatibilityLevel=1.3). Since PDF 1.3 does not support live transparency, Ghostscript bakes all lenses, shadows, and alpha effects into the background composited with the artwork.\n\n"
                    "<b>Adaptive DPI Scaling:</b> To prevent memory crashes on large files:\n"
                    "\u2022 Standard files: 300 DPI (default)\n"
                    "\u2022 Medium files (>5 pages or >8MB): 250 DPI\n"
                    "\u2022 Large files (>10 pages or >15MB): 200 DPI\n"
                    "\u2022 Pixel limit: 200,000,000 pixels maximum per page \u2014 DPI is automatically reduced (minimum 150) if exceeded\n\n"
                    "<b>Memory Management:</b> After every rasterisation step, gc.collect() and malloc_trim(0) are called to force the Linux kernel to reclaim fragmented heap memory, preventing out-of-memory crashes during multi-page processing.",
                "autofix": "Fully automatic. The engine selects the appropriate pipeline based on the type and complexity of transparency detected. Drop shadows, lens effects, outer glow, inner glow, feathered edges, and all alpha-blended elements are baked into flat composite bitmaps at the highest DPI the system can safely handle. Only the elements or pages that need flattening are processed \u2014 simple vector content is preserved wherever possible.",
                "outcomes": [
                    ("\u2705 Passed", "No problematic transparency, lenses, or drop shadows found."),
                    ("\u2728 Auto-Fixed", "Transparency/lenses/shadows were selectively flattened to press-safe bitmaps."),
                    ("\u2728 Emergency Raster", "Severe transparency required full-page rasterisation at 300 DPI."),
                ]
            },
            {
                "num": "6b",
                "name": "Mockup Auto-Crop (Bounding Box Detection)",
                "what": "Artwork files often contain mockup borders, presentation frames, extra white space, or instructional text outside the actual flyer canvas. These elements must be removed before bleed extension, or the bleed engine will extend the border instead of the artwork content.",
                "looks_for": "Uses contour detection and area-ratio analysis to identify the largest rectangular content region within the image. If the detected content bounding box occupies between 40% and 95% of the total image area, it is classified as a mockup border. The crop is applied to isolate the actual artwork before any further processing.",
                "autofix": "Fully automatic. The detected bounding box is cropped out, and all subsequent processing (scaling, bleed extension, CMYK conversion) operates on the clean artwork. The original file is preserved for comparison. If no mockup border is detected, the image is processed as-is.",
                "outcomes": [
                    ("\u2705 Not Needed", "No mockup border detected \u2014 artwork fills the canvas."),
                    ("\u2728 Auto-Cropped", "Mockup border removed \u2014 artwork extracted from presentation frame."),
                ]
            },
            {
                "num": "6c",
                "name": "Interactive Trim Lock (Crop Coordinates)",
                "what": "When auto-crop cannot lock the correct bounding box, the upload wizard exposes an interactive trim lock so you can snap the exact crop region before automation runs. Coordinates feed the same cover-scale pipeline as mockup radar.",
                "looks_for": "User-defined crop coordinates (X, Y, Width, Height) set via the interactive crop tool in the upload wizard. The crop tool features:\n\n"
                    "\u2022 <b>Aspect-ratio locked region</b> matching the selected target print size, ensuring the cropped area has the exact proportions needed.\n"
                    "\u2022 <b>Independent edge handles</b> \u2014 corner handles (28px threshold) are detected first, then edge handles (20px threshold) for single-side adjustments. Dragging an edge moves only that edge without affecting the opposite side.\n"
                    "\u2022 <b>Pan / Hand tool</b> \u2014 toggle the Hand icon in the zoom toolbar to switch between crop and pan mode. In pan mode, click-and-drag scrolls the artwork in the overflow container without altering crop coordinates. Touch support included.\n"
                    "\u2022 <b>CSS-only visual zoom</b> (0.5\u00d7 to 3.0\u00d7, step 1.5mm) \u2014 zooming changes the on-screen magnification only; it never modifies the crop rectangle or pixel data.\n"
                    "\u2022 <b>Select All</b> button \u2014 snaps the crop region to the full canvas pixel dimensions (canvas.width \u00d7 canvas.height) for exact coverage.\n\n"
                    "Coordinates are stored as normalised percentages (0.0\u20131.0) and scaled to pixel values during processing.",
                "autofix": "Live hand-off: crop coordinates from the wizard are applied first, then scaling, centring, and bleed extension. The locked region uses <b>scale-fill (cover)</b>: scale_factor = max(target_w_px / crop_w, target_h_px / crop_h), then overflow is centre-cropped to the exact target dimensions so the plate stays full-bleed without anamorphic stretch. Interactive trim lock overrides auto-crop and disables PDF text sandwich compositing to prevent raster/text drift.",
                "outcomes": [
                    ("\u2705 Not Needed", "No interactive trim lock \u2014 auto-crop or full image used."),
                    ("\u2728 Applied", "Trim lock coordinates applied \u2014 artwork extracted from the guided region."),
                ]
            },
            {
                "num": "6d",
                "name": "Scale & Centre to Target Size",
                "what": "Artwork pixel dimensions rarely match the exact target print size. The System Intelligence rule is <b>zero anamorphic distortion</b>: we never squish pixels on an independent X/Y scale. This check uses proportional <b>cover</b> scaling (same factor both axes) to fill the chosen print canvas at 300 DPI, then centre-crops overflow so the trim bitmap stays on a <b>1:1 pixel fidelity</b> grid before bleed runs.",
                "looks_for": "Compares the artwork\u2019s pixel dimensions against the target print size (set during upload). Calculates the effective DPI at the target size and determines if scaling is needed. Uses <b>scale-fill (cover)</b>: scale_factor = max(target_w_px / artwork_w, target_h_px / artwork_h) \u2014 the artwork is enlarged enough to completely cover the target canvas. Any overflow beyond the target dimensions is then centre-cropped (equal trim from both sides of the longer axis) to produce an exact pixel match. This is applied in both image and PDF processing paths.",
                "autofix": "Fully automatic. Runs after any crop step and before bleed extension. The scaling uses direction-aware interpolation: INTER_CUBIC for upscale, INTER_AREA for downscale (never INTER_LANCZOS4). After scaling and centre-cropping, the artwork exactly matches the target canvas, then 5mm bleed is applied to all sides. Reported as a \u2018Scale & Centre to Target\u2019 audit check with original and target dimensions.",
                "outcomes": [
                    ("\u2705 Passed", "Artwork already matches the target print size."),
                    ("\u2728 Auto-Fixed", "Artwork scaled and centred to fit the target print canvas."),
                ]
            },
            {
                "num": "6e",
                "name": "Bleed Perimeter Scan",
                "what": "After bleed extension, this check verifies that the bleed zone actually contains continuous artwork content \u2014 not blank white space or artefacts. A trim box is calculated from the centre of the image outward, and the perimeter outside the trim edges is scanned for content.",
                "looks_for": "Defines a centre-anchored trim box based on the target print dimensions. Scans a 5mm strip outside each trim edge, measuring mean luminance, standard deviation, and the ratio of non-white pixels. Content is considered present when the non-white ratio exceeds a minimum threshold.",
                "autofix": "Diagnostic only. If the perimeter scan fails (empty bleed zones), the bleed extension check is flagged, and a reprocess with a different bleed strategy may be recommended.",
                "outcomes": [
                    ("\u2705 Passed", "Bleed zone contains continuous content on all sides."),
                    ("\u26a0\ufe0f Warning", "Bleed zone appears empty or inconsistent \u2014 review bleed strategy."),
                ]
            },
            {
                "num": "6f",
                "name": "Small Text Colour Safety",
                "what": "Small text (below approximately 8pt) printed in multi-channel CMYK (e.g. rich black C40/M30/Y30/K100) can suffer from registration misalignment, making text appear blurry or doubled. This check identifies small text elements that should be printed in single-channel K-only black for sharpness.",
                "looks_for": "Analyses text blocks in PDF content streams, checking font size against a threshold. Text below the threshold is flagged if it uses multi-channel CMYK colour rather than pure K-only black.",
                "autofix": "Handled automatically by the K-Only Neutralisation engine (Check 2b). Small text identified as neutral is converted to K-only black with overprint enabled for maximum sharpness on press.",
                "outcomes": [
                    ("\u2705 Passed", "All small text uses press-safe single-channel colour."),
                    ("\u2728 Auto-Fixed", "Small text converted from rich black to K-only for sharp registration."),
                    ("\u26a0\ufe0f Warning", "Small text uses intentional multi-colour (not neutral) \u2014 may appear soft on press."),
                ]
            },
        ]
    },
    {
        "section": "LAYOUT & SAFETY",
        "color": BRAND_CYAN,
        "checks": [
            {
                "num": 7,
                "name": "Shrink & Re-Bleed Orchestrator (Safe Zone)",
                "what": "Litho trim tolerates guillotine drift; this channel is the live coupling between <b>validate_safe_zone()</b> telemetry and <b>auto_resolve_safe_zone()</b> geometry. Anything that energises the inner <b>"
                    f"{ENGINE_SAFE_ZONE_SPEC['safe_zone_mm']} mm</b> edge strips (computed in px from runtime DPI) is classified before bleed so typography is not surprised at cut time.",
                "looks_for": "<b>validate_safe_zone()</b> crops the trim BGR plane, derives <b>safe_px = mm \u2192 px</b> from "
                    f"{ENGINE_SAFE_ZONE_SPEC['safe_zone_mm']} mm, slices four edge windows, builds a median-adaptive binary mask, runs <b>MORPH_OPEN</b> with a 3×3 rect kernel, and measures closest foreground distance per side. "
                    "Warnings include side, distance_mm, has_text_logo, and right-edge text escalates to CRITICAL. "
                    "<b>Canny radar:</b> parallel <b>30 px</b> outer bands score high-frequency edge density so logo and body-copy energy is visible before RIP.",
                "autofix": "<b>auto_resolve_safe_zone()</b> clones the working plane with <b>img_bgr.copy()</b>. When violations fire, metadata sets shrinkApplied, pixelsRemovedPerSide=30, "
                    f"bleedPxEffective = target_bleed_px + {ENGINE_SAFE_ZONE_SPEC['bleed_boost_px_on_violation']}, then <b>cv2.resize</b> to "
                    f"(W\u2212{ENGINE_SAFE_ZONE_SPEC['shrink_total_per_axis_px']})×(H\u2212{ENGINE_SAFE_ZONE_SPEC['shrink_total_per_axis_px']}) using <b>{ENGINE_SAFE_ZONE_SPEC['resize']}</b>. "
                    "Non-violation paths keep the original canvas size and base bleed px. Strategy <b>auto</b> routes through add_clean_bleed; named strategies invoke _apply_forced_strategy_bleed(), which again extends from per-edge working copies (including Pixel-Drift with 1 px sampling).",
                "outcomes": [
                    ("\u2705 Passed", "Foreground clears the engine safe strips."),
                    ("\u2728 Auto-Healed", "Shrink &amp; Re-Bleed executed \u2014 INTER_AREA shrink + elevated bleed px + strategy melt."),
                    ("\u26a0\ufe0f Attention", "Caution band still lit on proof \u2014 console shows distance_mm telemetry."),
                    ("\u274c Critical", "Right-edge text or logo inside critical strip \u2014 guillotine shear risk logged."),
                ]
            },
            {
                "num": 8,
                "name": "Layout Balance Analysis",
                "what": "A well-balanced layout has its visual weight distributed evenly. Off-centre or lopsided designs can look unprofessional and may indicate content is positioned poorly relative to the trim area.",
                "looks_for": "Groups visual elements into layout blocks using contour analysis, then calculates the weighted centre of all blocks compared to the geometric centre. An imbalance is flagged when the weighted centre is more than 3mm from the geometric centre.",
                "autofix": "Live telemetry: emits weighted-centre offset vs geometric centre (mm). No silent bitmap rescale\u2014Auto-Heal remains the sanctioned bleed-time path in auto_resolve_safe_zone().",
                "outcomes": [
                    ("\u2705 Passed", "Visual weight is centred within 3mm tolerance."),
                    ("\u26a0\ufe0f Warning", "Layout is off-centre by more than 3mm."),
                ]
            },
            {
                "num": 9,
                "name": "Composition Centre Analysis",
                "what": "Goes deeper than layout balance by analysing the visual centroid \u2014 the point where the \u2018visual gravity\u2019 of the entire design converges. Significant elements like logos and headings are given extra weight (1.3\u20131.5x) in the calculation.",
                "looks_for": "Calculates a weighted visual centroid considering element size, contrast, and position. Compares this to the geometric centre. Flags deviation exceeding 10mm in any direction as requiring review.",
                "autofix": "Live telemetry: publishes visual centroid drift vectors for the dashboard. Pixels stay frozen unless Shrink &amp; Re-Bleed fires downstream.",
                "outcomes": [
                    ("\u2705 Passed", "Visual centroid is within 10mm of centre."),
                    ("\u26a0\ufe0f Warning", "Visual weight is significantly off-centre \u2014 consider adjusting."),
                ]
            },
            {
                "num": 10,
                "name": "Smart Downscale Radar (Signal Stack)",
                "what": "Historically labelled &ldquo;Smart Downscale,&rdquo; this channel is now <b>radar-only</b>. apply_downscale_if_needed() computes hypothetical safe-zone scale factors but <b>never mutates</b> the BGR buffer\u2014production policy forbids silent litho rescale.",
                "looks_for": "When Safe Zone CRITICAL stacks with Layout Balance warnings, stderr logs <b>[RADAR][SmartDownscale]</b> with the hypothetical scale cap while returning <b>applied=False, scale_factor=1.0</b>. The trim bitmap stays 1:1 until Shrink &amp; Re-Bleed runs inside auto_resolve_safe_zone().",
                "autofix": "Telemetry-only: surfaces overcrowding pressure on the console. Auto-Heal shrink + elevated bleed px is the only automated geometry move tied to this stack.",
                "outcomes": [
                    ("\u2705 Passed", "Layout stack stable \u2014 radar silent."),
                    ("\u2139\ufe0f Signal", "Compound pattern logged \u2014 rely on Shrink &amp; Re-Bleed auto-heal at bleed time."),
                ]
            },
            {
                "num": 11,
                "name": "Margin Normalisation",
                "what": "Uneven margins make printed materials look misaligned or poorly designed. This check ensures opposing margins (left/right and top/bottom) are symmetrical, which is especially important for double-sided printing and collated documents.",
                "looks_for": "Compares the safe zone distance on each pair of opposing sides. Flags asymmetry when the difference between left/right or top/bottom margins exceeds 3mm.",
                "autofix": "Live telemetry: streams opposing margin deltas (mm) to the intelligence console. No automatic geometric squash.",
                "outcomes": [
                    ("\u2705 Passed", "Opposing margins are within 3mm of each other."),
                    ("\u26a0\ufe0f Warning", "Margins are uneven by more than 3mm \u2014 content may appear off-centre when printed."),
                ]
            },
            {
                "num": 12,
                "name": "Trim Tolerance Simulation",
                "what": "Guillotine cutting has a mechanical tolerance of \u00b11\u20132mm. This simulation shows what your artwork would look like if the cut drifts slightly in any direction, helping identify content that is technically inside the safe zone but dangerously close.",
                "looks_for": "Simulates cutting at +1mm and -1mm offset from the intended trim line on all sides. Checks whether any critical content would be affected by this drift.",
                "autofix": "When the tolerance engine is enabled, applies the same safe-zone clearance pass; otherwise emits trim-drift simulation only as structured telemetry.",
                "outcomes": [
                    ("\u2705 Passed", "Content survives \u00b12mm trim variation."),
                    ("\u2728 Auto-Fixed", "Content was scaled to accommodate trim tolerance."),
                    ("\u26a0\ufe0f Warning", "Content may be partially cut if trim drifts."),
                ]
            },
        ]
    },
    {
        "section": "BOOKLET & MULTI-PAGE",
        "color": AMBER,
        "checks": [
            {
                "num": 13,
                "name": "Spine Shift Detection",
                "what": "In saddle-stitched or perfect-bound booklets, content placed too close to the binding spine gets hidden in the fold. This check analyses multi-page documents to detect content that may be obscured by the binding.",
                "looks_for": "Defines a 10mm \u2018spine zone\u2019 at the binding edge of documents with 4 or more pages. Measures the ratio of foreground content within this zone \u2014 flags a warning if more than 15% of content falls within it.",
                "autofix": "Live telemetry: binds spine-zone occupancy ratios per page into the audit stream for booklet imposition.",
                "outcomes": [
                    ("\u2705 Passed", "No significant content in the spine zone."),
                    ("\u26a0\ufe0f Warning", "Content detected near the spine \u2014 may be hidden by the binding."),
                ]
            },
            {
                "num": 14,
                "name": "Creep Compensation",
                "what": "In multi-page booklets, inner pages \u2018creep\u2019 outward when folded due to paper thickness. Without compensation, inner page content shifts toward the outer edge, causing misalignment between pages.",
                "looks_for": "Calculates the exact outward shift needed based on page position relative to the centre fold and paper thickness (default 0.1mm per sheet). The maximum creep shift is capped at 3.0mm.",
                "autofix": "Calculates and reports the precise creep shift in millimetres for each page position. The printer can apply these values during imposition.",
                "outcomes": [
                    ("\u2705 Passed", "Single-page or no significant creep detected."),
                    ("\u2139\ufe0f Info", "Creep values calculated and provided for the printer."),
                ]
            },
            {
                "num": 15,
                "name": "Gutter Collision Detection",
                "what": "The gutter is the inner margin where pages meet at the binding. Objects that extend into the gutter may be partially obscured or cause visual artifacts at the fold.",
                "looks_for": "Scans an 8mm gutter zone on the binding edge for significant objects (larger than 2mm). Counts the number of \u2018colliding\u2019 objects that would be affected by the binding.",
                "autofix": "Live telemetry: counts gutter collisions and pushes counts to the intelligence console for saddle-stitch planning.",
                "outcomes": [
                    ("\u2705 Passed", "No significant objects in the gutter zone."),
                    ("\u26a0\ufe0f Warning", "Objects detected in gutter \u2014 may be obscured by binding."),
                ]
            },
        ]
    },
    {
        "section": "TECHNICAL COMPLIANCE",
        "color": HexColor("#7c3aed"),
        "checks": [
            {
                "num": 17,
                "name": "QR Code Integrity",
                "what": "QR codes on printed materials must be scannable under real-world conditions. A QR code printed in multi-channel CMYK can suffer registration misalignment, causing the dark modules to blur and become unscannable. Additionally, QR codes require a clear quiet zone (white border) around them to be reliably detected by scanners.",
                "looks_for": "The engine renders each page at 300 DPI and scans for QR codes using a dual-detection pipeline:\n\n"
                    "\u2022 <b>pyzbar (ZBar library):</b> Primary detector \u2014 decodes QR symbols and verifies they return valid data.\n"
                    "\u2022 <b>OpenCV QRCodeDetector:</b> Fallback detector when pyzbar is unavailable.\n\n"
                    "For each detected QR code, the engine:\n"
                    "1. Attempts to decode the QR data to verify it is scannable.\n"
                    "2. Measures the quiet zone (clear border) around the QR bounding box.\n"
                    "3. Checks whether the dark modules use multi-channel CMYK colour (which risks registration blur on press).",
                "autofix": "Fully automatic. Two corrections are applied when QR codes are detected:\n\n"
                    "\u2022 <b>K-Only Black Enforcement:</b> Dark QR modules are forced to 100% K-only black (0C/0M/0Y/100K). This eliminates registration risk by printing the QR in a single ink channel, ensuring sharp, clean module edges.\n"
                    "\u2022 <b>Quiet Zone Enforcement (2mm):</b> A minimum 2mm white quiet zone is enforced around each QR code bounding box. If the existing quiet zone is insufficient, the surrounding area is cleared to white to ensure reliable scanner detection.\n\n"
                    "Applied during the final press compilation step on the CMYK-converted PDF, after all colour space conversions are complete. Both PDF and image processing paths include QR scanning.",
                "outcomes": [
                    ("\u2705 Passed", "QR code(s) detected and verified scannable \u2014 no corrections needed."),
                    ("\u2728 Auto-Fixed", "QR code(s) detected and optimised: dark modules forced to K-only black, 2mm quiet zone enforced."),
                    ("\u26a0\ufe0f Warning", "QR code(s) detected but could not be verified as scannable \u2014 check contrast and damage."),
                    ("\u2139\ufe0f None Found", "No QR codes detected in artwork."),
                ]
            },
            {
                "num": "2h",
                "name": "Hairline Stroke Enforcement",
                "what": "Hairlines are strokes that are too thin to be reliably reproduced on a litho press. While they may appear correctly on screen, they often break, disappear, or become inconsistent during plate-making or printing. This check ensures all line strokes meet the minimum printable thickness.",
                "looks_for": "Scans all PDF content streams for the stroke weight operator (w in PDF syntax). Identifies any stroke where the weight is less than 0.25pt (0.088mm), including zero-width strokes that rely on device-specific minimums.",
                "autofix": "Automatically rewrites all stroke weights below the 0.25pt threshold to a safe minimum of 0.3pt. Only the weight is modified \u2014 stroke colour is never altered. Applies across all colour spaces (CMYK, RGB, Grayscale) to ensure visibility across the entire document.",
                "outcomes": [
                    ("\u2705 Passed", "All strokes meet minimum weight requirements. No hairlines detected."),
                    ("\u2728 Auto-Fixed", "Hairline strokes detected (below 0.25pt) and bulked to 0.3pt for press stability."),
                ]
            },
            {
                "num": 16,
                "name": "PDF/X Compliance & White-Edge Risk",
                "what": "PDF/X is the international standard for print-ready PDF files. It mandates specific technical requirements including embedded fonts, correct colour spaces, and defined trim areas. The White-Edge Risk check additionally verifies that dark or coloured backgrounds have sufficient bleed to prevent white edges after cutting.",
                "looks_for": "PDF/X: Checks for TrimBox definitions, embedded fonts, absence of live transparency, absence of spot colours, and correct metadata. White-Edge Risk: Analyses edge regions for dark backgrounds (<128 brightness) with less than 2mm bleed, which would reveal white paper after cutting.",
                "autofix": "PDF/X auto-fixes missing TrimBox definitions and metadata titles. White-Edge Risk surfaces as structured telemetry when dark-background bleed is thin\u2014Smart Bleed channels pick up from there.",
                "outcomes": [
                    ("\u2705 Passed", "File meets PDF/X requirements and has no white-edge risk."),
                    ("\u2728 Auto-Fixed", "TrimBox and/or metadata were added to meet PDF/X standards."),
                    ("\u26a0\ufe0f Warning", "White-edge risk detected \u2014 dark background bleed may be insufficient."),
                    ("\u274c Failed", "Missing embedded fonts, live transparency, or other PDF/X violations."),
                ]
            },
        ]
    },
]


def build_guide_reportlab(output_path: str, context=None):
    context = context or {}
    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=MARGIN,
        title="Flyerz.co.za Artwork Intelligence \u2014 System Intelligence Guide",
        author="Flyerz.co.za",
    )

    story = []
    usable_w = PAGE_W - 2 * MARGIN

    dc = DASHBOARD_RULES_COPY
    story.append(Spacer(1, 6))
    story.append(Paragraph("FLYERZ.CO.ZA", sBrand))
    story.append(Paragraph(dc["panel_tag"].upper(), ParagraphStyle(
        "DashTag", parent=sBrand, fontSize=9, textColor=BRAND_CYAN, spaceAfter=4)))
    story.append(Paragraph(dc["panel_title"], ParagraphStyle(
        "DashTitle", parent=sTitle, fontSize=17, leading=21, textColor=TEXT_PRIMARY, spaceAfter=6)))
    story.append(Paragraph(dc["panel_intro"], sIntro))
    story.append(Spacer(1, 6))

    hr_data = [["", "", ""]]
    hr_table = Table(hr_data, colWidths=[usable_w * 0.3, usable_w * 0.4, usable_w * 0.3])
    hr_table.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (0, 0), 1.5, BRAND_BLUE),
        ("LINEBELOW", (1, 0), (1, 0), 1.5, BRAND_CYAN),
        ("LINEBELOW", (2, 0), (2, 0), 1.5, BRAND_RED),
    ]))
    story.append(hr_table)
    story.append(Spacer(1, 10))

    story.append(Paragraph(
        "<b>Legend</b> &mdash; Passed: channel clear · Auto-Heal / Auto-fixed: automation wrote a safe state · "
        "<b>System Radar</b>: edge and safe-zone signals (no silent rescale) · Attention: review gate · "
        "Failed: operator gate.",
        ParagraphStyle("LegendDash", parent=sBody, fontSize=8.5, textColor=TEXT_MUTED, leading=12)
    ))
    story.append(Spacer(1, 10))
    story.append(PageBreak())
    story.extend(build_system_intelligence_phase_story(usable_w))
    story.append(Spacer(1, 8))
    story.append(ShrinkBleedDiagramFlowable(usable_w))
    story.append(PageBreak())

    flat_checks = _flatten_checks_for_template()
    phase_colors = {
        1: HexColor("#4ade80"),
        2: HexColor("#38bdf8"),
        3: HexColor("#00cc88"),
    }
    phase_titles = {
        1: "Phase 1 — Non-Destructive Core · intelligence channels",
        2: "Phase 2 — Safe Zone Intelligence · intelligence channels",
        3: "Phase 3 — Smart Bleed Engine · intelligence channels",
    }

    for phase_num in (1, 2, 3):
        story.append(Paragraph(phase_titles[phase_num], sSectionHead))
        story.append(Spacer(1, 4))
        accent = phase_colors[phase_num]
        for check in flat_checks:
            if check["phase"] != phase_num:
                continue
            check_elements = []

            num_val = check["num"]
            num_label = str(num_val)
            num_para = Paragraph(f"<b>{num_label}</b>", sCheckNum)
            num_table = Table([[num_para]], colWidths=[22], rowHeights=[18])
            num_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (0, 0), accent),
                ("ALIGN", (0, 0), (0, 0), "CENTER"),
                ("VALIGN", (0, 0), (0, 0), "MIDDLE"),
                ("ROUNDEDCORNERS", [3, 3, 3, 3]),
                ("TOPPADDING", (0, 0), (0, 0), 2),
                ("BOTTOMPADDING", (0, 0), (0, 0), 2),
            ]))

            phase_chip = Paragraph(
                f"<font size=\"7\" color=\"#64748b\">Phase {phase_num}</font>",
                ParagraphStyle("PhChip", parent=sBody, fontSize=7, textColor=TEXT_MUTED),
            )
            name_para = Paragraph(f"<b>{check['name']}</b>", sCheckName)
            inner_head = Table([[num_table, name_para]], colWidths=[28, usable_w - 46])
            icn = BrandIconFlowable(icon_for_check(num_val), size=13)
            header_row = Table([[icn, inner_head]], colWidths=[18, usable_w - 18])
            header_row.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (0, 0), 0),
                ("LEFTPADDING", (1, 0), (1, 0), 4),
            ]))
            check_elements.append(header_row)
            check_elements.append(phase_chip)
            check_elements.append(Spacer(1, 4))

            check_elements.append(Paragraph("<b>What This Channel Automates</b>", sLabel))
            check_elements.append(Paragraph(check["what"], sBody))
            check_elements.append(Spacer(1, 3))

            check_elements.append(Paragraph("<b>System Radar</b>", sLabel))
            check_elements.append(Paragraph(check["looks_for"], sBody))
            check_elements.append(Spacer(1, 3))

            check_elements.append(Paragraph("<b>Auto-Heal</b>", sLabel))
            check_elements.append(Paragraph(check["autofix"], sBody))
            check_elements.append(Spacer(1, 3))

            check_elements.append(Paragraph("<b>States</b>", sLabel))
            for icon, desc in check["outcomes"]:
                check_elements.append(Paragraph(
                    f"<b>{icon}</b>&nbsp;&nbsp;{desc}", sOutcome
                ))
                check_elements.append(Spacer(1, 1))

            check_elements.append(Spacer(1, 2))

            card_content = []
            for elem in check_elements:
                card_content.append([elem])

            card_table = Table(card_content, colWidths=[usable_w - 16])
            card_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), LIGHT_BG),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (0, 0), 8),
                ("BOTTOMPADDING", (-1, -1), (-1, -1), 8),
                ("BOX", (0, 0), (-1, -1), 0.5, CARD_BORDER),
                ("ROUNDEDCORNERS", [4, 4, 4, 4]),
            ]))

            outer = Table([[card_table]], colWidths=[usable_w])
            outer.setStyle(TableStyle([
                ("LEFTPADDING", (0, 0), (0, 0), 0),
                ("RIGHTPADDING", (0, 0), (0, 0), 0),
                ("TOPPADDING", (0, 0), (0, 0), 0),
                ("BOTTOMPADDING", (0, 0), (0, 0), 0),
            ]))

            card_table.splitFirst = True
            card_table.splitInRow = True
            outer.splitFirst = True
            outer.splitInRow = True
            story.append(outer)
            story.append(Spacer(1, 6))

    story.append(HRFlowable(width="60%", thickness=1, color=CARD_BORDER,
                             spaceAfter=10, spaceBefore=6))

    story.append(Paragraph(
        "Every intelligence channel runs automatically. Your live Artwork Intelligence Report surfaces "
        "pass states, <b>System Radar</b> signals, <b>Auto-Heal</b> shrink/bleed corrections, and review gates "
        "&mdash; litho-grade automation, not spreadsheet theatre.",
        ParagraphStyle("Closing", parent=sIntro, fontSize=9.5, textColor=MUTED)
    ))
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        f"\u00a9 {datetime.now().year} Flyerz.co.za Artwork Intelligence. All rights reserved.",
        sFooter
    ))
    story.append(Paragraph(
        f"Generated {datetime.now().strftime('%d %B %Y')}",
        sFooter
    ))
    if context.get("jobId"):
        story.append(Paragraph(
            f"Personalized intelligence pack — Job #{context.get('jobId')} &mdash; {context.get('filename', '')}",
            sFooter
        ))

    doc.build(story, onFirstPage=_paint_dark_page, onLaterPages=_paint_dark_page)
    return {"success": True, "path": output_path, "engine": "reportlab"}


INTELLIGENCE_PHASE_BY_NUM = {
    "3": 1, "3b": 1, "4": 1, "5": 1, "6b": 1, "6c": 1, "6d": 1, "16": 1, "2h": 1,
    "7": 2, "8": 2, "9": 2, "10": 2, "11": 2, "12": 2, "6f": 2,
    "1": 3, "2": 3, "2b": 3, "6": 3, "6e": 3, "13": 3, "14": 3, "15": 3, "17": 3,
}


def _live_voice(text: str) -> str:
    if not text:
        return text
    return (
        text.replace("This check ", "This intelligence channel ")
        .replace("This check\u2019s ", "This channel\u2019s ")
        .replace("This check's ", "This channel's ")
        .replace(" manual ", " live-session ")
        .replace("Manual ", "Live-session ")
        .replace("Advisory only", "Telemetry channel")
        .replace("advisory only", "telemetry channel")
        .replace("Advisory ", "Telemetry ")
        .replace(" advisory", " telemetry")
        .replace("Manual Fixing", "System Radar")
        .replace("manual fixing", "System Radar")
        .replace("Manual fixing", "System Radar")
    )


def _flatten_checks_for_template():
    rows = []
    for section in CHECKS:
        for ch in section["checks"]:
            n = ch["num"]
            key = str(n)
            phase = INTELLIGENCE_PHASE_BY_NUM.get(str(n), 3)
            rows.append({
                "phase": phase,
                "phase_name": ("Phase 1 — Non-Destructive Core" if phase == 1
                               else "Phase 2 — Safe Zone Intelligence" if phase == 2
                               else "Phase 3 — Smart Bleed Engine"),
                "num": key,
                "name": ch["name"],
                "what": _live_voice(ch["what"]),
                "looks_for": _live_voice(ch["looks_for"]),
                "autofix": _live_voice(ch["autofix"]),
                "outcomes": ch["outcomes"],
                "legacy_section": section["section"],
            })
    return rows


def _try_build_guide_html(output_path: str, template_path: str, context: dict) -> None:
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    from xhtml2pdf import pisa

    root = os.path.dirname(template_path)
    env = Environment(
        loader=FileSystemLoader(root),
        autoescape=select_autoescape(["html", "xml"]),
    )
    tpl = env.get_template(os.path.basename(template_path))

    html = tpl.render(
        checks=_flatten_checks_for_template(),
        job_id=context.get("jobId"),
        filename=context.get("filename", ""),
        generated_at=context.get("generatedAt") or datetime.now().strftime("%Y-%m-%d %H:%M UTC"),
        auto_heal=context.get("autoHealEvent"),
        engine=ENGINE_SAFE_ZONE_SPEC,
        dash=DASHBOARD_RULES_COPY,
    )
    with open(output_path, "wb") as out_f:
        status = pisa.CreatePDF(html, dest=out_f, encoding="utf-8")
    if status.err:
        raise RuntimeError(f"xhtml2pdf reported {status.err} errors")


def build_guide(output_path: str, context=None):
    context = context or {}
    template_path = os.path.join(os.path.dirname(__file__), "reports", "check_guide_template.html")
    if os.path.isfile(template_path):
        try:
            _try_build_guide_html(output_path, template_path, context)
            return {"success": True, "path": output_path, "engine": "xhtml2pdf"}
        except Exception as e:
            sys.stderr.write(f"[CHECKS-GUIDE] Template PDF failed ({e}); using ReportLab fallback.\n")
            import traceback
            traceback.print_exc(file=sys.stderr)
    build_guide_reportlab(output_path, context)
    return {"success": True, "path": output_path, "engine": "reportlab"}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"success": False, "error": "Usage: checks_guide.py <output_path> [context_json_path]"}))
        sys.exit(1)

    output_path = sys.argv[1]
    ctx = {}
    if len(sys.argv) > 2 and sys.argv[2]:
        try:
            with open(sys.argv[2], "r", encoding="utf-8-sig") as cf:
                ctx = json.load(cf)
        except Exception as e:
            print(json.dumps({"success": False, "error": f"Bad context JSON: {e}"}))
            sys.exit(1)
    try:
        result = build_guide(output_path, ctx)
        print(json.dumps(result))
    except Exception as e:
        import traceback
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({"success": False, "error": str(e)}))
        sys.exit(1)
