"""The boards take their per-port IPv6 address from the gateway (#222).

The gateway hands each switch port one IPv6 address, by DHCPv6 only. IPv4
on a board comes from the kernel's ip=dhcp and nothing in the root ran a
DHCPv6 client, so no board held the address. roles/onpi/tasks/ipv6.yml adds
the client: dhcpcd, IPv6 only, on the interface the NFS root is mounted
over, started by one unit. These tests fail if:
  - the role installs the dhcpcd service package, or stops installing or
    enabling the pieces, or the image build stops checking for them,
  - the root's apt would take anything but dhcpcd-base from Debian's
    backports (the armmp kernel, for one), or the image build stops
    refusing a dhcpcd older than 10, which its own sandbox kills on the boards,
  - the client's settings stop being exactly the ones that make it add an
    address and nothing else, under an identifier made from the MAC address,
  - the start script names an interface instead of finding the one the NFS
    root is mounted over, or carries on when it cannot find it,
  - the gateway's lease for the port's one address stops being short,
  - verify-pi.yml stops checking a booted board for the address.

tests/test_board_ipv6_netns.py runs the client against a real dnsmasq.
"""

import configparser
import re
import subprocess
from pathlib import Path

import pytest
import yaml

from tests.ports_conf_render import defaults, render

REPO = Path(__file__).resolve().parent.parent
ONPI = REPO / "ansible/roles/onpi"
FILES = ONPI / "files/board-ipv6"
CONF = FILES / "fpgas-board-ipv6.conf"
SCRIPT = FILES / "fpgas-board-ipv6.sh"
UNIT = FILES / "fpgas-board-ipv6.service"
TASKS = ONPI / "tasks/ipv6.yml"
BACKPORTS_SOURCE = FILES / "debian-backports-dhcpcd.sources"
BACKPORTS_PIN = FILES / "debian-backports-dhcpcd.pref"
ARMMP_PIN = REPO / "ansible/roles/fixpi/templates/apt/debian-armmp.pref.j2"
CI_NFSROOT = REPO / "ansible/ci-nfsroot.yml"
VERIFY_PI = REPO / "ansible/verify-pi.yml"

# A board's /proc/mounts: the NFS root read-only under overlayroot's tmpfs.
OVERLAYROOT_MOUNTS = """\
sysfs /sys sysfs rw,nosuid,nodev,noexec,relatime 0 0
10.21.0.1:/srv/nfs/rpi/bookworm/root /media/root-ro nfs ro,relatime,vers=3,rsize=4096,wsize=4096,namlen=255,\
hard,nolock,proto=tcp,timeo=600,retrans=2,sec=sys,mountaddr=10.21.0.1,mountvers=3,mountproto=tcp,local_lock=all,\
addr=10.21.0.1 0 0
tmpfs-root /media/root-rw tmpfs rw,relatime 0 0
overlayroot / overlay rw,relatime,lowerdir=/media/root-ro,upperdir=/media/root-rw/overlay,\
workdir=/media/root-rw/overlay-workdir/_ 0 0
"""
# The same root mounted without the overlay.
PLAIN_NFS_MOUNTS = """\
10.21.0.1:/srv/nfs/rpi/bookworm/root / nfs rw,relatime,vers=3,proto=tcp,mountaddr=10.21.0.1,addr=10.21.0.1 0 0
"""
# A root on local storage, with an NFS mount elsewhere: not a netbooted board.
LOCAL_ROOT_MOUNTS = """\
/dev/mmcblk0p2 / ext4 rw,noatime 0 0
10.9.9.9:/export /mnt/data nfs rw,relatime,vers=3,addr=10.9.9.9 0 0
"""


def tasks() -> dict:
    return {task["name"]: task for task in yaml.safe_load(TASKS.read_text())}


def settings() -> list[str]:
    return [line.strip() for line in CONF.read_text().splitlines() if line.strip() and not line.startswith("#")]


# --- the role -------------------------------------------------------------

def test_the_role_runs_the_ipv6_tasks():
    main = yaml.safe_load((ONPI / "tasks/main.yml").read_text())
    assert any(task.get("ansible.builtin.include_tasks") == "ipv6.yml" for task in main)


def test_only_the_bare_client_is_installed():
    """dhcpcd-base has no service; the dhcpcd package's service would run DHCP on the NFS interface."""
    apt = [task["ansible.builtin.apt"] for task in tasks().values() if "ansible.builtin.apt" in task]
    assert [a["name"] for a in apt] == ["dhcpcd-base"]
    assert apt[0]["install_recommends"] is False


def stanzas(text: str) -> list[dict[str, str]]:
    """The stanzas of an apt sources or preferences file, comments dropped."""
    out = []
    for block in re.split(r"\n\s*\n", text):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if line.strip() and not line.startswith("#"))
        if fields:
            out.append(fields)
    return out


