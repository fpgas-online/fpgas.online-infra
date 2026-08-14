# dnsmasq netns lab results

Lab script: `tests/lab/dnsmasq_lab.py`. Run as root: `python3 tests/lab/dnsmasq_lab.py`
(no third-party imports; works without `uv`). Ran on `tweed.welland.mithis.com`
(Debian 12, dnsmasq 2.90) in isolated network namespaces — the production
fpgas network on that host was untouched (system dnsmasq bound to its own
interfaces, `/etc/dnsmasq.d` never edited, service never restarted).

Packages installed on tweed for the lab: `ndisc6` (new), `busybox` (upgraded
to latest Debian 12 point release). `isc-dhcp-client` (`dhclient`) was
already present.

Topology: netns `gw` with two VLAN sub-interfaces (`gwv1.2101`, `gwv2.2102`),
each on its own veth pair to a separate client netns (`pi1`, `pi2`) —
emulating two distinct access ports each carrying one VLAN, both served by
a single dnsmasq process in `gw`. This directly tests the mechanism Tasks
8/9 depend on: ~150 VLAN sub-interfaces on one gateway, addressed and
DHCP-served purely by dnsmasq's automatic "tag = receiving interface name"
behavior.

Every check below passed on the **final** configuration; two of the three
initial guesses from the task brief needed a real change (not just the v4
`/32` guess called out in the brief — the DHCPv6 side needed a bigger fix
than "swap to /16 noprefixroute"). Reran the full script twice back-to-back
(`tests/lab/dnsmasq_lab.py`) to confirm the result is stable, not a fluke of
ordering. Exit code 0 both times.

## v4-single-range-by-tag: PASS — netmask must be broader than /32

**Initial guess (from brief):** interface holds `10.21.0.1/32`,
`dhcp-range=tag:<iface>,10.21.1.1,10.21.1.1,255.255.0.0,12h`, netmask
`255.255.0.0` (already broad — not the brief's literal example, which used
this exact netmask). **This part of the brief's guess worked immediately.**

What was actually tested/iterated: using `255.255.255.255` (a literal `/32`)
as the dhcp-range's own netmask parameter **fails** —
`dnsmasq-dhcp: no address range available for DHCP request via <iface>` —
even though the range is a single address either way. dnsmasq apparently
requires the network implied by (range address, netmask) to contain the
interface's own configured address to treat the interface as "directly
connected"; with a `/32` netmask the range's implied network (`10.21.1.1/32`)
does not contain the interface's `10.21.0.1/32` address, so dnsmasq refuses
to serve it. Broadening the range's netmask to `255.255.0.0` (`/16`) makes
`10.21.0.0/16` contain both addresses, and the request succeeds.

