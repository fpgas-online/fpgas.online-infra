#!/usr/bin/env python3
"""QEMU VM integration test harness for fpgas.online Ansible infrastructure.

Usage:
    uv run tests/vm/run_tests.py [options]

Boots a Debian server VM, applies Ansible roles, optionally PXE-boots
a diskless aarch64 Pi VM from the server, and verifies everything works.
"""

import argparse
import concurrent.futures
import os
import subprocess
import sys
import time
from pathlib import Path

import paramiko
import yaml

from tests.vm.cloud_init import create_seed_iso
from tests.vm.network import proxy_jump_string
from tests.vm.ssh_auth import openssh_password_attempt, probe
from tests.vm.vswitch import AccessPortSwitch
from tests.vm.vm_manager import (
    DEBIAN_CLOUD_URL,
    DEBIAN_RELEASES,
    IMAGES_DIR,
    VMManager,
    create_overlay,
    download_image,
    download_qemu_rpi,
    find_pxeboot_firmware,
    find_qemu_rpi_binary,
    generate_ssh_keypair,
    open_proxied_socket,
)

REPO_ROOT = Path(__file__).parent.parent.parent
ANSIBLE_DIR = REPO_ROOT / "ansible"
TEST_INVENTORY = REPO_ROOT / "tests" / "inventory" / "test-hosts"
# The dry run of roles/server_user ahead of site.yml (see the playbook).
SERVER_USER_CHECK = Path(__file__).parent / "server_user_check.yml"

SSH_PORT = 2222
# Switch 1, port 1: 2000 + 100*1 + 1 (see ansible/filter_plugins/port_vlans.py).
# Must match tests/inventory/host_vars/test-vm.yml's `switches:` entry.
VLAN = 2101
# The address the per-port DHCP range gives the Pi on that port: test-pi's
# ansible_host in tests/inventory/test-hosts.
PI_ADDRESS = "10.21.1.1"
# The server VM's debian user also gets this password, and cloud-init turns
# sshd password login on for it: the state roles/sshd must take the server
# out of (tests/inventory/host_vars/test-vm.yml sets sshd_pubkey_only).
SERVER_PASSWORD = "fpgas-test-server"


def ensure_ansible_collections() -> None:
    """Install required Ansible collections if not already present."""
    # Pinned versions (requirements.yml): a no-op when they are already
    # installed (CI caches them), no --upgrade re-download on every run.
    subprocess.run(
        ["uv", "run", "ansible-galaxy", "collection", "install",
         "-r", str(Path(__file__).resolve().parents[2] / "requirements.yml")],
        check=True,
        stdin=subprocess.DEVNULL,
    )


def pi_password_from_inventory() -> str:
    """The pi user's password the test inventory provisions (pi_pw)."""
    host_vars = TEST_INVENTORY.parent / "host_vars" / "test-vm.yml"
    with open(host_vars) as f:
        return yaml.safe_load(f)["pi_pw"]


# Accounts an earlier converge gave the static ssh_public_keys: the jump
# account (roles/jump) and an operator (roles/operators). The seed ISO
# creates them already trusting ssh_public_keys_revoked; the converge must
# delete those keys.
REVOKED_KEY_USERS = ("pi", "tim")


def revoked_keys_from_inventory() -> list[str]:
    """The key lines the inventory revokes (ssh_public_keys_revoked)."""
    with open(TEST_INVENTORY.parent / "group_vars" / "all" / "ssh_keys.yml") as f:
        return yaml.safe_load(f).get("ssh_public_keys_revoked") or []


def revoked_keys_check(server: VMManager, key_path: Path, keys: list[str], present: bool) -> bool:
    """Check every REVOKED_KEY_USERS account still holds (present) or no longer holds each revoked key."""
    script = "set -e\n" + "".join(
        f"sudo grep -qF {key.split()[1]} /home/{user}/.ssh/authorized_keys"
        f" && echo '{user}: {key.split()[2]} present' || echo '{user}: {key.split()[2]} absent'\n"
        for user in REVOKED_KEY_USERS for key in keys
    )
    out = server_run(server, key_path, script)
    when = "before" if present else "after"
    print(f"[server] revoked static keys {when} converge:\n{out}")
    want, bad = ("present", "absent") if present else ("absent", "present")
    ok = out.startswith("rc=0\n") and f" {bad}" not in out and out.count(f" {want}") == len(REVOKED_KEY_USERS) * len(keys)
    if not ok:
        print(f"ERROR: the revoked static keys are not all {want} {when} the converge")
    return ok


