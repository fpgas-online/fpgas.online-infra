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
  - the gateway stops sending router advertisements on every port by
    itself (radvd, one interface per port), starts advertising from
    dnsmasq as well, or advertises the prefix as on-link or for
    autoconfiguration,
  - verify-pi.yml stops checking a booted board for the address and for a
    default route that the gateway keeps fresh.

tests/test_board_ipv6_netns.py runs the client against a real dnsmasq and a
real radvd.
"""

import configparser
import re
import subprocess
from pathlib import Path

import jinja2
import pytest
import yaml

from tests.ports_conf_render import SITE, defaults, render, render_as_on_main, render_radvd, switches

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
PXE = REPO / "ansible/roles/pxe"

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


def test_the_image_carries_the_unit_switched_off():
    """One image for every site: a site's gateway turns the client on (pxe_board_ipv6)."""
    enable = [task["ansible.builtin.systemd"] for task in tasks().values() if "ansible.builtin.systemd" in task]
    assert enable == [{"name": "fpgas-board-ipv6.service", "enabled": False}]


def test_the_image_build_checks_what_the_role_installed():
    text = CI_NFSROOT.read_text()
    assert "        - dhcpcd-base\n" in text
    names = [task["name"] for play in yaml.safe_load(text) for task in play.get("tasks", [])]
    for name in ("Assert the root has no dhcpcd service package",
                 "Check the image carries no DHCPv6 client identity",
                 "Check the image carries the unit that takes the board's IPv6 address",
                 "Check that unit is not enabled in the image"):
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

ON = {"pxe_board_ipv6": True}  # a site whose boards have IPv6


