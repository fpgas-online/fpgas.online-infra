# The uplink and inbound IPv4

**You are setting up a site's upstream and need the uplink it must give the gateway, and the IPv4 ports it must let through to it.** Each requirement says whether it is **built** (the deployed gateway relies on it now) or **designed** (a design, merged or in draft, needs it and no code uses it yet), as on [the landing page](../upstream-gateway.md).

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
| tcp `<s><pp>22` and `<s><pp>44` for switch `s`, port `pp` | Per-board ssh and the per-board auxiliary port. The gateway forwards each to its board. See [Network and power](../network.md) for the formula | built |
| tcp 22 | Logging in to a board by name (`ssh pi-sw2-p47@…`) without a port number: a username-routing ssh proxy on the upstream gateway forwards logins under board names (`pi…`) to the site gateway's ssh proxy, and other names stay with the upstream gateway | designed (decided 2026-10-04) |
| tcp 2222 | The upstream gateway's own sshd (backup) | designed (decided 2026-10-04) |
| tcp 2223 | Forwarded to the site gateway's own sshd on port 22 (backup): operators and deploys over IPv4 | designed (decided 2026-10-04) |
| tcp 2224 | Forwarded to the site gateway's ssh proxy, which listens on a port of its own beside the gateway's sshd; that port is not fixed yet (backup) | designed (decided 2026-10-04) |

Notes on the table:

- The WebRTC media port is built at Welland only. The firewall opens it
  only on a host that defines `webrtc_media_port`, and the web tier runs
  the WebRTC role only on a host that defines `webrtc_additional_hosts`.
  PS1 defines neither, so PS1 does not run WebRTC.
- The gateway forwards the per-board ports to the boards (built, in its firewall). Whether a site's
  upstream forwards them on to the gateway is the upstream's part; at Welland it did not when last
  checked, on 2026-09-06 (an earlier note in fpgas.online-docs; not re-checked since), so per-board ssh from outside
  over IPv4 may not work there. Note that the
  per-board port scheme differs between sites: Welland uses `<s><pp>22`
  and `<s><pp>44` with the forward policy set to drop, and PS1's legacy
  scheme uses `<100+N>22` and `<100+N>44`.
- The tcp 22 and 2222 to 2224 rows are from the ssh proxy design
  ([`docs/superpowers/specs/2026-10-03-ssh-username-proxy-design.md`](../superpowers/specs/2026-10-03-ssh-username-proxy-design.md),
  which recommends them; the design is on hold) for Welland, which is behind an upstream
  router. The tcp 22 and 2222 rows end on the upstream gateway and do not
  reach the site gateway directly. A site whose gateway is directly on a
  public address has no upstream proxy; how its port 22 is shared between
  sshd and the ssh proxy is not decided.
- At Welland none of those four rows is built (the design is on hold). Operators and deploys reach the
  site gateway's sshd over IPv6 (`ansible_host: gw.welland.fpgas.online` in `host_vars/fpgas.online.yml`).
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
gateway's uplink address, and the site's names must resolve to it for them ([DNS](ipv6-dns.md#dns)).
At Welland the gateway offers its uplink address as a WebRTC candidate for
the same reason (`webrtc_additional_hosts`).
