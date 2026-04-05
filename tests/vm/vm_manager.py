"""QEMU VM lifecycle management for Ansible testing."""

import json
import os
import socket
import subprocess
import time
from pathlib import Path

import paramiko


IMAGES_DIR = Path(__file__).parent / "images"
DEBIAN_CLOUD_URL = "https://cloud.debian.org/images/cloud/{dist}/latest/debian-12-genericcloud-amd64.qcow2"
DEBIAN_CLOUD_URL_ARM64 = "https://cloud.debian.org/images/cloud/{dist}/latest/debian-12-genericcloud-arm64.qcow2"


def kvm_available() -> bool:
    """Check if KVM acceleration is available."""
    return os.path.exists("/dev/kvm") and os.access("/dev/kvm", os.R_OK | os.W_OK)


def download_image(url: str, dest: Path) -> Path:
    """Download a cloud image if not already cached."""
    if dest.exists():
        print(f"Using cached image: {dest}")
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {url} -> {dest}")
    subprocess.run(
        ["wget", "-q", "--show-progress", "-O", str(dest), url],
        check=True,
    )
    return dest


def create_overlay(base_image: Path, overlay_path: Path, size: str = "20G") -> Path:
    """Create a qcow2 overlay so the base image stays clean.

    The overlay is resized to `size` (default 20G) to accommodate the RPi OS
    image extraction (~2.7GB) and installed packages.
    """
    subprocess.run(
        [
            "qemu-img", "create", "-f", "qcow2",
            "-b", str(base_image.resolve()), "-F", "qcow2",
            str(overlay_path),
        ],
        check=True,
        capture_output=True,
    )
    # Resize the overlay so the guest filesystem can grow
    subprocess.run(
        ["qemu-img", "resize", str(overlay_path), size],
        check=True,
        capture_output=True,
    )
    return overlay_path


def generate_ssh_keypair(key_path: Path) -> tuple[Path, str]:
    """Generate an ephemeral ed25519 SSH keypair. Returns (private_key_path, public_key_string)."""
    key_path.parent.mkdir(parents=True, exist_ok=True)
    if key_path.exists():
        key_path.unlink()
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-f", str(key_path), "-N", "", "-q"],
        check=True,
    )
    pubkey = key_path.with_suffix(".pub").read_text().strip()
    return key_path, pubkey


class QemuGuestAgent:
    """Communicate with QEMU guest agent over Unix socket."""

    def __init__(self, socket_path: Path):
        self.socket_path = socket_path

    def _send_command(self, command: str, arguments: dict | None = None, timeout: float = 10) -> dict:
        """Send a QGA command and return the response."""
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(str(self.socket_path))
        try:
            msg = {"execute": command}
            if arguments:
                msg["arguments"] = arguments
            sock.sendall(json.dumps(msg).encode() + b"\n")
            # Read response (may need multiple reads)
            data = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
                if b"\n" in data:
                    break
            return json.loads(data.decode().strip())
        finally:
            sock.close()

    def ping(self, timeout: float = 5) -> bool:
        """Check if guest agent is responsive."""
        try:
            self._send_command("guest-ping", timeout=timeout)
            return True
        except (ConnectionRefusedError, FileNotFoundError, socket.timeout, OSError):
            return False

    def get_interfaces(self) -> list[dict]:
        """Get network interface information from guest."""
        result = self._send_command("guest-network-get-interfaces")
        return result.get("return", [])

    def exec_command(self, command: str) -> dict:
        """Execute a command in the guest."""
        result = self._send_command(
            "guest-exec",
            {"path": "/bin/bash", "arg": ["-c", command], "capture-output": True},
        )
        pid = result.get("return", {}).get("pid")
        if pid is None:
            return result
        # Wait for completion
        time.sleep(1)
        status = self._send_command("guest-exec-status", {"pid": pid})
        return status.get("return", {})


