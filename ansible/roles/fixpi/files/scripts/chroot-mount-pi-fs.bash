#!/bin/bash
# Mount proc/sys/dev for chroot operations on Pi NFS root, run a command, unmount.
# Usage: chroot-mount-pi-fs.bash <nfs_root> <mount_point> "<command>"
#
# The whole mount+chroot runs inside a PRIVATE MOUNT NAMESPACE. With the
# previous plain binds, / being shared (systemd default) made the chroot's
# /dev a propagation peer of the host's /dev -- so the cleanup
# `umount .../dev/pts` propagated back and unmounted the HOST's devpts,
# leaving the machine unable to allocate ptys ("PTY allocation request
# failed", sshd "openpty: No such device") until devpts was remounted
# (tweed rebuild B1-10, 2026-08-25). In the private namespace nothing
# propagates to the host and every mount vanishes when the command exits,
# so no explicit unmounts are needed (or possible to get wrong).
set -euo pipefail

NFS_ROOT="$1"
MOUNT_POINT="$2"   # kept for call-compat; unused
COMMAND="$3"

ROOT_DIR="${NFS_ROOT}/root"

exec unshare --mount --propagation private /bin/bash -euo pipefail -c '
ROOT_DIR="$1"
COMMAND="$2"
mount --bind /proc "${ROOT_DIR}/proc"
mount --bind /sys  "${ROOT_DIR}/sys"
mount --bind /dev  "${ROOT_DIR}/dev"
mount --bind /dev/pts "${ROOT_DIR}/dev/pts"
exec chroot "${ROOT_DIR}" /bin/bash -c "${COMMAND}"
' -- "${ROOT_DIR}" "${COMMAND}"
