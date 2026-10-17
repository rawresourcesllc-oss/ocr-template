# Reeves County, TX — Mac Mini Transfer & OCR Processing Guide

## Overview

This guide details how to transfer the **Reeves County, TX** records (~280 GB, ~595,000 files) from this MacBook Pro directly to **Ryan's Mac Mini** (`ryans-mac-mini`) targeting the **`WD_BLACK_8tb`** external drive, and how to execute the high-accuracy Apple Vision OCR pipeline on the Mac Mini to generate all **3 output formats**:
1. **Searchable Multi-Page PDFs** (high-res scan with invisible selectable text layer).
2. **Clean Plain Text (.txt) and Structured JSON (.json)** per instrument.
3. **Local Searchable SQLite Database (`.sqlite`)** with full-text search indexing (FTS5).

---

## 1. Why Finder Drag-and-Drop is NOT Recommended for 600k Files

Dragging and dropping ~600,000 files over an SMB share in macOS Finder carries serious risks:
* **Silent Aborts & "Error -36"**: Finder often crashes or cancels the entire transfer midway if a single file has a network timeout, leaving you with an unknown partial mess.
* **Timestamp & Metadata Loss**: Finder over SMB frequently resets file modification dates to the current date/time rather than preserving the historical scan date.
* **No Resume**: If your network connection drops for 2 seconds, Finder aborts and cannot resume where it left off.
* **No Integrity Check**: Finder performs zero byte or checksum verification.

---

## 2. The Best 1:1 Methods (Fastest & True Bit-for-Bit Copy)

### Method 1: High-Speed `tar` Stream over Tailscale (Fastest)
Because Reeves County has ~595,000 individual files, streaming via `tar` avoids 600,000 individual network round-trips by sending a single continuous, contiguous data stream at maximum wire speed:

```bash
# 1. Ensure target directory exists on Mac Mini:
ssh ryan@100.122.169.98 "mkdir -p '/Volumes/WD_BLACK_8tb/County_Clerks'"

# 2. Stream at maximum network speed with 100% preserved timestamps & permissions:
caffeinate -dis tar -cf - -C "/Users/ryanwilson/Documents/County Clerks" "Reeves County, TX" | \
  ssh ryan@100.122.169.98 "tar -xpf - -C '/Volumes/WD_BLACK_8tb/County_Clerks/'"
```

### Method 2: Resumable `rsync` over Tailscale (Best for Pausing/Resuming)
If you prefer a progress bar and the ability to pause and resume at any time:

```bash
caffeinate -dis rsync -avP --stats \
  "/Users/ryanwilson/Documents/County Clerks/Reeves County, TX" \
  ryan@100.122.169.98:"/Volumes/WD_BLACK_8tb/County_Clerks/"
```
* `-a` (Archive mode): Preserves exact file modification times, permissions, owners, symlinks, and directory structures 1:1.
* `-P` (Partial/Progress): If the connection is ever interrupted, re-running this command resumes instantly without re-copying existing files.

---

## 3. Post-Transfer 1:1 Integrity Verification
After copying, verify that the file count and byte size match 1:1:

```bash
# Check file count on MacBook:
find "/Users/ryanwilson/Documents/County Clerks/Reeves County, TX" -type f | wc -l

# Check file count on Mac Mini:
ssh ryan@100.122.169.98 "find '/Volumes/WD_BLACK_8tb/County_Clerks/Reeves County, TX' -type f | wc -l"
```

---

## 3. Step 2: Running the 3-Output Apple Vision OCR on the Mac Mini

The Apple Silicon chip in the Mac Mini (M1/M2/M4) contains a dedicated **16-core Apple Neural Engine**, making it significantly faster at deep neural OCR than Intel processors.

### 1. Compile the OCR Engine on the Mac Mini
On the Mac Mini, open Terminal and compile the included OCR source:

```bash
clang -O3 /Volumes/WD_BLACK_8tb/County_Clerks/Reeves\ County,\ TX/reeves_ocr.m \
  -framework Foundation \
  -framework CoreGraphics \
  -framework CoreText \
  -framework ImageIO \
  -framework Vision \
  -lsqlite3 \
  -o /Volumes/WD_BLACK_8tb/County_Clerks/reeves_ocr
```

### 2. Run the OCR Pipeline
Run the worker script under `caffeinate` to prevent the Mac Mini from sleeping:

```bash
caffeinate -dis /Volumes/WD_BLACK_8tb/County_Clerks/reeves_ocr \
  "/Volumes/WD_BLACK_8tb/County_Clerks/Reeves County, TX" \
  "/Volumes/WD_BLACK_8tb/County_Clerks/Reeves_County_OCR"
```

---

## 4. Expected Outputs on `WD_BLACK_8tb`

The OCR engine will automatically generate:
1. **`/Volumes/WD_BLACK_8tb/County_Clerks/Reeves_County_OCR/Searchable_PDFs/`**
   - High-resolution visual scans with an embedded invisible text layer.
   - Openable in Preview, Acrobat, or any viewer with full text selection and `Cmd + F` search.
2. **`/Volumes/WD_BLACK_8tb/County_Clerks/Reeves_County_OCR/Text_JSON/`**
   - `.txt` files containing clean, plain text per document.
   - `.json` files containing page dimensions, bounding boxes, confidence scores, and text.
3. **`/Volumes/WD_BLACK_8tb/County_Clerks/Reeves_County_OCR/Database/county_records.sqlite`**
   - Single SQLite database with an FTS5 virtual table.
   - Run sub-millisecond keyword searches:
     ```bash
     sqlite3 /Volumes/WD_BLACK_8tb/County_Clerks/Reeves_County_OCR/Database/county_records.sqlite \
       "SELECT file_name, substr(full_text, 1, 150) FROM instruments_fts WHERE instruments_fts MATCH 'oil AND lease';"
     ```
