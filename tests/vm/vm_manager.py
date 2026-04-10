"""QEMU VM lifecycle management for Ansible testing."""

import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

import paramiko


IMAGES_DIR = Path(__file__).parent / "images"
DEBIAN_CLOUD_URL = "https://cloud.debian.org/images/cloud/{dist}/latest/debian-12-genericcloud-amd64.qcow2"
# rpi-qemu package constants
QEMU_RPI_REPO = "fpgas-online/rpi-qemu"
QEMU_RPI_STATIC_ASSET = "qemu-rpi-static-linux-amd64.tar.gz"
QEMU_RPI_PXEBOOT_DEB = "qemu-rpi-pxeboot"
QEMU_RPI_SYSTEM_BIN = "qemu-rpi-system-aarch64"
QEMU_RPI_PXEBOOT_BIN = "rpi4b-pxeboot.bin"
QEMU_RPI_PXEBOOT_DTB = "rpi4b-pxeboot.dtb"
QEMU_RPI_PXEBOOT_SHARE = Path("/usr/share/qemu-rpi-pxeboot")


def find_qemu_rpi_binary() -> str:
    """Find the qemu-rpi-system-aarch64 binary.

    Checks (in order):
    1. System-installed binary (from qemu-rpi-system-arm APT package)
    2. Static binary downloaded to tests/vm/images/
    """
    # Check system PATH (APT-installed or static binary)
    for name in [QEMU_RPI_SYSTEM_BIN, f"{QEMU_RPI_SYSTEM_BIN}-static"]:
        path = shutil.which(name)
        if path:
            print(f"[qemu-rpi] Using system binary: {path}")
            return path

    # Check for static binary in images dir
    static_bin = IMAGES_DIR / f"{QEMU_RPI_SYSTEM_BIN}-static"
    if static_bin.exists():
        print(f"[qemu-rpi] Using static binary: {static_bin}")
        return str(static_bin)

    raise FileNotFoundError(
        f"{QEMU_RPI_SYSTEM_BIN} not found. Install via:\n"
        f"  APT: echo 'deb [trusted=yes] https://fpgas-online.github.io/rpi-qemu trixie main' "
        f"| sudo tee /etc/apt/sources.list.d/qemu-rpi.list && sudo apt update && "
        f"sudo apt install qemu-rpi-system-arm qemu-rpi-pxeboot\n"
        f"  Or download static binary: gh release download -R {QEMU_RPI_REPO} "
        f"-p '{QEMU_RPI_STATIC_ASSET}' -D {IMAGES_DIR}"
    )


def find_pxeboot_firmware() -> tuple[str, str]:
    """Find the rpi4b-pxeboot.bin and .dtb firmware files.

    Returns (bin_path, dtb_path).

    Checks (in order):
    1. System-installed from qemu-rpi-pxeboot APT package
    2. Downloaded to tests/vm/images/
    """
    # Check system install
    sys_bin = QEMU_RPI_PXEBOOT_SHARE / QEMU_RPI_PXEBOOT_BIN
    sys_dtb = QEMU_RPI_PXEBOOT_SHARE / QEMU_RPI_PXEBOOT_DTB
    if sys_bin.exists() and sys_dtb.exists():
        print(f"[qemu-rpi] Using system pxeboot firmware: {sys_bin}")
        return str(sys_bin), str(sys_dtb)

    # Check images dir
    local_bin = IMAGES_DIR / QEMU_RPI_PXEBOOT_BIN
    local_dtb = IMAGES_DIR / QEMU_RPI_PXEBOOT_DTB
    if local_bin.exists() and local_dtb.exists():
        print(f"[qemu-rpi] Using local pxeboot firmware: {local_bin}")
        return str(local_bin), str(local_dtb)

    raise FileNotFoundError(
        f"PXE boot firmware not found. Install via:\n"
        f"  APT: sudo apt install qemu-rpi-pxeboot\n"
        f"  Or download: gh release download -R {QEMU_RPI_REPO} "
        f"-p '*.deb' -D {IMAGES_DIR}"
    )


