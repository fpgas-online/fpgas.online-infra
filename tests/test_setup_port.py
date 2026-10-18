"""The setup / config port (Tim, 18 Oct 2026; docs/pi/setup-port.md).

These tests fail if:
  - the setup port's boot tree stops being the fleet's with the radios on, the
    EEPROM unprotected and fpgas.setup_port=1 on both kernel command lines,
  - it stops hard-linking the fleet's other files (a copy would go stale
    silently, and doubles the tree),
  - a second build with nothing changed reports a change, or a fleet change is
    not picked up,
  - the build stops failing loud when the fleet's files no longer carry what
    it lifts,
  - dnsmasq stops serving that tree to the setup port of every switch, or
    serves it to any other port,
  - the root's rfkill udev rule stops being limited to the setup port,
  - the VM test stops building and checking a setup tree, or its virtual Pi
    stops booting the fleet's.
"""

import os
import subprocess
import sys
from pathlib import Path

import jinja2
import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "ansible/roles/fixpi/files/scripts/setup_port_tree.py"
FLEET_CONFIG = """# fleet
dtparam=audio=on
dtoverlay=disable-wifi
dtoverlay=disable-bt
eeprom_write_protect=1
[pi5]
dtoverlay=uart0-pi5
cmdline=cmdline-pi5.txt
[all]
"""
CMDLINE = "root=/dev/nfs ro ip=dhcp console=tty1\n"


def _fleet(tmp_path: Path) -> Path:
    fleet = tmp_path / "boot"
    (fleet / "overlays").mkdir(parents=True)
    (fleet / "config.txt").write_text(FLEET_CONFIG)
    (fleet / "cmdline.txt").write_text(CMDLINE)
    (fleet / "cmdline-pi5.txt").write_text(CMDLINE.replace("tty1", "ttyAMA10,115200"))
    (fleet / "kernel8.img").write_bytes(b"kernel")
    (fleet / "overlays/disable-wifi.dtbo").write_bytes(b"dtbo")
    os.symlink("kernel8.img", fleet / "kernel.img")
    os.symlink("overlays", fleet / "dtbs")
    return fleet


def _run(fleet: Path, setup: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), str(fleet), str(setup)], capture_output=True, text=True)


def test_the_tree_lifts_the_restrictions_and_links_the_rest(tmp_path):
    fleet, setup = _fleet(tmp_path), tmp_path / "boot-setup"
    run = _run(fleet, setup)
    assert run.returncode == 0, run.stderr
    assert run.stdout == "changed\n"
    config = (setup / "config.txt").read_text().splitlines()
    assert "eeprom_write_protect=0" in config
    assert not {"eeprom_write_protect=1", "dtoverlay=disable-wifi", "dtoverlay=disable-bt"} & set(config)
    # Everything else the fleet's config says stays, in order.
    assert config == ["# fleet", "dtparam=audio=on", "eeprom_write_protect=0", "[pi5]",
                      "dtoverlay=uart0-pi5", "cmdline=cmdline-pi5.txt", "[all]",
                      "[all]", "dtparam=spi=on", "dtparam=i2c_arm=on"]
    for name in ("cmdline.txt", "cmdline-pi5.txt"):
        words = (setup / name).read_text().split()
        assert words[-2:] == ["fpgas.setup_port=1", "systemd.restore_state=0"]
        assert words[:-2] == (fleet / name).read_text().split()
    for name in ("kernel8.img", "overlays/disable-wifi.dtbo"):
        assert os.stat(setup / name).st_ino == os.stat(fleet / name).st_ino
    assert os.readlink(setup / "kernel.img") == "kernel8.img"
    assert os.readlink(setup / "dtbs") == "overlays"  # a link to a directory stays a link
    # The fleet's own files are untouched.
    assert (fleet / "config.txt").read_text() == FLEET_CONFIG
    assert not (tmp_path / "boot-setup.new").exists() and not (tmp_path / "boot-setup.old").exists()


