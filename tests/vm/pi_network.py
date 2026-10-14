"""The virtual Pi's network under systemd-networkd (#230), proved on the running pair.

verify-pi has already checked the steady state: networkd manages the uplink,
the lease file is fresh, the gateway acknowledged the Pi within the lease
time, timesyncd's server came from DHCP, and the site holds a network-state
record from this boot. These steps prove what only a test can provoke.
What keeps the NFS root's address is KeepConfiguration=yes and
IgnoreCarrierLoss=yes in the .network that fpgas-uplink-network writes.

1. boot records: the states the uplink went through at boot reached the
   site as network-state records (configured and routable at least).
2. renewal: with the 5 minute per-port lease, the gateway logs a DHCPACK for
   the Pi within RENEW_WAIT_S (T1 is half the lease).
3. networkd restart: the IPv4 address (lifetime forever), the IPv6 address,
   both default routes and the NFS root survive; the gateway answers a
   global IPv6 ping.
4. gateway DHCP down past the lease: dnsmasq stopped for OUTAGE_S. The Pi
   answers ssh and reads its NFS root every POLL_S, keeps its address with
   lifetime forever, and logs no lost lease. Once dnsmasq is back, it is
   acknowledged again within RENEW_WAIT_S.
5. carrier loss: the cable is pulled for CABLE_OUT_S (QMP set_link). The
   address survives, and the site receives a no-carrier record and then a
   routable one. The no-carrier record waits in the Pi's spool until the
   cable is back.

Each step prints what it read; the first failure returns False.
"""

import json
import shlex
import time

RENEW_WAIT_S = 200      # T1 is 150 s on the 5 min lease; margin for the TCG Pi
OUTAGE_S = 360          # past the 300 s lease (and T2, 262 s)
POLL_S = 30
CABLE_OUT_S = 20
DJANGO_DIR = "/srv/www/pib"
SITE_USER = "admin"
# Files the Pi reads from its NFS root (not from the tmpfs overlay)
NFS_READ = "md5sum /usr/lib/os-release /usr/bin/python3"


class Step(Exception):
    pass


def _forever_v4(pi_sh, address: str) -> str:
    out = pi_sh("ip -o -4 addr show")
    line = next((l for l in out.splitlines() if f" {address}/" in l), "")
    if "valid_lft forever" not in line:
        raise Step(f"{address} is not held with lifetime forever: {out}")
    return line


def _site_records(server_sh, serial: str, since: str = "") -> list[dict]:
    """The site's network-state records for `serial`, oldest first, from the site's database."""
    code = (
        "import json\n"
        "from fleet.models import BootEvent\n"
        f"q = BootEvent.objects.filter(machine__serial={serial!r}, stage='network-state')\n"
        + (f"q = q.filter(ts__gte={since!r})\n" if since else "")
        + "print(json.dumps([{'boot_id': e.boot_id, 'ts': e.ts.isoformat(), 'trigger': e.detail.get('trigger'),"
        " 'operational_state': e.detail.get('operational_state'), 'interface': e.detail.get('interface')}"
        " for e in q.order_by('ts', 'id')]))\n"
    )
    # django_dir and user_name of tests/inventory (group_vars site.yml, host_vars test-vm.yml)
    out = server_sh(f"cd {DJANGO_DIR} && sudo -u {SITE_USER} venv/bin/python manage.py shell -c {shlex.quote(code)}")
    rc, _, body = out.partition("\n")
    lines = [l for l in body.splitlines() if l.startswith("[")]
    if rc != "rc=0" or len(lines) != 1:
        raise Step(f"reading the site's records failed: {out}")
    return json.loads(lines[0])


def _wait_for_ack(server_sh, address: str, mac: str, since_epoch: str, what: str) -> None:
    deadline = time.monotonic() + RENEW_WAIT_S
    pattern = f"DHCPACK(v2101) {address} {mac}"
    while True:
        out = server_sh(f"sudo journalctl -u dnsmasq --since @{since_epoch} --no-pager -o cat")
        acks = [l for l in out.splitlines() if pattern in l]
        if acks:
            print(f"[net] {what}: {acks[-1]}")
            return
        if time.monotonic() > deadline:
            raise Step(f"{what}: no '{pattern}' in the gateway's journal within {RENEW_WAIT_S} s:\n{out[-3000:]}")
        time.sleep(10)


