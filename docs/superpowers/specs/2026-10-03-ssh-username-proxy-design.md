# Design: per-board SSH names and a username-routing SSH proxy

Date: 2026-10-03
Status: proposed. Nothing here is implemented or deployed. The decisions in
[Decisions needed](#decisions-needed) are open, and the work is tracked by
the issues under [Work items](#work-items).

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

Every board gets one name and one login that work the same over IPv4 and
IPv6 with a stock OpenSSH client and no client configuration:

```
ssh pi-sw1-p7@pi-sw1-p7.<site-domain>
```

The host key it presents is the same on both paths, so switching between
IPv4 and IPv6 never produces a prompt or a changed-key warning, and it can be
checked against DNS (SSHFP) by users who opt in.

- **Phase 1:** a username-routing SSH proxy (sshpiper) on the gateway carries
  the IPv4 path, and IPv6 goes straight to the board. The board sees the
  gateway's address as the source of proxied connections.
- **Phase 2:** transparent IPv4 source rewriting, so the board sees the
  client's real address on proxied connections too.

## Non-goals

- Changing how operators, Ansible and the jump account reach the gateway's own
  sshd ([`docs/access.md`](../../access.md)). The gateway's sshd keeps port 22
  on its own addresses. Decision D1 does change the outside-IPv4 route to the
  site router itself; that is called out there.
- Removing the per-port DNAT. It stays as the legacy path until the board
  pages stop printing it. Removing it is a later, separate change.
- An IPv6-only Pi network. Pi 3 network boot is IPv4-only, Pi 4 IPv6 boot is an
  experimental alpha that needs ISC DHCP, and the Orange Pi FEL/U-Boot path and
  the NFS root are IPv4. The proxy-to-board hop could later use IPv6 with the
  client's IPv4 address embedded (RFC 6052), but phase 2 does not need it.
- Per-board host keys. Every board shares one NFS root and so one host key
  (access.md, "Host key"). This design keeps that; see
  [Trust](#trust-what-the-host-key-proves).
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
| No SSH client uses SRV or SVCB to find a port. OpenSSH takes the port only from `-p`, `host:port`, `Port` or `getservbyname("ssh")`. | OpenSSH `ssh.c` and `readconf.c`; bz#2217 (2014) and openssh-portable PR #228 (2021) are unmerged; RFC 9460 requires a per-protocol SVCB mapping and none exists for SSH | The port cannot come from DNS. The only zero-config paths are port 22 on the board's own address (IPv6) and port 22 on a proxy that routes on the username (IPv4). |
| SSHFP has no port dimension. OpenSSH looks SSHFP up at the connection host name only. | RFC 4255; `dns.c` `verify_host_key_dns()` | Each board needs its own DNS name. |
| The server sends its host key during key exchange, before the client sends a username. | RFC 4253 §8, RFC 4252 §5 | The proxy cannot choose a host key per board. It presents one key to every client. |
| A client's public-key signature covers the session identifier, which differs on each side of a proxy. | RFC 4252 §7 | The proxy cannot relay public-key logins. It checks the client's key itself and logs in to the board with its own key (sshpiper's "mapping key"). Passwords are relayed as they are. sshpiper's YAML plugin offers only password and public-key logins. |
| `UpdateHostKeys` defaults to `yes` (OpenSSH 8.5 and later) unless `VerifyHostKeyDNS` is on or a custom `UserKnownHostsFile` is set. After login the client treats known_hosts keys for that name that the server does not list as deprecated, and removes them, unless one of the keys also appears under another known_hosts name. | `readconf.c:2950-2957`; `clientloop.c:2189-2196`, `check_old_keys_othernames()` and the removal path after it | If the two paths presented different keys under one name, a user who knows only that one name would lose one path's key on each login with the other path and be prompted again, repeatedly. The proxy and the boards must present the same key. |
| An authoritative DNS server sees the resolver, not the client. The client-subnet option (RFC 7871) is optional and describes the path to the resolver. | Live `o-o.myaddr.l.google.com TXT` queries via 8.8.8.8, 2001:4860:4860::8888, 1.1.1.1 and a site resolver, 2026-10-03 | DNS cannot hand different SSHFP records to IPv4 and IPv6 clients. One key on both paths makes that unnecessary. |
| sshd has no PROXY-protocol support. sshpiper accepts PROXY headers from its clients (`--allowed-proxy-addresses`) but never sends them upstream. | OpenSSH source; sshpiper `cmd/sshpiperd/main.go` | The only way for a board to see the client's address on the IPv4 path is IP-level transparent proxying (phase 2). |
| Since OpenSSH 9.8, sshd penalises a source address after repeated failed logins (`PerSourcePenalties`, on by default, nothing exempt). The boards run OpenSSH 10.0 (trixie). | `sshd_config(5)` | In phase 1 every proxied login reaches the board from the gateway's address, as do the web terminal, the upload page and the jump route. A few wrong passwords through the proxy would make that board refuse the gateway for up to 10 minutes. |
| `VerifyHostKeyDNS` defaults to `no`. A "secure" SSHFP answer needs a DNSSEC-validated response and, with glibc 2.31 and later, `options trust-ad` in resolv.conf. | `readconf.c:2977`, `ssh_config(5)`, glibc 2.31 NEWS | SSHFP helps only users who opt in. Everyone else gets one ordinary first-connection prompt. |

An earlier draft gave the proxy its own ECDSA key and split the SSHFP set by
algorithm. OpenSSH does compare only same-algorithm SSHFP records
(`dns.c:259-281`), so the DNS side of that works, but `UpdateHostKeys` (row
five above) makes it churn known_hosts for default clients that use a single
board. It is recorded under decision D2 as the rejected alternative.

## Current state this builds on

From `main` at 96a7336d and [`docs/access.md`](../../access.md):

- **Board names.** `pi-sw<S>-p<P>`, IPv4 `10.21.<S>.<P>`, IPv6
  `<pib_network6_base><S:02d>::<P>` (`port_vlans.py`). dnsmasq publishes them
  in `pib_domain` through `host-record=` lines
  (`roles/pxe/templates/ports.conf.j2`). The site router delegates that zone
  to the gateway's dnsmasq. The delegation answers A records only:
  `dnsmasq_auth_subnet` is `10.21.0.0/16`, and dnsmasq serves only addresses
  inside the auth subnets. dnsmasq cannot sign a zone. `dns-rr=` records are
  served in auth zones (dnsmasq(8)).
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
- **IPv4.** The one public IPv4 address belongs to the site router. The router
  runs its own sshd on port 22 and DNATs chosen ports to the gateway
  (`eth_uplink_static_address`). Neither the router nor the gateway SNATs
  those connections, so on the legacy path the board sees the client's real
  address.
- **IPv6.** The site router routes the boards' /56 to the gateway, but the
  gateway's forward chain drops every new connection from the uplink to a
  `v*` interface (it accepts only established, DNATed and outbound traffic).
- **Isolation.** The forward chain drops `v*` to `v*` traffic, so one board
  cannot reach another. The input chain accepts a service on every interface,
  `v*` included, unless the rule names an interface.
- **The gateway's own sshd** is public-key only (`sshd_pubkey_only`), on port
  22, and carries the `pi` jump account.

## Design

### Names and DNS records

For each board (example `pi-sw1-p7`). `<site-domain>` is decision D4.

| Name | External view | Internal view (site resolver) | Purpose |
|---|---|---|---|
| `pi-sw1-p7.<site-domain>` | A = site public IPv4; AAAA = board IPv6 | A = `10.21.1.7`; AAAA = board IPv6 | The name users type |
| `ipv4.pi-sw1-p7.<site-domain>` | CNAME `ipv4.gw.<site-domain>` | CNAME `private-ipv4.pi-sw1-p7.<site-domain>` | Force the IPv4 path |
| `ipv6.pi-sw1-p7.<site-domain>` | AAAA = board IPv6 | same | Force the direct IPv6 path |
| `private-ipv4.pi-sw1-p7.<site-domain>` | A = `10.21.1.7` | same | Inside the site |
| `ipv4.gw.<site-domain>` | A = site public IPv4 | — | The proxy |

Every name that is not a CNAME also carries the same record,
`SSHFP 4 2 <fleet Ed25519 SHA-256>`, in both views. A CNAME cannot hold other
records (RFC 2181 §10.1); OpenSSH's SSHFP query follows it and gets the
target's record, which is the same one. Because the key is the same on every
path, split horizon changes only addresses and CNAME targets, never
fingerprints.

Notes:

- **The login name is not in DNS.** `pi-sw1-p7@` is only what the user types.
  The proxy routes on it.
- **Who answers the internal view.** The gateway's dnsmasq is authoritative
  for `pib_domain`, not for `<site-domain>`. The internal view of
  `<site-domain>` (the private A records and internal CNAME targets) has to
  come from the site resolver, as an internal zone or override for
  `<site-domain>`. That is part of decision D4.
- **Signing.** SSHFP is "secure" only from a DNSSEC-signed zone with a DS
  record in a signed parent. The external view is decision D4. Until the
  internal view is signed too, users inside the site get the ordinary prompt
  even with `VerifyHostKeyDNS yes`.
- **Generated, not hand-written.** Names and addresses come from
  `switches | port_vlan_map`. The fingerprint is not in the inventory: the
  generator reads it from the NFS root's `ssh_host_ed25519_key.pub` on every
  converge, so a regenerated key (infra#126) updates the records.
- **The shared A record depends on D1.** The external A record points at the
  site's public IPv4 address. That is right only if the router forwards port 22
  there to the proxy. Without D1, port 22 there is the router's own sshd, and
  the board names must not carry that A record.
- **No happy eyeballs.** OpenSSH tries addresses one at a time, so a client
  with broken IPv6 waits out the TCP connect timeout before it falls back to
  IPv4. The user docs recommend `ConnectTimeout` and point at the `ipv4.` name.
- **The internal A record needs a firewall rule** (decision D3). Today nothing
  inside the site can open a connection to `10.21.x.y`, and LAN clients cannot
  hairpin the router's public DNAT.

### Host keys: one key on every path

- **The proxy presents the fleet's Ed25519 host key.** The gateway already
  holds it in the NFS root. The converge copies it into the proxy's
  configuration directory, owned by the proxy's service user with mode 0600,
  re-copies it whenever it changes, and passes it explicitly with
  `--server-key /etc/sshpiper/ssh_host_ed25519_key`. The flag must be set:
  sshpiperd's default is `/etc/ssh/ssh_host_ed25519_key`, the gateway's own
  sshd key, and with that default the proxy would silently present the wrong
  key.
- **Boards offer Ed25519 only.** A fixpi sshd drop-in sets
  `HostKey /etc/ssh/ssh_host_ed25519_key`. The RSA and ECDSA key files stay
  on disk but are not offered. Every path then presents exactly one key,
  whatever the client's algorithm preference. Clients that exclude Ed25519
  (for example in FIPS mode) cannot connect on either path.
- **The proxy drops the board's host-key announcement.** After login sshd
  announces its host keys (`hostkeys-00@openssh.com`), and sshpiper, which is a
  packet pipe after authentication, forwards it to the client by default. The
  proxy runs with `--drop-hostkeys-message`, so the proxied path never touches
  known_hosts. Without the flag, Ed25519-only boards would still be harmless:
  the client already knows the one announced key. If a board ever announced
  RSA or ECDSA again, the client would ask for a proof, which the board signs
  over its own session, and the client would log "server gave bad signature"
  and abandon the update.
- **Rotation.** sshd loads its host keys when it starts, so after the NFS
  root's key changes (#126) booted boards keep the old key until their
  staggered reboot, up to about 33 minutes. During that window the proxy pins
  both the old and the new board key in `known_hosts_data`, and the proxy's own
  key and the SSHFP records switch to the new key when the reboot wave starts.
  Clients see the key change once, as they would without the proxy.

### Trust: what the host key proves

The fleet key is readable by anyone with root on any board, and every board
user has NOPASSWD sudo. Today that already lets a board user impersonate every
other board to a client they can intercept. Reusing the key for the proxy
extends that to the proxy. A verified SSHFP match therefore proves "an
fpgas.online board or proxy at this site", not "this board". Per-board keys
would need per-board state outside the shared root and are out of scope.

The proxy also holds the mapping key, which the boards accept for `pi`. Since
`pi` has NOPASSWD sudo, **whoever controls the proxy has root on every
board**. The proxy therefore runs as its own unprivileged user, with systemd
hardening, and the mapping key is readable only by that user.

### Phase 1: the gateway proxy

**Software.** [sshpiper](https://github.com/tg123/sshpiper) with its YAML
plugin. It is not packaged in Debian (`apt-cache policy sshpiper` returns
nothing on trixie), so it is packaged for the fpgas.online apt repository
(decision D5).

**Where it listens.** The intended layout (Tim, 2026-10-03) was the proxy on
the gateway's port 22, the gateway's own sshd moved to 2222, and a catch-all
rule forwarding every unknown username to the local sshd. Two facts change
that at Welland:

- The public IPv4 address, port 22 included, belongs to the site router. The
  proxy only sees what the router forwards to it.
- The gateway's sshd is public-key only. A catch-all through sshpiper is a
  terminating proxy like any other and cannot relay public-key logins, so every
  operator, the jump account and Ansible would need their keys loaded into
  sshpiper and a mapping key into their own account. Their logins would stop
  being end to end, and a mistake in the catch-all would lock Ansible out.

Recommended (D1, D6):

- The proxy listens on its own port (`ssh_proxy_listen_port`, for example
  2022), bound to the gateway's uplink address (`--address`), so the unit
  starts `After=network-online.target`.
- The input rule accepts that port only on `iifname {{ eth_uplink }}`. Boards
  must never reach the proxy. Every board is reachable from the internet
  anyway, so the point is not board-to-board access as such: connections
  that come from the proxy carry the gateway's identity (`10.21.0.1`, which
  the boards exempt from login penalties, below) and, in phase 2, the
  transparent path. Neither should be in a board user's hands.
- The gateway's sshd stays on port 22, unchanged.
- The site router forwards public IPv4 port 22 to the proxy port.
- Unknown usernames are not forwarded anywhere. sshpiper offers every client
  the union of all pipes' methods, so an unknown name (or `pi@`) is offered a
  password prompt and refused after it ("no matching pipe"). That also means
  the proxy does not reveal which names exist.
- The yaml plugin refuses configuration files with group or other permission
  bits, so the generated files are mode 0600.
- sshpiper's `failtoban` plugin is chained after the yaml plugin
  (`sshpiperd yaml ... -- failtoban`). It sees the real client address, so a
  password guesser is blocked at the proxy instead of at the boards.

**Routing rules** are generated from `switches | port_vlan_map`: one pair per
access port (88 at Welland, empty ports included), no regex. Two pipes may
share a username; sshpiper offers the union of their methods and picks the
pipe that matches the method the client uses.

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
matches the dialled address. Upstream host keys are always pinned: the sshpiper
README warns that leaving them out disables the check. The pin is regenerated
from the NFS root on every converge. If it goes stale, sshpiper reports every
proxied login as `Permission denied (publickey)`, which is why the verify play
logs in end to end.

**Authentication to the board.**

- **Passwords** are relayed. With the published `pi` password this is the
  zero-setup path.
- **Public keys** are checked by the proxy against the keys the boards already
  trust for `pi` (the operators' GitHub keys, the server-user key, the
  controller key and the jump account key). The proxy then logs in with its
  mapping key. fixpi adds the mapping key's public half as a new key source,
  `ssh_proxy`, for `pi` only (like `jump`), never for root. Only those keys
  pass the proxy: an ordinary user's own key cannot work over IPv4 (the proxy
  has no way to know it), though it would work over IPv6 if the user adds it
  to the board. Ordinary users log in with the password on IPv4.
- **What does not work through the proxy:**
  - `pi@` and `root@`, because the proxy only knows `pi-sw<S>-p<P>` names and
    always logs in as `pi`. `pi@pi-sw1-p7.<site-domain>` therefore works over
    IPv6 and is refused over IPv4. The user docs say to always use the
    `pi-sw<S>-p<P>@` form.
  - `ansible@`. Ansible keeps using the jump route.

**Firewall.**

- Input: the proxy port, on the uplink interface only.
- Forward, IPv6: `iifname {{ eth_uplink }} oifname "v*" ip6 daddr <board
  addresses> tcp dport 22 accept`. This exposes each board's sshd, with the
  published password, to the IPv6 internet. The per-port DNAT already gives the
  same exposure over IPv4.
- Forward, IPv4 from inside the site: decision D3.

**Board side** (fixpi):

- The Ed25519-only `HostKey` drop-in.
- **Exempt the gateway from login penalties.** The same drop-in sets
  `PerSourcePenaltyExemptList` to the gateway's board-side address
  (`{{ pib_network }}.0.1`). Otherwise a few failed logins through the proxy
  would lock the web terminal, the upload page, the jump route and every
  other proxied user out of that board for up to 10 minutes. Brute-force
  protection for the IPv4 path moves to the proxy (`failtoban`). After phase 2
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

  - The root is shared, so every board carries every alias:
    `pi-sw2-p33@` also works on `pi-sw1-p7`.
  - `whoami` shows `pi`, the first entry for uid 1000, while the logs show the
    alias. sudo resolves the user from the uid, so `pi`'s sudoers rule applies.
    Verification checks this rather than assuming it.
  - The proxy rewrites the name to `pi`, so the aliases matter only on the
    direct IPv6 path, and inside the site under D3.
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

**DNS.** Phase 1 publishes the records above. Hosting and signing the external
view is decision D4. In the gateway's own zone (`pib_domain`), a new variable
adds the boards' IPv6 /56 to the `auth-zone=` line only, so the delegated zone
answers AAAA records. `dnsmasq_auth_subnet` itself is left alone, because it
also feeds `domain=`, which takes a single range. `dns-rr=` lines carry the
SSHFP records there.

**What the board pages show.** fpgas.online-gw serves `/api/boards`, and its
`ssh` object gains `user` and `host`:

```json
{"host": "pi-sw1-p7.<site-domain>", "user": "pi-sw1-p7", "port": 22, "legacy_port": 10722}
```

fpgas.online-site renders the board pages. They print
`ssh pi-sw1-p7@pi-sw1-p7.<site-domain>` first, with the legacy `-p` form
underneath until the DNAT is retired.

**Source address.** On the proxied path the board sees the gateway's address.
The legacy DNAT path shows the client's real address, so phase 1 is a
regression for proxied logins until phase 2.

**Monitoring.** If the proxy is down, IPv4 logins are gone. The proxy runs
under systemd with `Restart=on-failure`, and `verify-server.yml` checks that
it is listening and that a login through it reaches a board.

### Phase 2: transparent IPv4 source

The goal is that on a proxied connection the board sees the client's real
address, as it already does on the direct IPv6 path.

The mechanism is Linux transparent proxying, the same one as nginx
`proxy_bind $remote_addr transparent`
([tproxy.rst](https://docs.kernel.org/networking/tproxy.html)).

1. **The proxy dials the board from the client's address.**
   - Its upstream socket sets `IP_TRANSPARENT` and binds to the client's
     address, with port 0, before connecting. Port 0 lets the kernel pick the
     source port, which avoids `EADDRINUSE` on quick reconnects.
   - sshpiper has no such option. It dials upstream with a bare
     `net.Dial(network, addr)` (`cmd/sshpiperd/internal/plugin/grpc.go:418` at
     the time of writing), and it never sends PROXY headers upstream, which sshd
     could not use anyway. The package therefore carries a small patch there,
     offered upstream: a `net.Dialer` with `LocalAddr` set to the downstream
     peer's address and a `Control` hook (which runs before `bind`) that sets
     `IP_TRANSPARENT`.
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

**Failure modes.**

- **Missing policy route.** Replies are forwarded to the internet and every
  proxied login times out. The verify play checks the rule, the route and an end
  to end login whose `$SSH_CONNECTION` on the board shows a non-gateway
  address.
- **4-tuple clash.** In theory a legacy DNAT connection and a transparent
  upstream connection could share a 4-tuple: the same client address and port
  to the same board on port 22. What conntrack does then has not been tested;
  at worst one login fails. It needs the client to reuse the same source port
  on both paths at once, and it disappears when the DNAT is retired.

The IPv6 path needs no phase 2: it is already direct.

## Decisions needed

These are Tim's calls. Each lists the recommendation.

| # | Decision | Recommendation |
|---|---|---|
| D1 | Does the site router forward public IPv4 port 22 to the proxy, moving its own sshd off public port 22? Without this, the IPv4 path needs `-p` again and the board names cannot carry the public A record. It changes the documented outside-IPv4 route for operators (access.md uses `-J <you>@<site router>`), and anyone who has used the router's public name or address on port 22 gets a hard "REMOTE HOST IDENTIFICATION HAS CHANGED" failure there, because the fleet key now answers. | Yes. Operators reach the router's sshd on another port or over IPv6, and are told about the key change beforehand. |
| D2 | Which host key does the proxy present: the fleet's Ed25519 key, or its own ECDSA key with an algorithm-split SSHFP set? | The fleet key. The algorithm split makes `UpdateHostKeys` delete one path's key on every login with the other path, so default clients would be re-prompted forever. It also puts the proxy behind the same trust limits as the boards. |
| D3 | How do clients inside the site reach a board over IPv4? The internal A record needs either a forward rule from the site's networks (`ssh_direct_ipv4_sources`) to `v*` on tcp/22, or no internal A record (IPv6 only inside the site), or router hairpin NAT for port 22. | Forward rule from listed site networks: it is what the split-horizon design assumes. |
| D4 | Which public zone is `<site-domain>`, who serves it, and is it DNSSEC-signed with a DS in a signed `fpgas.online`? | A signed `welland.fpgas.online`, generated from the inventory and the NFS root's host key. |
| D5 | How is sshpiper packaged: the mirror-repo model (`fpgas-online/sshpiper` with an orphan `packaging` branch), or a pinned upstream release binary in the role? Phase 2 needs a patch. | The mirror repo, which can carry the patch. |
| D6 | Keep any "unknown username goes to the local sshd" fallback? | No. Refuse unknown names, and keep the gateway's sshd on port 22 on its own addresses. |
| D7 | Is the login name the board hostname (`pi-sw1-p7`), or also the board slug (`tt07`)? Hostnames are placements, not identities. | Hostname now. Slug aliases can come later from the same inventory. |

## Verification

Before phase 1 merges, on one board and in the CI VM:

1. **One key.**
   - `ssh-keyscan` of a board and of the proxy each return exactly one key,
     and both match the NFS root's `ssh_host_ed25519_key.pub`.
   - With a default `ssh_config`, IPv4 → IPv6 → IPv6 → IPv4 logins prompt only
     on the first connection, and known_hosts holds one line for the name after
     each step. This is the `UpdateHostKeys` case.
2. **SSHFP.** Against a signed test zone with `VerifyHostKeyDNS yes`, both paths
   report "matching host key fingerprint found in DNS".
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
   - A board cannot connect to the proxy port.
   - After several failed proxied logins to one board, the web terminal, the
     jump route and a correct proxied login to that board still work
     (`PerSourcePenalties` exemption), and the proxy's `failtoban` blocks the
     failing client.
5. **Nothing else moved.**
   - The gateway's sshd, the jump account, Ansible's route and the web terminal
     (which logs in to `pi@10.21.S.P` with the password) are unchanged.
   - The `verify-server.yml` and `verify-pi.yml` access checks pass.
   - The web terminal's host-key policy is checked in fpgas.online-site
     before boards go Ed25519-only.

Before phase 2 merges:

- On the board, `$SSH_CONNECTION` for a proxied login shows the client's
  address.
- The legacy DNAT path, NFS and the boards' outbound traffic still work.
- Removing the policy route makes the verify play fail.

## Work items

Filled in with the issue links once they are filed.
