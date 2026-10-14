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
   global IPv6 ping. The link never goes down: the uplink's carrier_down_count
   is the same after the restart as before, and the kernel log shows no
   "Link is Down" for it and no NFS "not responding" or ESTALE since boot
   (at ps1, networkd taking the kernel-configured link down killed the
   NFS mount).
4. gateway DHCP down past the lease: dnsmasq stopped for OUTAGE_S. The Pi
   answers ssh and reads its NFS root every POLL_S and keeps its address
   with lifetime forever. Its lease does expire (networkd logs "DHCPv4
   connection considered critical") and networkd drops nothing ("DHCP lease
   lost" never appears). Once dnsmasq is back, it is acknowledged again
   within RENEW_WAIT_S.
6. the negative control: the generator's DHCP-only .network in place of ours,
   and networkd restarted, drops the address and loses the Pi (as at ps1:
   help-carl report1.md 3.2-3.4, logs 071 and 072). It shows that the steps
   above can fail. The second boot (run_tests.second_boot) resets the Pi
   afterwards.
5. carrier loss: the cable is pulled for CABLE_OUT_S (QMP set_link). The
   site receives a no-carrier record and then a routable one, and the
   no-carrier record, taken while the cable was out, still lists the port's
   IPv4 and IPv6 addresses. Without IgnoreCarrierLoss=yes networkd would have
   removed them at that point. Getting the same address back by DHCP later
   would hide that, so this is checked in the record rather than after the
   cable is back. The record is read about a second after the carrier loss,
   and networkd drops the configuration in the carrier-loss handler itself
   (IgnoreCarrierLoss= unset means no delay), so a drop shows in it. The no-carrier record waits in the Pi's spool until the
   broker can be reached.

Each step prints what it read; the first failure returns False.
"""

import json
import re
import shlex
import time

RENEW_WAIT_S = 200      # T1 is 150 s on the 5 min lease; margin for the TCG Pi
OUTAGE_S = 360          # past the 300 s lease (and T2, 262 s)
POLL_S = 30
CABLE_OUT_S = 20
DJANGO_DIR = "/srv/www/pib"
SITE_USER = "admin"
# Read from the NFS root (not the tmpfs overlay), with the page cache dropped first so the bytes come from the
# server, not from memory.
NFS_READ = "sync && echo 3 | sudo tee /proc/sys/vm/drop_caches && md5sum /usr/lib/os-release /usr/bin/python3"


class Step(Exception):
    pass


def _forever_v4(pi_sh, address: str) -> str:
    out = pi_sh("ip -o -4 addr show")
    line = next((l for l in out.splitlines() if f" {address}/" in l), "")
    if "valid_lft forever" not in line:
        raise Step(f"{address} is not held with lifetime forever: {out}")
    return line


def _site_records(server_sh, serial: str, after_id: int = 0) -> list[dict]:
    """The site's network-state records for `serial` with an id above `after_id`, in arrival order, from the site's
    database. Arrival order, not the record's time: that is the Pi's clock."""
    code = (
        "import json\n"
        "from fleet.models import BootEvent\n"
        f"q = BootEvent.objects.filter(machine__serial={serial!r}, stage='network-state')\n"
        + f"q = q.filter(id__gt={int(after_id)})\n"
        + "print(json.dumps([{'id': e.id, 'boot_id': e.boot_id, 'addresses': e.detail.get('addresses'), 'ts': e.ts.isoformat(), 'trigger': e.detail.get('trigger'),"
        " 'operational_state': e.detail.get('operational_state'), 'interface': e.detail.get('interface')}"
        " for e in q.order_by('id')]))\n"
    )
    # django_dir and user_name of tests/inventory (group_vars site.yml, host_vars test-vm.yml)
    out = server_sh(f"cd {DJANGO_DIR} && sudo -u {SITE_USER} venv/bin/python manage.py shell --verbosity=0 -c {shlex.quote(code)}")
    rc, _, body = out.partition("\n")
    lines = [l for l in body.splitlines() if l.startswith("[")]
    if rc != "rc=0" or len(lines) != 1:
        raise Step(f"reading the site's records failed: {out}")
    return json.loads(lines[0])


PING_LOG = "ping-uplink.log"  # in the server user's home, not /tmp


def _ping_start(srv, address: str, seconds: int) -> None:
    """Ping the Pi from the server every 0.2 s for `seconds`, in the background, with timestamps (-D)."""
    srv(f"rm -f {PING_LOG}; (nohup ping -D -n -i 0.2 -w {seconds} {address} > {PING_LOG} 2>&1 &)")


def _ping_gap(srv, seconds: int) -> tuple[float, int]:
    """(the longest time between two answers, the number of answers) once the background ping has ended."""
    time.sleep(seconds + 2)
    stamps = [float(m) for m in re.findall(r"^\[(\d+\.\d+)\] \d+ bytes from", srv(f"cat {PING_LOG}"), re.M)]
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    return (max(gaps) if gaps else float(seconds)), len(stamps)


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


def run(pi_sh, server_sh, set_link, address: str) -> str | None:
    """pi_sh/server_sh run a shell command and return "rc=N\\n<output>"; set_link(up) pulls the cable. Returns the
    boot id the steps ran in (the Pi is unreachable after the negative control), or None on a failure."""
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
        mac = next(l.split("=", 1)[1].strip() for l in network.splitlines() if l.startswith("MACAddress="))
        uplink = pi(f"grep -l -x {mac} /sys/class/net/*/address").strip().split("/")[-2]
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

        # 3. networkd restart. The link must not go down (at ps1, networkd taking the kernel-configured link down
        # killed the NFS mount): the carrier_down_count is the same after as before, and since the boot.
        down_before = pi(f"cat /sys/class/net/{uplink}/carrier_down_count").strip()
        _ping_start(srv, address, 25)
        pi("sudo systemctl restart systemd-networkd && sleep 10")
        down_after = pi(f"cat /sys/class/net/{uplink}/carrier_down_count").strip()
        gap, answers = _ping_gap(srv, 25)
        if gap > 1.0 or answers < 25:
            raise Step(f"the gateway's ping lost the Pi across the networkd restart: longest gap {gap:.2f} s, "
                       f"{answers} answers in 25 s")
        print(f"[net] networkd restart: the gateway's ping (every 0.2 s) saw at most {gap:.2f} s between answers")
        if down_after != down_before:
            raise Step(f"{uplink} went down during the networkd restart: carrier_down_count {down_before} -> {down_after}")
        print(f"[net] networkd restart: {uplink} carrier_down_count stayed {down_before}")
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

        # No link loss and no NFS trouble in the kernel log so far (boot, networkd's start and restart, the
        # outage below adds none either; the cable pull in step 5 is the first deliberate link loss).
        kernel = pi("sudo dmesg")
        trouble = [l for l in kernel.splitlines()
                   if (uplink in l and "Link is Down" in l) or "not responding" in l or "ESTALE" in l
                   or ("nfs" in l.lower() and " EIO" in l)]
        if trouble:
            raise Step("the kernel log shows the uplink down or NFS trouble before any deliberate link loss:\n"
                       + "\n".join(trouble))
        print(f"[net] kernel log: no '{uplink}: Link is Down' and no NFS trouble since boot")

        # 4. the gateway's DHCP down past the lease
        outage_mark = pi("date +%s").strip()
        srv("sudo systemctl stop dnsmasq")
        try:
            outage_end = time.monotonic() + OUTAGE_S
            while time.monotonic() < outage_end:
                time.sleep(POLL_S)
                _forever_v4(pi, address)
                pi(NFS_READ)
            print(f"[net] dnsmasq down {OUTAGE_S} s: the Pi kept {address} (forever) and its NFS root")
            # From the outage's start only: step 3's restart logs the same notice when the client stops.
            log = pi(f"journalctl -u systemd-networkd --since @{outage_mark} --no-pager -o cat")
            if "DHCP lease lost" in log:
                raise Step(f"networkd dropped the lease during the outage:\n{log[-3000:]}")
            critical = [l for l in log.splitlines() if "considered critical" in l]
            if not critical:
                raise Step(f"the lease never expired in {OUTAGE_S} s without the gateway's DHCP, so the outage "
                           f"proved nothing:\n{log[-3000:]}")
            print(f"[net] networkd: {critical[-1]}")
        finally:
            mark = srv("date +%s").strip()
            srv("sudo systemctl start dnsmasq")
        _wait_for_ack(server_sh, address, mac, mark, "first DHCPACK after the outage")

        # still none after the outage
        kernel = pi("sudo dmesg")
        trouble = [l for l in kernel.splitlines()
                   if (uplink in l and "Link is Down" in l) or "not responding" in l or "ESTALE" in l]
        if trouble:
            raise Step("the kernel log shows the uplink down or NFS trouble after the DHCP outage:\n" + "\n".join(trouble))

        # 5. carrier loss
        before = max((r["id"] for r in _site_records(server_sh, serial)), default=0)
        set_link(False)
        time.sleep(CABLE_OUT_S)
        set_link(True)
        time.sleep(30)  # carrier back, networkd routable, the dispatcher's run delivers the spool
        _forever_v4(pi, address)
        print("[net] " + pi("journalctl -b -u networkd-dispatcher --no-pager -o cat | tail -8"))
        deadline = time.monotonic() + 120
        while True:
            records = _site_records(server_sh, serial, before)
            states = [r["operational_state"] for r in records]
            if "no-carrier" in states and "routable" in states[states.index("no-carrier"):]:
                print(f"[net] cable out and in: the site received {states}")
                held = {a["address"] for a in records[states.index("no-carrier")]["addresses"]}
                want = {address} | ({gw6.rsplit("::", 1)[0] + "::" + address.rsplit(".", 1)[1]} if gw6 else set())
                if not want <= held:
                    raise Step(f"while the cable was out the uplink held {sorted(held)}, not {sorted(want)}: "
                               "networkd removed the address on carrier loss")
                print(f"[net] while the cable was out the uplink still held {sorted(want)}")
                break
            if time.monotonic() > deadline:
                raise Step(f"after the cable was out, the site's records after id {before} are {states}; "
                           "want no-carrier then routable")
            time.sleep(10)
        # 6. The negative control (lab only): what killed the NFS root at ps1. Replace our .network with what
        # systemd-network-generator would write from ip=dhcp (DHCP only, no KeepConfiguration) and restart networkd.
        # On this overlay root networkd's own network-root default does not apply (systemd 252 checks / with
        # statfs: the tmpfs overlay, not NFS), so it drops the kernel's address: the gateway loses the Pi for well
        # over a second. That shows the steps above can fail. The Pi is reset afterwards (the second boot).
        _ping_start(srv, address, 25)
        pi("sudo systemd-run --unit=fpgas-negative-control --on-active=3 /bin/sh -c "
           + shlex.quote("rm -f /run/systemd/network/10-fpgas-uplink.network && printf '[Match]\\nName="
                         + uplink + "\\n\\n[Network]\\nDHCP=ipv4\\n' > /run/systemd/network/71-default.network"
                         " && systemctl restart systemd-networkd"))
        gap, answers = _ping_gap(srv, 25)
        if gap <= 1.0 and answers >= 100:
            raise Step(f"the negative control did not break the link (longest gap {gap:.2f} s, {answers} answers): "
                       "a DHCP-only .network should have dropped the kernel's address, so these steps prove nothing")
        print(f"[net] negative control (the generator's DHCP-only .network): the gateway lost the Pi, longest gap "
              f"{gap:.2f} s, {answers} answers in 25 s, as at ps1")
    except Step as e:
        print(f"ERROR: {e}")
        return None
    return boot_id
