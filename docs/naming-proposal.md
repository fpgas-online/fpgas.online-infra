# Naming proposal

Status: proposal only. Nothing here has been renamed yet.
Based on `origin/main` at `4de0b24` (2026-09-29).

Many names here come from CarlFK/pici and, before that, the Debian videoteam
Ansible (`fixpi`, `onpi`, `nbp`, `pig`, `pib`, `conference_name`, `tweeks`).
They no longer describe what the code does in fpgas.online. This document
lists every named thing, what it really is (taken from the code), a proposed
name and how far each rename reaches.

**Blast radius codes** (used in every table):

| code | meaning |
|---|---|
| **D** | deploy-affecting: operator commands (tags, `--limit`, playbook paths), inventory groups, or a var that host_vars, group_vars or `-e` may set. After the rename, Ansible silently ignores the old var name. |
| **C** | CI-affecting: `tests/`, `.github/workflows`, `tests/ci/nfsroot_inputs.py` paths (a role-directory rename changes the NFS root image input key, so CI builds a new image) |
| **X** | cosmetic: task names, play names, comments, file names inside a role that nothing outside references |
| **M** | migration: a live path, unit name or file on a deployed host, or a name another repo uses. See section 4. |
| – | keep: the name is already short and descriptive |

---

## 1. Conventions

| subject | rule |
|---|---|
| Role names | Name the role after the **component** it manages, in `snake_case`, 1–2 words. Do not prefix by host. The one exception is the Pi NFS root, which is spread across several roles: every role that writes into the root gets the prefix `nfsroot_` (as `nfsroot_generation` already has). The one camera role that runs inside the root keeps its component name (`cam_pi`). |
| Where a role runs | Record where a role runs in the playbook that uses it, not in its name. The gateway roles are in `site.yml`, the web tier roles are in `web.yml`, and the image roles are in `ci-nfsroot*.yml`. Each role's `README.md` or `meta/main.yml` description says which of these runs it. |
| Variable prefix | Every var a role defines (defaults, vars, `set_fact`, `register`) starts with `<role>_`. ansible-lint `var-naming[no-role-prefix]` enforces this, so renaming a role renames all its vars. |
| Inventory vars | Vars that several roles read (site facts) use a short **topic prefix**: `nfsroot_`, `raspios_`, `lan_`, `site_`, `eth_uplink_`, `eth_local_`, `fleet_`, `tt_`. The word `site` is used **only** for the location (welland, ps1) and never for the Django app. |
| Tags | Each role entry in a playbook carries **one tag, identical to the role name** (underscores). Add sub-tags only when an operator runs that part alone. A sub-tag is one short topic word that is unique in the repo (`netboot`, `keys`, `tls`, `sunxi`). Feature tags that span roles stay: `verify`, `django`, `fleet`, `tt`, `streaming`, `always`. Delete every other tag. |
| Handler names | `Restart <unit>` or `Reload <unit>`, using the systemd unit name. Add a ` (<role>)` suffix only when two roles in one play need different actions under the same name. |
| Task names | See the style rule below. |
| Task files | Name each task file for what it does, in `kebab-case` (the most common style in the repo today), e.g. `pi-password.yml` rather than `userconf.yml`. |

**Task-name style rule**

1. Start with an imperative verb and use sentence case, e.g. "Install nfs-kernel-server". Do not use noun phrases like "Site directories" or "Vhost".
2. Name the object, not the file, unless the file is the object (`/etc/exports`).
3. Say where the task acts when that is not the play's host, e.g. "... in the NFS root" or "... on the gateway".
4. Keep names to 60 characters or fewer. Reasons belong in a `#` comment, not in the name.
5. For verification, use "Check X" for the probe that registers and "Assert X" for the assertion. Do not prefix with "Verify:", because the include is already named "Verify <role>".
6. Make every name unique within its role, so there are no two "Enable services" or two "Systemd files".
7. No slang or pici leftovers ("cuz", "netbootie", "and friends", "Apt update/upgrade", "Tweeks").

---

## 2. Inventory and proposals

### 2.1 Roles (29)

| current | proposed | alternatives | what it is | blast |
|---|---|---|---|---|
| `apt_cache` | keep | – | apt-cacher-ng + nginx TLS front end on the gateway; repoints the NFS root's apt sources at it | – |
| `apt_client` | keep | – | the gateway's own apt proxy config (`01site-proxy`) | – |
| `automation_user` | keep | – | the `ansible` automation account on the gateway | – |
| `cam_pi` | keep | `nfsroot_cam` | installs GStreamer and `fpgas-online-cam` into the NFS root, enables `cam.service` | – |
| `firewall` | keep | `nftables` | nftables rules + IPv4/IPv6 forwarding | – |
| `fixpi` | **`nfsroot_config`** | `nfsroot_site`, `nfsroot_netboot` | file-level edits to the Pi root from outside (no ARM code except CI). In CI it makes the root netbootable (cmdline, fstab, users, TFTP tree, Orange Pi kernel). On the gateway it applies the site layer (pi password, authorized_keys, TT catalogue, fleet.toml). | D C M |
| `fpgas_apt` | keep | `nfsroot_apt` | adds the fpgas.online, fpga-tools and nfsroot-watchdog apt repos inside the NFS root | – |
| `img` | **`nfsroot_image`** | `nfsroot_pull` | CI: downloads and extracts RasPiOS (`build.yml`). Gateway: podman pull of the GHCR image and extraction to `nfs_root` (`prefetch.yml`, `pull.yml`) | D C |
| `jump` | keep | `ssh_jump` | the restricted jump account (`pi`) on the gateway, used to hop to the Pis | – |
| `lldp` | keep | – | lldpd on the gateway | – |
| `mqtt` | **`fleet_broker`** | `mosquitto` | mosquitto broker for fleet self-registration | D (tag `mqtt`) |
| `netif` | **`nics`** | `uplink`, `server_nics` | names the gateway's two NICs by MAC (`eth-uplink`/`eth-local`), uplink networkd, static resolv.conf | D (tag) C (`tests/test_netif.py`) |
| `nfs` | **`nfs_server`** | `nfs_export`, keep | nfs-kernel-server: exports the NFS root on eth-local only | D (verify tag) |
| `nfsroot_generation` | keep | – | takes the root-update lock, bumps the generation, releases the lock (nfsroot-watchdog-server) | – |
| `nspawn_pi` | **`nfsroot_chroot`** | `nfsroot_build` | CI only: bind mounts, policy-rc.d, the deb cache, initramfs suppression and kernel pruning so apt can run inside the root. It has not used systemd-nspawn for a long time. | C D (verify tag) |
| `onpi` | **`nfsroot_packages`** | `pi_software`, `nfsroot_pi` | CI chroot: apt upgrade, setup-pi, TT bridge, fleet units, Pi atftpd port, FPGA boot check, nfsroot-watchdog. Never runs "on a Pi". | D C |
| `operators` | keep | – | operator accounts, keys, sudo, retired accounts | – |
| `pxe` | **`dnsmasq`** | `netboot`, `lan_services` | dnsmasq DHCP/TFTP (`base.conf`, `ports.conf`, legacy MAC tables, the "Raspberry Pi Boot" service) + chrony NTP for the board LAN. The Pis do not PXE. | D C (host vars `dnsmasq_auth_*` become role-prefixed) |
| `server_user` | keep | `web_user` | the gateway's own login account (`user_name`), including the in-place rename from `videoteam` | – |
| `site` | **`website`** | `django_site`, `webapp` | nginx vhost + certbot, the fpgas-online-site Django install, gunicorn/uvicorn/daphne, fleet consumer, pistat and PoE env. It collides with `site.yml`, "site layer", `fleet_site` and "per-site". | D C (heavy) |
| `ssh_key_fetch` | keep | – | library role: downloads `gh:`/`lp:` keys, fails when a download is empty | – |
| `sshd` | keep | – | pubkey-only drop-in with a lockout guard | – |
| `stream_server` | keep | `cam_hls`, `rtmp_hls` | nginx-rtmp ingest from the Pi cameras + HLS output on the gateway | – |
| `switch_vlans` | keep | `switch_config` | installs the `fpgas-switch-setup` CLI and `switches.yml`, and converges the physical switches' VLANs | – |
| `ttsite` | **`tt_website`** | `tinytapeout`, keep | the tinytapeout.fpgas.online vhost, Commander embed bundles, the TT board catalogue in Django | D C (inputs list names `roles/ttsite/templates/tt-boards.yaml.j2`) |
| `uhubctl` | keep, **or delete** | – | uhubctl + udev rule. The `[uhubctl]` group has been empty since 2026-09-04, so the role never runs. | D (if deleted) |
| `vlan_ports` | **`port_vlans`** | `vlan_ifaces` | gateway networkd VLAN netdevs/networks, one per switch port, + the eth-local trunk. Matches `filter_plugins/port_vlans.py` and `tests/test_port_vlans.py`. | D (tag `vlan-ports` in runbooks) |
| `webrtc` | keep | `cam_webrtc` | mediamtx WHEP leg for the camera streams | – |
| `wssh` | **`web_terminal`** | `webssh` | webssh (`wssh.service`/`.socket`) + nginx include: the browser SSH terminal | D (tag) M (unit names stay) |

