"""Generate cloud-init seed ISOs for QEMU VMs."""

import json
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path


def create_seed_iso(
    output_path: Path,
    ssh_pubkey: str,
    hostname: str = "test-vm",
    eth_local_mac: str = "52:54:00:aa:bb:02",
    eth_local_ip: str = "10.21.0.1/24",
    legacy_server_user: str = "testuser",
    password: str | None = None,
    revoked_key_users: Sequence[str] = (),
    revoked_keys: Sequence[str] = (),
    legacy_piroot: bool = False,
) -> Path:
    """Create a cloud-init NoCloud seed ISO.

    Configures the VM's user and SSH key, and brings up the second NIC
    (the VLAN trunk) for the roles to configure.

    It also gives the VM a server account under an OLD name
    (legacy_server_user), the way tweed had `videoteam` before it became
    `admin`: a keypair and a running service that runs as it. The test
    inventory renames it (server_user_rename_from), so every run exercises
    roles/server_user's in-place rename; run_tests.py checks the result.

    With a password the user can also log in with it, and cloud-init turns
    sshd password login on (ssh_pwauth writes `PasswordAuthentication yes`
    to sshd_config.d/50-cloud-init.conf): the drop-in roles/sshd writes must
    win over it, and the harness proves the password is then refused.

    Each of revoked_key_users is created already trusting revoked_keys (the
    inventory's ssh_public_keys_revoked), as the accounts on tweed an earlier
    converge gave them: the roles must delete them (run_tests.py checks the
    keys are there before the converge and gone after it).

    With legacy_piroot the VM also gets tweed's leftover piroot account as
    the removed roles/nspawn-pi made it: /usr/local/bin/chroot-shell as its
    login shell, /etc/sudoers.d/piroot and an authorized_keys. roles/operators
    must delete all of it (run_tests.py checks before and after).
    """
    password_auth = (
        f"    lock_passwd: false\n    plain_text_passwd: {password}\n"
        if password else ""
    )
    # Only with a password: without one the image default stays in force.
    ssh_pwauth = "ssh_pwauth: true\n" if password else ""
    # JSON strings are YAML double-quoted scalars: a key line's " # ..."
    # comment must not be read as a YAML comment.
    revoked_users = "".join(
        f"  - name: {user}\n    shell: /bin/bash\n    lock_passwd: true\n"
        "    ssh_authorized_keys:\n"
        + "".join(f"      - {json.dumps(key)}\n" for key in revoked_keys)
        for user in revoked_key_users
    ) if revoked_keys else ""
    piroot_user = (
        "  - name: piroot\n    shell: /usr/local/bin/chroot-shell\n    lock_passwd: true\n"
        "    ssh_authorized_keys:\n      - ssh-ed25519 "
        "AAAAC3NzaC1lZDI1NTE5AAAAIHBpcm9vdHBpcm9vdHBpcm9vdHBpcm9vdHBpcm9vdA piroot-key\n"
    ) if legacy_piroot else ""
    # write_files runs before users-groups, so the shell exists when the
    # account is created.
    piroot_files = """  - path: /usr/local/bin/chroot-shell
    permissions: '0755'
    content: |
      #!/bin/bash
      exec sudo /usr/sbin/chroot /srv/nfs/rpi/bookworm/root /bin/bash "$@"
  - path: /etc/sudoers.d/piroot
    permissions: '0440'
    content: |
      piroot ALL=NOPASSWD: /usr/sbin/chroot
""" if legacy_piroot else ""
    user_data = f"""#cloud-config
hostname: {hostname}
manage_etc_hosts: true

users:
  - name: debian
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
    ssh_authorized_keys:
      - {ssh_pubkey}
{password_auth}  - name: {legacy_server_user}
    shell: /bin/bash
    lock_passwd: true
{revoked_users}{piroot_user}
{ssh_pwauth}
# Stands in for the app servers that run as the account on tweed
# (gunicorn, daphne, ...): the rename must stop it, rewrite it, restart it.
write_files:
  - path: /etc/systemd/system/server-user-probe.service
    content: |
      [Unit]
      Description=Runs as the pre-rename server account (roles/server_user test)
      [Service]
      User={legacy_server_user}
      Group={legacy_server_user}
      ExecStart=/bin/sleep infinity
      [Install]
      WantedBy=multi-user.target
{piroot_files}
# No packages: the cloud image ships python3, and the roles install what
# they need themselves (site installs git for its pip installs), as on a
# fresh tweed. Installing them here was a minute of apt on every boot.

runcmd:
  # The pre-rename keypair, tagged so the check can tell it from a new one.
  - install -d -m 0700 -o {legacy_server_user} -g {legacy_server_user} /home/{legacy_server_user}/.ssh
  - runuser -u {legacy_server_user} -- ssh-keygen -q -t rsa -N "" -C pre-rename -f /home/{legacy_server_user}/.ssh/id_rsa
  # A key nobody manages, carried along by the rename: the account's
  # authorized_keys is exclusive, so it must be gone afterwards.
  - echo "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHN0YWxlc3RhbGVzdGFsZXN0YWxlc3RhbGVzdGFsZQ stale-key" > /home/{legacy_server_user}/.ssh/authorized_keys
  - chown {legacy_server_user}:{legacy_server_user} /home/{legacy_server_user}/.ssh/authorized_keys
  # A subordinate id range, like tweed's videoteam:165536:65536 (replaced,
  # not added to, in case useradd already gave the account one).
  - sed -i '/^{legacy_server_user}:/d' /etc/subuid /etc/subgid
  - echo "{legacy_server_user}:165536:65536" >> /etc/subuid
  - echo "{legacy_server_user}:165536:65536" >> /etc/subgid
  - systemctl daemon-reload
  - systemctl enable --now server-user-probe.service
  - systemctl disable --now systemd-resolved
  - rm -f /etc/resolv.conf
  - echo "nameserver 8.8.8.8" > /etc/resolv.conf
  # The internal NIC (enp0s3) is the per-port VLAN trunk. It must be owned
  # SOLELY by systemd-networkd via the vlan_ports role's 30-eth-local.network
  # (which sets its address AND declares VLAN=v2101...), mirroring tweed.
  # Do NOT pre-configure it here: an ifupdown stanza or a lower-numbered
  # /etc/systemd/network/10-enp0s3.network would win over the role's file and
  # its VLAN= directives would be ignored, so the v* interfaces never appear.
  - systemctl enable --now systemd-networkd
  - ip link set enp0s3 up
  - mkdir -p /etc/letsencrypt/live/test.fpgas.online
  - openssl req -x509 -newkey rsa:2048 -keyout /etc/letsencrypt/live/test.fpgas.online/privkey.pem -out /etc/letsencrypt/live/test.fpgas.online/fullchain.pem -days 1 -nodes -subj "/CN=test.fpgas.online"

package_update: false
package_upgrade: false
"""

    meta_data = f"""instance-id: {hostname}
local-hostname: {hostname}
"""

    with tempfile.TemporaryDirectory() as tmpdir:
        ud_path = Path(tmpdir) / "user-data"
        md_path = Path(tmpdir) / "meta-data"
        ud_path.write_text(user_data)
        md_path.write_text(meta_data)
        nc_path = Path(tmpdir) / "network-config"
        # First-boot uplink: DHCP on the kernel's name for the NIC, as a
        # fresh install's own config would be (tweed's d-i install: ifupdown
        # on its enpXsY name). Matched by NAME, not by MAC (cloud-init's
        # default): the netif role renames the NIC to eth-uplink and gives it
        # tweed's static networkd config, and this must then stop matching.
        nc_path.write_text(
            "version: 2\n"
            "ethernets:\n"
            "  enp0s2:\n"
            "    dhcp4: true\n"
            # netplan otherwise makes systemd-networkd-wait-online wait for
            # enp0s2 -- which after the rename never appears: a 2-minute
            # stall on the reboot (tweed's installer config has no such hook).
            "    optional: true\n"
        )

        subprocess.run(
            ["cloud-localds", "--network-config", str(nc_path),
             str(output_path), str(ud_path), str(md_path)],
            check=True,
            capture_output=True,
        )

    return output_path
