#!/usr/bin/env python3
"""
Flyerz.co.za Artwork Intelligence — Health Report Generator
Generates a human-friendly, branded PDF report using ReportLab.
Translates technical audit results into plain English.
"""

import sys
import json
import os
from datetime import datetime

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor, white, black
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Image as RLImage,
    Table, TableStyle, HRFlowable
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER

PAGE_W, PAGE_H = A4
MARGIN = 20 * mm

GREEN = HexColor("#16a34a")
GREEN_BG = HexColor("#f0fdf4")
BLUE = HexColor("#2563eb")
BLUE_BG = HexColor("#eff6ff")
RED = HexColor("#dc2626")
RED_BG = HexColor("#fef2f2")
DARK = HexColor("#1e293b")
MUTED = HexColor("#64748b")
BRAND = HexColor("#7c3aed")
BRAND_BG = HexColor("#f5f3ff")


def friendly_mapping(check):
    name = check.get("name", "")
    passed = check.get("passed", False)
    auto_fixed = check.get("autoFixed", False)
    msg = check.get("message", "")

    if "Bleed" in name:
        if auto_fixed:
            return {
                "icon": "sparkle",
                "title": "Edge Perfection",
                "body": "[FAI] We noticed your artwork was missing 'bleed' (the extra bit of colour for cutting). Don't worry \u2014 Flyerz.co.za Artwork Intelligence has added this for you so there are no white borders.",
                "type": "fixed"
            }
        elif passed:
            return {
                "icon": "check",
                "title": "Perfect Edges",
                "body": "Your artwork already had the correct bleed. Great job!",
                "type": "good"
            }
        else:
            return {
                "icon": "warn",
                "title": "Edge Issue",
                "body": "We couldn't add bleed to your artwork automatically. Please add 5mm bleed around your design.",
                "type": "issue"
            }

    if "Color" in name or "CMYK" in name:
        if passed:
            return {
                "icon": "check" if not auto_fixed else "sparkle",
                "title": "Colour Optimisation",
                "body": "[FAI] We've converted your colours to 'Print-Ready Mode' (CMYK). This ensures the colours you see on paper are as close as possible to what you intended.",
                "type": "fixed" if auto_fixed else "good"
            }
        else:
            return {
                "icon": "warn",
                "title": "Colour Attention Needed",
                "body": "Your colours may not print accurately. We recommend supplying a CMYK file from your design application.",
                "type": "issue"
            }

    if "Font" in name:
        if passed:
            return {
                "icon": "check" if not auto_fixed else "sparkle",
                "title": "Text Security",
                "body": "[FAI] We've locked your fonts into place. This means your text will look exactly the same on the final flyer as it does on your screen \u2014 no missing letters or font swaps!",
                "type": "fixed" if auto_fixed else "good"
            }
        else:
            return {
                "icon": "warn",
                "title": "Font Warning",
                "body": "Some fonts may not be embedded. Please embed all fonts or convert text to outlines in your design application.",
                "type": "issue"
            }

    if "Lens" in name or "Transparency" in name or "Transparenc" in name:
        if passed:
            return {
                "icon": "check" if not auto_fixed else "sparkle",
                "title": "Shadows & Effects",
                "body": "[FAI] We've flattened your shadows and transparencies to make sure they print perfectly without 'falling away' during the printing process.",
                "type": "fixed" if auto_fixed else "good"
            }
        else:
            return {
                "icon": "warn",
                "title": "Effects Warning",
                "body": "Some transparency effects may not print correctly. Please flatten transparencies in your design application.",
                "type": "issue"
            }

    if "DPI" in name or "Resolution" in name:
        if passed:
            return {
                "icon": "check" if not auto_fixed else "sparkle",
                "title": "Image Sharpness",
                "body": "Your artwork meets our minimum resolution requirements for sharp, crisp printing. No blurry images here!",
                "type": "fixed" if auto_fixed else "good"
            }
        else:
            return {
                "icon": "warn",
                "title": "Resolution Warning",
                "body": "Your image resolution is below our recommended minimum. The print may appear slightly blurry. We recommend 400 DPI for best results.",
                "type": "issue"
            }

    if "Margin" in name:
        if passed:
            return {
                "icon": "check",
                "title": "Safe Margins",
                "body": "Your important content is safely within the margins. Nothing will be accidentally cut off during trimming!",
                "type": "good"
            }
        else:
            return {
                "icon": "warn",
                "title": "Margin Warning",
                "body": "Some content may be too close to the edge and could be cut off. Keep important content at least 5mm from the edge.",
                "type": "issue"
            }

    if "Image" in name and "Embed" in name:
        if passed:
            return {
                "icon": "check" if not auto_fixed else "sparkle",
                "title": "Images Secured",
                "body": "All images in your artwork are properly embedded and ready for high-quality printing.",
                "type": "fixed" if auto_fixed else "good"
            }
        else:
            return {
                "icon": "warn",
                "title": "Image Issue",
                "body": "Some images may not be properly embedded. Please ensure all linked images are included.",
                "type": "issue"
            }

    if "QR" in name:
        if auto_fixed:
            return {
                "icon": "sparkle",
                "title": "QR Code Integrity",
                "body": "[FAI] We found QR code(s) in your design and optimised them for print — dark modules have been set to pure black ink and a clear quiet zone has been added around each code to ensure reliable scanning.",
                "type": "fixed"
            }
        elif passed:
            return {
                "icon": "check",
                "title": "QR Code Integrity",
                "body": "Your QR code(s) are sharp, correctly contrasted, and ready to scan perfectly off the press. No changes needed!",
                "type": "good"
            }
        else:
            return {
                "icon": "warn",
                "title": "QR Code Warning",
                "body": "We detected a QR code but couldn't verify it will scan reliably. Please check that your QR code is high-contrast and has enough clear space around it.",
                "type": "issue"
            }

    if "Visual Proof" in name:
        if passed:
            return {
                "icon": "check",
                "title": "Visual Proof Ready",
                "body": "A preview of your print-ready artwork has been generated for your review. Check it below!",
                "type": "good"
            }
        else:
            return {
                "icon": "warn",
                "title": "Preview Issue",
                "body": "We couldn't generate a visual preview. Please download and review the PDF manually.",
                "type": "issue"
            }

    if "Layout Balance" in name or "Layout Balancing" in name:
        if passed:
            return {
                "icon": "check",
                "title": "Layout Balance",
                "body": "Your design layout is well-balanced. All content blocks are evenly distributed across the page.",
                "type": "good"
            }
        else:
            return {
                "icon": "warn",
                "title": "Layout Balance",
                "body": "Your design appears slightly off-centre or unbalanced. Consider adjusting the positioning of your text and images for a more professional look.",
                "type": "issue"
            }

    if "Composition Center" in name or "Visual Center" in name:
        if passed:
            return {
                "icon": "check",
                "title": "Visual Composition",
                "body": "The visual weight of your design is nicely centred. Your flyer will look professionally composed.",
                "type": "good"
            }
        else:
            return {
                "icon": "warn",
                "title": "Visual Composition",
                "body": "The visual weight of your design is slightly off-centre. This may be intentional, but check that your layout looks balanced.",
                "type": "issue"
            }

    if "Margin Normalization" in name:
        if passed:
            return {
                "icon": "check",
                "title": "Even Margins",
                "body": "Your content margins are even on all sides. Everything will look symmetrical when printed.",
                "type": "good"
            }
        else:
            return {
                "icon": "warn",
                "title": "Uneven Margins",
                "body": "Your content has uneven margins — one side has more space than the other. Consider centring your content for a cleaner look.",
                "type": "issue"
            }

    if "Safe Zone" in name:
        if auto_fixed:
            return {
                "icon": "sparkle",
                "title": "Issues Resolved",
                "body": "[FAI] Safe zone violations were detected — content was too close to the trim edge. Flyerz.co.za Artwork Intelligence applied a 95% scale fix to pull all content safely within the danger zone, with edge replication filling the outer bleed area. No content will be cut off during trimming.",
                "type": "fixed"
            }
        elif passed:
            return {
                "icon": "check",
                "title": "Safe Zones Clear",
                "body": "All your important content is safely within the 5mm safe zone. Nothing will be accidentally cut off during trimming!",
                "type": "good"
            }
        else:
            return {
                "icon": "warn",
                "title": "Safe Zone Warning",
                "body": "Some content is too close to the trim edge and may be cut off. Keep important elements at least 5mm from the edge.",
                "type": "issue"
            }

    if "Tolerance" in name or "Trim Tolerance" in name:
        if auto_fixed:
            return {
                "icon": "sparkle",
                "title": "Issues Resolved",
                "body": "[FAI] Trim drift risk was detected. Flyerz.co.za Artwork Intelligence optimized the artwork so it remains safe even if the cutting machine drifts by 1mm in any direction.",
                "type": "fixed"
            }
        elif passed:
            return {
                "icon": "check",
                "title": "Trim Safety",
                "body": "Your design is safe even if the cutting machine drifts by 1mm in any direction. Nothing important will be cut off.",
                "type": "good"
            }
        else:
            return {
                "icon": "warn",
                "title": "Trim Risk",
                "body": "If the cutting machine drifts slightly (which is normal), some content near the edge may be cut off. Keep important elements at least 5mm from the edge.",
                "type": "issue"
            }

    if "White-Edge" in name or "White Edge" in name:
        if passed:
            return {
                "icon": "check",
                "title": "Edge Coverage",
                "body": "No risk of white edges showing after trimming. Your background extends properly into the bleed area.",
                "type": "good"
            }
        else:
            return {
                "icon": "warn",
                "title": "White Edge Warning",
                "body": "Your design has a dark background near the edges but not enough bleed. This means a thin white line could appear after cutting. Extend your background colour into the bleed area.",
                "type": "issue"
            }

    if "PDF/X" in name or "PDFX" in name:
        if passed:
            return {
                "icon": "check",
                "title": "Print Standard Compliance",
                "body": "Your file meets PDF/X print standards. It's compatible with professional print house workflows.",
                "type": "good"
            }
        else:
            return {
                "icon": "warn",
                "title": "Print Standard Issues",
                "body": "Your file has some issues that may cause problems at a professional print house. Review the technical details for specifics.",
                "type": "issue"
            }

    if "Spine" in name:
        if passed:
            return {
                "icon": "check",
                "title": "Spine Clearance",
                "body": "Content is safely away from the booklet spine. Nothing will be hidden in the binding.",
                "type": "good"
            }
        else:
            return {
                "icon": "warn",
                "title": "Spine Warning",
                "body": "Some content is too close to the booklet spine (centre fold). It may be hidden or hard to read after binding.",
                "type": "issue"
            }

    if "Creep" in name:
        return {
            "icon": "check" if not auto_fixed else "sparkle",
            "title": "Booklet Creep",
            "body": "Creep compensation has been calculated for your booklet. Inner pages will be slightly shifted outward to account for paper thickness.",
            "type": "fixed" if auto_fixed else "good"
        }

    if "Gutter" in name:
        if passed:
            return {
                "icon": "check",
                "title": "Gutter Clearance",
                "body": "No content overlapping the gutter (centre fold area). Your booklet pages will read cleanly.",
                "type": "good"
            }
        else:
            return {
                "icon": "warn",
                "title": "Gutter Collision",
                "body": "Some content overlaps the gutter area where the booklet folds. This content may be hidden or distorted after binding.",
                "type": "issue"
            }

    if "Downscale" in name:
        return {
            "icon": "warn",
            "title": "Scale Advisory",
            "body": "Your design may benefit from being slightly reduced in size to ensure all important content stays within the safe zone. This is a suggestion — repositioning elements is preferred.",
            "type": "issue"
        }

    if "Small Text" in name:
        if passed:
            return {
                "icon": "check",
                "title": "Text Readability",
                "body": "Your text sizes look good for print. Small text will remain legible on the final product.",
                "type": "good"
            }
        else:
            return {
                "icon": "warn",
                "title": "Small Text Warning",
                "body": "Some text may be too small to read clearly in print. We recommend a minimum of 7pt for body text.",
                "type": "issue"
            }

    if passed:
        return {
            "icon": "check" if not auto_fixed else "sparkle",
            "title": name,
            "body": msg,
            "type": "fixed" if auto_fixed else "good"
        }
    return {
        "icon": "warn",
        "title": name,
        "body": msg,
        "type": "issue"
    }


