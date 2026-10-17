#!/bin/bash
# Universal Apple Silicon County OCR Execution Script

COUNTY="${1:-Brazos}"
INPUT_DIR="${2:-/Volumes/WD_BLACK_8tb/County Clerks/Brazos County Data/BRAZOSCC}"
OUTPUT_DIR="${3:-/Volumes/WD_BLACK_8tb/County Clerks/Brazos County Data}"
WORKERS="${4:-10}"
RAM_FLOOR="${5:-2.0}"

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
PYTHON_BIN="/Users/ryan/brazos_env/bin/python"

if [ ! -f "$PYTHON_BIN" ]; then
  PYTHON_BIN="python3"
fi

echo "=========================================================================="
echo " Universal Apple Silicon (Neural Engine / GPU) OCR Pipeline"
echo " County          : $COUNTY"
echo " Source Directory: $INPUT_DIR"
echo " Output Directory: $OUTPUT_DIR"
echo " Workers         : $WORKERS parallel workers"
echo " Safe RAM Floor  : $RAM_FLOOR GB minimal reserve (unlocking ~22GB)"
echo "=========================================================================="

mkdir -p "$OUTPUT_DIR"
caffeinate -dis "$PYTHON_BIN" "$DIR/universal_county_ocr.py" \
  --county "$COUNTY" \
  --input-dir "$INPUT_DIR" \
  --output-dir "$OUTPUT_DIR" \
  --outputs all \
  --workers "$WORKERS" \
  --ram-floor "$RAM_FLOOR"