def test_the_client_comes_from_debians_backports_on_a_bookworm_root():
    """bookworm's own dhcpcd-base is the 9.4.1 its sandbox kills on the boards."""
    by_dest = {task[module]["dest"]: task for task in tasks().values()
               for module in ("ansible.builtin.copy",) if module in task}
    for dest in ("/etc/apt/sources.list.d/debian-backports-dhcpcd.sources",
                 "/etc/apt/preferences.d/debian-backports-dhcpcd.pref"):
        assert by_dest[dest]["when"] == 'dist == "bookworm"'
    assert stanzas(BACKPORTS_SOURCE.read_text()) == [{
        "Types": "deb", "URIs": "http://deb.debian.org/debian", "Suites": "bookworm-backports",
        "Components": "main", "Architectures": "armhf",
        "Signed-By": "/usr/share/keyrings/debian-archive-keyring.gpg"}]
    names = list(tasks())
    assert names.index("Pin the backports source to the DHCPv6 client only") < names.index("Install the DHCPv6 client")


def test_nothing_but_the_client_comes_from_backports():
    """Not the armmp kernel either, which the root takes from the same host.

    The kernel's pin has to name bookworm: a pin on the host alone would
    match the backports suite's newer armmp kernel too, and it is a
    specific pin, which apt puts before the catch-all below it.
    """
    assert stanzas(BACKPORTS_PIN.read_text()) == [
        {"Package": "dhcpcd-base", "Pin": "release n=bookworm-backports", "Pin-Priority": "500"}]
    kernel, rest = stanzas(ARMMP_PIN.read_text())
    assert "armmp" in kernel["Package"]
    assert kernel["Pin"] == "release o=Debian,n=bookworm"
    assert rest == {"Package": "*", "Pin": "origin deb.debian.org", "Pin-Priority": "-1"}


def test_the_image_build_refuses_a_client_older_than_10():
    names = [task["name"] for play in yaml.safe_load(CI_NFSROOT.read_text()) for task in play.get("tasks", [])]
    assert "Check the DHCPv6 client in the root is 10 or newer" in names
    assert "dpkg --compare-versions {{ ci_nfsroot_dhcpcd_version.stdout }} ge 1:10" in CI_NFSROOT.read_text()


def test_the_files_go_where_the_unit_and_the_script_look_for_them():
    copies = {task["ansible.builtin.copy"]["src"]: task["ansible.builtin.copy"]
              for task in tasks().values()
              if "ansible.builtin.copy" in task and "debian-backports" not in task["ansible.builtin.copy"]["src"]}
    assert {src: (copy["dest"], copy["mode"]) for src, copy in copies.items()} == {
        "board-ipv6/fpgas-board-ipv6.conf": ("/etc/fpgas-board-ipv6.conf", "0644"),
        "board-ipv6/fpgas-board-ipv6.sh": ("/usr/local/sbin/fpgas-board-ipv6.sh", "0755"),
        "board-ipv6/fpgas-board-ipv6.service": ("/etc/systemd/system/fpgas-board-ipv6.service", "0644"),
    }
    for src in copies:
        assert (ONPI / "files" / src).is_file()
    assert 'CONF=/etc/fpgas-board-ipv6.conf\n' in SCRIPT.read_text()


def test_the_unit_is_enabled():
    enable = [task["ansible.builtin.systemd"] for task in tasks().values() if "ansible.builtin.systemd" in task]
    assert enable == [{"name": "fpgas-board-ipv6.service", "enabled": True}]


def test_the_image_build_checks_what_the_role_installed():
    text = CI_NFSROOT.read_text()
    assert "        - dhcpcd-base\n" in text
    names = [task["name"] for play in yaml.safe_load(text) for task in play.get("tasks", [])]
    for name in ("Assert the root has no dhcpcd service package",
                 "Check the image carries no DHCPv6 client identity",
                 "Check the unit that takes the board's IPv6 address is enabled in the image"):
        assert name in names


# --- the client's settings ------------------------------------------------

def test_the_client_adds_an_ipv6_address_and_nothing_else():
    assert sorted(settings()) == sorted([
        "ipv6only",       # never IPv4: the kernel's, under the NFS root
        "noipv6rs",       # router advertisements stay with the kernel
        "ia_na",          # one address, asked for at once
        "duid ll",        # known to the gateway by the MAC address
        "persistent",     # the address stays if the client stops
        'script ""',      # no hooks: resolv.conf, hostname, time servers
        "nodev",
    ])


# --- the unit -------------------------------------------------------------

def unit() -> configparser.ConfigParser:
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str
    parser.read_string(UNIT.read_text())
    return parser


def test_the_unit_runs_the_script_and_holds_nothing_up():
    parsed = unit()
    assert parsed["Service"]["ExecStart"] == "/usr/local/sbin/fpgas-board-ipv6.sh"
    assert parsed["Service"]["Type"] == "exec"
    assert parsed["Service"]["Restart"] == "on-failure"
    assert parsed["Install"]["WantedBy"] == "multi-user.target"
    # Boot, ssh and the NFS root need IPv4 only: nothing is ordered after it.
    assert "Before" not in parsed["Unit"] and "RequiredBy" not in parsed["Install"]


def test_the_unit_names_no_interface():
    assert not re.search(r"\b(eth|end|enx|enp|wlan)\w*\b", UNIT.read_text())


# --- the script -----------------------------------------------------------

