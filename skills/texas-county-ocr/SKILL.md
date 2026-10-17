---
name: texas-county-ocr
description: >-
  Standardized Apple Silicon Vision Neural Engine OCR and Master Sovereignty
  Runsheet pipeline for Texas County Clerk records. Produces searchable multi-page
  PDFs, structured text/JSON, FTS5-indexed SQLite databases with standardized
  Texas legal descriptions (Abstract, Survey, Block, Lot, Section, Acreage), and
  17-column Harrison County-compliant Excel runsheets.
---

# Texas County Clerk Apple Silicon OCR & Master Sovereignty Runsheet Skill

This skill defines the unified, standard operating procedure for indexing, OCR processing, and Master Sovereignty Runsheet generation across any Texas County Clerk archive (e.g., Brazos, Reeves, Panola, Harrison).

---

## 1. Unified 4-Output Standard

Every county OCR execution must produce the following standardized output suite:

```
[County_Output_Directory]/
├── Searchable_PDFs/             # Format 1: Multi-page PDFs with invisible text layer
│   └── [Volume_or_Year]/
│       └── [Instrument_ID].pdf
├── Text_JSON/                   # Format 2: Per-document plain text and bounding boxes
│   └── [Volume_or_Year]/
│       ├── [Instrument_ID].txt
│       └── [Instrument_ID].json
├── Database/                    # Format 3: SQLite Database with FTS5 & Texas Legals
│   └── [county]_records.sqlite
└── Runsheets/                   # Format 4: 17-Column Master Sovereignty Runsheets
    └── [County] Master Sovereignty Runsheet.xlsx
```

### Format 1: Searchable Multi-Page PDFs (`Searchable_PDFs/`)
* Preserves original high-resolution visual scans 1:1.
* Embeds an invisible text layer using CoreGraphics (`kCGTextInvisible`) mapped to precise word coordinates (`normBox`).
* Fully selectable and searchable via `Cmd + F` in Apple Preview, Adobe Acrobat, or any standard PDF viewer.

### Format 2: Clean Plain Text & Structured JSON (`Text_JSON/`)
* **`.txt`**: Plain UTF-8 text per document with clear `--- PAGE N ---` page delimiters.
* **`.json`**: Structured JSON containing document metadata, page dimensions (`width`, `height`), line bounding boxes (`x`, `y`, `w`, `h`), confidence scores, and raw line text.

### Format 3: SQLite Database with FTS5 & Standardized Texas Legal Descriptions (`Database/`)
* **Schema**:
  ```sql
  CREATE TABLE IF NOT EXISTS instruments (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      county TEXT,
      relative_path TEXT UNIQUE,
      doc_key TEXT,
      file_name TEXT,
      page_count INTEGER,
      pdf_path TEXT,
      txt_path TEXT,
      json_path TEXT,
      legal_desc TEXT,
      full_text TEXT,
      processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
  );
  CREATE VIRTUAL TABLE IF NOT EXISTS instruments_fts USING fts5(
      county, file_name, doc_key, legal_desc, full_text,
      content='instruments', content_rowid='id'
  );
  ```
* **Texas Legal Standardization**:
  * **Abstract**: `A-XXX` (e.g., `A-32`, `A-106`)
  * **Survey**: `[Name] Sur` (e.g., `James Hope Sur`, `Francis Clampitt Sur`)
  * **Block**: `Blk [Code]` (e.g., `Blk 4`, `Blk B`)
  * **Lot**: `Lot [Code]` (e.g., `Lot 12`)
  * **Section**: `Sec [Num]` (e.g., `Sec 2`)
  * **Acreage**: `[Num] Ac` (e.g., `100 Ac`, `47.5 Ac`)
* **Full-Text Search (FTS5)**:
  Sub-millisecond keyword and phrase queries:
  ```bash
  sqlite3 Database/county_records.sqlite \
    "SELECT file_name, legal_desc FROM instruments_fts WHERE instruments_fts MATCH 'oil AND lease AND \"A-32\"';"
  ```

