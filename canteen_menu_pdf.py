"""
Compile all IITK Hall Canteen Menus into a single segmented PDF.
Sources: iitk.ac.in/estateoffice + hall9 subdomain
"""

import io
from datetime import datetime
from pypdf import PdfWriter, PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph,
    Spacer, PageBreak, HRFlowable
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT

# ── Canteen PDFs available ────────────────────────────────────────────────────
# Hall III, VI, XIV had no canteen PDF on the estate office server
HALLS = [
    {"roman": "I",    "number": "Hall 1",   "path": "/tmp/iitk_canteen/Hall_I_Canteen.pdf"},
    {"roman": "II",   "number": "Hall 2",   "path": "/tmp/iitk_canteen/Hall_II_Canteen.pdf"},
    {"roman": "IV",   "number": "Hall 4",   "path": "/tmp/iitk_canteen/Hall_IV_Canteen.pdf"},
    {"roman": "V",    "number": "Hall 5",   "path": "/tmp/iitk_canteen/Hall_V_Canteen.pdf"},
    {"roman": "VII",  "number": "Hall 7",   "path": "/tmp/iitk_canteen/Hall_VII_Canteen.pdf"},
    {"roman": "VIII", "number": "Hall 8",   "path": "/tmp/iitk_canteen/Hall_VIII_Canteen.pdf"},
    {"roman": "IX",   "number": "Hall 9",   "path": "/tmp/iitk_canteen/Hall_IX_alt.pdf"},   # text PDF
    {"roman": "X",    "number": "Hall 10",  "path": "/tmp/iitk_canteen/Hall_X_Canteen.pdf"},
    {"roman": "XI",   "number": "Hall 11",  "path": "/tmp/iitk_canteen/Hall_XI_Canteen.pdf"},
    {"roman": "XII",  "number": "Hall 12",  "path": "/tmp/iitk_canteen/Hall_XII_Canteen.pdf"},
    {"roman": "XIII", "number": "Hall 13",  "path": "/tmp/iitk_canteen/Hall_XIII_Canteen.pdf"},
]

MISSING = [
    {"roman": "III",  "number": "Hall 3"},
    {"roman": "VI",   "number": "Hall 6"},
    {"roman": "XIV",  "number": "Hall 14"},
]

# ── Colors ────────────────────────────────────────────────────────────────────
NAVY   = colors.HexColor("#1a3a5c")
BLUE   = colors.HexColor("#2e6da4")
LGRAY  = colors.HexColor("#f0f4f8")
MGRAY  = colors.HexColor("#adb5bd")
WHITE  = colors.white
GOLD   = colors.HexColor("#f0a500")
RED    = colors.HexColor("#c0392b")

