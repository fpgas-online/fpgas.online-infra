# VLAN-per-port network design for fpgas.online

Date: 2026-08-14
Status: draft, awaiting review
Scope: welland site (tweed.welland.mithis.com + Netgear switches).
The ps1 site (FS728TPv2) is explicitly out of scope and stays on the
old MAC-based scheme.

## Goals

1. **Isolation**: a device on one switch port can exchange traffic only
   with the gateway (tweed). Devices on different ports cannot talk to
   each other directly through the switch.
2. **Port-based addressing**: the IP address a device receives is
   determined by the switch port it is plugged into, not by its MAC
   address. Swapping a Raspberry Pi swaps its identity automatically;
   no MAC tables are maintained anywhere.
3. **Scale**: up to three switches behind one gateway.
4. **Containment**: the per-port VLANs exist only on the local switches
   and the gateway's `eth-local` interface. They never appear on the
   house network or `eth-uplink`.

## Non-goals / out of scope

- ps1 site (Netgear FS728TPv2, a Plus-series switch) — unchanged.
- Per-Pi dynamic/privacy IPv6 addresses — Pis get exactly one static
  DHCPv6 address each.
- Changing the PXE/TFTP/NFS boot chain — it is untouched apart from
  who gets which IP.

## Addressing and VLAN plan

For switch `s` (1–3) and port `p` (1–48):

| Item        | Formula                     | sw1 p7                    | sw2 p7                    | sw3 p1                    |
|-------------|-----------------------------|---------------------------|---------------------------|---------------------------|
| VLAN ID     | `2000 + 100*s + p`          | 2107                      | 2207                      | 2301                      |
| IPv4        | `10.21.s.p`                 | 10.21.1.7                 | 10.21.2.7                 | 10.21.3.1                 |
| IPv6        | `2404:e80:a137:210s::p`     | 2404:e80:a137:2101::7     | 2404:e80:a137:2102::7     | 2404:e80:a137:2103::1     |
| Hostname    | `pi-sw<s>-p<p>`             | pi-sw1-p7                 | pi-sw2-p7                 | pi-sw3-p1                 |
| tweed iface | `v<VLAN>`                   | v2107                     | v2207                     | v2301                     |

Reading rule: in the VLAN ID, the hundreds digit is the switch number
(= IPv4 third octet = IPv6 subnet digit) and the last two digits are
the port number (= IPv4 last octet = IPv6 interface ID).

- The subnet remains `10.21.0.0/16`; the gateway remains `10.21.0.1`.
  NFS root paths, `nfsroot=` kernel cmdline, DNS forwarding and the
  firewall's notion of "internal" do not move.
- IPv6 comes from the `2404:e80:a137:2100::/56` already routed to
  tweed; switch `s` uses subnet `210s::/64` of it.
- VLAN range 2101–2348 conflicts with nothing in the site VLAN
  allocation sheet (highest allocated ID today: 141) and is within the
  1–4093 hardware range.
- Ports 49–52 of each switch are reserved for trunks/uplinks and never
  get a per-port VLAN.

## Physical topology and trunks

```
house net (VLAN 5 mgmt, existing) ──┐            ┌── house net (mgmt)
                                    │            │
tweed eth-local ═══ trunk ═══ [switch 1] ═══ trunk ═══ [switch 2] ═══ trunk ═══ [switch 3]
                                 │  │  │          │  │  │              │  │  │
                                 Pi Pi Pi         Pi Pi Pi             Pi Pi Pi
```

- Pi-facing ports: untagged access ports, PVID = the port's VLAN,
  member of **only** that VLAN — explicitly removed from VLAN 1, so
  that VLAN-1 membership cleanly means "unconfigured/quarantine".
