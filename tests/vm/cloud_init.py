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

    Configures the VM with SSH key, Python3, qemu-guest-agent,
    and a static IP on the second NIC (for the socket VLAN).
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

packages:
  - python3
  - git
  - qemu-guest-agent

runcmd:
  - systemctl enable --now qemu-guest-agent
  - systemctl disable --now systemd-resolved
  - rm -f /etc/resolv.conf
  - echo "nameserver 8.8.8.8" > /etc/resolv.conf
  - ip link set enp0s3 up
  - ip addr add {eth_local_ip} dev enp0s3
  - mkdir -p /etc/letsencrypt/live/test.fpgas.online
  - openssl req -x509 -newkey rsa:2048 -keyout /etc/letsencrypt/live/test.fpgas.online/privkey.pem -out /etc/letsencrypt/live/test.fpgas.online/fullchain.pem -days 1 -nodes -subj "/CN=test.fpgas.online"

write_files:
  - path: /etc/network/interfaces.d/enp0s3.cfg
    content: |
      auto enp0s3
      iface enp0s3 inet static
        address {eth_local_ip}
  - path: /etc/systemd/network/10-enp0s3.network
    content: |
      [Match]
      Name=enp0s3
      [Network]
      Address={eth_local_ip}
      [Link]
      RequiredForOnline=no

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

        subprocess.run(
            ["cloud-localds", str(output_path), str(ud_path), str(md_path)],
            check=True,
            capture_output=True,
        )

    return output_path
