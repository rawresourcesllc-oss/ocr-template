# Brazos County, TX — Mac Mini Transfer & OCR Processing Guide

## Overview

This guide details how to transfer the **Brazos County, TX** records (47 years, 1967–2013, ~1,986,558 files) to **Ryan's Mac Mini** (`ryans-mac-mini`) targeting the **`WD_BLACK_8tb`** external drive, and how to execute the high-speed Apple Silicon Vision Neural Engine OCR and Master Sovereignty Runsheet pipeline to generate all **4 unified output formats**:
1. **Searchable Multi-Page PDFs** (`Searchable_PDFs/`): Original scans with embedded invisible selectable text layer (`kCGTextInvisible`), searchable via `Cmd + F`.
2. **Clean Plain Text & Structured JSON** (`Text_JSON/`): `.txt` per document and `.json` containing line bounding boxes, confidence scores, and raw text.
3. **Local Searchable SQLite Database (`brazos_ocr_results.db`)**: High-performance SQLite database with full-text search indexing (FTS5) and standardized Texas legal descriptions (`Abstract: A-XXX`, `Survey: [Name] Sur`, `Block`, `Lot`, `Section`, `Acreage`).
4. **Master Sovereignty Runsheet Excel Workbooks (17 Columns)**: Formatted strictly according to the Harrison County runsheet standard with `Aptos Narrow 10.5pt Bold` headers on row 6, `Calibri 10pt` data rows, freeze panes at row 6, and OCR legal enrichment in Columns N & O.

---

## 1. Directory Structure on `WD_BLACK_8tb`

```
/Volumes/WD_BLACK_8tb/County Clerks/Brazos County Data/
├── BRAZOSCC/                            # Source Deed Scans (47 Year Folders: 1967 -> 2013)
│   ├── 1967/
│   ├── 1968/
│   └── ...
├── brazos_ocr_results.db               # SQLite Master Database (FTS5 + Texas Legal Extracts)
├── brazos_ocr.log                      # Live streaming execution log
├── brazos_apple_vision_crawler.py      # 10-Worker Apple Silicon Vision Pipeline
├── build_brazos_master_runsheet.py     # 17-Column Excel Runsheet Builder & Enricher
├── run_brazos_ocr.sh                   # One-click execution script under caffeinate
├── push_to_mac_mini.sh                 # 1:1 Resumable Tailscale transfer script
└── Runsheets/                          # Generated Master Sovereignty Runsheet Workbooks
    ├── Brazos County 1967 through 1999 run sheet.xlsx  (631,618 rows)
    └── Brazos County 2000 through 2013 run sheet.xlsx  (505,381 rows)
```

---

## 2. 1:1 Network Transfer to Mac Mini (Tailscale)

### Method 1: Streaming `tar` over SSH (Fastest)
Because Brazos County has ~1.98 million files, streaming via `tar` avoids round-trip latency by sending a single contiguous byte stream:

```bash
# 1. Ensure target directory exists on Mac Mini:
ssh ryan@100.122.169.98 "mkdir -p '/Volumes/WD_BLACK_8tb/County Clerks/Brazos County Data'"

# 2. Stream at maximum network speed with 100% preserved timestamps & permissions:
caffeinate -dis tar -cf - -C "/Source/Directory" "Brazos County Data" | \
  ssh ryan@100.122.169.98 "tar -xpf - -C '/Volumes/WD_BLACK_8tb/County Clerks/'"
```

### Method 2: Resumable `rsync` over Tailscale
```bash
caffeinate -dis rsync -avP --stats \
  "/Source/Directory/Brazos County Data" \
  ryan@100.122.169.98:"/Volumes/WD_BLACK_8tb/County Clerks/"
```

### 1:1 File Count Verification
```bash
# Check source count:
find "/Source/Directory/Brazos County Data" -type f | wc -l

# Check destination count on Mac Mini:
ssh ryan@100.122.169.98 "find '/Volumes/WD_BLACK_8tb/County Clerks/Brazos County Data' -type f | wc -l"
```

---

## 3. Running the Apple Silicon Vision OCR Pipeline

The Mac Mini contains an **Apple Silicon M4 Pro** chip with a dedicated **16-core Apple Neural Engine** and unified memory GPU.

### Hardware Acceleration & Crash Prevention Settings
* **Concurrency**: 10 parallel Apple Vision workers (`MAX_WORKERS = 10`).
* **Throughput**: ~24–28 pages/second (~1,500 pages/min, ~90,000 pages/hour).
* **Worker Recycling (`maxtasksperchild = 50`)**: Child processes terminate and respawn every 50 tasks, completely eliminating native Metal/PyObjC heap accumulation.
* **Minimalist Safety Net (`SAFE_MIN_RAM_GB = 2.0`)**: Reserves only 2.0 GB of memory untouched for macOS kernel and `watchdogd`, unlocking **22.0 GB of the 24 GB RAM** for active work.
* **Dynamic Kernel Memory Pressure Check**: Checks `sysctl kern.memorystatus_vm_pressure_level`. Automatically pauses and flushes garbage collection if pressure reaches WARN ($\ge 2$).

### Start the Pipeline
```bash
cd "/Volumes/WD_BLACK_8tb/County Clerks/Brazos County Data"
./run_brazos_ocr.sh
```

### Monitor Live Progress
```bash
tail -f "/Volumes/WD_BLACK_8tb/County Clerks/Brazos County Data/brazos_ocr.log"
```

---

## 4. Master Sovereignty Runsheet Generation

To generate or enrich the 17-column Master Sovereignty Runsheet workbooks with all newly extracted Texas legal descriptions:

```bash
/Users/ryan/brazos_env/bin/python "/Volumes/WD_BLACK_8tb/County Clerks/Brazos County Data/build_brazos_master_runsheet.py"
```

### 17-Column Harrison County Standard Specifications:
1. `A: INSTRUMENT TYPE`
2. `B: RECORD DATE / FILED DATE`
3. `C: INSTRUMENT DATE`
4. `D: VOLUME`
5. `E: PAGE`
6. `F: INSTRUMENT # / CLERK FILE #`
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
* **Styling**: `Aptos Narrow 10.5pt Bold` row 6 headers (`#1B365D` navy fill, white text), `Calibri 10pt` data rows, freeze pane at row 6 (`ySplit="6"`, `topLeftCell="A7"`).