def lease_seconds(lease: str) -> int:
    """A dnsmasq lease time (`45`, `10m`, `12h`, ...) in seconds."""
    match = re.fullmatch(r"(\d+)([smhdw]?)", lease)
    assert match, lease
    return int(match.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[match.group(2)]


def test_the_ports_ipv6_range_is_one_address_with_the_roles_lease():
    lease = str(defaults()["pxe_port_lease6"])
    lines = [line for line in render(**ON).splitlines() if line.startswith("dhcp-range=") and "::" in line]
    assert lines == [f"dhcp-range=tag:v2101,2001:db8:a137:2101::1,2001:db8:a137:2101::1,64,{lease}"]
    assert render(**ON, pxe_port_lease6="2m").count("::1,64,2m\n") == 1


def test_the_ports_ipv6_lease_is_short():
    """It is how long the address stays unusable after the board in a port was swapped.

    Not below dnsmasq's minimum (two minutes), and far below the IPv4
    range's twelve hours, which is what it was.
    """
    seconds = lease_seconds(str(defaults()["pxe_port_lease6"]))
    assert 120 <= seconds <= 3600


def test_the_ipv4_range_is_unchanged():
    assert "dhcp-range=tag:v2101,10.21.1.1,10.21.1.1,255.255.0.0,12h\n" in render()


# --- the gateway: router advertisements on every port -----------------------

GATEWAY_ON = "switches is defined and pxe_board_ipv6 | bool"
GATEWAY_OFF = "switches is defined and not (pxe_board_ipv6 | bool)"

TWO_SWITCHES = [{**SITE["switches"][0], "index": 1, "access_ports": 3},
                {**SITE["switches"][0], "index": 2, "access_ports": 2}]


def radvd_interfaces(conf: str) -> dict[str, str]:
    """radvd.conf's interface blocks, by interface name, comments removed."""
    code = "\n".join(line for line in conf.splitlines() if not line.lstrip().startswith("#"))
    return dict(re.findall(r"^interface (\S+) \{\n(.*?)^\};$", code, re.MULTILINE | re.DOTALL))


def test_radvd_advertises_on_every_port_dnsmasq_serves():
    """One interface block per port: radvd's timers are per interface.

    dnsmasq's are per prefix, and every port of a switch shares one: it sent
    its periodic advertisement on one port of each switch only.
    """
    ports = re.findall(r"^dhcp-range=tag:(\S+?),[0-9a-f:]*::", render(**ON, switches=TWO_SWITCHES), re.MULTILINE)
    assert ports == ["v2101", "v2102", "v2103", "v2201", "v2202"]
    assert list(radvd_interfaces(render_radvd(switches=TWO_SWITCHES))) == ports


def test_radvd_says_what_the_per_port_addressing_needs():
    """Addresses by DHCPv6; the switch's prefix neither on-link nor for autoconfiguration."""
    values = defaults()
    blocks = radvd_interfaces(render_radvd(switches=TWO_SWITCHES))
    for iface, block in blocks.items():
        settings = [line.strip() for line in block.splitlines() if line.strip()]
        prefix = "2001:db8:a137:210" + iface[2]  # v2SPP: switch S's /64
        assert settings == [
            "AdvSendAdvert on;",
            "IgnoreIfMissing on;",
            "RemoveAdvOnExit off;",
            "AdvManagedFlag on;",
            "AdvOtherConfigFlag on;",
            f"MaxRtrAdvInterval {values['pxe_ra_interval']};",
            f"AdvDefaultLifetime {values['pxe_ra_lifetime']};",
            f"prefix {prefix}::/64 {{",
            "AdvOnLink off;",
            "AdvAutonomous off;",
            "};",
        ], iface


def test_the_default_route_outlives_lost_advertisements():
    """A board's default route lasts the lifetime; the gateway repeats itself every interval."""
    values = defaults()
    # radvd's own limits, and RFC 4861's default of three intervals.
    assert 10 <= values["pxe_ra_interval"] <= 1800
    assert 3 * values["pxe_ra_interval"] <= values["pxe_ra_lifetime"] <= 9000


def test_dnsmasq_advertises_nothing():
    """radvd does. Two advertisers on a port would each tell a board something of their own."""
    settings = [line.strip() for line in render(**ON, switches=TWO_SWITCHES).splitlines()
                if line.strip() and not line.lstrip().startswith("#")]
    assert not [line for line in settings if re.match(r"(enable-ra|ra-param)\b", line)]
    assert not [line for line in settings if line.startswith("dhcp-range=")
                and re.search(r"\b(ra-only|ra-names|ra-stateless|ra-advrouter|slaac|off-link)\b", line)]


def test_the_role_installs_checks_and_starts_radvd():
    tasks = {task["name"]: task for task in yaml.safe_load((PXE / "tasks/main.yml").read_text())}
    install = tasks["Install radvd (router advertisements on the per-port interfaces)"]
    configure = tasks["Configure radvd (one interface per switch port)"]
    start = tasks["Enable and start radvd"]
    assert install["ansible.builtin.apt"]["name"] == "radvd"
    assert configure["ansible.builtin.template"]["dest"] == "/etc/radvd.conf"
    # radvd reads the file before it replaces the one in use.
    assert configure["ansible.builtin.template"]["validate"] == "radvd --configtest --config %s"
    assert start["ansible.builtin.systemd"] == {"name": "radvd", "enabled": True, "state": "started"}
    # Only where the per-port scheme is in use and boards have IPv6.
    assert all(task["when"] == GATEWAY_ON for task in (install, configure, start))
    order = list(tasks)
    assert order.index(install["name"]) < order.index(configure["name"]) < order.index(start["name"])


def test_the_role_refuses_timing_radvd_would_silently_drop():
    """radvd runs on with a value outside its limits and advertises nothing on the port."""
    tasks = {task["name"]: task for task in yaml.safe_load((PXE / "tasks/main.yml").read_text())}
    check = tasks["Check the router advertisement timing is within radvd's limits"]
    assert check["ansible.builtin.assert"]["that"] == [
        "pxe_ra_interval | int >= 10",
        "pxe_ra_interval | int <= 1800",
        "pxe_ra_lifetime | int >= 3 * (pxe_ra_interval | int)",
        "pxe_ra_lifetime | int <= 9000",
    ]
    order = list(tasks)
    assert order.index(check["name"]) < order.index("Configure radvd (one interface per switch port)")


def test_a_changed_radvd_file_is_reloaded():
    """radvd reads the file again and carries on."""
    tasks = {task["name"]: task for task in yaml.safe_load((PXE / "tasks/main.yml").read_text())}
    handlers = {task["name"]: task for task in yaml.safe_load((PXE / "handlers/main.yml").read_text())}
    assert tasks["Configure radvd (one interface per switch port)"]["notify"] == "Reload radvd"
    assert handlers["Reload radvd"]["ansible.builtin.systemd"] == {"name": "radvd", "state": "reloaded"}


def test_verify_server_checks_the_advertiser():
    names = [task["name"] for task in yaml.safe_load((PXE / "tasks/verify/main.yml").read_text())]
    for name in ("Assert radvd service is active",
                 "Assert radvd accepts /etc/radvd.conf",
                 "Assert radvd advertises on every port dnsmasq serves, as the design needs",
                 "Assert dnsmasq advertises nothing (radvd does)",
                 "Assert the running dnsmasq has read its present configuration"):
        assert name in names


# --- the switch: pxe_board_ipv6, off unless a site's inventory turns it on -----

def when_true(when: str, **variables) -> bool:
    """Whether a task with this `when` would run, with these variables."""
    env = jinja2.Environment(undefined=jinja2.StrictUndefined)
    env.filters["bool"] = lambda value: value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes")
    return env.compile_expression(when)(**variables)


def pxe_tasks(path: str) -> list[dict]:
    return yaml.safe_load((PXE / path).read_text())


def about_ipv6(task: dict) -> bool:
    """A task of roles/pxe that exists for the boards' IPv6: radvd, the advertisements, the running dnsmasq."""
    return bool(re.search(r"radvd|advertis|running dnsmasq|dnsmasq's configuration last changed|ports\.conf$",
                          task["name"]))


def test_it_is_off_unless_a_sites_inventory_says_otherwise():
    assert defaults()["pxe_board_ipv6"] is False
    # No real site sets it: a merge changes nothing anywhere.
    for path in (REPO / "ansible/inventory").rglob("*"):
        if path.is_file():
            assert "pxe_board_ipv6" not in path.read_text(), path
    # The VM test's gateway does, so CI proves the feature.
    assert yaml.safe_load((REPO / "tests/inventory/host_vars/test-vm.yml").read_text())["pxe_board_ipv6"] is True


@pytest.mark.parametrize("site", [switches(1), switches(3), switches(48, 48)], ids=["1", "3", "48,48"])
def test_off_dnsmasq_is_configured_byte_for_byte_as_before(site):
    """tests/fixtures/ports.conf.j2.main is the template of the commit this work started from."""
    off = render(switches=site)
    assert off == render_as_on_main(switches=site)
    assert off == render(switches=site, pxe_board_ipv6=False)
    assert "\nenable-ra\n" in off and off.count(",off-link,64,12h\n") == sum(s["access_ports"] for s in site)
    assert off != render(switches=site, **ON)


def test_off_the_lease_and_timing_variables_change_nothing():
    """They belong to the feature; a site that sets one without turning it on gets nothing."""
    assert render(pxe_port_lease6="2m", pxe_ra_interval=10, pxe_ra_lifetime=30) == render_as_on_main()


@pytest.mark.parametrize("path", ["tasks/main.yml", "tasks/verify/main.yml"])
def test_off_no_task_for_the_boards_ipv6_runs(path):
    """Nothing installs, configures, starts or checks radvd; verify-server asserts nothing new."""
    # A gateway where it was never on: no radvd.conf to find.
    off = {"switches": TWO_SWITCHES, "pxe_board_ipv6": False, "pxe_radvd_left": {"stat": {"exists": False}}}
    ran = [task["name"] for task in pxe_tasks(path)
           if about_ipv6(task) and "when" in task and when_true(task["when"], **off)]
    # One read, of the file a gateway has only if the feature was on before.
    assert ran == (["Look for a radvd from when boards had IPv6"] if path == "tasks/main.yml" else [])
    # And every one of them is behind the switch, not just skipped by accident.
    gated = [task["name"] for task in pxe_tasks(path) if task.get("when") in (GATEWAY_ON, GATEWAY_OFF)
             or str(task.get("when", "")).startswith(GATEWAY_OFF)]
    for task in pxe_tasks(path):
        if about_ipv6(task) and task["name"] != "Create dnsmasq.d ports.conf (per-port hosts)":
            assert task["name"] in gated, task["name"]


def test_on_every_task_for_the_boards_ipv6_runs_and_the_removal_does_not():
    on = {"switches": TWO_SWITCHES, "pxe_board_ipv6": True}
    for path in ("tasks/main.yml", "tasks/verify/main.yml"):
        for task in pxe_tasks(path):
            if about_ipv6(task) and "when" in task:
                removal = "from when boards had IPv6" in task["name"] or task["name"].startswith("Remove that radvd")
                assert when_true(task["when"], **on, pxe_radvd_left={"stat": {"exists": True}}) is not removal, task["name"]


def test_turned_off_again_the_gateway_is_as_if_it_was_never_on():
    """radvd goes, with its file last; ports.conf is the old one again (the test above) and
    its change restarts dnsmasq, which advertises as it did."""
    tasks = {task["name"]: task for task in pxe_tasks("tasks/main.yml")}
    left = {"switches": TWO_SWITCHES, "pxe_board_ipv6": False, "pxe_radvd_left": {"stat": {"exists": True}}}
    never = {**left, "pxe_radvd_left": {"stat": {"exists": False}}}
    remove, forget = tasks["Remove that radvd"], tasks["Remove that radvd's configuration"]
    assert remove["ansible.builtin.apt"] == {"name": "radvd", "state": "absent", "purge": True}
    assert forget["ansible.builtin.file"] == {"path": "/etc/radvd.conf", "state": "absent"}
    assert tasks["Look for a radvd from when boards had IPv6"]["ansible.builtin.stat"] == {"path": "/etc/radvd.conf"}
    for task in (remove, forget):
        assert when_true(task["when"], **left) and not when_true(task["when"], **never)
    order = list(tasks)
    assert order.index(remove["name"]) < order.index(forget["name"])
    assert tasks["Create dnsmasq.d ports.conf (per-port hosts)"]["notify"] == "Restart dnsmasq"


def test_a_ports_conf_dnsmasq_rejects_never_replaces_the_one_in_use():
    task = {t["name"]: t for t in pxe_tasks("tasks/main.yml")}["Create dnsmasq.d ports.conf (per-port hosts)"]
    assert task["ansible.builtin.template"]["validate"] == "dnsmasq --test --conf-file=%s"


def test_the_gateway_turns_the_client_on_in_its_own_root():
    """roles/fixpi, the site layer: never in the image build, on with the switch, off without."""
    fixpi = REPO / "ansible/roles/fixpi/tasks"
    include = next(task for task in yaml.safe_load((fixpi / "main.yml").read_text())
                   if task.get("ansible.builtin.include_tasks") == "board-ipv6-site.yml")
    assert include["when"] == "not (fixpi_image_build | bool)"
    tasks = {task["name"]: task for task in yaml.safe_load((fixpi / "board-ipv6-site.yml").read_text())}
    link = "{{ nfs_root }}/root/etc/systemd/system/multi-user.target.wants/fpgas-board-ipv6.service"
    enable = tasks["Enable fpgas-board-ipv6.service in the NFS root"]
    disable = tasks["No fpgas-board-ipv6.service link in the NFS root of a site without IPv6 for its boards"]
    assert enable["ansible.builtin.file"]["dest"] == link and enable["ansible.builtin.file"]["state"] == "link"
    assert enable["ansible.builtin.file"]["src"] == "/etc/systemd/system/fpgas-board-ipv6.service"
    assert disable["ansible.builtin.file"] == {"path": link, "state": "absent"}
    on, off, unset = {"pxe_board_ipv6": True}, {"pxe_board_ipv6": False}, {}
    env = jinja2.Environment()  # undefined is off: the variable is the gateway's, and may not be set
    env.filters["bool"] = bool
    for name, task in tasks.items():
        runs = {key: env.compile_expression(task["when"])(**values)
                for key, values in (("on", on), ("off", off), ("unset", unset))}
        assert runs == ({"on": False, "off": True, "unset": True} if task is disable
                        else {"on": True, "off": False, "unset": False}), name


def test_verify_pi_asks_for_ipv6_only_where_the_gateway_has_it_on():
    play = next(p for p in yaml.safe_load(VERIFY_PI.read_text()) if p["name"] == "Verify running Pi")
    assert "hostvars[groups['nbp'][0]].pxe_board_ipv6 | default(false) | bool" in play["vars"]["verify_pi_ipv6"]
    for task in play["tasks"]:
        text = yaml.safe_dump(task)
        if task["name"] in ("Collect the Pi's state", "Results of the collected checks", "Find the per-port IPv6 address"):
            continue
        if re.search(r"verify_pi_ip6|verify_pi_board_ipv6|verify_pi_ra\b|verify_pi_radvd_conf", text):
            assert str(task.get("when", "")).startswith("verify_pi_ipv6 | bool"), task["name"]
    # Off, the collector does not wait for an address either.
    collector = next(t for t in play["tasks"] if t["name"] == "Collect the Pi's state")["vars"]["verify_pi_collector"]
    assert "ip6_deadline = time.monotonic() + {{ 60 if verify_pi_ipv6 | bool else 0 }}" in collector


def test_ci_names_the_tests_it_skipped():
    assert "run: uv run pytest -q -rs tests\n" in (REPO / ".github/workflows/lint.yml").read_text()


# --- verify-pi ------------------------------------------------------------

def test_verify_pi_checks_a_booted_board_for_the_address():
    names = [task["name"] for play in yaml.safe_load(VERIFY_PI.read_text()) for task in play.get("tasks", [])]
    for name in ("Find the per-port IPv6 address",
                 "Assert per-port IPv6 address present",
                 "Assert the board's DHCPv6 client is running",
                 "Assert the board has an IPv6 default route from a router advertisement",
                 "Assert the gateway keeps the board's IPv6 default route fresh",
                 "Assert the board resolves names through the gateway's IPv4 address only",
                 "Assert the netboot interface has the MTU of the gateway's port",
                 "The gateway reaches this Pi on its IPv6 address"):
        assert name in names


def test_the_vm_test_must_prove_the_periodic_advertisement():
    """Minutes after boot a route proves nothing at a site's ten-minute interval.

    So the VM test's gateway advertises every few seconds, and its Pi's
    verification fails if it could not tell a refreshed route from the one
    the Pi asked for at boot.
    """
    inventory = REPO / "tests/inventory/host_vars"
    gateway = yaml.safe_load((inventory / "test-vm.yml").read_text())
    pi = yaml.safe_load((inventory / "test-pi.yml").read_text())
    assert pi["verify_pi_require_periodic_ra"] is True
    # verify-pi.yml can tell once the Pi has been up for two intervals and
    # a little; the virtual Pi takes longer than that to answer ssh.
    assert 10 <= gateway["pxe_ra_interval"] <= 20
