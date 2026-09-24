# apt-cache

apt-cacher-ng on the gateway, served as `https://apt.<domain_name>`
(`apt.welland.fpgas.online` on tweed), so that every apt fetch by the Pi NFS
root is cached locally instead of re-downloaded from the internet on every
rebuild.

- Design: `docs/superpowers/specs/2026-09-08-tweed-apt-cache-design.md`
- Plan: `docs/superpowers/plans/2026-09-13-tweed-apt-cache.md`

## What it does

Runs in the `nbp` play of `site.yml`, after `img` and before `fixpi`.

1. Installs apt-cacher-ng and writes `/etc/apt-cacher-ng/zzz_override.conf`
   (port, optional `BindAddress`, optional upstream `Proxy`, one `Remap-` per
   entry in `apt_cache_remaps`) plus one `backends_<name>` file per remap.
   The package's own `acng.conf` is left alone; it supplies the stock
   `/debian` remap.
2. Installs nginx and renders `/etc/nginx/sites-enabled/<apt_cache_host>.conf`:
   ACME http-01 on port 80, TLS on 443 (v4 and v6) proxying to acng.
3. Requests a certificate with `certbot certonly --webroot`, but only once the
   name resolves (see Prerequisites).
4. Publishes the fact `apt_cache_url` and renders the NFS root's
   `sources.list` and `sources.list.d/raspi.list` from it.

`apt_cache_url` is what every client derives its source from:

| State | `apt_cache_url` |
|---|---|
| `apt_cache_enabled: false` | `""`, so clients keep their upstream URLs |
| certificate present | `https://<apt_cache_host>` |
| otherwise | `http://<eth_local_address>:3142`: still cached, needs no DNS or certificate |

Clients: this role (Raspbian, Raspberry Pi archive), `fixpi` (the Debian armmp
source and its keyring), `fpgas-apt` (via `hostvars[groups['nbp'][0]]`), and
`pxe` (a dnsmasq `host-record` so running Pis resolve the name to the gateway).

## Adding a repository

acng will not CONNECT-tunnel HTTPS, so **a repository without a remap cannot be
cached**. To add one:

1. Add an entry to `apt_cache_remaps` (`name`, `path`, `aliases`, `backend`,
   `probe`). Use the canonical backend URL, never one that redirects.
2. Point the client at `{{ apt_cache_url }}/<path>/`.

`verify/main.yml` fails if any `deb` or `URIs:` line in the NFS root does not
start with `apt_cache_url`.

## Prerequisites (outside this repo)

- **Public DNS**: an `apt.welland.fpgas.online` record in the fpgas.online zone
  on ns1, pointing at welland's public address (as `welland.fpgas.online`
  does). ten64 already forwards `*.welland.fpgas.online`, both http and SNI
  https, to tweed, so nothing else is needed for http-01 or for clients.
  Until the record exists the converge prints a warning and clients use the
  plain-http address. The first converge after it exists issues the
  certificate and moves every client to https.

## Operational notes

### Range hazard (GitHub Pages remaps: `fpgasonline`, `fpgatools`, `rp1jtag`)

GitHub Pages answers `Range` requests with `200` and the full body. If a
cached file is shorter than upstream's `Content-Length` but carries the same
`Last-Modified` (an interrupted fetch, or content replaced in place), acng
tries to resume, gets a `200`, and serves clients
`503 Server reports unexpected range` for that path until the file **and its
`.head`** are deleted from `/var/cache/apt-cacher-ng/<remap name>/`.
`verify-server.yml` fetches a Release file through every remap, so this shows
up there.

### Chaining to ten64 (`apt_cache_upstream_proxy`)

Setting `apt_cache_upstream_proxy: http://10.99.21.1:3142` sends every fetch
through ten64's apt-cacher-ng. The http upstreams (raspbian, raspberrypi,
debian) work. The https ones (fpgasonline, rp1jtag) **fail**: acng reaches an
https backend through a proxy with CONNECT, and ten64's acng refuses it
(`503 CONNECT denied (ask the admin to allow HTTPS tunnels)`, tested
2026-09-13). The default is empty, i.e. direct.

### `--limit` and partial runs

The facts are tagged `always`, so `--tags fixpi` or `--tags pi` still see
`apt_cache_url`. But the `pi` play reads it from the gateway host, so a run
limited to the `pi` host alone (without `fpgas.online`) falls back to the
upstream URL and rewrites `fpgas-online.list` back to upstream. Include the
gateway in the limit: `--limit fpgas.online,pi`.

### The cache is on the rebuild path

A stopped or broken acng breaks apt for the NFS root, and with it converges.
To take the cache out of the path, set `apt_cache_enabled: false` and
converge: every client, including the two NFS root files this role renders,
reverts to its upstream URL.