### 2.2 Playbooks and plays

| current | proposed | alternatives | what it is | blast |
|---|---|---|---|---|
| `site.yml` | keep | `gateway.yml` | full converge of the gateway + web tier + NFS root update | – |
| `web.yml` | keep | – | web tier play, imported by site.yml | – |
| `verify-server.yml` | keep | `verify-gateway.yml` | server-side checks | – |
| `verify-pi.yml` | keep | – | live Pi checks | – |
| `refresh-known-hosts.yml` | keep | – | re-pins the controller's known_hosts | – |
| `ci-nfsroot-base.yml` | `nfsroot-base.yml` | keep | CI stage 1: RasPiOS download + extract | C (base stage key: full rebuild) |
| `ci-nfsroot-upgrade.yml` | `nfsroot-upgrade.yml` | keep | CI stage 2: `apt full-upgrade` of the base | C (upgrade stage key) |
| `ci-nfsroot.yml` | `nfsroot-image.yml` | `nfsroot-build.yml` (clashes with the workflow name) | CI stage 3: the image roles + checks | C D (docs `--skip-tags pipw,keys`) |
| `ci-nfsroot-runner.yml` | `tasks/ci-runner.yml` | keep | not a playbook: a task list the three above import | C |
| play "Configure the server network interfaces" | "Configure the gateway NICs" | keep | site.yml play 1 | X |
| play "Configure the server services" | "Configure the gateway services" | – | site.yml play 3 | X |
| play "Start pulling the Pi NFS root image" | keep | – | | – |
| play "Configure USB hub power control" | keep (or delete with `uhubctl`) | – | | X |
| play "Update the Pi NFS root" | keep | – | | – |
| play "Deploy the web tier" | keep | – | | – |
| play "Verify server roles (nbp)" | "Verify the gateway" | – | | X |
| play "Verify web roles (pig)" | "Verify the web tier" | – | | X |
| play "Verify uhubctl" | keep (or delete) | – | | X |
| play "Verify running Pi" | "Verify the running Pis" | – | | X |
| play "Apply the generic fixpi layer to the NFS root" | "Make the NFS root netbootable" | "Apply the generic NFS root config" | | X |
| play "Install the Pi roles into the NFS root" | "Install the Pi software into the NFS root" | – | | X |
| play "Prepare the NFS root chroot", "Finish, clean and check the NFS root image", "Download and extract the RasPiOS base image", "Prepare the RasPiOS tree for the upgrade", "Upgrade the RasPiOS packages", "Finish and clean the upgraded stage", "Refresh pinned host keys ..." | keep | – | | – |
| task "Import the web tier playbook" | keep | – | | – |

### 2.3 Inventory groups, hosts and files

| current | proposed | alternatives | what it is | blast |
|---|---|---|---|---|
| group `nbp` | **`gateway`** | `server` | the netboot gateway (tweed, val2). In CI inventory it is the build runner. Used by `hostvars[groups['nbp'][0]]` in `onpi/tasks/tt.yml` and `verify-pi.yml`. | D (`--limit nbp`) C |
| group `pig` | **`web`** | `web_tier` | hosts running the web tier (web.yml) | D C |
| group `pxe` | **delete** | – | defined in `inventory/hosts` but no play targets it | D (nil) |
| group `uhubctl` | keep, **or delete** with the role | – | empty since 2026-09-04 | D |
| group `pi` (CI inventory) | **`pi_root`** | `nfsroot_chroot` | the extracted root reached by the chroot connection. Its host is named `nfsroot`, so the group cannot take that name as well. | C (verify-pi default `pi_live:pi`) |
| group `pi_live` (tests) | keep | `pis` | the booted virtual Pi | – |
| host `fpgas.online` | `welland.fpgas.online` (owner decision) | keep | the Welland gateway (tweed). The name reads like the apex. | D M (`--limit fpgas.online`, host_vars filename, known_hosts pins, ten64 deploy vars) |
| host `ps1.fpgas.online` | keep | – | PS:One gateway (val2) | – |
| host_vars `gator.yml`, `negk.yml`, `rpi-cb-1f-f7.yml` | **delete** | – | pici hosts that are not in any inventory | X |
| `inventory-ci-nfsroot/` | keep | `inventory-nfsroot` | CI build inventory | C |
| `tests/inventory/test-hosts` | `tests/inventory/hosts` | keep | VM test inventory (matches `ansible/inventory/hosts`) | C |
| group_vars file `srv.yml` | `nfsroot.yml` | `raspios.yml` | the RasPiOS image + NFS root path vars | C (listed in `nfsroot_inputs.py`, symlinked in 2 inventories) |
| group_vars file `ci.yml` | fold into `nfsroot.yml` | keep | holds only `tftpd_port` | C |
| group_vars `site.yml`, `ttsite.yml`, `streaming.yml`, `ssh_keys.yml`, `controller.yml`, `firewall.yml`, `all.yml` | `site.yml` → `website.yml`, `ttsite.yml` → `tt_website.yml` (follow role renames), rest keep | – | | X |
| `inventory-ci-nfsroot/group_vars/all/zz-ci-overrides.yml` | keep | – | | – |

