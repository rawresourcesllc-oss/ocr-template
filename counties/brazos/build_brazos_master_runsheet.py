#!/usr/bin/env python3
"""
Brazos County Runsheet Enricher
===============================
Safely snapshots `brazos_ocr_results.db` (so it never locks the live Apple Vision
crawler), filters out verb boilerplate ("do grant", "Lot NO"), merges `.001` + `.002`
OCR legal descriptions per `(year, doc_key)`, and enriches:
  - Column N (`PROPERTY DESCRIPTION`): appends any missing `A-XXX` Abstract or `XX Ac`
    Acreage discovered by Apple Vision OCR so Excel filters on Column N find them.
  - Column O (`ABSTRACTOR'S NOTES`): writes the full cleaned `OCR Legal: ...` string.
Syncs updated workbooks to both `X:\\County Clerks\\Brazos County Data\\Runsheets`
and `X:\\Runsheets`.
"""

import os
import re
import shutil
import sqlite3
import tempfile
import time
import zipfile
from xml.sax.saxutils import escape

CANDIDATE_DIRS = [
    r"X:\County Clerks\Brazos County Data",
    "/Volumes/WD_BLACK_8tb/County Clerks/Brazos County Data",
    "/Volumes/WD_BLACK_8_TB/County Clerks/Brazos County Data",
]
BASE_DIR = next((d for d in CANDIDATE_DIRS if os.path.isdir(d)), os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "brazos_ocr_results.db")
RUNSHEETS_DIR = os.path.join(BASE_DIR, "Runsheets")
GLOBAL_RUNSHEETS_DIR = r"X:\Runsheets" if os.path.isdir(r"X:\Runsheets") else "/Volumes/WD_BLACK_8tb/Runsheets"

BAD_LOT_BLK = {"NO", "NUMBER", "OR", "AND", "OF", "IN", "THE", "TO", "BEING", "ALL", "PART", "SAID", "BOLD", "S"}


def clean_str(val):
    if not val:
        return ""
    s = str(val).replace("\ufffd", " ")
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", s)
    return re.sub(r" {2,}", " ", s).strip()


def clean_ocr_legal_tokens(raw_leg):
    """Removes verb 'do grant' false positives and stopword Lot/Blk tokens."""
    if not raw_leg:
        return ""
    kept = []
    for tok in raw_leg.split(";"):
        t = clean_str(tok)
        if not t:
            continue
        tu = t.upper()
        # Filter out 'And By These Presents Do Grant' verb matches
        if tu.endswith(" GRANT") and any(w in tu for w in ("PRESENTS", "DO GRANT", "DOES GRANT", "HEREBY", "BARGAIN", "CONVEY")):
            continue
        if tu.startswith("LOT ") or tu.startswith("BLK "):
            val_part = tu.split(" ", 1)[1].strip()
            if val_part in BAD_LOT_BLK or (not any(c.isdigit() for c in val_part) and len(val_part) > 2):
                continue
        if t.upper() not in [k.upper() for k in kept]:
            kept.append(t)
    return "; ".join(kept)


def load_ocr_legals():
    print(f"[1/3] Taking read-only snapshot of {DB_PATH}...")
    if not os.path.exists(DB_PATH):
        raise FileNotFoundError(f"Database not found: {DB_PATH}")

    snap_db = os.path.join(tempfile.gettempdir(), "brazos_ocr_snapshot.db")
    shutil.copy2(DB_PATH, snap_db)

    conn = sqlite3.connect(snap_db)
    cur = conn.cursor()
    cur.execute("""
        SELECT doc_key, image_path, legal_desc
        FROM ocr_results
        WHERE legal_desc IS NOT NULL AND TRIM(legal_desc) != ''
        ORDER BY image_path ASC
    """)
    by_yr_doc = {}
    total_legals = 0
    for dk, img, leg in cur.fetchall():
        total_legals += 1
        c_leg = clean_ocr_legal_tokens(leg)
        if not c_leg:
            continue
        if img and "BRAZOSCC" in img:
            rel = img.replace("\\", "/").split("BRAZOSCC/", 1)[1]
            parts = rel.split("/")
            if len(parts) >= 2:
                yr = parts[0]
                stem = os.path.splitext(parts[-1])[0]
                yd_key = f"{yr}/{stem}".upper()
                if yd_key not in by_yr_doc:
                    by_yr_doc[yd_key] = c_leg
                else:
                    existing_tokens = [t.strip() for t in by_yr_doc[yd_key].split(";")]
                    for tok in [t.strip() for t in c_leg.split(";")]:
                        if tok and tok.upper() not in [e.upper() for e in existing_tokens]:
                            existing_tokens.append(tok)
                    by_yr_doc[yd_key] = "; ".join(existing_tokens)
    conn.close()
    try:
        os.remove(snap_db)
    except OSError:
        pass

    print(f"      Loaded {total_legals:,} OCR legal rows -> {len(by_yr_doc):,} clean (year/instrument) legals")
    return by_yr_doc


