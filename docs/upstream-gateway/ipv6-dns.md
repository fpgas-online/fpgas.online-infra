# IPv6 and DNS

**You are setting up a site's upstream and need what it must provide for IPv6 and for the site's names.** Each requirement says whether it is **built** (the deployed gateway relies on it now) or **designed** (a design, merged or in draft, needs it and no code uses it yet), as on [the landing page](../upstream-gateway.md).

## IPv6

| Requirement | State | Notes |
|---|---|---|
| A global IPv6 address for the gateway's uplink | built at Welland | IPv6 clients reach the web site and the WebRTC media port on it directly, with no forwarding. ps1's gateway had no global IPv6 address on 6 October 2026 (its read), so none of the IPv6 rows holds there. |
| The upstream lets tcp 80, 443 and 22, and tcp and udp `webrtc_media_port` (8189 at Welland, [Inbound IPv4](ipv4.md#inbound-ipv4)), reach the gateway's own global IPv6 address | built | Relied on today: the web site, the camera media, and ssh for operators and deploys. Which of these Welland's upstream admits has not been re-checked since 2026-09-06, when only tcp 22 was recorded (an unmerged IPv6 design draft). |
| A prefix routed to the gateway's uplink address, large enough for one /64 per fleet switch (a /56 at Welland) | built | Each board gets one address inside its switch's /64. The gateway is the router for the prefix; the upstream only needs a route to it. The prefix is routed by a static route to the gateway's static IPv6 uplink address, and must not change. The inventory runs no prefix-delegation client. |
| The upstream does not filter the board prefix, or filters it to the same ports the gateway allows | designed (an unmerged draft) | The gateway's forward chain decides what reaches a board. An upstream filter in front of it must allow at least ICMPv6, and tcp 22, 80 and 443 to the prefix, or direct IPv6 access to boards cannot work. Both filters have to agree, and both have to be checked. |
| Reverse DNS for the prefix routed to the gateway | designed (an unmerged draft) | Needed for per-board names to have matching reverse records. |

## DNS

| Requirement | State | Notes |
|---|---|---|
| `A` records for every public name of the site pointing at the public IPv4 address, and `AAAA` records pointing at the gateway's global IPv6 address | built | At Welland: the site name, the Tiny Tapeout site (a `CNAME` to it) and the package cache name. The names live in the public `fpgas.online` zone, which is not served by the site. The package cache name and its certificate exist only where `apt_cache_enabled` is true; PS1 sets it false. |
| A resolver the gateway can use | built | `eth_uplink_dns_server`. The gateway runs its own resolver for the fleet and forwards to this one. |
| Optional: an internal zone for the fleet delegated to the gateway | built, optional | The upstream's resolver delegates a zone (`dnsmasq_auth_zone`) to the gateway with an `NS` record and glue. The glue address must be reachable from the upstream resolver. Its queries arrive on the gateway's uplink, so their source address must be listed in `firewall_dns_query_sources`. A site that does not want this leaves the `dnsmasq_auth_*` variables (zone, glue, subnet, interface) unset. |
| A public zone for per-board names, `<site>.fpgas.online`, with `SSHFP` records | designed (merged) | Which zone, who serves it and whether it is signed are open decisions. |
