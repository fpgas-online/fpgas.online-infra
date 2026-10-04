# Design: per-board SSH names and a username-routing SSH proxy

Date: 2026-10-03, revised 2026-10-04
Status: proposed. Revised on 2026-10-04 for the owner's decisions D1, D2 and
D3 (see [What changed on 2026-10-04 and why](#what-changed-on-2026-10-04-and-why)).
Still nothing here is implemented or deployed. [Decisions](#decisions) says
what is decided and by whom, and what is open. The work is tracked by #191
and the issues under [Work items](#work-items).

Words used here: the **gateway** is the site's fpgas.online gateway, the host
this repository configures. The **upstream gateway** is the separately
managed router a site may sit behind; it holds the site's public IPv4 address
and is not part of fpgas.online. The **fleet key** is the one ssh host key
that every board presents, because every board boots the same root.

## What changed on 2026-10-04 and why

Tim decided three of the open questions on 2026-10-04. His words:

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

**What rested on the shared key.** The first version had one key on every
path so that one name per board, `pi-sw1-p7.<site-domain>`, could be answered
by the proxy over IPv4 and by the board over IPv6 without the client
noticing. With the proxy on a key of its own, everything built on that goes:

- **The shared board name.** A board name with an A record at the proxy and
  an AAAA record at the board would now be answered by two keys. It is
  replaced by one login name per site for the proxied path and AAAA-only
  board names for the direct path ([One name, one key](#one-name-one-key)).
- **One SSHFP record on every name.** Each name now carries the fingerprint
  of the one key that answers it.
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
proxy logs in to boards with, the login aliases, the login-penalty exemption,
the direct IPv6 firewall rule and the phase 2 mechanism on the gateway.

Further changes in this revision:

- D6 (refuse unknown usernames) and D7 (login name is the board hostname) are
  recorded as decided by engineering.
- A new engineering decision, D8, "one name, one key, always".
- A new question for Tim, D9: whether the direct IPv6 path to boards is
  wanted, given D2 and D3.
- The phase 2 patch is carried in our own package only. Nothing is sent to
  the sshpiper project.
- The firewall section adds an explicit drop of the proxy port for the board
  VLANs. The existing input chain accepts everything from the board network,
  so "accept on the uplink only" was not enough.

### Issues that change

- fpgas-online/fpgas.online-infra#186 (boards offer only Ed25519, accept
  login aliases and the proxy's mapping key): scope unchanged. The reason for
  Ed25519-only changes: one published fingerprint and one SSHFP record per
  board name, not "the same key as the proxy". The text "the same one the
  gateway proxy presents" and the note that the proxy's copy of the key
  follows the root's key are wrong now.
- fpgas-online/fpgas.online-infra#187 (gateway role running sshpiper): the
  proxy's host key is its own, from the vault, never the fleet key. It
  listens on port 2224. `--drop-hostkeys-message` becomes required. It
  accepts the upstream gateway's proxy as a client (PROXY header allow-list,
  an extra accepted key, exemption from `failtoban`). The firewall adds a
  drop of the proxy port from the board VLANs. The verify step "matches the
  root's host key" becomes "matches the proxy's own key and differs from the
  fleet key". The example command becomes
  `ssh pi-sw1-p7@ssh.<site-domain>`.
- fpgas-online/fpgas.online-infra#188 (firewall: direct IPv6 and internal
  IPv4 to boards on 22): the internal IPv4 part and
  `ssh_direct_ipv4_sources` are dropped (D3). The IPv6 rule stays, subject to
  D9.
- fpgas-online/fpgas.online-infra#189 (DNS names and SSHFP): new record set:
  `ssh.<site-domain>`, `gw.<site-domain>` and AAAA-only board names. No
  shared A record, no `ipv4.`, `ipv6.` or `private-ipv4.` names, no internal
  view, no change to the gateway's internal dnsmasq zone. SSHFP per name:
  the fleet key on board names, nothing on `ssh.<site-domain>` behind an
  upstream gateway unless its operator supplies the fingerprint.
- fpgas-online/fpgas.online-infra#190 (phase 2 transparent IPv4): unchanged
  for connections that reach the gateway's proxy directly (public port 2224,
  IPv6). For public port 22 the client's address must first survive the
  upstream gateway's proxy, which is an open point for the upstream.
- fpgas-online/apt#21 (package sshpiper): the phase 2 patch stays in our
  package; it is not offered to the sshpiper project.
- fpgas-online/fpgas.online-gw#2 (`/api/boards` `ssh` object): `host` is the
  site's login name `ssh.<site-domain>`, and a new `direct_host` carries the
  board's own name.
- fpgas-online/fpgas.online-site#44 (board pages show the new command): the
  pages show `ssh pi-sw1-p7@ssh.<site-domain>`, the direct IPv6 command and
  the fleet key fingerprint.
- fpgas-online/fpgas.online-docs#16 (user documentation): the two commands,
  what each first-connection prompt means, and never the site's web name for
  ssh.
- New, outside these repositories: the upstream gateway's proxy on public
  port 22 and the forwards on 2223 and 2224, written as requirements in the
  docs repository page "What a site needs from its upstream network".
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
ssh pi-sw1-p7@ssh.<site-domain>        # through the proxy; IPv4 and IPv6
ssh pi@pi-sw1-p7.<site-domain>         # straight to the board; IPv6 only
```

Each name is always answered by the same key, whichever address family the
client uses, so a default client is prompted once per name and never sees a
changed-key warning because it took a different path. Users who opt in can
check the keys against DNS (SSHFP) where the zone is signed.

- **Phase 1:** a username-routing SSH proxy (sshpiper) on the gateway, and
  direct IPv6 to the boards. The board sees the gateway's address as the
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
  pages stop printing it. Removing it is a later, separate change.
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
source on 2026-10-03, and from live DNS queries. No SSH behaviour has been
tested against a real board yet. [Verification](#verification) lists the
tests that must pass before each phase merges.

| Finding | Evidence | Consequence |
|---|---|---|
| No SSH client uses SRV or SVCB to find a port. OpenSSH takes the port only from `-p`, `host:port`, `Port` or `getservbyname("ssh")`. | OpenSSH `ssh.c` and `readconf.c`; bz#2217 (2014) and openssh-portable PR #228 (2021) are unmerged; RFC 9460 requires a per-protocol SVCB mapping and none exists for SSH | The port cannot come from DNS. The only zero-config paths are port 22 on the board's own address (IPv6) and port 22 on a proxy that routes on the username. |
| SSHFP has no port dimension. OpenSSH looks SSHFP up at the connection host name only. | RFC 4255; `dns.c` `verify_host_key_dns()` | A name's SSHFP record can describe only one port's key. Here that is port 22. Each board needs its own DNS name for the direct path. |
| The server sends its host key during key exchange, before the client sends a username. | RFC 4253 §8, RFC 4252 §5 | A proxy cannot choose a host key per board. It presents one key to every client. |
| A client's public-key signature covers the session identifier, which differs on each side of a proxy. | RFC 4252 §7 | A proxy cannot relay public-key logins. It checks the client's key itself and logs in to the next hop with its own key (sshpiper's "mapping key"). Passwords are relayed as they are. sshpiper's YAML plugin offers only password and public-key logins. |
| `known_hosts` is keyed by host name, and by `[name]:port` when the port is not 22. `UpdateHostKeys` defaults to `yes` (OpenSSH 8.5 and later) unless `VerifyHostKeyDNS` is on or a custom `UserKnownHostsFile` is set. After login the client treats known_hosts keys for that name that the server does not list as deprecated, and removes them, unless one of the keys also appears under another known_hosts name. | `readconf.c:2950-2957`; `clientloop.c:2189-2196`, `check_old_keys_othernames()` and the removal path after it | If one name were answered by different keys on different paths, a default client would be warned, or would lose one path's key on each login with the other path and be prompted again. Splitting the two keys by algorithm does not avoid this. A name and port must always be answered by one key (D8). |
| An authoritative DNS server sees the resolver, not the client. The client-subnet option (RFC 7871) is optional and describes the path to the resolver. | Live `o-o.myaddr.l.google.com TXT` queries via 8.8.8.8, 2001:4860:4860::8888, 1.1.1.1 and a site resolver, 2026-10-03 | DNS cannot hand different SSHFP records to IPv4 and IPv6 clients. A name's A and AAAA records must lead to the same key. |
| sshd has no PROXY-protocol support. sshpiper accepts PROXY headers from its clients (`--allowed-proxy-addresses`) but never sends them onward. | OpenSSH source; sshpiper `cmd/sshpiperd/main.go` | The only way for a board to see the client's address on the proxied path is IP-level transparent proxying (phase 2). A second proxy in front of the gateway's hides the client's address from it unless that proxy sends PROXY headers. |
| Since OpenSSH 9.8, sshd penalises a source address after repeated failed logins (`PerSourcePenalties`, on by default, nothing exempt). The boards run OpenSSH 10.0 (trixie). | `sshd_config(5)` | In phase 1 every proxied login reaches the board from the gateway's address, as do the web terminal, the upload page and the jump route. A few wrong passwords through the proxy would make that board refuse the gateway for up to 10 minutes. |
| `VerifyHostKeyDNS` defaults to `no`. A "secure" SSHFP answer needs a DNSSEC-validated response and, with glibc 2.31 and later, `options trust-ad` in resolv.conf. | `readconf.c:2977`, `ssh_config(5)`, glibc 2.31 NEWS | SSHFP helps only users who opt in, and only from a signed zone. Everyone else gets one ordinary first-connection prompt per name. |

## Current state this builds on

From `main` at 96a7336d and [`docs/access.md`](../../access.md). The firewall
template and access.md were re-read at 8151b39 on 2026-10-04.

- **Board names.** `pi-sw<S>-p<P>`, IPv4 `10.21.<S>.<P>`, IPv6
  `<pib_network6_base><S:02d>::<P>` (`port_vlans.py`). dnsmasq publishes them
  in `pib_domain` through `host-record=` lines
  (`roles/pxe/templates/ports.conf.j2`). The upstream resolver delegates that
  zone to the gateway's dnsmasq. It is an internal zone with private A
  records, and this design does not use or change it.
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
- **Isolation.** The forward chain drops `v*` to `v*` traffic, so one board
  cannot reach another: each board's link to the gateway is already the
  point-to-point link D3 describes. Nothing is forwarded from the uplink to a
  board's IPv4 address except the per-port DNAT, so the site's LAN has no
  path to the boards either. The input chain accepts a service on every
  interface, `v*` included, unless the rule names an interface, and its
  `internal_networks` chain accepts everything that comes from the board
  network.
- **The gateway's own sshd** is public-key only (`sshd_pubkey_only`), on port
  22, and carries the `pi` jump account.

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
| The gateway's sshd key | On the gateway (`/etc/ssh`), as today | The gateway's own sshd |

At a site behind an upstream gateway there is a fourth: the upstream
gateway's proxy has a host key of its own, which is the upstream operator's
and not an fpgas.online credential.

The proxy does not reuse the gateway's sshd key. The proxy runs as an
unprivileged service; if it were compromised, the gateway's own identity
would go with it.

### One name, one key

**Each name, on each port, is always answered by the same key**, whatever
path the connection takes (decision D8). Different keys get different names
or different ports. `<site-domain>` is the site's public name, for Welland
`welland.fpgas.online`; which zone holds the records is decision D4.

| Name | Records | Port | Answered by | Key |
|---|---|---|---|---|
| `ssh.<site-domain>` | A and AAAA, both at whatever answers public port 22 for the site | 22 | Behind an upstream gateway: the upstream gateway's proxy, which forwards board names to the gateway's proxy. On a public address: the gateway's proxy | Behind an upstream gateway: the upstream proxy's key. On a public address: the gateway's proxy key |
| | | 2223 | The gateway's own sshd (backup route for operators and deploys) | The gateway's sshd key |
| | | 2224 | The gateway's ssh proxy (backup route that skips the upstream proxy) | The gateway's proxy key |
| `gw.<site-domain>` | AAAA at the gateway. An A record only where the gateway has a public IPv4 address of its own | 22 | The gateway's own sshd | The gateway's sshd key |
| | | 2224 | The gateway's ssh proxy | The gateway's proxy key |
| `pi-sw1-p7.<site-domain>` | AAAA only, at the board's own global address | 22 | The board itself | The fleet key |

Notes:

- **The login name.** `ssh pi-sw1-p7@ssh.<site-domain>` is the command that
  works from everywhere. The user name selects the board; the host name is
  the same for every board at the site.
- **Both address families of `ssh.<site-domain>` lead to the same proxy.**
  Behind an upstream gateway the AAAA record points at the upstream
  gateway's proxy, not at the gateway, so the client sees the same key over
  IPv4 and IPv6. If the upstream gateway's proxy does not listen on IPv6,
  the name carries no AAAA record. It never carries an AAAA record that
  points somewhere else. The same holds for its ports 2223 and 2224: they
  must behave the same on every address the name has.
- **Board names have no A record.** A board has no public IPv4 address, so an
  A record could only point at a proxy. Without one, a board name can never
  be answered by a proxy, and so never by anything but the fleet key. A
  client without IPv6 gets "Could not resolve hostname" for a board name; it
  uses `ssh.<site-domain>`.
- **The backup ports do not collide with port 22.** `known_hosts` stores a
  non-default port as `[ssh.<site-domain>]:2224`, a separate entry from the
  bare name, so the different keys on ports 22, 2223 and 2224 never meet.
  Public port 2222 (the upstream gateway's own sshd) is the upstream's
  business and is not documented for visitors.
- **The site's web name is not used for ssh.** `<site-domain>` has an A
  record at the upstream gateway and an AAAA record at the gateway itself.
  `ssh <site-domain>` is therefore answered by two different hosts, with two
  keys, depending on the address family. The board pages and the user
  documentation never print it for ssh. The legacy
  `ssh -p <port> pi@<site-domain>` form keeps working over IPv4 until the
  DNAT is retired.
- **The same key under several names is fine.** The gateway's proxy key
  answers `[ssh.<site-domain>]:2224` and `[gw.<site-domain>]:2224`; the fleet
  key answers every board name. A client is prompted once per name and shown
  the same fingerprint.
- **The login name is not in DNS.** `pi-sw1-p7@` is only what the user types.
  The proxy routes on it.
- **SSHFP.** A record describes the key on port 22 of its name, because SSHFP
  has no port dimension:
  - board names carry `SSHFP 4 2 <fleet Ed25519 SHA-256>`;
  - `gw.<site-domain>` carries the gateway's sshd key;
  - `ssh.<site-domain>` carries the gateway's proxy key where the gateway
    itself answers port 22. Behind an upstream gateway the key on port 22 is
    the upstream operator's, so the record is published only if that operator
    supplies the fingerprint as site data; otherwise the name has no SSHFP
    record.

  A user who turns on `VerifyHostKeyDNS` and connects to port 2223 or 2224 of
  a name with an SSHFP record is told the key does not match DNS, because the
  record describes port 22. This is a known limit of SSHFP, listed under
  [Open points](#open-points).
- **Signing.** SSHFP is trusted by a client only from a DNSSEC-signed zone
  with a DS record in a signed parent (decision D4). From an unsigned zone
  the client still shows the ordinary prompt.
- **Generated, not hand-written.** Names and addresses come from
  `switches | port_vlan_map`. The fleet fingerprint is not in the inventory:
  the generator reads it from the NFS root's `ssh_host_ed25519_key.pub` on
  every converge, so a regenerated key (infra#126) updates the records. The
  record set changes only when `switches` or a key changes.
- **No happy eyeballs.** OpenSSH tries addresses one at a time, so a client
  with broken IPv6 waits out the TCP connect timeout on a name with an AAAA
  record before it falls back to IPv4. The user docs recommend
  `ConnectTimeout` and `-4`.

### The paths, and what each key proves

| Path | Hops that end an ssh connection | Key the visitor's client checks |
|---|---|---|
| `ssh.<site-domain>`, port 22, behind an upstream gateway | upstream proxy, gateway's proxy, board | the upstream proxy's |
| `ssh.<site-domain>`, port 22, gateway on a public address | gateway's proxy, board | the gateway's proxy key |
| port 2224 on `ssh.<site-domain>` or `gw.<site-domain>` | gateway's proxy, board | the gateway's proxy key |
| `pi-sw1-p7.<site-domain>` (IPv6) | board | the fleet key |
| legacy per-port DNAT (IPv4) | board | the fleet key |

A proxy is the end of the visitor's ssh connection and the start of a new one
to the next hop. **Each proxy on the path sees the whole session in clear**,
the password included. Behind an upstream gateway a visitor's IPv4 connection
on port 22 is terminated twice, by the upstream proxy and then by the
gateway's proxy, before it reaches the board.

What each key proves:

- **The fleet key** proves only "this is some fpgas.online netboot board, or
  someone who has logged in to one". It does not prove which board, and it
  does not protect a connection against anyone on the network path. That
  applies to the direct IPv6 path and to the legacy DNAT path, which both
  cross the internet under this key alone. A visitor who cares uses the
  proxied path.
- **The gateway's proxy key** proves "this is the site's gateway". It is what
  the visitor checks on port 2224 and, at a site on a public address, on port
  22. Behind an upstream gateway it is what the upstream proxy checks before
  it hands a login on.
- **The upstream proxy's key** proves "this is the site's upstream gateway".
  It is not an fpgas.online credential: fpgas.online does not hold it, cannot
  rotate it, and can publish its fingerprint only if the upstream operator
  provides it. On public port 22 it is the only key the visitor's client
  checks, so on that path the visitor trusts the upstream operator with the
  session. Port 2224 is the route that does not.
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

- **Through the proxy** (`ssh pi-sw1-p7@ssh.<site-domain>`):
  - The first time ever at a site, one prompt for `ssh.<site-domain>` with
    the fingerprint of whatever answers port 22 there.
  - Never again for any board at that site: every board is behind the same
    name and key.
  - No changed-key warning when a board is swapped, rebooted or re-imaged, or
    when the fleet key is regenerated. The visitor's client never sees a
    board's key on this path.
  - A changed-key refusal only if the key on port 22 is replaced: the
    gateway's proxy key at a public-address site (kept in the vault so that
    a rebuild does not change it), or the upstream proxy's key behind an
    upstream gateway (the upstream operator's doing).
- **Straight to a board** (`ssh pi@pi-sw1-p7.<site-domain>`, IPv6):
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
(decision D5).

**Where it listens.**

- The proxy listens on its own port, `ssh_proxy_listen_port`, default 2224:
  the number the upstream gateway publishes for it (D1), so the port is the
  same on every route to it. It is bound to the gateway's uplink addresses
  (`--address`), so the unit starts `After=network-online.target`.
- The gateway's sshd stays on port 22, unchanged. It is public-key only, and
  a terminating proxy cannot relay public-key logins, so putting the proxy in
  front of it would mean loading every operator's, the jump account's and
  Ansible's keys into the proxy and would stop their logins being end to end.
- Unknown usernames are refused, not forwarded anywhere (D6). sshpiper offers
  every client the union of all pipes' methods, so an unknown name (or `pi@`)
  is offered a password prompt and refused after it ("no matching pipe").
  That also means the proxy does not reveal which names exist.
- At a site behind an upstream gateway, connections reach the proxy three
  ways: from the upstream gateway's proxy (public port 22, board names),
  through the upstream gateway's forward of public port 2224, and directly
  over IPv6.
- At a site whose gateway is on a public address the proxy has to answer
  port 22 itself, and the gateway's sshd has to move or use another address.
  That is not designed here ([Open points](#open-points)).
- The yaml plugin refuses configuration files with group or other permission
  bits, so the generated files are mode 0600.
- sshpiper's `failtoban` plugin is chained after the yaml plugin
  (`sshpiperd yaml ... -- failtoban`), so a password guesser is blocked at
  the proxy instead of at the boards. The upstream gateway's proxy must never
  be banned: every login through public port 22 arrives from its address.
  The role has to exempt that address, or run without `failtoban` at a site
  behind an upstream proxy; which of the two sshpiper allows is checked in
  #187.

**The proxy's host key.**

- An Ed25519 key generated once for the gateway and stored in the vault
  (`vault_ssh_proxy_host_key`). The role installs it as
  `/etc/sshpiper/ssh_host_ed25519_key`, owned by the proxy's service user,
  mode 0600, and passes it with `--server-key`. A rebuilt gateway gets the
  same key from the vault, so visitors and the upstream proxy see no change.
- The role fails if the variable is not set. It does not generate a key
  silently, and it never falls back to sshpiperd's default,
  `/etc/ssh/ssh_host_ed25519_key`, which is the gateway's own sshd key.
- The role also fails if the proxy's public key equals the fleet's.
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

**Fleet key rotation.** sshd loads its host keys when it starts, so after the
NFS root's key changes (#126) booted boards keep the old key until their
staggered reboot, up to about 33 minutes. During that window the proxy pins
both the old and the new board key in `known_hosts_data`. The board names'
SSHFP records switch to the new key when the reboot wave starts. Proxied
visitors notice nothing; direct IPv6 visitors see the key change once.

**Authentication to the board.**

- **Passwords** are relayed. With the published `pi` password this is the
  zero-setup path, and it works through any number of proxies.
- **Public keys** are checked by the proxy against the keys the boards already
  trust for `pi` (the operators' GitHub keys, the server-user key, the
  controller key and the jump account key). The proxy then logs in with its
  mapping key. fixpi adds the mapping key's public half as a new key source,
  `ssh_proxy`, for `pi` only (like `jump`), never for root. Only those keys
  pass the proxy: an ordinary user's own key cannot work through it (the
  proxy has no way to know it), though it would work on the direct path if
  the user adds it to the board. Ordinary users log in with the password.
- **Public keys through the upstream proxy.** The upstream gateway's proxy
  cannot relay a public-key login either. For such logins to work on public
  port 22 it has to check the visitor's key itself and then log in to the
  gateway's proxy with a key of its own, which the gateway's proxy accepts
  from an inventory list (`ssh_proxy_extra_authorized_keys`, empty by
  default). Accepting that key gives its holder nothing that the published
  password does not already give. Operators who log in with keys can always
  use port 2224 or IPv6, where the gateway's proxy checks their key itself.
- **What does not work through the proxy:**
  - `pi@` and `root@`, because the proxy only knows `pi-sw<S>-p<P>` names and
    always logs in as `pi`. The user docs say to use the `pi-sw<S>-p<P>@`
    form with `ssh.<site-domain>`.
  - `ansible@`. Ansible keeps using the jump route.

**Firewall.**

- Input: accept the proxy port on `iifname {{ eth_uplink }}`, and drop it on
  every other interface with a rule placed before `jump internal_networks`.
  That chain accepts everything from the board network, so without the drop a
  board could reach the proxy on the gateway's uplink address. Boards must
  not reach the proxy directly: connections that come from the proxy carry
  the gateway's identity (`10.21.0.1`, which the boards exempt from login
  penalties, below) and, in phase 2, the transparent path.
- Forward, IPv6: `iifname {{ eth_uplink }} oifname "v*" ip6 daddr <board
  addresses> tcp dport 22 accept`. This exposes each board's sshd, with the
  published password, to the IPv6 internet. The per-port DNAT already gives
  the same exposure over IPv4. This rule is the direct path, and it is the
  subject of D9.
- **Nothing else is opened** (D3). There is no forward rule from the site's
  networks to the boards' IPv4 addresses, and `v*` to `v*` stays dropped. A
  device on the site's LAN reaches a board exactly as an internet client
  does: through the proxy, or over IPv6 through the rule above, arriving on
  the gateway's uplink. A board reaches another board's sshd by no direct
  path. A board user can still go out to the internet and come back in
  through the proxy like any other visitor; behind an upstream proxy the
  gateway cannot tell that connection from anyone else's.

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
  other proxied user out of that board for up to 10 minutes. Brute-force
  protection for the proxied path moves to the proxies. After phase 2
  proxied logins carry the client's address and are penalised per client
  again; the exemption stays for the gateway's own connections.
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
    that the same login name works on both paths. They are a convenience;
    `pi@` works on the direct path without them.
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
[One name, one key](#one-name-one-key). Where they are hosted and whether the
zone is signed is decision D4. The gateway's internal dnsmasq zone
(`pib_domain`) is not changed.

**What the board pages show.** fpgas.online-gw serves `/api/boards`, and its
`ssh` object gains `user`, `host` and `direct_host`:

```json
{"host": "ssh.<site-domain>", "user": "pi-sw1-p7", "port": 22,
 "direct_host": "pi-sw1-p7.<site-domain>", "legacy_port": 10722}
```

fpgas.online-site renders the board pages. They print
`ssh pi-sw1-p7@ssh.<site-domain>` first, then the direct IPv6 command with
the fleet key fingerprint (if D9 keeps the direct path), with the legacy `-p`
form underneath until the DNAT is retired.

**Source address.** On the proxied path the board sees the gateway's address.
The legacy DNAT path shows the client's real address, so phase 1 is a
regression for proxied logins until phase 2.

**Monitoring.** If the proxy is down, logins through `ssh.<site-domain>` are
gone. The proxy runs under systemd with `Restart=on-failure`, and
`verify-server.yml` checks that it is listening and that a login through it
reaches a board. Behind an upstream gateway the public port 22 path also
depends on the upstream proxy, which the gateway cannot restart; a check from
outside the site is the only thing that sees it.

### Requirements on the upstream network

These apply to a site behind an upstream gateway. They are requirements, not
a design for the upstream. They belong in the docs repository
(fpgas-online/fpgas.online-docs) page "What a site needs from its upstream
network", which is where an upstream operator reads them.

| Requirement | Why |
|---|---|
| Public port 22: a username-routing ssh proxy that forwards logins under board names (`pi…`) to the gateway's proxy port, keeping the user name | D1 |
| Public port 2223 forwarded to the gateway's sshd (port 22); public port 2224 forwarded to the gateway's proxy port. Plain forwards that keep the client's source address | D1; the backup routes, and the routes on which the visitor checks an fpgas.online key |
| The upstream proxy pins the gateway's proxy key and refuses anything else | It is the only check on that hop |
| The upstream proxy relays password logins. For public-key logins it logs in to the gateway's proxy with a key of its own, whose public half is given to the site | A proxy cannot relay a public-key login |
| The upstream proxy does not pass host-key announcements from the next hop to the client | The client would be offered a key that is not the one it connected to. The gateway's proxy already drops the boards' announcements; this covers its own |
| The upstream proxy's host key does not change without notice | Every visitor's client refuses to connect after a change |
| Whatever addresses `ssh.<site-domain>` resolves to, ports 22, 2223 and 2224 behave the same on all of them. If the upstream proxy has no IPv6 listener, the name has no AAAA record | One name, one key |
| The upstream proxy limits password guessing | The gateway's proxy sees all of these logins from one address and cannot tell the clients apart |
| Clients inside the site reach the same services under the same names as clients outside: `ssh.<site-domain>` leads to the same proxy and key, and `gw.<site-domain>` to the gateway (a route to the gateway's uplink address, or hairpin) | D3; a laptop that moves between inside and outside must not see a key change |
| Wanted, not required: the upstream proxy tells the gateway's proxy the client's address (PROXY protocol header) | `failtoban` and phase 2 on the port 22 path |

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

- On public port 2224 and at a public-address site, the gateway's proxy sees
  the visitor's own address, and phase 2 works as described.
- On public port 22 behind an upstream gateway, the gateway's proxy sees the
  upstream proxy's address. For the board to see the visitor's address, that
  address has to survive the upstream proxy first: either the upstream proxy
  sends a PROXY protocol header, which sshpiper accepts from listed addresses
  (`--allowed-proxy-addresses`) and which the patch would then use as the
  address to dial from, or the upstream proxy is itself transparent. Whether
  either is possible is the upstream's matter and is not designed here. Until
  it is settled, phase 2 gives boards the visitor's address on port 2224
  only, and the upstream proxy's address on port 22. This is an open point.
- Proxied IPv6 connections (an IPv6 visitor using `ssh.<site-domain>`) are
  dialled to the board over IPv4, so an IPv6 client address cannot be carried
  at all without the RFC 6052 idea in [Non-goals](#non-goals). The board sees
  the gateway's address for those.

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
| D4 | Where do the public names live, who serves them, and is the zone signed? | **Open**, see below. |
| D5 | How is sshpiper packaged? | **Open**, see below. |
| D6 | Does the gateway's proxy forward unknown usernames to the gateway's own sshd? | **Decided by engineering, 2026-10-04**: no. The proxy refuses every name that is not a board name. The gateway's sshd stays reachable on its own: public port 2223 over IPv4 (D1), and port 22 on the gateway's own address over IPv6. |
| D7 | Is the login name the board hostname or the board slug? | **Decided by engineering, 2026-10-04**: the hostname, `pi-sw<S>-p<P>`, only, for now. Hostnames are placements, not identities; slug aliases can be added later from the same data. |
| D8 | How do the proxy's key and the fleet key coexist without warnings in default clients? | **Decided by engineering, 2026-10-04, open to Tim's veto**: one name, one key, always. `ssh.<site-domain>` for the proxied path, AAAA-only board names for the direct path, backup routes on other ports ([One name, one key](#one-name-one-key)). |
| D9 | May a visitor connect straight to a board's own IPv6 address? | **Open, for Tim**, see below. |

### D4: where the names live

The records are `ssh.<site-domain>` (A, AAAA), `gw.<site-domain>` (AAAA) and
one AAAA per switch port, plus SSHFP records. They change only when the
site's public addresses, its list of switches or a key changes. Board names
follow switch ports, not boards, so plugging boards in and out changes
nothing in DNS.

SSHFP records are only useful to a client if the zone is DNSSEC-signed, with
a DS record in a signed parent, and the user has turned on
`VerifyHostKeyDNS`. Then the first-connection prompt goes away. From an
unsigned zone the client still prompts, and the record is worth nothing
against someone who can forge DNS answers.

| Option | What an operator does | What a visitor sees |
|---|---|---|
| A. The records go into the existing public `fpgas.online` zone, which is served outside the site. The converge writes a zone fragment; whoever runs that zone loads it. Signing follows that zone | Loads the fragment when the switch list, an address or a key changes. No new service on the gateway, no new requirement on the upstream network | Names resolve whether or not the site is up. SSHFP is checked only if `fpgas.online` is signed; otherwise the ordinary prompt |
| B. `<site-domain>` becomes a zone of its own, delegated to the gateway, which runs a signing authoritative server (dnsmasq cannot sign) | Runs and monitors a public DNS server on the gateway; arranges the delegation and a DS record; the upstream network must forward port 53. The site's web name is in the same zone | Records are always current and can be signed. When the gateway is down, the site's names, the web name included, stop resolving |
| C. No per-board names. Only `ssh.<site-domain>` and `gw.<site-domain>`, entered by hand once | Nothing recurring | The proxied command works. The direct path needs the board's IPv6 address copied from the board page, and has no SSHFP |

Recommendation: **A**. The record set is small and nearly static, a site
outage should not take the site's names with it, and it asks nothing new of
the upstream network. Whether `fpgas.online` is signed today has not been
checked; if it is not, signing it is a separate decision and SSHFP waits for
it.

### D5: how sshpiper is packaged

| Option | What an operator sees | Consequences |
|---|---|---|
| A. A mirror repository under fpgas-online (`fpgas-online/sshpiper`, upstream branches mirrored, packaging on its own branch) that builds a Debian package into the fpgas.online apt repository | `apt install sshpiper` on the gateway; upgrades arrive through apt like our other packages | Can carry the phase 2 patch. Costs one more packaging repository to keep building |
| B. The role downloads a pinned upstream release binary and checks its checksum | A binary under `/usr/local`, upgraded by changing a version and checksum in this repository | Nothing to build. Cannot carry a patch, so phase 2 would need option A anyway |

Recommendation: **A**, the same model as our other packaged tools, because
phase 2 needs the patch.

### D9: the direct IPv6 path

In plain words: **may a visitor connect straight to a board's own IPv6
address, passing through the gateway's firewall but not through its proxy?**

It is the one path in this design that is not terminated at the gateway. The
packets are forwarded by the gateway, so at the link level the board still
talks only to the gateway (D3). But the ssh connection ends on the board, and
the only key protecting it across the internet is the fleet key, which D2
says is not fit to protect traffic. The legacy per-port DNAT has the same
property over IPv4 today.

| Option | What a visitor sees |
|---|---|
| A. Keep it, documented as the less protected path | Two commands on the board page. The direct one works with the visitor's own key if they add it to the board, shows the board the visitor's real address, and does not depend on the proxy. Its host key proves little, and the page says so |
| B. Drop it: no IPv6 forward rule, no board names in DNS; IPv6 visitors use the proxy | One command. Every ssh session ends on the gateway's proxy. No per-board DNS records at all, which also settles most of D4. The login aliases on the boards are no longer needed |

Recommendation: **A**, because the legacy DNAT already exposes the same thing
over IPv4 and the direct path is the only one where a visitor's own key
works. If the answer is B, #188 and the board-name half of #189 are closed
unbuilt, and the alias part of #186 is dropped.

## Open points

These are not the owner's decisions; they are things the design does not
settle yet.

- **A site whose gateway is on a public address.** There the gateway's proxy
  must answer port 22, on the address where the gateway's sshd listens today.
  The likely answer is D1's layout applied to the gateway itself (proxy on
  22, sshd on 2223), which moves Ansible's and the operators' route. No such
  site with `switches:` exists, so it is left until one does.
- **The upstream proxy and IPv6.** Whether the upstream gateway's proxy
  listens on IPv6 decides whether `ssh.<site-domain>` has an AAAA record. An
  IPv6-only visitor without it uses `gw.<site-domain>` port 2224 or a board
  name.
- **The client's address through the upstream proxy** (phase 2 and
  `failtoban`), above.
- **Whether `failtoban` can exempt an address** and whether it uses the
  address from a PROXY header. Checked in #187.
- **SSHFP and the backup ports.** SSHFP describes port 22. A user with
  `VerifyHostKeyDNS` on gets a mismatch warning on ports 2223 and 2224 of a
  name that has an SSHFP record. Giving the backup routes names of their own
  would avoid it, at the price of more names.
- **Inside the site.** D8 holds for a laptop that moves between the site's
  LAN and the internet only if the upstream network makes
  `ssh.<site-domain>` lead to the same proxy from both sides.
- **The site's web name.** Someone who types `ssh pi-sw1-p7@<site-domain>`
  reaches the upstream proxy over IPv4 and the gateway's sshd over IPv6,
  where the login fails with `Permission denied (publickey)`. Documentation
  is the only defence.

## Verification

Before phase 1 merges, on one board and in the CI VM:

1. **One name, one key.**
   - `ssh-keyscan` of a board returns exactly one key, the NFS root's
     `ssh_host_ed25519_key.pub`.
   - `ssh-keyscan` of the proxy port returns exactly one key. It matches the
     vaulted proxy key, and it differs from the fleet key and from the
     gateway's sshd key.
   - With a default `ssh_config`, repeated logins to `ssh.<site-domain>` over
     IPv4 and IPv6, to different boards, prompt only on the first connection,
     and known_hosts holds one unchanged line for the name after each step.
   - A proxied login leaves known_hosts byte for byte unchanged
     (`--drop-hostkeys-message`).
   - Logins to two board names over IPv6 prompt once each, with the same
     fingerprint, and never touch the `ssh.<site-domain>` line.
   - No board name resolves to an A record, and every address of
     `ssh.<site-domain>` returns the same key on port 22.
2. **SSHFP.** Against a signed test zone with `VerifyHostKeyDNS yes`, a board
   name reports "matching host key fingerprint found in DNS".
3. **Aliases.**
   - `pi-sw1-p7@` logs in on the board with the password and with a key, and
     lands in `/home/pi`.
   - `id` shows the same groups as for `pi`, and `sudo -n true` works.
   - After an image pull and converge the aliases are back.
4. **Proxy.**
   - Password and public-key logins through the proxy reach the right board as
     `pi`.
   - An unknown username and `pi@` are refused after the password prompt.
   - scp, sftp, port forwarding and agent forwarding work through it.
   - A board cannot connect to the proxy port, on any of the gateway's
     addresses.
   - With a wrong pin for a board, the proxied login to it fails.
   - After several failed proxied logins to one board, the web terminal, the
     jump route and a correct proxied login to that board still work
     (`PerSourcePenalties` exemption), and the proxy's `failtoban` blocks the
     failing client but never the upstream proxy's address.
   - The role refuses to converge without a proxy host key, and with one that
     equals the fleet key.
5. **Nothing else moved, nothing else opened.**
   - The gateway's sshd, the jump account, Ansible's route and the web terminal
     (which logs in to `pi@10.21.S.P` with the password) are unchanged.
   - The `verify-server.yml` and `verify-pi.yml` access checks pass.
   - The web terminal's host-key policy is checked in fpgas.online-site
     before boards go Ed25519-only.
   - One board cannot open a connection to another board's port 22, over IPv4
     or IPv6, and a host on the uplink side cannot open one to a board's IPv4
     address.

Once the upstream network has built its side, from outside the site:

- `ssh pi-sw1-p7@ssh.<site-domain>` on port 22 reaches the board with the
  password.
- Port 2223 presents the gateway's sshd key and port 2224 the gateway's proxy
  key.
- The same commands from inside the site present the same keys.

Before phase 2 merges:

- On the board, `$SSH_CONNECTION` for a login through the gateway's proxy
  port shows the client's address.
- The legacy DNAT path, NFS and the boards' outbound traffic still work.
- Removing the policy route makes the verify play fail.

## Work items

Tracking: #191.

| Phase | Issue | What | What this revision changes for it |
|---|---|---|---|
| 1 | fpgas-online/apt#21 | Package sshpiper (D5) | The phase 2 patch stays in our package |
| 1 | #186 | Boards: Ed25519-only host key, penalty exemption, login aliases, mapping key | Scope unchanged; the reason for Ed25519-only is now one published fingerprint, not matching the proxy |
| 1 | #187 | Gateway `ssh_proxy` role (sshpiper) | Own host key from the vault, port 2224, required `--drop-hostkeys-message`, the upstream proxy as a client, a drop rule for the board VLANs |
| 1 | #188 | Firewall: direct IPv6 to boards on port 22 | The internal IPv4 part is dropped (D3); the IPv6 rule waits on D9 |
| 1 | #189 | DNS names and SSHFP records (D4) | New record set: `ssh.<site-domain>`, `gw.<site-domain>`, AAAA-only board names; no helper names, no internal view |
| 1 | fpgas-online/fpgas.online-gw#2 | `/api/boards` `ssh` object gains `host`, `user` and `direct_host` | `host` is `ssh.<site-domain>`; `direct_host` is new |
| 1 | fpgas-online/fpgas.online-site#44 | Board pages show the new commands | Two commands and the fleet key fingerprint |
| 1 | fpgas-online/fpgas.online-docs#16 | User documentation | The two commands, the prompts, never the web name |
| 1 | — | The upstream gateway's proxy on public port 22 and the forwards on 2223 and 2224 (D1; outside these repos), with the requirements written up in the docs repository page "What a site needs from its upstream network" | New |
| 1 | — | `docs/access.md`: the operators' IPv4 route becomes public port 2223, once it exists | New |
| 2 | #190 | Transparent IPv4 source | Works for port 2224 and public-address sites; port 22 behind an upstream proxy is an open point |
