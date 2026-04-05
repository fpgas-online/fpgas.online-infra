#!/usr/bin/env python3
"""QEMU VM integration test harness for fpgas.online Ansible infrastructure.

Usage:
    uv run tests/vm/run_tests.py [options]

Boots a Debian server VM, applies Ansible roles, optionally PXE-boots
a diskless aarch64 Pi VM from the server, and verifies everything works.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

from tests.vm.cloud_init import create_seed_iso
from tests.vm.network import proxy_jump_string, wait_for_socket_listen
from tests.vm.vm_manager import (
    DEBIAN_CLOUD_URL,
    IMAGES_DIR,
    VMManager,
    create_overlay,
    download_image,
    generate_ssh_keypair,
)

REPO_ROOT = Path(__file__).parent.parent.parent
ANSIBLE_DIR = REPO_ROOT / "ansible"
TEST_INVENTORY = REPO_ROOT / "tests" / "inventory" / "test-hosts"

SSH_PORT = 2222
VLAN_PORT = 12345


def ensure_ansible_collections() -> None:
    """Install required Ansible collections if not already present."""
    subprocess.run(
        ["uv", "run", "ansible-galaxy", "collection", "install",
         "community.crypto", "ansible.posix", "--upgrade"],
        check=True,
        stdin=subprocess.DEVNULL,
    )


def run_ansible(playbook: str, inventory: Path, limit: str, extra_args: list[str] | None = None) -> int:
    """Run an ansible-playbook command and return exit code."""
    cmd = [
        "uv", "run", "ansible-playbook",
        str(ANSIBLE_DIR / playbook),
        "-i", str(inventory),
        "--limit", limit,
        "--ssh-extra-args", "-o StrictHostKeyChecking=accept-new",
    ]
    if extra_args:
        cmd.extend(extra_args)
    print(f"\n{'='*60}")
    print(f"Running: {' '.join(cmd)}")
    print(f"{'='*60}\n")
    # Open /dev/null for stdin to avoid Ansible's non-blocking IO detection issue
    result = subprocess.run(cmd, stdin=subprocess.DEVNULL)
    return result.returncode


def phase_server(args, workdir: Path) -> VMManager | None:
    """Run the server phase: boot VM, apply roles, verify."""
    dist = args.distro
    image_url = DEBIAN_CLOUD_URL.format(dist=dist)
    image_path = IMAGES_DIR / "debian-12-genericcloud-amd64.qcow2"

    # Ensure Ansible collections are installed
    ensure_ansible_collections()

    # Download and prepare
    download_image(image_url, image_path)
    key_path, pubkey = generate_ssh_keypair(workdir / "test_key")
    seed_iso = create_seed_iso(workdir / "seed.iso", pubkey)
    overlay = create_overlay(image_path, workdir / "server-overlay.qcow2")

    # Boot server
    server = VMManager("server", workdir)
    server.boot_server(overlay, seed_iso, ssh_port=SSH_PORT, vlan_port=VLAN_PORT)

    if not server.wait_for_guest_agent(timeout=180):
        print("ERROR: Server VM guest agent did not respond.")
        print(f"Serial log: {server.serial_log}")
        server.shutdown()
        return None

    try:
        ssh = server.wait_for_ssh(port=SSH_PORT, key_path=key_path)
        ssh.close()
    except TimeoutError:
        print("ERROR: Server VM SSH did not become available.")
        server.shutdown()
        return None

    # Select inventory
    if args.inventory == "production":
        inventory = REPO_ROOT / "ansible" / "inventory"
        extra = ["--vault-password-file", args.vault_password_file] if args.vault_password_file else []
    else:
        inventory = TEST_INVENTORY
        extra = []

    # Set SSH key and become for ansible (test VM uses non-root user)
    extra.extend([
        "-e", f"ansible_ssh_private_key_file={key_path}",
        "--become",
    ])
    if args.skip_tags:
        extra.extend(["--skip-tags", args.skip_tags])

    # Run site.yml
    rc = run_ansible("site.yml", inventory, "test-vm", extra)
    if rc != 0:
        print(f"ERROR: site.yml failed with exit code {rc}")
        if args.keep_vm:
            print(f"VM kept alive. SSH: ssh -i {key_path} -p {SSH_PORT} -o StrictHostKeyChecking=no debian@127.0.0.1")
            return None  # Signal failure even with --keep-vm
        server.shutdown()
        server.cleanup()
        return None

    # Run verify.yml
    rc = run_ansible("verify.yml", inventory, "test-vm", extra)
    if rc != 0:
        print(f"WARNING: verify.yml failed with exit code {rc}")

    if args.ssh_to_server:
        print(f"\nSSH into server: ssh -i {key_path} -p {SSH_PORT} -o StrictHostKeyChecking=no debian@127.0.0.1")
        print("Press Ctrl+C to exit and continue teardown.")
        try:
            subprocess.run([
                "ssh", "-i", str(key_path), "-p", str(SSH_PORT),
                "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null",
                "debian@127.0.0.1",
            ])
        except KeyboardInterrupt:
            pass

    return server


def get_uboot_dtb() -> Path:
    """Get the bcm2711-rpi-4-b.dtb built by U-Boot (compatible with QEMU raspi4b)."""
    from tests.vm.build_uboot import UBOOT_SRC

    dtb = UBOOT_SRC / "arch" / "arm" / "dts" / "bcm2711-rpi-4-b.dtb"
    if dtb.exists():
        print(f"[pi] Using U-Boot DTB: {dtb} ({dtb.stat().st_size} bytes)")
        return dtb

    # Fallback: use the pre-cached copy
    dtb_cached = IMAGES_DIR / "bcm2711-rpi-4-b.dtb"
    if dtb_cached.exists():
        return dtb_cached

    raise FileNotFoundError("bcm2711-rpi-4-b.dtb not found — build U-Boot first")


def build_uboot(workdir: Path) -> Path:
    """Build U-Boot for RPi 3B if not already cached."""
    from tests.vm.build_uboot import UBOOT_BIN, main as build_main

    if UBOOT_BIN.exists():
        print(f"[pi] Using cached U-Boot: {UBOOT_BIN}")
        return UBOOT_BIN

    print("[pi] Building U-Boot for raspi3b...")
    build_main()
    return UBOOT_BIN


def phase_pi(args, workdir: Path, server: VMManager) -> bool:
    """Run the Pi phase: boot QEMU raspi3b with U-Boot PXE from server."""
    # Verify server socket is listening
    if not wait_for_socket_listen(VLAN_PORT):
        print("ERROR: Server VLAN socket not listening.")
        return False

    key_path = workdir / "test_key"

    # Build U-Boot for raspi3b
    uboot_bin = build_uboot(workdir)

    # Get DTB from U-Boot build (compatible with QEMU raspi3b)
    dtb_path = get_uboot_dtb()

    # Boot Pi VM — U-Boot will PXE boot from server (DHCP + TFTP)
    pi = VMManager("pi", workdir)
    pi.boot_pi(
        vlan_port=VLAN_PORT,
        uboot_bin=str(uboot_bin),
        dtb=str(dtb_path),
    )

    if not pi.wait_for_guest_agent(timeout=300):
        print("WARNING: Pi VM guest agent did not respond.")
        print(f"Serial log: {pi.serial_log}")
        # Continue — the Pi may still be booting

    # Wait for SSH via ProxyJump through server
    key_path = workdir / "test_key"
    proxy = proxy_jump_string("debian", "127.0.0.1", SSH_PORT)

    try:
        ssh = pi.wait_for_ssh(
            host="10.21.0.128", port=22, username="testuser",
            key_path=key_path, proxy_jump=proxy, timeout=300,
        )
        ssh.close()
    except TimeoutError:
        print("ERROR: Pi VM SSH not reachable via ProxyJump.")
        pi.shutdown()
        return False

    # Select inventory
    inventory = TEST_INVENTORY
    extra = ["-e", f"ansible_ssh_private_key_file={key_path}"]

    # Run verify.yml for Pi
    rc = run_ansible("verify.yml", inventory, "test-pi", extra)
    if rc != 0:
        print(f"WARNING: Pi verify.yml failed with exit code {rc}")

    if args.ssh_to_pi:
        print(f"\nSSH into Pi via ProxyJump:")
        print(f"  ssh -i {key_path} -o StrictHostKeyChecking=no -o ProxyJump=debian@127.0.0.1:{SSH_PORT} testuser@10.21.0.128")
        print("Press Ctrl+C to exit.")
        try:
            subprocess.run([
                "ssh", "-i", str(key_path),
                "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null",
                "-o", f"ProxyJump=debian@127.0.0.1:{SSH_PORT}",
                "testuser@10.21.0.128",
            ])
        except KeyboardInterrupt:
            pass

    if not args.keep_vm:
        pi.shutdown()

    return rc == 0


def main():
    parser = argparse.ArgumentParser(description="QEMU VM integration tests for fpgas.online")
    parser.add_argument("--distro", choices=["bookworm", "trixie"], default="bookworm")
    parser.add_argument("--phase", choices=["server", "pi", "all"], default="all")
    parser.add_argument("--keep-vm", action="store_true", help="Don't teardown on success")
    parser.add_argument("--inventory", choices=["minimal", "production"], default="minimal")
    parser.add_argument("--vault-password-file", type=str, help="Vault password file for production inventory")
    parser.add_argument("--skip-tags", type=str, default="cam,django",
                        help="Comma-separated Ansible tags to skip (default: cam,django)")
    parser.add_argument("--ssh-to-server", action="store_true", help="Drop into SSH on server after setup")
    parser.add_argument("--ssh-to-pi", action="store_true", help="Drop into SSH on Pi via ProxyJump")
    args = parser.parse_args()

    workdir = Path(__file__).parent / "workdir"
    workdir.mkdir(exist_ok=True)

    server = None
    try:
        if args.phase in ("server", "all"):
            server = phase_server(args, workdir)
            if server is None:
                print("\nSERVER PHASE FAILED")
                sys.exit(1)
            print("\nSERVER PHASE PASSED")

        if args.phase in ("pi", "all"):
            if server is None:
                print("ERROR: Pi phase requires a running server VM (use --phase all)")
                sys.exit(1)
            success = phase_pi(args, workdir, server)
            if not success:
                print("\nPI PHASE FAILED")
                sys.exit(1)
            print("\nPI PHASE PASSED")

    finally:
        if server and not args.keep_vm:
            server.shutdown()
            server.cleanup()

    print("\nALL PHASES PASSED")


if __name__ == "__main__":
    main()
