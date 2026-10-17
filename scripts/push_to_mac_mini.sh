#!/bin/bash
# Universal 1:1 County Data Push to Mac Mini over Tailscale / Network

COUNTY_FOLDER="${1:-Brazos County Data}"
MAC_MINI_USER="${2:-ryan}"
MAC_MINI_IP="${3:-100.122.169.98}"
DEST_DRIVE="${4:-/Volumes/WD_BLACK_8tb}"
DEST_DIR="$DEST_DRIVE/County Clerks"
SRC_BASE="/Users/ryanwilson/Documents/County Clerks"
SRC_DIR="$SRC_BASE/$COUNTY_FOLDER"

echo "=========================================================="
echo " 1:1 County Push to Ryan's Mac Mini ($MAC_MINI_USER@$MAC_MINI_IP)"
echo " County Folder: $COUNTY_FOLDER"
echo " Target Drive : $DEST_DIR"
echo "=========================================================="

# 1. Test SSH Connection
echo ""
echo "[1/4] Checking connection to Mac Mini..."
if ! ssh -o ConnectTimeout=5 "$MAC_MINI_USER@$MAC_MINI_IP" "echo 'Connected successfully to $(hostname)'"; then
  echo "ERROR: Could not connect to $MAC_MINI_USER@$MAC_MINI_IP via SSH."
  exit 1
fi

# 2. Check destination drive
echo ""
echo "[2/4] Verifying destination drive ($DEST_DRIVE)..."
if ! ssh "$MAC_MINI_USER@$MAC_MINI_IP" "[ -d '$DEST_DRIVE' ]"; then
  echo "WARNING: $DEST_DRIVE does not appear to be mounted on the Mac Mini!"
  exit 1
fi

echo "Drive $DEST_DRIVE found! Creating destination folder..."
ssh "$MAC_MINI_USER@$MAC_MINI_IP" "mkdir -p '$DEST_DIR'"

# 3. Resumable Transfer
echo ""
echo "[3/4] Starting 1:1 Resumable Transfer (preserving timestamps & permissions)..."
caffeinate -dis rsync -avP --stats \
  "$SRC_DIR" \
  "$MAC_MINI_USER@$MAC_MINI_IP:$DEST_DIR/"

RSYNC_STATUS=$?

if [ $RSYNC_STATUS -eq 0 ]; then
  echo ""
  echo "[4/4] Verifying file counts 1:1..."
  SRC_COUNT=$(find "$SRC_DIR" -type f | wc -l)
  DEST_COUNT=$(ssh "$MAC_MINI_USER@$MAC_MINI_IP" "find '$DEST_DIR/$COUNTY_FOLDER' -type f | wc -l")
  
  echo "=========================================================="
  echo " TRANSFER COMPLETE!"
  echo " Source Files (MacBook):   $SRC_COUNT"
  echo " Dest Files (Mac Mini):    $DEST_COUNT"
  if [ "$SRC_COUNT" -eq "$DEST_COUNT" ]; then
    echo " RESULT: PERFECT 1:1 MATCH!"
  else
    echo " RESULT: File counts differ. Re-run to sync missed files."
  fi
  echo "=========================================================="
else
  echo "Transfer interrupted with exit code $RSYNC_STATUS."
  exit $RSYNC_STATUS
fi
