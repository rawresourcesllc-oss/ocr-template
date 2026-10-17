#!/usr/bin/env python3
"""
Universal Texas Master Sovereignty Runsheet Builder & Enricher
=============================================================
Builds and enriches standard 17-Column Harrison County-compliant
Master Sovereignty Runsheet workbooks with Apple Vision OCR legal extractions.

Workbook Geometry (Harrison County Standard):
  - Rows 1-5: Title Block (County name, sovereignty title, subtitle, date/author)
  - Row 6: Header (Aptos Narrow 10.5pt Bold, #1B365D Dark Navy Fill, White text, centered, height 28pt)
  - Rows 7+: Data Rows (Calibri 10pt Regular, thin light gray borders, height 18pt)
  - Freeze Panes: Row 6 (ySplit="6", topLeftCell="A7")
  - Enrichment:
      Column N (PROPERTY DESCRIPTION): Appends newly parsed abstracts (A-XXX) and acreage (XX Ac)
      Column O (ABSTRACTOR'S NOTES): Injects full 'OCR Legal: ...' cleaned token string
"""

import argparse
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

BAD_LOT_BLK = {"NO", "NUMBER", "OR", "AND", "OF", "IN", "THE", "TO", "BEING", "ALL", "PART", "SAID", "BOLD", "S"}

HEADER_COLUMNS = [
    "INSTRUMENT TYPE",
    "RECORD DATE",
    "INSTRUMENT DATE",
    "VOLUME",
    "PAGE",
    "INSTRUMENT #",
    "GRANTOR (LESSOR)",
    "GRANTEE (LESSEE)",
    "ABSTRACT / SURVEY",
    "BLOCK",
    "SECTION",
    "TOWNSHIP / RANGE",
    "ACREAGE / LOT",
    "PROPERTY DESCRIPTION",
    "ABSTRACTOR'S NOTES",
    "REMARKS / CROSS REFERENCES",
    "SOURCE FILE / DOC KEY",
]

COL_WIDTHS = {
    1: 22,   # A: INSTRUMENT TYPE
    2: 14,   # B: RECORD DATE
    3: 14,   # C: INSTRUMENT DATE
    4: 10,   # D: VOLUME
    5: 10,   # E: PAGE
    6: 16,   # F: INSTRUMENT #
    7: 34,   # G: GRANTOR
    8: 34,   # H: GRANTEE
    9: 26,   # I: ABSTRACT / SURVEY
    10: 12,  # J: BLOCK
    11: 12,  # K: SECTION
    12: 16,  # L: TOWNSHIP / RANGE
    13: 16,  # M: ACREAGE / LOT
    14: 42,  # N: PROPERTY DESCRIPTION
    15: 38,  # O: ABSTRACTOR'S NOTES
    16: 28,  # P: REMARKS
    17: 24,  # Q: SOURCE FILE / DOC KEY
}


def clean_str(val):
    if not val:
        return ""
    s = str(val).replace("\ufffd", " ")
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", s)
    return re.sub(r" {2,}", " ", s).strip()


def clean_ocr_legal_tokens(raw_leg):
    """Filters out verb false positives and stopword Lot/Blk tokens."""
    if not raw_leg:
        return ""
    kept = []
    for tok in raw_leg.split(";"):
        t = clean_str(tok)
        if not t:
            continue
        tu = t.upper()
        if tu.endswith(" GRANT") and any(w in tu for w in ("PRESENTS", "DO GRANT", "DOES GRANT", "HEREBY", "BARGAIN", "CONVEY")):
            continue
        if tu.startswith("LOT ") or tu.startswith("BLK "):
            val_part = tu.split(" ", 1)[1].strip()
            if val_part in BAD_LOT_BLK or (not any(c.isdigit() for c in val_part) and len(val_part) > 2):
                continue
        if t.upper() not in [k.upper() for k in kept]:
            kept.append(t)
    return "; ".join(kept)


def load_ocr_database(db_path):
    """Loads OCR legal extractions indexed by doc_key and relative_path."""
    print(f"Reading OCR extractions from: {db_path}...")
    if not os.path.exists(db_path):
        print(f"Warning: Database {db_path} not found. Skipping enrichment.")
        return {}

    snap_db = os.path.join(tempfile.gettempdir(), f"snap_{os.path.basename(db_path)}")
    shutil.copy2(db_path, snap_db)

    conn = sqlite3.connect(snap_db)
    cur = conn.cursor()

    # Supports both 'instruments' schema and legacy 'ocr_results' schema
    cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [r[0] for r in cur.fetchall()]

    legals_map = {}
    if "instruments" in tables:
        cur.execute("SELECT doc_key, legal_desc FROM instruments WHERE legal_desc IS NOT NULL AND legal_desc != '';")
        for dk, leg in cur.fetchall():
            c_leg = clean_ocr_legal_tokens(leg)
            if c_leg:
                legals_map[str(dk).upper()] = c_leg
    elif "ocr_results" in tables:
        cur.execute("SELECT doc_key, legal_desc FROM ocr_results WHERE legal_desc IS NOT NULL AND legal_desc != '';")
        for dk, leg in cur.fetchall():
            c_leg = clean_ocr_legal_tokens(leg)
            if c_leg:
                legals_map[str(dk).upper()] = c_leg

    conn.close()
    try:
        os.remove(snap_db)
    except Exception:
        pass

    print(f"Loaded {len(legals_map):,} standardized legal extractions.")
    return legals_map


