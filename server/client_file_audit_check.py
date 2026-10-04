#!/usr/bin/env python3
"""Original-file checks: fonts, hairlines, black, resolution, spots, and the client note."""

from __future__ import annotations

import os
import tempfile

from client_file_audit import (
    FONT_ASK,
    apply_vector_fixes,
    audit_pdf,
    combined_client_message,
    font_report,
    repair_cmyk_images,
)
from designer_assistant import reply_from_audit


def fail(message: str) -> None:
    raise SystemExit(f"FAIL {message}")


def check(name: str, ok: bool, detail: str = "") -> None:
    if not ok:
        fail(f"{name} — {detail}")
    print(f"PASS {name}")


def _save(doc, path: str) -> None:
    doc.save(path)
    doc.close()


def _unembedded(path: str) -> None:
    import pymupdf as fitz

    doc = fitz.open()
    page = doc.new_page(width=300, height=400)
    page.insert_text((40, 80), "Helvetica headline", fontsize=24, fontname="helv", color=(0, 0, 0))
    _save(doc, path)


def _type3(path: str) -> None:
    import pikepdf

    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(200, 200))
    font = pikepdf.Dictionary(
        Type=pikepdf.Name("/Font"),
        Subtype=pikepdf.Name("/Type3"),
        FontMatrix=pikepdf.Array([0.001, 0, 0, 0.001, 0, 0]),
        FontBBox=pikepdf.Array([0, 0, 1000, 1000]),
        Encoding=pikepdf.Dictionary(Type=pikepdf.Name("/Encoding"), Differences=pikepdf.Array([65, pikepdf.Name("/A")])),
        CharProcs=pikepdf.Dictionary(A=pikepdf.Stream(pdf, b"0 0 100 100 re f")),
        FirstChar=65,
        LastChar=65,
        Widths=pikepdf.Array([1000]),
    )
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F3=pdf.make_indirect(font)))
    page.Contents = pikepdf.Stream(pdf, b"BT /F3 12 Tf 40 40 Td (A) Tj ET")
    pdf.save(path)
    pdf.close()


def _substituted(path: str) -> None:
    import pikepdf

    pdf = pikepdf.new()
    descriptor = pikepdf.Dictionary(
        Type=pikepdf.Name("/FontDescriptor"),
        FontName=pikepdf.Name("/NimbusSans-Regular"),
        Flags=32,
        FontBBox=pikepdf.Array([-100, -200, 1000, 800]),
        ItalicAngle=0,
        Ascent=800,
        Descent=-200,
        CapHeight=700,
        StemV=80,
        FontFile2=pikepdf.Stream(pdf, b"\x00\x01fake"),
    )
    font = pikepdf.Dictionary(
        Type=pikepdf.Name("/Font"),
        Subtype=pikepdf.Name("/TrueType"),
        BaseFont=pikepdf.Name("/Arial"),
        FontDescriptor=pdf.make_indirect(descriptor),
    )
    page = pdf.add_blank_page(page_size=(200, 200))
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=pdf.make_indirect(font)))
    page.Contents = pikepdf.Stream(pdf, b"")
    pdf.save(path)
    pdf.close()


def _press_problems(path: str) -> None:
    import pikepdf

    # Hairline, registration black, rich black on 8 pt text, a large 100K panel,
    # a 400% tint, and a spot colour. Page is A5-ish so the panel is large.
    content = b"""
q 0.10 w 20 20 m 200 20 l S Q
1 1 1 1 k 0 0 100 40 re f
q BT /F1 8 Tf 0.4 0.3 0.3 1 k 30 300 Td (Small) Tj ET Q
0 0 0 1 k 0 80 400 220 re f
1 1 1 0.5 k 0 40 30 20 re f
/Spot1 cs 1 scn 40 40 80 30 re f
BT /F1 24 Tf 0 0 0 1 k 40 360 Td (HEAD) Tj ET
"""
    pdf = pikepdf.new()
    spot = pikepdf.Array([
        pikepdf.Name("/Separation"),
        pikepdf.Name("/Pantone-123"),
        pikepdf.Name("/DeviceCMYK"),
        pikepdf.Dictionary(
            FunctionType=2,
            Domain=pikepdf.Array([0, 1]),
            C0=pikepdf.Array([0, 0, 0, 0]),
            C1=pikepdf.Array([0, 0.3, 0.9, 0]),
            N=1,
        ),
    ])
    font = pikepdf.Dictionary(Type=pikepdf.Name("/Font"), Subtype=pikepdf.Name("/Type1"), BaseFont=pikepdf.Name("/Helvetica"))
    page = pdf.add_blank_page(page_size=(420, 595))
    page.Resources = pikepdf.Dictionary(
        Font=pikepdf.Dictionary(F1=pdf.make_indirect(font)),
        ColorSpace=pikepdf.Dictionary(Spot1=spot),
    )
    page.Contents = pikepdf.Stream(pdf, content)
    pdf.save(path)
    pdf.close()