# tweed's leftover piroot account from the removed roles/nspawn-pi (seeded by
# cloud_init.py's legacy_piroot): roles/operators (operators_retired_accounts)
# must delete the account, its group, home, sudoers file and login shell.
PIROOT_PRESENT_CHECK = """set -ex
getent passwd piroot | grep -qx 'piroot:.*:/home/piroot:/usr/local/bin/chroot-shell'
getent group piroot
sudo test -s /home/piroot/.ssh/authorized_keys
sudo visudo -cf /etc/sudoers.d/piroot
test -x /usr/local/bin/chroot-shell
"""
PIROOT_ABSENT_CHECK = """set -x
rc=0
if getent passwd piroot; then rc=1; fi
if getent group piroot; then rc=1; fi
for f in /home/piroot /etc/sudoers.d/piroot /usr/local/bin/chroot-shell; do
  if sudo test -e "$f"; then echo "$f left"; rc=1; fi
done
exit $rc
"""


def piroot_check(server: VMManager, key_path: Path, present: bool) -> bool:
    """Check the legacy piroot account is all there (present) or all gone."""
    out = server_run(server, key_path, PIROOT_PRESENT_CHECK if present else PIROOT_ABSENT_CHECK)
    when = "before" if present else "after"
    print(f"[server] legacy piroot account {when} converge:\n{out}")
    if not out.startswith("rc=0\n"):
        print(f"ERROR: the legacy piroot account is not {'all there' if present else 'gone'} {when} the converge")
        return False
    return True


def pi_password_login_works(host: str, password: str, key_path: Path, proxy_jump: str) -> bool:
    """Log in to the Pi as `pi` with the shared password, as the web terminal does.

    webssh (roles/wssh) has no key for the Pi: the board page's iframe URL
    carries the password and paramiko authenticates with it. The key login
    above says nothing about that path -- useradd creates the account with
    its password locked, and only fixpi/userconf.yml writing the hash into
    the NFS root makes it usable.
    """
    sock = open_proxied_socket(proxy_jump, key_path, host, 22)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            host, port=22, username="pi", password=password,
            look_for_keys=False, allow_agent=False,
            timeout=5, auth_timeout=10, sock=sock,
        )
    except paramiko.ssh_exception.AuthenticationException as exc:
        print(f"[pi] password login as pi FAILED: {exc}")
        return False
    _in, out, _err = client.exec_command("id -un", timeout=30)
    who = out.read().decode(errors="replace").strip()
    client.close()
    print(f"[pi] password login as pi OK (id -un: {who})")
    return who == "pi"


def run_ansible(playbook: str | Path, inventory: Path, limit: str, extra_args: list[str] | None = None,
                log_path: Path | None = None) -> int:
    """Run an ansible-playbook command and return exit code.

    playbook is a name under ansible/, or a path to a harness playbook
    elsewhere (its roles come from ansible/roles, via ANSIBLE_ROLES_PATH).
    With log_path the output goes to that file instead of stdout: used for a
    playbook that runs alongside another one, printed once it has finished.
    """
    cmd = [
        "uv", "run", "ansible-playbook",
        str(ANSIBLE_DIR / playbook),
        "-i", str(inventory),
        "--limit", limit,
        # Generous connect patience: the TCG-emulated Pi runs the full
        # image service set (fpgas-cam gstreamer, fpgas-tt, pistat) at
        # ~1/20th speed right after boot, and default SSH timeouts caught
        # it mid-thrash ("banner exchange" timeouts in verify-pi).
        "--ssh-extra-args",
        "-o StrictHostKeyChecking=accept-new -o ConnectTimeout=120 -o ServerAliveInterval=15",
        "-e", "ansible_timeout=120",
    ]
    if extra_args:
        cmd.extend(extra_args)
    print(f"\n{'='*60}")
    print(f"Running: {' '.join(cmd)}")
    print(f"{'='*60}\n")
    env = {**os.environ, "ANSIBLE_ROLES_PATH": str(ANSIBLE_DIR / "roles")}
    # Open /dev/null for stdin to avoid Ansible's non-blocking IO detection issue
    if log_path is None:
        return subprocess.run(cmd, stdin=subprocess.DEVNULL, env=env).returncode
    with open(log_path, "w") as log:
        return subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=log,
                              stderr=subprocess.STDOUT, env=env).returncode