def make_cover_and_toc() -> bytes:
    """Return a bytes PDF containing cover page + TOC."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm,
        topMargin=2*cm, bottomMargin=2*cm,
    )

    styles = getSampleStyleSheet()

    s_title = ParagraphStyle("T", fontSize=26, textColor=NAVY,
                              fontName="Helvetica-Bold", alignment=TA_CENTER, spaceAfter=6)
    s_sub   = ParagraphStyle("S", fontSize=12, textColor=colors.HexColor("#555"),
                              alignment=TA_CENTER, spaceAfter=4)
    s_date  = ParagraphStyle("D", fontSize=9, textColor=colors.HexColor("#888"),
                              alignment=TA_CENTER)
    s_toc_h = ParagraphStyle("TH", fontSize=18, textColor=NAVY,
                              fontName="Helvetica-Bold", spaceAfter=10)
    s_toc   = ParagraphStyle("TC", fontSize=11, textColor=NAVY, leading=20)
    s_miss  = ParagraphStyle("TM", fontSize=10, textColor=RED, leading=18,
                              fontName="Helvetica-Oblique")
    s_note  = ParagraphStyle("TN", fontSize=8, textColor=colors.HexColor("#666"))

    story = []

    # ── Cover ─────────────────────────────────────────────────────────────────
    story.append(Spacer(1, 3*cm))
    story.append(Paragraph("IIT Kanpur", s_sub))
    story.append(Paragraph("Hall Canteen Menus", s_title))
    story.append(Spacer(1, 0.3*cm))
    story.append(Paragraph("Price Schedule of Items — All Available Halls", s_sub))
    story.append(Spacer(1, 0.2*cm))
    story.append(Paragraph(
        f"Compiled on {datetime.now().strftime('%d %B %Y')}  |  Source: iitk.ac.in/estateoffice",
        s_date
    ))
    story.append(Spacer(1, 1.2*cm))
    story.append(HRFlowable(width="100%", thickness=2, color=NAVY))
    story.append(Spacer(1, 0.8*cm))

    # Stats box
    stats = [[
        Paragraph(f"<font color='#1a3a5c'><b>{len(HALLS)}</b></font><br/><font size=9>Halls with menus</font>",
                  ParagraphStyle("x", alignment=TA_CENTER, fontSize=20)),
        Paragraph(f"<font color='#c0392b'><b>{len(MISSING)}</b></font><br/><font size=9>No data available</font>",
                  ParagraphStyle("x2", alignment=TA_CENTER, fontSize=20)),
    ]]
    st = Table(stats, colWidths=[8*cm, 8*cm])
    st.setStyle(TableStyle([
        ("ALIGN",  (0,0),(-1,-1),"CENTER"),
        ("VALIGN", (0,0),(-1,-1),"MIDDLE"),
        ("BOX",    (0,0),(-1,-1), 1, MGRAY),
        ("INNERGRID",(0,0),(-1,-1), 0.5, MGRAY),
        ("BACKGROUND",(0,0),(0,0), colors.HexColor("#eaf4fb")),
        ("BACKGROUND",(1,0),(1,0), colors.HexColor("#fdf2f2")),
        ("TOPPADDING",(0,0),(-1,-1),14),
        ("BOTTOMPADDING",(0,0),(-1,-1),14),
    ]))
    story.append(st)
    story.append(Spacer(1, 0.8*cm))

    story.append(Paragraph(
        "Each section contains the original canteen price schedule document as published by the "
        "IIT Kanpur Estate Office. Items include hot drinks, cold beverages, sandwiches, "
        "snacks, veg &amp; non-veg meals, and packaged goods.",
        ParagraphStyle("desc", fontSize=9, textColor=colors.HexColor("#444"),
                       alignment=TA_CENTER, leading=14)
    ))

    story.append(PageBreak())

    # ── TOC ───────────────────────────────────────────────────────────────────
    story.append(Paragraph("Table of Contents", s_toc_h))
    story.append(HRFlowable(width="100%", thickness=1, color=MGRAY))
    story.append(Spacer(1, 0.4*cm))

    for i, h in enumerate(HALLS, 1):
        story.append(Paragraph(
            f"&nbsp;&nbsp;{i:02d}.&nbsp;&nbsp;<b>Hall {h['roman']}</b>"
            f"&nbsp;&nbsp;<font color='gray' size=9>({h['number']})</font>",
            s_toc
        ))

    story.append(Spacer(1, 0.6*cm))
    story.append(Paragraph("Not Available:", s_miss))
    for h in MISSING:
        story.append(Paragraph(
            f"&nbsp;&nbsp;✗&nbsp;&nbsp;Hall {h['roman']} ({h['number']}) — "
            "no canteen menu found on iitk.ac.in",
            s_miss
        ))

    story.append(Spacer(1, 0.8*cm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=MGRAY))
    story.append(Spacer(1, 0.3*cm))
    story.append(Paragraph(
        "Note: These are the official price schedules submitted during canteen tender processes. "
        "Actual current prices may vary. Documents sourced from www.iitk.ac.in/estateoffice.",
        s_note
    ))

    story.append(PageBreak())

    doc.build(story)
    buf.seek(0)
    return buf.read()


def make_divider_page(hall: dict) -> bytes:
    """Return a 1-page divider/section header for a hall."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm,
        topMargin=2*cm, bottomMargin=2*cm,
    )
    story = []
    story.append(Spacer(1, 6*cm))

    # Big hall banner
    banner = [[Paragraph(
        f"Hall {hall['roman']}<br/><font size=14>({hall['number']})</font>",
        ParagraphStyle("BN", fontSize=30, textColor=WHITE,
                       fontName="Helvetica-Bold", alignment=TA_CENTER, leading=38)
    )]]
    bt = Table(banner, colWidths=[16*cm])
    bt.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(-1,-1), NAVY),
        ("ALIGN",         (0,0),(-1,-1), "CENTER"),
        ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
        ("TOPPADDING",    (0,0),(-1,-1), 30),
        ("BOTTOMPADDING", (0,0),(-1,-1), 30),
        ("BOX",           (0,0),(-1,-1), 2, GOLD),
    ]))
    story.append(bt)
    story.append(Spacer(1, 0.6*cm))
    story.append(Paragraph(
        "Canteen Price Schedule  —  IIT Kanpur Estate Office",
        ParagraphStyle("sub2", fontSize=11, textColor=colors.HexColor("#555"),
                       alignment=TA_CENTER)
    ))

    story.append(PageBreak())
    doc.build(story)
    buf.seek(0)
    return buf.read()


def compile_pdf(output_path: str):
    writer = PdfWriter()

    # 1. Cover + TOC
    print("  Building cover and TOC...")
    cover_bytes = make_cover_and_toc()
    cover_reader = PdfReader(io.BytesIO(cover_bytes))
    for page in cover_reader.pages:
        writer.add_page(page)

    # 2. Each hall: divider + original PDF
    for hall in HALLS:
        print(f"  Adding Hall {hall['roman']} ({hall['number']})...")

        # Divider page
        div_bytes  = make_divider_page(hall)
        div_reader = PdfReader(io.BytesIO(div_bytes))
        for page in div_reader.pages:
            writer.add_page(page)

        # Original canteen PDF
        try:
            src_reader = PdfReader(hall["path"])
            for page in src_reader.pages:
                writer.add_page(page)
        except Exception as e:
            print(f"    Warning: could not add {hall['path']}: {e}")

    # 3. Write out
    with open(output_path, "wb") as f:
        writer.write(f)

    print(f"\n✅  Saved to: {output_path}")
    total = sum(1 for _ in PdfReader(output_path).pages)
    print(f"   Total pages: {total}")


if __name__ == "__main__":
    out = "/home/mai/Documents/trade-bot/iitk_canteen_menus_all_halls.pdf"
    print("Compiling IITK Hall Canteen Menus PDF...")
    compile_pdf(out)