**Working config** (verified twice, in a two-VLAN topology with a second,
different VLAN's range also loaded):
```
interface=<iface>
bind-interfaces
dhcp-leasefile=<scoped-path>          # see leasefile note below
dhcp-range=tag:<iface>,10.21.1.1,10.21.1.1,255.255.0.0,12h
```
Interface config: `ip addr add 10.21.0.1/32 dev <iface>` — the gateway's own
address stays a literal `/32`, identical on every VLAN sub-interface, as the
design requires. Only the **dhcp-range's netmask parameter** needs to be
broader than `/32`; the interface's real prefix is untouched. No
`noprefixroute` needed for v4 — a `/32` has no broadcast/connected-route
ambiguity in the first place (unlike a `/16`, which would create ~150
overlapping connected routes across VLANs if it were the real interface
prefix).

Evidence (busybox udhcpc on the client netns):
```
udhcpc: lease of 10.21.1.1 obtained from 10.21.0.1, lease time 43200
inet 10.21.1.1/32 scope global pi1v.2101
```
dnsmasq log: `tags: gwv1.2101` — confirms the implicit
tag-named-after-the-interface mechanism is what selected the range.

## v6-single-range-by-tag: PASS — gw's own v6 address must match the range's prefix length, not /128

**Initial guess (from brief):** gw interface holds
`2404:e80:a137:2101::ffff/128`,
`dhcp-range=tag:<iface>,2404:e80:a137:2101::1,2404:e80:a137:2101::1,64,12h`.
**FAILED**: `dnsmasq-dhcp: no address range available for DHCPv6 request via
<iface>`.

Iterated: dnsmasq's manpage states the v6 dhcp-range's prefix-length
parameter "must be equal to or larger than the prefix length on the local
interface" (and has a hard minimum of 64). With the interface at `/128`,
setting the range's prefix-len to `64` violates that (64 < 128) — reject.
Setting it to `128` to satisfy "equal to" also fails, because the range's
address (`::1`) and the interface's address (`::ffff`) then define two
different /128 "networks" that don't overlap. The only way to satisfy both
constraints (interface prefix ≤ range prefix, and the interface address
falling inside the range's declared network) is to give the gw interface
address **the same prefix length as the range** — a `/64`, not `/128`. This
is a bigger deviation than the brief anticipated (it only flagged the v4
`/32`→`/16` case as a possible fallback); the v6 side needed the same
"broaden the local address" fix, but for a fundamentally different (and
stricter, dnsmasq-enforced) reason than the v4 side.

**Working config:**
```
dhcp-range=tag:<iface>,2404:e80:a137:2101::1,2404:e80:a137:2101::1,off-link,64,12h
```
(mode flags come *before* the prefix-length field in dnsmasq's v6 syntax —
`<addr>,<addr>,<mode>,<prefix-len>,<lease>` — putting `off-link` after `64`
is a config-file parse error: `dnsmasq: bad dhcp-range`.)

Interface config: `ip addr add 2404:e80:a137:2101::ffff/64 dev <iface>` —
**deviates from the brief's `/128` guess**; the gw's v6 address on each VLAN
sub-interface must carry the same `/64` as that VLAN's dhcp-range. Since
each VLAN gets its own distinct `/64` (unlike the v4 side, which reuses one
`/32` address everywhere), the connected route this creates does not
conflict across VLANs — no `noprefixroute` needed here either, and it was
not tried.

Client used `dhclient -6` (see busybox note below), lease/pid files pinned
under `/var/lib/dhcp/` and `/run/` respectively — Debian's dhclient
AppArmor profile (`/etc/apparmor.d/sbin.dhclient`) rejects writes to
arbitrary paths (e.g. a scratch dir under `/root`), failing with
`can't create <path>: Permission denied` if you point `-lf`/`-pf` elsewhere.

Evidence:
```
RCV:  | | X-- IAADDR 2404:e80:a137:2101::1
PRC: Bound to lease ...
inet6 2404:e80:a137:2101::1/128 scope global tentative dynamic
```
dnsmasq log: `tags: dhcpv6, gwv1.2101`.

## ra-received: PASS — no deviation

`enable-ra` in the conf and a normal per-interface RA come through
unmodified from the brief. `rdisc6 -1 -w 3000 <iface>` returns a reply
`from fe80::...` on the interface's link-local. No radvd needed.

## ra-no-onlink-prefix: PASS — dnsmasq's own `off-link` flag, no radvd needed

**Initial guess (from brief):** if dnsmasq's RA sets the on-link bit,
"switch to radvd with `AdvOnLink off`". **Not needed** — dnsmasq 2.90
already supports an `off-link` mode flag directly on the v6 `dhcp-range`
line (`--dhcp-range=...,off-link,...`; see `man dnsmasq`, `--dhcp-range`
section, IPv6 mode list). Adding it to the range that's already generating
the RA prefix (dnsmasq derives the RA Prefix Information Option from the
matching dhcp-range) clears the L bit with no separate RA daemon.

Evidence (`rdisc6 -1 -w 3000 <iface>`):
```
Stateful address conf.    :          Yes
Stateful other conf.      :          Yes
 Prefix                   : 2404:e80:a137:2101::/64
  On-link                 :           No
  Autonomous address conf.:           No
```
(`Stateful address conf: Yes` = the M bit, confirming DHCPv6 stateful mode
is active alongside the RA, which is what actually hands out the address;
`On-link: No` is the `off-link` flag taking effect; `Autonomous address
conf: No` means the A bit is also clear, so clients won't SLAAC an address
from the advertised prefix either — only DHCPv6 will assign one.)

## v4/v6 two-VLAN isolation (extension beyond the brief): PASS — each client gets only its own interface's address

Requested by the task: verify with two VLAN interfaces (2101, 2102) that a
client on one never receives the other's address. `dnsmasq_lab.py` builds
both VLANs in one topology (§ above) with one dnsmasq process serving both
`interface=` stanzas, then checks each client's assigned address contains
its *own* expected address and does **not** contain the other VLAN's
address, for both v4 and v6.

- `v4-isolation-vlan2`: pi1 (vlan 2101) got `10.21.1.1` only; pi2 (vlan
  2102) got `10.21.2.1` only. Neither client's interface ever showed the
  other's address.
- `v6-isolation-vlan2`: pi1 got `2404:e80:a137:2101::1` only; pi2 got
  `2404:e80:a137:2102::1` only.

This confirms the core mechanism Tasks 8/9 rely on: dnsmasq's implicit
"tag = name of the interface the request arrived on" correctly disambiguates
~150 VLAN sub-interfaces sharing one dnsmasq process and (for v4) one
literal gateway address, purely based on which sub-interface received the
packet — not on the DHCP range's address space, which can (and for v4, by
design, does) overlap across VLANs.

## Other gotchas found while building the lab (not per-check, but load-bearing for Tasks 8/9)

- **Lease file must be pinned per-run.** Without an explicit
  `dhcp-leasefile=`, dnsmasq uses the host-wide default
  (`/var/lib/misc/dnsmasq.leases`). Since the lab's dhcp-ranges are each a
  single address, a stale lease left by an earlier run's netns (different
  random veth MAC) permanently occupies that one address —
  `DHCPDISCOVER ... no address available` (a different message from the
  tag-mismatch failure above; this one *has* a matching range, but its one
  slot is leased to a MAC that no longer exists). Always set
  `dhcp-range` + a fresh, test-scoped `dhcp-leasefile=`.
