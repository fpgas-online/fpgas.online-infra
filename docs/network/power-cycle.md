# Power-cycling a board

**You want to power-cycle one board's Pi, at welland or at ps1: it has hung, or it must boot a new NFS root.**
Cutting the Pi's PoE on its switch port is the only remote reset; there is no other power control. The Pi
loses everything it held (its root is read-only with a tmpfs on top, [Netboot and the NFS root](https://docs.fpgas.online/en/latest/setup/netboot.html)),
and anyone using the board loses their session. Visitors use the boards at any time; that is expected, so cut
a port only when it is needed.

## At welland

**First, check who is on the port.** The port is in the Pi's name: `pi-sw2-p46` is switch 2, port 46, at
`10.21.2.46`. On the welland gateway, that address must be answered by the MAC of the Pi you mean (the table of
6 October 2026 on [Hosts and boards at welland](https://docs.fpgas.online/en/latest/sites/welland-boards.html#what-was-up-on-6-october-2026) gives each
port's MAC):

```console
$ grep ' 10.21.2.46 ' /var/lib/misc/dnsmasq.leases
$ ip -4 neigh show 10.21.2.46
```

A netbooted Pi never renews its DHCP lease, so 12 hours after it boots its lease is gone while it runs on
(fpgas.online-infra issue #230). The neighbour entry names the MAC that last answered; it can stay after the
Pi has gone (it did while ports were off on 6 October 2026), so it identifies the board but does not prove it
is up. If another MAC answers, stop: something was moved.

**From the board's page.** A board page on welland.fpgas.online that has a Reset button power-cycles its
port: off, half a second, on (`src/snmp_switch/views.py` in fpgas.online-poe, main fcb4a2a). It refuses a port
that is not a board the site offers, and a second cycle of the same port within 60 seconds by default
(fpgas.online-poe `README.md`). The switch settings it needs reached the welland gateway with
fpgas.online-infra PR #84, merged 2026-09-14. The NeTV2 hosts have no board page.

**From the gateway**, as root, with the switch's SNMP write community. Both welland switches answer the
standard PoE MIB (`pethPsePortAdminEnable`, `1.3.6.1.2.1.105.1.1.1.3.1.<port>`) over SNMP v2c: the
fpgas.online coordinator switched welland ports off and on through it, with fpgas.online-poe's `netgear_switch`
library, after the NFS root updates of 5 and 6 October 2026. The communities are in the vault of
fpgas.online-infra and are rendered, root-only, into `/etc/systemd/system/gunicorn.service.d/poe.conf` as
`FPGAS_SWITCH_COMMUNITY_<switch>` (the `site` role, `templates/gunicorn-poe.conf.j2`). Never copy one into a
file, a commit or a page.

| Switch | Management address | Up on it on 6 October 2026 (the gateway's neighbour table) |
|---|---|---|
| 1, Netgear GSM7252PS | 10.1.5.23 | NeTV2 hosts on ports 10, 12, 14, 16, 18; the Fomu host on 17; an Acorn host on 38 |
| 2, Netgear S3300-52X-PoE+ | 10.1.5.11 | Orange Pis on 19, 21, 22, 24; their hub host on 30; Tiny Tapeout FPGA hosts on 33, 35, 36; Acorn hosts on 46, 47 |

Run the cycle as one line, so that a dropped connection cannot leave the port off, and read the port before
and after:

```console
$ # the switch, its write community (read it from poe.conf; it is not printed here), and the port
$ export SW=10.1.5.23 COMMUNITY='<switch-write-community>' PORT=14
$ OID=1.3.6.1.2.1.105.1.1.1.3.1.$PORT
$ # read: admin state (1 on, 2 off) and delivery (3 = delivering power)
$ snmpget -v2c -c "$COMMUNITY" -Ovq $SW $OID 1.3.6.1.2.1.105.1.1.1.6.1.$PORT
$ # off, a few seconds, on
$ snmpset -v2c -c "$COMMUNITY" $SW $OID i 2 && sleep 5 && snmpset -v2c -c "$COMMUNITY" $SW $OID i 1
$ # read again: expect 1, then 3 once the Pi draws power
$ snmpget -v2c -c "$COMMUNITY" -Ovq $SW $OID 1.3.6.1.2.1.105.1.1.1.6.1.$PORT
```

This `snmpset` line has not been run as written by fpgas.online; the power cycles of 5 and 6 October 2026 went
through fpgas.online-poe's `netgear_switch` library instead. If the line stopped after "off", run the "on"
`snmpset` by itself, then read again. The port number is the
switch port (`p` in the Pi's name), not a VLAN or an address. The Pi is back when its SSH port answers: from
the gateway, `nc -z -w 3 10.21.<switch>.<port> 22` (`10.21.1.14` for switch 1, port 14).

`poe.sh` and `allpoe.sh` from fpgas.online-poe do not work at welland: the `snmp.yml` task that writes their
settings is skipped on a per-port gateway, by design (its comment in fpgas.online-infra, main).

## At ps1

At ps1 the gateway, its switch and its power-cycling tool are Carl Karsten's own, not fpgas.online's; the
procedure, with the checks to make first, is on [The ps1 gateway and switch: power
control](https://docs.fpgas.online/en/latest/sites/ps1-gateway.html#power-control).

## How long the board is gone

The Pi netboots again: a kernel and a root over the network, not a resume from disk. The test automation
allows about two minutes from power-on to SSH (`docs/verify-hardware.md` in fpgas.online-test-designs). [Acorns at
welland](https://docs.fpgas.online/en/latest/boards/acorn/installations/welland.html#reads-of-september-2026) records, from its reads of September
2026, that a Pi 5 there takes more than 90 seconds, and that a hung one draws about 0.4 W on its port instead of
about 8 W; not measured again since.

## After an NFS root update

A Pi that booted before the root was replaced fails to open the new files (`ESTALE`); [Netboot and the NFS
root](https://docs.fpgas.online/en/latest/setup/netboot.html) says why. Each Pi's watchdog reboots it on its own once it sees a newer root: after the
update of 6 October 2026 at welland, the Orange Pis, which were left to it, were back by 08:57, 36 minutes after
the 08:21 swap. To have a board back sooner, power-cycle its port as above; on 6 October 2026 twelve
power-cycled boards were all back within 9 minutes.
