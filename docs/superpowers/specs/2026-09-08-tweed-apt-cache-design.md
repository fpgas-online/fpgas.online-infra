# Design: apt caching proxy on the gateway (`apt-cache` role)

Date: 2026-09-08
Status: implemented 2026-09-13 (`roles/apt-cache`). Plan:
`docs/superpowers/plans/2026-09-13-tweed-apt-cache.md`. Its "Findings that
refine the spec" table records where the implementation departs from this
document, and why: role placement after `img`, an empty `BindAddress`, the
`apt.fpgas.online` backend, the added `rp1jtag` and Debian coverage, the
plain-http fallback, and the https limitation of chaining to ten64.

## Problem

The fpgas.online gateways run no apt cache of their own. Every `apt` fetch by
the Pi NFS root — and by the gateway itself — goes to the public internet.

Two consequences:

1. **Rebuild cost.** `roles/cam/pi/tasks/main.yml:2-8` runs a full
   `apt upgrade` of the NFS root inside the `systemd-nspawn` chroot under
   `qemu-user-static` emulation. The 2026-08-26 tweed rebuild ledger records
   this as the dominant converge cost at roughly 86 minutes, all of it
   re-downloaded on every rebuild.
2. **No local resilience.** The fleet cannot install or upgrade anything when
   the uplink is down or slow.

The estate does have a proven apt-cacher-ng role, but it lives on **ten64**
(`welland-ansible-rpi/roles/apt_proxy`), which is a different repo and a
different host. Depending on it would couple fpgas.online to ten64.

**Requirement (Tim, 2026-09-08):** tweed runs its own cache; it must *support*
forwarding upstream to ten64's proxy but must **not need it by default**. **All
apt repos are cached, no exceptions.** The cache is set up properly, with TLS.

## Non-goals

- Changing ten64's `apt_proxy` role or its SNI front-end.
- Caching anything that is not an apt repository.
- Replacing the fpgas.online apt *publishing* pipeline (`fpgas-online/apt`);
  this caches that repo, it does not publish it.

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Daemon | apt-cacher-ng | Proven in-estate; native upstream chaining via `Proxy:`. |
| Scope | Fleet-facing (running Pis on the `v*` VLANs) | Tim, 2026-09-08. |
| Chroot | Uses the cache too | Same filesystem; Tim accepted the shared path. |
| Upstream chaining | acng `Proxy:` directive, empty default | Supported, not required. |
| Coverage | Every repo, via explicit remaps | Tim: "no exceptions". |
| TLS | New name `apt.welland.fpgas.online`, own LE lineage | Tim, 2026-09-08. |

## Why remaps, not `Acquire::http::Proxy`

apt-cacher-ng will not CONNECT-tunnel HTTPS, and `/HTTPS///` passthrough is
off. A plain `Acquire::http::Proxy` therefore caches only the plain-HTTP repos
and silently bypasses the HTTPS ones. Since **every** repo must be cached, each
one needs an explicit remap and clients must be pointed at the proxy **by URL**:

```
https://apt.welland.fpgas.online/<remap>/ <suite> <components>
```

This is the same conclusion ten64's role reached, so the two stay consistent.

## Repos and remaps

| Client | Upstream | Remap | Source |
|---|---|---|---|
| Pi NFS root | `http://raspbian.raspberrypi.com/raspbian/` | `Remap-raspbian` | ported from ten64 |
| Pi NFS root | `http://archive.raspberrypi.com/debian/` | `Remap-raspberrypi` | ported from ten64 |
| Pi NFS root | `https://fpgas.online/apt` | `Remap-fpgasonline` | **new** |
| Gateway | `http://deb.debian.org/debian` | stock `Remap-debrep` | acng.conf |
| Gateway | `http://security.debian.org/debian-security` | stock security remap | acng.conf |

The raspbian remap keeps ten64's multi-host backend list
(`archive.raspbian.org`, `raspbian.raspberrypi.com`, `mirrordirector.raspbian.org`)
so a client pointed at any of those mirrors hits one cache directory.

### GitHub Pages `Range` hazard

`fpgas.online/apt` is GitHub Pages, which answers `Range` requests with `200` and
the full body rather than `206`. If a cached file under `Remap-fpgasonline` is
shorter than upstream's current `Content-Length` while carrying the same
`Last-Modified` (interrupted fetch, or content replaced in place), acng attempts a
resume, gets a `200`, and serves clients:

```
503 Server reports unexpected range
```

for that path until the stale file **and its `.head`** are removed from
`/var/cache/apt-cacher-ng/fpgasonline/`. This is a known, documented failure at
ten64. Mitigation here: document it in the role README, and have verify perform a
real fetch through the remap so the condition is caught by
`verify-server.yml` rather than by a broken converge.

## Components

### `roles/apt-cache` (new, runs in the `nbp` play)

```
defaults/main.yml
tasks/main.yml
tasks/verify/main.yml
templates/acng.conf.j2
templates/nginx-apt-cache.conf.j2
files/zzz_override.conf
files/backends_raspbian
files/backends_raspberrypi
files/backends_fpgasonline
README.md
```