def download_qemu_rpi(dest_dir: Path) -> None:
    """Download qemu-rpi static binary and pxeboot firmware from GitHub releases."""
    dest_dir.mkdir(parents=True, exist_ok=True)

    static_bin = dest_dir / f"{QEMU_RPI_SYSTEM_BIN}-static"
    if not static_bin.exists():
        print(f"[qemu-rpi] Downloading static binary from {QEMU_RPI_REPO}...")
        tarball = dest_dir / QEMU_RPI_STATIC_ASSET
        subprocess.run(
            ["gh", "release", "download", "-R", QEMU_RPI_REPO,
             "-p", QEMU_RPI_STATIC_ASSET, "-D", str(dest_dir),
             "--clobber"],
            check=True,
        )
        subprocess.run(
            ["tar", "xzf", str(tarball), "-C", str(dest_dir)],
            check=True,
        )
        tarball.unlink()
        if not static_bin.exists():
            raise FileNotFoundError(f"Expected {static_bin} after extracting tarball")
        static_bin.chmod(0o755)
        print(f"[qemu-rpi] Static binary: {static_bin}")

    pxeboot_bin = dest_dir / QEMU_RPI_PXEBOOT_BIN
    if not pxeboot_bin.exists():
        print(f"[qemu-rpi] Downloading pxeboot firmware from {QEMU_RPI_REPO}...")
        # Download the pxeboot deb and extract the firmware files
        subprocess.run(
            ["gh", "release", "download", "-R", QEMU_RPI_REPO,
             "-p", f"{QEMU_RPI_PXEBOOT_DEB}_*.deb", "-D", str(dest_dir),
             "--clobber"],
            check=True,
        )
        # Find the downloaded deb (filename includes version)
        debs = list(dest_dir.glob(f"{QEMU_RPI_PXEBOOT_DEB}_*.deb"))
        if not debs:
            raise FileNotFoundError("Failed to download qemu-rpi-pxeboot deb")
        deb_path = debs[0]
        # Extract firmware files from the deb
        subprocess.run(
            ["dpkg-deb", "-x", str(deb_path), str(dest_dir / "pxeboot-extract")],
            check=True,
        )
        extract_share = dest_dir / "pxeboot-extract" / "usr" / "share" / "qemu-rpi-pxeboot"
        for fname in [QEMU_RPI_PXEBOOT_BIN, QEMU_RPI_PXEBOOT_DTB]:
            src = extract_share / fname
            dst = dest_dir / fname
            if src.exists():
                shutil.copy2(src, dst)
        # Clean up
        shutil.rmtree(dest_dir / "pxeboot-extract", ignore_errors=True)
        deb_path.unlink(missing_ok=True)

        if not pxeboot_bin.exists():
            raise FileNotFoundError("Failed to extract pxeboot firmware from deb")
        print(f"[qemu-rpi] PXE firmware: {pxeboot_bin}")


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
        memory: int = 2048,
        qemu_bin: str = "",
        pxeboot_bin: str = "",
        pxeboot_dtb: str = "",
        mac: str = "52:54:00:12:34:56",
    ) -> None:
        """Boot the aarch64 Pi VM using patched QEMU raspi4b with PXE boot.

        Uses qemu-rpi-system-aarch64 (patched QEMU with BCM2838 GENET
        ethernet emulation) and qemu-rpi-pxeboot firmware (U-Boot with
        embedded VideoCore PXE boot sequence).

        The firmware autonomously: DHCP from dnsmasq, TFTP probes the same
        files a real RPi 4B VideoCore GPU would request (deadbeef/config.txt,
        kernel8.img, DTB), then boots the kernel.
        """
        cmd = [
            qemu_bin,
            "-M", "raspi4b",
            "-m", str(memory),
            # PXE boot firmware (U-Boot with embedded VideoCore emulation)
            "-kernel", pxeboot_bin,
            "-dtb", pxeboot_dtb,
            # Native GENET ethernet connected to server VLAN
            "-nic", f"socket,connect=:{vlan_port},mac={mac}",
            # Headless — RPi 4B uses PL011 UART (serial0/ttyAMA0)
            "-display", "none",
            "-serial", f"file:{self.serial_log}",
            # NO disk — PXE boot -> kernel -> NFS root
        ]
        print(f"[{self.name}] Booting Pi VM (raspi4b + qemu-rpi GENET + PXE, aarch64 TCG)...")
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