def test_a_second_build_is_unchanged_and_a_fleet_change_is_picked_up(tmp_path):
    fleet, setup = _fleet(tmp_path), tmp_path / "boot-setup"
    assert _run(fleet, setup).stdout == "changed\n"
    assert _run(fleet, setup).stdout == "unchanged\n"
    # A root update replaces a file (a new inode): the setup tree follows it.
    (fleet / "kernel8.img").unlink()
    (fleet / "kernel8.img").write_bytes(b"kernel 2")
    assert _run(fleet, setup).stdout == "changed\n"
    assert (setup / "kernel8.img").read_bytes() == b"kernel 2"
    (fleet / "overlays/new.dtbo").write_bytes(b"new")
    assert _run(fleet, setup).stdout == "changed\n"
    assert (setup / "overlays/new.dtbo").exists()
    (fleet / "overlays/new.dtbo").unlink()
    assert _run(fleet, setup).stdout == "changed\n"
    assert not (setup / "overlays/new.dtbo").exists()
    with open(fleet / "config.txt", "a") as f:
        f.write("dtparam=audio=off\n")
    assert _run(fleet, setup).stdout == "changed\n"
    assert "dtparam=audio=off" in (setup / "config.txt").read_text().splitlines()
    # names filecmp hides by default are compared too
    (fleet / ".git").mkdir()
    (fleet / ".git/x").write_bytes(b"1")
    assert _run(fleet, setup).stdout == "changed\n"
    (fleet / ".git/x").unlink()
    (fleet / ".git/x").write_bytes(b"2")
    assert _run(fleet, setup).stdout == "changed\n"
    # a link to a directory that points elsewhere
    (fleet / "dtbs").unlink()
    os.symlink(".git", fleet / "dtbs")
    assert _run(fleet, setup).stdout == "changed\n"
    assert os.readlink(setup / "dtbs") == ".git"