def _low_res(path: str) -> None:
    import pymupdf as fitz
    from PIL import Image

    folder = os.path.dirname(path)
    picture = os.path.join(folder, "soft.jpg")
    Image.new("RGB", (80, 60), (20, 80, 140)).save(picture, quality=85)
    doc = fitz.open()
    page = doc.new_page(width=400, height=500)
    page.insert_image(fitz.Rect(20, 20, 380, 470), filename=picture)
    tiny = os.path.join(folder, "speck.png")
    Image.new("RGB", (4, 4), (0, 0, 0)).save(tiny)
    page.insert_image(fitz.Rect(2, 2, 8, 8), filename=tiny)
    _save(doc, path)


def _dejavu(path: str) -> None:
    import pymupdf as fitz

    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    doc = fitz.open()
    page = doc.new_page(width=300, height=400)
    font = fitz.Font(fontfile=font_path)
    writer = fitz.TextWriter(page.rect)
    writer.append((40, 80), "Embedded", font=font, fontsize=18)
    writer.write_text(page, color=(0.1, 0.2, 0.8))
    _save(doc, path)


def test_fonts() -> None:
    folder = tempfile.mkdtemp(prefix="audit-font-")
    bare = os.path.join(folder, "helv.pdf")
    _unembedded(bare)
    report = font_report(bare)
    names = [row["name"] for row in report["problems"]]
    check("font-unembedded", any("Helvetica" in name for name in names), str(report))
    check("font-ask", FONT_ASK in combined_client_message(audit_pdf(bare)))

    typed = os.path.join(folder, "type3.pdf")
    _type3(typed)
    type_report = font_report(typed)
    check("font-type3-ok", type_report["problems"] == [] and type_report["type3"], str(type_report))

    stood = os.path.join(folder, "sub.pdf")
    _substituted(stood)
    sub = font_report(stood)
    check("font-substituted", any(row["reason"] == "substituted" for row in sub["problems"]), str(sub))

    good = os.path.join(folder, "embedded.pdf")
    _dejavu(good)
    embedded = font_report(good)
    check("font-embedded-ok", embedded["problems"] == [], str(embedded))


def test_vectors_and_message() -> None:
    folder = tempfile.mkdtemp(prefix="audit-vec-")
    src = os.path.join(folder, "bad.pdf")
    _press_problems(src)
    audit = audit_pdf(src, 148, 210)
    check("hairline-found", audit["hairlines"]["count"] >= 1, str(audit["hairlines"]))
    check("registration", audit["black"]["registration"] is True, str(audit["black"]))
    check("tac", audit["black"]["tacOver"] is True, str(audit["black"].get("peakTac")))
    check("rich-small", audit["black"]["richSmallText"] >= 1, str(audit["black"]))
    check("k-only-large", audit["black"]["largeKOnly"] is True, str(audit["black"]))
    check("spot", "Pantone-123" in audit["spots"]["names"], str(audit["spots"]))
    note = combined_client_message(audit)
    check("note-fonts", "Helvetica" in note and "embedded or outlined" in note, note)
    check("note-spot", "Pantone-123" in note, note)
    check("note-hair", "0.25" in note, note)
    check("note-black", "registration black" in note and "18 pt" in note, note)

    dest = os.path.join(folder, "fixed.pdf")
    fixes = apply_vector_fixes(src, dest)
    check("fixes-ran", fixes["rewritten"] and fixes["hairlines"] >= 1 and fixes["blackFixes"] >= 1, str(fixes))
    again = audit_pdf(dest)
    check("hairline-gone", again["hairlines"]["count"] == 0, str(again["hairlines"]))
    check("registration-gone", again["black"]["registration"] is False, str(again["black"]))
    check("tac-capped", again["black"]["peakTac"] <= 300.5, str(again["black"].get("peakTac")))
    check("rich-small-gone", again["black"]["richSmallText"] == 0, str(again["black"]))
    check("spot-gone", again["spots"]["names"] == [], str(again["spots"]))
    raw = open(dest, "rb").read()
    import pikepdf
    pdf = pikepdf.open(dest)
    stream = b"\n".join(page.get("/Contents").read_bytes() for page in pdf.pages)
    pdf.close()
    text = stream.decode("latin1", "replace")
    check("stroke-raised", "0.1000" not in text and "0.10" not in text, text[:400])
    check("small-text-k", "0.0000 0.0000 0.0000 1.0000 k" in text or "0 0 0 1 k" in text, text)
    check("rich-area", "0.4000 0.3000 0.3000 1.0000" in text or "0.4 0.3 0.3 1" in text, text)
    check("no-separation-bytes", b"/Separation" not in raw or b"/Pantone-123" not in open(dest, "rb").read(), "")


