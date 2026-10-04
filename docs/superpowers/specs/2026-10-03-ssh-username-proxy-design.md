# Design: ssh to boards by name, through a username-routing ssh proxy

Date: 2026-10-03, revised 2026-10-04 and 2026-10-05
Status: decided, not yet built. The owner's decisions are in
[Decisions](#decisions). Nothing here is deployed. Three points need the
owner before the part they touch is switched on; they are marked **FINDING**
and collected in [Findings for the owner](#findings-for-the-owner). None of
them blocks building. The work is tracked by #191 and the issues under
[Work items](#work-items).

Words used here:

- The **gateway** is the site's fpgas.online gateway, the host this
  repository configures.
- The **upstream gateway** is the separately managed router a site may sit
  behind. It holds the site's public IPv4 address. This repository does not
  configure it and never names it.
- The **transit address** is the gateway's uplink IPv4 address
  (`eth_uplink_static_address`), private at a site behind an upstream
  gateway.
- `<site>` is the site's public name, for example `welland.fpgas.online`.
- `<board-name>` is a board's login name, for example `pi-sw2-p47`. It names
  a switch port, a place, and not a device: whatever is plugged in there
  answers.
- The **fleet key** is the one ssh host key that every board presents,
  because every board boots the same root. Anyone who logs in to a board can
  read it.
- The **site proxy key** is the one ssh host key that answers port 22 of
  `<site>`.

## What a visitor types

```
ssh pi-sw2-p47@welland.fpgas.online            # through the proxy; IPv4 or IPv6
ssh pi@ipv6.pi-sw2-p47.welland.fpgas.online    # straight to the board; IPv6 only
```

No port number, no client configuration. The first form is the one every
page prints first.

## Decisions

Tim's words are quoted from the question record; the answers of 2026-10-04
are also copied on #191. Where a quote named a machine, the name is replaced
by its role in square brackets.

| # | Decision | Source |
|---|---|---|
| D1 | Public IPv4 port 22 is a username-routing ssh proxy **on the upstream gateway**. It forwards board names to the gateway's proxy; every other name goes to the upstream gateway's own sshd. Backup ports: 2222 is the upstream gateway's own sshd, 2223 the gateway's own sshd, 2224 the gateway's proxy | Tim, 2026-10-04: "The site's gateway should get it's own proxy which works in a somewhat similar manner to the proxy on the fpgas.online gateway. piXXX users will be forwarded the fpgas.online gateway. As a backup, port 2222 should go to [the upstream gateway]'s ssh server and port 2223 should forwarded directly to [the fpgas.online gateway's] ssh server and port 2224 should forward directly to [the fpgas.online gateway's] ssh proxy." |
| D2 | The fleet key is not what protects traffic between a client and a proxy. The proxy has a host key that is not public | Tim, 2026-10-04: "the ssh host key is on the rpi and available to anyone who logs into that system, so should *not* be what encrypts traffic to [the fpgas.online gateway]" |
| D3 | Devices on the site's own LAN reach everything through the gateway, like internet devices. A board talks only to the gateway | Tim, 2026-10-04: "Other devices behind [the upstream gateway] should access all welland.fpgas.online resources via gw.welland.fpgas.online and look pretty much like any internet device except with a private IP addresses. Devices inside XXX.welland.fpgas.online should not be accessing other XXX.welland.fpgas.online devices, they should only be talking to gw.welland.fpgas.online -- IE really pi1.welland.fgpas.online<->gw.welland.fgpas.online should be considered a direct point to point link." |
| D5 | sshpiper is packaged from the mirror repository fpgas-online/sshpiper | Tim, 2026-10-04: "Mirror repo fpgas-online/sshpiper; I will create it". The repository exists since 2026-10-04 |
| D13 | The proxy carries visitors to boards and admins to the gateway, so the client-to-proxy hop always uses a key that is not public. The hop behind the proxy is trusted | Tim, 2026-10-04: "Data from visitor<->pi should be treated as effectively unencrypted (everyone on the planet has root on the pi and could just intercept it there). Data from admin<->gateway needs to be encrypted. As the proxy is doing both XXX<-A1->proxy<-B1->pi and XXX<-A2->proxy<-B2->gateway and we can't tell if XXX is a visitor or an admin before setting up A1 or A2, they both needs to be encrypted with a key that is not publically available. Both B1 and B2 being unecrypted is also fine as B2 is a trusted step." |
| D14 | Visitors use `ssh <board-name>@<site>`, the same on IPv4 and IPv6. They are never told to use `-p` | Tim, 2026-10-04: "We want people to always use something like `ssh pi-sw2-p47@welland.fpgas.online` without having to understand the complexity of a shared single ipv4." and "We don't want people to ever have to use `-p 10722` as that does not work with sshfp." |
| D15 | `ssh pi@ipv6.<board-name>.<site>` goes straight to the board over IPv6. There are no `ipv4.` names and no plain `<board-name>.<site>` names | Tim, 2026-10-04, as a question: "Maybe `ssh pi@ipv6.pi-sw2-p47.welland.fpgas.online` is equivalent to `ssh pi-sw2-p47@welland.fpgas.online` and `ssh pi@ipv4.pi-sw2-p47.welland.fpgas.online` and `ssh pi@pi-sw2-p47.welland.fpgas.online` don't work?" This reading was shown to him twice afterwards and drew no comment. See F3 |
| D16 | **One site proxy key, shared by both proxies.** Not the fleet key and not either machine's own sshd key. It is installed on the gateway's proxy and on the upstream gateway's proxy, so every client sees one key for `<site>` on IPv4 and IPv6, with one SSHFP record set | Tim, 2026-10-05, choosing "One shared host key on both proxies". The accepted text: "I generate one proxy host key for the site (not the fleet key, not either machine's own sshd key); it is installed on the fpgas.online gateway's proxy and on the upstream gateway's proxy; every client sees one key for the name on IPv4 and IPv6, with one SSHFP record. Cost: that private key also lives on the upstream gateway, so the fpgas.online docs must say a site's upstream proxy holds the site's proxy key." This replaces his 2026-10-04 choice of the key-types trick, which the lab disproved ([Rejected alternatives](#rejected-alternatives)) |

Decided by engineering, open to the owner's veto:

| # | Decision |
|---|---|
| E1 | On the gateway's proxy a name that is not a board name is **refused by the proxy itself**. Nothing from the proxy reaches the gateway's own sshd in phase 1. Operators reach that sshd on port 2223. This is the default while the owner is asked (O1) |
| E16 | The proxy is not exposed on any public port until our package carries the patch that closes a failed onward connection ([The required package patch](#the-required-package-patch)) |
| E2 | The login name is `<board-name>`, the board's host name `pi-sw<S>-p<P>`, and nothing else for now. Names from the fleet registry can be added later as further pipes |
| E3 | On the gateway's uplink, port 22 is the proxy, port 2223 the gateway's own sshd and port 2224 the proxy, on IPv4 and on IPv6 alike. A client inside the site, a client on IPv6 and the upstream gateway's forwards all find the same thing on the same port |
| E4 | A board cannot reach the site's own public ssh entry points, nor the proxy |
| E5 | Public IPv4 port 22 behind an upstream gateway is password-only for board names. Key logins to boards use IPv6 or port 2224 |
| E6 | While the fleet key rotates, the `ipv6.` names carry both fingerprints and the proxy pins both |
| E7 | Boards get no login aliases. The direct path logs in as `pi` (D15), and the proxy rewrites the name, so nothing needs `<board-name>` to exist as an account on a board |
| E8 | The generated DNS records are written as a zone fragment, and the verify play fails when public DNS differs from it. Loading the fragment into the zone is done by the zone's owner. See F4 |

## Problem

A board page prints this today (`Pi.ssh_port` in fpgas.online-site):

```
ssh -p <per-board port> pi@<site>
```

The port is the gateway's per-board DNAT
([`roles/firewall`](../../../ansible/roles/firewall/templates/nftables.conf.j2),
`<switch><pp>22` to the board's port 22). It has four problems:

1. **It does not work from outside at a site behind an upstream gateway**
   unless that gateway forwards one port per board. Observed 2026-10-05 at
   Welland: the upstream gateway's own sshd holds public IPv4 port 22, and no
   ssh port is forwarded to the gateway. So no ssh command reaches a board
   from the IPv4 internet there.
2. **The port has to be copied from a web page.** Nothing in DNS tells an ssh
   client which port to use.
3. **The host key cannot be checked against DNS.** SSHFP is looked up by name
   only, with no port.
4. **IPv6 is not used**, although every board has a global IPv6 address.

## Goal and phases

Every board is reachable from outside with a stock OpenSSH client, with the
two commands under [What a visitor types](#what-a-visitor-types). A default
client is asked once to accept the key of `<site>` and never sees a
changed-key refusal because it took the other address family. A client that
opts in checks the key against DNS (SSHFP).

- **Phase 1:** works from outside on IPv4 and IPv6. On the proxied path the
  board sees the gateway's address as the source.
- **Phase 2** (#190): transparent proxying, so the board sees the client's
  address on proxied IPv4 logins. Phase 1 does not depend on it in any way.

## Non-goals

- Configuring the upstream gateway from this repository. This document
  states what the gateway needs from it
  ([What the upstream gateway must provide](#what-the-upstream-gateway-must-provide)).
- Per-board host keys. Every board shares one root and so one host key.
- An IPv6-only board network. Network boot and the NFS root are IPv4.
- A path from the site's own LAN to the boards, or from one board to
  another (D3).
- Hosts without `switches:`. Every template and task here is guarded
  `when: switches is defined`, like the rest of the per-port model.

## Findings that shape the design

From the OpenSSH source, the RFCs, the sshpiper source at commit `2038d993`
of fpgas-online/sshpiper, live DNS queries, and the lab in
[`tests/lab/ssh_key_types`](../../../tests/lab/ssh_key_types/README.md)
(PR #214). Only the lab rows were tested with real clients.

| Finding | Evidence | Consequence |
|---|---|---|
| No ssh client takes a port from DNS | OpenSSH `ssh.c`, `readconf.c`; no SVCB mapping exists for ssh | The only paths without a port number are port 22 on the board's own address and port 22 on a proxy that routes on the user name |
| SSHFP has no port. OpenSSH looks it up at the host name only | RFC 4255; `dns.c` `verify_host_key_dns()` | A name's SSHFP records describe one port's key. A name gets SSHFP only for the key on its port 22, and no other port is documented under that name |
| The server sends its host key before the client sends a user name | RFC 4253 §8, RFC 4252 §5 | A proxy presents one key to every client. It cannot choose a key per board, or per visitor and admin |
| A client's public-key signature covers the session identifier, which differs on each side of a proxy | RFC 4252 §7 | A proxy cannot relay a public-key login. It checks the client's key itself and logs in onward with a key of its own. Passwords are relayed as typed |
| OpenSSH's known_hosts check is not per key type. If a name has any stored key and the presented key equals none, the result is "changed", whatever the types | `check_hostkeys_by_key_or_type()` in `hostfile.c`; lab scenarios 1a to 1d, 2a, 2b on OpenSSH 8.2, 8.9, 9.2, 9.6, 10.0 and 10.5 ([results](../../../tests/lab/ssh_key_types/results-2026-10-04.md)) | Two proxies with two different keys under one name do not work for a default client, even with different key types. One name and port needs one key (D16) |
| After login a server announces its host keys (`hostkeys-00@openssh.com`). OpenSSH 8.5 and later then removes stored keys the server did not list. sshpiper forwards the backend's announcement unless told not to | Lab scenarios 3b, 6d to 6g | Both proxies run with `--drop-hostkeys-message`. Without it the client is offered the board's or the sshd's keys under the proxy's name |
| `VerifyHostKeyDNS` defaults to `no`. With `yes` and a DNSSEC-validated matching SSHFP record the key is accepted with no prompt. If SSHFP records exist and none matches, the client prints the changed-key banner | `sshconnect.c` `verify_host_key()`, `ssh_config(5)`. Not tested in the lab | SSHFP helps only clients that opt in, and only from a signed zone. A published record that does not match is worse than none |
| An authoritative DNS server sees the resolver, not the client | Live queries, 2026-10-03 | DNS cannot hand different SSHFP records to IPv4 and IPv6 clients |
| sshpiperd listens on one address and one port (`--address`, `--port`). `--server-key` defaults to `/etc/ssh/ssh_host_ed25519_key` | `cmd/sshpiperd/main.go`, `daemon.go` | One instance on a wildcard address; the firewall maps port 22 to it. The key path is always given explicitly, or the proxy would present the gateway's own sshd key |
| The yaml plugin tries pipes in file order and uses the first whose user name matches and whose check passes. A pipe with `authorized_keys` is a public-key pipe; one without is a password pipe and relays the password unchecked. `username_regex_match` makes the name a regular expression. `authorized_keys` paths may contain `$DOWNSTREAM_USER`. The proxy offers every client the union of all pipes' methods | `plugin/yaml/skel.go`, `libplugin/skel/skel.go` | Only board names have pipes. A name with no pipe is still offered a password prompt and is refused after it ("no matching pipe"), so the proxy does not reveal which names exist |
| sshpiperd does not close an onward connection whose login failed. It stays open until the target sshd's `LoginGraceTime` and holds one of that sshd's unauthenticated slots (`MaxStartups 10:30:100`) | Measured by the reviewer of this document with the real sshpiperd and OpenSSH in containers, 2026-10-05: 18 wrong passwords through the proxy left the target sshd at 18 startups, and 5 of 20 key logins made to it directly were dropped. `authUpstream` in the sshpiper.crypto fork never closes the connection | About ten wrong passwords in two minutes for one board name start dropping the web terminal's and the jump route's logins to that board. A catch-all pipe to the gateway's sshd would let anyone do the same to the operators' logins. So: no catch-all (E1), and a required patch in our package (E16) |
| The yaml and failtoban plugins are built only with the Go build tag `full` | `//go:build full` in `plugin/yaml`, `plugin/failtoban` | The package builds with that tag |
| failtoban counts onward-login failures and pipe-creation failures per client address, bans at `--max-failures` (default 5) for `--ban-duration` (default 60 minutes), and never bans an `--ignore-ip` address | `plugin/failtoban/main.go` | Explicit, gentler values; the upstream proxy's address is ignored |
| sshd has no PROXY-protocol support. sshpiper accepts PROXY headers (`--allowed-proxy-addresses`) and never sends them | OpenSSH source; `cmd/sshpiperd/main.go` | A board sees the client's address on a proxied login only through transparent proxying (phase 2) |
| Since OpenSSH 9.8 sshd penalises a source address after failed logins (`PerSourcePenalties`, on by default) | `sshd_config(5)` | In phase 1 every proxied login reaches a board from the gateway's address, as do the web terminal and the jump route. The boards exempt that address |

## Current state this builds on

From `main` at 9bc1430 (2026-10-05).

- **Board names and addresses.** `pi-sw<S>-p<P>`, IPv4 `10.21.<S>.<P>`, IPv6
  `<pib_network6_base><S:02d>::<P>`, all from `switches | port_vlan_map`
  (`port_vlans.py`). No per-board configuration exists and none is added.
- **The public zone.** `fpgas.online` is the owner's zone, hosted outside the
  site and DNSSEC-signed (queried 2026-10-04). It holds, for a site: `<site>`
  with an A record (the public IPv4 address) and an AAAA record (the
  gateway); `gw.<site>` as a CNAME to `<site>`; and `<board-name>.<site>`
  names with A records that carry the boards' **private** addresses.
- **Board accounts.** One `pi` account (NOPASSWD sudo), one published
  password (`pi_pw`), one set of `authorized_keys` and one set of host keys
  (RSA, ECDSA, Ed25519), shared by every board.
- **Root changes reboot the fleet** (`docs/access.md`, "why a key change
  reboots the fleet").
- **The gateway's own sshd** is public-key only (`roles/sshd`,
  `sshd_pubkey_only`) and listens on port 22 of every address: no role sets
  `Port` or `ListenAddress` today. Its accounts are the operators, the
  automation account, and `pi`, a restricted jump account
  (`roles/jump`). Ansible reaches it at `gw.<site>` over IPv6.
- **The firewall** accepts tcp 22 on every interface in its input chain, and
  `internal_networks` accepts everything from the board network. The
  per-board DNAT rules name the uplink interface (#204, fixed). The forward
  chain drops new IPv6 connections from the uplink to a board.

## Design

### Host keys in plain words

When an ssh client connects, the server proves who it is with its **host
key**. The client remembers the public half under the name it connected to.
The first connection to a name asks the user to accept the key; later ones
are silent while the key is the same, and refused if it differs. The host
key does not itself encrypt the session, but whoever holds its private half
can pretend to be the server to any client whose traffic they can intercept,
and then read everything, passwords included.

| Key | Where its private half is | Presented by | Published as |
|---|---|---|---|
| The site proxy key (Ed25519) | The Ansible vault; the gateway, readable only by the proxy's service user; **and the upstream gateway**, where its operator installs it | Both proxies, on port 22 of `<site>` | SSHFP on `<site>`; the fingerprint on the board pages |
| The fleet key (Ed25519) | The boards' shared root: readable by every visitor | Every board | SSHFP on each `ipv6.<board-name>.<site>`; the fingerprint on the board pages |
| The gateway's sshd keys | The gateway, `/etc/ssh`, as today | The gateway's own sshd | Not in DNS |

**The site proxy key.**

- One key per site, type Ed25519. One type only: each proxy then offers
  exactly one key, there is one SSHFP record set, and the lab shows that a
  second type buys nothing. Ed25519 is the type every supported client
  prefers and the type the boards use.
- Generated once by the site's operator (`ssh-keygen -t ed25519`, no
  passphrase) and stored in the vault as `vault_ssh_proxy_host_key`. It is
  never the fleet key and never a key that an sshd presents.
- The `ssh_proxy` role installs it on the gateway. The role does not
  generate it and fails without it.
- **It also lives on the upstream gateway** (D16). The site's operator
  copies it there from the vault by hand; this repository does not manage
  the upstream gateway. This is the stated cost of D16: whoever controls
  the upstream gateway can pretend to be the site's proxy, on IPv6 as well
  as IPv4, and can read every session that passes through its own proxy.
  The upstream gateway is on the IPv4 path of every session anyway.
- A site with no upstream gateway has the key only on the gateway.

**Rotating the site proxy key.** Both proxies and the SSHFP records change
together:

1. Generate the new key and put it in the vault.
2. Lower the TTL of the SSHFP records on `<site>`; wait out the old TTL.
3. Install the new key on the upstream gateway's proxy and converge the
   gateway, in one maintenance step. Between the two, the address families
   answer with different keys.
4. Replace the SSHFP records and the fingerprint on the board pages.

Every visitor who has connected before gets the changed-key refusal once and
clears it with `ssh-keygen -R <site>`. There is no way around that, so the
key is rotated only when it has to be: when it may have leaked, or when the
upstream gateway changes hands.

**What each key proves.**

- The site proxy key proves "this is one of the site's proxies". It protects
  the client-to-proxy hop for visitors and admins alike (D13).
- The fleet key proves only "this is some fpgas.online board, or someone who
  has logged in to one". The direct IPv6 path crosses the internet under
  this key alone. That is accepted (D13): visitor-to-board data is treated
  as public.
- Each proxy ends the client's ssh connection and starts a new one, so
  **each proxy on the path sees the whole session in clear**. On public
  IPv4 port 22 that is the upstream gateway's proxy and then the gateway's.
- Behind the gateway's proxy, the hop to a board runs on that board's own
  VLAN, where the gateway is the only other device. The proxy pins the
  fleet key there, which catches a wrong address, not an attacker.

### Names and DNS records

| Name | Records | Port 22 is answered by | SSHFP |
|---|---|---|---|
| `<site>` | A: the public IPv4 address. AAAA: the gateway. Both exist today | IPv4: the upstream gateway's proxy. IPv6: the gateway's proxy. Both present the site proxy key | `SSHFP 4 2 <SHA-256 of the site proxy key>` |
| `ipv6.<board-name>.<site>`, one per access port | AAAA only: the board's global address | The board | `SSHFP 4 2 <SHA-256 of the fleet key>` |
| `gw.<site>` | A and AAAA of its own, the same addresses as `<site>`. Not a CNAME | As `<site>`; documented only with `-p 2223` and `-p 2224`, for operators | none |

- `<board-name>.<site>` does not exist. The private A records under those
  names are **removed**: a visitor's client that resolves a private address
  connects to whatever has that address on the visitor's own network and
  offers it the published password.
- There are no `ssh.`, `proxy.`, `ipv4.` or `private-ipv4.` names and no
  internal DNS view.
- `gw.<site>` stops being a CNAME because a lookup through a CNAME finds the
  SSHFP records of `<site>`, and ports 2223 and 2224 do not both answer with
  that key. With records of its own and no SSHFP, a client that has
  `VerifyHostKeyDNS` on sees no mismatch on the backup ports.
- `known_hosts` stores a non-default port as `[gw.<site>]:2223`, apart from
  the port 22 entry, so the keys on the three ports never meet.
- Any other name that is a CNAME to `<site>` (a second web site at the same
  address) answers ssh exactly as `<site>` does.
- The zone is signed, which is what makes SSHFP worth publishing. A client
  uses it only with `VerifyHostKeyDNS yes` and a validating resolver.

**Generated, not hand-written.** A converge writes the record set as a zone
fragment on the gateway: names and addresses from `switches |
port_vlan_map`, the fleet fingerprint from the NFS root's
`ssh_host_ed25519_key.pub`, the proxy fingerprint from the installed public
half of the site proxy key. The verify play compares public DNS with the
fragment and fails on any difference, including a private A record or a
`gw.<site>` CNAME that is still there. Who loads the fragment into the zone
is E8 and F4.

### Ports and listeners

On the gateway's uplink, for IPv4 (the transit address) and IPv6 (each of
the gateway's own global addresses; `<site>`'s AAAA records may name more
than one) alike:

| Port on the uplink | Answered by | Key | How |
|---|---|---|---|
| 22 | The gateway's proxy | site proxy key | A firewall rewrite of port 22 to 2224, on the uplink interface only, switched on by `ssh_proxy_takes_port_22` |
| 2223 | The gateway's own sshd | the gateway's sshd keys | sshd listens on 2223 as well as on 22; the firewall admits 2223 on the uplink only |
| 2224 | The gateway's proxy | site proxy key | sshpiperd listens here |

Seen from outside:

| Public address and port | Leads to |
|---|---|
| IPv4 22 | The upstream gateway's proxy. Board names go to the gateway's port 2224; every other name goes to the upstream gateway's own sshd |
| IPv4 2222 | The upstream gateway's own sshd |
| IPv4 2223 | Forwarded to the gateway's port 2223: the gateway's own sshd |
| IPv4 2224 | Forwarded to the gateway's port 2224: the gateway's proxy |
| IPv6 22 | The gateway's proxy |
| IPv6 2223 | The gateway's own sshd |
| IPv6 2224 | The gateway's proxy |

Every forward on the upstream gateway is to the same port number on the
transit address. Ports 2222 to 2224 are backup and operator routes. Visitors
are never told about them (D14).

A client inside the site resolves `<site>` to the transit address
(docs, "Clients inside the site") and finds the proxy on port 22 there, with
the same key as outside (E3, D3).

### The gateway role `ssh_proxy` (#187)

**Package.** `sshpiper`, from the fpgas.online apt repository, built from
the mirror repository fpgas-online/sshpiper (D5, fpgas-online/apt#21) with
the build tag `full`. It installs `/usr/bin/sshpiperd` and the plugins
`yaml` and `failtoban` in `/usr/lib/sshpiper/`. The unit, user and
configuration belong to the role, not the package.

**Variables.**

| Variable | Default | Meaning |
|---|---|---|
| `ssh_proxy_enabled` | `false` | Nothing is installed or opened while false |
| `ssh_proxy_host_key` | `{{ vault_ssh_proxy_host_key }}` | The site proxy key's private half. The role fails if it is empty |
| `ssh_proxy_host_key_path` | `/etc/sshpiper/ssh_host_ed25519_key` | Where it is installed: owner `sshpiper`, mode 0600 |
| `ssh_proxy_listen_address` | `::` | Wildcard; one dual-stack socket |
| `ssh_proxy_listen_port` | `2224` | |
| `ssh_proxy_takes_port_22` | `false` | The firewall rewrite of port 22 on the uplink. Off until the operators' and Ansible's route has moved to 2223 ([Rollout](#rollout-and-removal-of-the-old-path)) |
| `ssh_proxy_upstream_proxy_addresses` | `[]` | Addresses the upstream gateway's proxy connects from. Given to `failtoban --ignore-ip` |
| `ssh_proxy_max_failures`, `ssh_proxy_ban_duration` | `20`, `5m` | failtoban |
| `ssh_direct_ipv6` | `true` | The forward rule and the `ipv6.` records of the direct path (D15, F3) |
| `ssh_proxy_gateway_key_logins` | `false` | Key logins for gateway accounts through the proxy (F1) |
| `ssh_proxy_gateway_accounts` | the operator accounts and the jump account | The accounts that get them. Never the automation account, never root |

**The service.** User `sshpiper`, unprivileged, with systemd hardening
(`ProtectSystem=strict`, `NoNewPrivileges=yes`), `After=network-online.target`,
`Restart=on-failure`:

```
/usr/bin/sshpiperd \
  --address :: --port 2224 \
  --server-key /etc/sshpiper/ssh_host_ed25519_key \
  --drop-hostkeys-message \
  /usr/lib/sshpiper/yaml --config /etc/sshpiper/pipes.yaml \
  -- \
  /usr/lib/sshpiper/failtoban --max-failures 20 --ban-duration 5m \
      --ignore-ip <each of ssh_proxy_upstream_proxy_addresses>
```

- `--server-key` is always given. The default is the gateway's own sshd key.
  `--server-key-generate-mode` stays at its default, `disable`.
- `--drop-hostkeys-message` is required (see the findings table).
- The role fails if the proxy's public key equals the fleet key or one of
  the gateway's sshd keys.
- `pipes.yaml` and the key files are mode 0600; the yaml plugin refuses
  anything more open.
- Go opens a dual-stack socket for `::` on Linux unless `bindv6only` is
  set. The verify play checks that the port answers on both families.

**How board names are known.** The pipes are generated from `switches |
port_vlan_map`, the same map that makes the VLANs, the addresses and the
DHCP entries. One pipe pair per access port, empty ports included. This is
chosen over the fleet registry because a board name is a port's name and its
address follows from the port by formula: no board has to be registered, or
even present, for its name to route; nothing is configured per board; and a
login does not depend on the registry being up. A login to an empty port
times out at the proxy.

**Routing rules**, in this order in `pipes.yaml`:

```yaml
version: "1.0"
pipes:
  # 1. Per access port: password logins, relayed as typed.
  - from:
      - username: "pi-sw2-p47"
    to:
      host: "10.21.2.47:22"
      username: "pi"
      known_hosts_data:
        - "<base64 of: 10.21.2.47 ssh-ed25519 AAAA... (the fleet key)>"
  # 2. Per access port: key logins by the keys the boards trust for pi.
  - from:
      - username: "pi-sw2-p47"
        authorized_keys: "/etc/sshpiper/board_authorized_keys"
    to:
      host: "10.21.2.47:22"
      username: "pi"
      private_key: "/etc/sshpiper/board_mapping_key"
      known_hosts_data:
        - "<the same line>"
  # 3. Per gateway account, only if ssh_proxy_gateway_key_logins (F1).
  - from:
      - username: "<account>"
        authorized_keys: "/etc/sshpiper/gateway_authorized_keys/<account>"
    to:
      host: "127.0.0.1:22"
      private_key: "/etc/sshpiper/gateway_mapping_key"
      known_hosts_data:
        - "<base64 of: 127.0.0.1 <each of the gateway's sshd host keys>>"
```

- A board name reaches that board's sshd as user `pi`.
- There is no catch-all pipe. Any other name is refused by the proxy after
  the password prompt, and no connection is made to anything (E1).
- `pi@<site>` is therefore refused on the gateway's proxy. The board pages
  say that the login name is the board name.
- Board host keys are always pinned. With a stale pin every proxied login
  fails, which is why the verify play logs in end to end.

**How the proxy authenticates onward.**

- **Passwords are relayed.** With the published `pi` password this is the
  visitor's path, and it works through both proxies.
- **Public keys cannot be relayed** (findings table). For a board name the
  gateway's proxy checks the client's key against the keys the boards
  already trust for `pi`, then logs in with its board mapping key, whose
  public half fixpi adds to `pi`'s `authorized_keys` on the boards. A
  visitor's own key cannot work through the proxy; it works on the direct
  IPv6 path once the visitor has added it on the board.
- **An admin and a non-board name.** In phase 1 the gateway's proxy refuses
  it (E1). **The admin does not get in through port 22 of the gateway.**
  The admin's route is `ssh -p 2223 <account>@gw.<site>`, which reaches the
  gateway's sshd directly, on IPv4 and IPv6, with the admin's key checked
  end to end.

  **OPEN POINT O1.** The owner's words take it as given that the proxy also
  carries admins to the gateway ("As the proxy is doing both … and
  XXX<-A2->proxy<-B2->gateway"). Phase 1 does not build that, for two
  measured reasons: relaying non-board names to the gateway's key-only sshd
  lets anyone exhaust that sshd's login slots (findings table), and a
  public-key login cannot be relayed at all, so the proxy would have to
  hold a key that logs in to the gateway's accounts
  ([Future work](#future-work)). Until the owner decides otherwise, admins
  use port 2223.

  A consequence to know: over IPv4, public port 22 is the upstream
  gateway's proxy, and non-board names there go to the upstream gateway's
  own sshd (a reading of D1, E15). Over IPv6 the same command is refused by
  the gateway's proxy. Both present the same host key, so no warning marks
  the difference. `ssh -p 2223 <account>@gw.<site>` reaches the gateway's
  sshd on both families.

- **failtoban.** The password is public, so limiting guesses protects no
  secret. It limits noise and keeps a board's sshd responsive. Logins that
  arrive through the upstream gateway's proxy all come from its address,
  which is ignored; limiting those is the upstream proxy's job. A client
  offers the keys in its agent before the password; whether each rejected
  key counts as a failure is not verified, which is why the limits are
  explicit and gentle. The user documentation carries: "If you see 'Too
  many authentication failures', use `ssh -o PubkeyAuthentication=no
  <board-name>@<site>`."

**Who holds what.** The proxy holds the board mapping key, and `pi` has
NOPASSWD sudo, so whoever controls the proxy has root on every board. That
is no more than every visitor has. With F1 switched on it also holds the
gateway mapping key.

### The required package patch

sshpiperd leaves an onward connection open after its login failed (findings
table). Our package carries a patch, on the packaging branch of
fpgas-online/sshpiper and nowhere else, so that **a failed onward
authentication closes the upstream connection** (`authUpstream` in the
sshpiper.crypto fork is where it is never closed).

- The patch is required for phase 1, not an improvement. Without it a
  handful of wrong passwords for a board name locks the web terminal and
  the jump route out of that board, and the patch is the only fix: the
  proxy's own limits count per client address, the leak is per target.
- The VM test asserts it: after N failed proxied logins to a board (N above
  the sshd's `MaxStartups` start value), the board's sshd shows no
  lingering unauthenticated sessions, and a correct login to that board
  made at once succeeds.
- **Until a package with the patch is published, the proxy is not exposed
  on a public port** (E16): `ssh_proxy_enabled` is set only in the VM test
  inventory, and no production site sets it
  ([Rollout](#rollout-and-removal-of-the-old-path)).

### The gateway's own sshd on port 2223 (`roles/sshd`)

`roles/sshd` owns the gateway's sshd configuration, and today it sets no
`Port` or `ListenAddress`. It gains a drop-in, written when
`sshd_backup_port` is defined (2223 where `ssh_proxy_enabled`):

```
ListenAddress 0.0.0.0:22
ListenAddress [::]:22
ListenAddress {{ eth_uplink_static_address }}:{{ sshd_backup_port }}
ListenAddress [::]:{{ sshd_backup_port }}
```

- Port 22 stays on every address, exactly as today: boards, the web tier
  and anything on the gateway itself are not affected.
- Over IPv4, port 2223 exists only on the transit address. That line is
  what the upstream gateway's same-port forward of 2223 reaches.
- Over IPv6, port 2223 is on the wildcard address, because the gateway has
  more than one global address and a client may arrive on any of them. The
  firewall admits it on the uplink interface only. It is the operators' and
  Ansible's route once port 22 on the uplink is the proxy.
- Once one `ListenAddress` is given, sshd listens only on those listed,
  which is why the two port 22 lines are written out.
- **To prove in the VM test:** sshd may start before the transit address is
  configured. After a reboot of the gateway the 2223 listeners must be
  there. If the IPv4 one is not, that line becomes `0.0.0.0:2223` and the
  firewall alone limits it to the uplink.
- Nothing else changes in the gateway's sshd. The proxy never connects to
  it (E1), so it needs no penalty exemption.
- The role's lockout guard and `sshd -t` run before the reload, as for the
  key-only drop-in.

### Firewall (`roles/firewall`, #187 and #188)

All in the per-port branch of `nftables.conf.j2`.

Input:

- `iifname {{ eth_uplink }} tcp dport { 2223, 2224 } accept`, and a drop of
  those two ports on every other interface, placed before
  `jump internal_networks`. Without the drop a board would reach the proxy:
  `internal_networks` accepts everything from the board network.
- The existing `tcp dport ssh accept` stays: boards and the site's services
  reach the gateway's sshd on port 22 as today.

NAT, when `ssh_proxy_takes_port_22`:

- `iifname {{ eth_uplink }} fib daddr type local tcp dport 22 redirect to
  :2224`, in the existing `ip nat` prerouting chain and in an `ip6 nat`
  prerouting chain. `fib daddr type local` limits it to the gateway's own
  addresses, so connections forwarded to a board's port 22 (the direct IPv6
  path) are not rewritten. The proxy listens on the wildcard address, so
  whichever local address `redirect` picks is answered. This is argued from
  netfilter semantics and is proved in the VM test.
- Only connections arriving on the uplink are rewritten. Port 22 from a
  board or from the gateway itself is still the sshd.
- From then on nothing arriving on the uplink reaches the sshd on port 22.
  It is on 2223.

Forward:

- **The direct IPv6 path (D15)**, when `ssh_direct_ipv6` (default `true`
  where `ssh_proxy_enabled`): `iifname {{ eth_uplink }} oifname "v*"
  ip6 daddr { <board addresses from port_vlan_map> } tcp dport 22 accept`.
  This exposes each board's sshd, with the published password, to the IPv6
  internet. The per-board DNAT gives the same exposure over IPv4.
- **Boards going out and back in (E4):** `iifname "v*" ip daddr { <the
  site's public IPv4 address> } tcp dport { 22, 2222, 2223, 2224 } drop`
  before the rule that accepts `v*` to the uplink. The address is site data
  in the inventory.
- `v*` to `v*` stays dropped. Nothing is opened from the site's LAN to the
  boards' IPv4 addresses (D3).

The per-board DNAT rules are not touched in phase 1. Their removal is in
[Rollout](#rollout-and-removal-of-the-old-path).

### Boards (`roles/fixpi`, #186)

- **Ed25519 only.** An sshd drop-in sets
  `HostKey /etc/ssh/ssh_host_ed25519_key`. There is then one fleet
  fingerprint to publish, one SSHFP record per `ipv6.` name and one pin per
  board in the proxy. The RSA and ECDSA files stay on disk, unused.
- **`PerSourcePenaltyExemptList {{ pib_network }}.0.1`** in the same
  drop-in: the gateway's board-side address. Otherwise a few wrong
  passwords through the proxy lock the web terminal, the upload page, the
  jump route and every other proxied visitor out of that board.
- **The board mapping key's public half** in `pi`'s `authorized_keys`, as a
  new key source `ssh_proxy`, for `pi` only, never root.
- **The login banner, the `pi` account and its password stay as today.**
- **No login aliases** (E7).
- **The direct path** needs nothing else on the board: its sshd already
  listens on its global IPv6 address, and `ssh pi@ipv6.<board-name>.<site>`
  is an ordinary login. The visitor's own key works there once added to
  `~pi/.ssh/authorized_keys`, until the board resets.
- The first converge changes files in the shared root and so reboots the
  fleet. A later change to `switches` does not, because nothing on the
  boards follows the port map any more.
- The web terminal's host-key policy is checked in fpgas.online-site before
  boards go Ed25519-only.

**Fleet key rotation (E6).** After the root's key changes, booted boards
keep the old key until their reboot. For the length of the reboot wave the
proxy pins both keys and the `ipv6.` names carry both SSHFP records. Proxied
visitors notice nothing; direct visitors see the key change once. The site
proxy key is not involved.

### What the board pages print (fpgas-online/fpgas.online-site#44)

fpgas.online-gw serves `/api/boards`; its `ssh` object becomes:

```json
{"user": "pi-sw2-p47", "host": "welland.fpgas.online",
 "direct_user": "pi", "direct_host": "ipv6.pi-sw2-p47.welland.fpgas.online",
 "host_key_fingerprint": "SHA256:<site proxy key>",
 "direct_host_key_fingerprint": "SHA256:<fleet key>",
 "legacy_port": <per-board port>}
```

The page prints, in this order:

```
ssh pi-sw2-p47@welland.fpgas.online
scp FILE pi-sw2-p47@welland.fpgas.online:Uploads/
```

with the password as today, the line "The login name is the board's name,
not `pi`", and the site proxy key's fingerprint. Then, under "Directly over
IPv6":

```
ssh pi@ipv6.pi-sw2-p47.welland.fpgas.online
```

with the fleet key's fingerprint and the line "This key is shared by every
board and is public." No line on the page contains `-p`, once the legacy
line is dropped ([Rollout](#rollout-and-removal-of-the-old-path)). A site
without `ssh_proxy_enabled` keeps today's page.

### What the upstream gateway must provide

For a site behind an upstream gateway. These are requirements; how the
upstream gateway meets them is its operator's business. They belong on the
docs page "What a site needs from its upstream network"
(fpgas-online/fpgas.online-docs, `docs/setup/upstream-gateway.md`).

IPv4, under the decided host-key option (D16):

1. **Public tcp 22: a username-routing ssh proxy.**
   - It presents the **site proxy key** and no other host key. The site's
     operator installs the key from the fpgas.online vault.
   - It does not pass host-key announcements on to clients (for sshpiper,
     `--drop-hostkeys-message`).
   - A user name matching `^pi-sw[0-9]+-p[0-9]+$` is connected to the
     gateway's transit address, port 2224, with the same user name, relaying
     the password. It needs no list of boards.
   - On that onward connection it pins the site proxy key, which is what the
     gateway's proxy presents.
   - It accepts no public key for a board name, and lets a client that
     offers several keys reach the password prompt.
   - Every other user name goes to the upstream gateway's own sshd.
2. **Public tcp 2222:** the upstream gateway's own sshd.
3. **Public tcp 2223:** forwarded to the gateway's transit address, port
   2223, keeping the client's source address.
4. **Public tcp 2224:** forwarded to the gateway's transit address, port
   2224, keeping the client's source address.
5. **Inside the site:** `<site>` resolves to, or is routed to, the gateway's
   transit address, where ports 22, 2223 and 2224 answer as in
   [Ports and listeners](#ports-and-listeners).
6. Wanted, not required: its proxy limits repeated failed logins per client.

IPv6: the upstream gateway lets tcp 22, 2223 and 2224 reach the gateway's
global addresses, and tcp 22 reach the board prefix. Nothing is proxied or
forwarded.

The per-board ssh ports (`<s><pp>22`) are no longer asked for.

**What changes under the two options that were not chosen** is in
[Rejected alternatives](#rejected-alternatives). The gateway side is built
so that either is a change of inventory values.

### A site whose gateway is on a public address

There is no upstream gateway and no second proxy. With
`ssh_proxy_takes_port_22` the gateway's proxy answers public port 22 on
both families itself, the sshd is on 2223, and the site proxy key lives only
on the gateway. No site with `switches:` is built this way today.

### Rollout and removal of the old path

Each step leaves every existing route working until its replacement has
been verified. At a site where the per-board ports do not work from outside
(Welland, observed 2026-10-05), steps 1 to 6 take nothing away from
visitors.

1. Package published (apt#21), **with the required patch**, and the VM
   test's leak assertion green. Until then `ssh_proxy_enabled` is set only
   in the VM test inventory and no later step is taken at any site (E16).
2. Gateway converge with `ssh_proxy_enabled: true`: proxy on 2224, sshd also
   on 2223, firewall rules, board changes (one fleet reboot). Verified from
   the transit side on port 2224.
3. Ansible's inventory and the operators' documented command move to port
   2223 (`ansible_port: 2223`; `docs/access.md`). Verified.
4. `ssh_proxy_takes_port_22: true`. Port 22 on the uplink is now the proxy.
   `ssh <board-name>@<site>` works over IPv6 from outside.
5. The upstream gateway: forwards of 2223 and 2224; its own sshd also on
   2222; then the site proxy key and the proxy on public port 22.
   `ssh <board-name>@<site>` works over IPv4 from outside.
6. DNS: SSHFP on `<site>`, the `ipv6.` names, `gw.<site>` as records of its
   own; the private `<board-name>.<site>` A records are removed in the same
   change. They never gave a visitor a working command.
7. Both commands verified from outside the site, on IPv4 and on IPv6.
8. The board pages print the new commands first, with the `-p` line
   underneath only at a site where it works from outside.
9. After a period agreed for that site, the `-p` line leaves the page, and
   then the per-board `<s><pp>22` DNAT rules leave the firewall and the
   upstream requirements. The `<s><pp>44` rules are not part of this.

### Monitoring

The proxy runs under systemd with `Restart=on-failure`. `verify-server.yml`
checks that it listens and that a login through it reaches a board. The
public IPv4 path also depends on the upstream gateway's proxy, which only a
check from outside the site can see.

## Phase 2: transparent IPv4 source (#190)

The goal is that on a proxied IPv4 connection the board sees the client's
address. Phase 1 works without any of this.

The mechanism is Linux transparent proxying
([tproxy.rst](https://docs.kernel.org/networking/tproxy.html)).

1. **The proxy dials the board from the client's address.** Its board-side
   socket sets `IP_TRANSPARENT` and binds to the client's address with port
   0 before connecting. sshpiper dials with a bare `net.Dial(network, addr)`
   (`cmd/sshpiperd/internal/plugin/grpc.go:418` at commit `2038d993`). Our
   package carries a small patch there, behind an opt-in flag: a
   `net.Dialer` with `LocalAddr` set and a `Control` hook that sets
   `IP_TRANSPARENT`. The patch lives in our packaging branch only. The
   service gains `CAP_NET_RAW` and nothing else.
2. **The board's replies come back to the proxy.** In `nftables.conf.j2`:

   ```
   table inet tproxy {
     chain prerouting {
       type filter hook prerouting priority mangle;
       iifname "v*" tcp sport 22 socket transparent 1 meta mark set 0x1 accept
     }
   }
   ```

   and a policy route owned by systemd-networkd: a `.network` for `lo` with
   `[RoutingPolicyRule] FirewallMark=1 Table=100 Family=ipv4` and
   `[Route] Type=local Destination=0.0.0.0/0 Table=100`. Only packets that
   belong to a transparent socket are diverted.
3. Nothing changes on the boards.

What it covers:

- Connections that reach the gateway's proxy with the client's own IPv4
  address: public port 2224, and port 22 at a site on a public address.
- Not public IPv4 port 22 behind an upstream gateway: there the gateway's
  proxy sees the upstream proxy's address. The client's address would have
  to arrive in a PROXY header, which sshpiper accepts
  (`--allowed-proxy-addresses`) and never sends. This is open for phase 2
  and is the upstream gateway's matter.
- Not IPv6: the onward hop is IPv4.

Failure mode to test: without the policy route the replies are forwarded
out of the uplink and every proxied login times out. The verify play checks
the rule, the route and a login whose `$SSH_CONNECTION` on the board shows
a non-gateway address.

## Rejected alternatives

**The key-types trick** (chosen by Tim on 2026-10-04 on condition that it be
proved with real clients, then disproved). One proxy offers only an ECDSA
host key and the other only Ed25519, each its own, with host-key
announcements off, in the hope that OpenSSH keeps both under one name.
It does not. In the lab (PR #214,
[README](../../../tests/lab/ssh_key_types/README.md),
[results](../../../tests/lab/ssh_key_types/results-2026-10-04.md)), with
two real sshpiper proxies under one name, OpenSSH 8.2, 8.9, 9.2, 9.6, 10.0
and 10.5 all accept the first proxy's key and refuse the second with
"REMOTE HOST IDENTIFICATION HAS CHANGED", in either order, with
`StrictHostKeyChecking` at `ask` and at `accept-new` (scenarios 1a to 1d).
`ssh-keygen -R` only moves the refusal to the other proxy (1g). Only
dropbear's client and PuTTY's plink keep one key per type (5a, 5d).

**No ssh proxy on the upstream gateway.** The upstream gateway forwards
public tcp 22 straight to the gateway's proxy. One key, held only on the
gateway, and the client's address survives. Rejected because it changes D1:
the upstream gateway's own sshd leaves public port 22 for good, and every
non-board name on IPv4 reaches the fpgas.online gateway. What it would
change:

| Where | Change |
|---|---|
| Gateway role | None in code. `ssh_proxy_upstream_proxy_addresses` becomes empty |
| Upstream gateway | Item 1 of the list becomes "public tcp 22 forwarded to the gateway's transit address, port 22, keeping the client's source address". It holds no key |
| DNS | None |
| Board page | None |

**Two different keys, both published.** Each proxy keeps a key of its own,
and the board page prints both as `known_hosts` lines for visitors to paste
before their first visit. Rejected because an OpenSSH visitor who does not
paste them gets the changed-key refusal on the other address family (lab
scenario 6l; with both pasted, 6a and 6b are silent), and visitors are meant
to need no setup. What it would change:

| Where | Change |
|---|---|
| Gateway role | `ssh_proxy_host_key` holds a key used only on the gateway. Nothing else |
| Upstream gateway | Its proxy presents a key of its own, of the other type, and pins the gateway's |
| DNS | Two SSHFP records on `<site>`, one per key |
| Board page | Two `known_hosts` lines and the instruction to paste them |

**The fleet key on the proxy.** Rejected by D2.

**A catch-all pipe: every non-board name relayed to the gateway's own
sshd.** This was in the previous revision of this document. Rejected for
phase 1 on measurement: sshpiperd leaves each failed onward connection open,
so 18 wrong passwords for `root` through the proxy took the gateway's sshd
to its `MaxStartups` limit and it dropped 5 of 20 operator key logins on
port 2223 (reviewer's lab, 2026-10-05). The sshd is key-only, so nothing
relayed by password could ever log in anyway.

**`ssh.<site>` for IPv4 and `proxy.<site>` for IPv6**, each with its own
key. Overruled by D14: one command on both address families.

**Login aliases on the boards** (`<board-name>` as an account). Not needed
once the direct path logs in as `pi` (E7); they made every change to
`switches` reboot the fleet.

**A host certificate on both proxies**, signed by one authority. Needs a
`@cert-authority` line in every visitor's `known_hosts`. Not tested.

## Findings for the owner

| # | Finding | What is built meanwhile |
|---|---|---|
| F1 | Admin key logins through the proxy make the proxy a holder of gateway credentials ([above](#the-gateway-role-ssh_proxy-187)) | The mapping is built behind `ssh_proxy_gateway_key_logins`, default off. Admins use `-p 2223` |
| F2 | `ssh <account>@<site>` for a non-board name reaches the upstream gateway's sshd over IPv4 and the gateway's sshd over IPv6, silently | Documented; the admin command is `ssh -p 2223 <account>@gw.<site>` |
| F3 | D15 rests on a sentence Tim wrote as a question ("Maybe … ?"), and the reading drew no comment when shown to him. It opens every board's sshd to the IPv6 internet under the public fleet key | Built as D15. The forward rule and the `ipv6.` records are one variable (`ssh_direct_ipv6`) and can be left off |
| F4 | How the generated records get into the public zone was never asked. The zone is the owner's and hosted outside the site | The fragment and the comparing verify step (E8). Someone with access to the zone loads it |
| F5 | The docs page says the upstream is never asked to hold an fpgas.online credential. D16 makes it hold the site proxy key | The docs page is changed with this design (fpgas-online/fpgas.online-docs#16) |

## Verification

In the CI VM and on one board, before phase 1 is switched on at a site. The
VM has no upstream gateway: every step goes to the gateway's uplink
addresses.

1. **The login, with a real ssh client.**
   - `ssh <board-name>@<gateway>` on port 2224 with the password lands on
     that board: `hostname` on the far side prints `<board-name>`, and
     `$SSH_CONNECTION` shows the board's address.
   - The same with `ssh_proxy_takes_port_22`, on port 22, over IPv4 and
     over IPv6.
   - A key the boards trust for `pi` logs in to a board name through the
     proxy. A key they do not trust is refused and the password still works.
   - A name that is not a board name (`root`, `pi`, an operator's account)
     is refused by the proxy after the password prompt, and the gateway's
     own sshd logs no connection from the proxy.
   - **The leak assertion:** after N failed proxied logins to one board (N
     above the sshd's `MaxStartups` start value), that board's sshd shows no
     lingering unauthenticated sessions, and a correct login to it made at
     once succeeds.
   - scp, sftp and `ssh -J` work through the proxy.
2. **Keys.**
   - `ssh-keyscan` of ports 22 and 2224 on the uplink, both families,
     returns exactly one key, the site proxy key. Port 2223 returns the
     gateway's sshd keys. A board returns exactly one key, the fleet key.
   - A proxied login leaves `known_hosts` byte for byte unchanged.
   - With a default client, logins to several board names through `<site>`
     prompt once.
   - The role refuses to converge without the key, and with one equal to
     the fleet key or to a gateway sshd key.
3. **The sshd on 2223.** Both listeners are present after a reboot. Port
   2223 does not answer on a board-side address. Ansible converges through
   it.
4. **Limits.** After several failed proxied logins to one board, the web
   terminal, the jump route and a correct proxied login to it still work.
   failtoban blocks a client that keeps failing, not an ignored address. A
   client with ten unrelated keys in its agent reaches the password prompt
   and is not banned.
5. **Nothing else opened.** A board cannot reach port 2223 or 2224 on any
   gateway address, cannot reach another board's port 22 by any route, and
   cannot open the site's public address on 22, 2222, 2223 or 2224. A host
   on the uplink cannot reach a board's IPv4 address except through the
   legacy DNAT ports.
6. **The direct path.** From the uplink side over IPv6, `ssh pi@<board's
   global address>` logs in and presents the fleet key.
7. **DNS.** The fragment has an SSHFP record on `<site>` for the proxy key,
   an AAAA and an SSHFP record for each `ipv6.` name, no A record for any
   board name and no SSHFP on `gw.<site>`. Against a signed test zone with
   `VerifyHostKeyDNS yes`, a first login shows no prompt.

From outside the site, once the upstream gateway has done its part:

- `ssh <board-name>@<site>` reaches the board over IPv4 and over IPv6, and
  a default client that has accepted the key on one family is silent on the
  other.
- `ssh pi@ipv6.<board-name>.<site>` reaches the board.
- `-p 2223` and `-p 2224` on `gw.<site>` present the gateway's sshd keys
  and the site proxy key, on both families.
- The same commands from inside the site present the same keys.
- Public DNS matches the fragment.

Before phase 2 is switched on: `$SSH_CONNECTION` on the board for a login
through port 2224 shows the client's address; the legacy DNAT path, NFS and
the boards' outbound traffic still work; removing the policy route makes
the verify play fail.

## Work items

Tracking: #191. The issue texts predate this revision; this document is
what to build from.

| Phase | Issue | What | What its text gets wrong now |
|---|---|---|---|
| 1 | fpgas-online/apt#21 | Package `sshpiper` from fpgas-online/sshpiper: `sshpiperd`, `yaml`, `failtoban`, built with the tag `full` | It omits `failtoban` and the required phase 1 patch (a failed onward authentication closes the upstream connection), and says to offer the phase 2 patch to the sshpiper project. Both patches stay in our packaging branch |
| 1 | #187 | Gateway role `ssh_proxy`; `roles/sshd` port 2223; the input and NAT rules | It copies the fleet key to the proxy and binds the uplink address only. Its "unknown usernames are not forwarded anywhere" stands (E1) |
| 1 | #186 | Boards: Ed25519-only host key, penalty exemption, board mapping key | Login aliases are dropped (E7). "The same key the proxy presents" is wrong |
| 1 | #188 | Firewall: direct IPv6 to boards; boards cannot reach the public ssh ports | The internal IPv4 rule is dropped (D3) |
| 1 | #189 | DNS: the generated fragment and the comparing verify step | Its record set (per-board A records, `ipv4.`, `private-ipv4.`, an internal view) is replaced by [Names and DNS records](#names-and-dns-records) |
| 1 | fpgas-online/fpgas.online-gw#2 | `/api/boards` `ssh` object | Fields as in [What the board pages print](#what-the-board-pages-print-fpgas-onlinefpgasonline-site44) |
| 1 | fpgas-online/fpgas.online-site#44 | Board pages | Its command, `ssh <board-name>@<board-name>.<site>`, is replaced |
| 1 | fpgas-online/fpgas.online-docs#16 | User documentation, and the upstream-network page | See F5 |
| 1 | — | `docs/access.md`: port 2223 as the operators' and Ansible's route | New |
| 1 | — | The upstream gateway: the list above. Outside these repositories | New |
| 2 | #190 | Transparent IPv4 source | Covers port 2224 and public-address sites; port 22 behind an upstream gateway is open |
