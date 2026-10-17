#!/usr/bin/env python3
"""
Universal Texas County Clerk Apple Silicon Vision OCR Pipeline
==============================================================
Hardware-accelerated for Apple Silicon (M1/M2/M3/M4 Pro/Max) with Neural Engine/GPU.
Unified multi-format generation:
  1. Searchable Multi-Page PDFs (kCGTextInvisible layer over high-res scans)
  2. Clean Plain Text (.txt) and Structured JSON (.json) with word bounding boxes
  3. Searchable SQLite Database with FTS5 indexing & standardized Texas legal descriptions
  4. Feeds 17-column Master Sovereignty Runsheet Excel workbooks

Crash-Proof Architecture:
  - Worker process recycling (maxtasksperchild = 50) forces kernel to unmap Metal/PyObjC heaps
  - Minimalist dynamic RAM floor (SAFE_MIN_RAM_GB = 2.0) leaves 22.0 GB usable on a 24 GB Mac
  - Pre-batch kernel memory pressure monitoring (sysctl kern.memorystatus_vm_pressure_level)
  - Periodic SQLite WAL checkpoints prevent unbounded disk bloat
"""

import argparse
import gc
import json
import multiprocessing as mp
import os
import re
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import objc
import psutil
import Quartz
import Vision
from Foundation import NSDictionary, NSURL
from PIL import Image

Image.MAX_IMAGE_PIXELS = None

# Default Configuration
DEFAULT_WORKERS = 10
DEFAULT_BATCH_SIZE = 200
DEFAULT_MAX_TASKS_PER_CHILD = 50
DEFAULT_SAFE_MIN_RAM_GB = 2.0
DEFAULT_RESUME_RAM_GB = 3.0
COOPERATIVE_YIELD_SEC = 0.01

STOPWORDS_LOT_BLK = {"NO", "NUMBER", "OR", "AND", "OF", "IN", "THE", "S", "BOLD", "TO", "BEING", "ALL", "PART"}


class TimeoutError(Exception):
    pass


def _alarm_handler(signum, frame):
    raise TimeoutError("OCR timeout")