@pytest.fixture
def board(tmp_path):
    """Run the script on a made-up board: its /proc/mounts, and `ip` and `dhcpcd` that record their arguments."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls = tmp_path / "calls"

    def run(mounts: str, route: str) -> tuple[subprocess.CompletedProcess, list[str]]:
        (tmp_path / "mounts").write_text(mounts)
        (tmp_path / "route").write_text(route)
        (bindir / "ip").write_text(f'#!/bin/sh\necho "ip $*" >> {calls}\ncat {tmp_path / "route"}\n')
        (bindir / "dhcpcd").write_text(f'#!/bin/sh\necho "dhcpcd $*" >> {calls}\n')
        for tool in ("ip", "dhcpcd"):
            (bindir / tool).chmod(0o755)
        calls.write_text("")
        proc = subprocess.run(["sh", str(SCRIPT)], capture_output=True, text=True, stdin=subprocess.DEVNULL,
                              env={"PATH": f"{bindir}:/usr/bin:/bin", "FPGAS_BOARD_IPV6_MOUNTS": str(tmp_path / "mounts")})
        return proc, calls.read_text().splitlines()

    return run


@pytest.mark.parametrize("mounts", [OVERLAYROOT_MOUNTS, PLAIN_NFS_MOUNTS], ids=["overlayroot", "plain"])
@pytest.mark.parametrize("iface", ["eth0", "end0", "enx00e04c680001"])
def test_the_client_runs_on_the_interface_the_nfs_root_is_mounted_over(board, mounts, iface):
    """Whatever it is called on this board type: the one the kernel routes to the NFS server through."""
    proc, calls = board(mounts, f"10.21.0.1 dev {iface} src 10.21.1.7 uid 0 \\    cache \n")
    assert proc.returncode == 0, proc.stderr
    assert calls == ["ip -o route get 10.21.0.1", f"dhcpcd -B -f /etc/fpgas-board-ipv6.conf {iface}"]


def test_a_route_through_a_router_still_names_the_interface(board):
    proc, calls = board(OVERLAYROOT_MOUNTS, "10.21.0.1 via 10.21.1.254 dev eth0 src 10.21.1.7 uid 0 \\    cache \n")
    assert proc.returncode == 0, proc.stderr
    assert calls[-1] == "dhcpcd -B -f /etc/fpgas-board-ipv6.conf eth0"


def test_no_client_is_started_where_the_root_is_not_on_nfs(board):
    proc, calls = board(LOCAL_ROOT_MOUNTS, "10.9.9.9 dev eth0 src 10.9.9.1 uid 0 \\    cache \n")
    assert proc.returncode != 0
    assert "the root is not netbooted" in proc.stderr
    assert calls == []


def test_no_client_is_started_without_a_route_to_the_nfs_server(board):
    proc, calls = board(OVERLAYROOT_MOUNTS, "")
    assert proc.returncode != 0
    assert "no route to the NFS server 10.21.0.1" in proc.stderr
    assert calls == ["ip -o route get 10.21.0.1"]


def test_the_script_names_no_interface():
    code = [line for line in SCRIPT.read_text().splitlines() if not line.lstrip().startswith("#")]
    assert not re.search(r"\b(eth|end|enx|enp|wlan)\d\w*\b", "\n".join(code))


# --- the gateway: the port's one address ------------------------------------

def lease_seconds(lease: str) -> int:
    """A dnsmasq lease time (`45`, `10m`, `12h`, ...) in seconds."""
    match = re.fullmatch(r"(\d+)([smhdw]?)", lease)
    assert match, lease
    return int(match.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[match.group(2)]


def test_the_ports_ipv6_range_is_one_address_with_the_roles_lease():
    lease = str(defaults()["pxe_port_lease6"])
    lines = [line for line in render().splitlines() if line.startswith("dhcp-range=") and "::" in line]
    assert lines == [f"dhcp-range=tag:v2101,2001:db8:a137:2101::1,2001:db8:a137:2101::1,off-link,64,{lease}"]
    assert render(pxe_port_lease6="2m").count(",off-link,64,2m\n") == 1


def test_the_ports_ipv6_lease_is_short():
    """It is how long the address stays unusable after the board in a port was swapped.

    Not below dnsmasq's minimum (two minutes), and far below the IPv4
    range's twelve hours, which is what it was.
    """
    seconds = lease_seconds(str(defaults()["pxe_port_lease6"]))
    assert 120 <= seconds <= 3600


def test_the_ipv4_range_is_unchanged():
    assert "dhcp-range=tag:v2101,10.21.1.1,10.21.1.1,255.255.0.0,12h\n" in render()


# --- verify-pi ------------------------------------------------------------

def test_verify_pi_checks_a_booted_board_for_the_address():
    names = [task["name"] for play in yaml.safe_load(VERIFY_PI.read_text()) for task in play.get("tasks", [])]
    for name in ("Find the per-port IPv6 address",
                 "Assert per-port IPv6 address present",
                 "Assert the board's DHCPv6 client is running",
                 "The gateway reaches this Pi on its IPv6 address"):
        assert name in names
