#!/bin/bash
# Extract Raspberry Pi OS image to NFS root directories.
# Usage: img2files.sh <zip_name> <img_name> <dist>
# Run from the cache directory containing the downloaded .img.xz
set -euo pipefail

ZIP_NAME="$1"
IMG_NAME="$2"
DIST="$3"

NFS_ROOT="/srv/nfs/rpi/${DIST}"

if [ ! -f "${IMG_NAME}" ]; then
    echo "Extracting ${ZIP_NAME}..."
    xz -dk "${ZIP_NAME}"
fi

LOOP=$(losetup --find --show --partscan "${IMG_NAME}")
echo "Loop device: ${LOOP}"

sleep 1

mkdir -p /tmp/rpi-boot
mount "${LOOP}p1" /tmp/rpi-boot

mkdir -p /tmp/rpi-root
mount "${LOOP}p2" /tmp/rpi-root

mkdir -p "${NFS_ROOT}/boot"
mkdir -p "${NFS_ROOT}/root"

echo "Syncing boot partition..."
rsync -a /tmp/rpi-boot/ "${NFS_ROOT}/boot/"

echo "Syncing root partition..."
rsync -a /tmp/rpi-root/ "${NFS_ROOT}/root/"

umount /tmp/rpi-boot
umount /tmp/rpi-root
losetup -d "${LOOP}"
rmdir /tmp/rpi-boot /tmp/rpi-root

echo "Done. NFS root at ${NFS_ROOT}"
