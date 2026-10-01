"""
Generate a complete, segmented PDF of weekly mess menus for all halls
from campusmess.in
"""

import json
import requests
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer,
    PageBreak, KeepTogether
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.platypus import HRFlowable
from datetime import datetime

# ─── Hall data (fetched from API) ────────────────────────────────────────────
HALLS = [
    {"id": 13, "name": "GH 1",    "type": "Girls"},
    {"id": 1,  "name": "Hall 14", "type": "Boys"},
    {"id": 11, "name": "Hall 13", "type": "Boys"},
    {"id": 2,  "name": "Hall 12", "type": "Boys"},
    {"id": 6,  "name": "Hall 11", "type": "Boys"},
    {"id": 3,  "name": "Hall 10", "type": "Boys"},
    {"id": 12, "name": "Hall 9",  "type": "Boys"},
    {"id": 5,  "name": "Hall 8",  "type": "Co-ed"},
    {"id": 4,  "name": "Hall 7",  "type": "Girls"},
    {"id": 10, "name": "Hall 6",  "type": "Co-ed"},
    {"id": 8,  "name": "Hall 5",  "type": "Co-ed"},
    {"id": 7,  "name": "Hall 4",  "type": "Girls"},
    {"id": 9,  "name": "Hall 2",  "type": "Co-ed"},
    {"id": 15, "name": "Hall 3",  "type": "Boys"},
    {"id": 17, "name": "Hall 1",  "type": "Boys"},
]

DAYS_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MEALS_ORDER = ["Breakfast", "Lunch", "Dinner"]

# ─── Color palette ────────────────────────────────────────────────────────────
COL_HEADER_BG   = colors.HexColor("#1a3a5c")   # deep navy
COL_HALL_TITLE  = colors.HexColor("#1a3a5c")
COL_DAY_BG      = colors.HexColor("#2e6da4")   # medium blue
COL_BREAKFAST   = colors.HexColor("#fff3cd")   # warm yellow
COL_LUNCH       = colors.HexColor("#d4edda")   # soft green
COL_DINNER      = colors.HexColor("#d1ecf1")   # light cyan
COL_EXTRAS_BG   = colors.HexColor("#f8d7da")   # pale rose for extras
COL_WHITE       = colors.white
COL_BLACK       = colors.black
COL_GRID        = colors.HexColor("#adb5bd")

MEAL_COLORS = {
    "Breakfast": COL_BREAKFAST,
    "Lunch":     COL_LUNCH,
    "Dinner":    COL_DINNER,
}

def fetch_hall_menu(hall_id):
    url = f"https://campusmess.in/api/halls/{hall_id}/weekly"
    try:
        r = requests.get(url, timeout=10)
        data = r.json()
        return data.get("data", [])
    except Exception as e:
        print(f"  Warning: could not fetch hall {hall_id}: {e}")
        return []

def build_meal_lookup(menu_items):
    """Returns {day: {meal_type: item}} dict."""
    lookup = {}
    for item in menu_items:
        day  = item.get("dayOfWeek", "")
        meal = item.get("mealType", "")
        lookup.setdefault(day, {})[meal] = item
    return lookup

def wrap(text, style):
    """Wrap plain text in a Paragraph for table cells."""
    if not text:
        return Paragraph("—", style)
    # Clean up newlines
    text = text.replace("\n", "<br/>")
    return Paragraph(text, style)

