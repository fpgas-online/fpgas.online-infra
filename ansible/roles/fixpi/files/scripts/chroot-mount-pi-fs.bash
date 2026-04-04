#!/bin/bash
# Mount proc/sys/dev for chroot operations on Pi NFS root, run a command, unmount.
# Usage: chroot-mount-pi-fs.bash <nfs_root> <mount_point> "<command>"
set -euo pipefail

NFS_ROOT="$1"
MOUNT_POINT="$2"
COMMAND="$3"

ROOT_DIR="${NFS_ROOT}/root"

mount --bind /proc "${ROOT_DIR}/proc"
mount --bind /sys "${ROOT_DIR}/sys"
mount --bind /dev "${ROOT_DIR}/dev"
mount --bind /dev/pts "${ROOT_DIR}/dev/pts"

chroot "${ROOT_DIR}" /bin/bash -c "${COMMAND}" || RETVAL=$?

umount "${ROOT_DIR}/dev/pts"
umount "${ROOT_DIR}/dev"
umount "${ROOT_DIR}/sys"
umount "${ROOT_DIR}/proc"

exit ${RETVAL:-0}
