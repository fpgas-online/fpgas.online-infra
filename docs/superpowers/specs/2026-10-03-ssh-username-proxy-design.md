# Design: per-board SSH names and a username-routing SSH proxy

Date: 2026-10-03, revised 2026-10-04
Status: proposed. Revised on 2026-10-04 for the owner's decisions D1, D2, D3
and D5 (see [What changed on 2026-10-04 and why](#what-changed-on-2026-10-04-and-why)).
Still nothing here is implemented or deployed. [Decisions](#decisions) says
what is decided and by whom, and what is open. The work is tracked by #191
and the issues under [Work items](#work-items).

Words used here: the **gateway** is the site's fpgas.online gateway, the host
this repository configures. The **upstream gateway** is the separately
managed router a site may sit behind; it holds the site's public IPv4 address
and is not part of fpgas.online. The **fleet key** is the one ssh host key
that every board presents, because every board boots the same root.

## What changed on 2026-10-04 and why

Tim decided four of the open questions on 2026-10-04. His words:

- **D1**, asked whether the upstream gateway should forward public IPv4 port
  22 to the proxy on the gateway:

  > The site's gateway should get it's own proxy which works in a somewhat similar manner to the proxy on the fpgas.online gateway. piXXX users will be forwarded the fpgas.online gateway. As a backup, port 2222 should go to ten64.welland's ssh server and port 2223 should forwarded directly to gw.welland.fpgas.online/tweed.welland.mithis.com ssh server and port 2224 should forward directly to gw.welland.fpgas.online/tweed.welland.mithis.com ssh proxy.

  Reading: public IPv4 port 22 is a username-routing ssh proxy **on the
  upstream gateway**. Logins under board names (`pi…`) are forwarded to the
  gateway's proxy; other names stay with the upstream gateway. Public port
  2222 is the upstream gateway's own sshd, 2223 is forwarded to the gateway's
  own sshd, and 2224 is forwarded to the gateway's ssh proxy. The upstream
  proxy and the forwards are not built from this repository: they are
  requirements on the upstream network.

- **D2**, asked which host key the proxy presents, the fleet key or one of
  its own:

  > Unclear, the ssh host key is on the rpi and available to anyone who logs into that system, so should *not* be what encrypts traffic to gw.welland.fpgas.online

  Reading: the first version's recommendation, that the proxy presents the
  fleet key, is rejected. The proxy presents a key of its own. He also found
  the question unclear, so this revision explains host keys in plain words
  ([Host keys in plain words](#host-keys-in-plain-words)).

- **D3**, asked how a client inside the site reaches a board by name over
  IPv4:

  > Other devices behind ten64.welland.mithis.com should access all welland.fpgas.online resources via gw.welland.fpgas.online and look pretty much like any internet device except with a private IP addresses. Devices inside XXX.welland.fpgas.online should not be accessing other XXX.welland.fpgas.online devices, they should only be talking to gw.welland.fpgas.online -- IE really pi1.welland.fgpas.online<->gw.welland.fgpas.online should be considered a direct point to point link.

  Reading: (a) devices on the site's own LAN get no special path to the
  boards. They reach boards the way an internet client does, through the
  gateway, only arriving from private addresses. (b) A board talks only to
  the gateway. The link between a board and the gateway is a point-to-point
  link, and boards never talk to each other.

- **D5**, asked how sshpiper is packaged:

  > Mirror repo fpgas-online/sshpiper; I will create it

  Reading: the mirror-repository option. The work waits on him creating the
  repository.

**What rested on the shared key.** The first version had one key on every
path so that one name per board, `pi-sw1-p7.<site-domain>`, could be answered
by the proxy over IPv4 and by the board over IPv6 without the client
noticing. With the proxy on a key of its own, everything built on that goes:

- **The shared board name.** A board name with an A record at the proxy and
  an AAAA record at the board would now be answered by two keys. It is
  replaced by login names for the proxied paths and AAAA-only board names for
  the direct path ([One name, one key](#one-name-one-key)).
- **One SSHFP record on every name.** Each name now carries the fingerprint
  of the key that answers it on port 22, or no SSHFP record.
- **The `ipv4.`, `ipv6.` and `private-ipv4.` helper names and the split
  horizon.** They existed to pick a path under a shared name and key. The
  name now picks the path. D3 removes the internal view as well.
- **Copying the fleet key to the proxy**, and the proxy's key rotating with
  the fleet key. The proxy's key is generated for the gateway and never
  enters the boards' root.
- **"One key" verification**: the test that the proxy and a board return the
  same key, and the IPv4, IPv6, IPv6, IPv4 login sequence under one name.
- **The trust statement** that an SSHFP match proves "a board or proxy at
  this site". The proxy and the boards now prove different things.
- **The warning attached to D1** that the fleet key would start answering on
  the upstream gateway's public port 22. It will not: the upstream gateway's
  own proxy answers there with its own key.

What did not rest on it and stays: the routing rules, the mapping key the
proxy logs in to boards with, the login-penalty exemption and the phase 2
mechanism on the gateway, and, if D9 is yes, the login aliases and the direct
IPv6 firewall rule.

Further changes in this revision:

- D6 (refuse unknown usernames) and D7 (login name is the board hostname) are
  recorded as decided by engineering.
- New engineering decisions: D8 (one name, one key), D10 (boards cannot reach
  the site's own public ssh entry points), D11 (public port 22 behind an
  upstream gateway is password-only, and the upstream gateway holds no
  fpgas.online credential) and D12 (both fingerprints are published while the
  fleet key rotates).
- A new question for Tim, D9: whether the direct IPv6 path to boards is
  wanted, given D2 and D3.
- The phase 2 patch is carried in our own package only. Nothing is sent to
  the sshpiper project.
- The firewall section adds an explicit drop of the proxy port for the board
  VLANs. The existing input chain accepts everything from the board network,
  so "accept on the uplink only" was not enough.
- "Current state" is corrected on two facts: what the public zone holds
  today, and that board-to-board isolation has a hole (#204).

### Issues that change

- fpgas-online/fpgas.online-infra#186 (boards offer only Ed25519, accept
  login aliases and the proxy's mapping key): scope unchanged, and the alias
  part waits on D9. The reason for Ed25519-only changes: one published
  fingerprint and one SSHFP record per board name, not "the same key as the
  proxy". Stale in its text: "the same one the gateway proxy presents"; the
  Ed25519 bullet's reasoning about "keys the proxy does not present" (the
  proxy now drops the announcement whatever the boards offer); and its
  "Related" paragraph, because the proxy has no copy of the fleet key and
  only its pins and the board names' SSHFP records follow the root's key.
- fpgas-online/fpgas.online-infra#187 (gateway role running sshpiper):
  - The proxy's host key is its own, from the vault, never the fleet key.
  - One instance listens on port 2224 on every address, fenced by the
    firewall; port 22 on the proxy's own IPv6 address is rewritten to it
    (`dnat to :2224`). `failtoban` gets explicit `--max-failures` and
    `--ban-duration` values.
  - `--drop-hostkeys-message` becomes required.
  - `failtoban` runs with `--ignore-ip` for the upstream gateway's address.
    The line "it sees the real client address" is true only for port 2224
    and IPv6.
  - The firewall adds a drop of the proxy port from the board VLANs.
  - The verify step "matches the root's host key" becomes "matches the
    proxy's own key and differs from the fleet key and the gateway's sshd
    key".
  - "Depends on … D1 (the site router forwarding public IPv4 port 22 to this
    port)" is stale: D1 is now an upstream proxy plus a forward of 2224, and
    the role does not depend on either to be built and tested.
  - Its reference to the section "Host keys: one key on every path" is to a
    removed section; the section is now "The proxy's host key".
  - The example command becomes `ssh pi-sw1-p7@ssh.<site-domain>`.
- fpgas-online/fpgas.online-infra#188 (firewall: direct IPv6 and internal
  IPv4 to boards on 22): the title's "(and from site networks over IPv4)" and
  the `ssh_direct_ipv4_sources` item are dropped (D3). The IPv6 rule stays,
  subject to D9. It gains the drop of connections from the board VLANs to the
  site's own public ssh addresses and ports (D10).
  fpgas-online/fpgas.online-infra#204 (a board can reach another board's sshd
  through the gateway's per-board DNAT ports) is a prerequisite.
- fpgas-online/fpgas.online-infra#189 (DNS names and SSHFP): its section
  reference "Names and DNS records" becomes "One name, one key". New record
  set: `ssh.<site-domain>` (A only, no SSHFP), `gw.<site-domain>` (AAAA only,
  SSHFP for every host key the gateway's sshd offers), `proxy.<site-domain>`
  (AAAA only, the proxy key's SSHFP) and AAAA-only board names with the fleet
  key's SSHFP (subject to D9). It must **remove** what the public zone holds
  today: the per-port names' private A records, and the CNAME from
  `gw.<site-domain>` to the web name. No `ipv4.`, `ipv6.` or `private-ipv4.`
  names, no internal view, no change to the gateway's internal dnsmasq zone.
- fpgas-online/fpgas.online-infra#190 (phase 2 transparent IPv4): unchanged
  for connections that reach the gateway's proxy directly (public port 2224).
  For public port 22 the client's address must first survive the upstream
  gateway's proxy, which is an open point for the upstream. If the upstream
  proxy sends PROXY headers, the role gains `--allowed-proxy-addresses` and
  the patch dials from the header's address.
- fpgas-online/apt#21 (package sshpiper): built from the mirror repository
  fpgas-online/sshpiper (D5, waiting on Tim to create it). The phase 2 patch
  stays in our package; it is not offered to the sshpiper project.
- fpgas-online/fpgas.online-gw#2 (`/api/boards` `ssh` object): `host` is
  `ssh.<site-domain>`, and new `host_ipv6` and `direct_host` carry
  `proxy.<site-domain>` and the board's own name.
- fpgas-online/fpgas.online-site#44 (board pages show the new command): the
  pages show `ssh pi-sw1-p7@ssh.<site-domain>`, the IPv6 form with
  `proxy.<site-domain>`, and, if D9 keeps it, the direct command and the
  fleet key fingerprint.
- fpgas-online/fpgas.online-docs#16 (user documentation): the commands, what
  each first-connection prompt means, that port 22 over IPv4 is
  password-only, the "Too many authentication failures" line, and never the
  site's web name for ssh.
- New, outside these repositories: the upstream gateway's proxy on public
  port 22 and the forwards on 2223 and 2224, written as requirements in the
  docs repository page "What a site needs from its upstream network". That
  page's tcp 2224 row says the proxy's port "is not fixed yet"; it is 2224.
- fpgas-online/fpgas.online-infra#191 (tracking): its goal sentence and its
  "Decisions needed" list are out of date; copy from this section and from
  [Decisions](#decisions).

## Problem

Users reach a board today through a per-board port on the site's one public
IPv4 address. fpgas.online-site prints it on the board page (`Pi.ssh_port`):

```
ssh -p 10722 pi@welland.fpgas.online
```

The port is the gateway's per-port DNAT
([`roles/firewall`](../../../ansible/roles/firewall/templates/nftables.conf.j2),
`<switch><pp>22 -> 10.21.<s>.<p>:22`). This has three problems:

1. **The port has to be copied from a web page.** Nothing in DNS can tell an
   SSH client which port to use (see [Findings](#findings-that-shape-the-design)).
2. **The host key cannot be checked against DNS.** SSHFP is looked up by
   name only, and every board shares the one name `welland.fpgas.online`.
3. **IPv6 is not used**, although every board already has a global IPv6
   address.

## Goal

Every board can be reached with a stock OpenSSH client, no client
configuration and no port number:

```
ssh pi-sw1-p7@ssh.<site-domain>        # through the proxies; IPv4
ssh pi-sw1-p7@proxy.<site-domain>      # through the gateway's proxy; IPv6
ssh pi@pi-sw1-p7.<site-domain>         # straight to the board; IPv6 (if D9 is yes)
```

Each name is always answered by the same key, so a default client is
prompted once per name and never sees a changed-key warning because it took a
different path. Users who opt in can check the fpgas.online keys against DNS
(SSHFP).

- **Phase 1:** a username-routing SSH proxy (sshpiper) on the gateway, and
  direct IPv6 to the boards (if D9 is yes). The board sees the gateway's address as the
  source of proxied connections.
- **Phase 2:** transparent IPv4 source rewriting, so the board sees the
  client's real address on proxied connections too.

## Non-goals

- Changing the gateway's own sshd ([`docs/access.md`](../../access.md)). It
  keeps port 22 on the gateway's own addresses, for operators, Ansible and
  the jump account. D1 adds one route to it: public IPv4 port 2223.
- Building the upstream gateway's proxy or its port forwards. The upstream
  network is separately managed. This document states what the gateway
  needs from it ([Requirements on the upstream network](#requirements-on-the-upstream-network)).
- Removing the per-port DNAT. It stays as the legacy path until the board
  pages stop printing it. Removing it is a separately dated step after the
  proxy has run for an agreed period (and see D9).
- An IPv6-only Pi network. Pi 3 network boot is IPv4-only, Pi 4 IPv6 boot is an
  experimental alpha that needs ISC DHCP, and the Orange Pi FEL/U-Boot path and
  the NFS root are IPv4. The proxy-to-board hop could later use IPv6 with the
  client's IPv4 address embedded (RFC 6052), but phase 2 does not need it.
- Per-board host keys. Every board shares one NFS root and so one host key
  (access.md, "Host key"). This design keeps that; see
  [The paths, and what each key proves](#the-paths-and-what-each-key-proves).
- A path from the site's own LAN to the boards, or from one board to another
  (D3).
- ps1.fpgas.online and other hosts without `switches:`. Every template and task
  below is guarded `when: switches is defined`, like the rest of the per-port
  model.

## Findings that shape the design

These come from reading the OpenSSH 10.5p1 source, the RFCs and sshpiper's
source on 2026-10-03 and 2026-10-04, and from live DNS queries. No SSH
behaviour has been tested against a real board yet.
[Verification](#verification) lists the tests that must pass before each
phase merges.

| Finding | Evidence | Consequence |
|---|---|---|
| No SSH client uses SRV or SVCB to find a port. OpenSSH takes the port only from `-p`, `host:port`, `Port` or `getservbyname("ssh")`. | OpenSSH `ssh.c` and `readconf.c`; bz#2217 (2014) and openssh-portable PR #228 (2021) are unmerged; RFC 9460 requires a per-protocol SVCB mapping and none exists for SSH | The port cannot come from DNS. The only zero-config paths are port 22 on the board's own address (IPv6) and port 22 on a proxy that routes on the username. |
| SSHFP has no port dimension. OpenSSH looks SSHFP up at the connection host name only. | RFC 4255; `dns.c` `verify_host_key_dns()` | A name's SSHFP records can describe only one port's keys. A name gets SSHFP records only if port 22 is the only port we publish for it. Each board needs its own DNS name for the direct path. |
| The server sends its host key during key exchange, before the client sends a username. | RFC 4253 §8, RFC 4252 §5 | A proxy cannot choose a host key per board. It presents one key to every client. |
| A client's public-key signature covers the session identifier, which differs on each side of a proxy. | RFC 4252 §7 | A proxy cannot relay public-key logins. It checks the client's key itself and logs in to the next hop with its own key (sshpiper's "mapping key"). Passwords are relayed as they are. sshpiper's YAML plugin offers only password and public-key logins. |
| `known_hosts` is keyed by host name, and by `[name]:port` when the port is not 22. `UpdateHostKeys` defaults to `yes` (OpenSSH 8.5 and later) unless `VerifyHostKeyDNS` is on or a custom `UserKnownHostsFile` is set. After login the client treats known_hosts keys for that name that the server does not list as deprecated, and removes them, unless one of the keys also appears under another known_hosts name. | `readconf.c:2950-2957`; `clientloop.c:2189-2196`, `check_old_keys_othernames()` and the removal path after it | If one name were answered by different keys on different paths, a default client would be warned, or would lose one path's key on each login with the other path and be prompted again. Splitting the two keys by algorithm does not avoid this. A name and port must always be answered by one key (D8). |
| An authoritative DNS server sees the resolver, not the client. The client-subnet option (RFC 7871) is optional and describes the path to the resolver. | Live `o-o.myaddr.l.google.com TXT` queries via 8.8.8.8, 2001:4860:4860::8888, 1.1.1.1 and a site resolver, 2026-10-03 | DNS cannot hand different SSHFP records to IPv4 and IPv6 clients. A name's A and AAAA records must lead to the same key. |
| sshd has no PROXY-protocol support. sshpiper accepts PROXY headers from its clients (`--allowed-proxy-addresses`) but never sends them onward. | OpenSSH source; sshpiper `cmd/sshpiperd/main.go` | The only way for a board to see the client's address on the proxied path is IP-level transparent proxying (phase 2). A second proxy in front of the gateway's hides the client's address from it unless that proxy sends PROXY headers. |
| sshpiperd listens on one address and one port (`--address`, `--port`). Its `failtoban` plugin takes `--ignore-ip` and never bans a listed address. | sshpiper `cmd/sshpiperd/main.go` and `plugin/failtoban/main.go`, read 2026-10-04 | One instance cannot listen on port 22 of one address and port 2224 of another by itself; see "Where it listens". The upstream proxy's address can be exempted. |
| Since OpenSSH 9.8, sshd penalises a source address after repeated failed logins (`PerSourcePenalties`, on by default, nothing exempt). The boards run OpenSSH 10.0 (trixie). | `sshd_config(5)` | In phase 1 every proxied login reaches the board from the gateway's address, as do the web terminal, the upload page and the jump route. A few wrong passwords through the proxy would make that board refuse the gateway for up to 10 minutes. |
| `VerifyHostKeyDNS` defaults to `no`. With `yes` and a DNSSEC-validated SSHFP answer that matches, the client accepts the key silently, with no prompt. Without validation (or with `ask`) it prints "Matching host key fingerprint found in DNS" and still prompts. Validation reaches ssh only if the resolver validates and, with glibc 2.31 and later, resolv.conf has `options trust-ad`. If SSHFP records exist and none matches, the client prints the full "REMOTE HOST IDENTIFICATION HAS CHANGED" banner with "Update the SSHFP RR in DNS with the new host key to get rid of this message", then falls back to known_hosts. | `readconf.c:2977`, `sshconnect.c` `verify_host_key()`, `ssh_config(5)`, glibc 2.31 NEWS | SSHFP helps only users who opt in, and only from a signed zone. Everyone else gets one ordinary first-connection prompt per name. A published SSHFP record that does not match is worse than none, so no name here may produce a mismatch. |

## Current state this builds on

From `main` at 96a7336d and [`docs/access.md`](../../access.md). The firewall
template and access.md were re-read at 8151b39 on 2026-10-04, and the public
DNS was queried the same day.

- **Board names.** `pi-sw<S>-p<P>`, IPv4 `10.21.<S>.<P>`, IPv6
  `<pib_network6_base><S:02d>::<P>` (`port_vlans.py`). dnsmasq publishes them
  in `pib_domain` through `host-record=` lines
  (`roles/pxe/templates/ports.conf.j2`). The upstream resolver delegates that
  zone to the gateway's dnsmasq. It is an internal zone with private A
  records, and this design does not use or change it.
- **The public zone** (queried 2026-10-04, Welland). `fpgas.online` is the
  owner's zone, hosted outside the site, and it is DNSSEC-signed: a DS record
  (algorithm 13) is in the parent and a validating resolver returns its
  answers as authenticated. It already holds names this design reuses:
  - `pi-sw2-p47.welland.fpgas.online` has an A record with the board's
    **private** address, `10.21.2.47`, no AAAA and no SSHFP;
  - `gw.welland.fpgas.online` is a CNAME to the web name
    `welland.fpgas.online`, so it has the web name's A record (the upstream
    gateway) and AAAA record (the gateway);
  - `ssh.welland.fpgas.online` and `proxy.welland.fpgas.online` do not exist.
- **Board accounts.** Every board shares one NFS root and so one `pi` account
  (uid 1000, NOPASSWD sudo), one password (`pi_pw`, public by design), one set
  of `authorized_keys`, and one host key. The root holds RSA, ECDSA and Ed25519
  host keys (`roles/fixpi/tasks/netboot.yml`, "Pre-generate SSH host keys"),
  and sshd offers all three.
- **Image pulls replace the root's account files.** `roles/img/tasks/pull.yml`
  runs `rsync --delete` and excludes only the host keys, the `authorized_keys`
  files and the watchdog directory. Every new image therefore replaces
  `/etc/passwd`, `/etc/shadow` and `/etc/group`; fixpi re-applies `pi`'s
  password hash after each pull.
- **Root changes reboot the fleet.** A changed file in the root gets a new
  inode, which the booted boards answer with ESTALE, and it bumps the NFS root
  generation (access.md, "why a key change reboots the fleet").
- **IPv4.** At a site behind an upstream gateway, the one public IPv4 address
  belongs to the upstream gateway. It runs its own sshd on port 22 and DNATs
  chosen ports to the gateway (`eth_uplink_static_address`). Neither it nor
  the gateway SNATs those connections, so on the legacy path the board sees
  the client's real address.
- **IPv6.** The upstream gateway routes the boards' prefix to the gateway,
  but the gateway's forward chain drops every new connection from the uplink
  to a `v*` interface (it accepts only established, DNATed and outbound
  traffic).
- **Isolation, and its hole.** The forward chain drops `v*` to `v*` traffic,
  which is meant to make each board's link to the gateway the point-to-point
  link D3 describes. It does not fully do so today. The per-board DNAT rules
  match only the gateway's uplink address and the port, with no interface
  match, and the forward chain accepts `ct status dnat` before the
  board-to-board drop. A board can therefore reach another board's port 22
  (and 4444) through the gateway's uplink address. That is #204, fixed
  separately by adding `iifname "{{ eth_uplink }}"` to those rules, and it is
  a prerequisite of this design. From the uplink side, nothing reaches a
  board's IPv4 address except through those DNAT ports.
- **The input chain** accepts a service on every interface, `v*` included,
  unless the rule names an interface, and its `internal_networks` chain
  accepts everything that comes from the board network.
- **The gateway's own sshd** is public-key only (`sshd_pubkey_only`), on port
  22 on every address, and carries the `pi` jump account.

## Design

### Host keys in plain words

When an ssh client connects, the server proves who it is with its **host
key**: a key pair whose private half is meant to exist only on that server.
The client remembers the public half under the name it connected to, in
`~/.ssh/known_hosts`.

- **The first connection to a name** shows the key's fingerprint and asks
  "Are you sure you want to continue connecting?". Nothing vouches for the key
  at that moment unless the user compares the fingerprint with one published
  somewhere they trust.
- **Later connections** are silent while the key is the same. If the name
  answers with a different key, the client refuses to connect ("REMOTE HOST
  IDENTIFICATION HAS CHANGED").
- **The host key does not itself encrypt the session.** Each connection
  negotiates fresh encryption keys, and the host key signs that negotiation.
  What the host key gives is the assurance that the other end of the
  encryption is the server and not someone in between. Whoever holds the
  private half can pretend to be the server to any client whose traffic they
  can intercept, and then read and change everything in the session,
  passwords included.

That is why the fleet key is the wrong key for the proxy. Every board boots
the same root, so every board has the same host key, and every visitor has
root on a board. Its private half is in effect public. A key that anyone
holds proves nothing about who is answering. The gateway's proxy therefore
gets a key that exists only on the gateway.

There are three kinds of key in this design:

| Key | Where its private half is | Presented by |
|---|---|---|
| The fleet key | In the boards' shared root: readable by every visitor | Every board |
| The gateway's proxy key | On the gateway, readable only by the proxy's service user, and in the Ansible vault so that a rebuilt gateway keeps the same key | The gateway's ssh proxy |
| The gateway's sshd keys | On the gateway (`/etc/ssh`), as today | The gateway's own sshd |

At a site behind an upstream gateway there is a fourth: the upstream
gateway's proxy has a host key of its own, which is the upstream operator's
and not an fpgas.online credential.

The proxy does not reuse the gateway's sshd key. The proxy runs as an
unprivileged service; if it were compromised, the gateway's own identity
would go with it.

### One name, one key

**Every name we publish for ssh is answered by one key (or one sshd's key
set) on port 22. Other ports are kept apart by `known_hosts` and carry no
SSHFP** (decision D8). Different keys get different names. `<site-domain>`
is the site's public name, for Welland `welland.fpgas.online`.

For a site behind an upstream gateway:

| Name | Records | Port | Answered by | Key | SSHFP |
|---|---|---|---|---|---|
| `ssh.<site-domain>` | A only: the public IPv4 address | 22 | The upstream gateway's proxy, which forwards board names to the gateway's proxy. Password logins only (D11) | The upstream proxy's | none |
| | | 2223 | The gateway's own sshd (backup route for operators and deploys) | The gateway's sshd keys | |
| | | 2224 | The gateway's ssh proxy (the route that skips the upstream proxy) | The gateway's proxy key | |
| `proxy.<site-domain>` | AAAA only: an address of its own on the gateway | 22 | The gateway's ssh proxy | The gateway's proxy key | the proxy key |
| `gw.<site-domain>` | AAAA only: the gateway | 22 | The gateway's own sshd | The gateway's sshd keys | one per host-key algorithm the sshd offers |
| `pi-sw1-p7.<site-domain>` | AAAA only: the board's own global address (D9) | 22 | The board itself | The fleet key | the fleet key |

Notes:

- **`ssh.<site-domain>` is the visitors' login name.**
  `ssh pi-sw1-p7@ssh.<site-domain>` works from any client with IPv4. The user
  name selects the board; the host name is the same for every board at the
  site. It has no AAAA record, so it is only ever answered by the public IPv4
  address. It has no SSHFP record: the key on its port 22 is the upstream
  operator's, and its ports 2223 and 2224 answer with other keys.
- **`proxy.<site-domain>` is the same thing for IPv6**, without the upstream
  proxy: `ssh pi-sw1-p7@proxy.<site-domain>`. The gateway's sshd owns port 22
  on the gateway's address, and SSHFP cannot tell ports apart, so the proxy
  gets an IPv6 address of its own on the gateway (`ssh_proxy_address6`, taken
  from the prefix already routed to the gateway) where port 22 is the proxy.
  The name can then carry the proxy key's SSHFP record, and an IPv6 visitor
  checks an fpgas.online key and needs no port number.
- **`gw.<site-domain>` is for operators and deploys**: the gateway's own sshd
  over IPv6 (fpgas-online/fpgas.online-infra PR #199 moves Ansible's
  inventory to this name over IPv6). It becomes a record of its own, not a
  CNAME to the web name: a CNAME cannot carry SSHFP, and through the CNAME
  the name is answered by the upstream gateway over IPv4 and the gateway over
  IPv6. The generator reads the gateway's `/etc/ssh/ssh_host_*_key.pub` and
  publishes an SSHFP record for each.
- **Board names have no A record.** A board has no public IPv4 address, so an
  A record could only point at a proxy or, as in the public zone today, at
  the board's private address. The private A records must go: a visitor's
  client that resolves `10.21.2.47` connects to whatever has that address on
  the visitor's own network and offers it the published password. A client
  without IPv6 then gets "Could not resolve hostname" for a board name; it
  uses `ssh.<site-domain>`.
- **The backup ports do not collide with port 22.** `known_hosts` stores a
  non-default port as `[ssh.<site-domain>]:2224`, a separate entry from the
  bare name, so the different keys on ports 22, 2223 and 2224 never meet.
  Public port 2222 (the upstream gateway's own sshd) is the upstream's
  business and is not documented for visitors.
- **The site's web name is not published for ssh.** `<site-domain>` has an A
  record at the upstream gateway and an AAAA record at the gateway itself, so
  `ssh <site-domain>` is answered by two different hosts, with two keys,
  depending on the address family. It carries no SSHFP record, and the board
  pages and the user documentation never print it for ssh. The legacy
  `ssh -p <port> pi@<site-domain>` form keeps working over IPv4 until the
  DNAT is retired.
- **The same key under several names is fine.** The gateway's proxy key
  answers `[ssh.<site-domain>]:2224` and `proxy.<site-domain>`; the fleet
  key answers every board name. A client is prompted once per name and shown
  the same fingerprint.
- **`CheckHostIP`** (off by default since OpenSSH 8.5) also records keys by
  address. No address in this layout answers one port with two keys, so it
  raises no conflict.
- **The login name is not in DNS.** `pi-sw1-p7@` is only what the user types.
  The proxy routes on it.
- **No published name can produce an SSHFP mismatch.** Every name with SSHFP
  records has exactly one documented port, 22, and the records are the keys
  that answer there. During a fleet key rotation the board names carry both
  the old and the new fingerprint (D12).
- **Signing.** A client trusts SSHFP only from a DNSSEC-signed zone.
  `fpgas.online` is signed. From an unsigned zone the client still shows the
  ordinary prompt.
- **Generated, not hand-written.** Names and addresses come from
  `switches | port_vlan_map`. Fingerprints are not in the inventory: the
  generator reads the fleet key from the NFS root's
  `ssh_host_ed25519_key.pub`, the gateway's sshd keys from `/etc/ssh` and the
  proxy key from its installed public half on every converge. The record set
  changes only when `switches`, an address or a key changes. How it gets into
  the public zone is decision D4.
- **No happy eyeballs is needed.** Each name has one address family, so a
  client never waits out one family's timeout before trying the other.

A site whose gateway is on a public address has no upstream proxy, and how
its port 22 is shared between the proxy and the gateway's sshd is not
designed ([Open points](#open-points)).

### The paths, and what each key proves

| Path | Hops that end an ssh connection | Key the visitor's client checks |
|---|---|---|
| `ssh.<site-domain>`, port 22 (IPv4) | upstream proxy, gateway's proxy, board | the upstream proxy's |
| `ssh.<site-domain>`, port 2224 (IPv4) | gateway's proxy, board | the gateway's proxy key |
| `proxy.<site-domain>` (IPv6) | gateway's proxy, board | the gateway's proxy key |
| `pi-sw1-p7.<site-domain>` (IPv6, D9) | board | the fleet key |
| legacy per-port DNAT (IPv4) | board | the fleet key |

A proxy is the end of the visitor's ssh connection and the start of a new one
to the next hop. **Each proxy on the path sees the whole session in clear**,
the password included. On public port 22 a visitor's connection is terminated
twice, by the upstream proxy and then by the gateway's proxy, before it
reaches the board.

What each key proves:

- **The fleet key** proves only "this is some fpgas.online netboot board, or
  someone who has logged in to one". It does not prove which board, and it
  does not protect a connection against anyone on the network path. That
  applies to the direct IPv6 path and to the legacy DNAT path, which both
  cross the internet under this key alone. A visitor who cares uses a proxied
  path.
- **The gateway's proxy key** proves "this is the site's gateway". It is what
  the visitor checks on `proxy.<site-domain>` and on port 2224, and what the
  upstream proxy checks before it hands a login on.
- **The upstream proxy's key** proves "this is the site's upstream gateway".
  It is not an fpgas.online credential: fpgas.online does not hold it, cannot
  rotate it and does not publish it. On public port 22 it is the only key the
  visitor's client checks, so on that path the visitor trusts the upstream
  operator with the session: the upstream proxy sees the password and
  everything typed. `proxy.<site-domain>` and port 2224 are the routes that
  do not.
- **The upstream gateway holds no fpgas.online credential** (D11). It relays
  the password the visitor types, which is public anyway. It has no key that
  the gateway's proxy or a board accepts.
- **Between the gateway's proxy and a board** the proxy pins the fleet key
  and refuses a board that presents anything else. Because the key is public
  this is not authentication. It catches a mistake: a wrong address, or a
  device on the port that is not a fleet board. What binds a connection to a
  switch port is the address: `10.21.<S>.<P>` exists only on that port's
  VLAN, the gateway is the only router on it, and boards cannot reach each
  other, so only the device plugged into that port can answer. That hop never
  leaves the gateway's point-to-point link to the board.

The proxy also holds the mapping key, which the boards accept for `pi`. Since
`pi` has NOPASSWD sudo, **whoever controls the proxy has root on every
board**. The proxy therefore runs as its own unprivileged user, with systemd
hardening, and the mapping key and the proxy's host key are readable only by
that user. The mapping key has nothing to do with any host key: it is a
client key, generated for the proxy, whose public half goes into the boards'
`authorized_keys`.

Per-board host keys would need per-board state outside the shared root and
are out of scope.

### What a visitor sees

- **Through `ssh.<site-domain>`** (IPv4):
  - The first time ever at a site, one prompt with the upstream proxy's
    fingerprint. Then a password prompt; the visitor's own keys are not
    accepted on this route.
  - Never another host-key prompt for any board at that site: every board is
    behind the same name and key.
  - No changed-key warning when a board is swapped, rebooted or re-imaged, or
    when the fleet key is regenerated. The visitor's client never sees a
    board's key on this path.
  - A changed-key refusal only if the upstream operator replaces the upstream
    proxy's key.
- **Through `proxy.<site-domain>`** (IPv6): the same, with the gateway's
  proxy key, which is published in DNS and kept in the vault so that a
  rebuilt gateway does not change it. Operators' keys work here.
- **Straight to a board** (`ssh pi@pi-sw1-p7.<site-domain>`, IPv6, if D9
  keeps it):
  - One prompt per board name, each showing the same fleet key fingerprint.
    The board pages publish that fingerprint. From the second board on,
    OpenSSH adds that the key is already known under the other board names.
  - No warning when a board is swapped or rebooted: every board has the same
    key.
  - A changed-key refusal for every board name the visitor has used after the
    fleet key is regenerated (infra#126). The user documentation says how to
    clear it (`ssh-keygen -R`).
- **Backup ports**: one prompt for `[ssh.<site-domain>]:2224` or
  `[ssh.<site-domain>]:2223`, separate from the port 22 entry.

### Phase 1: the gateway proxy

**Software.** [sshpiper](https://github.com/tg123/sshpiper) with its YAML
plugin. It is not packaged in Debian (`apt-cache policy sshpiper` returns
nothing on trixie), so it is packaged for the fpgas.online apt repository
from a mirror repository, fpgas-online/sshpiper (D5).

**Where it listens.**

- The proxy's port is `ssh_proxy_listen_port`, 2224: the number the upstream
  gateway publishes for it (D1), so the port is the same inside and outside.
- sshpiperd takes one listening address and one port. The proxy has to answer
  in two places: port 2224 on the gateway's uplink IPv4 address (what the
  upstream gateway's proxy and its forward of public 2224 connect to), and
  port 22 on the proxy's own IPv6 address (`proxy.<site-domain>`). One
  instance does both:
  - it binds the wildcard address (`--address ::`) on port 2224. sshpiperd
    listens with `net.Listen("tcp", JoinHostPort(address, port))`
    (`cmd/sshpiperd/daemon.go`), and Go opens a dual-stack socket for a
    wildcard address on Linux unless `bindv6only` is set, so the one listener
    takes IPv4 and IPv6;
  - the firewall rewrites only the port:
    `iifname {{ eth_uplink }} ip6 daddr {{ ssh_proxy_address6 }} tcp dport 22
    dnat to :2224`, in an `ip6 nat` prerouting chain. The destination address
    is unchanged, so the input rule
    `iifname {{ eth_uplink }} ip6 daddr {{ ssh_proxy_address6 }} tcp dport 2224
    accept` matches both the rewritten connections and direct ones to port
    2224 of that address, and nothing else. A `redirect` would not do: it
    also rewrites the destination address to the incoming interface's own.
    This is argued from netfilter semantics and is to be proved in the VM
    test under #187;
  - over IPv4, port 2224 is accepted on the uplink only. Over IPv6 it is
    accepted only to `ssh_proxy_address6`, so the proxy answers neither on
    the address that `gw.<site-domain>` names nor on the uplink interface's
    own address.
- `ssh_proxy_address6` is configured on the gateway as a /128 (on `lo`), so
  traffic from a board to it is delivered locally and never forwarded. What
  a board gets on it: port 22 is the gateway's sshd (the rewrite applies on
  the uplink only; the sshd is key-only; this is the same reach a board has
  to the gateway today), and port 2224 is dropped by the non-uplink drop.
- Why not bind port 22 on the proxy's address directly: the gateway's sshd
  holds port 22 on the wildcard address, and Linux refuses a second listener
  on the same port at a specific address while it does. The sshd would have
  to list its addresses one by one, and a mistake there locks operators and
  Ansible out. Why not two instances: each would keep its own `failtoban`
  state and double what has to be monitored. The Linux bind behaviour is from
  memory and is checked in #187; the port rewrite does not depend on it.
- If the rewrite rule is missing, port 22 on the proxy's address is answered
  by the gateway's sshd with the wrong key. The verify play therefore checks
  that `ssh-keyscan` of the proxy's address returns the proxy key.
- The gateway's sshd stays on port 22, unchanged. It is public-key only, and
  a terminating proxy cannot relay public-key logins, so putting the proxy in
  front of it would mean loading every operator's, the jump account's and
  Ansible's keys into the proxy and would stop their logins being end to end.
- Unknown usernames are refused, not forwarded anywhere (D6). sshpiper offers
  every client the union of all pipes' methods, so an unknown name (or `pi@`)
  is offered a password prompt and refused after it ("no matching pipe").
  That also means the proxy does not reveal which names exist.
- The yaml plugin refuses configuration files with group or other permission
  bits, so the generated files are mode 0600.
- sshpiper's `failtoban` plugin is chained after the yaml plugin
  (`sshpiperd yaml ... -- failtoban`). The password is public, so limiting
  guesses does not protect a secret. It limits noise, and it keeps the
  boards' sshd responsive: a flood of logins through the proxy would
  otherwise occupy a board's connection slots. Behind an upstream gateway
  every login through public port 22 arrives from the upstream proxy's
  address, so that address is given to `--ignore-ip` and is never banned;
  limiting on port 22 is then the upstream's job. On port 2224 and over IPv6
  `failtoban` sees the visitor's own address.
- **Agent keys and the ban.** A client offers every key in its agent before
  it offers the password. On the gateway's proxy a visitor whose keys the
  proxy does not know is in the same position as on the upstream proxy
  ([Requirements on the upstream network](#requirements-on-the-upstream-network)):
  the keys are rejected first. `failtoban` counts pipe-creation failures as
  well as board authentication failures, and its defaults are 5 failures and
  a 60-minute ban (`plugin/failtoban/main.go`). Whether each rejected key
  counts as a failure is not verified. The role therefore sets both values
  explicitly, so that a visitor who mistypes, tries `pi@` or has a full
  agent is not locked out for an hour: `--max-failures 20` and
  `--ban-duration 5m`, as a starting point to adjust from the proxy's logs.
  The user documentation carries the line: "If you see 'Too many
  authentication failures', your client offered its keys first. Use
  `ssh -o PubkeyAuthentication=no pi-sw2-p47@ssh.<site-domain>`."

**The proxy's host key.**

- An Ed25519 key generated once for the gateway and stored in the vault
  (`vault_ssh_proxy_host_key`). The role installs it as
  `/etc/sshpiper/ssh_host_ed25519_key`, owned by the proxy's service user,
  mode 0600, and passes it with `--server-key`. A rebuilt gateway gets the
  same key from the vault, so visitors and the upstream proxy see no change.
- The role fails if the variable is not set. It does not generate a key
  silently, and it never falls back to sshpiperd's default,
  `/etc/ssh/ssh_host_ed25519_key`, which is the gateway's own sshd key.
- The role also fails if the proxy's public key equals the fleet's or one of
  the gateway's sshd keys.
- The private half is never written into the boards' root or anywhere a
  board can read.

**The proxy drops the board's host-key announcement.** After login sshd
announces its host keys (`hostkeys-00@openssh.com`), and sshpiper, which is a
packet pipe after authentication, forwards that to the client by default. On
this design the announcement would list the fleet key to a client that
connected to the proxy's key. The proxy therefore runs with
`--drop-hostkeys-message`. The flag is required, and verification checks that
a proxied login leaves `known_hosts` untouched.

**Routing rules** are generated from `switches | port_vlan_map`: one pair per
access port, empty ports included, no regex. Two pipes may share a username;
sshpiper offers the union of their methods and picks the pipe that matches
the method the client uses.

```yaml
version: "1.0"
pipes:
  - from:
      - username: "pi-sw1-p7"           # password logins, relayed
    to:
      host: "10.21.1.7:22"
      username: "pi"
      known_hosts_data:
        - "<base64 of: 10.21.1.7 ssh-ed25519 AAAA...>"
  - from:
      - username: "pi-sw1-p7"           # public-key logins
        authorized_keys: "/etc/sshpiper/authorized_keys"
    to:
      host: "10.21.1.7:22"
      username: "pi"
      private_key: "/etc/sshpiper/mapping_key"
      known_hosts_data:
        - "<base64 of: 10.21.1.7 ssh-ed25519 AAAA...>"
```

`known_hosts_data` takes base64-encoded known_hosts lines whose host field
matches the dialled address. The line is the fleet's public key, read from
the NFS root on every converge. Board host keys are always pinned: the
sshpiper README warns that leaving them out disables the check. What the pin
is worth is described under
[The paths, and what each key proves](#the-paths-and-what-each-key-proves).
If it goes stale, sshpiper reports every proxied login as
`Permission denied (publickey)`, which is why the verify play logs in end to
end.

**Fleet key rotation** (D12). sshd loads its host keys when it starts, so
after the NFS root's key changes (#126) booted boards keep the old key until
their staggered reboot, up to about 33 minutes. During that window:

- the proxy pins both the old and the new board key in `known_hosts_data`;
- the board names carry SSHFP records for both keys, so a board on either key
  matches DNS. The old fingerprint is removed by the first converge after the
  wave has ended.

Proxied visitors notice nothing; direct IPv6 visitors see the key change
once. The proxy's own key is not involved.

**Authentication to the board.**

- **Passwords** are relayed. With the published `pi` password this is the
  zero-setup path, and it works through both proxies.
- **Public keys** are checked by the gateway's proxy against the keys the
  boards already trust for `pi` (the operators' GitHub keys, the server-user
  key, the controller key and the jump account key). The proxy then logs in
  with its mapping key. fixpi adds the mapping key's public half as a new key
  source, `ssh_proxy`, for `pi` only (like `jump`), never for root. Only
  those keys pass the proxy: an ordinary user's own key cannot work through
  it (the proxy has no way to know it), though it would work on the direct
  path if the user adds it to the board. Ordinary users log in with the
  password.
- **Public port 22 behind an upstream gateway is password-only** (D11). The
  upstream proxy cannot relay a public-key login, and it is given no key that
  the gateway's proxy accepts. Anyone who wants key authentication uses the
  gateway's own proxy: `proxy.<site-domain>` over IPv6, or port 2224 over
  IPv4.
- **What does not work through the proxy:**
  - `pi@` and `root@`, because the proxy only knows `pi-sw<S>-p<P>` names and
    always logs in as `pi`. The user docs say to use the `pi-sw<S>-p<P>@`
    form.
  - `ansible@`. Ansible keeps using the jump route.

**Firewall.** #204 is a prerequisite: until the per-board DNAT rules name the
uplink interface, a board reaches other boards through them, whatever this
design adds.

- Input: accept the proxy port on `iifname {{ eth_uplink }}` (over IPv6 only
  to `ssh_proxy_address6`), and drop it on every other interface with a rule
  placed before `jump internal_networks`. That chain accepts everything from
  the board network, so without the drop a board could reach the proxy on the
  gateway's uplink address. Boards must not reach the proxy: connections that
  come from the proxy carry the gateway's identity (`10.21.0.1`, which the
  boards exempt from login penalties, below) and, in phase 2, the transparent
  path, and the proxy would be a board-to-board path.
- NAT, IPv6: the port rewrite of port 22 on `ssh_proxy_address6`, on the
  uplink interface only.
- Forward, IPv6: `iifname {{ eth_uplink }} oifname "v*" ip6 daddr <board
  addresses> tcp dport 22 accept`. This exposes each board's sshd, with the
  published password, to the IPv6 internet. The per-port DNAT already gives
  the same exposure over IPv4. This rule is the direct path, and it is the
  subject of D9.
- Forward, boards going out and back in (D10): drop connections from the
  board VLANs to the site's own public ssh entry points, before the rule
  that accepts `v*` to the uplink:
  `iifname "v*" ip daddr { <public ssh addresses> } tcp dport { 22, 2222,
  2223, 2224 } drop`, and the same for any public IPv6 ssh address that is
  not on the gateway itself. The addresses are site data in the inventory.
  Without it a board could reach another board, or the gateway's sshd,
  through the public side. This closes the path at the gateway for the
  site's own addresses. A board can still reach other sites' proxies, and any
  other host on the internet, like any internet host.
- **Nothing else is opened** (D3). There is no forward rule from the site's
  networks to the boards' IPv4 addresses, and `v*` to `v*` stays dropped. A
  device on the site's LAN reaches a board exactly as an internet client
  does: through a proxy, or over IPv6 through the rule above, arriving on the
  gateway's uplink.

**Board side** (fixpi):

- **Boards offer Ed25519 only.** A fixpi sshd drop-in sets
  `HostKey /etc/ssh/ssh_host_ed25519_key`. The RSA and ECDSA key files stay
  on disk but are not offered. There is then one fleet fingerprint to
  publish, one SSHFP record per board name and one pin line per board in the
  proxy, whatever the client's algorithm preference. Clients that exclude
  Ed25519 (for example in FIPS mode) cannot connect.
- **Exempt the gateway from login penalties.** The same drop-in sets
  `PerSourcePenaltyExemptList` to the gateway's board-side address
  (`{{ pib_network }}.0.1`). Otherwise a few failed logins through the proxy
  would lock the web terminal, the upload page, the jump route and every
  other proxied user out of that board for up to 10 minutes. Guess-limiting
  for the proxied path moves to the proxies. After phase 2 proxied logins
  carry the client's address and are penalised per client again; the
  exemption stays for the gateway's own connections.
- **Login aliases.** sshd has no username-alias option and looks the account up
  itself, so fixpi adds one passwd entry per access port. Each shares `pi`'s
  uid, gid, home and shell, and gets a shadow entry with the same `pi_pw` hash,
  written on the gateway only, like `pi`'s:

  ```
  pi-sw1-p7:x:1000:1000::/home/pi:/bin/bash
  ```

  Each alias is also added to every supplementary group `pi` is in. sshd calls
  `initgroups()` with the login name, and without this an alias would lose
  `dialout`, `gpio`, `i2c`, `spi`, `video` and the other groups that hardware
  access depends on.

  - The proxy rewrites the name to `pi`, so the aliases matter only on the
    direct path: they let `ssh pi-sw1-p7@pi-sw1-p7.<site-domain>` work, so
    that the same login name works on every path. They are a convenience;
    `pi@` works on the direct path without them. If D9 drops the direct path
    they are not built.
  - The root is shared, so every board carries every alias:
    `pi-sw2-p33@` also works on `pi-sw1-p7`.
  - `whoami` shows `pi`, the first entry for uid 1000, while the logs show the
    alias. sudo resolves the user from the uid, so `pi`'s sudoers rule applies.
    Verification checks this rather than assuming it.
  - The aliases also go into `/etc/gshadow` group member lists, so `grpck`
    stays clean.
  - An image pull replaces `/etc/passwd`, `/etc/shadow`, `/etc/group` and
    `/etc/gshadow`, so fixpi re-applies the aliases after every pull, as it
    already does for `pi`'s hash.
  - Every write is idempotent: a file is rewritten only when its content
    differs. A rewritten `/etc/shadow` makes booted boards refuse SSH until
    they reboot (access.md), so a converge that rewrote it every time would
    reboot the fleet every time.

**What reboots the fleet.** Each of these changes a file in the shared root:

- the first phase 1 converge, which writes `/etc/passwd`, `/etc/shadow`,
  `/etc/group`, the sshd drop-in and `pi`'s `authorized_keys`;
- every new image, which already reboots the fleet; the aliases are re-applied
  in the same converge;
- any change to `switches` or `access_ports`, because the alias list follows
  `port_vlan_map`;
- regenerating the mapping key, for example after a gateway reinstall.

Changing the proxy's host key does not: it is not in the root.

**DNS.** Phase 1 publishes the records in
[One name, one key](#one-name-one-key) and removes the private A records and
the `gw.<site-domain>` CNAME that the public zone holds today. How the
records get into the zone is decision D4. The gateway's internal dnsmasq zone
(`pib_domain`) is not changed.

**What the board pages show.** fpgas.online-gw serves `/api/boards`, and its
`ssh` object gains `user`, `host`, `host_ipv6` and `direct_host`:

```json
{"host": "ssh.<site-domain>", "host_ipv6": "proxy.<site-domain>",
 "user": "pi-sw1-p7", "port": 22,
 "direct_host": "pi-sw1-p7.<site-domain>", "legacy_port": 10722}
```

fpgas.online-site renders the board pages. They print
`ssh pi-sw1-p7@ssh.<site-domain>` first, then the IPv6 form with
`proxy.<site-domain>`, then the direct IPv6 command with the fleet key
fingerprint (if D9 keeps the direct path), with the legacy `-p` form
underneath until the DNAT is retired.

**Source address.** On the proxied path the board sees the gateway's address.
The legacy DNAT path shows the client's real address, so phase 1 is a
regression for proxied logins until phase 2.

**Monitoring.** If the proxy is down, logins through `ssh.<site-domain>` and
`proxy.<site-domain>` are gone. The proxy runs under systemd with
`Restart=on-failure`, and `verify-server.yml` checks that it is listening and
that a login through it reaches a board. The public port 22 path also depends
on the upstream proxy, which the gateway cannot restart; a check from outside
the site is the only thing that sees it.

### Requirements on the upstream network

These apply to a site behind an upstream gateway. They are requirements, not
a design for the upstream. They belong in the docs repository
(fpgas-online/fpgas.online-docs) page "What a site needs from its upstream
network", which is where an upstream operator reads them.

| Requirement | Why |
|---|---|
| Public port 22: a username-routing ssh proxy that forwards logins under board names (`pi…`) to the gateway's proxy port (2224 on the gateway's uplink address), keeping the user name and relaying the password | D1 |
| Public port 2223 forwarded to the gateway's sshd (port 22); public port 2224 forwarded to the gateway's proxy port. Plain forwards that keep the client's source address | D1; the backup routes, and the IPv4 route on which the visitor checks an fpgas.online key |
| The upstream proxy pins the gateway's proxy key (a public key) and refuses anything else | It is the only check on that hop |
| The upstream holds no fpgas.online credential. Public-key logins under board names are refused at the upstream proxy, so the client falls back to the password | D11 |
| The upstream proxy answers a public-key attempt for a board name with "try password", and does not count such attempts toward its limit of failed logins, or allows at least 20 | A client offers every key in its agent before the password; a visitor with several keys would otherwise be disconnected ("Too many authentication failures") before the prompt |
| The upstream proxy does not pass host-key announcements from the next hop to the client | The client would be offered a key that is not the one it connected to |
| The upstream proxy's host key does not change without notice | Every visitor's client refuses to connect after a change |
| The upstream admits tcp 22 over IPv6 to the gateway's address and to the proxy's address, and to the board prefix if D9 keeps the direct path | `gw.<site-domain>`, `proxy.<site-domain>`, board names |
| Clients inside the site reach the same services under the same names as clients outside: `ssh.<site-domain>` leads to the same upstream proxy and key from both sides | D3; a laptop that moves between inside and outside must not see a key change |
| The upstream proxy should limit repeated failed logins per client | The gateway's proxy sees all of these logins from one address, cannot tell the clients apart and ignores that address. Not required: the password is public, so this limits noise, not access |
| Wanted, not required: the upstream proxy tells the gateway's proxy the client's address (PROXY protocol header) | Phase 2 and per-client limiting on the port 22 path |

**Cut-over order for the operators' route.** Nothing of fpgas.online's moves.
Today no fpgas.online route uses public IPv4 port 22: it is the upstream
gateway's own sshd, operators and deploys reach the gateway's sshd over IPv6
(`gw.<site-domain>`), and the only outside IPv4 route is a jump through an
account on the upstream gateway. The order is therefore: the gateway's proxy
and firewall first (this repository, testable alone); then, in the upstream
network, 2222 for its own sshd, then 2223 and 2224, then the proxy on 22;
then `ssh.<site-domain>` is published and the board pages change. Public
port 2223 gives operators a direct IPv4 route for the first time, and
`docs/access.md` is updated when it exists.

### Phase 2: transparent IPv4 source

The goal is that on a proxied connection the board sees the client's real
address, as it already does on the direct IPv6 path.

The mechanism is Linux transparent proxying, the same one as nginx
`proxy_bind $remote_addr transparent`
([tproxy.rst](https://docs.kernel.org/networking/tproxy.html)).

1. **The proxy dials the board from the client's address.**
   - Its board-side socket sets `IP_TRANSPARENT` and binds to the client's
     address, with port 0, before connecting. Port 0 lets the kernel pick the
     source port, which avoids `EADDRINUSE` on quick reconnects.
   - sshpiper has no such option. It dials the next hop with a bare
     `net.Dial(network, addr)` (`cmd/sshpiperd/internal/plugin/grpc.go:418` at
     the time of writing), and it never sends PROXY headers onward, which sshd
     could not use anyway. Our package therefore carries a small patch there:
     a `net.Dialer` with `LocalAddr` set to the downstream peer's address and
     a `Control` hook (which runs before `bind`) that sets `IP_TRANSPARENT`.
     The patch stays in our packaging repository.
   - The service gains `CAP_NET_RAW` through `AmbientCapabilities=` and nothing
     else. `CAP_NET_ADMIN` also satisfies the kernel check, but it would let a
     compromised proxy rewrite the gateway's firewall and routes.
2. **The board's replies come back to the proxy instead of going out to the
   internet.**
   - The gateway is every board's default route, so the replies pass through
     it, but by default they would be forwarded, because the forward chain
     accepts `v*` to uplink. They must be delivered locally.
   - Only packets that belong to a transparent socket are diverted. The legacy
     DNAT replies, which also have source port 22, have no local socket bound
     to their destination, so they are untouched.
   - The table goes into `nftables.conf.j2` itself, because the template
     starts with `flush ruleset`:

     ```
     table inet tproxy {
       chain prerouting {
         type filter hook prerouting priority mangle;
         iifname "v*" tcp sport 22 socket transparent 1 meta mark set 0x1 accept
       }
     }
     ```

   - A policy route, owned by systemd-networkd so it survives restarts: a
     `.network` for `lo` with `[RoutingPolicyRule] FirewallMark=1 Table=100
     Family=ipv4` and `[Route] Type=local Destination=0.0.0.0/0 Table=100`.
   - The diverted replies then pass the input chain through
     `internal_networks` (source `10.21.0.0/16`), and the masquerade rule cannot
     match them.
3. **Nothing else changes.** No masquerade applies, because these packets never
   leave through the uplink, and the board needs no change.

**What two hops do to it.** The gateway's proxy can only dial from the
address it sees.

- On public port 2224 the gateway's proxy sees the visitor's own address, and
  phase 2 works as described.
- On public port 22 the gateway's proxy sees the upstream proxy's address.
  For the board to see the visitor's address, that address has to survive the
  upstream proxy first: either the upstream proxy sends a PROXY protocol
  header, which sshpiper accepts from listed addresses
  (`--allowed-proxy-addresses`) and which the patch would then dial from, or
  the upstream proxy is itself transparent. Whether either is possible is the
  upstream's matter and is not designed here. Until it is settled, phase 2
  gives boards the visitor's address on port 2224 only, and the upstream
  proxy's address on port 22. This is an open point.
- Connections through `proxy.<site-domain>` arrive over IPv6 and are dialled
  to the board over IPv4, so the client's address cannot be carried at all
  without the RFC 6052 idea in [Non-goals](#non-goals). The board sees the
  gateway's address for those.

**Failure modes.**

- **Missing policy route.** Replies are forwarded to the internet and every
  proxied login times out. The verify play checks the rule, the route and an end
  to end login whose `$SSH_CONNECTION` on the board shows a non-gateway
  address.
- **4-tuple clash.** In theory a legacy DNAT connection and a transparent
  connection to the board could share a 4-tuple: the same client address and port
  to the same board on port 22. What conntrack does then has not been tested;
  at worst one login fails. It needs the client to reuse the same source port
  on both paths at once, and it disappears when the DNAT is retired.

The direct IPv6 path needs no phase 2.

## Decisions

| # | Decision | Status |
|---|---|---|
| D1 | What answers the site's public IPv4 port 22? | **Decided by Tim, 2026-10-04**: "The site's gateway should get it's own proxy which works in a somewhat similar manner to the proxy on the fpgas.online gateway. piXXX users will be forwarded the fpgas.online gateway. As a backup, port 2222 should go to ten64.welland's ssh server and port 2223 should forwarded directly to gw.welland.fpgas.online/tweed.welland.mithis.com ssh server and port 2224 should forward directly to gw.welland.fpgas.online/tweed.welland.mithis.com ssh proxy." A proxy on the upstream gateway; board names go on to the gateway's proxy; 2222, 2223 and 2224 are the backup routes. |
| D2 | Which host key does the gateway's proxy present? | **Decided by Tim, 2026-10-04**: "Unclear, the ssh host key is on the rpi and available to anyone who logs into that system, so should *not* be what encrypts traffic to gw.welland.fpgas.online". Not the fleet key. A key of its own, kept on the gateway and in the vault. |
| D3 | How does a client inside the site reach a board? | **Decided by Tim, 2026-10-04**: "Other devices behind ten64.welland.mithis.com should access all welland.fpgas.online resources via gw.welland.fpgas.online and look pretty much like any internet device except with a private IP addresses. Devices inside XXX.welland.fpgas.online should not be accessing other XXX.welland.fpgas.online devices, they should only be talking to gw.welland.fpgas.online -- IE really pi1.welland.fgpas.online<->gw.welland.fgpas.online should be considered a direct point to point link." Like an internet client, through the gateway. No LAN-to-board rule, no internal DNS view, no board-to-board path. |
| D4 | How do the generated records get into the public zone? | **Open: Tim decides**, see below. |
| D5 | How is sshpiper packaged? | **Decided by Tim, 2026-10-04**: "Mirror repo fpgas-online/sshpiper; I will create it". Waiting on him to create the repository. |
| D6 | Does the gateway's proxy forward unknown usernames to the gateway's own sshd? | **Decided by engineering, 2026-10-04**: no. The proxy refuses every name that is not a board name. The gateway's sshd stays reachable on its own: public port 2223 over IPv4 (D1), and `gw.<site-domain>` over IPv6. |
| D7 | Is the login name the board hostname or the board slug? | **Decided by engineering, 2026-10-04**: the hostname, `pi-sw<S>-p<P>`, only, for now. Hostnames are placements, not identities; slug aliases can be added later from the same data. |
| D8 | How do the proxy's key and the fleet key coexist without warnings in default clients? | **Decided by engineering, 2026-10-04, open to Tim's veto**: every name we publish for ssh is answered by one key (set) on port 22; other ports are kept apart by known_hosts and carry no SSHFP. `ssh.<site-domain>` (IPv4), `proxy.<site-domain>` (IPv6), `gw.<site-domain>` (the gateway's sshd), AAAA-only board names ([One name, one key](#one-name-one-key)). |
| D9 | May a visitor connect straight to a board's own IPv6 address? | **Open: Tim decides**, see below. |
| D10 | May a board reach the site's own public ssh entry points? | **Decided by engineering, 2026-10-04**: no. The gateway drops connections from the board VLANs to the site's public ssh addresses on ports 22, 2222, 2223 and 2224. |
| D11 | Do public-key logins work on public port 22 behind an upstream gateway? | **Decided by engineering, 2026-10-04**: no. That path is password-only, and the upstream gateway holds no fpgas.online credential. Key authentication uses the gateway's own proxy (`proxy.<site-domain>`, or port 2224). |
| D12 | Which fingerprint do board names carry while the fleet key rotates? | **Decided by engineering, 2026-10-04**: both, for the length of the reboot wave. |

### Questions for Tim

#### D9: may a visitor connect straight to a board's own IPv6 address?

In plain words: may a visitor connect straight to a board's own IPv6
address, passing through the gateway's firewall but not through its proxy?

Recommendation: **no**.

Why it is a question: it is the one path that does not end at the gateway.
The gateway forwards the packets, so the board's link is still only to the
gateway, as D3 asks. But the ssh session ends on the board, and the only key
protecting it across the internet is the fleet key, which D2 says should not
be what protects traffic.

Two terms: the **fleet key** is the one host key all boards share; anyone
who logs in to a board can copy it. **Login aliases** are extra account
names like `pi-sw2-p47` on the boards.

**No (recommended).** Every ssh session ends on the gateway's proxy.

- **Visitor:** `ssh pi-sw2-p47@ssh.<site-domain>` (IPv4) or
  `…@proxy.<site-domain>` (IPv6), then the published password. Today's
  `ssh -p 10722 pi@welland.fpgas.online` stops working when the per-board
  ports are retired: the user name changes from `pi` to the board name, the
  port number goes, and scripts and `scp -P` lines must be edited. A visitor
  can no longer add their own key to a board and log in with it; every login
  types the password.
- **Operator:** your keys keep working, as `pi`, on `proxy.<site-domain>`
  and port 2224. `root@` and Ansible keep the existing jump through the
  gateway's sshd. All visitor ssh now depends on the proxy service: if it is
  down, only the web terminal and the jump route reach boards. No per-board
  DNS records, no IPv6 rule to boards, no login aliases.
- **Also decides:** the IPv4 per-board ssh ports (10722 and so on) are
  retired, as a separately dated step after the proxy has run for an agreed
  period, so there is a fallback during rollout; say if you want that kept
  as a separate decision. The `…44` ports are not affected.
- **Costs:**
  - The proxy is the single route for visitor ssh once the per-board ports
    go.
  - Visitors type the public password every time.
  - Boards never see an IPv6 visitor's address, and a port-22 visitor's only
    if the upstream cooperates, so attribution lives in the proxy's logs,
    which must be kept.
  - All scp and sftp traffic (bitstreams) passes through one or two userland
    proxies, which is untested.
  - An IPv4 visitor who wants a host key that fpgas.online controls needs
    port 2224, so a port number returns for that case.

**Yes.** Keep the direct path, documented as the less protected one.

- **Visitor:** a third command on the board page,
  `ssh pi@pi-sw2-p47.<site-domain>`, IPv6 only. It accepts the visitor's own
  key once they add it to the board, shows the board the visitor's real
  address, and works when the proxy is down. Its host key proves only "some
  fpgas.online board, or someone who has logged in to one", and the page
  says so. One first-connection prompt per board.
- **Operator:** one AAAA and one SSHFP record per switch port in public DNS,
  regenerated when switches or the fleet key change (D4). An IPv6 forward
  rule to every board's port 22. Login aliases on the boards, so adding a
  switch reboots the fleet. The legacy per-board ports stay until retired
  separately.

#### D4: how do the generated records get into the public zone?

In plain words: the design needs ssh records under `<site-domain>` in the
public `fpgas.online` zone. That zone is yours, hosted outside the site, and
it is DNSSEC-signed (checked 2026-10-04), which is what makes SSHFP records
worth publishing. The records come from the inventory and from keys on the
gateway, so a converge can generate them as a zone fragment: a text file of DNS
records. The cost of every option is how that fragment gets into your zone.

Recommendation: **A**.

The records: `ssh.<site-domain>` (A), `gw.<site-domain>` and
`proxy.<site-domain>` (AAAA and SSHFP), and, only if D9 is "yes", one AAAA
and one SSHFP per switch port. Board names follow switch ports, not boards,
so plugging boards in and out changes nothing. Whatever the option, the
per-port names' private A records (`pi-sw2-p47` → `10.21.2.47`) and the
`gw.<site-domain>` CNAME that the zone holds today are removed.

| Option | What you or an operator do | What a visitor sees |
|---|---|---|
| **A (recommended).** The records stay in the `fpgas.online` zone. The converge writes them as a zone fragment, and the verify play fails when the public DNS differs from it | You load the fragment into the zone when the verify play says it is out of date: when a switch is added, an address changes or a key is replaced; also after a gateway reinstall, if its sshd keys change. With D9 "no" that is three names, entered once. Nothing new runs on the gateway and nothing new is asked of the upstream network | Names resolve whether or not the site is up. SSHFP is validated, because the zone is signed |
| **B.** `<site-domain>` becomes a zone of its own, delegated to the gateway, which runs a signing DNS server | You add a delegation and a DS record once. The gateway then runs and monitors a public DNS server, and the upstream network forwards port 53 to it. The site's web name moves into that zone | Records are always current. When the gateway is down, every name of the site, the web name included, stops resolving |
| **C.** A script with a credential for the DNS host pushes the fragment | Nothing by hand after setup. A DNS credential that can change `fpgas.online` is stored in the vault and used from the gateway or from CI. Only if the DNS host offers an API; not checked | The same as A |

#### For confirmation: the visitor's command

Not a choice between options; say if either point is not what you want.

1. The command on the board page becomes
   `ssh pi-sw2-p47@ssh.<site-domain>` (for Welland
   `ssh pi-sw2-p47@ssh.welland.fpgas.online`), replacing today's
   `ssh -p 10722 pi@welland.fpgas.online`: the board is chosen by the
   user name, and the host name is the same for every board at the site. The
   per-board host name (`pi-sw2-p47.<site-domain>`) is only for the direct
   IPv6 path of D9. IPv6 visitors use `proxy.<site-domain>` in place of
   `ssh.<site-domain>`.
2. On public IPv4 port 22, behind your upstream gateway, logins are
   password-only. The upstream proxy holds no fpgas.online key, sees the
   session in clear, and is what the visitor's client checks there. Key
   logins, and a host key that fpgas.online controls, are on
   `proxy.<site-domain>` (IPv6) and on port 2224 (IPv4).

## Open points

These are not the owner's decisions; they are things the design does not
settle yet.

- **A site whose gateway is on a public address.** There the gateway's proxy
  must answer port 22 on the address where the gateway's sshd listens today,
  and `ssh.<site-domain>` could carry the proxy key's SSHFP. The likely
  answer is D1's layout applied to the gateway itself (proxy on 22, sshd on
  2223), which moves Ansible's and the operators' route. No such site with
  `switches:` exists, so it is left until one does.
- **The client's address through the upstream proxy** (phase 2 and
  per-client limiting on port 22), above.
- **Inside the site.** D8 holds for a laptop that moves between the site's
  LAN and the internet only if the upstream network makes
  `ssh.<site-domain>` lead to the same proxy from both sides.
- **The site's web name.** Someone who types `ssh pi-sw1-p7@<site-domain>`
  reaches the upstream proxy over IPv4 and the gateway's sshd over IPv6,
  where the login fails with `Permission denied (publickey)`. Documentation
  is the only defence.
- **What else uses `gw.<site-domain>` over IPv4.** Making it AAAA-only
  removes the A record it has today through the CNAME. Anything that reaches
  it over IPv4 lands on the upstream gateway today and would stop resolving.
- **Boards and the gateway's own sshd.** D10 stops a board reaching the
  gateway's sshd through the public side. A board can still reach it on the
  gateway's own addresses, as today; it is key-only.

## Verification

Before phase 1 merges, on one board and in the CI VM. None of these steps
uses an upstream proxy: they go to the gateway's proxy, on port 2224 of the
uplink address and on port 22 of the proxy's IPv6 address.

1. **One name, one key.**
   - `ssh-keyscan` of a board returns exactly one key, the NFS root's
     `ssh_host_ed25519_key.pub`.
   - `ssh-keyscan` of the proxy, on both of its listening points, returns
     exactly one key. It matches the vaulted proxy key, and it differs from
     the fleet key and from the gateway's sshd keys.
   - `ssh-keyscan` of port 22 on the gateway's own address returns the
     gateway's sshd keys, and port 2224 does not answer there over IPv6.
     Port 2224 does not answer over IPv6 on the uplink interface's own
     address either.
   - With a default `ssh_config`, repeated logins through the proxy's name to
     different boards prompt only on the first connection, and known_hosts
     holds one unchanged line for the name after each step.
   - A proxied login leaves known_hosts byte for byte unchanged
     (`--drop-hostkeys-message`).
   - Logins to two board names over IPv6 prompt once each, with the same
     fingerprint, and never touch the proxy's line (if D9 keeps the direct
     path).
   - The generated record set has no A record for a board name, no SSHFP
     record on a name with more than one documented port, and an SSHFP
     record for every key that `ssh-keyscan` returns on `gw.<site-domain>`.
2. **SSHFP.** Against a signed test zone, with a validating resolver,
   `options trust-ad` and `VerifyHostKeyDNS yes`, a first login to the
   proxy's name, to the gateway's name and to a board name shows no host-key
   prompt. With a wrong record in the test zone the client prints the
   changed-key banner. During a simulated fleet key rotation a board on the
   old key and a board on the new key both pass.
3. **Aliases** (if D9 keeps the direct path).
   - `pi-sw1-p7@` logs in on the board with the password and with a key, and
     lands in `/home/pi`.
   - `id` shows the same groups as for `pi`, and `sudo -n true` works.
   - After an image pull and converge the aliases are back.
4. **Proxy.**
   - Password and public-key logins through the proxy reach the right board as
     `pi`.
   - An unknown username and `pi@` are refused after the password prompt.
   - scp, sftp, port forwarding and agent forwarding work through it.
   - With a wrong pin for a board, the proxied login to it fails.
   - After several failed proxied logins to one board, the web terminal, the
     jump route and a correct proxied login to that board still work
     (`PerSourcePenalties` exemption).
   - `failtoban` blocks a client that keeps failing, and does not block the
     same failures from an address listed in `--ignore-ip`.
   - A client whose agent holds ten unrelated keys reaches the password
     prompt on the gateway's proxy and is not banned.
   - The role refuses to converge without a proxy host key, and with one that
     equals the fleet key or a gateway sshd key.
5. **Nothing else moved, nothing else opened.**
   - The gateway's sshd, the jump account, Ansible's route and the web terminal
     (which logs in to `pi@10.21.S.P` with the password) are unchanged.
   - The `verify-server.yml` and `verify-pi.yml` access checks pass.
   - The web terminal's host-key policy is checked in fpgas.online-site
     before boards go Ed25519-only.
   - One board cannot reach another board's port 22 directly (IPv4 or IPv6),
     through the gateway's DNAT ports (#204), or through the proxy port on
     any of the gateway's addresses.
   - A board cannot reach the proxy through `ssh_proxy_address6` on port 22
     or 2224.
   - A board cannot open a connection to the site's public ssh addresses on
     ports 22, 2222, 2223 or 2224 (D10).
   - A host on the uplink side cannot open a connection to a board's IPv4
     address, except through the legacy DNAT ports.

Once the upstream network has built its side, from outside the site:

- `ssh pi-sw1-p7@ssh.<site-domain>` on port 22 reaches the board with the
  password, and a public-key login is not accepted there.
- Port 2223 presents the gateway's sshd keys and port 2224 the gateway's
  proxy key.
- The same commands from inside the site present the same keys.
- Repeated failed logins through port 22 never get the upstream proxy's
  address banned at the gateway.
- A client whose agent holds ten unrelated keys reaches the password prompt
  through the upstream proxy too, and is not banned.
- The public DNS matches the generated record set, and the old private A
  records and the `gw.<site-domain>` CNAME are gone.

Before phase 2 merges:

- On the board, `$SSH_CONNECTION` for a login through port 2224 of the
  gateway's proxy shows the client's address.
- The legacy DNAT path, NFS and the boards' outbound traffic still work.
- Removing the policy route makes the verify play fail.

## Work items

Tracking: #191.

| Phase | Issue | What | What this revision changes for it |
|---|---|---|---|
| 0 | #204 | Firewall: per-board DNAT rules name the uplink interface | Prerequisite; fixed separately |
| 1 | fpgas-online/apt#21 | Package sshpiper from the mirror repository fpgas-online/sshpiper (D5) | Waiting on Tim to create the repository; the phase 2 patch stays in our package |
| 1 | #186 | Boards: Ed25519-only host key, penalty exemption, login aliases, mapping key | Scope unchanged; the reason for Ed25519-only is now one published fingerprint, not matching the proxy; aliases wait on D9 |
| 1 | #187 | Gateway `ssh_proxy` role (sshpiper) | Own host key from the vault; one instance on 2224 with a port rewrite from port 22 on the proxy's own IPv6 address; required `--drop-hostkeys-message`; `failtoban --ignore-ip` for the upstream proxy; a drop rule for the board VLANs |
| 1 | #188 | Firewall: direct IPv6 to boards on port 22 | The internal IPv4 part is dropped (D3); the IPv6 rule waits on D9; gains the drop from board VLANs to the site's public ssh entry points (D10); needs #204 |
| 1 | #189 | DNS names and SSHFP records (D4) | New record set: `ssh.` (A, no SSHFP), `gw.` and `proxy.` (AAAA, SSHFP), AAAA-only board names (D9); removes today's private A records and the `gw.` CNAME; no helper names, no internal view |
| 1 | fpgas-online/fpgas.online-gw#2 | `/api/boards` `ssh` object gains `host`, `host_ipv6`, `user` and `direct_host` | `host` is `ssh.<site-domain>`; `host_ipv6` and `direct_host` are new |
| 1 | fpgas-online/fpgas.online-site#44 | Board pages show the new commands | The IPv4 and IPv6 proxy commands; the direct command and fleet fingerprint if D9 keeps them |
| 1 | fpgas-online/fpgas.online-docs#16 | User documentation | The commands, the prompts, password-only on port 22, never the web name |
| 1 | — | The upstream gateway's proxy on public port 22 and the forwards on 2223 and 2224 (D1; outside these repos), with the requirements written up in the docs repository page "What a site needs from its upstream network" | New |
| 1 | — | `docs/access.md`: public port 2223 as the operators' IPv4 route, once it exists | New |
| 2 | #190 | Transparent IPv4 source | Works for port 2224; port 22 behind an upstream proxy is an open point (PROXY header: `--allowed-proxy-addresses`, and the patch dials from the header's address) |
