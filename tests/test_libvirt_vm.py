"""Unit tests rendering the libvirt-vm role's domain XML and cloud-init
templates (ansible/roles/libvirt-vm/templates) with the gw-welland shape from
ansible/inventory/host_vars/tweed-hv.yml."""
import pathlib
import xml.etree.ElementTree as ET

import jinja2
import yaml

TPL = pathlib.Path(__file__).resolve().parents[1] / "ansible" / "roles" / "libvirt-vm" / "templates"

# gw-welland-blue: the representative VM (host_vars/tweed-hv.yml). The NIC
# MACs deliberately reuse bare-metal tweed's physical MACs.
GW = dict(
    vm_domain="gw-welland-blue",
    vm_hostname="gw-welland",
    vm_memory_mb=5120,
    vm_vcpus=3,
    vm_disk_path_eff="/var/lib/libvirt/images/gw-welland-blue.qcow2",
    vm_seed_iso="/var/lib/libvirt/images/gw-welland-blue-seed.iso",
    vm_nvram_path="/var/lib/libvirt/qemu/nvram/gw-welland-blue_VARS.fd",
    vm_ovmf_code="/usr/share/OVMF/OVMF_CODE_4M.fd",
    vm_ovmf_vars="/usr/share/OVMF/OVMF_VARS_4M.fd",
    vm_nics=[{"bridge": "br-fpgas", "mac": "0C:C4:7A:16:3B:4A"},
             {"bridge": "br-uplink", "mac": "0c:c4:7a:16:3b:4b"}],
    vm_extra_volumes=[{"path": "/var/lib/libvirt/images/nfsroot.qcow2", "target": "vdb"}],
    vm_uplink_bridge="br-uplink",
    vm_ansible_ssh_pubkey="ssh-ed25519 AAAATESTKEY fpgas.online-ansible",
)


def render(name, **vars):
    env = jinja2.Environment(undefined=jinja2.StrictUndefined, trim_blocks=True,
                             keep_trailing_newline=True)
    tpl = env.from_string((TPL / name).read_text())
    return tpl.render(ansible_managed="test-managed", **vars)


def domain():
    return ET.fromstring(render("domain.xml.j2", **GW))


def test_domain_machine_and_firmware():
    d = domain()
    assert d.find("os/type").get("machine") == "q35"
    assert d.find("os/loader").text == "/usr/share/OVMF/OVMF_CODE_4M.fd"
    nvram = d.find("os/nvram")
    assert nvram.get("template") == "/usr/share/OVMF/OVMF_VARS_4M.fd"
    assert nvram.text == "/var/lib/libvirt/qemu/nvram/gw-welland-blue_VARS.fd"


def test_domain_shape():
    d = domain()
    assert d.find("name").text == "gw-welland-blue"
    assert d.find("memory").text == "5120" and d.find("memory").get("unit") == "MiB"
    assert d.find("vcpu").text == "3"


def test_domain_disks_include_extra_volume():
    d = domain()
    by_target = {disk.find("target").get("dev"): disk for disk in d.findall("devices/disk")}
    assert by_target["vda"].find("source").get("file") == "/var/lib/libvirt/images/gw-welland-blue.qcow2"
    assert by_target["vdb"].find("source").get("file") == "/var/lib/libvirt/images/nfsroot.qcow2"
    assert by_target["vdb"].find("target").get("bus") == "virtio"
    assert by_target["sda"].get("device") == "cdrom"
    assert by_target["sda"].find("source").get("file").endswith("-seed.iso")


def test_domain_nics_fixed_macs_on_bridges():
    d = domain()
    nics = [(i.find("source").get("bridge"), i.find("mac").get("address"),
             i.find("model").get("type")) for i in d.findall("devices/interface")]
    # MACs come out lowercased even when host_vars carry uppercase
    assert nics == [("br-fpgas", "0c:c4:7a:16:3b:4a", "virtio"),
                    ("br-uplink", "0c:c4:7a:16:3b:4b", "virtio")]


def test_domain_serial_console():
    d = domain()
    assert d.find("devices/serial").get("type") == "pty"
    console = d.find("devices/console")
    assert console.get("type") == "pty"
    assert console.find("target").get("type") == "serial"


def test_user_data():
    out = render("user-data.j2", **GW)
    assert out.startswith("#cloud-config\n")
    data = yaml.safe_load(out)
    assert data["hostname"] == "gw-welland"
    assert data["ssh_pwauth"] is False
    (user,) = data["users"]
    assert user["name"] == "ansible"
    assert user["sudo"] == "ALL=(ALL) NOPASSWD:ALL"
    assert user["ssh_authorized_keys"] == ["ssh-ed25519 AAAATESTKEY fpgas.online-ansible"]


def test_network_config_dhcp_only_on_uplink():
    data = yaml.safe_load(render("network-config.j2", **GW))
    assert data["version"] == 2
    nic0, nic1 = data["ethernets"]["nic0"], data["ethernets"]["nic1"]
    # br-fpgas NIC: matched (lowercased) but otherwise untouched — no dhcp,
    # no addresses; the guest's own converge owns it.
    assert nic0["match"]["macaddress"] == "0c:c4:7a:16:3b:4a"
    assert nic0["dhcp4"] is False
    assert "addresses" not in nic0
    # br-uplink NIC: dhcp4
    assert nic1["match"]["macaddress"] == "0c:c4:7a:16:3b:4b"
    assert nic1["dhcp4"] is True


def test_meta_data_stable_instance_id():
    out = render("meta-data.j2", **GW)
    data = yaml.safe_load(out)
    assert data["instance-id"] == "gw-welland-blue"
    assert data["local-hostname"] == "gw-welland"
