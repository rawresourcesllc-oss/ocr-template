#!/bin/bash
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PYTHON_BIN="/Users/ryan/brazos_env/bin/python"

if [ ! -f "$PYTHON_BIN" ]; then
  PYTHON_BIN="python3"
fi

echo "=========================================================================="
echo " Brazos County Apple Silicon (Neural Engine / GPU) OCR Pipeline"
echo " 10-Worker High Horsepower Edition with 2.0GB Minimal RAM Reserve"
echo " Directory: $DIR"
echo "=========================================================================="

mkdir -p "$DIR/Runsheets"
caffeinate -dis "$PYTHON_BIN" "$DIR/brazos_apple_vision_crawler.py"
