#!/bin/bash
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
OUT_DIR="$DIR/../Reeves_County_OCR"

echo "=== Compiling Reeves County Apple Vision OCR Engine ==="
clang -O3 "$DIR/reeves_ocr.m" \
  -framework Foundation \
  -framework CoreGraphics \
  -framework CoreText \
  -framework ImageIO \
  -framework Vision \
  -lsqlite3 \
  -o "$DIR/reeves_ocr"

if [ $? -ne 0 ]; then
  echo "Compilation failed! Ensure Xcode Command Line Tools are installed (xcode-select --install)."
  exit 1
fi

echo "=== Starting Multi-Threaded Apple Vision OCR Pipeline ==="
echo "Source: $DIR"
echo "Output: $OUT_DIR"

mkdir -p "$OUT_DIR"
caffeinate -dis "$DIR/reeves_ocr" "$DIR" "$OUT_DIR" | tee -a "$OUT_DIR/ocr_progress.log"
