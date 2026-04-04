#!/bin/bash
# Set password for Pi user and save the password hash.
# Usage: pipw.sh <user> <password_hash> <boot_dir> <root_dir>
set -euo pipefail

USER="$1"
PW_HASH="$2"
BOOT_DIR="$3"
ROOT_DIR="$4"

# Write userconf.txt for first-boot password setup
echo "${USER}:${PW_HASH}" > "${BOOT_DIR}/userconf.txt"

# Write password.txt so we know a password was set
mkdir -p "${ROOT_DIR}/etc/ssh"
echo "Password set for ${USER}" > "${ROOT_DIR}/etc/ssh/password.txt"