- Trunk ports (gateway-facing and inter-switch): tagged members of
  every per-port VLAN that must transit them (switch 1's gateway trunk
  carries all three switches' blocks, ~145 VLANs), VLAN 1 untagged as
  quarantine.
- House-facing management port on each switch: **never touched** by
  the provisioning tool. Management traffic (VLAN 5) never mixes with
  the per-port VLANs, and per-port VLANs are never tagged onto the
  house-facing port.

## Switch management

Management stays exactly as it is today: each switch is managed over
the global **net VLAN 5** (`10.1.5.x`, DHCP/DNS from ten64) via its
existing house-network connection. Nothing management-related rides
the fpgas trunk. Known units:

| Unit | Model | Library model key | Mgmt address |
|------|-------|-------------------|--------------|
| s3300 (production PoE) | S3300-52X-PoE+ | `gsm7228ps` (alias `s3300`) | 10.1.5.11 |
| s2 (prototype) | GSM7252PS | `gsm7252ps` | 10.1.5.23 |

The provisioning tool connects to these management addresses; it can
run from tweed or any host that can reach VLAN 5.

## Component 1: switch provisioning CLI (`fpgas-switch-setup`)

Lives in **fpgas.online-poe** (already the switch-management repo,
already pip-installed on the server). Uses
[python-netgear-switch-library](https://github.com/mithro/python-netgear-switch-library)
(`SyncSwitch`: `create_vlan`, `set_vlan_membership`, `set_pvid`) over
SNMP. The library already encodes the model quirks that matter (the
S3300's split egress/untagged membership writes, the GSM7252PS
`fastpath_switchport` SNMP dialect).

Behaviour:

- Input: switch index, model, management host, port roles (access
  ports, gateway trunk, downstream trunks, house uplink), and which
  downstream VLAN blocks its trunks must carry.
- Converges the switch to the desired state **for the 2101–2348 VLAN
  block only**. It reads current state first and applies diffs; it
  never creates/deletes/modifies VLANs outside its own block and never
  touches the house-uplink port. This keeps the dual-homed switches'
  management plane (VLAN 5) safe even mid-write.
- `--check` prints the diff without applying; exit code signals drift
  (usable from Ansible as a change detector).
- Ordering rule: trunk memberships are written before access-port
  PVIDs so a Pi port is never PVID'd into a VLAN that cannot yet reach
  the gateway.

An Ansible role in fpgas.online-infra invokes the CLI for each entry
in the `switches:` host_vars list.

## Component 2: gateway network config (tweed, systemd-networkd)

A new `vlan-ports` role in fpgas.online-infra generates, from the
`switches:` host_vars list:

- One `.netdev` + `.network` pair per port: VLAN `v2101`…`v2348` on
  `eth-local`. Each `.network` carries:
  - `Address=10.21.0.1/32` (the shared gateway address),
  - a host route `10.21.s.p/32` to that port's Pi,
  - `IPv4ProxyARP=true` (Pis believe /16 is on-link; tweed
    proxy-answers so all Pi↔Pi IPv4 hairpins through the gateway
    where nftables decides),
  - IPv6: link-local + router advertisement per the DHCP section.
- `eth-local` itself: untagged = VLAN 1 quarantine, `10.21.0.1/24`,
  plus `VLAN=` references to all port netdevs.
- The temporary `eth-fpgas` (VLAN 21 over `eth-uplink`) leg is retired
  at cutover; `2404:e80:a137:2100::1/56` moves off the flat interface
  and the /56 is consumed as per-switch /64s.

### Firewall (extension of existing `firewall` role)

nftables `inet` family (covers IPv4+IPv6):

- forward: `v*` ↔ `v*` **default deny**; `v*` → `eth-uplink` allowed
  (existing NAT/route path); established/related back in.
- input on `v*` and `eth-local`: DHCP(v6), DNS, TFTP, NFS, NTP, SSH —
  the services tweed already offers the Pis.
- Opening a specific Pi↔Pi flow later is a one-line nftables rule,
  not a topology change.

## Component 3: DHCP/DNS/PXE (pxe role rework, dnsmasq)

dnsmasq stays as the single DHCP+DHCPv6+RA+DNS+TFTP daemon. The
MAC-table files (`pibs.conf`, `switch.conf`) are deleted, replaced by
a generated `ports.conf` with one stanza per port. Verified against
dnsmasq 2.90 man page: *"a tag whose name is the name of the interface
on which the request arrived is also set"*, and `dhcp-range` accepts
`tag:` selectors.

```
# switch 1 port 7 -> pi-sw1-p7
dhcp-range=tag:v2107,10.21.1.7,10.21.1.7,255.255.0.0,12h
dhcp-range=tag:v2107,2404:e80:a137:2101::7,2404:e80:a137:2101::7,64,12h
host-record=pi-sw1-p7,pi-sw1-p7.fpgas.welland.mithis.com,10.21.1.7,2404:e80:a137:2101::7
```

- Any DHCP client on the port — RPi bootloader ROM (always untagged;
  the PVID stamps it into the VLAN), kernel, or a debug laptop — gets
  that port's addresses. No `dhcp-host` lines exist.
- IPv6 is DHCPv6 managed (M=1, A=0), as the current config already
  does. DHCPv6 addresses are /128 by nature and RAs advertise the
  router without an on-link prefix, so Pi↔Pi IPv6 also routes via
  tweed into the same nftables policy — no NDP proxy needed.
- Quarantine pool on untagged `eth-local` (e.g. 10.21.0.128–150): a
  Pi on an unconfigured port surfaces visibly in `dnsmasq.leases`
  instead of silently joining a network.
- Router/DNS/NTP DHCP options, TFTP and the PXE chain are unchanged.

## Failure modes

- **Unconfigured port** → device lands in VLAN-1 quarantine pool;
  visible, isolated.
- **Trunk misconfiguration** → no DHCP for that switch at all: loud.
- **Provisioning tool crash mid-run** → diff-based convergence means
  re-running completes the job; house VLANs were never in the write
  set.
- **Pi swapped between ports** → it simply becomes the new port's
  identity; the old lease expires.

## Risks to verify FIRST (prototype step 1, before other work)

1. S3300 maximum concurrent VLANs: switch 1's trunk needs ~146; spec
   suggests 256 but must be proven on 10.1.5.11.
2. dnsmasq IPv4: single-address `dhcp-range` selected via interface
   tag on an interface whose only address is `10.21.0.1/32` (range
   selection is subnet-based; the /32 may need to be `10.21.0.1/16`
   with `noprefixroute` or similar).
3. dnsmasq IPv6: same question for the DHCPv6 range, and whether
   dnsmasq's RA can advertise router-only / off-link prefix. Fallback:
   radvd next to dnsmasq.
4. GSM7252PS `fastpath_switchport` SNMP VLAN writes at this scale
   (48 VLANs + memberships in one converge run).

## Testing

- **CI, no hardware**: `fpgas-switch-setup` unit tests run against
  `ngsw serve` mock switches (`gsm7252ps` and `gsm7228ps` models
  exist in the library).
- **VM end-to-end**: extend `tests/vm/network.py` so the server VM's
  Pi-facing link is a Linux vlan-filtering bridge emulating one
  switch: the virtual Pi's tap becomes an untagged access port in
  VLAN 2101. The same `site.yml` / `verify-server.yml` /
  `verify-pi.yml` then prove DHCP-by-port, PXE boot and isolation in
  CI. No QEMU-specific changes to any Ansible role.
- **Hardware prototype** (tweed + s2 GSM7252PS + s3300):
  1. Run the risk checks above.
  2. Provision both switches; diff-check idempotency.
  3. Netboot one Pi per switch; confirm it gets `10.21.s.p` +
     `2404:e80:a137:210s::p` purely from its port.
  4. Prove isolation: Pi cannot reach a neighbour port (IPv4 + IPv6),
     can reach tweed and the internet; hairpin blocked by nftables;
     unconfigured port lands in quarantine.
  5. Move a Pi between ports and watch its identity follow the port.

## Rollout

1. Prototype at welland (eth-local is currently dark, so the fpgas
   network is already offline — low risk). Physical prerequisite:
   cable s2/s3300 trunk to tweed `eth-local`.
2. Land infra roles + poe CLI behind the prototype's results.
3. Cut production over: provision the production S3300, re-cable Pis
   (port assignments = today's ports), retire `eth-fpgas`,
   delete `pibs.conf`.
4. Update dependents: Django site fixtures (new hostnames/IPs),
   fpgas.online-test-designs hardware docs, wiki references to
   `pi<N>` names.

## Config schema (host_vars sketch)

```yaml
pib_network: "10.21"                 # /16 base
pib_network6: "2404:e80:a137:21"     # /56 base, subnets 00-ff

switches:
  - index: 1
    model: s3300                     # library model key or alias
    mgmt_host: sw-netgear-s3300-1.net.welland.mithis.com
    access_ports: 48                 # ports 1..48 get VLANs 2101..2148
    gateway_trunk_port: 49
    downstream_trunk_ports: [50]     # carries switch 2+3 blocks
    house_uplink_port: 52            # NEVER touched by the tool
  - index: 2
    model: gsm7252ps
    mgmt_host: sw-netgear-gsm7252ps-s2.net.welland.mithis.com
    access_ports: 48
    gateway_trunk_port: 49           # uplinks to switch 1 port 50
    downstream_trunk_ports: [50]
    house_uplink_port: 52
```

Everything else (VLAN IDs, interface names, addresses, hostnames,
dnsmasq stanzas, networkd files, nftables sets, switch port maps) is
derived from this list by formula — there is no other per-device
state.