### 2.4 Tags (87 distinct)

Under the convention, tags equal role names. Tag renames change operator commands (runbooks, notes on ten64, and memory notes like `--tags pi,fpgas-apt,onpi,cam`). **The tags `pi`, `onpi` and `cam` no longer select anything in `site.yml`**, because those roles only run in the CI build, so that documented NFS-root command is already stale.

| current | proposed | where | what it selects | blast |
|---|---|---|---|---|
| `verify` | keep | verify-*.yml, ci-nfsroot.yml, site verify | all checks | – |
| `always` | keep | site.yml, verify-pi, server_user, apt_cache | lock/unlock and facts | – |
| `django` | keep | site role, ttsite, verify | the Django deploy (runbook `--tags django`, `--skip-tags django`) | – |
| `fleet`, `tt`, `streaming` | keep | several roles | cross-role features | – |
| `fixpi` | `nfsroot_config` | site.yml, fixpi, verify-server | the site layer | D |
| `img` | `nfsroot_image` | site.yml, ci-nfsroot-base, img, **onpi/apt.yml (wrong role)** | pull/extract | D C |
| `onpi` | `nfsroot_packages` | ci-nfsroot.yml | | C |
| `cam` | `cam_pi` | ci-nfsroot.yml, cam_pi | | C |
| `fpgas-apt` | `fpgas_apt` | ci-nfsroot.yml, onpi (6), verify-* | Used inside onpi as a catch-all. Drop it there. | D C |
| `apt-cache`, `apt-client`, `automation-user`, `switch-vlans`, `nspawn-pi` | `apt_cache`, `apt_client`, `automation_user`, `switch_vlans`, `nfsroot_chroot` | roles + verify-server | the role | D |
| `vlan-ports` | `port_vlans` | vlan_ports | the role | D (runbook) |
| `server_user`, `operators`, `jump`, `sshd`, `lldp`, `firewall`, `uhubctl`, `ttsite`, `mqtt`, `netif`, `nfs`, `pxe`, `wssh`, `site` | the role's (new) name | roles + verify-server | the role | D |
| `nfs-root` | `nfsroot` | verify-server (20) | inline NFS root checks | D |
| `cam-stream`, `cam-webrtc` | `stream_server`, `webrtc` | verify-server | | D |
| `le` | `tls` | site (22) | nginx + certbot | D |
| `netboot` | keep | fixpi, ci-nfsroot | netboot prep | – |
| `keys` | keep | site.yml, fixpi | authorized_keys / GitHub keys | – |
| `sunxi` | keep | fixpi | Orange Pi kernel/TFTP | – |
| `sunxi-kernel` | delete (use `sunxi`) | fixpi | one task | D (dated runbook only) |
| `pipw` | `pi_password` | fixpi, **nfsroot-build.yml `--skip-tags pipw,keys`** | the pi password | C D |
| `qemu` | delete | fixpi (17) | leftover from the nspawn/qemu era | X |
| `cmdline`, `pibs`, `ispi`, `hostname`, `bashrc`, `nogrow`, `manage` | delete | fixpi, pxe, site | single-task pici tags | X |
| `index.html`, `pibdemos`, `pibup`, `pifpgas`, `demos`, `switch` | delete (with the empty task files) | site | empty includes | X |
| `snmp` | `poe_env` | site | writes the PoE switch env to /etc/environment | D (runbook `--skip-tags snmp`) |
| `pistat` | keep | site | pistat (a name the site repo owns) | – |
| `ia`, `ab`, `mp`, `tmux` | delete (dead files) | onpi | | X |
| `tweeks`, `pitweeks`, `setup-pi`, `stale-root`, `tftpd`, `rp1-jtag`, `acorn`, `fpga-verify` | delete (tags inside a CI-only role are never used) | onpi | | X |
| `staticips` | delete | uhubctl | pici leftover on the udev task | X |
| `pibsite` | delete | wssh | | X |
| `forwarding`, `nftables` | delete | firewall | | X |
| `pxe-test` | delete (the `when:` already gates it) | pxe | | X |
| `ttsite-boards`, `ttsite-django`, `ttsite-embed`, `ttsite-nginx` | keep as topic words (`tt_boards`...) or delete | ttsite | | D (low) |
| verify-pi `boot`, `system`, `network`, `services`, `packages`, `uart`, `hw-camera`, `hw-fpga`, `hw-sunxi`, `hw-usb-gadget`, `rp1-jtag`, `acorn`, `fpga-verify` | keep | verify-pi.yml | README documents `--skip-tags hw-camera,hw-fpga` | – |

### 2.5 Variables

#### 2.5.1 Inventory (group_vars/host_vars) vars