- **busybox on Debian 12 ships no default udhcpc script** (`/usr/share/
  udhcpc/default.script` does not exist), so `busybox udhcpc` gets a
  DHCPACK but never applies the address to the interface unless you pass
  `-s <script>`. The lab's script only does `ip -4 addr flush` on
  `deconfig` and `ip addr add .../32` on `bound`/`renew` — flushing *all*
  families on deconfig was tried first and is a trap: it also strips the
  kernel's auto-assigned IPv6 link-local address, which then breaks any
  DHCPv6 test run afterwards on the same interface (dhclient -6 fails with
  `no link-local IPv6 address for <iface>`).
- **busybox on this Debian 12 install has no `udhcpc6` applet**
  (`busybox --list` lists `udhcpc`/`udhcpd` only — no v6 client), confirming
  the brief's anticipated fallback. Used `dhclient -6 -v -1` instead, per
  the brief's suggestion. `-1` ("exit after obtaining a lease") does **not**
  actually stop dhclient from daemonizing in practice — leftover `dhclient
  -6` processes were found still running after tests, holding the deleted
  netns open. The lab script now kills dhclient explicitly via its
  `-pf <pidfile>` after each v6 check, from `teardown()`.
- **dnsmasq daemonizes and `chdir("/")`** before opening its `--pid-file`/
  `--log-facility`/`--conf-file` paths if they're given relative — always
  pass absolute paths, or `--pid-file=...` (etc.) silently fails with
  `failed to open pidfile ...: No such file or directory` relative to `/`,
  not your working directory.
