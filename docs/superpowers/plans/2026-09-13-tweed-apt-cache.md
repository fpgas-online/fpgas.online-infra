# Gateway apt cache (`apt-cache` role) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** tweed runs its own apt-cacher-ng, served as `https://apt.welland.fpgas.online`, and every apt source of the Pi NFS root goes through it.

**Architecture:** A new `apt-cache` role in the `nbp` play (after `img`, before `fixpi`) installs apt-cacher-ng with data-driven remaps, fronts it with an Ansible-owned nginx vhost and a `certbot certonly --webroot` lineage, and publishes one fact, `apt_cache_url`, that every apt client in the repo derives its source URL from. Clients: the NFS root's `sources.list`/`raspi.list` (owned by the role), fixpi's Debian armmp source, the fpgas-apt repository, and a dnsmasq `host-record` for running Pis.

**Tech Stack:** Ansible, apt-cacher-ng, nginx, certbot (webroot), dnsmasq.

**Spec:** `docs/superpowers/specs/2026-09-08-tweed-apt-cache-design.md`

## Global Constraints

- Every repo the NFS root uses goes through the cache: "All apt repos should be cached, no exceptions."
- Upstream chaining via acng `Proxy:`; empty by default (`apt_cache_upstream_proxy: ""`), rendered only when non-empty.
- TLS: `certbot certonly --webroot -w /var/www/html`, never `certbot --nginx`; every TLS server block has both `listen 443 ssl` and `listen [::]:443 ssl`.
- The role must converge cleanly without a certificate (CI VM, and prod until public DNS exists).
- ps1 ships `apt_cache_enabled: false`.
- No QEMU-specific workarounds; the VM test runs the same roles.
- Variables and registers carry the `apt_cache_` prefix (ansible-lint `var-naming[no-role-prefix]`).
- PR only; not deployed.

## Findings that refine the spec (verified 2026-09-13)

| Spec said | Reality | Plan |
|---|---|---|
| Role in `nbp` play | nginx is installed by `site` in the later `pig` play; fixpi runs chroot `apt` in the `nbp` play | Role goes after `img`, before `fixpi`, and installs nginx itself |
| `BindAddress: {{ pib_network }}.0.1 127.0.0.1` | ps1's `pib_network` is `10.21.0` (tweed's `10.21`); ten64's acng with an explicit BindAddress listens on 127.0.0.1 only | `apt_cache_bind_addresses: ""` (acng listens on all addresses); the nftables input chain already drops new connections to 3142 from the uplink |
| `templates/acng.conf.j2` | Stock acng.conf already carries `Remap-debrep`/`Remap-secdeb`; `zzz_override.conf` is read last | Leave acng.conf stock; template `zzz_override.conf` only |
| `Remap-fpgasonline` → `https://fpgas.online/apt` | That URL 301s to `https://apt.fpgas.online/` | Backend is `https://apt.fpgas.online/` |
| Repos: raspbian, raspberrypi, fpgas | Live NFS root also has `debian-armmp.sources` (deb.debian.org) and a hand-applied `rp1-jtag.list` (draft PR #48) | Debian goes via stock `/debian`; add `Remap-rp1jtag` |
| Key fetch through the cache | acng 403s `raspbian.public.key` but passes `*.gpg` (404 upstream, not 403) | `pubkey.gpg` fetch goes through the cache |
| Chain to ten64 | ten64 acng answers CONNECT with 403 | Chaining works for http upstreams only; documented in README |
| `sources.list` rewritten "by the role" | `img` re-extracts the stock image on a fresh install | Rendered by the role, which runs after `img` |

Client URL rule (`apt_cache_url`):

- disabled → `""` (clients keep upstream URLs)
- certificate present → `https://{{ apt_cache_host }}`
- otherwise → `http://{{ eth_local_address }}:{{ apt_cache_port }}` (still cached, no DNS or cert needed; this is the CI path)

---

### Task 1: apt-cacher-ng with data-driven remaps

**Files:**
- Create: `ansible/roles/apt-cache/defaults/main.yml`, `handlers/main.yml`, `tasks/main.yml`, `tasks/facts.yml`, `tasks/server.yml`, `templates/zzz_override.conf.j2`, `templates/backends.j2`

**Interfaces:**
- Produces: `apt_cache_remaps` (list of `{name, path, aliases, backend, probe}`), facts `apt_cache_url`, `apt_cache_host`, `apt_cache_cert_exists`.

- [ ] Write defaults with remaps raspbian, raspberrypi, fpgasonline, rp1jtag, plus `apt_cache_stock_probes` for `/debian`.
- [ ] Template `zzz_override.conf` (Port, optional BindAddress, optional Proxy, one `Remap-` per entry) and one `backends_<name>` per entry.
- [ ] Smoke test before wiring: render both templates into `tmp/acng/`, run `/usr/sbin/apt-cacher-ng -c tmp/acng ForeGround=1 Port=13142 CacheDir=... LogDir=...` locally, and `curl` each `http://127.0.0.1:13142/<path>/<probe>`. Expected: 200 for every remap and for `/debian/dists/bookworm/Release`.
- [ ] Commit.

### Task 2: nginx vhost + certificate

**Files:**
- Create: `ansible/roles/apt-cache/tasks/tls.yml`, `templates/nginx-apt-cache.conf.j2`

- [ ] Install nginx-extras (same flavour as `site`), ACME webroot, certbot only if `/usr/bin/certbot` is absent.
- [ ] Render the vhost (plain first); gate certbot on `apt_cache_certbot` and on `getent ahosts {{ apt_cache_host }}` succeeding, warn otherwise.
- [ ] Re-render with TLS when the lineage was just issued, recompute `apt_cache_url`, flush handlers.
- [ ] `nginx -t` passes (checked in verify); commit.

### Task 3: point every client at the cache

**Files:**
- Create: `ansible/roles/apt-cache/tasks/nfsroot.yml`, `templates/sources.list.j2`, `templates/raspi.list.j2`
- Modify: `ansible/site.yml` (add role), `ansible/inventory/host_vars/ps1.fpgas.online.yml` (disable), `ansible/roles/fixpi/templates/apt/debian-armmp.sources.j2`, `ansible/roles/fixpi/tasks/sunxi.yml` (keyring URL), `ansible/roles/fpgas-apt/defaults/main.yml`, `ansible/roles/fpgas-apt/tasks/main.yml` (single-owner list file), `ansible/roles/pxe/templates/dnsmasq-base.conf.j2` (host-record)

- [ ] Each client derives its URL from `apt_cache_url`, falling back to today's upstream URL when it is empty or undefined (ci-nfsroot.yml has no apt-cache role).
- [ ] fpgas-apt reads the fact from `hostvars[groups['nbp'][0]]`, the pattern `onpi/tasks/tt.yml` already uses.
- [ ] `yamllint` + `ansible-lint` clean; `ansible-playbook --syntax-check`; commit.

### Task 4: verification

**Files:**
- Create: `ansible/roles/apt-cache/tasks/verify/main.yml`
- Modify: `ansible/verify-server.yml`

- [ ] Assert: package installed, service active, a listener on 3142 reachable at `eth_local_address`, a `Release` fetch through every remap returns 200, the TLS path returns 200 with a valid certificate when one exists, and every `deb`/`URIs:` in the NFS root starts with `apt_cache_url`.
- [ ] Commit.

### Task 5: docs + PR

- [ ] `ansible/roles/apt-cache/README.md`: prerequisites (ns1 record), Range hazard, chaining limitation, `--limit` caveat.
- [ ] Update the spec's status and point at this plan.
- [ ] Push, open PR, watch CI to green. Do not merge or deploy.