def wait_for_pi_boot(pi: VMManager, timeout: int = 300) -> tuple[bool, str | None]:
    """Monitor Pi serial log until the firmware hands off to the kernel.

    Returns (success, pi_ip) where pi_ip is extracted from DHCP output.

    Success is the kernel handoff, not a getty "login:" prompt: the netbooted
    image never prints one on the captured console, so waiting for it burned
    the whole timeout on every run (60 min per VM test) before the SSH wait --
    which is the real userland-readiness check -- answered at once.
    """
    import re

    milestones = [
        ("DHCP", "Firmware got network"),
        ("Loading", "TFTP loading files"),
        ("Booting Linux", "Kernel handoff"),
    ]
    seen = set()
    pi_ip = None
    deadline = time.time() + timeout

    uboot_log = Path(str(pi.serial_log) + ".uboot")

    while time.time() < deadline:
        # Read both serial ports — kernel on serial0, U-Boot on serial1
        content = ""
        if pi.serial_log.exists():
            content += pi.serial_log.read_text(errors="replace")
        if uboot_log.exists():
            content += uboot_log.read_text(errors="replace")
        if content:
            for marker, desc in milestones:
                if marker in content and marker not in seen:
                    seen.add(marker)
                    print(f"[pi] Boot milestone: {desc} ({marker})")
            # Extract Pi's IP from DHCP output
            if pi_ip is None:
                ip_match = re.search(r"our IP address is (\d+\.\d+\.\d+\.\d+)", content)
                if ip_match:
                    pi_ip = ip_match.group(1)
                    print(f"[pi] Pi IP from DHCP: {pi_ip}")
            if "Booting Linux" in content:
                return True, pi_ip
            # The firmware resets and retries forever when TFTP has no
            # kernel for it: fail now instead of at the timeout.
            if content.count("No kernel image found") >= 2:
                print("[pi] ERROR: the firmware found no kernel over TFTP (twice)")
                break
        # Check if QEMU process died
        if not pi.is_alive():
            print("[pi] ERROR: QEMU process exited unexpectedly")
            break
        time.sleep(5)

    # Timeout or process died — print BOTH serial logs' tails for debugging.
    # serial0 (pi.serial_log) is the kernel console; serial1 (.uboot) is the
    # U-Boot/firmware console. On a stall after TFTP the kernel log is where
    # the NFS-root/boot failure shows up, so dump both (the kernel one is
    # frequently the empty/interesting one).
    # NOTE: the "milestones seen" above are substring matches and can false-
    # positive (e.g. "Loading" matches U-Boot's "Loading Environment from
    # FAT", not a TFTP kernel load), so treat them as hints only -- the full
    # dumps below are authoritative.
    print(f"[pi] Boot milestones seen: {[m for m, _ in milestones if m in seen]}")
    dump_pi_serial_logs(pi)
    return False, pi_ip


def dump_pi_serial_logs(pi: VMManager) -> None:
    """Print both Pi serial logs in full (kernel serial0, U-Boot serial1)."""
    for label, path in (
        ("kernel serial0", pi.serial_log),
        ("u-boot serial1", Path(str(pi.serial_log) + ".uboot")),
    ):
        if path.exists():
            text = path.read_text(errors="replace")
            print(f"[pi] ===== FULL {label} ({path.name}), {len(text)} bytes =====")
            for line in text.splitlines():
                print(f"  {line}")
            print(f"[pi] ===== end {label} =====")
        else:
            print(f"[pi] {label} log {path.name} does not exist")


def server_password_login_accepted(key_path: Path) -> bool:
    """Before the converge: the password works, so the check after it means something.

    cloud-init's ssh_pwauth (50-cloud-init.conf, PasswordAuthentication yes)
    and the debian user's password are in force, as a password-enabled
    install of tweed would be.
    """
    result = probe("127.0.0.1", SSH_PORT, "debian", password=SERVER_PASSWORD, key_path=key_path)
    print(f"[server] before converge: sshd offers {result.allowed}, "
          f"password login {'OK' if result.password_ok else 'REFUSED'}, "
          f"key login {'OK' if result.key_ok else 'REFUSED'}")
    return "password" in result.allowed and bool(result.password_ok) and bool(result.key_ok)