def get_system_ram_status():
    """Returns (available_ram_gb, pressure_level). Pressure: 1=Normal, 2=Warn, 4=Critical."""
    try:
        vm = psutil.virtual_memory()
        avail_gb = vm.available / (1024 ** 3)
    except Exception:
        try:
            out = subprocess.check_output(["vm_stat"], stderr=subprocess.DEVNULL).decode()
            stats = {}
            for line in out.splitlines():
                parts = line.split(":")
                if len(parts) == 2:
                    val = parts[1].strip().rstrip(".")
                    if val.isdigit():
                        stats[parts[0].strip()] = int(val)
            ps = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 16384
            free_pages = (
                stats.get("Pages free", 0)
                + stats.get("Pages inactive", 0)
                + stats.get("Pages speculative", 0)
                + stats.get("Pages purgeable", 0)
            )
            avail_gb = (free_pages * ps) / (1024 ** 3)
        except Exception:
            avail_gb = 12.0

    try:
        p_out = subprocess.check_output(
            ["sysctl", "-n", "kern.memorystatus_vm_pressure_level"],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        pressure = int(p_out) if p_out.isdigit() else 1
    except Exception:
        pressure = 1

    return avail_gb, pressure


def enforce_ram_safety(safe_floor_gb=DEFAULT_SAFE_MIN_RAM_GB, resume_gb=DEFAULT_RESUME_RAM_GB, logger=None):
    """Pauses pipeline execution if RAM drops below safe floor or pressure rises."""
    avail_gb, pressure = get_system_ram_status()
    if avail_gb < safe_floor_gb or pressure >= 2:
        reason = f"Available RAM ({avail_gb:.2f} GB) < Safe Floor ({safe_floor_gb:.1f} GB)" if avail_gb < safe_floor_gb else f"Kernel Pressure Elevated ({pressure})"
        msg = f"\n[RAM SAFETY GUARD] {reason}. Pausing pipeline to protect system..."
        if logger:
            logger(msg)
        else:
            print(msg, flush=True)
        gc.collect()

        consecutive_ok = 0
        while True:
            time.sleep(3)
            gc.collect()
            avail_gb, pressure = get_system_ram_status()
            if avail_gb >= resume_gb and pressure == 1:
                consecutive_ok += 1
                if consecutive_ok >= 2:
                    resume_msg = f"[RAM SAFETY GUARD] RAM recovered to {avail_gb:.2f} GB (Pressure: {pressure}). Resuming OCR.\n"
                    if logger:
                        logger(resume_msg)
                    else:
                        print(resume_msg, flush=True)
                    break
            else:
                consecutive_ok = 0


def worker_init():
    """Initializes worker processes with lowered niceness and ignores SIGINT."""
    try:
        os.nice(4)
    except Exception:
        pass
    signal.signal(signal.SIGINT, signal.SIG_IGN)


def init_database(db_file):
    """Creates instruments table and FTS5 full-text search virtual table."""
    os.makedirs(os.path.dirname(db_file), exist_ok=True)
    conn = sqlite3.connect(db_file)
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL;")
    cur.execute("PRAGMA synchronous=NORMAL;")
    cur.execute("PRAGMA cache_size = -64000;")
    cur.execute("""
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
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_inst_doc_key ON instruments(doc_key);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_inst_rel_path ON instruments(relative_path);")

    # FTS5 Virtual Table for sub-millisecond keyword searches
    cur.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS instruments_fts USING fts5(
            county,
            file_name,
            doc_key,
            legal_desc,
            full_text,
            content='instruments',
            content_rowid='id'
        );
    """)
    cur.execute("""
        CREATE TRIGGER IF NOT EXISTS instruments_ai AFTER INSERT ON instruments BEGIN
            INSERT INTO instruments_fts(rowid, county, file_name, doc_key, legal_desc, full_text)
            VALUES (new.id, new.county, new.file_name, new.doc_key, new.legal_desc, new.full_text);
        END;
    """)
    conn.commit()
    conn.close()


def parse_texas_legal(text):
    """Extracts standardized Texas Abstract, Survey, Block, Lot, Section, and Acreage."""
    if not text:
        return ""
    clean = " ".join(text.split())
    parts = []

    m_sur = re.search(r"([A-Za-z0-9\.\s]{3,35}\s+(?:SURVEY|SUR|SVY|HEADRIGHT|HR|LEAGUE))\b", clean, re.IGNORECASE)
    if m_sur:
        s = m_sur.group(1).strip()
        if not re.search(r"\b(?:LAND|AERIAL|ENGINEER|COUNTY|ROAD|TOPOGRAPHIC|PRESENTS|DO|DOES|HEREBY|BARGAIN|CONVEY)\b", s, re.IGNORECASE):
            sur = re.sub(r"\s+(?:Survey|Sur|Svy)\b", " Sur", s.title(), flags=re.IGNORECASE)
            parts.append(sur)

    m_abs = re.search(r"(?:ABSTRACT|ABST|ABS|A-)\s*(?:NO\.?\s*)?#?\s*([A-Z]?\s*[-]?\s*\d{1,5})\b", clean, re.IGNORECASE)
    if m_abs:
        num = re.sub(r"[^0-9]", "", m_abs.group(1))
        if num and int(num) > 0:
            parts.append(f"A-{num}")

    m_blk = re.search(r"\b(?:BLOCK|BLK)\s*#?\s*([A-Z0-9]{1,6})\b", clean, re.IGNORECASE)
    if m_blk:
        bval = m_blk.group(1).upper()
        if bval not in STOPWORDS_LOT_BLK:
            parts.append(f"Blk {bval}")

    m_lot = re.search(r"\b(?:LOT|LT)\s*#?\s*([A-Z0-9]{1,6})\b", clean, re.IGNORECASE)
    if m_lot:
        lval = m_lot.group(1).upper()
        if lval not in STOPWORDS_LOT_BLK:
            parts.append(f"Lot {lval}")

    m_sec = re.search(r"\b(?:SEC|SECTION)\s*#?\s*(\d{1,6})\b", clean, re.IGNORECASE)
    if m_sec:
        parts.append(f"Sec {m_sec.group(1)}")

    m_ac = re.search(r"(\d+(?:\.\d+)?)\s*(?:ACRES|ACRE|AC\b)", clean, re.IGNORECASE)
    if m_ac:
        parts.append(f"{m_ac.group(1)} Ac")

    return "; ".join(parts)


def process_instrument(task_args):
    """
    Worker task:
      1. Performs Apple Vision Neural Engine OCR
      2. Generates Searchable PDF with invisible text layer (if requested)
      3. Writes clean .txt and structured .json (if requested)
      4. Standardizes Texas legal description
      5. Returns database insertion record
    """
    filePath, relPath, county, outBaseDir, gen_pdf, gen_text_json = task_args
    fileName = os.path.basename(filePath)
    baseName = os.path.splitext(fileName)[0]
    relDir = os.path.dirname(relPath)

    try:
        signal.signal(signal.SIGALRM, _alarm_handler)
        signal.alarm(15)

        with objc.autorelease_pool():
            fileUrl = NSURL.fileURLWithPath_(str(filePath))
            imgSource = Quartz.CGImageSourceCreateWithURL(fileUrl, None)
            if not imgSource:
                return None

            pageCount = Quartz.CGImageSourceGetCount(imgSource)
            if pageCount == 0:
                return None

            pdfPath = ""
            txtPath = ""
            jsonPath = ""

            if gen_pdf:
                pdfDir = os.path.join(outBaseDir, "Searchable_PDFs", relDir)
                os.makedirs(pdfDir, exist_ok=True)
                pdfPath = os.path.join(pdfDir, f"{baseName}.pdf")

            if gen_text_json:
                txtDir = os.path.join(outBaseDir, "Text_JSON", relDir)
                os.makedirs(txtDir, exist_ok=True)
                txtPath = os.path.join(txtDir, f"{baseName}.txt")
                jsonPath = os.path.join(txtDir, f"{baseName}.json")

            pdfCtx = None
            if gen_pdf:
                pdfUrl = NSURL.fileURLWithPath_(str(pdfPath))
                pdfCtx = Quartz.CGPDFContextCreateWithURL(pdfUrl, None, None)

            fullTextParts = []
            pagesJson = []

            for pageIdx in range(pageCount):
                img = Quartz.CGImageSourceCreateImageAtIndex(imgSource, pageIdx, None)
                if not img:
                    continue

                width = Quartz.CGImageGetWidth(img)
                height = Quartz.CGImageGetHeight(img)
                pageRect = Quartz.CGRectMake(0, 0, width, height)

                if pdfCtx:
                    Quartz.CGPDFContextBeginPage(pdfCtx, {Quartz.kCGPDFContextMediaBox: Quartz.NSValue.valueWithRect_(pageRect)})
                    Quartz.CGContextDrawImage(pdfCtx, pageRect, img)

                # Apple Silicon Vision OCR
                reqHandler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(img, NSDictionary.dictionary())
                req = Vision.VNRecognizeTextRequest.alloc().init()
                req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
                req.setUsesLanguageCorrection_(True)
                req.setRecognitionLanguages_(["en-US"])

                success, err = reqHandler.performRequests_error_([req], None)
                pageLines = []
                linesJson = []

                if success and not err:
                    results = req.results()
                    if results:
                        if pdfCtx:
                            Quartz.CGContextSetTextDrawingMode(pdfCtx, Quartz.kCGTextInvisible)

                        for obs in results:
                            topCand = obs.topCandidates_(1)
                            if not topCand:
                                continue
                            lineStr = str(topCand[0].string())
                            pageLines.append(lineStr)

                            box = obs.boundingBox()
                            # Convert normalized coordinates (0,0 bottom-left in Vision)
                            tx = box.origin.x * width
                            ty = box.origin.y * height
                            tw = box.size.width * width
                            th = box.size.height * height

                            linesJson.append({
                                "text": lineStr,
                                "confidence": float(topCand[0].confidence()),
                                "box": {"x": tx, "y": ty, "w": tw, "h": th},
                            })

                            if pdfCtx:
                                font = Quartz.CTFontCreateWithName("Helvetica", max(10.0, th * 0.8), None)
                                if font:
                                    attrs = {Quartz.kCTFontAttributeName: font}
                                    attrStr = Quartz.CFAttributedStringCreate(None, lineStr, attrs)
                                    if attrStr:
                                        ctLine = Quartz.CTLineCreateWithAttributedString(attrStr)
                                        if ctLine:
                                            Quartz.CGContextSetTextPosition(pdfCtx, tx, ty)
                                            Quartz.CTLineDraw(ctLine, pdfCtx)

                if pdfCtx:
                    Quartz.CGPDFContextEndPage(pdfCtx)

                pageText = "\n".join(pageLines)
                fullTextParts.append(f"--- PAGE {pageIdx + 1} ---\n{pageText}")
                pagesJson.append({
                    "page_index": pageIdx + 1,
                    "width": width,
                    "height": height,
                    "lines": linesJson,
                    "raw_text": pageText,
                })

            if pdfCtx:
                Quartz.CGPDFContextClose(pdfCtx)

            fullText = "\n\n".join(fullTextParts)

            if gen_text_json and txtPath:
                with open(txtPath, "w", encoding="utf-8") as f:
                    f.write(fullText)
                docData = {
                    "county": county,
                    "file_name": fileName,
                    "relative_path": relPath,
                    "total_pages": pageCount,
                    "pages": pagesJson,
                }
                with open(jsonPath, "w", encoding="utf-8") as f:
                    json.dump(docData, f, indent=2)

            legalDesc = parse_texas_legal(fullText)
            docKey = baseName

            return (county, relPath, docKey, fileName, pageCount, pdfPath, txtPath, jsonPath, legalDesc, fullText)

    except Exception:
        return None
    finally:
        signal.alarm(0)


def main():
    parser = argparse.ArgumentParser(description="Universal Texas County Clerk Apple Silicon OCR Pipeline")
    parser.add_argument("--county", required=True, help="County name (e.g. Brazos, Reeves)")
    parser.add_argument("--input-dir", required=True, help="Path to input scanned documents")
    parser.add_argument("--output-dir", required=True, help="Base path for outputs")
    parser.add_argument("--outputs", default="all", help="Output formats: all, or comma-separated pdf,text,db")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help=f"Parallel workers (default: {DEFAULT_WORKERS})")
    parser.add_argument("--ram-floor", type=float, default=DEFAULT_SAFE_MIN_RAM_GB, help=f"Safe RAM reserve in GB (default: {DEFAULT_SAFE_MIN_RAM_GB})")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help=f"Batch size per commit (default: {DEFAULT_BATCH_SIZE})")
    args = parser.parse_args()

    county = args.county
    inDir = os.path.abspath(args.input_dir)
    outDir = os.path.abspath(args.output_dir)
    os.makedirs(outDir, exist_ok=True)

    gen_pdf = "pdf" in args.outputs or args.outputs == "all"
    gen_text_json = "text" in args.outputs or args.outputs == "all"

    dbDir = os.path.join(outDir, "Database")
    os.makedirs(dbDir, exist_ok=True)
    dbPath = os.path.join(dbDir, f"{county.lower().replace(' ', '_')}_records.sqlite")
    logPath = os.path.join(outDir, "ocr_progress.log")

    def log(msg):
        print(msg, flush=True)
        try:
            with open(logPath, "a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except Exception:
            pass

    log("=" * 75)
    log(f"UNIVERSAL TEXAS COUNTY APPLE SILICON OCR ENGINE — {county.upper()} COUNTY")
    log("=" * 75)
    log(f"Input Directory  : {inDir}")
    log(f"Output Directory : {outDir}")
    log(f"Database Target  : {dbPath}")
    log(f"PDF Output       : {'ENABLED' if gen_pdf else 'DISABLED'}")
    log(f"Text/JSON Output : {'ENABLED' if gen_text_json else 'DISABLED'}")
    log(f"Workers / Floor  : {args.workers} Workers | {args.ram_floor:.1f} GB RAM Floor")
    log("=" * 75)

    init_database(dbPath)

    # Load already processed paths
    conn = sqlite3.connect(dbPath)
    cur = conn.cursor()
    cur.execute("SELECT relative_path FROM instruments;")
    donePaths = {row[0] for row in cur.fetchall()}
    cur.execute("SELECT COUNT(*), SUM(CASE WHEN legal_desc != '' THEN 1 ELSE 0 END) FROM instruments;")
    processed_count, legals_found = cur.fetchone()
    processed_count = processed_count or 0
    legals_found = legals_found or 0
    log(f"Existing Records : {processed_count:,} instruments ({legals_found:,} with legal descriptions)")

    # Discover candidate files
    log("Discovering files in input directory...")
    valid_exts = {".tif", ".tiff", ".pdf", ".001", ".002", ".jpg", ".jpeg"}
    pendingTasks = []
    for root, _, files in os.walk(inDir):
        for fn in sorted(files):
            if fn.startswith("."):
                continue
            ext = os.path.splitext(fn)[1].lower()
            if ext in valid_exts:
                full_p = os.path.join(root, fn)
                rel_p = os.path.relpath(full_p, inDir)
                if rel_p not in donePaths:
                    pendingTasks.append((full_p, rel_p, county, outDir, gen_pdf, gen_text_json))

    log(f"Found {len(pendingTasks):,} pending instruments to process.")
    if not pendingTasks:
        log("County is already 100% processed!")
        conn.close()
        return

    t0 = time.time()
    ctx = mp.get_context("spawn")
    with ctx.Pool(
        processes=args.workers,
        initializer=worker_init,
        maxtasksperchild=DEFAULT_MAX_TASKS_PER_CHILD,
    ) as pool:
        for i in range(0, len(pendingTasks), args.batch_size):
            enforce_ram_safety(safe_floor_gb=args.ram_floor, resume_gb=args.ram_floor + 1.0, logger=log)

            batch = pendingTasks[i : i + args.batch_size]
            t_b0 = time.time()

            results = pool.map(process_instrument, batch)
            valid_results = [r for r in results if r is not None]

            cur.executemany("""
                INSERT OR REPLACE INTO instruments
                (county, relative_path, doc_key, file_name, page_count, pdf_path, txt_path, json_path, legal_desc, full_text)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, valid_results)
            conn.commit()

            if processed_count % 1500 == 0:
                cur.execute("PRAGMA wal_checkpoint(PASSIVE);")

            new_legals = sum(1 for r in valid_results if r[8] != "")
            legals_found += new_legals
            processed_count += len(batch)

            dt = time.time() - t_b0
            speed = len(batch) / dt if dt > 0 else 0
            elapsed_m = (time.time() - t0) / 60
            avail_ram, _ = get_system_ram_status()

            status_line = (
                f"[{min(i + len(batch), len(pendingTasks)):,}/{len(pendingTasks):,}] "
                f"DB: {processed_count:,} | +{len(batch)} | "
                f"{speed:.1f} p/s | "
                f"Legals: {legals_found:,} ({(legals_found / max(1, processed_count)) * 100:.1f}%) | "
                f"Avail RAM: {avail_ram:.1f} GB | "
                f"Elapsed: {elapsed_m:.1f}m"
            )
            log(status_line)

            time.sleep(COOPERATIVE_YIELD_SEC)
            gc.collect()

    cur.execute("PRAGMA wal_checkpoint(PASSIVE);")
    conn.close()
    log("\n" + "=" * 75)
    log(f"COUNTY OCR PIPELINE COMPLETED FOR {county.upper()} COUNTY!")
    log("=" * 75)


if __name__ == "__main__":
    main()
