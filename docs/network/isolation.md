# Checking isolation

**You changed welland's switches or the gateway's firewall and want to prove that a Pi still reaches only
the gateway, that a Pi's identity follows its port, and that an unknown device is quarantined.** These are
stages 6, 7 and 8 of the prototype runbook in fpgas.online-infra
(`docs/superpowers/specs/2026-08-14-vlan-per-port-prototype-runbook.md`); they apply to welland only, since
ps1 has one flat network. You need two Pis on different switches that you may log in to and, for check 2,
one you may move and reboot, plus a laptop for check 3. Visitors use the boards: pick Pis nobody is using.

The runbook numbers the switches the other way round from the inventory: where it says "switch 1" it means
the S3300, which is switch 2 here. The addresses below use the inventory's numbering.

## 1. A Pi reaches the gateway and not another Pi

Log in to a Pi on switch 1 (here `pi-sw1-p1`) and run, for both address families:

```console
$ # these two must FAIL: Pi to Pi is dropped by the gateway's forward chain
$ ping -c3 10.21.2.1
$ ping6 -c3 2404:e80:a137:2102::1
$ # these must SUCCEED: the gateway, on this switch's VLAN
$ ping -c3 10.21.0.1
$ ping6 -c3 2404:e80:a137:2101::ffff
```

Then the same from the Pi on switch 2, the other way round, and from the gateway confirm it reaches both Pis.

The gateway's IPv6 address is per switch: `2404:e80:a137:210<s>::ffff`, so a Pi on switch 1 reaches it at
`…2101::ffff` and a Pi on switch 2 at `…2102::ffff`. No reply from the old `2404:e80:a137:2100::1` is not a
failure; that address is not on the Pi network.

A pass is a silent drop: no reply and no "unreachable", because the packet dies against the forward chain's
`policy drop`. To be sure it never left the gateway, watch the target while the ping runs:

```console
$ # on the TARGET Pi
$ sudo tcpdump -ni any icmp
```

No ICMP from the other Pi may appear. If any does, stop: the firewall did not converge.

## 2. Identity follows the port

Move a Pi from one port to another free port on the same switch, here port 1 to port 3 of switch 1, and power
it up there (a reboot, not a DHCP renew, so the IPv6 state is fresh). A pass is the Pi coming back as
`pi-sw1-p3` at `10.21.1.3` and `2404:e80:a137:2101::3` with nothing changed but the cable. Move it back and it
reverts.

## 3. An unknown device is quarantined

Use a port outside the access range that nothing else uses (`host_vars/fpgas.online.yml`, fpgas.online-infra
main, read 2026-10-07):

- switch 1: ports 41 to 45 are outside its access range (1 to 40) and kept for special items; use one with
  nothing plugged in. Never 46 (tweed's BMC), 47 (the trunk to tweed), 48 (tweed's uplink), 49 and 51
  (another house switch), or 50 (the trunk to switch 2);
- switch 2: every port from 1 to 48 is an access port, 51 is its trunk and 52 the house uplink; 49 and 50 are
  not recorded. There is no free port to use here.

Plug a laptop into that port and watch the leases on the gateway:

```console
$ tail -f /var/lib/misc/dnsmasq.leases
```

A pass is a lease from the quarantine pool, `10.21.0.128` to `10.21.0.150`, with no host name, for one hour
(`ansible/roles/pxe/templates/ports.conf.j2`, fpgas.online-infra main, read 2026-10-07). The laptop reaches the
gateway and no Pi.