def server_pubkey_only(key_path: Path) -> bool:
    """After the converge: sshd offers only publickey; the password is refused, the key is not."""
    result = probe("127.0.0.1", SSH_PORT, "debian", password=SERVER_PASSWORD, key_path=key_path)
    openssh = openssh_password_attempt("127.0.0.1", SSH_PORT, "debian")
    print(f"[server] after converge: sshd offers {result.allowed}, "
          f"password login {'OK' if result.password_ok else 'REFUSED'}, "
          f"key login {'OK' if result.key_ok else 'REFUSED'}")
    print(f"[server] ssh with only a password: {openssh}")
    ok = (result.allowed == ["publickey"] and result.password_ok is False
          and result.key_ok is True and "Permission denied (publickey)." in openssh)
    if not ok:
        print("ERROR: the server must accept only public-key logins (roles/sshd)")
    return ok


def phase_server(args, workdir: Path, switch: AccessPortSwitch) -> VMManager | None:
    """Run the server phase: boot VM, apply roles, verify."""
    dist = args.distro
    num = DEBIAN_RELEASES[dist]
    image_url = DEBIAN_CLOUD_URL.format(dist=dist, num=num)
    image_path = IMAGES_DIR / f"debian-{num}-genericcloud-amd64.qcow2"

    # Ensure Ansible collections are installed
    ensure_ansible_collections()

    # Download and prepare
    download_image(image_url, image_path)
    key_path, pubkey = generate_ssh_keypair(workdir / "test_key")
    revoked_keys = revoked_keys_from_inventory()
    seed_iso = create_seed_iso(workdir / "seed.iso", pubkey, password=SERVER_PASSWORD,
                               revoked_key_users=REVOKED_KEY_USERS, revoked_keys=revoked_keys,
                               legacy_piroot=True)
    overlay = create_overlay(image_path, workdir / "server-overlay.qcow2")

    # Boot server
    server = VMManager("server", workdir)
    server.boot_server(overlay, seed_iso, ssh_port=SSH_PORT, trunk_port=switch.trunk_port)

    # SSH, then cloud-init's first boot finished (its runcmd sets up DNS and
    # the trunk NIC) before any role runs.
    try:
        ssh = server.wait_for_ssh(port=SSH_PORT, key_path=key_path, timeout=300)
        _in, out, _err = ssh.exec_command("cloud-init status --wait", timeout=600)
        status = out.read().decode(errors="replace").strip()
        rc = out.channel.recv_exit_status()
        ssh.close()
    except TimeoutError:
        print("ERROR: Server VM SSH did not become available.")
        print(f"Serial log: {server.serial_log}")
        server.shutdown()
        return None
    print(f"[server] cloud-init: {status} (rc={rc})")
    if rc != 0:
        print(f"ERROR: cloud-init did not finish cleanly. Serial log: {server.serial_log}")
        server.shutdown()
        return None

    if not server_password_login_accepted(key_path):
        print("ERROR: the fresh server VM does not accept its password, so the "
              "public-key-only check after the converge would prove nothing.")
        server.shutdown()
        return None

    if revoked_keys and not revoked_keys_check(server, key_path, revoked_keys, present=True):
        server.shutdown()
        return None

    if not piroot_check(server, key_path, present=True):
        server.shutdown()
        return None

    # Select inventory
    if args.inventory == "production":
        inventory = REPO_ROOT / "ansible" / "inventory"
        extra = ["--vault-password-file", args.vault_password_file] if args.vault_password_file else []
    else:
        inventory = TEST_INVENTORY
        extra = []

    # The server pulls the prebuilt NFS root (issue #34): CI builds the
    # image from the same checkout in the nfsroot job and passes its ref
    # here, so the VM test exercises the production pull path with the
    # PR's own roles baked in.
    extra.extend(["-e", f"img_nfsroot_image={args.nfsroot_image}"])

    # The key and become come from the inventory and ansible.cfg, as in
    # production (tests/inventory/group_vars/all/controller.yml names the
    # key generated above).

    # A check-mode run of the server account role first: the rename is
    # pending, and --check must report it rather than fail (and change
    # nothing -- the rename check after site.yml proves the real run did
    # the whole job). A playbook of that one role, not a tagged web.yml
    # (issue #157): see tests/vm/server_user_check.yml.
    if args.inventory != "production":
        rc = run_ansible(SERVER_USER_CHECK, inventory, "test-vm", extra + ["--check"])
        if rc != 0 or not check_server_user_untouched(server, key_path):
            print(f"ERROR: the check-mode server_user run failed (rc={rc}) or changed the host")
            if not args.keep_vm:
                server.shutdown()
                server.cleanup()
            return None

    # Run site.yml (server roles; pulls and site-layers the prebuilt NFS root)
    rc = run_ansible("site.yml", inventory, "test-vm", extra)
    if rc != 0:
        print(f"ERROR: site.yml failed with exit code {rc}")
        if args.keep_vm:
            print(f"VM kept alive. SSH: ssh -i {key_path} -p {SSH_PORT} -o StrictHostKeyChecking=no debian@127.0.0.1")
            return None
        server.shutdown()
        server.cleanup()
        return None

    # roles/sshd has reloaded sshd by the end of its play: the running daemon,
    # not just its configuration, must now refuse the password.
    if not server_pubkey_only(key_path):
        if not args.keep_vm:
            server.shutdown()
            server.cleanup()
        return None

    if revoked_keys and not revoked_keys_check(server, key_path, revoked_keys, present=False):
        if not args.keep_vm:
            server.shutdown()
            server.cleanup()
        return None

    if not piroot_check(server, key_path, present=False):
        if not args.keep_vm:
            server.shutdown()
            server.cleanup()
        return None

    if args.inventory != "production" and not check_server_user_rename(server, key_path):
        if not args.keep_vm:
            server.shutdown()
            server.cleanup()
        return None

    # EXPERIMENT (#172, branch exp-172 only)
    if os.environ.get("EXP_COLLECTOR") == "c":
        print("[exp] masking fpgas-verify in the NFS root:\n" + server_run(
            server, key_path,
            "set -x; for r in /srv/nfs/rpi/bookworm/root /srv/nfs/rpi/bookworm; do "
            "[ -d $r/etc/systemd ] && sudo systemctl --root=$r mask fpgas-verify.service; done; "
            "ls -l /srv/nfs/rpi/bookworm/root/etc/systemd/system/fpgas-verify.service || true"))

    server.ansible_inventory = inventory
    server.ansible_extra = extra
    return server