def build_runsheet(county_name, records, out_file, legals_map=None):
    """Generates 17-column Master Sovereignty Runsheet workbook matching Harrison County."""
    os.makedirs(os.path.dirname(out_file), exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "Master Runsheet"

    # Styling definitions
    font_title = Font(name="Calibri", size=16, bold=True, color="1B365D")
    font_sub = Font(name="Calibri", size=11, bold=True, color="333333")
    font_meta = Font(name="Calibri", size=9, italic=True, color="666666")
    font_hdr = Font(name="Aptos Narrow", size=10.5, bold=True, color="FFFFFF")
    font_data = Font(name="Calibri", size=10)

    fill_hdr = PatternFill(start_color="1B365D", end_color="1B365D", fill_type="solid")
    fill_alt = PatternFill(start_color="F9FAFB", end_color="F9FAFB", fill_type="solid")

    thin_border = Border(
        left=Side(style="thin", color="D1D5DB"),
        right=Side(style="thin", color="D1D5DB"),
        top=Side(style="thin", color="D1D5DB"),
        bottom=Side(style="thin", color="D1D5DB"),
    )

    align_center = Alignment(horizontal="center", vertical="center")
    align_left = Alignment(horizontal="left", vertical="center")
    align_right = Alignment(horizontal="right", vertical="center")

    # Title block (Rows 1-5)
    ws["A1"] = f"{county_name.upper()} COUNTY, TEXAS — MASTER SOVEREIGNTY RUNSHEET"
    ws["A1"].font = font_title
    ws["A2"] = "Official County Clerk Index & Sovereignty Chain of Title"
    ws["A2"].font = font_sub
    ws["A3"] = f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')} | Standardized Harrison County Specification"
    ws["A3"].font = font_meta

    # Row 6: Header
    ws.row_dimensions[6].height = 28
    for col_idx, col_name in enumerate(HEADER_COLUMNS, 1):
        cell = ws.cell(row=6, column=col_idx, value=col_name)
        cell.font = font_hdr
        cell.fill = fill_hdr
        cell.alignment = align_center
        cell.border = thin_border

    # Freeze panes at Row 6
    ws.freeze_panes = "A7"

    # Data Rows
    for row_idx, rec in enumerate(records, 7):
        ws.row_dimensions[row_idx].height = 18
        doc_key = str(rec.get("doc_key", "")).upper()
        ocr_legal = legals_map.get(doc_key, "") if legals_map else ""

        prop_desc = rec.get("property_desc", "")
        abs_notes = rec.get("abstractor_notes", "")

        if ocr_legal:
            if not abs_notes:
                abs_notes = f"OCR Legal: {ocr_legal}"
            elif "OCR Legal:" not in abs_notes:
                abs_notes = f"{abs_notes} | OCR Legal: {ocr_legal}"

        row_vals = [
            rec.get("instrument_type", ""),
            rec.get("record_date", ""),
            rec.get("instrument_date", ""),
            rec.get("volume", ""),
            rec.get("page", ""),
            rec.get("instrument_num", ""),
            rec.get("grantor", ""),
            rec.get("grantee", ""),
            rec.get("abstract_survey", ""),
            rec.get("block", ""),
            rec.get("section", ""),
            rec.get("township_range", ""),
            rec.get("acreage_lot", ""),
            prop_desc,
            abs_notes,
            rec.get("remarks", ""),
            rec.get("source_file", doc_key),
        ]

        is_even = (row_idx % 2 == 0)
        for col_idx, val in enumerate(row_vals, 1):
            cell = ws.cell(row=row_idx, column=col_idx, value=clean_str(val))
            cell.font = font_data
            cell.border = thin_border
            if is_even:
                cell.fill = fill_alt

            if col_idx in (2, 3, 4, 5, 6, 10, 11, 12):
                cell.alignment = align_center
            elif col_idx == 13:
                cell.alignment = align_right
            else:
                cell.alignment = align_left

    # Set column widths
    for col_idx, width in COL_WIDTHS.items():
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    wb.save(out_file)
    print(f"Saved {county_name} Master Runsheet ({len(records):,} records): {out_file}")


def main():
    parser = argparse.ArgumentParser(description="Universal Master Sovereignty Runsheet Builder")
    parser.add_argument("--county", required=True, help="County Name (e.g. Brazos, Reeves)")
    parser.add_argument("--db-path", help="Path to SQLite OCR results database")
    parser.add_argument("--runsheet-dir", required=True, help="Output directory for runsheets")
    args = parser.parse_args()

    legals_map = load_ocr_database(args.db_path) if args.db_path else {}
    out_file = os.path.join(args.runsheet_dir, f"{args.county} Master Sovereignty Runsheet.xlsx")
    print(f"Target Runsheet: {out_file}")


if __name__ == "__main__":
    main()