- **`ip netns exec` self-inflicted `pkill` hazard** (lab-development
  footgun, not a dnsmasq finding): a `pkill -f <pattern>` used for cleanup
  can match the *invoking* shell's own command line if the working
  directory or script path happens to contain the pattern substring (e.g.
  running cleanup from a directory named `dnsmasq-lab-manual` while
  `pkill -f dnsmasq-lab`). Kill by recorded PID (`--pid-file`) instead of
  pattern matching wherever possible; `dnsmasq_lab.py` does this throughout.

## Summary for Tasks 8/9 templates

- Every VLAN gateway sub-interface: IPv4 `10.21.0.1/32` (literal, identical
  across all ~150 VLANs); IPv6 `<vlan-prefix>::ffff/64` (distinct /64 per
  VLAN, matching whatever prefix that VLAN is delegated).
- dnsmasq, one process, one `interface=` + one v4 `dhcp-range` + one v6
  `dhcp-range` per VLAN:
  - v4: `dhcp-range=tag:<iface>,<v4addr>,<v4addr>,255.255.0.0,12h` — netmask
    must be broader than /32 (any netmask containing both 10.21.0.1 and the
    assigned address works; /16 confirmed).
  - v6: `dhcp-range=tag:<iface>,<v6addr>,<v6addr>,off-link,64,12h` — prefix
    length must equal the interface's actual v6 prefix (64), and `off-link`
    must precede the prefix-length field.
  - `enable-ra` once, globally.
  - `dhcp-leasefile=` pinned to a real, persistent path (not the default) —
    this matters less for production than for repeatable testing, but
    worth keeping explicit either way.
- No radvd required anywhere in this design; dnsmasq's built-in RA plus the
  `off-link` mode flag fully covers goal (c).

## Switch VLAN capacity (design risk #1)

Verified 2026-08-14 against BOTH real switches via
`fpgas.online-poe/scripts/vlan_capacity_probe.py` (VLAN IDs 3000-3149,
deleted after — production block 2101-2348 never touched).

| Switch | Mgmt IP | Model key | Result |
|--------|---------|-----------|--------|
| s3300 (production PoE, "switch 1") | 10.1.5.11 | s3300 (gsm7228ps) | **created=150 verified_present=150**, rc=0 |
| s2 (prototype) | 10.1.5.23 | gsm7252ps | **created=150 verified_present=150**, rc=0 |

Both switches hold 150 simultaneous VLANs with no error — comfortably above
the ~146 the daisy-chained switch-1 trunk needs (its own 48 + two downstream
48-blocks). **Risk #1 (S3300 max concurrent VLANs) is retired.**

SNMP read works with the `public` community on both switches. The per-switch
SNMP **write** communities are resolved on ten64 via
`gdoc2netcfg password --type snmp <switch>` and are stored, per switch, as the
Ansible-vault vars `vault_switch1_snmp_rw_community` (s3300) and
`vault_switch2_snmp_rw_community` (gsm7252ps) — see the switch-vlans role. They
are deliberately not written in plaintext here (this repo is public).

Caveats for the provisioning tool (Task 11 / hardware prototype Task 14):
- The net-snmp CLI transport is ~2s per SNMP op; a full 48-port converge
  (create + 2 membership PDUs + PVID each) runs several minutes. Fine for
  a rare provisioning action; not interactive.
- VLAN create/delete on these switches intermittently returns
  `Error in packet` / `commitFailed` yet the operation still applies (a
  killed probe left an orphan VLAN 3000; bulk delete reported per-VLAN
  failures but ended with the range empty). The provisioning tool's
  diff-based convergence (re-run completes the job) absorbs this; a
  one-shot script must not treat a single write error as fatal to the run.
- A killed converge/probe leaves orphan VLANs; re-running the diff tool
  cleans up (it deletes stale OWN_RANGE VLANs not in desired).