# The test inventory renames the VM's pre-existing server account (created
# by tests/vm/cloud_init.py under the old name, with a keypair tagged
# "pre-rename" and a service running as it) to user_name, as tweed's
# videoteam became admin. After site.yml: the old account is gone, the new
# one kept the old keypair, the Pi NFS root trusts that key (fixpi copies
# it), the service came back up as the new name, the account has
# passwordless sudo (server_user_sudo), its authorized_keys lost the
# unmanaged stale key, and its subuid/subgid ranges were renamed.
SERVER_USER_OLD = "testuser"
SERVER_USER_NEW = "admin"
SERVER_USER_RENAME_CHECK = f"""set -ex
if getent passwd {SERVER_USER_OLD}; then echo "{SERVER_USER_OLD} still exists"; exit 1; fi
getent passwd {SERVER_USER_NEW} | grep -q ':/home/{SERVER_USER_NEW}:'
pub=$(sudo cat /home/{SERVER_USER_NEW}/.ssh/id_rsa.pub)
case "$pub" in *" pre-rename") ;; *) echo "keypair was regenerated: $pub"; exit 1;; esac
sudo grep -qxF "$pub" /srv/nfs/rpi/bookworm/root/home/pi/.ssh/authorized_keys
systemctl is-active server-user-probe.service
test "$(ps -o user= -p "$(systemctl show -p MainPID --value server-user-probe.service)")" = {SERVER_USER_NEW}
test "$(sudo -u {SERVER_USER_NEW} sudo -n id -un)" = root
if sudo grep -q stale-key /home/{SERVER_USER_NEW}/.ssh/authorized_keys; then echo "stale key kept"; exit 1; fi
sudo grep -q . /home/{SERVER_USER_NEW}/.ssh/authorized_keys
grep -qx '{SERVER_USER_NEW}:165536:65536' /etc/subuid
grep -qx '{SERVER_USER_NEW}:165536:65536' /etc/subgid
if grep -q '^{SERVER_USER_OLD}:' /etc/subuid /etc/subgid; then echo "old subid range left"; exit 1; fi
"""