def enrich_workbook(xlsx_path, by_yr_doc):
    print(f"[2/3] Enriching {xlsx_path}...")
    backup_path = xlsx_path + ".bak_pre_ocr"
    if not os.path.exists(backup_path):
        shutil.copy2(xlsx_path, backup_path)
    else:
        # Always start from clean pre-OCR backup so re-runs are idempotent
        shutil.copy2(backup_path, xlsx_path)

    row_pat = re.compile(r'<row r="(\d+)"[^>]*>.*?</row>', re.DOTALL)
    cell_pat = re.compile(r'<c r="([A-Z]+)\d+"([^/>]*)(?:>(.*?)</c>|/>)', re.DOTALL)
    t_pat = re.compile(r"<t[^>]*>(.*?)</t>", re.DOTALL)
    v_pat = re.compile(r"<v>(.*?)</v>", re.DOTALL)

    with zipfile.ZipFile(xlsx_path, "r") as zin:
        sheet_text = zin.read("xl/worksheets/sheet1.xml").decode("utf-8")

    stats = {"rows": 0, "col_n_upgraded": 0, "col_o_filled": 0}

    def make_inlinestr(col, r_num, style, text):
        esc = escape(clean_str(text))
        return f'<c r="{col}{r_num}" s="{style}" t="inlineStr"><is><t>{esc}</t></is></c>'

    def replace_row(match):
        r_num = int(match.group(1))
        row_xml = match.group(0)
        if r_num < 7:
            return row_xml

        cells = {}
        for cm in cell_pat.finditer(row_xml):
            col = cm.group(1)
            attrs = cm.group(2)
            inner = cm.group(3) or ""
            tm = t_pat.search(inner) or v_pat.search(inner)
            val_str = tm.group(1).strip() if tm else ""
            cells[col] = (attrs, inner, val_str, cm.group(0))

        g_val = cells.get("G", ("", "", "", ""))[2]
        q_val = cells.get("Q", ("", "", "", ""))[2]
        if not g_val and not q_val:
            return row_xml

        stats["rows"] += 1
        yd_key = ""
        if "BRAZOSCC/" in q_val.upper():
            rel = q_val.replace("\\", "/").upper().split("BRAZOSCC/", 1)[1]
            parts = rel.split("/")
            if len(parts) >= 2:
                yd_key = f"{parts[0]}/{os.path.splitext(parts[-1])[0]}"

        ocr_leg = by_yr_doc.get(yd_key) or ""
        if not ocr_leg:
            return row_xml

        cur_n = cells.get("N", ("", "", "", ""))[2].replace("&amp;", "&")
        cur_o = cells.get("O", ("", "", "", ""))[2]
        updates = {}

        if not cur_n:
            updates["N"] = make_inlinestr("N", r_num, "5", ocr_leg)
            stats["col_n_upgraded"] += 1
        else:
            # Check if OCR discovered A-XXX Abstract, Survey, or Acreage missing from Col N
            cur_n_up = cur_n.upper()
            new_n_parts = []
            for tok in [t.strip() for t in ocr_leg.split(";")]:
                tu = tok.upper()
                if tu.startswith("A-") and tu not in cur_n_up:
                    new_n_parts.append(tok)
                elif tu.endswith(" AC") and " AC" not in cur_n_up:
                    new_n_parts.append(tok)
                elif (" SUR" in tu or " LEAGUE" in tu or " HEADRIGHT" in tu) and not any(w in cur_n_up for w in ("SUR", "LEAGUE", "HEADRIGHT")):
                    new_n_parts.append(tok)
            if new_n_parts:
                upgraded_n = f"{cur_n}; {'; '.join(new_n_parts)}"
                updates["N"] = make_inlinestr("N", r_num, "5", upgraded_n)
                stats["col_n_upgraded"] += 1

            if ocr_leg.upper() != cur_n_up and not cur_o:
                updates["O"] = make_inlinestr("O", r_num, "3", f"OCR Legal: {ocr_leg}")
                stats["col_o_filled"] += 1

        if not updates:
            return row_xml

        new_row = row_xml
        for col, new_c_xml in updates.items():
            if col in cells:
                new_row = new_row.replace(cells[col][3], new_c_xml, 1)
        return new_row

    new_sheet_text = row_pat.sub(replace_row, sheet_text)

    tmp_xlsx = os.path.join(tempfile.gettempdir(), os.path.basename(xlsx_path) + ".tmp")
    if os.path.exists(tmp_xlsx):
        os.remove(tmp_xlsx)

    with zipfile.ZipFile(xlsx_path, "r") as zin, zipfile.ZipFile(tmp_xlsx, "w", compression=zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            if item.filename == "xl/worksheets/sheet1.xml":
                zout.writestr(item, new_sheet_text.encode("utf-8"))
            else:
                zout.writestr(item, zin.read(item.filename))

    shutil.copy2(tmp_xlsx, xlsx_path)
    if os.path.isdir(GLOBAL_RUNSHEETS_DIR):
        shutil.copy2(tmp_xlsx, os.path.join(GLOBAL_RUNSHEETS_DIR, os.path.basename(xlsx_path)))
    os.remove(tmp_xlsx)
    print(f"      Processed {stats['rows']:,} rows | Upgraded Col N (Property Desc): {stats['col_n_upgraded']:,} | Enriched Col O (Notes): {stats['col_o_filled']:,}")


def main():
    t0 = time.time()
    print("=" * 70)
    print("BRAZOS COUNTY RUNSHEET OCR ENRICHMENT")
    print("=" * 70)
    by_yr_doc = load_ocr_legals()
    fp_1967 = os.path.join(RUNSHEETS_DIR, "Brazos County 1967 through 1999 run sheet.xlsx")
    if os.path.exists(fp_1967):
        enrich_workbook(fp_1967, by_yr_doc)
    fp_2000 = os.path.join(RUNSHEETS_DIR, "Brazos County 2000 through 2013 run sheet.xlsx")
    if os.path.exists(fp_2000):
        enrich_workbook(fp_2000, by_yr_doc)
    print("=" * 70)
    print(f"COMPLETED IN {time.time() - t0:.1f} SECONDS!")
    print("=" * 70)


if __name__ == "__main__":
    main()