Ordering: the `nbp` play already precedes the `pi` play in `ansible/site.yml`, so
the cache exists before the chroot's apt runs.

### Variables

| Variable | Default | Notes |
|---|---|---|
| `apt_cache_host` | `apt.{{ domain_name }}` | Public name; own LE lineage. `domain_name` is `welland.fpgas.online` on tweed (`host_vars/fpgas.online.yml:133`), so this yields `apt.welland.fpgas.online`. This repo has no `site` variable — that is ten64's convention. |
| `apt_cache_bind_addresses` | `{{ pib_network }}.0.1 127.0.0.1` | acng `BindAddress`. |
| `apt_cache_upstream_proxy` | `""` | Empty ⇒ direct. Set to `http://10.99.21.1:3142` to chain to ten64. Rendered as acng's `Proxy:` line **only when non-empty**. |
| `apt_cache_dir` | `/var/cache/apt-cacher-ng` | tweed has 198 GB free. |
| `apt_cache_enabled` | `true` | Escape hatch to disable per host. |

### Client rewiring

- `roles/fpgas-apt`: point `fpgas_apt_url` at
  `https://{{ apt_cache_host }}/fpgas-online`. The variable already exists
  (`roles/fpgas-apt/defaults/main.yml:6`); only its value changes.
- Pi NFS root `sources.list` and `sources.list.d/raspi.list`: rewritten to the
  `raspbian` and `raspberrypi` remaps. These files come from the stock RPi OS
  image, so the role must own them rather than patch them in place.
- The gateway's own `sources.list` is **out of scope for the first change** — see
  Risks; it is the host that runs the cache, so pointing it at itself creates a
  bootstrap ordering problem worth handling separately.

### TLS

Follows `roles/site/tasks/certbot.yml` exactly:

- `certbot certonly --webroot -w /var/www/html`, never `certbot --nginx`. That
  file's header (lines 1-22) records why: the nginx plugin rewrote the vhost and
  emitted an IPv4-only `listen 443 ssl`, so IPv6 clients were handed the
  tinytapeout certificate.
- The vhost is rendered by Ansible, twice: plain-HTTP first so http-01 can be
  answered, then again with the TLS server once the lineage exists.
- tweed already has a working `00-http-default.conf` ACME default server and two
  live lineages (`welland.fpgas.online`, `tinytapeout.fpgas.online`).

### DNS (split horizon)

- **Public** `apt.welland.fpgas.online` → the welland public address, reaching
  tweed's port 80 for the ACME challenge.
- **Pool** — a dnsmasq `host-record` resolving the same name to
  `{{ pib_network }}.0.1`, so Pis reach the cache directly on the gateway
  instead of hairpinning out through ten64 and back.

## Testing

- `tasks/verify/main.yml`: acng installed; service active; **actually listening**
  on `{{ pib_network }}.0.1:3142`; nginx vhost present; certificate present
  (skipped where `site_certbot` is false, as CI has no cert); and a **real fetch
  through each remap** — a `Release` file per remap, which is what catches the
  Range hazard and a bad backend.
- Wire the role into `ansible/verify-server.yml` alongside the existing entries.
- VM CI: the QEMU harness runs the same `site.yml`, so the role must converge
  without a certificate. `apt_cache_enabled` and the existing `site_certbot`
  pattern cover that.
- A converge-twice run must show `changed=0` on the second pass.

## Risks

1. **The cache is now on the rebuild critical path.** Tim chose the shared
   configuration deliberately. Because sources are rewritten by URL, a broken or
   stopped acng breaks apt for the NFS root, which breaks converges and rebuilds.
   Guards: the `nbp`-before-`pi` ordering; verify fetches through each remap; and
   `apt_cache_enabled: false` reverts clients to upstream URLs.
2. **Cert issuance has prerequisites outside this repo** — the public DNS record
   in the fpgas.online zone on ns1, and the welland inbound path for http-01.
   Until both exist the lineage cannot issue. The role must therefore converge
   cleanly *without* the certificate and light up once DNS lands.
3. **Gateway self-reference.** Pointing tweed's own `sources.list` at a cache
   that tweed hosts is a chicken-and-egg on a fresh install; deliberately
   deferred, so "no exceptions" is met for the fleet first and the gateway
   second.
4. **Suite drift.** `fpgas_apt_suite` is `bookworm` while tweed runs trixie; the
   remap is suite-agnostic, but the client lines are not.

## Open questions

- Should the gateway's own `sources.list` be rewritten in a follow-up (risk 3)?
- `ps1.fpgas.online` is also in the `nbp` group and has no `domain_name`. First
  implementation ships `apt_cache_enabled: false` for ps1 so the role is inert
  there; enabling it is a separate decision with its own DNS and cert work.
- If PR #56 (`split-prep`, per-site identity vars) merges first, `apt_cache_host`
  should be rederived from those vars rather than from `domain_name`.

## References

- ten64's role: `tweed-split-design/data/welland-ansible-rpi/roles/apt_proxy/`
  (README documents the remap requirement and the Range hazard).
- `roles/site/tasks/certbot.yml` — the certbot pattern and its history.
- `roles/cam/pi/tasks/main.yml:2-8` — the `apt upgrade` this caches.