SERVER_USER_UNTOUCHED_CHECK = f"""set -ex
getent passwd {SERVER_USER_OLD}
if getent passwd {SERVER_USER_NEW}; then echo "check mode created {SERVER_USER_NEW}"; exit 1; fi
systemctl is-active server-user-probe.service
sudo grep -q stale-key /home/{SERVER_USER_OLD}/.ssh/authorized_keys
"""


def check_server_user_untouched(server: VMManager, key_path: Path) -> bool:
    """Check a --check run left the pending rename alone."""
    out = server_run(server, key_path, SERVER_USER_UNTOUCHED_CHECK)
    print(f"[server] check-mode server account check:\n{out}")
    return out.startswith("rc=0\n")


def check_server_user_rename(server: VMManager, key_path: Path) -> bool:
    """Check roles/server_user renamed the VM's old server account in place."""
    out = server_run(server, key_path, SERVER_USER_RENAME_CHECK)
    print(f"[server] server account rename check:\n{out}")
    if not out.startswith("rc=0\n"):
        print("ERROR: the server account was not renamed in place")
        return False
    return True


def verify_server(args, server: VMManager, log_path: Path | None = None) -> bool:
    """Run verify-server.yml against the converged server VM."""
    rc = run_ansible("verify-server.yml", server.ansible_inventory, "test-vm", server.ansible_extra,
                     log_path=log_path)
    if rc != 0:
        print(f"ERROR: verify-server.yml failed with exit code {rc}")
        return False
    key_path = server.workdir / "test_key"
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
    return True


def ensure_qemu_rpi() -> tuple[str, str, str]:
    """Ensure qemu-rpi packages are available. Returns (qemu_bin, pxeboot_bin, pxeboot_dtb).

    Tries system-installed packages first, then falls back to downloading
    from GitHub releases.
    """
    try:
        qemu_bin = find_qemu_rpi_binary()
    except FileNotFoundError:
        print("[pi] qemu-rpi not found system-wide, downloading...")
        download_qemu_rpi(IMAGES_DIR)
        qemu_bin = find_qemu_rpi_binary()

    try:
        pxeboot_bin, pxeboot_dtb = find_pxeboot_firmware()
    except FileNotFoundError:
        print("[pi] pxeboot firmware not found, downloading...")
        download_qemu_rpi(IMAGES_DIR)
        pxeboot_bin, pxeboot_dtb = find_pxeboot_firmware()

    return qemu_bin, pxeboot_bin, pxeboot_dtb


def server_run(server: VMManager, key_path: Path, cmd: str, timeout: int = 120) -> str:
    """Run a shell command on the server VM; return rc + stdout + stderr."""
    ssh = server.wait_for_ssh(port=SSH_PORT, key_path=key_path)
    try:
        _in, out, err = ssh.exec_command(cmd, timeout=timeout)
        rc = out.channel.recv_exit_status()
        return (f"rc={rc}\n{out.read().decode(errors='replace')}"
                f"{err.read().decode(errors='replace')}")
    finally:
        ssh.close()


# The server's view of the Pi, printed when verify-pi fails: neighbour entry,
# ping, VLAN counters, sockets, dnsmasq/nfs journal. It tells a Pi that went
# silent (qemu-rpi's GENET TX freeze, fixed in rpi-qemu#16) from one that
# answers but fails a check.
SERVER_NET_DIAG = (
    "set -x; ip neigh show 10.21.1.1; ping -c 3 -W 2 10.21.1.1; "
    "ip neigh show 10.21.1.1; ip -s link show v2101; ip -4 addr show v2101; "
    "ss -tni dst 10.21.1.1; "
    "sudo journalctl --no-pager -n 40 -u dnsmasq -u nfs-server; "
    "sudo dmesg | tail -n 40"
)