def test_the_derived_files_are_readable_whatever_the_umask(tmp_path):
    fleet, setup = _fleet(tmp_path), tmp_path / "boot-setup"
    run = subprocess.run(["bash", "-c", f'umask 077 && exec "{sys.executable}" "{SCRIPT}" "{fleet}" "{setup}"'],
                         capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    for name in ("config.txt", "cmdline.txt", "cmdline-pi5.txt"):
        assert (setup / name).stat().st_mode & 0o777 == 0o644
    assert setup.stat().st_mode & 0o777 == 0o755


@pytest.mark.parametrize("change, why", [
    (lambda f: (f / "config.txt").write_text(FLEET_CONFIG.replace("dtoverlay=disable-bt\n", "")),
     "'dtoverlay=disable-bt' 0 times"),
    (lambda f: (f / "config.txt").write_text(FLEET_CONFIG + "eeprom_write_protect=1\n"),
     "'eeprom_write_protect=1' 2 times"),
    (lambda f: (f / "config.txt").write_text(FLEET_CONFIG + "eeprom_write_protect=0\n"),
     "already carries 'eeprom_write_protect=0'"),
    (lambda f: (f / "config.txt").write_text(FLEET_CONFIG + "dtparam=spi=on\n"),
     "already carries 'dtparam=spi=on'"),
    (lambda f: (f / "cmdline.txt").write_text("root=/dev/nfs fpgas.setup_port=0\n"),
     "already names fpgas.setup_port"),
    (lambda f: (f / "cmdline-pi5.txt").write_text("a\nb\n"), "cmdline-pi5.txt is not one line"),
    (lambda f: (f / "cmdline-pi5.txt").unlink(), "cmdline-pi5.txt is missing"),
])
def test_the_build_fails_loud_on_a_fleet_it_does_not_know(tmp_path, change, why):
    fleet, setup = _fleet(tmp_path), tmp_path / "boot-setup"
    assert _run(fleet, setup).returncode == 0
    served = (setup / "config.txt").read_text()
    change(fleet)
    run = _run(fleet, setup)
    assert run.returncode != 0 and why in run.stderr, run.stderr
    # The tree served before stays served.
    assert (setup / "config.txt").read_text() == served


def _ports_conf(**extra) -> str:
    sys.path.insert(0, str(REPO / "ansible/filter_plugins"))
    try:
        import port_vlans
    finally:
        sys.path.pop(0)
    env = jinja2.Environment(undefined=jinja2.StrictUndefined)
    env.filters["port_vlan_map"] = port_vlans.port_vlan_map
    template = env.from_string((REPO / "ansible/roles/pxe/templates/ports.conf.j2").read_text())
    switches = [{"index": 1, "access_ports": 40}, {"index": 2, "access_ports": 48}]
    return template.render(ansible_managed="x", switches=switches, pib_network="10.21",
                           pib_network6_base="2404:e80:a137:21", pib_domain="example", eth_local="eth-local",
                           **extra)


def test_dnsmasq_serves_the_tree_to_the_setup_port_of_every_switch_only():
    text = _ports_conf(setup_port=1, setup_port_tftp_root="/srv/nfs/rpi/bookworm/boot-setup")
    roots = [line for line in text.splitlines() if line.startswith("tftp-root=")]
    assert roots == ["tftp-root=/srv/nfs/rpi/bookworm/boot-setup,v2101",
                     "tftp-root=/srv/nfs/rpi/bookworm/boot-setup,v2201"]
    # Each follows its own port's interface block.
    blocks = text.split("interface=")
    assert any(b.startswith("v2101\n") and "boot-setup,v2101" in b for b in blocks)
    assert any(b.startswith("v2201\n") and "boot-setup,v2201" in b for b in blocks)


def test_a_site_with_no_setup_port_serves_no_setup_tree():
    assert "tftp-root=" not in _ports_conf()


def test_welland_names_its_setup_port_and_the_tree_path_is_shared():
    welland = (REPO / "ansible/inventory/host_vars/fpgas.online.yml").read_text().splitlines()
    assert [line for line in welland if line.startswith("setup_port:")] == ["setup_port: 1"]
    srv = yaml.safe_load((REPO / "ansible/inventory/group_vars/all/srv.yml").read_text())
    assert srv["setup_port_tftp_root"] == "{{ nfs_root }}/boot-setup"


def test_the_vm_test_builds_a_setup_tree_and_its_pi_boots_the_fleets():
    # The virtual Pi is on switch 1 port 1 (tests/vm/run_tests.py, AccessPortSwitch(VLAN), VLAN 2101): it boots the fleet's
    # tree, so the normal path stays tested end to end, while the gateway builds, serves and checks port 2's.
    vm = yaml.safe_load((REPO / "tests/inventory/host_vars/test-vm.yml").read_text())
    assert vm["setup_port"] == 2 and vm["switches"][0]["access_ports"] == 2
    harness = (REPO / "tests/vm/run_tests.py").read_text()
    assert "\nVLAN = 2101\n" in harness and "AccessPortSwitch(VLAN)" in harness


def test_the_rfkill_rule_acts_only_on_the_setup_port():
    rules = [line for line in (REPO / "ansible/roles/fixpi/files/90-fpgas-setup-port-rfkill.rules").read_text()
             .splitlines() if line and not line.startswith("#")]
    assert rules == ['IMPORT{cmdline}="fpgas.setup_port"',
                     'SUBSYSTEM=="rfkill", ACTION=="add", ENV{fpgas.setup_port}=="1", ATTR{soft}="0"']


def test_the_tree_is_built_last_and_never_in_the_image():
    main = yaml.safe_load((REPO / "ansible/roles/fixpi/tasks/main.yml").read_text())
    assert main[-1]["ansible.builtin.include_tasks"] == "setup-port.yml"
    tasks = yaml.safe_load((REPO / "ansible/roles/fixpi/tasks/setup-port.yml").read_text())
    (build,) = [t for t in tasks if "ansible.builtin.script" in t]
    assert build["when"] == ["setup_port is defined", "not (fixpi_image_build | bool)"]
    assert build["ansible.builtin.script"]["cmd"] == \
        "files/scripts/setup_port_tree.py {{ tftp_root }} {{ setup_port_tftp_root }}"