### Format 4: Master Sovereignty Runsheet Excel Workbook (17 Columns)
* Strictly adheres to the **Harrison County Runsheet Standard**:
  * **Title Block (Rows 1–5)**: County name, sovereignty runsheet title, subtitle, date/author, blank row 5.
  * **Header (Row 6)**: Font `Aptos Narrow 10.5pt Bold`, fill `#1B365D` (Dark Navy Blue), font color `#FFFFFF` (White), centered, row height 28pt, thin gray borders.
  * **Data (Rows 7+)**: Font `Calibri 10pt Regular`, left-aligned text, centered dates/volumes/pages, right-aligned acreage, row height 18pt.
  * **Freeze Panes**: Frozen at row 6 (`ySplit="6"`, `topLeftCell="A7"`).
  * **17 Columns (A–Q)**:
    1. `A: INSTRUMENT TYPE`
    2. `B: RECORD DATE`
    3. `C: INSTRUMENT DATE`
    4. `D: VOLUME`
    5. `E: PAGE`
    6. `F: INSTRUMENT #`
    7. `G: GRANTOR (LESSOR)`
    8. `H: GRANTEE (LESSEE)`
    9. `I: ABSTRACT / SURVEY`
    10. `J: BLOCK`
    11. `K: SECTION`
    12. `L: TOWNSHIP / RANGE`
    13. `M: ACREAGE / LOT`
    14. `N: PROPERTY DESCRIPTION` (Enriched with discovered abstracts/surveys/acreage)
    15. `O: ABSTRACTOR'S NOTES` (Enriched with full `OCR Legal: ...` tokens)
    16. `P: REMARKS / CROSS REFERENCES`
    17. `Q: SOURCE FILE / DOC KEY`
  * **File Partitioning**: If county records exceed Excel's limit of 1,048,576 rows, split chronologically (e.g., *1967 through 1999* and *2000 through 2013*).

---

## 2. Hardware-Accelerated Engine & Crash Prevention Rules

To process 500,000 to 2,000,000+ images per county without memory leaks or macOS kernel watchdog panics:

1. **Dedicated Worker Recycling (`maxtasksperchild = 50`)**:
   Long-running native Objective-C/Metal allocations do not fully clear in Python's autorelease pool alone. Worker child processes must be destroyed and respawned after 50 tasks to force the macOS kernel to unmap 100% of heap and unified memory.
2. **Minimalist Dynamic RAM Floor (`SAFE_MIN_RAM_GB = 2.0`)**:
   Reserves only 2.0 GB of memory untouched for macOS kernel and `watchdogd`, unlocking 22.0 GB of a 24 GB Mac mini for active computing.
3. **Kernel Memory Pressure Monitoring**:
   Before every batch, query `sysctl kern.memorystatus_vm_pressure_level`. If pressure $\ge 2$ (WARN or CRITICAL), immediately pause, invoke `gc.collect()`, and resume once pressure returns to 1 and available RAM $\ge 3.0\text{ GB}$.
4. **Optimized Concurrency**:
   * **10 Workers** on Apple Silicon M-series (M1/M2/M3/M4 Pro/Max).
   * Yields **~24–28 pages/second** (~1,500 pages/min, ~90,000 pages/hour).
5. **Periodic SQLite WAL Checkpoints**:
   Call `PRAGMA wal_checkpoint(PASSIVE);` every 1,500 images to prevent the `-wal` file from ballooning.

---

## 3. Standard CLI Commands

### Run Full County OCR Pipeline:
```bash
python3 universal_county_ocr.py \
  --county "Brazos" \
  --input-dir "/Volumes/WD_BLACK_8tb/County Clerks/Brazos County Data/BRAZOSCC" \
  --output-dir "/Volumes/WD_BLACK_8tb/County Clerks/Brazos County Data" \
  --outputs all \
  --workers 10 \
  --ram-floor 2.0
```

### Build or Enrich Master Sovereignty Runsheets:
```bash
python3 universal_runsheet_builder.py \
  --county "Brazos" \
  --db-path "/Volumes/WD_BLACK_8tb/County Clerks/Brazos County Data/brazos_ocr_results.db" \
  --runsheet-dir "/Volumes/WD_BLACK_8tb/Runsheets"
```

### Fast Native Objective-C Engine (Maximum Raw C/Metal Throughput):
```bash
clang -O3 county_ocr.m \
  -framework Foundation -framework CoreGraphics -framework CoreText \
  -framework ImageIO -framework Vision -lsqlite3 \
  -o county_ocr

caffeinate -dis ./county_ocr \
  "/Volumes/WD_BLACK_8tb/County Clerks/Reeves County, TX" \
  "/Volumes/WD_BLACK_8tb/County Clerks/Reeves_County_OCR"
```

---

## 4. Multi-Machine Network Transfer Standard (Tailscale)

When transferring 500k+ county images between machines (e.g. MacBook Pro to Mac Mini):

* **Method 1: Streaming `tar` (Fastest, zero per-file roundtrips)**:
  ```bash
  caffeinate -dis tar -cf - -C "/Source/Path" "County Folder" | \
    ssh user@target-mac-mini "tar -xpf - -C '/Volumes/WD_BLACK_8tb/County_Clerks/'"
  ```
* **Method 2: Resumable `rsync` (Best for pause/resume)**:
  ```bash
  caffeinate -dis rsync -avP --stats \
    "/Source/Path/County Folder" \
    user@target-mac-mini:"/Volumes/WD_BLACK_8tb/County_Clerks/"
  ```
* **Post-Transfer Verification**:
  Always verify file counts 1:1 using `find ... -type f | wc -l`.