def start_pi(workdir: Path, switch: AccessPortSwitch) -> VMManager | None:
    """Power on the virtual Pi: it netboots from the server on its own.

    Started as soon as site.yml has converged -- the point at which a real
    deploy's boards are PoE-cycled -- so the TCG boot runs alongside
    verify-server instead of after it.
    """
    qemu_bin, pxeboot_bin, pxeboot_dtb = ensure_qemu_rpi()
    pi = VMManager("pi", workdir)
    pi.boot_pi(
        access_port=switch.access_port,
        qemu_bin=qemu_bin,
        pxeboot_bin=pxeboot_bin,
        pxeboot_dtb=pxeboot_dtb,
    )
    time.sleep(2)
    if not pi.is_alive():
        print(f"ERROR: Pi QEMU process exited with code {pi.process.returncode if pi.process else 'unknown'}")
        if pi.qemu_log.exists():
            print(f"QEMU output:\n{pi.qemu_log.read_text(errors='replace')}")
        return None
    return pi


def phase_pi(args, workdir: Path, server: VMManager, pi: VMManager) -> bool:
    """Run the Pi phase: boot QEMU raspi4b with PXE from server.

    Uses qemu-rpi (patched QEMU with GENET ethernet) and qemu-rpi-pxeboot
    firmware (U-Boot with embedded VideoCore PXE sequence). The firmware
    autonomously does DHCP + TFTP from the server's dnsmasq, loads the
    RPi kernel and DTB, and boots into the NFS root.

    The Pi's NIC connects to the vswitch's access port; the server VM is
    already connected to the trunk port (see phase_server), so no separate
    "is the socket listening" wait is needed here -- the vswitch itself
    started listening on both ports before either VM booted (see main()).
    """
    key_path = workdir / "test_key"

    # Monitor serial log for boot milestones. TFTP and the kernel handoff
    # take about a minute under TCG; ten is a hung boot, not a slow one.
    booted, pi_ip = wait_for_pi_boot(pi, timeout=600)
    if not booted:
        print("ERROR: the Pi firmware did not hand off to the kernel.")
        return False

    # The per-port DHCP range gives the Pi on switch 1 port 1 10.21.1.1
    # (ansible/filter_plugins/port_vlans.py), which is test-pi's address in
    # tests/inventory/test-hosts. Anything else is an addressing bug, not a
    # value to paper over (a -e ansible_host override also retargeted every
    # task verify-pi delegates to the server).
    pi_host = PI_ADDRESS
    if pi_ip != PI_ADDRESS:
        print(f"ERROR: the Pi got {pi_ip} from DHCP, expected {PI_ADDRESS} (switch 1 port 1)")
        dump_pi_serial_logs(pi)
        return False
    print(f"[pi] Pi IP: {pi_host}")

    # Wait for SSH via ProxyJump through server
    proxy = proxy_jump_string("debian", "127.0.0.1", SSH_PORT)

    # SSH is the userland-readiness check (the boot wait above ends at the
    # kernel handoff). The userland boot takes 1-2 min under TCG.
    try:
        ssh = pi.wait_for_ssh(
            host=pi_host, port=22, username="pi",
            key_path=key_path, proxy_jump=proxy, timeout=600,
        )
        ssh.close()
    except TimeoutError:
        print("ERROR: Pi VM SSH not reachable via ProxyJump.")
        dump_pi_serial_logs(pi)
        pi.shutdown()
        return False

    # The web terminal's login path (password, no key; see the docstring),
    # checked alongside verify-pi: both only read, and each SSH handshake
    # with the emulated Pi takes tens of seconds.
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    password_ok = pool.submit(pi_password_login_works, pi_host, pi_password_from_inventory(),
                              key_path, proxy)

    # Run verify-pi.yml against the running Pi (test-pi in the inventory).
    inventory = TEST_INVENTORY
    rc = run_ansible("verify-pi.yml", inventory, "test-pi",
                     ["-e", f"exp_collector={os.environ.get('EXP_COLLECTOR', 'a')}"])
    if not password_ok.result():
        print("ERROR: the pi user's password login failed -- the web terminal cannot log in.")
        rc = rc or 1
    pool.shutdown()
    if rc != 0:
        print(f"ERROR: verify-pi.yml failed with exit code {rc}")
        print("[server] network view of the Pi at failure:\n"
              + server_run(server, key_path, SERVER_NET_DIAG))
        text = pi.serial_log.read_text(errors="replace") if pi.serial_log.exists() else ""
        print(f"[pi] ===== last 200 lines of kernel serial0 ({len(text)} bytes) =====")
        for line in text.splitlines()[-200:]:
            print(f"  {line}")
        print("[pi] ===== end =====")

    if args.ssh_to_pi:
        print(f"\nSSH into Pi via ProxyJump:")
        print(f"  ssh -i {key_path} -o StrictHostKeyChecking=no -o ProxyJump=debian@127.0.0.1:{SSH_PORT} pi@{pi_host}")
        print("Press Ctrl+C to exit.")
        try:
            subprocess.run([
                "ssh", "-i", str(key_path),
                "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null",
                "-o", f"ProxyJump=debian@127.0.0.1:{SSH_PORT}",
                f"pi@{pi_host}",
            ])
        except KeyboardInterrupt:
            pass

    if not args.keep_vm:
        pi.shutdown()

    return rc == 0