class VMManager:
    """Manage a QEMU VM lifecycle."""

    def __init__(self, name: str, workdir: Path):
        self.name = name
        self.workdir = workdir
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.process: subprocess.Popen | None = None
        self.qga_socket = self.workdir / f"{name}-qga.sock"
        self.serial_log = self.workdir / f"{name}-serial.log"
        self.guest_agent = QemuGuestAgent(self.qga_socket)

    def boot_server(
        self,
        overlay: Path,
        seed_iso: Path,
        ssh_port: int = 2222,
        vlan_port: int = 12345,
        memory: int = 2048,
    ) -> None:
        """Boot the x86_64 server VM with two NICs + guest agent."""
        accel = "kvm" if kvm_available() else "tcg"
        cpu = "host" if accel == "kvm" else "max"
        cmd = [
            "qemu-system-x86_64",
            "-machine", f"q35,accel={accel}",
            "-cpu", cpu,
            "-m", str(memory),
            "-smp", "2",
            # Boot disk (overlay on cloud image)
            "-drive", f"file={overlay},format=qcow2,if=virtio",
            # Cloud-init seed ISO
            "-drive", f"file={seed_iso},format=raw,if=virtio",
            # NIC 1: user-mode for SSH from host
            "-netdev", f"user,id=net0,hostfwd=tcp::{ssh_port}-:22",
            "-device", "virtio-net-pci,netdev=net0,mac=52:54:00:aa:bb:01",
            # NIC 2: socket-listen for internal VLAN
            "-netdev", f"socket,id=net1,listen=:{vlan_port}",
            "-device", "virtio-net-pci,netdev=net1,mac=52:54:00:aa:bb:02",
            # Guest agent
            "-device", "virtio-serial",
            "-device", "virtserialport,chardev=qga0,name=org.qemu.guest_agent.0",
            "-chardev", f"socket,path={self.qga_socket},server=on,wait=off,id=qga0",
            # Headless
            "-nographic",
            "-serial", f"file:{self.serial_log}",
        ]
        print(f"[{self.name}] Booting server VM (accel={accel})...")
        self.process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

    def boot_pi(
        self,
        vlan_port: int = 12345,
        memory: int = 1024,
        kernel: str = "",
        initrd: str = "",
        nfs_root: str = "10.21.0.1:/srv/nfs/rpi/bookworm/root",
        cmdline_extra: str = "",
    ) -> None:
        """Boot the aarch64 Pi VM with direct kernel boot from RPi NFS root.

        Uses QEMU -kernel to load the RPi kernel8.img directly (bypassing
        GRUB/UEFI since RPi kernel lacks EFI stub). The kernel + initramfs
        are fetched from the server VM via SSH before boot.
        """
        cmdline = f"root=/dev/nfs nfsroot={nfs_root},nfsvers=3 ro ip=dhcp rootwait console=ttyAMA0,115200 {cmdline_extra}".strip()
        cmd = [
            "qemu-system-aarch64",
            "-machine", "virt,accel=tcg",
            "-cpu", "max",
            "-m", str(memory),
            # Direct kernel boot (RPi kernel from server NFS root)
            "-kernel", kernel,
            "-initrd", initrd,
            "-append", cmdline,
            # Single NIC: socket-connect to server VLAN
            # Use e1000 instead of virtio-net — the RPi initramfs has e1000
            # drivers but not virtio-net drivers
            "-netdev", f"socket,id=lan0,connect=:{vlan_port}",
            "-device", "e1000,netdev=lan0,mac=52:54:00:12:34:56",
            # Guest agent
            "-device", "virtio-serial",
            "-device", "virtserialport,chardev=qga0,name=org.qemu.guest_agent.0",
            "-chardev", f"socket,path={self.qga_socket},server=on,wait=off,id=qga0",
            # Headless, serial to log
            "-nographic",
            "-serial", f"file:{self.serial_log}",
            # NO disk — NFS root only
        ]
        print(f"[{self.name}] Booting Pi VM (diskless PXE, aarch64 TCG)...")
        self.process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

    def wait_for_guest_agent(self, timeout: int = 180) -> bool:
        """Wait for guest agent to become responsive."""
        print(f"[{self.name}] Waiting for guest agent...")
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.guest_agent.ping():
                print(f"[{self.name}] Guest agent ready.")
                return True
            time.sleep(2)
        print(f"[{self.name}] Guest agent timeout after {timeout}s")
        return False

    def wait_for_ssh(
        self,
        host: str = "127.0.0.1",
        port: int = 2222,
        username: str = "debian",
        key_path: Path | None = None,
        timeout: int = 120,
        proxy_jump: str | None = None,
    ) -> paramiko.SSHClient:
        """Wait for SSH to become available and return a connected client."""
        print(f"[{self.name}] Waiting for SSH on {host}:{port}...")
        deadline = time.time() + timeout

        while time.time() < deadline:
            try:
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

                sock = None
                if proxy_jump:
                    # Parse proxy_jump as "user@host:port"
                    proxy_user, proxy_rest = proxy_jump.split("@")
                    proxy_host, proxy_port = proxy_rest.split(":")
                    proxy = paramiko.SSHClient()
                    proxy.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                    proxy.connect(
                        proxy_host, port=int(proxy_port),
                        username=proxy_user, key_filename=str(key_path),
                        timeout=5,
                    )
                    transport = proxy.get_transport()
                    sock = transport.open_channel(
                        "direct-tcpip", (host, port), ("127.0.0.1", 0)
                    )

                client.connect(
                    host, port=port, username=username,
                    key_filename=str(key_path) if key_path else None,
                    timeout=5, auth_timeout=10,
                    sock=sock,
                )
                print(f"[{self.name}] SSH ready.")
                return client
            except (
                paramiko.ssh_exception.NoValidConnectionsError,
                paramiko.ssh_exception.SSHException,
                socket.timeout,
                ConnectionRefusedError,
                OSError,
            ):
                time.sleep(2)

        raise TimeoutError(f"[{self.name}] SSH not ready after {timeout}s")

    def shutdown(self, ssh_client: paramiko.SSHClient | None = None) -> None:
        """Graceful shutdown: SSH -> guest agent -> SIGTERM."""
        if ssh_client:
            try:
                print(f"[{self.name}] Shutting down via SSH...")
                ssh_client.exec_command("sudo shutdown -h now")
                ssh_client.close()
                if self.process:
                    self.process.wait(timeout=30)
                return
            except Exception:
                pass

        if self.guest_agent.ping():
            try:
                print(f"[{self.name}] Shutting down via guest agent...")
                self.guest_agent._send_command("guest-shutdown")
                if self.process:
                    self.process.wait(timeout=30)
                return
            except Exception:
                pass

        if self.process:
            print(f"[{self.name}] Sending SIGTERM...")
            self.process.terminate()
            self.process.wait(timeout=10)

    def cleanup(self) -> None:
        """Remove temporary files (overlays, seed ISOs, keys, sockets)."""
        for pattern in ["*.qcow2", "*.iso", "*.sock", "*-serial.log"]:
            for f in self.workdir.glob(pattern):
                f.unlink(missing_ok=True)

    def is_alive(self) -> bool:
        """Check if the QEMU process is still running."""
        if self.process is None:
            return False
        return self.process.poll() is None