def build_report(checks, filename, proof_path, output_path, proof_paths=None, artwork_path=None):
    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=MARGIN,
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "ReportTitle",
        parent=styles["Title"],
        fontSize=22,
        textColor=BRAND,
        spaceAfter=2 * mm,
        alignment=TA_CENTER,
        fontName="Helvetica-Bold",
    )

    subtitle_style = ParagraphStyle(
        "ReportSubtitle",
        parent=styles["Normal"],
        fontSize=11,
        textColor=MUTED,
        spaceAfter=6 * mm,
        alignment=TA_CENTER,
        fontName="Helvetica",
    )

    section_title_style = ParagraphStyle(
        "SectionTitle",
        parent=styles["Heading2"],
        fontSize=13,
        textColor=DARK,
        spaceBefore=6 * mm,
        spaceAfter=3 * mm,
        fontName="Helvetica-Bold",
    )

    check_title_good = ParagraphStyle(
        "CheckTitleGood",
        parent=styles["Normal"],
        fontSize=11,
        textColor=GREEN,
        fontName="Helvetica-Bold",
        spaceAfter=1 * mm,
    )

    check_title_fixed = ParagraphStyle(
        "CheckTitleFixed",
        parent=styles["Normal"],
        fontSize=11,
        textColor=BLUE,
        fontName="Helvetica-Bold",
        spaceAfter=1 * mm,
    )

    check_title_issue = ParagraphStyle(
        "CheckTitleIssue",
        parent=styles["Normal"],
        fontSize=11,
        textColor=RED,
        fontName="Helvetica-Bold",
        spaceAfter=1 * mm,
    )

    body_style = ParagraphStyle(
        "CheckBody",
        parent=styles["Normal"],
        fontSize=10,
        textColor=DARK,
        spaceAfter=4 * mm,
        leading=14,
        fontName="Helvetica",
    )

    footer_style = ParagraphStyle(
        "Footer",
        parent=styles["Normal"],
        fontSize=9,
        textColor=MUTED,
        alignment=TA_CENTER,
        spaceBefore=8 * mm,
        fontName="Helvetica-Oblique",
    )

    story = []

    story.append(Paragraph("Flyerz.co.za Artwork Intelligence", ParagraphStyle(
        "BrandName", parent=styles["Normal"],
        fontSize=14, textColor=BRAND, alignment=TA_CENTER,
        fontName="Helvetica-Bold", spaceAfter=2 * mm,
    )))

    story.append(Paragraph("Flyerz.co.za Artwork Intelligence Report", title_style))

    now = datetime.now().strftime("%d %B %Y at %H:%M")
    story.append(Paragraph(
        f'File: <b>{filename}</b><br/>Report generated on {now}',
        subtitle_style
    ))

    story.append(HRFlowable(
        width="100%", thickness=1, color=HexColor("#e2e8f0"),
        spaceAfter=6 * mm, spaceBefore=2 * mm,
    ))

    good_count = sum(1 for c in checks if c.get("passed") and not c.get("autoFixed"))
    fixed_count = sum(1 for c in checks if c.get("autoFixed"))
    issue_count = sum(1 for c in checks if not c.get("passed"))

    summary_parts = []
    if good_count:
        summary_parts.append(f'<font color="{GREEN.hexval()}">{good_count} Good to Go</font>')
    if fixed_count:
        summary_parts.append(f'<font color="{BLUE.hexval()}">{fixed_count} Fixed by Artwork Intelligence</font>')
    if issue_count:
        summary_parts.append(f'<font color="{RED.hexval()}">{issue_count} Needs Attention</font>')

    story.append(Paragraph(
        " &nbsp;&bull;&nbsp; ".join(summary_parts) if summary_parts else "No checks performed",
        ParagraphStyle("Summary", parent=styles["Normal"], fontSize=11,
                        alignment=TA_CENTER, spaceAfter=6 * mm, fontName="Helvetica-Bold")
    ))

    artwork_img_path = artwork_path
    if not artwork_img_path and proof_path and os.path.exists(proof_path):
        artwork_img_path = proof_path
    if not artwork_img_path and all_proof_paths:
        for pp in all_proof_paths:
            if pp and os.path.exists(pp):
                artwork_img_path = pp
                break

    artwork_buf = None
    if artwork_img_path and os.path.exists(artwork_img_path):
        try:
            from PIL import Image as PILImg
            import io as _io

            pil_img = PILImg.open(artwork_img_path)
            if pil_img.mode == "CMYK":
                pil_img = pil_img.convert("RGB")
            pil_img.thumbnail((700, 500), PILImg.LANCZOS)
            artwork_buf = _io.BytesIO()
            pil_img.save(artwork_buf, format="PNG", optimize=True)
            artwork_buf.seek(0)
            pil_img.close()
            del pil_img

            from reportlab.lib.utils import ImageReader
            artwork_buf.seek(0)
            reader = ImageReader(artwork_buf)
            iw, ih = reader.getSize()
            avail_w = PAGE_W - 2 * MARGIN
            if iw > 0 and ih > 0:
                ratio = min(avail_w / iw, 120 * mm / ih)
                artwork_buf.seek(0)
                img = RLImage(artwork_buf, width=iw * ratio, height=ih * ratio)
                is_comparison = artwork_img_path and "bleed_proof" in os.path.basename(artwork_img_path)
                section_label = "Before & After Comparison" if is_comparison else "Your Artwork"
                story.append(Paragraph(section_label, section_title_style))
                story.append(img)
                story.append(Spacer(1, 6 * mm))
        except Exception as img_err:
            sys.stderr.write(f"[FAI] Could not embed artwork image: {img_err}\n")

    story.append(Paragraph("Detailed Results", section_title_style))

    for check in checks:
        mapped = friendly_mapping(check)
        ctype = mapped["type"]

        if ctype == "good":
            icon = "\u2705"
            ts = check_title_good
        elif ctype == "fixed":
            icon = "\u2728"
            ts = check_title_fixed
        else:
            icon = "\u26a0\ufe0f"
            ts = check_title_issue

        story.append(Paragraph(f"{icon} {mapped['title']}", ts))
        story.append(Paragraph(mapped["body"], body_style))

    all_proof_paths = proof_paths if proof_paths else ([proof_path] if proof_path and os.path.exists(proof_path) else [])
    all_proof_paths = [p for p in all_proof_paths if p and os.path.exists(p)]

    if all_proof_paths:
        story.append(Spacer(1, 4 * mm))
        story.append(HRFlowable(
            width="100%", thickness=1, color=HexColor("#e2e8f0"),
            spaceAfter=4 * mm, spaceBefore=2 * mm,
        ))
        page_label = "Before cut & after cut — print preview"
        story.append(Paragraph(page_label, section_title_style))
        story.append(Paragraph(
            "Before cut shows your full print file including bleed (outside the red line is trimmed off). "
            "After cut shows the finished size your customer receives.",
            ParagraphStyle("ProofDesc", parent=styles["Normal"], fontSize=10,
                            textColor=MUTED, spaceAfter=4 * mm, fontName="Helvetica")
        ))

        for page_idx, p_path in enumerate(all_proof_paths):
            try:
                from PIL import Image as PILImg
                import io as _io
                pil_img = PILImg.open(p_path)
                pil_img.thumbnail((800, 600), PILImg.LANCZOS)
                buf = _io.BytesIO()
                pil_img.save(buf, format="PNG", optimize=True)
                buf.seek(0)
                pil_img.close()
                del pil_img

                from reportlab.lib.utils import ImageReader
                reader = ImageReader(buf)
                iw, ih = reader.getSize()
                avail_w = PAGE_W - 2 * MARGIN
                if iw > 0 and ih > 0:
                    ratio = min(avail_w / iw, 150 * mm / ih)
                    img = RLImage(buf, width=iw * ratio, height=ih * ratio)
                    bn = os.path.basename(p_path).lower()
                    if "before" in bn:
                        caption = "Before cut — full artwork including bleed (outside red line is cut off)"
                    elif "after" in bn:
                        caption = "After cut — finished size (what you receive)"
                    elif len(all_proof_paths) > 1:
                        caption = f"Page {page_idx + 1} of {len(all_proof_paths)}"
                    else:
                        caption = None
                    if caption:
                        story.append(Paragraph(
                            caption,
                            ParagraphStyle("PageLabel", parent=styles["Normal"], fontSize=9,
                                            textColor=MUTED, spaceAfter=2 * mm, fontName="Helvetica-Bold")
                        ))
                    story.append(img)
                    story.append(Spacer(1, 4 * mm))
            except Exception:
                story.append(Paragraph(
                    f"<i>Preview image for page {page_idx + 1} could not be embedded.</i>",
                    ParagraphStyle("ProofErr", parent=styles["Normal"], fontSize=9,
                                    textColor=MUTED, fontName="Helvetica-Oblique")
                ))

    story.append(Spacer(1, 8 * mm))
    story.append(HRFlowable(
        width="100%", thickness=2, color=BRAND,
        spaceAfter=4 * mm, spaceBefore=4 * mm,
    ))
    story.append(Paragraph(
        "Your flyer is now officially Print-Ready! \u2013 Powered by Flyerz.co.za Artwork Intelligence",
        footer_style
    ))
    story.append(Paragraph(
        "\u00a9 2026 Flyerz.co.za Artwork Intelligence. All rights reserved.",
        ParagraphStyle("FooterSub", parent=styles["Normal"], fontSize=8,
                        textColor=MUTED, alignment=TA_CENTER, fontName="Helvetica",
                        spaceBefore=2 * mm)
    ))

    doc.build(story)

    if artwork_buf:
        try:
            artwork_buf.close()
        except Exception:
            pass

    return {"success": True, "reportPath": output_path}


