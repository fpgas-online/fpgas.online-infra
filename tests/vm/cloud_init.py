"""Generate cloud-init seed ISOs for QEMU VMs."""

import subprocess
import tempfile
from pathlib import Path


def create_seed_iso(
    output_path: Path,
    ssh_pubkey: str,
    hostname: str = "test-vm",
    eth_local_mac: str = "52:54:00:aa:bb:02",
    eth_local_ip: str = "10.21.0.1/24",
) -> Path:
    """Create a cloud-init NoCloud seed ISO.

    Configures the VM's user and SSH key, and brings up the second NIC
    (the VLAN trunk) for the roles to configure.
    """
    user_data = f"""#cloud-config
hostname: {hostname}
manage_etc_hosts: true

users:
  - name: debian
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
    ssh_authorized_keys:
      - {ssh_pubkey}

# No packages: the cloud image ships python3, and the roles install what
# they need themselves (site installs git for its pip installs), as on a
# fresh tweed. Installing them here was a minute of apt on every boot.

runcmd:
  - systemctl disable --now systemd-resolved
  - rm -f /etc/resolv.conf
  - echo "nameserver 8.8.8.8" > /etc/resolv.conf
  # The internal NIC (enp0s3) is the per-port VLAN trunk. It must be owned
  # SOLELY by systemd-networkd via the vlan-ports role's 30-eth-local.network
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
