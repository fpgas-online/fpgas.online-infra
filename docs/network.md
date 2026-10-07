# Network and power

**You want to know which address and name a Pi gets from the switch port it is plugged into, at welland or at
ps1, and why the network is laid out that way.** To power-cycle a board, converge the switches or check
isolation, go to the pages listed under [The tasks](#the-tasks).

Every Raspberry Pi in the fleet sits behind its site's gateway on a private `10.21.x.x` network. ps1 is one
flat `/24` with every Pi on it; welland spreads a `/16` across one VLAN per switch port, so each Pi has a
segment to itself with the gateway as the only thing it can reach. Power is PoE from the same switches, so
"reboot the board" and "cut its port's power" are the same operation.

## The tasks

<a id="poe-power-control"></a>
- [Power-cycling a board](network/power-cycle.md), at welland or at ps1.

<a id="runnable-welland-poe-cycle"></a>
- [Power-cycling a board at welland](network/power-cycle.md#at-welland), from the gateway.

<a id="switches"></a>
- [Converging the switches](network/switches.md): the welland switches' VLANs.

<a id="verifying-isolation"></a>
- [Checking isolation](network/isolation.md): that one Pi cannot reach another.

- [The PoE scripts](network/poe-scripts.md): `poe.sh`, `allpoe.sh`, `allpoeoff.sh`, and why they do not run at welland.

- [Sources](network/sources.md): the files and records these pages come from.


## Two addressing schemes

welland has run **VLAN-per-port** since late August 2026 (fpgas.online-infra PR #10, merged 2026-08-25 Adelaide time): a Pi's identity comes from the
switch port it is plugged into. ps1 runs the **legacy MAC table**: a Pi is recognised by its MAC and handed a
reserved address, so its identity travels with the board rather than the socket.

One inventory variable decides: a gateway whose `host_vars` define `switches:` gets the per-port scheme, one
that does not gets the MAC table. Only `fpgas.online` (welland's gateway, tweed) defines it
(`ansible/inventory/host_vars/` in fpgas.online-infra, main, read 2026-10-07).

That one `is defined` test decides the `tftp_root` expression in `group_vars/all/srv.yml`, picks which half of the
`firewall` role's nftables template is rendered (per-port isolation and DNAT, or the legacy one), and gates the
`vlan_ports` and `switch_vlans` roles in `site.yml`: without it neither runs. The QEMU CI VM defines one switch with a
single access port and `switches_manage: false` (`tests/inventory/host_vars/test-vm.yml`), so every role runs and
the address derivation is exercised against an emulated switch; only the SNMP converge is skipped, for want of real
hardware.

> [!NOTE]
> dnsmasq's own coupling to this is indirect: `dnsmasq-base.conf.j2` tests `dhcp_range is defined`, not `switches`,
> and emits a flat pool if the host has one and nothing if it does not. The two line up only because a per-port host's
> `host_vars` deliberately omits `dhcp_range` (with a comment saying why), so that every port gets its own range from
> `ports.conf.j2` instead (`host_vars/fpgas.online.yml` lines 60-64, main, read 2026-10-07).

For switch index `s` and port `p` at welland, and for port `N` at ps1:

| Item | Per-port (welland) | MAC table (ps1) |
|---|---|---|
| Chosen by | the switch port the cable is in | the Pi's MAC |
| VLAN ID | `2000 + 100*s + p` | none: one flat network |
| IPv4 | `10.21.s.p` | `10.21.0.<100+N>` |
| IPv6 | `2404:e80:a137:210s::p` | none |
| Host name | `pi-sw<s>-p<p>` | `pi<N>` |
| Gateway interface | `v<VLAN>` | `eth-local` |
| SSH port forwarded from outside | `<s><pp>22` | `<100+N>22` |
| Second forwarded port (to the Pi's 4444) | `<s><pp>44` | `<100+N>44` |
| Example | sw1 p7: VLAN 2107, 10.21.1.7, `2404:e80:a137:2101::7`, `pi-sw1-p7`, `v2107`, 10722 | port 7: `pi7`, 10.21.0.107, 10722 |

Read a welland VLAN ID straight: the hundreds digit is the switch number, which is also the IPv4 third octet
and the IPv6 subnet digit; the last two digits are the port, which is also the IPv4 last octet and the IPv6
interface ID. The gateway is `10.21.0.1` under both schemes.

> [!NOTE]
> The two forwarded-port formulas agree numerically for switch 1: legacy port 7 and sw1 p7 both give 10722, because
> `<100+N>` and `<s><pp>` are the same five digits when `s` is 1 (the template's own example: sw1 p7 gives
> 10722/10744, sw2 p7 gives 20722/20744). The two sites are separate hosts with separate uplink addresses, so
> nothing collides; it is arithmetic, not a design guarantee, and it stops holding at switch 2.

welland's switch 1 is the Netgear GSM7252PS (management 10.1.5.23, the head switch: tweed's `eth-local` trunks
into its port 47) and switch 2 is the Netgear S3300-52X-PoE+ (10.1.5.11, behind switch 1's port 50); cabling
confirmed from the switches' own LLDP on 2026-08-22 (comments in `host_vars/fpgas.online.yml`). The design
spec of 2026-08-14 in fpgas.online-infra numbers them the other way round; the inventory is what runs.

> [!NOTE]
> Open, in fpgas.online-infra: nobody has corrected the losing copy. The VLAN-per-port design spec (2026-08-14)
> still numbers the S3300 as switch 1 and the GSM7252PS as switch 2, and the prototype runbook follows it; the
> deployed `host_vars`, the LLDP survey of 2026-08-22 and the [Welland
> page](https://docs.fpgas.online/en/latest/sites/welland.html) say the opposite. Update the spec and the runbook,
> or mark them superseded. (Read on 2026-10-07; the runbook's numbering is also noted on [Checking
> isolation](network/isolation.md).)

The per-port formulas live in one place, the `port_vlan_map` filter in fpgas.online-infra; every VLAN
interface, dnsmasq entry, port forward and host name is derived from the `switches:` list with it, and no
MAC address appears in the per-port dnsmasq configuration, by design (`roles/pxe/templates/ports.conf.j2`).
There is no other per-device state. The gateway service in
[fpgas.online-gw](https://github.com/fpgas-online/fpgas.online-gw) derives the same addresses from a board's
name for both schemes, on purpose: its README says the board-name to address derivation lives only there, so
that the web tier never touches the Pi network (read 2026-10-07).

## Why per-port

The design spec's first goal: *a device on one switch port can exchange traffic only with the gateway;
devices on different ports cannot talk to each other directly through the switch*. A Pi-facing port is an
untagged access port in its own VLAN only, removed from VLAN 1, so VLAN 1 means "unconfigured". Every per-port
VLAN reaches tweed tagged over the trunk, where nftables' `forward` chain drops VLAN-to-VLAN traffic. IPv4
between Pis hairpins through the gateway (proxy ARP for the `/16`) and is dropped there; so is IPv6.

The second goal is that identity follows the socket: a Pi moved to another port becomes that port's host, and
no MAC table is kept. The third is containment: the per-port VLANs exist only on the local switches and on
tweed's `eth-local`, and switch management stays on the house network's VLAN 5, which the converge never
touches.

What a fault looks like (from the design spec):

- **Unconfigured port**: the device gets an address from the quarantine pool, 10.21.0.128 to 10.21.0.150, with no host name, and
  reaches only the gateway.

- **Trunk misconfiguration**: no DHCP at all for that switch.

- **Converge stopped half-way**: running it again finishes the job; the house VLANs were never in what it writes.

- **Pi moved between ports**: it becomes the new port's host; its old lease expires.

"Isolated networks", as the public site puts it, is about the network only: a person with a shell on one Pi
cannot reach another Pi. It does not give a person a board to themselves; nothing in the network decides who
is using which board.

Combined with the read-only NFS root, the damage a user can do is bounded and reverts on the next power cycle.
Nothing in the sources reviewed for the earlier docs page described a lock or a booking mechanism either, so read
"two users could be on one Pi at the same time" as an inference from their absence, not a documented behaviour
(from the earlier docs page, not re-checked).

## Interface naming on the Pi

Some Pis have two Ethernet interfaces: their own, and a USB adapter wired to the FPGA board's Ethernet for
testing it (the Arty hosts). The kernel's order between them is not stable. Two systemd `.link` files in
[fpgas.online-setup-pi](https://github.com/fpgas-online/fpgas.online-setup-pi), `11-eth-uplink.link` and
`12-eth-fpga.link`, name them by USB path rather than order: `eth-uplink` for the Pi's own link, `eth-fpga`
for the adapter. The infra repo's `TECHDEBT.md` (item 1, main, read 2026-10-07) records why: in one instance the
dongle got `eth0` and the onboard NIC `eth1`, and the onboard NIC ended up with both a DHCP lease and the dongle's
static address. They match `Path=platform-3f980000.usb-usb-0:1.1.1:1.0` and
`platform-3f980000.usb-usb-0:1.2:1.0`, so they rename only on a Pi whose USB layout matches those paths.

The gateway does the same for its own two NICs: the `netif` role in fpgas.online-infra writes MAC-matched
`.link` files that name them `eth-local` and `eth-uplink`, which every downstream template refers to by variable. The
`.link` match carries `Type=ether` so that the VLAN children, which inherit the parent's MAC, do not match it and get
renamed too (`roles/netif/templates/link.j2`). Renames apply only when a device is added, so the role reboots once on
a host whose NICs still carry their old names.

## Sources

fpgas.online-infra, main (read 2026-10-07): `docs/superpowers/specs/2026-08-14-vlan-per-port-network-design.md`
(goals, formulas, failure modes); `ansible/inventory/host_vars/fpgas.online.yml` (the `switches:` list and the
cabling of 2026-08-22); `ansible/inventory/host_vars/ps1.fpgas.online.yml`; `ansible/filter_plugins/port_vlans.py`;
`ansible/roles/pxe/templates/ports.conf.j2` (the quarantine pool); `ansible/roles/firewall/templates/nftables.conf.j2`;
`ansible/roles/netif/`. fpgas.online-setup-pi: `fixpi/etc/systemd/network/`.

The full list: [Sources](network/sources.md).
