#!/bin/bash

MAC_MINI_USER="ryan"
MAC_MINI_IP="100.122.169.98"
DEST_DRIVE="/Volumes/WD_BLACK_8tb"
DEST_DIR="$DEST_DRIVE/County_Clerks"
SRC_DIR="/Users/ryanwilson/Documents/County Clerks/Reeves County, TX"

echo "=========================================================="
echo " Reeves County 1:1 Push to Ryan's Mac Mini ($MAC_MINI_USER@$MAC_MINI_IP)"
echo " Target: $DEST_DIR"
echo "=========================================================="

# 1. Test SSH Connection
echo ""
echo "[1/4] Checking connection to Mac Mini..."
if ! ssh -o ConnectTimeout=5 "$MAC_MINI_USER@$MAC_MINI_IP" "echo 'Connected successfully to $(hostname)'"; then
  echo "ERROR: Could not connect to $MAC_MINI_USER@$MAC_MINI_IP via SSH."
  echo "Please check your password or ensure Remote Login is enabled for 'ryan' on the Mac Mini."
  exit 1
fi

# 2. Check if WD_BLACK_8tb is mounted
echo ""
echo "[2/4] Verifying destination drive ($DEST_DRIVE)..."
if ! ssh "$MAC_MINI_USER@$MAC_MINI_IP" "[ -d '$DEST_DRIVE' ]"; then
  echo "WARNING: $DEST_DRIVE does not appear to be mounted on the Mac Mini!"
  echo "Please make sure the WD_BLACK_8tb drive is plugged into the Mac Mini and mounted in Finder."
  echo "Available volumes on Mac Mini:"
  ssh "$MAC_MINI_USER@$MAC_MINI_IP" "ls -l /Volumes"
  exit 1
fi

echo "Drive $DEST_DRIVE found! Creating destination folder..."
ssh "$MAC_MINI_USER@$MAC_MINI_IP" "mkdir -p '$DEST_DIR'"

# 3. Start 1:1 Resumable Transfer
echo ""
echo "[3/4] Starting 1:1 Resumable Transfer (preserving timestamps & permissions)..."
echo "Command: rsync -avP --stats \"$SRC_DIR\" \"$MAC_MINI_USER@$MAC_MINI_IP:$DEST_DIR/\""
echo "Machine will be kept awake with caffeinate throughout the transfer."
echo ""

caffeinate -dis rsync -avP --stats \
  "$SRC_DIR" \
  "$MAC_MINI_USER@$MAC_MINI_IP:$DEST_DIR/"

RSYNC_STATUS=$?

# 4. Verification Check
if [ $RSYNC_STATUS -eq 0 ]; then
  echo ""
  echo "[4/4] Verifying file counts 1:1..."
  SRC_COUNT=$(find "$SRC_DIR" -type f | wc -l)
  DEST_COUNT=$(ssh "$MAC_MINI_USER@$MAC_MINI_IP" "find '$DEST_DIR/Reeves County, TX' -type f | wc -l")
  
  echo "=========================================================="
  echo " TRANSFER COMPLETE!"
  echo " Source Files (MacBook):   $SRC_COUNT"
  echo " Dest Files (Mac Mini):     $DEST_COUNT"
  if [ "$SRC_COUNT" -eq "$DEST_COUNT" ]; then
    echo " RESULT: PERFECT 1:1 MATCH!"
  else
    echo " RESULT: File counts differ. Run this script again to sync any missed files."
  fi
  echo "=========================================================="
  echo ""
  echo "Next Step on the Mac Mini:"
  echo "1. ssh $MAC_MINI_USER@$MAC_MINI_IP (or sit at the Mac Mini)"
  echo "2. cd '$DEST_DIR/Reeves County, TX'"
  echo "3. ./run_reeves_ocr.sh"
else
  echo "Transfer interrupted with exit code $RSYNC_STATUS. Re-run this script to resume."
  exit $RSYNC_STATUS
fi