def main():
    parser = argparse.ArgumentParser(description="QEMU VM integration tests for fpgas.online")
    # tweed runs Debian 13 (trixie): test the server OS production runs.
    parser.add_argument("--distro", choices=["bookworm", "trixie"], default="trixie")
    parser.add_argument("--phase", choices=["server", "all"], default="all")
    parser.add_argument("--nfsroot-image", type=str, required=True,
                        help="GHCR ref of the prebuilt Pi NFS root the server pulls "
                             "(e.g. ghcr.io/fpgas-online/nfsroot:bookworm-armhf); "
                             "CI passes the image built from the same checkout")
    parser.add_argument("--keep-vm", action="store_true", help="Don't teardown on success")
    parser.add_argument("--inventory", choices=["minimal", "production"], default="minimal")
    parser.add_argument("--vault-password-file", type=str, help="Vault password file for production inventory")
    parser.add_argument("--ssh-to-server", action="store_true", help="Drop into SSH on server after setup")
    parser.add_argument("--ssh-to-pi", action="store_true", help="Drop into SSH on Pi via ProxyJump")
    args = parser.parse_args()
    # Line-buffered even into a pipe, so CI logs carry true timestamps.
    sys.stdout.reconfigure(line_buffering=True)

    workdir = Path(__file__).parent / "workdir"
    workdir.mkdir(exist_ok=True)

    # The vswitch must be listening on both ports before either VM starts
    # dialing out to it (QEMU socket netdevs with connect= dial once at
    # boot), so it's started here, ahead of both phases, and stopped once
    # both VMs are done with it.
    switch = AccessPortSwitch(VLAN)
    switch.start()

    server = None
    pi = None
    try:
        server = phase_server(args, workdir, switch)
        if server is None:
            print("\nSERVER PHASE FAILED")
            sys.exit(1)
        if args.phase != "all":
            if not verify_server(args, server):
                print("\nSERVER PHASE FAILED")
                sys.exit(1)
            print("\nSERVER PHASE PASSED")
        else:
            # The server is converged: power the Pi on now -- the point at
            # which a real deploy's boards are PoE-cycled -- and verify both
            # sides at once. verify-server only reads, so it runs in the
            # background (output printed when it ends) while the Pi netboots
            # and verify-pi runs against it.
            pi = start_pi(workdir, switch)
            if pi is None:
                print("\nPI PHASE FAILED")
                sys.exit(1)
            server_log = workdir / "verify-server.log"
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                server_ok = pool.submit(verify_server, args, server, server_log)
                pi_ok = phase_pi(args, workdir, server, pi)
                server_passed = server_ok.result()
            print(f"\n{'=' * 60}\nverify-server.yml output (ran alongside the Pi phase)\n{'=' * 60}")
            print(server_log.read_text(errors="replace"))
            print(f"\nSERVER PHASE {'PASSED' if server_passed else 'FAILED'}")
            print(f"PI PHASE {'PASSED' if pi_ok else 'FAILED'}")
            if not (server_passed and pi_ok):
                sys.exit(1)

    finally:
        if pi is not None and pi.is_alive() and not args.keep_vm:
            pi.shutdown()
        if server and not args.keep_vm:
            server.shutdown()
            server.cleanup()
        switch.stop()

    print("\nALL PHASES PASSED")


if __name__ == "__main__":
    main()