def main():
    if len(sys.argv) < 5:
        print(json.dumps({"error": "Usage: health_report.py <checks_json> <filename> <output_path> <result_file> [proof_paths_json]"}))
        sys.exit(1)

    checks_json = sys.argv[1]
    filename = sys.argv[2]
    output_path = sys.argv[3]
    result_file = sys.argv[4]

    proof_path = None
    proof_paths = None
    artwork_path = None
    if len(sys.argv) > 5:
        raw_arg = sys.argv[5]
        try:
            parsed = json.loads(raw_arg)
            if isinstance(parsed, list):
                proof_paths = parsed
                proof_path = parsed[0] if parsed else None
            else:
                proof_path = raw_arg
        except (json.JSONDecodeError, Exception):
            proof_path = raw_arg

    if len(sys.argv) > 6:
        artwork_path = sys.argv[6]

    try:
        checks = json.loads(checks_json)
        result = build_report(checks, filename, proof_path, output_path, proof_paths=proof_paths, artwork_path=artwork_path)
        with open(result_file, "w") as f:
            json.dump(result, f)
        sys.stderr.write(f"[FAI] Report result written to {result_file}\n")
        print(json.dumps({"ok": True, "resultFile": result_file}))
    except Exception as e:
        import traceback
        error_result = {"error": str(e), "traceback": traceback.format_exc()}
        try:
            with open(result_file, "w") as f:
                json.dump(error_result, f)
            print(json.dumps({"ok": False, "resultFile": result_file}))
        except Exception:
            print(json.dumps({"ok": False, "error": str(e)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