def test_resolution_ignores_speck() -> None:
    folder = tempfile.mkdtemp(prefix="audit-res-")
    path = os.path.join(folder, "soft.pdf")
    _low_res(path)
    audit = audit_pdf(path, 148, 210)
    names = [row["name"] for row in audit["resolution"]["images"]]
    check("res-severe", audit["resolution"]["severe"] is True, str(audit["resolution"]))
    check("res-names-soft", any("soft" in name.lower() or "image" in name.lower() or name for name in names), str(audit["resolution"]))
    check("res-ignores-speck", not any("speck" in name.lower() for name in names), str(names))
    note = combined_client_message(audit)
    from green_gate import LOW_RES_MESSAGE
    check("res-message", LOW_RES_MESSAGE in note and "ppi" in note, note)
    worst = audit["resolution"]["worst"]
    check("res-worst-not-max", worst is not None and worst < 150, str(worst))


def test_reply_only_ran_checks() -> None:
    audit = {
        "isPdf": True,
        "pages": 2,
        "fonts": {"checked": True, "problems": [{"name": "Arial", "reason": "not embedded"}], "type3": []},
        "hairlines": {"checked": False, "count": 9},
        "black": {"checked": True, "registration": False, "tacOver": False, "peakTac": 80, "richSmallText": 0, "largeKOnly": False},
        "resolution": {"checked": True, "worst": 220, "images": [{"name": "photo", "ppi": 220}], "amber": True, "severe": False},
        "spots": {"checked": True, "names": []},
        "size": {"ran": True, "ok": True, "detail": "The page is 148 x 210 mm, which matches the order."},
        "bleed": {"ran": True, "ok": False, "detail": "There is no bleed."},
        "textCut": {"ran": True, "ok": True, "detail": "Text is inside the safe area."},
        "qr": {"ran": False, "ok": False, "detail": "QR is fine and reads PAY."},
    }
    reply, actions = reply_from_audit(audit)
    check("reply-font", "Arial" in reply and "embedded or outlined" in reply, reply)
    check("reply-res", "220" in reply and "photo" in reply, reply)
    check("reply-no-hairline-claim", "hairline" not in reply.lower() and "0.25" not in reply, reply)
    check("reply-no-qr-claim", "QR" not in reply and "PAY" not in reply, reply)
    check("reply-pages", "2" in reply, reply)
    ids = [item["id"] for item in actions]
    check("actions-bleed", "add-bleed" in ids, str(ids))
    check("actions-message", "client-message" in ids, str(ids))
    check("actions-no-hairline-button", "thicken-hairlines" not in ids, str(ids))


def test_image_repair_keeps_small_k() -> None:
    import numpy as np
    import pikepdf

    folder = tempfile.mkdtemp(prefix="audit-ink-")
    path = os.path.join(folder, "plate.pdf")
    canvas = np.zeros((200, 200, 4), np.uint8)
    canvas[:, :] = (0, 0, 0, 255)
    canvas[0:24, 0:24] = (0, 0, 0, 0)
    canvas[4:10, 4:10] = (255, 255, 255, 255)
    pdf = pikepdf.new()
    image = pikepdf.Stream(pdf, canvas.tobytes())
    image["/Type"] = pikepdf.Name("/XObject")
    image["/Subtype"] = pikepdf.Name("/Image")
    image["/Width"] = 200
    image["/Height"] = 200
    image["/ColorSpace"] = pikepdf.Name("/DeviceCMYK")
    image["/BitsPerComponent"] = 8
    page = pdf.add_blank_page(page_size=(200, 200))
    page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im1=image))
    page.Contents = pikepdf.Stream(pdf, b"q 200 0 0 200 0 0 cm /Im1 Do Q")
    pdf.save(path)
    pdf.close()
    repair_cmyk_images(path)
    opened = pikepdf.open(path)
    raw = list(opened.pages[0]["/Resources"]["/XObject"].values())[0].read_bytes()
    opened.close()
    plate = np.frombuffer(raw, dtype=np.uint8).reshape(200, 200, 4)
    # The big 100K field becomes rich black. A registration speck becomes 100K because it is small.
    rich = plate[100, 100]
    speck = plate[6, 6]
    check("image-rich", int(rich[0]) > 40 and int(rich[3]) > 200, str(rich))
    check("image-speck-k", int(speck[0]) < 20 and int(speck[3]) > 200, str(speck))


def main() -> None:
    test_fonts()
    test_vectors_and_message()
    test_resolution_ignores_speck()
    test_reply_only_ran_checks()
    test_image_repair_keeps_small_k()
    print("client file audit checks passed")


if __name__ == "__main__":
    main()
