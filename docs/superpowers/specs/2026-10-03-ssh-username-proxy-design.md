# Design: ssh to boards by name, through a username-routing ssh proxy

Date: 2026-10-03, revised 2026-10-04 and 2026-10-05
Status: decided, not yet built. Nothing here is deployed. The owner's
decisions are in [Decisions](#decisions), with the engineering decisions
and readings kept apart from his words. Four points are open with the
owner (O1 to O4, [Open points for the owner](#open-points-for-the-owner)).
For each, the document says what phase 1 builds while he is asked, so none
of them blocks building. The work is tracked by #191 and the issues under
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
board page prints. The second is also open (D17), and the page prints it
only to a visitor who reached the website over IPv6.

## Decisions

Tim's words are quoted from the question record; the answers of 2026-10-04
are also copied on #191. Where a quote named a machine, the name is replaced
by its role in square brackets, and a bracket after a phrase says what the
phrase refers to. The middle column says only what his words say; what
engineering added to make them buildable is in the second table.

The numbers are not consecutive: D4 and D6 to D12 belonged to earlier
revisions of this document and are not reused.

| # | Decision | Source |
|---|---|---|
| D1 | The upstream gateway gets a username-routing ssh proxy of its own. It forwards "piXXX" users to the gateway. Backup ports: 2222 is the upstream gateway's own sshd, 2223 the gateway's own sshd, 2224 the gateway's proxy. Readings added by engineering: E15 | Tim, 2026-10-04: "The site's gateway [the upstream gateway] should get it's own proxy which works in a somewhat similar manner to the proxy on the fpgas.online gateway. piXXX users will be forwarded the fpgas.online gateway. As a backup, port 2222 should go to [the upstream gateway]'s ssh server and port 2223 should forwarded directly to [the fpgas.online gateway's] ssh server and port 2224 should forward directly to [the fpgas.online gateway's] ssh proxy." |
| D2 | The fleet key is not what protects traffic between a client and a proxy. The proxy has a host key that is not public | Tim, 2026-10-04: "Unclear, the ssh host key is on the rpi and available to anyone who logs into that system, so should *not* be what encrypts traffic to [the fpgas.online gateway]" |
| D3 | Devices on the site's own LAN reach everything through the gateway, like internet devices. A board talks only to the gateway | Tim, 2026-10-04: "Other devices behind [the upstream gateway] should access all welland.fpgas.online resources via gw.welland.fpgas.online and look pretty much like any internet device except with a private IP addresses. Devices inside XXX.welland.fpgas.online should not be accessing other XXX.welland.fpgas.online devices, they should only be talking to gw.welland.fpgas.online -- IE really pi1.welland.fgpas.online<->gw.welland.fgpas.online should be considered a direct point to point link." |
| D5 | sshpiper is packaged from the mirror repository fpgas-online/sshpiper | Tim, 2026-10-04: "Mirror repo fpgas-online/sshpiper; I will create it". The repository exists since 2026-10-04 |
| D13 | Visitor-to-board data is treated as public. Admin-to-gateway data must be encrypted. The client-to-proxy hop therefore uses a key that is not public; the hop behind the proxy need not be protected. His sentence takes it as a premise that the proxy carries admins to the gateway; it does not decide that, and phase 1 does not build it (O1) | Tim, 2026-10-04: "Data from visitor<->pi should be treated as effectively unencrypted (everyone on the planet has root on the pi and could just intercept it there). Data from admin<->gateway needs to be encrypted. As the proxy is doing both XXX<-A1->proxy<-B1->pi and XXX<-A2->proxy<-B2->gateway and we can't tell if XXX is a visitor or an admin before setting up A1 or A2, they both needs to be encrypted with a key that is not publically available. Both B1 and B2 being unecrypted is also fine as B2 is a trusted step." |
| D14 | Visitors use `ssh <board-name>@<site>`, the same on IPv4 and IPv6. They are never told to use `-p` | Tim, 2026-10-04: "We want people to always use something like `ssh pi-sw2-p47@welland.fpgas.online` without having to understand the complexity of a shared single ipv4." and "We don't want people to ever have to use `-p 10722` as that does not work with sshfp." |
| D17 | The direct path `ssh pi@ipv6.<board-name>.<site>` is built and open. Reading added by engineering, E17: the board page shows the direct command only to a visitor who reached the website over IPv6 | Tim, 2026-10-05, asked whether to build and open the direct IPv6 path: "Build it, open it, the website shows the extra direct IPv6 is coming from an IPv6 source." |
| D15 | **Not a decision: a reading of a sentence he wrote as a question** (O2). Read as: there are no `ipv4.` names and no plain `<board-name>.<site>` names, so the private A records under the plain names are removed. (The first half of the sentence, the `ipv6.` name, is now decided: D17) | Tim, 2026-10-04, as a question: "Maybe `ssh pi@ipv6.pi-sw2-p47.welland.fpgas.online` is equivalent to `ssh pi-sw2-p47@welland.fpgas.online` and `ssh pi@ipv4.pi-sw2-p47.welland.fpgas.online` and `ssh pi@pi-sw2-p47.welland.fpgas.online` don't work?" This reading was shown to him twice afterwards and drew no comment, so it is not confirmed |
| D16 | **One site proxy key, shared by both proxies.** Not the fleet key and not either machine's own sshd key. It is installed on the gateway's proxy and on the upstream gateway's proxy, so every client sees one key for `<site>` on IPv4 and IPv6, with one SSHFP record set | Tim, 2026-10-05, choosing "One shared host key on both proxies". The accepted text: "I generate one proxy host key for the site (not the fleet key, not either machine's own sshd key); it is installed on the fpgas.online gateway's proxy and on the upstream gateway's proxy; every client sees one key for the name on IPv4 and IPv6, with one SSHFP record. Cost: that private key also lives on the upstream gateway, so the fpgas.online docs must say a site's upstream proxy holds the site's proxy key." This replaces his 2026-10-04 choice of the key-types trick, which the lab disproved ([Rejected alternatives](#rejected-alternatives)) |

Decided by engineering, open to the owner's veto:

| # | Decision |
|---|---|
| E1 | On the gateway's proxy a name that is not a board name is **refused by the proxy itself**. Nothing from the proxy reaches the gateway's own sshd in phase 1. Operators reach that sshd on port 2223. This is the default while the owner is asked (O1) |
| E2 | The login name is `<board-name>`, the board's host name `pi-sw<S>-p<P>`, and nothing else for now. Names from the fleet registry can be added later as further pipes |
| E3 | On the gateway's uplink, port 22 is the proxy, port 2223 the gateway's own sshd and port 2224 the proxy, on IPv4 and on IPv6 alike. A client inside the site, a client on IPv6 and the upstream gateway's forwards all find the same thing on the same port |
| E4 | A board cannot reach the site's own public ssh entry points, nor the proxy |
| E5 | Board names are password-only through the proxy, on every path and port. Phase 1 has no key pipes: the proxy holds no key for the boards and none for the gateway |
| E6 | While the fleet key rotates, the `ipv6.` names carry both fingerprints and the proxy pins both |
| E7 | Boards get no login aliases. The direct path logs in as `pi`, and the proxy rewrites the name, so nothing needs `<board-name>` to exist as an account on a board |
| E8 | The role writes the generated DNS records as a zone fragment. Loading it into the public zone is manual, by the zone's owner. The verify step that compares public DNS with the fragment has a switch of its own, `ssh_dns_verify`, default off, so verify is not red before the zone is loaded. Default while the owner is asked (O3) |
| E9 | failtoban runs with `--max-failures 20` and `--ban-duration 5m`, not its defaults of 5 and 60 minutes |
| E10 | `gw.<site>` stops being a CNAME to `<site>` and gets address records of its own, with no SSHFP |
| E11 | Boards offer only their Ed25519 host key |
| E12 | The first converge with the proxy enabled reboots the fleet once, because it changes sshd drop-ins in the shared root. It is a planned step of the rollout |
| E13 | The old `ssh -p <port> pi@<site>` line leaves the board pages in the same change that adds the new command, and the API carries no `legacy_port`. It never worked from outside at a site behind an upstream gateway, and D14 says visitors are never told `-p`. The per-board `<s><pp>22` DNAT rules are removed in the next converge. Default while the owner is asked (O4) |
| E14 | What `pi@<site>` means. On the gateway's proxy (IPv6, port 2224, inside the site): refused, like any non-board name. On public IPv4 port 22: whatever the upstream gateway's sshd does with the name `pi` (E15). It is never a board. The board pages say that the login name is the board's name |
| E15 | Readings of D1 that his words do not state: (a) on the upstream gateway's proxy, every name that is not a board name goes to the upstream gateway's own sshd; (b) "piXXX" means the names matching `^pi-sw[0-9]+-p[0-9]+$`; (c) the upstream gateway's proxy connects onward to the gateway's proxy on port 2224, the same port his "2224" backup forward reaches |
| E16 | The proxy is not exposed on any public port until our package carries the patch that closes a failed onward connection ([The required package patch](#the-required-package-patch)) |
| E17 | How D17 is built. `ssh_direct_ipv6` defaults to true at a site with a routed board prefix (`pib_network6_base` defined) and `ssh_proxy_enabled`. Reading of "the website shows the extra direct IPv6 is coming from an IPv6 source": the board page prints the direct command only when the visitor's own connection to the website arrived over IPv6; the proxy command is always printed |

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
first command under [What a visitor types](#what-a-visitor-types), and with
the second where the site has switched the direct path on. A default
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
| A key pipe logs in onward before the client has proved it holds the key. A client that offers an authorised **public** key with a non-matching private key is denied, but the proxy has already logged in to the target with its mapping key, and that authenticated onward session stays alive | Reviewer's test with the real sshpiperd, 2026-10-05: the board logged `Accepted publickey for pi`, and a gateway sshd behind a key pipe logged an accepted login. Operators' public keys are published (GitHub) | Anyone could open authenticated sessions on a board or the gateway without a key. No key pipes until our package fixes this (E5, [Future work](#future-work)) |
| The yaml and failtoban plugins are built only with the Go build tag `full` | `//go:build full` in `plugin/yaml`, `plugin/failtoban` | The package builds with that tag |
| failtoban counts per client address: a wrong password counts 2, any aborted connection counts 1, a default `ssh-keyscan` (which probes several key types) counts 5. Refused keys are not counted. The limit is a fixed window that starts at the client's first connection and lasts `--ban-duration`, not a ban from the last failure, and a successful login does not reset it. A client over the limit sees `Connection closed by … port 2224`. An `--ignore-ip` address is never limited | `plugin/failtoban/main.go`; reviewer's measurements with the real sshpiperd, 2026-10-05 | Explicit, gentler values (E9). The upstream proxy's address is ignored. Verification and the VM test use `ssh-keyscan -t ed25519`, and the test client must stay under the limit or be listed in `--ignore-ip` |
| A login to a board name whose port is empty is answered after about 3 seconds with `Permission denied`, exactly like a wrong password | Reviewer's test, 2026-10-05 | The board page says so |
| sshpiperd has no cap on connections before key exchange, and writes an ERROR line for each refused key | Reviewer's test, 2026-10-05 | A per-source limit on new connections in the firewall; `LimitNOFILE` and `TasksMax` in the unit; journald's rate limit bounds the log |
| After login sshpiper passes agent, X11 and port-forward channels through untouched. `ssh -W` through a board name worked | Reviewer's test, 2026-10-05 | A board is a machine every visitor has root on. The documentation says: never forward an agent to a board |
| sshd has no PROXY-protocol support. sshpiper accepts PROXY headers (`--allowed-proxy-addresses`) and never sends them | OpenSSH source; `cmd/sshpiperd/main.go` | A board sees the client's address on a proxied login only through transparent proxying (phase 2) |
| Since OpenSSH 9.8 sshd penalises a source address after failed logins (`PerSourcePenalties`, on by default). Before 9.8 the option `PerSourcePenaltyExemptList` does not exist: it is a configuration error and sshd does not start. The boards' root is Debian bookworm today (inventory `dist: bookworm`), OpenSSH 9.2 | `sshd_config(5)`; reviewer's test on OpenSSH 9.2, 2026-10-05: `sshd -t` exits 255 with the line present | On a root with OpenSSH 9.8 or later, every proxied login reaches a board from the gateway's address, as do the web terminal and the jump route, so the boards exempt that address. On an older root there is no penalty and the line must not be written |

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
| The fleet key (Ed25519) | The boards' shared root: readable by every visitor | Every board | Where the direct path is on: SSHFP on each `ipv6.<board-name>.<site>`, and the fingerprint on the board pages. Otherwise nowhere |
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
  this key alone. That is accepted (D13, D17): visitor-to-board data is
  treated as public.
- Each proxy ends the client's ssh connection and starts a new one, so
  **each proxy on the path sees the whole session in clear**. On public
  IPv4 port 22 that is the upstream gateway's proxy and then the gateway's.
- Behind the gateway's proxy, the hop to a board runs on that board's own
  VLAN, where the gateway is the only other device. The proxy pins the
  fleet key there, which catches a wrong address, not an attacker.

**What the direct IPv6 path exposes** (D17). Every board's sshd, with the
published `pi` password and NOPASSWD sudo, is reachable from the whole IPv6
internet on port 22.

- What protects a board is not its sshd: the board holds nothing secret, it
  is reset on every boot and by the regular reset, it sits alone on its own
  VLAN, and the gateway's forward chain lets nothing else in (only tcp 22
  to the board addresses) and keeps boards from reaching each other.
- The board's sshd keeps its own limits: `MaxStartups`, `MaxAuthTries`,
  and, on a root with OpenSSH 9.8 or later, `PerSourcePenalties` per
  client address. There is no failtoban on this path and no proxy log;
  the board's own journal, lost at reset, is the only record of who logged
  in.
- The host key a client sees on this path is the fleet key, which is
  public. It is published as SSHFP on the `ipv6.` names if the zone
  carries them (O3), and its fingerprint is on the board page.
- A client with several keys in its agent can use up the board's
  `MaxAuthTries` before the password prompt ("Too many authentication
  failures"). The board page gives the hint
  `ssh -o PubkeyAuthentication=no pi@ipv6.<board-name>.<site>`.
- A visitor's own key works here once added to
  `~pi/.ssh/authorized_keys` on the board, until the board resets.
- What a visitor does over this path, and what the board then does on the
  internet, is the same as over the proxy: the proxy never limited that.

### Names and DNS records

| Name | Records | Port 22 is answered by | SSHFP |
|---|---|---|---|
| `<site>` | A: the public IPv4 address. AAAA: the gateway. Both exist today | IPv4: the upstream gateway's proxy. IPv6: the gateway's proxy. Both present the site proxy key | `SSHFP 4 2 <SHA-256 of the site proxy key>` |
| `ipv6.<board-name>.<site>`, one per access port (D17; where `ssh_direct_ipv6` is on) | AAAA only: the board's global address | The board | `SSHFP 4 2 <SHA-256 of the fleet key>` |
| `gw.<site>` | An A record (the public IPv4 address) and exactly one AAAA record (a gateway address that answers ssh from outside) of its own. Not a CNAME (E10) | As `<site>`; documented only with `-p 2223` and `-p 2224`, for operators | none |

- `<board-name>.<site>` does not exist. The private A records under those
  names are **removed**: a visitor's client that resolves a private address
  connects to whatever has that address on the visitor's own network and
  offers it the published password. The removal, and having no plain or
  `ipv4.` names, rest on a sentence the owner wrote as a question and has
  not confirmed (O2); the reason given here stands by itself.
- Until the zone carries the `ipv6.` names (O3), the direct path works only
  by address. The board page prints the name only at a site with
  `ssh_dns_verify` on, which is the site's statement that the zone is
  loaded; before that it prints the board's IPv6 address instead.
- The inventory already requires `gw.<site>` to "keep exactly one AAAA
  record, an address that answers ssh from outside" (the site's host_vars).
  That stays true. Nothing in this design needs `<site>` or `gw.<site>` to
  have more than one.
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

**Generated, not hand-written** (E8).

- A converge writes the record set as a zone fragment on the gateway, at
  `ssh_dns_fragment_path` (proposed default
  `/var/lib/fpgas-online/dns/<site>.zone-fragment`, mode 0644).
- Format: RFC 1035 master-file lines with fully qualified owner names, one
  record per line, sorted, with a leading comment that names the converge
  and lists the records to **remove** (the private `<board-name>.<site>` A
  records, the `gw.<site>` CNAME). It can be pasted into a zone file or
  read by a script.
- Sources: names and addresses from `switches | port_vlan_map`; the fleet
  fingerprint from the NFS root's `ssh_host_ed25519_key.pub`; the proxy
  fingerprint from the installed public half of the site proxy key.
- Loading it into the public zone is manual, by the zone's owner (O3).
- The comparison with public DNS is a verify step behind `ssh_dns_verify`,
  default `false`. While it is off, verify checks only that the fragment
  exists and is well formed. Once the zone is loaded the site switches it
  on, and from then on verify fails on any difference, including a private
  A record or a `gw.<site>` CNAME that is still there.

### Ports and listeners

On the gateway's uplink, for IPv4 (the transit address) and IPv6 (any
global address the gateway itself holds) alike:

| Port on the uplink | Answered by | Key | How |
|---|---|---|---|
| 22 | The gateway's proxy | site proxy key | A firewall rewrite of port 22 to 2224, on the uplink interface only, switched on by `ssh_proxy_takes_port_22` |
| 2223 | The gateway's own sshd | the gateway's sshd keys | sshd listens on 2223 as well as on 22; the firewall admits 2223 on the uplink only |
| 2224 | The gateway's proxy | site proxy key | sshpiperd listens here |

Seen from outside:

| Public address and port | Leads to |
|---|---|
| IPv4 22 | The upstream gateway's proxy. Board names go to the gateway's port 2224; every other name goes to the upstream gateway's own sshd (E15) |
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
the build tag `full` and the required patch. It carries `sshpiperd` and the
plugins `yaml` and `failtoban`. Where it puts them is the packaging work's
choice, which is in progress (the sshpiper project's own release layout
puts plugins in `plugins/` beside the binary), so the role takes both
paths as variables and asserts that the files exist. The unit, user and
configuration belong to the role, not the package.

**Variables.** The first group is read by more than one role
(`ssh_proxy`, `sshd`, `firewall`, and the verify plays), so it lives in the
inventory, `group_vars/all/`, with site values in host_vars, and not in one
role's defaults:

| Variable | Default | Read by | Meaning |
|---|---|---|---|
| `ssh_proxy_enabled` | `false` | all three | Nothing is installed or opened while false |
| `ssh_proxy_listen_port` | `2224` | `ssh_proxy`, `firewall` | |
| `sshd_backup_port` | `2223` | `sshd`, `firewall` | Used only where `ssh_proxy_enabled` |
| `ssh_proxy_takes_port_22` | `false` | `firewall` | The rewrite of port 22 on the uplink. Off until the operators' and Ansible's route has moved to 2223 ([Rollout](#rollout-and-removal-of-the-old-path)) |
| `ssh_direct_ipv6` | `true` where `pib_network6_base` is defined, else `false` | `firewall`, the DNS fragment, the API | The forward rule and the `ipv6.` records of the direct path (D17, E17) |
| `site_public_ipv4` | none; site data | `firewall`, the DNS fragment | The site's public IPv4 address. **New**: no inventory variable holds it today; it appears only inside `webrtc_additional_hosts` |
| `ssh_dns_verify` | `false` | verify | Compare public DNS with the fragment (E8) |

The rest are the `ssh_proxy` role's own defaults:

| Variable | Default | Meaning |
|---|---|---|
| `ssh_proxy_sshpiperd_path` | `/usr/bin/sshpiperd` (proposal) | The daemon |
| `ssh_proxy_plugin_dir` | `/usr/lib/sshpiper` (proposal) | Where `yaml` and `failtoban` are |
| `ssh_dns_fragment_path` | see [Names and DNS records](#names-and-dns-records) | |
| `ssh_proxy_host_key` | `{{ vault_ssh_proxy_host_key }}` | The site proxy key's private half. The role fails if it is empty |
| `ssh_proxy_host_key_path` | `/etc/sshpiper/ssh_host_ed25519_key` | Where it is installed: owner `sshpiper`, mode 0600 |
| `ssh_proxy_listen_address` | `::` | Wildcard; one dual-stack socket |
| `ssh_proxy_upstream_proxy_addresses` | `[]` | Addresses the upstream gateway's proxy connects from. Given to `failtoban --ignore-ip` |
| `ssh_proxy_max_failures`, `ssh_proxy_ban_duration` | `20`, `5m` | failtoban (E9) |
| `ssh_proxy_connection_rate` | `30/minute` burst `30` (proposal) | New connections to the proxy port per source address, in the firewall |

**The service.** User `sshpiper`, unprivileged, with systemd hardening
(`ProtectSystem=strict`, `NoNewPrivileges=yes`), `LimitNOFILE=4096` and
`TasksMax=512` (proposals; sshpiperd itself has no cap on connections),
`After=network-online.target`, `Restart=on-failure`:

```
{{ ssh_proxy_sshpiperd_path }} \
  --address :: --port 2224 \
  --server-key /etc/sshpiper/ssh_host_ed25519_key \
  --drop-hostkeys-message \
  {{ ssh_proxy_plugin_dir }}/yaml --config /etc/sshpiper/pipes.yaml \
  -- \
  {{ ssh_proxy_plugin_dir }}/failtoban --max-failures 20 --ban-duration 5m \
      [--ignore-ip <address> for each of ssh_proxy_upstream_proxy_addresses]
```

- `--server-key` is always given. The default is the gateway's own sshd key.
  `--server-key-generate-mode` stays at its default, `disable`.
- `--drop-hostkeys-message` is required (see the findings table).
- `--ignore-ip` is left out altogether when the list is empty.
- The role fails if the proxy's public key equals the fleet key or one of
  the gateway's sshd keys.
- `pipes.yaml` is mode 0600 because the yaml plugin refuses a configuration
  file with group or other permission bits. The plugin makes no such check
  on the host key; that file is 0600 because it is a secret.
- Go opens a dual-stack socket for `::` on Linux unless `bindv6only` is
  set. The verify play checks that the port answers on both families.

**Where the role runs.** The pipes pin the fleet key, which the role reads
from the NFS root's `ssh_host_ed25519_key.pub`. On a fresh gateway that file
exists only after the play "Update the Pi NFS root" in `site.yml`, which is
later than "Configure the server services". The `ssh_proxy` role therefore
runs in a play of its own after that one, and fails if the file is missing.
`roles/sshd` and `roles/firewall` stay where they are: they need only the
shared variables.

**How board names are known.** The pipes are generated from `switches |
port_vlan_map`, the same map that makes the VLANs, the addresses and the
DHCP entries. One pipe per access port, empty ports included. This is
chosen over the fleet registry because a board name is a port's name and its
address follows from the port by formula: no board has to be registered, or
even present, for its name to route; nothing is configured per board; and a
login does not depend on the registry being up. A login to an empty port
is answered after about 3 seconds with `Permission denied`, the same as a
wrong password; the client cannot tell the two apart.

**Routing rules**, in this order in `pipes.yaml`:

```yaml
version: "1.0"
pipes:
  # One pipe per access port: password logins, relayed as typed.
  - from:
      - username: "pi-sw2-p47"
    to:
      host: "10.21.2.47:22"
      username: "pi"
      known_hosts_data:
        - "<base64 of: 10.21.2.47 ssh-ed25519 AAAA... (the fleet key)>"
```

- A board name reaches that board's sshd as user `pi`.
- **There are no key pipes in phase 1** (E5). No pipe has
  `authorized_keys`, the proxy holds no private key other than its host
  key, and it offers clients the method `password` only.
- There is no catch-all pipe. Any other name is refused by the proxy after
  the password prompt, and no connection is made to anything (E1).
- `pi@<site>` is therefore refused on the gateway's proxy. The board pages
  say that the login name is the board name.
- Board host keys are always pinned. With a stale pin every proxied login
  fails, which is why the verify play logs in end to end.

**How the proxy authenticates onward.**

- **Passwords are relayed.** With the published `pi` password this is the
  visitor's path, and it works through both proxies.
- **Public keys do not work through the proxy, for anyone, on any path**
  (E5). A key login cannot be relayed (findings table), and sshpiper's
  substitute, a key pipe, is not safe to use as it is: it logs in onward
  for anyone who merely offers an authorised public key (findings table).
  Operators reach a board with their key through the gateway's sshd on
  port 2223 (`ssh -J <account>@gw.<site>:2223 pi@<board address>`), as
  `docs/access.md` describes today with port 22. A visitor's own key works
  on the direct IPv6 path, where that is switched on, once the visitor has
  added it on the board.
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
  use port 2223. The question is with him again, with three options, and
  phase 1 as written is right under each of them
  ([Open points for the owner](#open-points-for-the-owner), O1).

  A consequence to know: over IPv4, public port 22 is the upstream
  gateway's proxy, and non-board names there go to the upstream gateway's
  own sshd (a reading of D1, E15). Over IPv6 the same command is refused by
  the gateway's proxy. Both present the same host key, so no warning marks
  the difference. `ssh -p 2223 <account>@gw.<site>` reaches the gateway's
  sshd on both families.

- **failtoban.** The password is public, so limiting guesses protects no
  secret. It limits noise and keeps a board's sshd responsive. Logins that
  arrive through the upstream gateway's proxy all come from its address,
  which is ignored; limiting those is the upstream proxy's job, and a
  requirement on it.
  - What counts is in the findings table. With `--max-failures 20` and
    `--ban-duration 5m` (E9) a client gets about ten wrong passwords in a
    five-minute window that starts at its first connection; then it sees
    `Connection closed by … port 2224` until the window ends. A successful
    login does not reset the count.
  - Keys in a client's agent are not a problem on the proxy: it offers only
    `password`, and refused keys are not counted. The hint "if you see 'Too
    many authentication failures', use `ssh -o PubkeyAuthentication=no`"
    belongs to the direct IPv6 path only, where a board's sshd counts every
    offered key.
- **Channels.** After login the proxy passes every channel through:
  agent forwarding, X11, port forwarding, `-W`. The user documentation
  says: never forward an agent to a board.

**Who holds what.** The proxy holds one secret, the site proxy key. It holds
no key that a board or the gateway accepts. Whoever controls the proxy sees
every proxied session in clear, which for a board is no more than every
visitor can do with root on that board.

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
`ssh_proxy_enabled`:

```
ListenAddress 0.0.0.0:22
ListenAddress [::]:22
ListenAddress 0.0.0.0:{{ sshd_backup_port }}
ListenAddress [::]:{{ sshd_backup_port }}
```

- Port 22 stays on every address, exactly as today: boards, the web tier
  and anything on the gateway itself are not affected.
- Port 2223 is on the wildcard addresses too, and **the firewall admits it
  on the uplink interface only**. A `ListenAddress` that names an address
  the host does not have yet makes sshd log a bind failure and never try
  again, so the transit address is not named here.
- Over IPv4 it is what the upstream gateway's same-port forward of 2223
  reaches. Over IPv6 it is the operators' and Ansible's route once port 22
  on the uplink is the proxy.
- Once one `ListenAddress` is given, sshd listens only on those listed,
  which is why the two port 22 lines are written out.
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
  `internal_networks` accepts everything from the board network. The
  loopback interface is accepted earlier in the chain, as today.
- A per-source limit on new connections to the proxy port, before that
  accept: a meter keyed on the source address,
  `ssh_proxy_connection_rate`, over which new connections are dropped.
  sshpiperd has no cap of its own before key exchange. The addresses in
  `ssh_proxy_upstream_proxy_addresses` are exempt, since every IPv4 visitor
  arrives from them.
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

- **The direct IPv6 path** (D17), when `ssh_direct_ipv6`:
  `iifname {{ eth_uplink }} oifname "v*"
  ip6 daddr { <board addresses from port_vlan_map> } tcp dport 22 accept`.
  This exposes each board's sshd, with the published password, to the IPv6
  internet under the public fleet key
  ([Host keys in plain words](#host-keys-in-plain-words), "What the direct
  IPv6 path exposes"). Only port 22, only to addresses the port map
  generates, only arriving on the uplink.
- **Boards going out and back in (E4):** `iifname "v*" ip daddr
  {{ site_public_ipv4 }} tcp dport { 22, 2222, 2223, 2224 } drop`
  before the rule that accepts `v*` to the uplink.
- `v*` to `v*` stays dropped. Nothing is opened from the site's LAN to the
  boards' IPv4 addresses (D3).

The per-board DNAT rules are not touched in phase 1. Their removal is in
[Rollout](#rollout-and-removal-of-the-old-path).

### Boards (`roles/fixpi`, #186)

- **Ed25519 only** (E11). An sshd drop-in sets
  `HostKey /etc/ssh/ssh_host_ed25519_key`. There is then one fleet
  fingerprint to publish, one SSHFP record per `ipv6.` name and one pin per
  board in the proxy. The RSA and ECDSA files stay on disk, unused.
- **`PerSourcePenaltyExemptList {{ pib_network }}.0.1`, only on a root
  whose OpenSSH is 9.8 or later.** The address is the gateway's board-side
  one. On such a root, a few wrong passwords through the proxy would
  otherwise lock the web terminal, the upload page, the jump route and
  every other proxied visitor out of that board.
  - The line goes in a drop-in of its own, written by fixpi only when the
    root's `openssh-server` version, read from the extracted root at deploy
    time (`dpkg-query --root=<the root> -W openssh-server`), is 9.8 or
    later; otherwise fixpi removes the file. It is not keyed on `dist`.
  - Today's root is bookworm with OpenSSH 9.2: the option is unknown there,
    sshd would refuse to start, and every board would lose ssh. There is
    also no penalty to exempt on 9.2. The line appears by itself when the
    root moves to trixie.
  - The check that the root's sshd accepts its configuration is the VM
    test: the virtual Pi boots this root, and its existing ssh login check
    fails if sshd did not start. The gateway cannot run the root's
    `sshd -t` itself: the root is for another architecture.
  - The gateway's own sshd gets no such line (the proxy never connects to
    it). For the record, the Welland gateway and the VM test's server run
    Debian 13 (trixie), whose OpenSSH is 10.0 and knows the option.
- **No new key in `authorized_keys`.** The proxy has no key for the boards
  (E5), so fixpi gains no key source.
- **The login banner, the `pi` account and its password stay as today.**
- **No login aliases** (E7).
- **The direct path** (D17) needs nothing else on the board: its sshd
  already
  listens on its global IPv6 address, and `ssh pi@ipv6.<board-name>.<site>`
  is an ordinary login. The visitor's own key works there once added to
  `~pi/.ssh/authorized_keys`, until the board resets.
- The first converge changes files in the shared root and so reboots the
  fleet (E12). A later change to `switches` does not, because nothing on the
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
 "host_key_fingerprint": "SHA256:<site proxy key>",
 "direct_user": "pi", "direct_host": "ipv6.pi-sw2-p47.welland.fpgas.online",
 "direct_host_key_fingerprint": "SHA256:<fleet key>"}
```

The three `direct_` fields are present only where `ssh_direct_ipv6` is on.
There is no `legacy_port` (E13).

The page prints:

```
ssh pi-sw2-p47@welland.fpgas.online
scp FILE pi-sw2-p47@welland.fpgas.online:Uploads/
```

with:

- the password, as today;
- "The login name is the board's name, not `pi`.";
- "Log in with the password. ssh keys are not accepted on this path.";
- "If you get `Permission denied` with the right password, the board may
  be off or not plugged in: check its status on this page.";
- "Do not forward your ssh agent (`-A`) to a board.";
- the site proxy key's fingerprint.

**Only to a visitor whose own connection to the website arrived over IPv6**
(E17, a reading of D17), and only where `ssh_direct_ipv6` is on, the page
adds, under "Directly over IPv6":

```
ssh pi@ipv6.pi-sw2-p47.welland.fpgas.online
```

with the fleet key's fingerprint, "This key is shared by every board and is
public.", and "If you see 'Too many authentication failures', use
`ssh -o PubkeyAuthentication=no pi@ipv6.pi-sw2-p47.welland.fpgas.online`".

How the site knows the visitor's address family: the website runs behind
the gateway's nginx, so the application sees nginx's address, not the
visitor's. nginx passes the client address on (`$remote_addr`, as
`X-Real-IP` or `X-Forwarded-For`), and the page shows the direct block when
that address is an IPv6 address. The header must be set by the gateway's
own nginx and never taken from the request, and the page must not be cached
across visitors. Whether the site's nginx template already passes the
address to the application was not checked for this document; it is part of
site#44. An IPv4 visitor, including one inside the site, sees only the
proxy command.

No line on the page contains `-p`: the old `ssh -p <port> pi@<site>` line
is removed in the same change (E13). A site without `ssh_proxy_enabled`
keeps today's page.

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
   - A user name matching `^pi-sw[0-9]+-p[0-9]+$` (the reading of "piXXX",
     E15) is connected to the gateway's transit address, port 2224 (E15),
     with the same user name, relaying the password. It needs no list of
     boards.
   - On that onward connection it pins the site proxy key, which is what the
     gateway's proxy presents. As a known_hosts line the pin is
     `[<transit address>]:2224 ssh-ed25519 <the site proxy key>`.
   - It has password pipes only, so it offers clients the method `password`
     only, like the gateway's proxy. No key pipes (the same defect applies).
   - Every other user name goes to the upstream gateway's own sshd (E15).
     What that sshd does with them is the upstream operator's business.
   - **It runs the same patched package**, or closes failed onward
     connections by other means. Otherwise wrong passwords for non-board
     names through it hold the login slots of the upstream gateway's own
     sshd, which sees all of them arriving from the loopback address.
   - **Required: it logs each login** with the client's address, the user
     name and the time, **and limits failed logins and new connections per
     client address.** The gateway sees only the upstream proxy's address
     for IPv4 visitors, cannot tell them apart, and exempts that address
     from its own limits. Without this, IPv4 visitors are neither limited
     nor attributable.
   - To know: through two proxies one wrong password makes two login
     attempts at the board.
2. **Public tcp 2222:** the upstream gateway's own sshd.
3. **Public tcp 2223:** forwarded to the gateway's transit address, port
   2223, keeping the client's source address.
4. **Public tcp 2224:** forwarded to the gateway's transit address, port
   2224, keeping the client's source address.
5. **Inside the site:** `<site>` resolves to, or is routed to, the gateway's
   transit address, where ports 22, 2223 and 2224 answer as in
   [Ports and listeners](#ports-and-listeners).

IPv6: the upstream gateway lets tcp 22, 2223 and 2224 reach the gateway's
global address, and tcp 22 reach the board prefix (D17; not needed at a
site with `ssh_direct_ipv6` off). Nothing is proxied or forwarded. The allowance for 2223
and 2224 is needed **before** the gateway's operators move to port 2223
([Rollout](#rollout-and-removal-of-the-old-path), step 2).

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
been verified. The step that can cut the deployer off is step 5, and steps
2 to 4 exist to make it safe.

1. **Package published** (apt#21), **with the required patch**, and the VM
   test's leak assertion green. Until then `ssh_proxy_enabled` is set only
   in the VM test inventory and no later step is taken at any site (E16).
2. **The upstream gateway admits the backup ports first.** Over IPv6 it lets
   tcp 2223 and 2224 reach the gateway's global address; over IPv4 it
   forwards public 2223 and 2224 to the transit address. Nothing listens
   there yet. This comes before anything moves to 2223, because an upstream
   IPv6 filter exists: `docs/access.md` records one of the gateway's IPv6
   addresses timing out on port 22 from outside.
3. **Gateway converge with `ssh_proxy_enabled: true`** and
   `ssh_proxy_takes_port_22: false`: proxy on 2224, sshd also on 2223,
   firewall rules, board changes. This converge reboots the fleet once
   (E12). Port 22 is untouched. Checked from outside the site:
   `ssh -p 2223` reaches the gateway's sshd over IPv6 and over IPv4, and
   `ssh -p 2224 <board-name>@gw.<site>` reaches a board.
4. **Everything that reaches the gateway's sshd from the uplink side moves
   to port 2223, in one step:**
   - Ansible: `ansible_port: 2223` in the site's host_vars.
   - The jump route: `ssh -J pi@gw.<site>:2223 pi@<board address>`, and the
     hop from the jump shell, `ssh -p 2223 pi@gw.<site>`.
   - Every other command in `docs/access.md` that names the gateway: the
     operators' own logins, the automation account, `-J <account>@…` to a
     board as `ansible` or `root`.
   - Every ssh client configuration that points at the gateway on port 22:
     each operator's own, and the managed configuration the automation
     sessions use. `known_hosts` gains `[gw.<site>]:2223` with the sshd's
     keys.
   - The VM harness: it reaches the server VM through a host forward to
     guest port 22 (`hostfwd=tcp::<port>-:22` in `tests/vm/vm_manager.py`).
     It gains a second forward, to guest port 2223, and uses that one for
     Ansible and its own ssh from the point where the server's sshd listens
     there; and a third, to guest port 2224, for the proxy assertions.

   Not affected, because they never cross the uplink: the web terminal and
   the upload page (they connect from the gateway to a board's address on
   its VLAN), the jump account's own hop from the gateway to a board, and
   boards reaching the gateway's sshd on port 22. This is from
   `docs/access.md` and the roles; the VM test's existing web-terminal
   login check confirms it at step 5.

   Then a full converge and both verify playbooks run through port 2223.
5. **`ssh_proxy_takes_port_22: true`.** Port 22 on the uplink becomes the
   proxy. `roles/firewall` asserts, before it changes anything, that the
   Ansible connection it is running over arrived on the backup port: the
   server port in `$SSH_CONNECTION` (its fourth field) must equal
   `sshd_backup_port`. A converge that still arrives on port 22 fails at
   the assert and changes nothing. After it,
   `ssh <board-name>@<site>` works over IPv6 from outside.
6. **The upstream gateway's proxy.** Its own sshd also on 2222; then the
   site proxy key and the proxy on public port 22, built from the same
   patched package. `ssh <board-name>@<site>` works over IPv4 from outside.
7. **DNS.** The zone's owner loads the fragment: SSHFP on `<site>`,
   `gw.<site>` as records of its own, the `ipv6.` names where
   `ssh_direct_ipv6` is on; the private `<board-name>.<site>` A records are
   removed in the same change. They never gave a visitor a working command.
   Then `ssh_dns_verify` is switched on.
8. **Verified from outside the site**, on IPv4 and on IPv6.
9. **The board pages switch** to the new command. The
   `ssh -p <port> pi@<site>` line is removed in the same change, not kept
   underneath (E13, O4).
10. **The per-board `<s><pp>22` DNAT rules** leave the gateway's firewall
    and the upstream requirements in the next converge after step 9. The
    `<s><pp>44` rules are not part of this.

At a site where the `-p` command does work from outside today (none with
`switches:` does), steps 9 and 10 are the only ones that take a working
command away, and they come after step 8.

### Monitoring

The proxy runs under systemd with `Restart=on-failure`. `verify-server.yml`
checks that it is active and listening; `verify-pi.yml` checks that a login
through it reaches a board ([Verification](#verification)). The proxy's log
is the only record of which client address logged in to which board name,
so it is kept in the journal like the sshd's. Each refused key writes an
ERROR line; journald's rate limit bounds that. The public IPv4 path also depends on the upstream gateway's proxy, which only a
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

## Future work

Neither of these is built in phase 1. Each needs a patch in our package
first, with a test that shows the onward login is made only after the
client has proved it holds the key.

- **Key logins to boards through the proxy.** A pipe per board with the
  keys the boards trust for `pi`, and a mapping key whose public half fixpi
  adds to `pi`'s `authorized_keys`. Whoever controls the proxy then has
  root on every board, which every visitor has anyway.
- **Admins to the gateway through the proxy** (O1). A pipe per gateway
  account with that account's authorised keys, and a mapping key the
  gateway's sshd accepts for those accounts from the loopback address
  only. This makes the proxy a holder of gateway credentials: whoever
  controls the proxy service controls the gateway, and the sshd's log shows
  the mapping key, not the admin's. It needs the owner's decision as well
  as the patch.

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

## Open points for the owner

Four. For each, phase 1 builds the stated default, so none blocks building.

| # | Open point | Built in phase 1 while he is asked |
|---|---|---|
| O1 | **Administrators on port 22.** His words assume the proxy carries them to the gateway (D13). Re-asked on 2026-10-05 with three options: (a) the proxy checks the administrator's key and logs in to the gateway with a key it holds; (b) no proxy on the gateway's port 22, with board names as locked accounts on the real sshd; (c) administrators stay on port 2223. Facts behind the question: relaying non-board names to a key-only sshd by password can never log anyone in and lets anyone hold that sshd's login slots; a key login needs the proxy to hold a key for the gateway's accounts, through a key pipe that is itself defective ([Future work](#future-work)); and for a non-board name, public IPv4 port 22 leads to the upstream gateway's sshd (E15), where the same facts apply if that sshd is key-only, while IPv6 port 22 leads to a refusal, under one host key | Non-board names are refused by the gateway's proxy (E1). Operators use `ssh -p 2223 <account>@gw.<site>`. **Phase 1 as written is right under all three options**: (c) is what it builds; (a) adds key pipes later, on top of it, once the package is patched; (b) changes only what answers port 22 on the gateway's uplink, which is one variable (`ssh_proxy_takes_port_22`), and leaves the proxy on 2224, the sshd on 2223, the boards, the DNS records and the upstream forwards as they are |
| O2 | **The board names in DNS.** From a sentence he wrote as a question ("Maybe … ?") and has not confirmed; his answer of 2026-10-05 (D17) covers the `ipv6.` path only. Still unconfirmed: no `ipv4.` names; no plain `<board-name>.<site>` names; and with that the removal of the private A records those names carry today | No `ipv4.` or plain names are created. The fragment lists the private A records for removal, for the reason given under [Names and DNS records](#names-and-dns-records) |
| O3 | **How the generated records get into the public zone.** Never asked. The zone is the owner's and hosted outside the site | The role writes the fragment; loading it is manual; `ssh_dns_verify` stays off until it is loaded, so verify is not red meanwhile, and stays red after it is switched on until public DNS matches (E8). Until the SSHFP record is loaded, visitors get the ordinary first-connection prompt |
| O4 | **Removing the old `ssh -p <port> pi@<site>` line and the per-board ssh DNAT ports.** D14 says visitors are never told `-p`; it does not say when the old path goes | The line leaves the page with this rollout, the DNAT rules in the next converge, and there is no `legacy_port` (E13) |

Not open, but to be done because of D16: the docs page for the upstream
network says the upstream is never asked to hold an fpgas.online
credential. It must now say that a site's upstream proxy holds the site's
proxy key (fpgas-online/fpgas.online-docs#16).

## Verification

`ssh-keyscan` is always run as `ssh-keyscan -t ed25519`: the default probes
several key types and counts 5 against failtoban's limit. The test client
either stays under the limit or is listed in `--ignore-ip` for the tests
that are not about the limit.

**Where the checks live.** `verify-server.yml` runs before any Pi exists,
so it holds only what needs no board: the service is active, the listeners,
the keys, the firewall rules, the fragment. Every check that logs in to a
board is in `verify-pi.yml`, which runs against a live Pi and already
checks that the gateway's jump account reaches the board. The real
client for the proxied login runs on the test host and reaches the proxy
through the harness's forward to guest port 2224, so it arrives on the
gateway's uplink like an outside client.

### Required now, in the VM test (phase 1 cannot merge without these)

The harness needs only what it has, plus two more host forwards (guest 2223
and 2224) and a password-capable ssh client on the test host.

1. **A real ssh client logs in as `<board-name>@<site>` through the proxy
   port and lands on that board**: the password is accepted, `hostname`
   prints `<board-name>`, and `$SSH_CONNECTION` on the far side shows the
   board's own address. (`<site>` is given to the client as a host alias
   for the forwarded port.)
2. **A non-board name is refused by the proxy**: `root`, `pi` and an
   operator's account get `Permission denied` after the password prompt,
   and the gateway's own sshd logs no connection from the proxy.
3. **The gateway's sshd answers on 2223**: Ansible's own connection uses
   that forward, and `ssh-keyscan -t ed25519` there returns the gateway's
   sshd key.
4. **`ssh-keyscan -t ed25519` on the proxy port returns only the site proxy
   key**, and it differs from the fleet key and from the gateway's sshd
   keys. A proxied login leaves the client's `known_hosts` byte for byte
   unchanged.
5. **The leak assertion** (B1 patch): after N failed proxied logins to the
   board (N above the sshd's `MaxStartups` start value), the board's sshd
   shows no lingering unauthenticated sessions, and a correct proxied login
   and the existing web-terminal login to that board succeed at once.
6. **The proxy offers `password` only**: a client restricted to public-key
   authentication is refused, and the board logs no connection for it.
7. **The role's guards**: it refuses to converge without the site proxy
   key, with one equal to the fleet key or a gateway sshd key, and with
   `ssh_proxy_takes_port_22` when the Ansible connection did not arrive on
   the backup port.
8. **Port 22 as the proxy**: with `ssh_proxy_takes_port_22` on (the harness
   already on the 2223 forward), the old forward to guest port 22 presents
   the site proxy key and logs a board name in.
9. **Nothing else opened**: from the virtual Pi, ports 2223 and 2224 do not
   answer on any gateway address, and port 22 on the gateway is still its
   sshd.
10. **The board's sshd started** with the generated drop-ins (the existing
    ssh login check), on whichever OpenSSH the root has.
11. **The fragment** exists, is well formed, has the SSHFP record for
    `<site>`, no A record for any board name and no SSHFP on `gw.<site>`.

### Later: needs something the VM harness does not have today

Each is checked on the real site during rollout until the harness can do
it. What is missing is named.

| Check | Needs |
|---|---|
| The proxy and the sshd on 2223 answer over IPv6; the redirect of port 22 works in the `ip6 nat` chain | An IPv6 uplink (the harness's user-mode network runs with `ipv6=off`) |
| The direct path: `ssh pi@<board's global address>` from the uplink side presents the fleet key; with `ssh_direct_ipv6` off it is dropped | An IPv6 uplink and a host routed to the board prefix |
| The 2223 and 2224 listeners are back after a reboot of the gateway | A reboot of the server VM in the harness |
| With `VerifyHostKeyDNS yes`, a first login shows no prompt; a wrong record gives the changed-key banner | A signed test zone and a validating resolver |
| A board cannot open the site's public address on 22, 2222, 2223, 2224 | An address beyond the uplink that stands for the public address |
| failtoban's limit and the firewall's per-source limit hold for a second client and not for an ignored address | A second client address |
| scp and sftp through the proxy | Nothing missing; lower priority, added after the required set |

Not kept as checks: "`ssh -J` works through the proxy" (a jump through a
board name to somewhere else is not something the design offers) and "a
client with ten keys in its agent" (the proxy offers only `password`;
measured by the reviewer: such a client reaches the prompt and logs in).

"The jump route" in this document always means the operators' route in
`docs/access.md`: `ssh -J pi@gw.<site>:2223 pi@<board address>`, through
the gateway's own sshd on port 2223, never through the proxy. The VM
check for it is the existing one in `verify-pi.yml` (the gateway's jump
account reaches the board), which does not cross the uplink.

### From outside the site, once the upstream gateway has done its part

- `ssh <board-name>@<site>` reaches the board over IPv4 and over IPv6, and
  a default client that has accepted the key on one family is silent on the
  other.
- A wrong password through public IPv4 port 22, repeated, leaves no
  lingering sessions on the gateway's proxy or the board.
- `-p 2223` and `-p 2224` on `gw.<site>` present the gateway's sshd keys
  and the site proxy key, on both families.
- The same commands from inside the site present the same keys.
- Where `ssh_direct_ipv6` is on, `ssh pi@ipv6.<board-name>.<site>` reaches
  the board.
- With `ssh_dns_verify` on, public DNS matches the fragment.

### Before phase 2 is switched on

`$SSH_CONNECTION` on the board for a login through port 2224 shows the
client's address; NFS and the boards' outbound traffic still work; removing
the policy route makes the verify play fail.

## Work items

Tracking: #191. The issue texts predate this revision; this document is
what to build from.

| Phase | Issue | What | What its text gets wrong now |
|---|---|---|---|
| 1 | fpgas-online/apt#21 | Package `sshpiper` from fpgas-online/sshpiper: `sshpiperd`, `yaml`, `failtoban`, built with the tag `full` | It omits `failtoban` and the required phase 1 patch (a failed onward authentication closes the upstream connection), and says to offer the phase 2 patch to the sshpiper project. Both patches stay in our packaging branch |
| 1 | #187 | Gateway role `ssh_proxy` (password pipes only); `roles/sshd` port 2223; the input and NAT rules, the per-source limit, the backup-port assert | It copies the fleet key to the proxy and binds the uplink address only. Its "unknown usernames are not forwarded anywhere" stands (E1) |
| 1 | #186 | Boards: Ed25519-only host key; the penalty exemption on a root with OpenSSH 9.8 or later | Login aliases (E7) and the proxy's mapping key (E5) are dropped. "The same key the proxy presents" is wrong. The exemption line as written there breaks sshd on today's root |
| 1 | #188 | Firewall: direct IPv6 to boards (D17), behind `ssh_direct_ipv6`, on by default at a site with a board prefix; boards cannot reach the public ssh ports | The internal IPv4 rule is dropped (D3) |
| 1 | #189 | DNS: the generated fragment, and the comparing verify step behind `ssh_dns_verify` | Its record set (per-board A records, `ipv4.`, `private-ipv4.`, an internal view) is replaced by [Names and DNS records](#names-and-dns-records) |
| 1 | fpgas-online/fpgas.online-gw#2 | `/api/boards` `ssh` object | Fields as in [What the board pages print](#what-the-board-pages-print-fpgas-onlinefpgasonline-site44) |
| 1 | fpgas-online/fpgas.online-site#44 | Board pages: the proxy command always; the direct IPv6 command only to a visitor who arrived over IPv6, known from the client address the gateway's nginx forwards | Its command, `ssh <board-name>@<board-name>.<site>`, is replaced. The page needs the visitor's address family, which the issue does not mention |
| 1 | fpgas-online/fpgas.online-docs#16 | User documentation (password only, never forward an agent, what `Permission denied` can mean), and the upstream-network page | The upstream page must say the upstream proxy holds the site's proxy key (D16), and that logging and rate limiting there are required |
| 1 | — | `docs/access.md`: port 2223 as the operators', the jump route's and Ansible's port | New |
| 1 | — | VM harness: host forwards to guest 2223 and 2224; a real ssh client for the proxied login | New |
| 1 | — | The upstream gateway: the list above. Outside these repositories | New |
| 2 | #190 | Transparent IPv4 source | Covers port 2224 and public-address sites; port 22 behind an upstream gateway is open |
