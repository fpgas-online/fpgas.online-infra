# What a site needs from its upstream network

**You are setting up a new site, or checking an existing one, and need to know what the network above its
gateway must provide.**

A site is one gateway host, `gw.<site>.fpgas.online`, with the fleet behind
it. The gateway is the fleet's only path to anything else. These pages list
what the network **above** the gateway must provide, so that a site can be
set up without knowing how any other site's upstream is built.

fpgas.online does not manage the upstream network, and nothing in the
fpgas.online repositories may depend on a particular upstream device or name
one. The upstream is only required to meet these pages.

A gateway can sit in either of two places:

- **Behind a NAT gateway**: The gateway's uplink has a private IPv4 address. A separately managed
  router holds the site's public IPv4 address and forwards to the gateway.
  Welland is built this way.

- **Directly on a public IPv4 address**: The gateway's uplink holds the public IPv4 address itself. Nothing is
  forwarded. PS1's inventory is built this way (see the note under
  [Inbound IPv4](upstream-gateway/ipv4.md#inbound-ipv4)).

The gateway's uplink rules are the same whether it is behind a NAT gateway
or on a public address: it accepts the same ports on its uplink. What
differs between sites is the per-board port scheme, which follows how the
fleet is wired (see [Network and power](network.md)). The difference
between the two placements is only whether something upstream has to pass
the traffic on.

Each requirement on these pages says whether it is **built** (the deployed gateway
relies on it now) or **designed** (a design, merged or in draft, needs it
and no code uses it yet). A designed row says which of the two it is.

## The requirements

<a id="the-uplink"></a>
<a id="inbound-ipv4"></a>
<a id="clients-inside-the-site"></a>
- [The uplink and inbound IPv4](upstream-gateway/ipv4.md): the link, the gateway's fixed address, the ports the
  upstream lets through, and clients inside the site.

<a id="ipv6"></a>
<a id="dns"></a>
- [IPv6 and DNS](upstream-gateway/ipv6-dns.md): the gateway's address, the routed prefix, and the site's names.

<a id="outbound-from-the-gateway"></a>
- [Outbound, from the gateway](upstream-gateway/outbound.md): what the gateway fetches from the internet.

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

<a id="checking-a-site-against-this-page"></a>
## Checking a site against these pages

From a host outside the site, for each public name: `http` and `https`
answer with the gateway's own certificate; a board's ssh port answers with
the fleet's host key ([Inbound IPv4](upstream-gateway/ipv4.md#inbound-ipv4)); a camera page plays over an IPv4-only
connection (the media port) and, where the site's gateway has a global IPv6 address, over an IPv6-only one
([IPv6](upstream-gateway/ipv6-dns.md#ipv6); ps1's had none on 6 October 2026). From the gateway: package updates,
the root file system pull and certificate renewal succeed ([Outbound](upstream-gateway/outbound.md)). From inside the
site: the site name resolves and loads ([Clients inside the site](upstream-gateway/ipv4.md#clients-inside-the-site)).