def make_pdf(output_path="mess_menu_all_halls.pdf"):
    doc = SimpleDocTemplate(
        output_path,
        pagesize=landscape(A4),
        leftMargin=1.2*cm, rightMargin=1.2*cm,
        topMargin=1.5*cm, bottomMargin=1.5*cm,
        title="IIT Campus Mess — Weekly Menu (All Halls)",
        author="campusmess.in",
    )

    styles = getSampleStyleSheet()

    # Custom paragraph styles
    style_cover_title = ParagraphStyle(
        "CoverTitle",
        parent=styles["Title"],
        fontSize=28,
        textColor=COL_HEADER_BG,
        spaceAfter=10,
        alignment=TA_CENTER,
        fontName="Helvetica-Bold",
    )
    style_cover_sub = ParagraphStyle(
        "CoverSub",
        parent=styles["Normal"],
        fontSize=13,
        textColor=colors.HexColor("#555555"),
        spaceAfter=6,
        alignment=TA_CENTER,
    )
    style_hall_title = ParagraphStyle(
        "HallTitle",
        parent=styles["Heading1"],
        fontSize=16,
        textColor=COL_WHITE,
        backColor=COL_HALL_TITLE,
        spaceBefore=0,
        spaceAfter=4,
        alignment=TA_CENTER,
        fontName="Helvetica-Bold",
        borderPad=8,
    )
    style_day_header = ParagraphStyle(
        "DayHeader",
        fontSize=9,
        textColor=COL_WHITE,
        fontName="Helvetica-Bold",
        alignment=TA_CENTER,
    )
    style_meal_label = ParagraphStyle(
        "MealLabel",
        fontSize=8,
        textColor=colors.HexColor("#1a3a5c"),
        fontName="Helvetica-Bold",
        alignment=TA_CENTER,
    )
    style_cell = ParagraphStyle(
        "Cell",
        fontSize=7,
        leading=9,
        textColor=COL_BLACK,
        alignment=TA_LEFT,
    )
    style_extras_cell = ParagraphStyle(
        "ExtrasCell",
        fontSize=7,
        leading=9,
        textColor=colors.HexColor("#721c24"),
        alignment=TA_LEFT,
        fontName="Helvetica-Oblique",
    )
    style_toc = ParagraphStyle(
        "TOC",
        parent=styles["Normal"],
        fontSize=11,
        leading=18,
        textColor=COL_HEADER_BG,
    )
    style_index_title = ParagraphStyle(
        "IndexTitle",
        parent=styles["Heading2"],
        fontSize=18,
        textColor=COL_HEADER_BG,
        fontName="Helvetica-Bold",
        spaceAfter=12,
    )
    style_legend = ParagraphStyle(
        "Legend",
        parent=styles["Normal"],
        fontSize=8,
        textColor=colors.HexColor("#333333"),
    )

    story = []

    # ── COVER PAGE ────────────────────────────────────────────────────────────
    story.append(Spacer(1, 4*cm))
    story.append(Paragraph("🍽  Campus Mess Weekly Menu", style_cover_title))
    story.append(Spacer(1, 0.3*cm))
    story.append(Paragraph("All Residential Halls — Complete Schedule", style_cover_sub))
    story.append(Spacer(1, 0.2*cm))
    story.append(Paragraph(
        f"Generated on {datetime.now().strftime('%d %B %Y, %I:%M %p')} &nbsp;|&nbsp; Source: campusmess.in",
        style_cover_sub
    ))
    story.append(Spacer(1, 1.5*cm))
    story.append(HRFlowable(width="100%", thickness=2, color=COL_HEADER_BG))
    story.append(Spacer(1, 0.8*cm))

    # Quick stats table on cover
    stats_data = [
        [Paragraph("<b>Total Halls</b>", style_cover_sub),
         Paragraph("<b>Meals per Day</b>", style_cover_sub),
         Paragraph("<b>Days Covered</b>", style_cover_sub)],
        [Paragraph(f"<font size=22 color='#1a3a5c'><b>{len(HALLS)}</b></font>", style_cover_sub),
         Paragraph("<font size=22 color='#1a3a5c'><b>3</b></font>", style_cover_sub),
         Paragraph("<font size=22 color='#1a3a5c'><b>7</b></font>", style_cover_sub)],
    ]
    stats_tbl = Table(stats_data, colWidths=[7*cm, 7*cm, 7*cm])
    stats_tbl.setStyle(TableStyle([
        ("ALIGN", (0,0), (-1,-1), "CENTER"),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0,0), (-1,-1), [colors.HexColor("#f0f4f8"), colors.white]),
        ("BOX", (0,0), (-1,-1), 1, COL_GRID),
        ("INNERGRID", (0,0), (-1,-1), 0.5, COL_GRID),
        ("TOPPADDING", (0,0), (-1,-1), 10),
        ("BOTTOMPADDING", (0,0), (-1,-1), 10),
    ]))
    story.append(stats_tbl)
    story.append(Spacer(1, 1*cm))

    # Legend
    legend_data = [[
        Paragraph("■ Breakfast (Yellow)", ParagraphStyle("l1", fontSize=9, textColor=colors.HexColor("#856404"), backColor=COL_BREAKFAST)),
        Paragraph("■ Lunch (Green)",      ParagraphStyle("l2", fontSize=9, textColor=colors.HexColor("#155724"), backColor=COL_LUNCH)),
        Paragraph("■ Dinner (Cyan)",      ParagraphStyle("l3", fontSize=9, textColor=colors.HexColor("#0c5460"), backColor=COL_DINNER)),
        Paragraph("■ Extras / Non-Veg (Pink)", ParagraphStyle("l4", fontSize=9, textColor=colors.HexColor("#721c24"), backColor=COL_EXTRAS_BG)),
    ]]
    legend_tbl = Table(legend_data, colWidths=[5.5*cm]*4)
    legend_tbl.setStyle(TableStyle([
        ("ALIGN", (0,0), (-1,-1), "CENTER"),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING", (0,0), (-1,-1), 6),
        ("BOTTOMPADDING", (0,0), (-1,-1), 6),
        ("BOX", (0,0), (-1,-1), 0.5, COL_GRID),
        ("INNERGRID", (0,0), (-1,-1), 0.5, COL_GRID),
    ]))
    story.append(legend_tbl)
    story.append(PageBreak())

    # ── TABLE OF CONTENTS ─────────────────────────────────────────────────────
    story.append(Paragraph("Table of Contents", style_index_title))
    story.append(HRFlowable(width="100%", thickness=1, color=COL_GRID))
    story.append(Spacer(1, 0.4*cm))

    for i, hall in enumerate(HALLS, 1):
        story.append(Paragraph(
            f"&nbsp;&nbsp;{i:02d}.&nbsp;&nbsp;<b>{hall['name']}</b>"
            f"&nbsp;&nbsp;<font color='gray'>({hall['type']} Hostel)</font>",
            style_toc
        ))
    story.append(PageBreak())

    # ── PER-HALL PAGES ────────────────────────────────────────────────────────
    page_w = landscape(A4)[0] - 2.4*cm   # usable width

    for hall in HALLS:
        print(f"  Processing {hall['name']} (ID {hall['id']})...")
        menu_items = fetch_hall_menu(hall["id"])
        lookup = build_meal_lookup(menu_items)

        # Hall title banner
        hall_banner_data = [[
            Paragraph(
                f"{hall['name']}  —  {hall['type']} Hostel",
                style_hall_title
            )
        ]]
        hall_banner = Table(hall_banner_data, colWidths=[page_w])
        hall_banner.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,-1), COL_HALL_TITLE),
            ("TOPPADDING",  (0,0), (-1,-1), 8),
            ("BOTTOMPADDING", (0,0), (-1,-1), 8),
            ("LEFTPADDING",  (0,0), (-1,-1), 10),
        ]))
        story.append(hall_banner)
        story.append(Spacer(1, 0.25*cm))

        # Calculate column widths:
        # Col 0: Meal label (~1.8cm), Cols 1-7: 7 days equally
        col0_w = 1.8*cm
        day_col_w = (page_w - col0_w) / 7

        # Build table header row
        header_row = [Paragraph("Meal", style_day_header)]
        for day in DAYS_ORDER:
            header_row.append(Paragraph(day, style_day_header))

        table_data = [header_row]
        row_styles = []

        # Row index tracker (header = 0)
        row_idx = 1

        for meal in MEALS_ORDER:
            # Main description row
            desc_row = [Paragraph(meal, style_meal_label)]
            for day in DAYS_ORDER:
                item = lookup.get(day, {}).get(meal)
                desc = (item.get("description") or "").strip() if item else ""
                desc_row.append(wrap(desc, style_cell))

            # Extras row (only if any hall has extras for this meal)
            has_extras = any(
                bool((lookup.get(day, {}).get(meal) or {}).get("extras") or "")
                for day in DAYS_ORDER
            )

            extras_row = [Paragraph("Extras /\nNon-Veg", ParagraphStyle(
                "ExtLbl", fontSize=7, textColor=colors.HexColor("#721c24"),
                fontName="Helvetica-BoldOblique", alignment=TA_CENTER
            ))]
            for day in DAYS_ORDER:
                item = lookup.get(day, {}).get(meal)
                ex = (item.get("extras") or "").strip() if item else ""
                extras_row.append(wrap(ex, style_extras_cell) if ex else Paragraph("", style_cell))

            table_data.append(desc_row)
            row_styles.append(("BACKGROUND", (0, row_idx), (-1, row_idx), MEAL_COLORS[meal]))
            row_idx += 1

            if has_extras:
                table_data.append(extras_row)
                row_styles.append(("BACKGROUND", (0, row_idx), (-1, row_idx), COL_EXTRAS_BG))
                row_idx += 1

            # Thin separator after each meal group
            row_styles.append(("LINEBELOW", (0, row_idx - 1), (-1, row_idx - 1), 1.2, COL_GRID))

        col_widths = [col0_w] + [day_col_w] * 7
        menu_table = Table(table_data, colWidths=col_widths, repeatRows=1)

        base_style = [
            # Header
            ("BACKGROUND",   (0, 0), (-1, 0), COL_DAY_BG),
            ("TEXTCOLOR",    (0, 0), (-1, 0), COL_WHITE),
            ("FONTNAME",     (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",     (0, 0), (-1, 0), 9),
            ("ALIGN",        (0, 0), (-1, 0), "CENTER"),
            # Meal label column
            ("ALIGN",        (0, 1), (0, -1), "CENTER"),
            ("VALIGN",       (0, 0), (-1, -1), "TOP"),
            # Grid
            ("BOX",          (0, 0), (-1, -1), 1.2, COL_HEADER_BG),
            ("INNERGRID",    (0, 0), (-1, -1), 0.4, COL_GRID),
            # Padding
            ("TOPPADDING",   (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
            ("LEFTPADDING",  (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ]

        menu_table.setStyle(TableStyle(base_style + row_styles))

        story.append(KeepTogether([menu_table]))
        story.append(PageBreak())

    # Build PDF
    print("  Building PDF...")
    doc.build(story)
    print(f"\n✅  PDF saved to: {output_path}")

if __name__ == "__main__":
    output = "/home/mai/Documents/trade-bot/mess_menu_all_halls.pdf"
    print("Fetching menus and generating PDF...")
    make_pdf(output)
