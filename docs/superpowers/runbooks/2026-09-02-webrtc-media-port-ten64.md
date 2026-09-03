# Runbook: open the WebRTC media port on ten64 (IPv4 DNAT)

Date: 2026-09-02. Companion to `roles/cam/webrtc` (mediamtx WHEP streaming).

## Why a manual step exists

WebRTC media (ICE) runs on UDP (with TCP fallback) on a single muxed port,
`webrtc_media_port` (8189), terminated by mediamtx on tweed. IPv6 viewers
reach tweed directly (`2404:e80:a137:2100::1`; tweed's own nftables input
accepts the port - roles/firewall). IPv4 viewers must come through the one
public address, ten64.welland's `87.121.95.37` - and ten64 is **not managed
by this repository**: its nginx SNI proxy handles only TCP :443
(`/etc/nginx/HTTPS-SNI-PROXY.md` on ten64), which WebRTC's UDP media cannot
ride. WHEP *signalling* needs nothing here: it is plain HTTPS, proxied like
every other request to `/cam/<host>/whep`.

## The change (on ten64, by hand)

Static port-forwards live in the `published_dnat4` map in
`/etc/nftables.d/zones.nft` (forward traffic is auto-accepted by the
`ct status dnat` rule in `fw.nft`). Add the tweed transit address for both
protocols:

```
    udp . 8189 : 10.99.21.2,        # WebRTC media -> tweed mediamtx
    tcp . 8189 : 10.99.21.2,        # (TCP fallback for UDP-blocked clients)
```

then `sudo nft -f /etc/nftables.conf` and commit /etc (etckeeper).

## Verification

- From a host outside welland:
  `nc -u -z -w2 87.121.95.37 8189` proves nothing by itself (ICE only
  answers valid STUN), so instead open a board page and check
  `chrome://webrtc-internals` shows a succeeded candidate pair to
  `87.121.95.37:8189` (IPv4 client) or `2404:e80:a137:2100::1:8189` (IPv6).
- On tweed: `sudo nft list chain inet filter input | grep 8189` shows the
  accepts and their packet counters moving while a WHEP session runs.
- On ten64: `sudo nft list map inet fw published_dnat4` includes the two
  8189 entries; `sudo nft list counters | grep dnat_fwd` moves.

## Deploy ordering (whole WebRTC series)

1. infra: this branch (mediamtx role + firewall + vhost include) converges
   tweed; then the manual ten64 DNAT above.
2. fpgas.online-cam rtsp-publish deb rolls out to the Pis (the tee probes
   the RTSP port, so the wrong order is inert, not broken).
3. fpgas.online-site WHEP player.