| current | proposed | alternatives | what it is | blast |
|---|---|---|---|---|
| `nfs_root` | **`nfsroot_dir`** | `nfsroot_path` | `/srv/nfs/rpi/<dist>`: holds `boot/` and `root/`. Used in ~230 places (fixpi 101). | D C (tests read it) M (value stays) |
| `dist` | **`raspios_release`** | `nfsroot_release` | RasPiOS release (`bookworm`) in paths and apt suites | D C (`tests/vm`, `nfsroot_publish.py`) |
| `user` | **`pi_user`** | – | the Pi login account (`pi`). "user" is also a common loop var name. | D C |
| `user_name` | **`server_user_name`** | – | the gateway's own account (`admin`, formerly `videoteam`) | D |
| `pi_pw` | `pi_password` | – | the Pi user's password (public by design) | D |
| `img_host` | `raspios_mirror` | – | RasPiOS download host (CI base stage) | C |
| `dir_date` | `raspios_date` | – | RasPiOS image date | C |
| `release_date` | delete (alias of `dir_date`) | – | | C |
| `img_path` | `raspios_dir` | – | URL path to the image | C |
| `base_name` | delete (inline it) | – | | C |
| `img_name` | `raspios_image` | – | `.img` file name | C |
| `zip_name` | `raspios_image_xz` | – | `.img.xz` file name | C |
| `tftp_root` | keep | – | TFTP root (the NFS root's `boot/` on per-port hosts) | – |
| `tftpd_port` | move to `nfsroot_packages` defaults as `nfsroot_packages_tftpd_port` | `pi_tftpd_port` | the port the Pi's own atftpd listens on (6069) | C |
| `domain` | **`apex_domain`** | `root_domain` | `fpgas.online`, used for apt repo URLs and dnsmasq | D |
| `domain_name` | **`site_fqdn`** | `web_fqdn` | this site's public name (`welland.fpgas.online`) | D |
| `streaming_frontend_hostname` | delete (only feeds `domain_name` in one host_vars file) | – | videoteam leftover | D |
| `streaming_frontend_aliases` | `site_aliases` | – | extra names for ALLOWED_HOSTS | D |
| `pib_network` | **`lan_prefix`** | `board_net` | IPv4 prefix of the board LAN (`10.21` / `10.21.0`) | D (filter plugin, 5 roles) |
| `pib_network6_base` | `lan_prefix6` | – | IPv6 base of the board LAN | D |
| `pib_domain` | `lan_domain` | `board_domain` | DNS domain of the board hostnames | D |
| `dhcp_range` | `dnsmasq_dhcp_range` | – | flat-scheme DHCP range (ps1) | D |
| `conference_name` | **`nginx_prefix`** (value stays `pib`) | `web_conf_prefix` | videoteam leftover. It prefixes nginx include file names (`pib-wssh.conf`) and the RTMP conf. Changing the **value** is a migration. | D M |
| `room_name`, `time_zone`, `common_name`, `subject_alt_names`, `tt06_dev_id`, `switch_base`, `ssh_password_auth`, `firewall_internal_networks`, `firewall_rules` | **delete** | – | set in inventory, read by nothing | X |
| `django_dir` | keep | – | `/srv/www/pib` | M (value) |
| `static_dir` | `django_static_dir` | – | Django STATIC_ROOT | D |
| `django_project_name` | keep | – | `pib`, the project package from fpgas-online-site | M (other repo) |
| `letsencrypt_account_email` | keep (owner decision on the value) | – | the value is still `carl@NextDayVideo.com` | – |
| `fixture_path` | `site_fixture` | `board_fixture` | the site repo's Django board fixture file | D |
| `fleet_broker` | **`fleet_enabled`** | `fleet` | bool: this site runs fleet self-registration (broker, consumer, fleet.toml, verify). It is not the broker's address. | D C |
| `fleet_site` | keep | – | fleet site id (`welland`, `ps1`) | – |
| `switch` | **`poe_switch`** | `snmp_switch` | dict: the legacy single switch's SNMP PoE creds, `mpi_port`, `nos` | D C (tests/vm, 26 site refs) |
| `switch.nos` | `poe_switch.pis` | `.boards` | the Pi list (port, MAC, serial) for the legacy MAC-table scheme | D |
| `switches` | keep | – | per-port-VLAN switch list | – |
| `switches_manage` | `switch_vlans_manage` | – | lets `switch_vlans` push config | D |
| `eth_uplink*`, `eth_local*` | keep | – | NIC names/addresses | – |
| `sunxi_boards`, `sunxi_default_dtb` | keep | `opi_boards` | Orange Pi boards and DTB | – |
| `streaming` | keep | – | streaming data root and method | – |
| `tt_boards` | keep | – | TT board catalogue | – |
| `tt_install` | keep | – | bake the TT debs without a catalogue (CI) | – |
| `tt_commander_embed_version/_sha256`, `tt_commander_legacy_embed_version/_sha256` | `tt_website_commander_*` | keep | Commander bundle pins (read only by ttsite) | D |
| `ttsite_domain`, `ttsite_certbot` | `tt_website_domain`, `tt_website_certbot` | – | follow the role | D |
| `pxe_test_clients` | `dnsmasq_test_clients` | – | extra DHCP hosts for tests | D |
| `dnsmasq_auth_zone/_glue/_subnet/_interface` | keep (become role-prefixed once `pxe` → `dnsmasq`) | – | dnsmasq authoritative zone | – |
| `ssh_imports`, `ssh_imports_revoked`, `ssh_public_keys`, `ssh_public_keys_revoked`, `operators_accounts` | keep | – | shared jump/operator key inputs | – |
| `nfsroot_build_deb_cache` | `nfsroot_chroot_deb_cache` | – | CI deb cache dir | C |
| `fixpi_image_build`, `fixpi_generate_host_keys` | prefix follows role (`nfsroot_config_*`) | – | CI switches | C |
| `fixpi_server_monitor` | delete with its tasks (owner decision) | `nfsroot_config_serial_console` | gates pici "server is a Pi" tasks inside fixpi that act **on the gateway** (tio, dialout, getty mask, `/boot/firmware/config.txt`) | C |
| `img_pull_retries`, `img_pull_delay` | prefix follows role | – | set in the test-vm host_vars | C |
| `automation_user_manage`, `sshd_pubkey_only`, `server_user_*`, `apt_client_*`, `apt_cache_enabled`, `firewall_dns_query_sources`, `webrtc_*`, `site_under_construction`, `site_require_fpga_verified` | keep (the `site_*` ones follow the role → `website_*`) | – | | D (site_* only) |
| `verify_pi_fpga_expect`, `verify_pi_header_uart_console`, `verify_pi_hosts` | keep | – | | – |

#### 2.5.2 Role defaults (122 vars)

Renaming a role renames every var in its defaults and every `register`/`set_fact` name (the lint rule). Only the name changes that go beyond that prefix swap are listed here.

| role → new | current | proposed | what it is | blast |
|---|---|---|---|---|
| fixpi → nfsroot_config | `fixpi_*` (14) | `nfsroot_config_*` | | C D (`-e` possible) |
| | `fixpi_jump_ssh_pubkey` | `nfsroot_config_jump_pubkey` | the jump account's key authorized on the Pis | D |
| | `fixpi_server_user_pubkey` | `nfsroot_config_server_pubkey` | | D |
| | `fixpi_github_key_users`, `fixpi_github_keys_base_url` | `nfsroot_config_github_users`, `..._github_url` | | D |
| img → nfsroot_image | `img_nfsroot_image` | `nfsroot_image_ref` | GHCR image reference (`...:bookworm-armhf`) | D (override for testing) |
| | `img_cache_dir` (`/var/cache/pib`) | `nfsroot_image_raspios_cache` (value `/var/cache/raspios`) | CI download cache | C |
| | `img_pull_*` | `nfsroot_image_pull_*` | | C |
| onpi → nfsroot_packages | `onpi_nfsroot_watchdog_*` (7) | `nfsroot_packages_watchdog_*` | watchdog slots, spacing, dry run | D (`dry_run` is an `-e` knob) C |
| | `onpi_fpga_verify_package` | `nfsroot_packages_fpga_check` | which board package the boot check installs | C |
| nspawn_pi → nfsroot_chroot | `nspawn_pi_sshd_port` | **delete** | unused (nspawn era) | X |
| | `nspawn_pi_root`, `nspawn_pi_kernel_*`, `nspawn_pi_max_kernel_trees`, `nspawn_pi_suppress_initramfs` | `nfsroot_chroot_*` | | C |
| netif → nics | `netif_uplink_manage`, `netif_allow_reboot` | `nics_uplink_manage`, `nics_allow_reboot` | | D (low: not set in inventory) |
| site → website | `site_certbot`, `site_under_construction`, `site_require_fpga_verified`, `site_package`, `site_poe_package`, `site_upload_max_body_size` | `website_*` | `site_package`/`site_poe_package` are **documented `-e` overrides** for branch deploys | D C |
| ttsite → tt_website | `ttsite_boards_path`, `ttsite_daemon_port`, `ttsite_ws_read_timeout`, `ttsite_pi_network` | `tt_website_*` (`_pi_network` → `_lan_prefix`) | | D |
| uhubctl | `uhubctl_usb_hubs` | keep (or delete with the role) | | – |
| everything else (`apt_cache_*`, `apt_client_*`, `automation_user_*`, `firewall_*`, `fpgas_apt_*`, `jump_*`, `nfsroot_generation_*`, `operators_*`, `server_user_*`, `sshd_*`, `ssh_key_fetch_*`, `switch_vlans_*`, `webrtc_*`) | keep | – | | – |

### 2.6 Handlers (31)

| current | role(s) | proposed | notes | blast |
|---|---|---|---|---|
| `Reload-systemd` | cam_pi, onpi, stream_server | `Reload systemd` | hyphenated; site and wssh already use `Reload systemd` | X |
| `Udev-reload` | uhubctl | `Reload udev` | | X |
| `Exportfs` | nfs | `Reload NFS exports` | runs `exportfs -r` | X |
| `Reload sshd for pubkey-only` | sshd | keep | the suffix keeps it apart from jump's `Reload sshd` | – |
| `Restart network-manager`, `Restart networking` | pxe | **delete** | nothing notifies them | X |
| `Restart apt-cacher-ng (apt-cache)`, `Reload nginx (apt-cache)` | apt_cache | keep | the suffix pattern is fine | – |
| `Reload nginx` | site, ttsite, wssh, stream_server, webrtc | keep | same action everywhere | – |
| `Restart nginx`, `Restart dnsmasq`, `Restart chrony`, `Restart mediamtx`, `Restart mosquitto`, `Restart wssh`, `Restart fleet-consumer`, `Restart django services`, `Reload nftables`, `Reload networkd`, `Reload sshd`, `Reload systemd` | various | keep | | – |

### 2.7 Task files, templates and files inside roles

| role | current | proposed | what it is | blast |
|---|---|---|---|---|
| fixpi | `tasks/tweeks.yml` | `tasks/tweaks.yml` | config.txt, /etc tweaks, prompt, + the gateway serial tasks | X |
| fixpi | `tasks/userconf.yml` | `tasks/pi-user.yml` | pi password, banner, ssh keys | X |
| fixpi | `tasks/nogrow.yml` | `tasks/no-resize.yml` | stop the rootfs resize and swapfile | X |
| fixpi | `tasks/manage.yml` | `tasks/mode-scripts.yml` | maintenance.sh/production.sh + switch to maintenance | X |
| fixpi | `tasks/ansible-home.yml`, `authorized_keys.yml`, `github_keys.yml`, `netboot.yml`, `sunxi*.yml`, `tt-site.yml`, `fleet-site.yml` | keep (`authorized_keys.yml`/`github_keys.yml` → kebab-case) | | X (`github_keys.yml` is `tasks_from` in site.yml) |
| fixpi | `templates/resolve.conf.j2` | **delete or fix** | writes `/etc/resolve.conf` (misspelt, so nothing reads it). It is listed in `tests/ci/nfsroot_manifest.py`. | C |
| fixpi | `files/etc/network/interfaces.d/eth1.conf` | delete | pici static 192.168.100.100. Copied into the root, but ifupdown is masked there. | C |
| fixpi | `files/scripts/chroot-mount-pi-fs.bash` | keep | installed as `/usr/local/sbin/...` | M |
| fixpi, onpi, wssh | `notes.txt` | delete, or fold into README | pici notes | X |
| onpi | `tasks/arty_blink.yml`, `arty_here.yml`, `arty_wire.yml`, `tmux.yml`, `pistat.yml` | **delete** | nothing includes them | X C |
| onpi | `tasks/tweeks.yml` | `tasks/pi-dirs.yml` | Uploads/Downloads dirs | X C |
| onpi | `tasks/nonfs.yml` | `tasks/nfs-version.yml` | drops `nfsvers=4.2` from cmdline | X C |
| onpi | `tasks/stale_root.yml` | `tasks/watchdog.yml` | nfsroot-watchdog | X C |
| onpi | `tasks/tftpd.yml`, `apt.yml`, `fleet.yml`, `tt.yml`, `fpga_verify.yml` | keep (`fpga_verify` → `fpga-check.yml`) | | X C |
| site | `tasks/pib.yml`, `js_player.yml`, `pibdemos.yml`, `pibup.yml`, `pibfpgas.yml`, `switch.yml` | **delete** | empty or placeholder includes | X |
| site | `tasks/index.yml` | fold into `django.yml` | creates `static_dir` | X |
| site | `tasks/snmp.yml` | `tasks/poe-env.yml` | PoE switch env in /etc/environment | X |
| site | `tasks/apt.yml` | `tasks/packages.yml` | | X |
| site | `tasks/fpgas-online-site.yml` | `tasks/install.yml` | pip-installs site + poe | X |
| site | `templates/includes/{pibfpgas,pibup,pistat,snmp_switch}.conf.j2` | keep | named after the site repo's Django apps | M |
| pxe | `templates/pibs.conf.j2` | `templates/mac-hosts.conf.j2` (dest stays `pibs.conf`) | legacy MAC-table DHCP hosts | X |
| pxe | `templates/interfaces-static.j2` | **delete** | unused | X |
| pxe | `files/rpi.conf`, `files/send_stat.conf` | keep | dest file names on the gateway | M |
| stream_server | `templates/pib.conf.j2` | `templates/rtmp-app.conf.j2` (dest stays) | RTMP application `pib` | X |
| stream_server | `tasks/base.yml`, `tasks/back.yml` | `tasks/rtmp.yml`, `tasks/hls.yml` | | X |
| wssh | `files/etc/systemd/system/gunicorn.service`, `gunicorn.socket` | **delete** | unused copies | X |
| wssh | `files/etc/nginx/includes/wssh.conf` (used as a template) | `templates/wssh.conf.j2` | | X |
| img | `files/img2files.sh` | keep | | – |
| nspawn_pi | `files/nfsroot_kernels.py` | keep | installed as `/usr/local/sbin/nfsroot-kernels` | – |
| mqtt | `templates/fpgas-fleet.conf.j2` | keep | | – |

### 2.8 Systemd units and live paths the repo installs

Keep all of these. Renaming them is a migration (section 4).

| name | installed by | where |
|---|---|---|
| `gunicorn.service/.socket` (+ `gunicorn.service.d/poe.conf`), `uvicorn.service`, `daphne.service/.socket`, `fleet-consumer.service` | site | gateway |
| `wssh.service/.socket` | wssh | gateway |
| `mediamtx.service` | webrtc | gateway |
| `fpgas-hostname-hosts.service`, `cam.service`, `fpgas-tt.service` (enabled), nfsroot-watchdog units | fixpi, cam_pi, onpi | NFS root |
| `arty_blink/here/wire.service` | onpi dead task files | none (never installed) |
| `/srv/nfs/rpi/<dist>/{boot,root}`, `/srv/tftp`, `/srv/www/pib`, `/srv/www/static`, `/srv/streams` | nfs, img, fixpi, site, stream_server | gateway |
| `/etc/nginx/includes/pib-*.conf`, `/etc/nginx/rtmp/pib.conf`, `/etc/dnsmasq.d/{base,ports,pibs,switch,rpi,send_stat}.conf` | site, wssh, stream_server, pxe | gateway |
| `/etc/fpgas/switches.yml`, `/opt/fpgas-switch/venv`, `/usr/local/venv/wssh`, `/usr/local/sbin/{maintenance,production}.sh`, `/usr/local/sbin/chroot-mount-pi-fs.bash`, `/usr/local/sbin/nfsroot-kernels`, `/usr/local/bin/jump-shell` | switch_vlans, wssh, fixpi, nspawn_pi, jump | gateway |
| `/etc/fpgas-online/{tt-boards.yaml,fleet.toml}` | fixpi, onpi, ttsite | root and gateway |

### 2.9 `tests/` and `docs/` directories

| current | proposed | alternatives | what it is | blast |
|---|---|---|---|---|
| `tests/ci/` | **`ci/`** | `tools/nfsroot/` | not tests: the NFS root build/publish/promote tooling (`nfsroot_publish.py`, `_inputs.py`, `_stages.py`) | C (workflows, tests import it) |
| `tests/vm/` | keep | – | QEMU VM harness | – |
| `tests/lab/` | keep | – | dnsmasq lab harness | – |
| `tests/inventory/` | keep | – | VM test inventory | – |
| `tests/test_*.py` | keep | – | names match what they test | – |
| `docs/superpowers/{plans,specs,runbooks}` | keep | move `runbooks/` to `docs/runbooks/` | the owner's cross-repo convention. Runbooks are operator docs, not plan artefacts. | X |
| `docs/hardware/`, `docs/rebuilds/` | keep | – | | – |
| dated plans/specs under `docs/superpowers/` | **do not rewrite** | – | they are historical and quote old names on purpose | – |

---

## 3. Task-name cleanups per role (flagged only)

The roles written recently (apt_cache, apt_client, nfsroot_generation,
operators, server_user, ssh_key_fetch, sshd, switch_vlans, webrtc, firewall,
lldp) are fine apart from the rows below. Rows marked **(del)** go away with
dead code.

| role | current | proposed |
|---|---|---|
| fixpi | Link bootcode.bin in tftp root because pi netboot not too smart. | Link bootcode.bin into the TFTP root |
| fixpi | Link pi serial-num to boot dir, cuz that is how it works. | Link each Pi serial number to the boot dir |
| fixpi | Create tftp root directory | Create the TFTP root |
| fixpi | Save the original config.txt, cmdline.txt, fstab | Back up the stock config.txt, cmdline.txt and fstab |
| fixpi | Replace cmdline with netbootie versions | Write the netboot cmdline.txt files |
| fixpi | Fstab - replace local fs to nfs | Write the NFS fstab |
| fixpi | Install nfs-common into pi's fs | Install nfs-common in the NFS root |
| fixpi | Don't helper resize the root fs | Divert the resize and swap helpers |
| fixpi | Don't resize the root fs (two tasks share this name) | Mask the growfs, resize and swap units / Remove resize2fs_once |
| fixpi | Don't manage a swap file | Disable dphys-swapfile |
| fixpi | Avoid [FAILED] Failed to start Set console font and keymap. | Disable console-setup in the NFS root |
| fixpi | Avoid Wi-Fi is currently blocked by rfkill. | Remove the rfkill Wi-Fi login warning |
| fixpi | Remove etc/hostname (dhclient will get hostname from server) | Remove /etc/hostname (DHCP names the Pi) |
| fixpi | Etc overrides | Copy the /etc overrides into the NFS root |
| fixpi | Resolve.conf | (del) or "Write /etc/resolv.conf in the NFS root" if it is fixed |
| fixpi | Add time to bash prompt | Add the time to the pi user's prompt |
| fixpi | Config.txt disable onboard Wi-Fi and Bluetooth, enable uart | Disable Wi-Fi and Bluetooth, enable the UART |
| fixpi | Config.txt write-protect the bootloader EEPROM | Write-protect the bootloader EEPROM |
| fixpi | Config.txt enable the 40-pin header UART on Pi 5 | Enable the Pi 5 header UART |
| fixpi | Config.txt dwc2 peripheral mode on Pi 4 / Pi 5 (USB gadget console) | Enable the USB gadget console on Pi 4 and 5 |
| fixpi | Is server pi | Check whether the gateway is a Pi |
| fixpi | Enable /dev/serial0 | Enable /dev/serial0 on a Pi gateway |
| fixpi | Apt remove brltty | Remove brltty from the gateway |
| fixpi | Install packages on server | Install tio on the gateway |
| fixpi | Let tio connect to the tty and see pi boot messages | Add the server user to dialout |
| fixpi | Disable (mask) getty systemd service | Mask the gateway's ttyAMA0 getty |
| fixpi | Create issue.d dir | Create /etc/issue.d in the NFS root |
| fixpi | Display IP, pw and things on console | Install the console login banner |
| fixpi | Enable sshd | Enable sshd at boot (boot/ssh) |
| fixpi | Get fixed sshswitch (`when: false`) | (del) |
| fixpi | Sshd password settings | Install the sshd password-login drop-in |
| fixpi | Generate ssh keys for server user | Generate the server user's ssh key |
| fixpi | Create .ssh dirs | Create root's and pi's .ssh dirs |
| fixpi | Generate ssh keys for pi users pi and root | Generate ssh keys for pi and root |
| fixpi | Set perms (use numeric UID — pi user is UID 1000 on RPi OS) | Give the .ssh dirs to pi (uid 1000) |
| fixpi | Install scripts to manage pi states | Install the maintenance and production scripts |
| fixpi | Put the pi boot system into maintenance mode | Switch the NFS root to maintenance mode |
| fixpi | Reboot every Pi booted from this root (their sshd cannot read the replaced shadow) | Reboot the Pis after a password change |
| fixpi | Download the operators' GitHub keys (unless done before the root update) | Download the operators' GitHub keys |
| fixpi | Write the NFS root's authorized_keys (only when the key list changed) | Write the NFS root's authorized_keys |
| fixpi verify | Read cmdline.txt from nfs_root/boot | Read the root's cmdline.txt |
| fixpi verify | Check TFTP serial number directories from switch.nos | Check the per-serial TFTP dirs |
| fixpi verify | Sunxi kernel payload present in tftp | Check the sunxi kernel payload in TFTP |
| onpi | Apt update/upgrade | Upgrade the NFS root packages |
| onpi | Apt remove tiny-vim | Remove vim-tiny |
| onpi | Install packages | Install the NFS root packages |
| onpi | Let kernel figure out nfs ver / Let the kernel pick the NFS version | Drop nfsvers from cmdline.txt |
| onpi | Tftpd port systemd | Move the atftpd socket to tftpd_port |
| onpi | Tftpd port conf | Move atftpd to tftpd_port |
| onpi | Pi tftp dir user writable | Let pi write to /srv/tftp |
| onpi | Create home/pi/updownload | Create pi's Uploads and Downloads dirs |
| onpi | Tt_boards from the server host | Read tt_boards from the gateway |
| onpi | Tiny Tapeout bridge packages (generic) | Install the TT bridge packages |
| onpi | Tiny Tapeout site catalogue (when this site has TT boards) | Set the TT site catalogue |
| onpi | Nfsroot-watchdog settings for fpgas.online | Configure nfsroot-watchdog |
| onpi | Install the FPGA boot check for every board, and the images it checks against | Install the FPGA boot check and its images |
| onpi | Arty_blink, Arty_here, Arty_wire, Systemd pistat_ssh.service, tmux tasks | (del) |
| cam_pi | Apt update/upgrade | Upgrade the NFS root packages |
| cam_pi | Install gst packages | Install GStreamer |
| img | Install packages | Install the image extraction tools |
| img | Create pib cache dir | Create the RasPiOS download cache |
| img | Download pi sd card img file | Download the RasPiOS image |
| img | Extract files from raspios.img.xz | Extract boot/ and root/ from the image |
| img verify | Check nfs_root/root/bin/bash exists | Check the root has /bin/bash |
| img verify | Check nfs_root/boot/kernel8.img exists | Check boot/ has kernel8.img |
| nfs | Install packages | Install nfs-kernel-server |
| nfs | Create some dirs | Create the export dirs |
| nfs | Nfs only on internal nic | Bind NFS to eth-local |
| nfs | Write etc/exports | Write /etc/exports |
| nfs | Enable and start server service rpcbind | Enable rpcbind |
| nfs | Enable and start server service nfs-kernel-server | Enable nfs-kernel-server |
| pxe | Enable Raspberry Pi Boot | Advertise the Raspberry Pi boot service |
| pxe | Create dnsmasq.d pib.conf (legacy MAC-table hosts) | Write the MAC-table DHCP hosts (legacy) |
| pxe | Create dnsmasq.d switch.conf (legacy MAC-table hosts) | Write the switch's DHCP host (legacy) |
| pxe | Create dnsmasq.d ports.conf (per-port hosts) | Write the per-port DHCP hosts |
| netif | Current names of the two NICs (by MAC) | Find the NICs by MAC |
| netif | MAC-matched .link files | Name the NICs by MAC |
| netif | Uplink network (systemd-networkd) | Configure the uplink network |
| netif | Static resolv.conf (networkd without systemd-resolved) | Write a static resolv.conf |
| netif | Is the host running ifupdown | Check whether ifupdown is in use |
| netif | Re-gather facts after rename | Gather facts again after the rename |
| uhubctl | Set udev rule to give user access to turn on and off the usb ports | Let the server user switch hub ports |
| mqtt | Fleet broker config | Configure the fleet broker |
| site | Python and friends | Install the web tier Python packages |
| site | Vhost / Vhost (https, after the certificate was issued) | Write the nginx vhost (http) / (https) |
| site | Obtain certificate (webroot http-01 via the port-80 vhost) | Obtain the certificate (http-01) |
| site | Copy daphne socket file to /etc/systemd/system/daphne.socket | Install daphne.socket |
| site | Copy daphne systemd file to /etc/systemd/system/daphne.service | Install daphne.service |
| site | Copy gunicorn socket file to ... / Copy gunicorn systemd file to ... | Install gunicorn.socket / Install gunicorn.service |
| site | Copy uvicorn systemd file to /etc/systemd/system/uvicorn.service | Install uvicorn.service |
| site | Enable services (three tasks) | Enable daphne / Enable gunicorn / Enable uvicorn |
| site | Start/Make sure uvicorn.service systemd service is running | Start uvicorn |
| site | Site service user | Create the Django service user |
| site | Site directories | Create the Django directories |
| site | Locate the installed pib project package | Locate the installed Django project |
| site | SECRET_KEY and DEBUG | Set SECRET_KEY and DEBUG |
| site | Site_web_hosts | Collect the site's host names |
| site | ALLOWED_HOSTS / CSRF_TRUSTED_ORIGINS | Set ALLOWED_HOSTS / Set CSRF_TRUSTED_ORIGINS |
| site | Pass the pi's user pw | Set the Pi password for the web terminal |
| site | Sites's domain name | Set the Django site domain |
| site | FLEET_MQTT broker address for the fleet consumer | Set FLEET_MQTT for the fleet consumer |
| site | Under-construction banner | Set the under-construction banner |
| site | Set static dir | Set STATIC_ROOT |
| site | Manage.py | Install manage.py |
| site | Create etc/nginx/includes dir | Create /etc/nginx/includes |
| site | Populate /etc/environment | Write the PoE switch settings to /etc/environment |
| site | Include the JS player / pibdemos / pibup / pibfpgas tasks (now empty); Include the switch tasks (not yet implemented); Create static content dir | (del) |
| site verify | Listening sockets; Nginx vhosts that terminate TLS on IPv4/IPv6; Certificate served over IPv6 for {{ domain_name }} | List the listening sockets; Find the TLS vhosts (IPv4/IPv6); Check the IPv6 certificate |
| ttsite | Vhost / Vhost (https, ...) | Write the TT vhost (http) / (https) |
| ttsite | Ttsite settings in local_settings.py | Write the TT Django settings |
| ttsite | Collectstatic (ttsite assets) | Collect the TT static assets |
| ttsite | Per-board websocket include | Write the per-board websocket include |
| ttsite | Embed bundle directory / Legacy embed bundle directory | Create the embed bundle dir / Create the legacy embed dir |
| ttsite verify | Tt-boards.yaml present, Vhost present, Live boards, Live fpga boards, Nginx config valid, Landing page answers on the TT host, Designs API answers on the TT host, Legacy embed bundle present when pinned | Check tt-boards.yaml exists, Check the vhost exists, Count the live boards, Count the live FPGA boards, Check the nginx config, Check the landing page, Check the designs API, Check the legacy embed bundle |
| wssh | Install wssh deb packages | Install the web terminal packages |
| wssh | Install wssh pip packages | Install webssh into its venv |
| wssh | Systemd files (two tasks) | Install wssh.socket / Install wssh.service |
| wssh | Enable services | Enable wssh |
| wssh | Create etc/nginx/includes dir | Create /etc/nginx/includes |
| wssh | Deploy nginx conf | Install the wssh nginx include |
| jump | Ssh directory; Restricted command directory; Keypair for hopping onward to the Pis; Sshd drop-in for the jump account; No sudoers entry for the jump account; Sshd configuration is still valid | Create the .ssh dir; Create the command dir; Generate the onward keypair; Install the sshd drop-in; Remove any sudoers entry; Check the sshd config |
| sshd | Accounts that must keep a working key; Public-key-only sshd drop-in; No public-key-only drop-in when the host does not ask for it; Sshd configuration is still valid | List the key-login accounts; Install the pubkey-only drop-in; Remove the pubkey-only drop-in; Check the sshd config |
| apt_cache | Apt cache facts; Apt cache service; Apt-cacher-ng; Nginx front end and certificate; Remap backends; Apt cache vhost | Set the apt cache facts; Configure apt-cacher-ng; Install apt-cacher-ng; Configure the TLS front end; Write the remap backends; Write the apt cache vhost |
| automation_user, jump, operators, server_user, sshd verify | "Verify: ..." prefix (54 tasks) | drop the prefix, use Check/Assert |
| verify-server.yml | Verify apt-client, Verify automation-user, Verify nspawn-pi, Verify apt-cache | Verify apt_client, ... (the exact role names) |

---

## 4. Migrations that need special care

Do **not** fold these into a rename PR. The recommendation for each is to rename the variable and keep the value.

| item | who depends on it | recommendation |
|---|---|---|
| `/srv/nfs/rpi/<dist>` (value of `nfs_root`) | NFS exports, Pi `cmdline.txt nfsroot=`, nfsroot-watchdog(-server)/nfsroot-generation, CI inventory `ansible_host=/srv/nfs/rpi/bookworm/root`, `tests/ci`, the `site/snmp.yml` env keys `nfs_pth/nfs_boot/nfs_root` | keep the path; rename only the var |
| `dist` value `bookworm` / image tag `nfsroot:bookworm-armhf` | GHCR tags, the promote job, the trixie upgrade work | keep |
| `conference_name: pib`: `/etc/nginx/includes/pib-*.conf`, `/etc/nginx/rtmp/pib.conf` | nginx vhost `include` lines | keep the value. A value change needs a task that removes the old files. |
| RTMP application `pib` (`webrtc_rtmp_source_app`) | fpgas-online-cam publishers on every Pi (in the NFS root) | cross-repo. Keep. |
| `/srv/www/pib`, Django project `pib`, apps `pistat`/`pibup`/`pibfpgas` | fpgas.online-site | owned by the site repo. Keep. |
| unit names `wssh`, `gunicorn`, `uvicorn`, `daphne`, `fleet-consumer`, `mediamtx` | running gateways, monitoring, runbooks | keep. Renaming them means stopping and disabling the old units in the same run. |
| `/etc/dnsmasq.d/*.conf` file names | the running dnsmasq. A renamed file leaves the old one loaded. | keep. Only template **source** names change. |
| `/etc/environment` keys (`pistat_host`, `SNMP_SWITCH_*`, `mpi_port`, `pi_ports`, `nfs_*`) | setup-pi (in the root), fpgas-online-poe, `maintenance.sh` | keep |
| accounts `pi` (jump), `ansible`, the server user | ssh from operators and the web terminal | keep |
| inventory host `fpgas.online` → `welland.fpgas.online` | `--limit` in docs, ten64 `.worktrees/main-deploy` + `-e @vars.json`, `refresh-known-hosts.yml` pins, the host_vars filename | owner decision. If done, do it alone. |
| retired var names in `-e` files and host_vars off-repo (e.g. ten64 vars.json; an old `site_poe_package_override` name already exists in operator notes) | branch deploys | with each var rename PR, add the old name to a **retired-vars guard** (a task at the top of site.yml/web.yml that fails if a retired name is defined), so an old override fails loudly and is not silently ignored |
| role-dir renames of `img`, `nspawn_pi` | the CI base/upgrade **stage keys** (`nfsroot_inputs.py`) | expect a one-off full RasPiOS download + upgrade-stage rebuild on that PR. Other image-role renames only produce a new image. Check that `nfsroot_diff.py` shows no content change before merging. |
| tag renames | runbooks, operator notes (the `--tags pi,fpgas-apt,onpi,cam` NFS-root command is already stale) | update the runbooks in the same PR. Tell the owner which commands changed. |

---

## 5. Execution order

Each step is one small PR that stands alone and is based on main. Merge them one at a time. Every PR updates README/CLAUDE.md/runbook references, `verify-server.yml` includes and `tests/` in the same change. Dated plans and specs are not rewritten.

| # | PR | contents | blast |
|---|---|---|---|
| 1 | naming conventions + guard | add section 1 to CLAUDE.md; add the empty retired-vars guard task | X |
| 2 | delete dead site tasks | `site/tasks/{pib,js_player,pibdemos,pibup,pibfpgas,switch}.yml` + their tags; fold `index.yml` | X |
| 3 | delete dead onpi tasks | `onpi/tasks/{arty_*,tmux,pistat}.yml` | C (new image, no content change) |
| 4 | delete dead inventory | orphan host_vars, `[pxe]` group, the unused vars in 2.5.1, `nspawn_pi_sshd_port`, unused pxe handlers + `interfaces-static.j2`, wssh gunicorn copies | X |
| 5 | handler names | the 2.6 renames | X |
| 6 | owner decision: `fixpi_server_monitor` tasks and `resolve.conf` | delete or move | C |
| 7 | `vlan_ports` → `port_vlans` | dir, tag, task names | D (tag) |
| 8 | `mqtt` → `fleet_broker`; `fleet_broker` var → `fleet_enabled` | | D |
| 9 | `wssh` → `web_terminal` | role only; the unit stays `wssh` | D |
| 10 | `netif` → `nics` | | D C |
| 11 | `nfs` → `nfs_server` | | D |
| 12 | `pxe` → `dnsmasq` | + `dhcp_range`, `pxe_test_clients` | D |
| 13 | `site` → `website` | + `site_*` → `website_*`, `le` → `tls`, `snmp` → `poe_env`, task files | D C |
| 14 | `ttsite` → `tt_website` | + `ttsite_*`, `tt_commander_*` vars | D C |
| 15 | `fixpi` → `nfsroot_config` | + `pipw` → `pi_password` (workflow skip-tags), task files, task names | D C |
| 16 | `onpi` → `nfsroot_packages` | + `tftpd_port` moved in, internal tags removed | C |
| 17 | `nspawn_pi` → `nfsroot_chroot` | + `nfsroot_build_deb_cache` | C (upgrade stage rebuild) |
| 18 | `img` → `nfsroot_image` | + `img_nfsroot_image` → `nfsroot_image_ref` | D C (base stage rebuild) |
| 19 | RasPiOS vars | `srv.yml` → `nfsroot.yml`, `img_host`/`dir_date`/... → `raspios_*`, `dist` → `raspios_release` | C D |
| 20 | `nfs_root` → `nfsroot_dir` | one mechanical PR (~230 refs) | D C |
| 21 | account vars | `user` → `pi_user`, `user_name` → `server_user_name`, `pi_pw` → `pi_password` | D C |
| 22 | LAN and domain vars | `pib_network*` → `lan_prefix*`, `pib_domain` → `lan_domain`, `domain` → `apex_domain`, `domain_name` → `site_fqdn`, `streaming_frontend_*`, `conference_name` → `nginx_prefix` | D |
| 23 | switch vars | `switch` → `poe_switch`, `nos` → `pis`, `switches_manage` | D C |
| 24 | groups | `nbp` → `gateway`, `pig` → `web`, CI `pi` → `pi_root` | D C |
| 25 | playbook files | `ci-nfsroot*.yml` → `nfsroot-*.yml`, runner → `tasks/ci-runner.yml` | C (base+upgrade rebuild) |
| 26 | `tests/ci/` → `ci/` | | C |
| 27 | task-name sweep for the kept roles | section 3 rows for apt_cache, jump, sshd, and the verify prefix | X |
| 28 | (optional, owner) host `fpgas.online` → `welland.fpgas.online` | | D M |

PRs 7 to 14 touch disjoint files and can go in any order. PRs 15 to 18 each produce a new NFS root image, so merge them one at a time and check the image after each. PRs 19 to 24 touch many files at once, so rebase each onto main right before merging, and time them for a quiet point in the other open PRs.