def run(pi_sh, server_sh, set_link, address: str) -> bool:
    """pi_sh/server_sh run a shell command and return "rc=N\\n<output>"; set_link(up) pulls the cable."""
    def pi(cmd: str) -> str:
        out = pi_sh(cmd)
        if not out.startswith("rc=0\n"):
            raise Step(f"on the Pi, `{cmd}`: {out}")
        return out.split("\n", 1)[1]

    def srv(cmd: str) -> str:
        out = server_sh(cmd)
        if not out.startswith("rc=0\n"):
            raise Step(f"on the server, `{cmd}`: {out}")
        return out.split("\n", 1)[1]

    try:
        network = pi("cat /run/systemd/network/10-fpgas-uplink.network")
        uplink = next(l.split("=", 1)[1] for l in network.splitlines() if l.startswith("Name="))
        mac = pi(f"cat /sys/class/net/{uplink}/address").strip()
        serial = pi("sed -n 's/^Serial[[:space:]]*: //p' /proc/cpuinfo").strip()
        boot_id = pi("cat /proc/sys/kernel/random/boot_id").strip()
        gw6 = next((l.split("=", 1)[1] for l in network.splitlines() if l.startswith("Gateway=")), "")
        print(f"[net] uplink {uplink} {mac}, serial {serial}, boot {boot_id}, IPv6 gateway {gw6 or '-'}")

        # 1. boot records
        records = [r for r in _site_records(server_sh, serial) if r["boot_id"] == boot_id]
        print(f"[net] boot records: {[(r['trigger'], r['operational_state']) for r in records]}")
        triggers = {r["trigger"] for r in records}
        if not {"configured", "routable"} <= triggers or any(r["interface"] != uplink for r in records):
            raise Step(f"the site holds no configured and routable records of {uplink} from this boot: {records}")

        # 2. renewal
        mark = srv("date +%s").strip()
        _wait_for_ack(server_sh, address, mac, mark, "renewal")

        # 3. networkd restart
        pi("sudo systemctl restart systemd-networkd && sleep 10")
        line = _forever_v4(pi, address)
        routes = pi("ip -4 route show default; ip -6 addr show scope global; ip -6 route show default")
        print(f"[net] after a networkd restart: {line}\n{routes}")
        if f"default via 10.21.0.1 dev {uplink}" not in routes:
            raise Step(f"no IPv4 default route via the gateway after a networkd restart: {routes}")
        if gw6:
            if f"default via {gw6} dev {uplink}" not in routes or "/128 scope global" not in routes:
                raise Step(f"the IPv6 address or default route via {gw6} did not survive a networkd restart")
            print("[net] " + pi(f"ping -6 -c 2 -W 5 {gw6}"))
        print("[net] " + pi(NFS_READ))

        # 4. the gateway's DHCP down past the lease
        srv("sudo systemctl stop dnsmasq")
        try:
            outage_end = time.monotonic() + OUTAGE_S
            while time.monotonic() < outage_end:
                time.sleep(POLL_S)
                _forever_v4(pi, address)
                pi(NFS_READ)
            print(f"[net] dnsmasq down {OUTAGE_S} s: the Pi kept {address} (forever) and its NFS root")
            log = pi("journalctl -b -u systemd-networkd --no-pager -o cat")
            if "DHCP lease lost" in log:
                raise Step(f"networkd dropped the lease during the outage:\n{log[-3000:]}")
            critical = [l for l in log.splitlines() if "considered critical" in l]
            print(f"[net] networkd: {critical[-1] if critical else 'no expiry seen'}")
        finally:
            mark = srv("date +%s").strip()
            srv("sudo systemctl start dnsmasq")
        _wait_for_ack(server_sh, address, mac, mark, "first DHCPACK after the outage")

        # 5. carrier loss
        since = pi("date -u +%Y-%m-%dT%H:%M:%S+00:00").strip()
        set_link(False)
        time.sleep(CABLE_OUT_S)
        set_link(True)
        time.sleep(30)  # carrier back, networkd routable, the dispatcher's run delivers the spool
        _forever_v4(pi, address)
        print("[net] " + pi("journalctl -b -u networkd-dispatcher --no-pager -o cat | tail -8"))
        deadline = time.monotonic() + 120
        while True:
            states = [r["operational_state"] for r in _site_records(server_sh, serial, since)]
            if "no-carrier" in states and "routable" in states[states.index("no-carrier"):]:
                print(f"[net] cable out and in: the site received {states}")
                break
            if time.monotonic() > deadline:
                raise Step(f"after the cable was out, the site's records since {since} are {states}; "
                           "want no-carrier then routable")
            time.sleep(10)
    except Step as e:
        print(f"ERROR: {e}")
        return False
    return True
