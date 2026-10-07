# What a site needs from its upstream network

**You are setting up a new site, or checking an existing one, and need to know what the network above its
gateway must provide.**

A site is one gateway host, `gw.<site>.fpgas.online`, with the fleet behind
it. The gateway is the fleet's only path to anything else. This page lists
what the network **above** the gateway must provide, so that a site can be
set up without knowing how any other site's upstream is built.

fpgas.online does not manage the upstream network, and nothing in the
fpgas.online repositories may depend on a particular upstream device or name
one. The upstream is only required to meet this page.

A gateway can sit in either of two places:

- **Behind a NAT gateway**: The gateway's uplink has a private IPv4 address. A separately managed
  router holds the site's public IPv4 address and forwards to the gateway.
  Welland is built this way.

- **Directly on a public IPv4 address**: The gateway's uplink holds the public IPv4 address itself. Nothing is
  forwarded. PS1's inventory is built this way (see the note under
  [Inbound IPv4](#inbound-ipv4)).

The gateway's uplink rules are the same whether it is behind a NAT gateway
or on a public address: it accepts the same ports on its uplink. What
differs between sites is the per-board port scheme, which follows how the
fleet is wired (see [Network and power](network.md)). The difference
between the two placements is only whether something upstream has to pass
the traffic on.

Each requirement below says whether it is **built** (the deployed gateway
relies on it now) or **designed** (a design, merged or in draft, needs it
and no code uses it yet). A designed row says which of the two it is.

## The uplink

| Requirement | State | Notes |
|---|---|---|
| One Ethernet link from the gateway's uplink interface to the upstream network, a standard 1500-byte link | built | The fleet's VLANs never appear on this link. |
| A fixed IPv4 address, a default route and a DNS resolver for the gateway, given in the inventory | built | `eth_uplink_static_*` and `eth_uplink_dns_server`. The inventory has no working DHCP uplink: the firewall and the web application use `eth_uplink_static_address` unconditionally. |
| Outbound IPv4 from that address to the internet | built | The gateway masquerades the whole fleet behind its uplink address, so the upstream sees one source address. Behind a NAT gateway, the upstream NATs it once more. |
| The address must not change | built | The gateway's firewall forwards per-board ports addressed to `eth_uplink_static_address`, and the web application lists it among its allowed host names. |
| ICMP and ICMPv6 are not filtered | built | Path MTU discovery needs them. |

## Inbound IPv4

"Reaches the gateway" means: on a public address, the port is simply open to
the internet; behind a NAT gateway, the upstream forwards the port on the
public IPv4 address to the gateway's uplink address, on the same port
number except where a row says otherwise.

| Public port | What it is for | State |
|---|---|---|
| tcp 80 | The web site, and Let's Encrypt `http-01` challenges (`/.well-known/acme-challenge/`) for every public name of the site | built |
| tcp 443 | The web site, the web terminal and the camera players. **TLS ends on the gateway**: an upstream that proxies must pass TLS through untouched (route on the SNI name), not terminate it | built |
| udp and tcp `webrtc_media_port` (8189) | WebRTC camera media. Signalling rides on 443; the media does not, and cannot go through an HTTP or TLS proxy | built at Welland |
| tcp `<s><pp>22` and `<s><pp>44` for switch `s`, port `pp` | Per-board ssh and the per-board auxiliary port. The gateway forwards each to its board. See [Network and power](network.md) for the formula | built |
| tcp 22 | Logging in to a board by name (`ssh pi-sw2-p47@…`) without a port number: a username-routing ssh proxy on the upstream gateway forwards logins under board names (`pi…`) to the site gateway's ssh proxy, and other names stay with the upstream gateway | designed (decided 2026-10-04) |
| tcp 2222 | The upstream gateway's own sshd (backup) | designed (decided 2026-10-04) |
| tcp 2223 | Forwarded to the site gateway's own sshd on port 22 (backup): operators and deploys over IPv4 | designed (decided 2026-10-04) |
| tcp 2224 | Forwarded to the site gateway's ssh proxy, which listens on a port of its own beside the gateway's sshd; that port is not fixed yet (backup) | designed (decided 2026-10-04) |

Notes on the table:

- The WebRTC media port is built at Welland only. The firewall opens it
  only on a host that defines `webrtc_media_port`, and the web tier runs
  the WebRTC role only on a host that defines `webrtc_additional_hosts`.
  PS1 defines neither, so PS1 does not run WebRTC.
- The gateway forwards the per-board ports to the boards. At Welland the
  upstream did not forward them when last checked (2026-09-06), so
  per-board ssh from outside over IPv4 does not work there. Note that the
  per-board port scheme differs between sites: Welland uses `<s><pp>22`
  and `<s><pp>44` with the forward policy set to drop, and PS1's legacy
  scheme uses `<100+N>22` and `<100+N>44`.
- The tcp 22 and 2222 to 2224 rows are decision D1 of the ssh proxy
  design, decided 2026-10-04 for Welland, which is behind an upstream
  router. The tcp 22 and 2222 rows end on the upstream gateway and do not
  reach the site gateway directly. A site whose gateway is directly on a
  public address has no upstream proxy; how its port 22 is shared between
  sshd and the ssh proxy is not decided.
- Today's state, as of 2026-10-04: at Welland none of this is built. Public
  IPv4 port 22 is answered by the upstream router's own sshd, so a client
  there sees that router's host key. Operators reach the site gateway's
  sshd over IPv6, and deploys will too once the open inventory change in
  fpgas.online-infra merges.
- If the upstream is an HTTP reverse proxy for port 80 rather than a plain
  port forward, it must pass the `Host` header on: the gateway's virtual
  hosts are selected by it.

> [!NOTE]
> A comment in `ansible/web.yml` (main, read 2026-10-07) says ps1 sits behind CGNAT. The ps1 gateway read
> on 6 October 2026 found its uplink on the public address 76.227.131.147/25, as its `host_vars` say
> ([The ps1 gateway and switch](https://docs.fpgas.online/en/latest/sites/ps1-gateway.html)), so the
> `web.yml` comment is out of date.

### Clients inside the site

A client on the upstream's own LAN usually cannot reach the public address
and be forwarded back in ("hairpin"). Such clients need a route to the
gateway's uplink address, and the site's names must resolve to it for them.
At Welland the gateway offers its uplink address as a WebRTC candidate for
the same reason (`webrtc_additional_hosts`).

## IPv6

| Requirement | State | Notes |
|---|---|---|
| A global IPv6 address for the gateway's uplink | built | IPv6 clients reach the web site and the WebRTC media port on it directly, with no forwarding. |
| The upstream lets tcp 80, 443 and 22, and tcp and udp `webrtc_media_port`, reach the gateway's own global IPv6 address | built | Relied on today: the web site, the camera media, and ssh for operators and deploys. The draft IPv6 design recorded Welland's upstream admitting only tcp 22 to the gateway over IPv6 on 2026-09-06. That has to be re-checked. |
| A prefix routed to the gateway's uplink address, large enough for one /64 per fleet switch (a /56 at Welland) | built | Each board gets one address inside its switch's /64. The gateway is the router for the prefix; the upstream only needs a route to it. The prefix is routed by a static route to the gateway's static IPv6 uplink address, and must not change. The inventory runs no prefix-delegation client. |
| The upstream does not filter the board prefix, or filters it to the same ports the gateway allows | designed (draft) | The gateway's forward chain decides what reaches a board. An upstream filter in front of it must allow at least ICMPv6, and tcp 22, 80 and 443 to the prefix, or direct IPv6 access to boards cannot work. Both filters have to agree, and both have to be checked. |
| Reverse DNS for the prefix routed to the gateway | designed (draft) | Needed for per-board names to have matching reverse records. |

## DNS

| Requirement | State | Notes |
|---|---|---|
| `A` records for every public name of the site pointing at the public IPv4 address, and `AAAA` records pointing at the gateway's global IPv6 address | built | At Welland: the site name, the Tiny Tapeout site (a `CNAME` to it) and the package cache name. The names live in the public `fpgas.online` zone, which is not served by the site. The package cache name and its certificate exist only where `apt_cache_enabled` is true; PS1 sets it false. |
| A resolver the gateway can use | built | `eth_uplink_dns_server`. The gateway runs its own resolver for the fleet and forwards to this one. |
| Optional: an internal zone for the fleet delegated to the gateway | built, optional | The upstream's resolver delegates a zone (`dnsmasq_auth_zone`) to the gateway with an `NS` record and glue. The glue address must be reachable from the upstream resolver. Its queries arrive on the gateway's uplink, so their source address must be listed in `firewall_dns_query_sources`. A site that does not want this leaves the `dnsmasq_auth_*` variables (zone, glue, subnet, interface) unset. |
| A public zone for per-board names, `<site>.fpgas.online`, with `SSHFP` records | designed (merged) | Which zone, who serves it and whether it is signed are open decisions. |

## Outbound, from the gateway

The gateway must be able to reach the internet generally: outbound https,
and http for the Debian and Raspberry Pi package hosts.
The hosts below are examples of what it fetches, and why.

| The gateway fetches | For |
|---|---|
| `deb.debian.org`, `archive.raspbian.org`, `archive.raspberrypi.com` (http), and `apt.fpgas.online` | Its own packages and the package cache it runs for the fleet |
| `ghcr.io` | The prebuilt fleet root file system |
| `github.com`: public ssh keys, release assets, `git+https` clones | Operators' ssh keys; the mediamtx tarball and the Tiny Tapeout commander releases; the site and PoE control packages |
| `raw.githubusercontent.com` | One service unit file fetched while preparing the fleet root |
| A Python package index | The site's `pip` installs |
| Let's Encrypt | Certificates |
| NTP servers, outbound udp 123 (the gateway runs chrony with Debian's default pool; no site setting names a time server) | Its clock, which the fleet takes from it |

An upstream package cache is **not** required. A site may point the gateway
at one (`apt_client_proxy`), as an optimisation only.

## What the upstream is never asked for

- The fleet's VLANs, DHCP, TFTP or NFS. They exist only between the gateway
  and the fleet switches.
- Access to the fleet switches' management or PoE control. The gateway does
  that.
- Running any fpgas.online software, or holding any fpgas.online
  credential.

## Deploying from outside the site

Ansible must be able to reach the gateway's ssh as the `ansible` account
from wherever the operator runs it: by the gateway's public name, over IPv6
or, once built, over IPv4 on port 2223. A site must not need an operator to
be on the upstream network to deploy.

At welland the inventory reaches the gateway by its public name over IPv6 (`ansible_host:
gw.welland.fpgas.online` in `host_vars/fpgas.online.yml`, main, read 2026-10-07).

## Checking a site against this page

From a host outside the site, for each public name: `http` and `https`
answer with the gateway's own certificate; a board's ssh port answers with
the fleet's host key; a camera page plays over an IPv4-only connection (the
media port) and over an IPv6-only one. From the gateway: package updates,
the root file system pull and certificate renewal succeed. From inside the
site: the site name resolves and loads.
