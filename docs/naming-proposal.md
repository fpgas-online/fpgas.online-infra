# Naming proposal

Status: proposal only. Nothing here has been renamed yet (PR #164).
Based on `origin/main` at `4de0b24` (2026-09-29). Second draft: every proposed
name was checked for confusion (see the "confusion check" column and the last
section). Third draft: the owner's decisions are applied (section 6).

Many names here come from CarlFK/pici and, before that, the Debian videoteam
Ansible (`fixpi`, `onpi`, `nbp`, `pig`, `pib`, `conference_name`, `tweeks`).
They no longer describe what the code does in fpgas.online. This document
lists every named thing, what it really is (taken from the code), a proposed
name and how far each rename reaches.

**Tags are out of scope.** They are not being renamed; they are being removed
(issue #157: deploys and verification run whole playbooks, never
`--tags`/`--skip-tags`). Section 2.4 keeps only the notes #157 needs.

**How each name was checked.** A name passes the confusion check when a
reader who knows Ansible and fpgas.online cannot reasonably read it as
something else. The check covers: Ansible keywords and modules; Debian
packages, services and units; other names in this repo; fpgas.online terms
(site = a location such as welland or ps1; gateway; board; fleet; root;
image); where the name runs (gateway, CI chroot, the Pi at boot, web tier);
and what it acts on.

**Blast radius codes** (used in every table):

| code | meaning |
|---|---|
| **D** | deploy-affecting: operator commands (`--limit`, `-e`, playbook paths), inventory groups, or a var that host_vars, group_vars or `-e` may set. After the rename, Ansible silently ignores the old var name. |
| **C** | CI-affecting: `tests/`, `.github/workflows`, `tests/ci/nfsroot_inputs.py` paths (a role-directory rename changes the NFS root image input key, so CI builds a new image) |
| **X** | cosmetic: task names, play names, comments, file names inside a role that nothing outside references |
| **M** | migration: a live path, unit name or file on a deployed host, or a name another repo uses. See section 4. |
| – | keep: the name is already short and descriptive |

---

## 1. Conventions

| subject | rule |
|---|---|
| Role names | Name the role after the **component** it manages, in `snake_case`, 1–2 words. Do not prefix by host. Roles that exist to build or maintain the Pi NFS root get the prefix `nfsroot_` (as `nfsroot_generation` already has): `nfsroot_image`, `nfsroot_chroot`, `nfsroot_netboot`, `nfsroot_site`, `nfsroot_apt`, `nfsroot_cam`, `nfsroot_packages`. The one exception is `uhubctl`, which keeps its tool name (decision 6). |
| Where a role runs | Record where a role runs in the playbook that uses it, not in its name. The gateway roles are in `site.yml`, the web tier roles are in `web.yml`, and the image roles are in `ci-nfsroot*.yml`. Each role's single `README.md` says which of these runs it. |
| Variable prefix | Every var a role defines (defaults, vars, `set_fact`, `register`) starts with `<role>_`. ansible-lint `var-naming[no-role-prefix]` enforces this, so renaming a role renames all its vars. |
| Inventory vars | Vars that several roles read (site facts) use a short **topic prefix**: `nfsroot_`, `raspios_`, `lan_`, `site_`, `eth_uplink_`, `eth_local_`, `fleet_`, `tt_`. The word `site` is used **only** for the location (welland, ps1) and never for the Django app. |
| Cross-role files | A role that reads another role's file (`{{ role_path }}/../<role>/...`) is part of that role's rename. Today there are four: `site/tasks/pistat.yml` → `pxe/files`, `fixpi/tasks/fleet-site.yml` → `onpi/templates`, `fixpi/tasks/tt-site.yml` and `onpi/tasks/tt.yml` → `ttsite/templates`. |
| Tags | None. See #157. |
| Handler names | `Restart <unit>` or `Reload <unit>`, using the systemd unit name. Add a ` (<role>)` suffix only when two roles in one play need different actions under the same name. |
| Task names | See the style rule below. |
| Task files | Name each task file for what it does, in `kebab-case` (the most common style in the repo today), e.g. `pi-password.yml` rather than `userconf.yml`. Never `tweaks.yml`, `misc.yml` or similar. |

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

### 2.1 Roles (29 today, 32 after the split and the two new roles)

"Runs" says where: **gw** = gateway (site.yml), **web** = web tier (web.yml), **CI** = the image build (`ci-nfsroot*.yml`; `chroot` = inside the root through the chroot connection).

| current | proposed | runs | what it is | confusion check | blast |
|---|---|---|---|---|---|
| `apt_cache` | keep; rename `tasks/nfsroot.yml` → `tasks/root-sources.yml` | gw | apt-cacher-ng + nginx TLS front end; `root-sources.yml` rewrites the NFS root's apt sources (including those `nfsroot_apt` added) to go through the cache | clear. The task-file rename removes the clash with the role `nfsroot_apt`; both READMEs say "nfsroot_apt adds the repos in CI; apt_cache points them at this site's cache on the gateway". The file stays in `apt_cache` because it needs `apt_cache_remaps` and `apt_cache_host` | – |
| `apt_client` | keep | gw | the gateway's own apt proxy config (`01site-proxy`) | clear | – |
| `automation_user` | keep | gw | the `ansible` automation account | clear | – |
| `cam_pi` | **`nfsroot_cam`** | CI chroot | installs GStreamer and `fpgas-online-cam` into the NFS root, enables `cam.service` | clear: the family prefix says it is the camera software inside the root, apart from `stream_server`/`webrtc` on the web tier | C |
| — (new) | **`chrony`** | gw | chrony as the board LAN's NTP server (install + `allow <lan>/16` + handler), moved out of `pxe` | clear: named after the one daemon it manages; the gateway's own time sync is the same daemon | X |
| `firewall` | keep | gw | nftables rules + IPv4/IPv6 forwarding | clear (alt `nftables` names the tool, not the job) | – |
| `fixpi` | **split** into `nfsroot_netboot` + `nfsroot_site` (map in 2.10) | CI + gw | file-level edits to the root and its boot/TFTP tree. Today both runs do almost all of it; only the ARM-code tasks are gated to CI by `fixpi_image_build` | see the two rows below | D C M |
| — (from `fixpi`) | **`nfsroot_netboot`** | CI only | turns the RasPiOS tree into a read-only netboot root: cmdline/fstab, users and sudo, sshd on, first-boot/resize/swap off, config.txt, sunxi kernel bake, nfs-common | candidates: `nfsroot_netboot` (risk: "netboot" also names the whole DHCP/TFTP chain, and fpgas.online-netboot-pi; the `nfsroot_` prefix confines it to the root, and its main file is already `netboot.yml`); `nfsroot_base` (collides with the CI **base stage**, `ci-nfsroot-base.yml`); `nfsroot_boot` (reads as the `boot/` partition only); `nfsroot_diskless` (accurate, but not a word used anywhere in the project). **Pick `nfsroot_netboot`** | C |
| — (from `fixpi`) | **`nfsroot_site`** | gw only | applies this location's values to the pulled root and publishes its boot files: pi password, logins and keys, ssh host keys, authorized_keys, TT catalogue, fleet.toml, `pistat_host`, timesyncd server, the legacy TFTP tree and the sunxi TFTP payload | candidates: `nfsroot_site` (site = location, the convention's meaning; matches the existing "site layer", `tt-site.yml`, `fleet-site.yml`; the old `site` role is gone by then); `nfsroot_local` (clashes with `eth-local` and Ansible's `local` connection); `nfsroot_values` (misses the TFTP publishing and host keys); `nfsroot_deploy` (reads as the pull, which is `nfsroot_image`). **Pick `nfsroot_site`** | D |
| `fpgas_apt` | **`nfsroot_apt`** | CI chroot | adds the fpgas.online, fpga-tools and nfsroot-watchdog apt repos inside the NFS root | clear once `apt_cache/tasks/nfsroot.yml` is renamed `root-sources.yml` (row above). Could read as "installs packages"; its README says "repos and keys only; packages are `nfsroot_packages`" | C |
| `img` | **`nfsroot_image`** | CI + gw | CI base stage: downloads and extracts RasPiOS (`build.yml`). Gateway: podman pull of the GHCR image and extraction to `nfs_root` (`prefetch.yml`, `pull.yml`) | minor risk: could read as "builds/publishes the image" (that is the workflow + `tests/ci`). Alternatives: `nfsroot_fetch` (collides with `ansible.builtin.fetch`), `nfsroot_pull` (wrong for CI's download), `nfsroot_extract` (misses the prefetch). Keep `nfsroot_image`: both halves turn an image into the root tree | D C |
| `jump` | keep | gw | the restricted jump account (`pi`) used to hop to the Pis | minor: can read as a verb; the account name `pi` also equals the Pi login (`pi_user`). Alt `ssh_jump`. Keep | – |
| `lldp` | keep | gw | lldpd | clear | – |
| `mqtt` | **keep** (decided) | web | mosquitto with the fleet listener config; shared with sensors2mqtt | clear. `fleet_broker` was rejected: the broker is shared, and it is the bool var being retired | – |
| `netif` | **`nics`** | gw | names the two NICs by MAC (`eth-uplink`/`eth-local`), uplink networkd, static resolv.conf, reboot after a rename | risk: a reader may look here for the eth-local addresses (they are in `vlan_ifaces`). Alternatives: `uplink` (misses the naming), `network` (too broad; reads as `networking.service`), `nic_names` (misses the uplink). Keep `nics`; say "eth-local is addressed by vlan_ifaces" in the README | C (`tests/test_netif.py`) |
| `nfs` | **`nfs_server`** | gw | nfs-kernel-server: exports the NFS root on eth-local only, creates the export dirs | clear: `_server` keeps it apart from the `nfsroot_*` roles and from `nfs-common` in the root | X |
| `nfsroot_generation` | keep | gw | takes the root-update lock, bumps the generation, releases the lock (nfsroot-watchdog-server) | clear | – |
| `nspawn_pi` | **`nfsroot_chroot`** | CI (+ gw verify) | CI: bind mounts, policy-rc.d, the deb cache, initramfs suppression and kernel pruning so apt can run inside the root. Its verify runs on the **gateway** (verify-server.yml) and installs `/usr/local/sbin/nfsroot-kernels` there. No systemd-nspawn any more | clear: matches the `community.general.chroot` connection it prepares | C D |
| `onpi` | **`nfsroot_packages`** | CI chroot | apt upgrade, setup-pi, TT bridge, fleet units, the Pi's own atftpd port, FPGA boot check, nfsroot-watchdog, drops `nfsvers` from cmdline.txt. Never runs on a Pi (only in the CI chroot) | risk: "packages INTO the root, or packages that SERVE it?" The `nfsroot_` family means "the root's content"; the NFS server is `nfs_server`, so it reads INTO. Not unique: `nfsroot_cam` and `nfsroot_apt` also install into the root; this is the general set. Alternatives: `nfsroot_software` (vaguer), `pi_packages` (leaves the family), `nfsroot_pi` (reads as the host). Keep | D C |
| `operators` | keep | gw | operator accounts, keys, sudo, retired accounts | clear | – |
| `pxe` | **`dnsmasq`** (decided; chrony moves to `chrony`) | gw | dnsmasq DHCP/TFTP/auth DNS (`base.conf`, `ports.conf`, legacy MAC tables, the "Raspberry Pi Boot" service). The Pis do not PXE | clear once chrony is out: named after the one daemon it manages | D C (`site/tasks/pistat.yml` reads `../pxe/files/send_stat.conf`) |
| — (new) | **`serial_monitor`** | gw | the gateway side of watching a Pi's serial console: remove brltty, install tio, add the server user to `dialout`, mask the ttyAMA0 getty; on a Pi gateway, enable `/dev/serial0`. Moved out of fixpi's `tweeks.yml`; replaces `fixpi_server_monitor` | candidates: `serial_monitor` (says "watch serial lines"); `serial_console` (reads as *providing* a console on the gateway, the opposite of masking its getty); `tio` (the role does more than install tio); `console_tap` (unfamiliar). **Pick `serial_monitor`**. Minor risk: "monitor" as in monitoring; the README says "serial lines, not metrics" | X |
| `server_user` | keep | web | the host's own admin login (`user_name`), incl. the in-place rename from `videoteam` | minor: "server" beside the `gateway` group. It runs in web.yml, so a host-neutral word is right. Keep | – |
| `site` | **`website`** | web | nginx vhost + certbot, the fpgas-online-site Django install, gunicorn/uvicorn/daphne, fleet consumer, pistat (redis + a dnsmasq drop-in) and the PoE env | clear. Minor risk: could be read as the apex fpgas.online landing site; the README says "this location's Django site". Alternatives: `web_app`, `django_site` (reads as Django's Sites framework), `django` (reads as "installs Django"). Keep `website` | D C (heavy) |
| `ssh_key_fetch` | keep | gw | library role: downloads `gh:`/`lp:` keys, fails when a download is empty | clear | – |
| `sshd` | keep | gw | pubkey-only drop-in with a lockout guard | clear | – |
| `stream_server` | keep | web | nginx-rtmp ingest from the Pi cameras + HLS output | clear | – |
| `switch_vlans` | keep | gw | installs `fpgas-switch-setup` and `switches.yml`, converges the **switches'** VLANs | clear once its gateway-side sibling is `vlan_ifaces` | – |
| `ttsite` | **`tt_website`** | web | the tinytapeout.fpgas.online vhost, Commander embed bundles, the TT board catalogue in Django | clear, pairs with `website`. `tinytapeout` would read as the TT bridge software (that is in `nfsroot_packages`) | D C (inputs list + two `../ttsite/templates` readers) |
| `uhubctl` | **keep**, and move it into the NFS root (decided; section 5.1) | today: nobody (`[uhubctl]` is empty). After: CI chroot | uhubctl + a udev rule for a pici-era external D-Link DUB-H7 hub. The FPGA boards hang off each **Pi's** USB ports, so switching must run on the Pi | clear. Keeps its tool name (the exception to the `nfsroot_` family) | C |
| `vlan_ports` | **`vlan_ifaces`** (was `port_vlans`) | gw | the gateway's networkd VLAN netdevs/networks, one per switch port, + the eth-local trunk address | `port_vlans` fails: beside `switch_vlans` it reads as switch-side config, and it takes the name of the `port_vlan_map` filter that firewall and dnsmasq also use. Alternatives: `vlan_netdevs` (networkd jargon), `lan_vlans`. `vlan_ifaces` says "this host's interfaces" | X |
| `webrtc` | keep | web | mediamtx WHEP leg for the camera streams | clear (alt `cam_webrtc`) | – |
| `wssh` | **`webssh`** (was `web_terminal`) | web | the webssh backend (`wssh.service`/`.socket`, pip `webssh`) + its nginx include | `web_terminal` could mean the Django terminal page, or ttyd/xterm.js. `webssh` names the component the role installs (convention), and the unit `wssh` is its binary name | X M (unit names stay) |

### 2.2 Playbooks and plays

| current | proposed | what it is | confusion check | blast |
|---|---|---|---|---|
| `site.yml` | keep | full converge of the gateway + web tier + NFS root update | "site" here is Ansible's standard top playbook name; alt `gateway.yml`. Keep | – |
| `web.yml` | keep | web tier play, imported by site.yml | clear, matches group `web` | – |
| `verify-server.yml` | keep | server-side checks | alt `verify-gateway.yml` (it also verifies the web tier). Keep | – |
| `verify-pi.yml` | keep | live Pi checks | clear | – |
| `refresh-known-hosts.yml` | keep | re-pins the controller's known_hosts | clear | – |
| `ci-nfsroot-base.yml`, `ci-nfsroot-upgrade.yml`, `ci-nfsroot.yml` | **keep** (was `nfsroot-*.yml`) | CI stages 1–3 | the `ci-` prefix is the only thing that says WHERE they run. `nfsroot-image.yml` for stage 3 would collide with the role `nfsroot_image`, which stage 3 does not run. Renaming also rekeys the base/upgrade stages | – |
| `ci-nfsroot-runner.yml` | `tasks/ci-runner.yml` | not a playbook: a task list the three above import (binfmt fix) | clear | C |
| play "Configure the server network interfaces" | "Configure the gateway NICs" | site.yml play 1 | clear | X |
| play "Configure the server services" | "Configure the gateway services" | site.yml play 3 | clear | X |
| play "Start pulling the Pi NFS root image", "Update the Pi NFS root", "Deploy the web tier" | keep | | clear | – |
| play "Configure USB hub power control", "Verify uhubctl" | **delete** | the role moves into the CI build and verify-pi (5.1) | – | X |
| play "Verify server roles (nbp)" | "Verify the gateway" | | clear | X |
| play "Verify web roles (pig)" | "Verify the web tier" | | clear | X |
| play "Verify running Pi" | "Verify the running Pis" | | clear | X |
| play "Apply the generic fixpi layer to the NFS root" | "Make the NFS root netbootable" (runs `nfsroot_netboot`) | CI | clear | X |
| task "Apply the site layer to the NFS root" (site.yml) | keep (runs `nfsroot_site`) | gw | clear | – |
| play "Install the Pi roles into the NFS root" | "Install the Pi software into the NFS root" (+ `uhubctl`) | CI | clear | X |
| plays "Prepare the NFS root chroot", "Finish, clean and check the NFS root image", "Download and extract the RasPiOS base image", "Prepare the RasPiOS tree for the upgrade", "Upgrade the RasPiOS packages", "Finish and clean the upgraded stage", "Refresh pinned host keys ..." | keep | | clear | – |

### 2.3 Inventory groups, hosts and files

| current | proposed | what it is | confusion check | blast |
|---|---|---|---|---|
| group `nbp` | **`gateway`** | the netboot gateway (tweed, val2). In the CI inventory it is the build runner (`localhost`) standing in for it. Read by `hostvars[groups['nbp'][0]]` in `onpi/tasks/tt.yml`, `fpgas_apt/defaults/main.yml` and `verify-pi.yml` | clear; matches the tweed-split design, where the gateway VM keeps these roles. In CI it means "the host holding the root", which is what the shared lookups need. Alt `server` (clashes with `server_user`, too generic) | D (`--limit nbp`) C |
| group `pig` | **`web`** | hosts running the web tier (web.yml) | clear, matches `web.yml` and the tweed-split web VM. Alt `web_tier` | D C |
| group `pxe` | **delete** | defined in `inventory/hosts` but no play targets it | – | D (nil) |
| group `uhubctl` | **delete** (both inventories) | empty since 2026-09-04; the role moves to the CI build (5.1) | – | D (nil) |
| group `pi` (CI inventory) | **`pi_chroot`** (was `pi_root`) | the extracted root reached by the chroot connection; its host is `nfsroot` | `pi_root` reads as the Pi's `root` account (fixpi manages both `pi` and `root` keys). Alternatives: `nfsroot` (Ansible warns when a group and host share a name), `chroot` (loses "Pi"). `pi_chroot` says both what and how | C. `verify-pi.yml`'s default `pi_live:pi` is **not** affected: nothing runs verify-pi on the CI inventory, so the `:pi` is already dead. Drop it, and the stale `-i ansible/inventory --limit pi` usage line |
| group `pi_live` (tests) | keep | the booted virtual Pi | clear | – |
| host `fpgas.online` | **`welland.fpgas.online`** (decided; its own announced PR, section 4.1) | the Welland gateway (tweed). The name reads like the apex | clear | D M |
| host `ps1.fpgas.online` | keep | PS:One gateway (val2) | clear | – |
| host_vars `gator.yml`, `negk.yml`, `rpi-cb-1f-f7.yml` | **delete** | pici hosts that are not in any inventory | – | X |
| `inventory-ci-nfsroot/` | keep | CI build inventory | clear | C |
| `tests/inventory/test-hosts` | `tests/inventory/hosts` | VM test inventory (matches `ansible/inventory/hosts`) | clear | C |
| group_vars file `srv.yml` | `nfsroot.yml` | the RasPiOS image vars, `nfs_root`, `tftp_root`, `user` | clear. Alt `raspios.yml` (misses `nfs_root`/`tftp_root`) | C (listed in `nfsroot_inputs.py`, symlinked in 2 inventories) |
| group_vars file `ci.yml` | fold into `inventory-ci-nfsroot/group_vars/all/all.yml` | holds only `tftpd_port`, which moves to a role default (2.5.1) | clear | C |
| group_vars file `site.yml` | `website.yml` | `django_dir`, `static_dir`, `django_project_name`, `letsencrypt_account_email` | `site` is the location word | X |
| group_vars file `ttsite.yml` | `tt.yml` | `ttsite_domain` + Commander pins, which become `tt_*` inventory vars (2.5.1) | clear | X |
| group_vars `streaming.yml`, `ssh_keys.yml`, `controller.yml`, `firewall.yml`, `all.yml` | keep | | clear | – |
| `inventory-ci-nfsroot/group_vars/all/zz-ci-overrides.yml` | keep | | clear | – |

### 2.4 Tags: removed, not renamed (#157)

The tags are being removed (#157), so this proposal renames none. Role-rename
PRs leave the old tags alone. Ideally #157 lands first, so no rename PR has to
touch a tag. Notes for the #157 work:

| tag use | where | what should replace it |
|---|---|---|
| `--skip-tags pipw,keys` | `.github/workflows/nfsroot-build.yml`, comment in `ci-nfsroot.yml` | The fixpi split (decision 1) removes it: the pi password and keys move to `nfsroot_site`, which CI never runs. If #157 lands first, gate `userconf.yml` and `ansible-home.yml` on `not fixpi_image_build` as a stopgap. |
| `--skip-tags hw-camera,hw-fpga` | `README.md` (verify-pi) | Per-Pi host vars in the existing `verify_pi_` family: `verify_pi_camera: false` and `verify_pi_fpga: false` (default true). `verify_pi_fpga_expect: missing` (test-pi) already covers the boot-check half. |
| `web.yml --check --tags server_user`, and the `--skip-tags` option | `tests/vm/run_tests.py` (lines 385, 392, 651, 701) | **Missed by #157's table.** Replace it with a small playbook that runs only `server_user` in check mode, and drop the option. |
| `always` | site.yml, verify-pi, server_user, apt_cache, nfsroot_generation | #157 drops them with their partial-run workarounds |
| docs that describe tags | comments in `verify-server.yml` (`--skip-tags django`), `web.yml`, `verify-pi.yml` header, `nspawn_pi/tasks/verify/main.yml` (`--tags nspawn-pi`) | rewrite them in #157 |

Fact corrections for #157 from this audit: `mp` is on live tasks in
`onpi/tasks/apt.yml`, not only in dead files, and `fpgas-apt` appears 10
times in `onpi/tasks`, not 6.

### 2.5 Variables

#### 2.5.1 Inventory (group_vars/host_vars) vars

| current | proposed | what it is | confusion check | blast |
|---|---|---|---|---|
| `nfs_root` | **`nfsroot_dir`** | `/srv/nfs/rpi/<dist>`: holds `boot/` and `root/`. ~230 uses (fixpi 101) | minor: `nfsroot_dir/root` reads as "the root's root". Alt `nfsroot_path`, `nfsroot_base`. `_dir` is the shortest clear choice | D C M (value stays) |
| `dist` | **`raspios_release`** | RasPiOS release (`bookworm`) in paths and apt suites | clear. Alt `nfsroot_release` (also covers the Debian armmp kernel for the Orange Pis). Keep `raspios_` to match its siblings | D C (`tests/vm`, `nfsroot_publish.py`) |
| `user` | **`pi_user`** | the Pi login account (`pi`) | clear. Note that `jump_user` is also `pi` (the gateway's jump account); the prefixes keep them apart | D C |
| `user_name` | **`server_user_name`** | the host's own admin account (`admin`; ps1 still `videoteam`) | clear; matches the `server_user` role prefix, as the lint rule wants | D |
| `pi_pw` | `pi_password` | the Pi user's password (public by design) | clear | D |
| `img_host` | `raspios_mirror` | RasPiOS download host (`http://downloads.raspberrypi.org`) | minor: it is the origin, not a mirror. Alt `raspios_host`. Keep: it is the var a mirror override would set | C |
| `dir_date` | `raspios_date` | RasPiOS image date | clear | C |
| `release_date` | delete (alias of `dir_date`) | | – | C |
| `img_path` | **`raspios_url_path`** (was `raspios_dir`) | URL path to the image | `raspios_dir` reads as a local directory, beside `nfsroot_dir` | C |
| `base_name` | delete (inline it) | | – | C |
| `img_name` | `raspios_image` | `.img` file name | clear | C |
| `zip_name` | `raspios_image_xz` | `.img.xz` file name | clear | C |
| `tftp_root` | keep | TFTP root (the NFS root's `boot/` on per-port hosts) | clear | – |
| `tftpd_port` | move to role defaults as `nfsroot_packages_tftpd_port` | the port the **Pi's own** atftpd listens on (6069) | risk: reads as the gateway's TFTP (dnsmasq, port 69). The role prefix says it is inside the root | C |
| `domain` | **delete** (was `apex_domain`) | `fpgas.online` / `test.fpgas.online` in group_vars `all.yml` | **nothing reads it** | X |
| `domain_name` | **`site_fqdn`** | this location's public name (`welland.fpgas.online`); certbot, vhost, Django Site, `pistat_host` in the root. CI sets the apex as a placeholder | clear under the convention (site = location). Alt `web_fqdn` | D |
| `streaming_frontend_hostname` | delete; set `site_fqdn` directly | feeds `domain_name` in `fpgas.online.yml` and `tests/.../test-vm.yml` (+ orphan `gator.yml`) | – | D |
| `streaming_frontend_aliases` | `site_aliases` | extra names for ALLOWED_HOSTS | clear | D |
| `pib_network` | **`lan_ip4_base`** (was `lan_prefix`) | IPv4 prefix string that octets are appended to: `10.21` on per-port sites, `10.21.0` on ps1 | `lan_prefix` reads as a CIDR or a prefix length. The value's shape differs between the two schemes; note it in the var's comment | D (filter plugin, 5 roles) |
| `pib_network6_base` | **`lan_ip6_base`** (was `lan_prefix6`) | IPv6 base of the board LAN | pairs with `lan_ip4_base` and with the filter's `ip4`/`ip6` keys | D |
| `pib_domain` | `lan_domain` | DNS domain of the board hostnames (resolv.conf search, `host-record`) | clear | D |
| `dhcp_range` | `dnsmasq_dhcp_range` | flat-scheme DHCP range (ps1) | clear | D |
| `conference_name` | **`nginx_file_prefix`** (was `nginx_prefix`), value stays `pib` | prefixes nginx include files (`pib-wssh.conf`, `pib-live-hls.conf`, `pib-webrtc.conf`) and the site's nginx log names | `nginx_prefix` collides with nginx's own `--prefix`/`-p` (install prefix). Alt `vhost_prefix` | D M |
| `room_name`, `time_zone`, `common_name`, `subject_alt_names`, `tt06_dev_id`, `switch_base`, `ssh_password_auth`, `firewall_internal_networks`, `firewall_rules` | **delete** | set in inventory, read by nothing (checked) | – | X |
| `django_dir` | keep | `/srv/www/pib` | clear | M (value) |
| `static_dir` | `django_static_dir` | Django STATIC_ROOT | clear | D |
| `django_project_name` | keep | `pib`, the project package from fpgas-online-site | clear | M (other repo) |
| `letsencrypt_account_email` | keep; value → **`admin@fpgas.online`** (decided) | ACME account contact | clear | D (certbot account update) |
| `fixture_path` | `site_fixture` | the site repo's Django board fixture file | clear (per-location data) | D |
| `fleet_broker` | **`fleet_enabled`** | bool: this site runs fleet self-registration (broker, consumer, fleet.toml, verify). Not the broker's address | clear. `fleet` alone would clash with the concept | D C |
| `fleet_site` | keep | fleet site id (`welland`, `ps1`) | clear | – |
| `switch` | **`snmp_switch`** (was `poe_switch`) | dict: the SNMP PoE target (creds, `mac`, `oid`, `mpi_port`) + `nos`. **Not legacy-only**: welland (per-port) sets it too, for the PoE env | `poe_switch` reads as "the PoE switch" when every entry in `switches` is also a PoE switch. `snmp_switch` matches the live `SNMP_SWITCH_*` env keys and `snmp_switch.conf` | D C (tests/vm, 26 site refs) |
| `switch.nos` | `snmp_switch.pis` | the Pi list (port, MAC, serial) for the legacy MAC-table scheme | clear. Alt `.boards` (board = the FPGA) | D |
| `switches` | keep | per-port-VLAN switch list | clear | – |
| `switches_manage` | `switch_vlans_manage` | lets `switch_vlans` push config | clear | D |
| `eth_uplink*`, `eth_local*` | keep | NIC names/addresses | clear | – |
| `sunxi_boards`, `sunxi_default_dtb` | keep | Orange Pi boards and DTB | clear (alt `opi_boards`) | – |
| `streaming`, `tt_boards`, `tt_install` | keep | | clear | – |
| `ttsite_domain` | **`tt_fqdn`** (was `tt_website_domain`) | TT vhost name. An **inventory** var read by `ttsite`, `site` and `webrtc`, so it takes a topic prefix, not a role prefix | pairs with `site_fqdn` | D |
| `tt_commander_embed_version/_sha256`, `tt_commander_legacy_embed_version/_sha256` | keep | Commander bundle pins (inventory, `tt_` topic prefix) | clear | – |
| `ttsite_certbot` | `tt_website_certbot` | follows the role | clear | D |
| `pxe_test_clients` | `dnsmasq_test_clients` | extra DHCP hosts for tests | clear | D |
| `dnsmasq_auth_zone/_glue/_subnet/_interface` | keep (role-prefixed once `pxe` → `dnsmasq`). **`dnsmasq_auth_zone` defaults to `lan_domain`** in the role defaults (decided); set it in host_vars only where they differ (today they are equal on welland, so remove it there) | dnsmasq authoritative zone | clear | D |
| `ssh_imports`, `ssh_imports_revoked`, `ssh_public_keys`, `ssh_public_keys_revoked`, `operators_accounts` | keep | shared jump/operator key inputs | clear | – |
| `nfsroot_build_deb_cache` | `nfsroot_chroot_deb_cache` | CI deb cache dir | clear | C |
| `fixpi_image_build`, `fixpi_generate_host_keys` | **delete** | CI/gateway switches that the split makes unneeded: `nfsroot_netboot` runs only in CI, host keys are generated only by `nfsroot_site` | – | C |
| `fixpi_server_monitor` | **delete**; its tasks move to the `serial_monitor` role (decided) | gated brltty, tio, `dialout` and the getty mask on the build host. The `/boot/firmware/config.txt` edit beside them was gated by a stat only | – | C |
| `fixpi_ansible_user`, `fixpi_ansible_uid` | **`nfsroot_ansible_user`, `nfsroot_ansible_uid`** in group_vars `nfsroot.yml` | the automation account inside the root. Both halves of the split and `verify-pi.yml` read it, so it becomes an inventory var with the topic prefix | clear | C |
| `img_pull_retries`, `img_pull_delay` | `nfsroot_image_pull_*` | set in the test-vm host_vars | clear | C |
| `automation_user_manage`, `sshd_pubkey_only`, `server_user_*`, `apt_client_*`, `apt_cache_enabled`, `firewall_dns_query_sources`, `webrtc_*`, `site_under_construction`, `site_require_fpga_verified` | keep (the `site_*` ones → `website_*`) | | the `site_*` ones collide with the location word until renamed | D (site_* only) |
| `verify_pi_fpga_expect`, `verify_pi_header_uart_console`, `verify_pi_hosts` | keep | | clear | – |
| — (new) | `verify_pi_usb_power_cycle` | bool, default false: allow verify-pi's active USB power-cycle check on this Pi (5.1) | clear, in the play's `verify_pi_` family | – |

#### 2.5.2 Role defaults (122 vars)

Renaming a role renames every var in its defaults and every `register`/`set_fact` name (the lint rule). Only the name changes that go beyond that prefix swap are listed here.

| role → new | current | proposed | confusion check | blast |
|---|---|---|---|---|
| fixpi → split | `fixpi_*` (14) | `nfsroot_netboot_*` or `nfsroot_site_*` per the map in 2.10 | | C D (`-e` possible) |
| | `fixpi_jump_ssh_pubkey` | `nfsroot_site_jump_pubkey` | clear | D |
| | `fixpi_server_user_pubkey` | `nfsroot_site_server_user_pubkey` | `_server_pubkey` would read as the server's **host** key | D |
| | `fixpi_github_key_users`, `fixpi_github_keys_base_url` | `nfsroot_site_github_users`, `nfsroot_site_github_url` | clear | D |
| | `fixpi_authorized_keys_files`, `fixpi_ansible_public_key` | `nfsroot_site_*` | clear | D |
| | `fixpi_sunxi_dtbs`, `fixpi_sunxi_i2c_nodes` | `nfsroot_site_sunxi_*` (the TFTP publish runs on the gateway) | clear | D |
| | `fixpi_sunxi_kernel_package`, `fixpi_sunxi_debian_keyring_*` | `nfsroot_netboot_sunxi_*` (the kernel bake runs in CI) | clear | C |
| fpgas_apt → nfsroot_apt | `fpgas_apt_*` | `nfsroot_apt_*` | clear | C |
| cam_pi → nfsroot_cam | `cam_pi_*` (if any) | `nfsroot_cam_*` | clear | C |
| img → nfsroot_image | `img_nfsroot_image` | `nfsroot_image_ref` | clear (OCI "image reference") | D (branch-deploy override) |
| | `img_cache_dir` (`/var/cache/pib`) | `nfsroot_image_raspios_cache` (value `/var/cache/raspios`) | clear | C |
| | `img_pull_*` | `nfsroot_image_pull_*` | | C |
| onpi → nfsroot_packages | `onpi_nfsroot_watchdog_*` (7) | `nfsroot_packages_watchdog_*` | minor: "watchdog" alone could mean the fleet PoE watchdog (poe #8); the role prefix gives the context | D (`dry_run` is an `-e` knob) C |
| | `onpi_fpga_verify_package` | **`nfsroot_packages_boards_package`** (was `_fpga_check`) | the value is a board-set package (`fpgas-online-all-boards`) that pulls in fpgas-verify; `_fpga_check` reads as a bool | C |
| nspawn_pi → nfsroot_chroot | `nspawn_pi_sshd_port` | **delete** | unused (nspawn era) | X |
| | `nspawn_pi_root`, `nspawn_pi_kernel_*`, `nspawn_pi_max_kernel_trees`, `nspawn_pi_suppress_initramfs` | `nfsroot_chroot_*` | clear | C |
| netif → nics | `netif_uplink_manage`, `netif_allow_reboot` | `nics_uplink_manage`, `nics_allow_reboot` | clear | D (low: not set in inventory) |
| site → website | `site_certbot`, `site_under_construction`, `site_require_fpga_verified`, `site_package`, `site_poe_package`, `site_upload_max_body_size` | `website_*` | clear. `site_package`/`site_poe_package` are **documented `-e` overrides** for branch deploys | D C |
| ttsite → tt_website | `ttsite_boards_path`, `ttsite_daemon_port`, `ttsite_ws_read_timeout` | `tt_website_*` | clear | D |
| | `ttsite_pi_network` | **delete**; read `lan_ip4_base` | a hard-coded `"10.21"` copy of `pib_network` | D |
| uhubctl | `uhubctl_usb_hubs` | keep the name; retarget from the D-Link DUB-H7 to the Pi onboard hubs (5.1) | clear | C |
| everything else (`apt_cache_*`, `apt_client_*`, `automation_user_*`, `firewall_*`, `jump_*`, `nfsroot_generation_*`, `operators_*`, `server_user_*`, `sshd_*`, `ssh_key_fetch_*`, `switch_vlans_*`, `webrtc_*`) | keep | | | – |

### 2.6 Handlers (31)

| current | role(s) | proposed | notes | blast |
|---|---|---|---|---|
| `Reload-systemd` | cam_pi, onpi, stream_server | `Reload systemd` | site and wssh already use `Reload systemd` | X |
| `Udev-reload` | uhubctl | `Reload udev` | | X |
| `Exportfs` | nfs | `Reload NFS exports` | runs `exportfs -r` | X |
| `Reload sshd for pubkey-only` | sshd | keep | the suffix keeps it apart from jump's `Reload sshd` | – |
| `Restart network-manager`, `Restart networking` | pxe | **delete** | nothing notifies them (checked) | X |
| `Restart chrony` | pxe | moves to the new `chrony` role | | X |
| `Restart dnsmasq` | site | keep; moves with `pistat.yml`'s dnsmasq drop-in if that goes to `dnsmasq` | the website role notifying dnsmasq is surprising; see 2.7 | X |
| `Restart apt-cacher-ng (apt-cache)`, `Reload nginx (apt-cache)` | apt_cache | keep | the suffix pattern is fine | – |
| `Reload nginx` | site, ttsite, wssh, stream_server, webrtc | keep | same action everywhere | – |
| `Restart nginx`, `Restart mediamtx`, `Restart mosquitto`, `Restart wssh`, `Restart fleet-consumer`, `Restart django services`, `Reload nftables`, `Reload networkd`, `Reload sshd`, `Reload systemd` | various | keep | | – |

### 2.7 Task files, templates and files inside roles

| role | current | proposed | what it is | confusion check | blast |
|---|---|---|---|---|---|
| fixpi | all task files | split between `nfsroot_netboot` and `nfsroot_site`, with renames; see **2.10** | | | C D |
| fixpi | `templates/resolve.conf.j2` + its task | **delete** (decided) | writes `/etc/resolve.conf` (misspelt, so nothing reads it); also drop its `tests/ci/nfsroot_manifest.py` entry | – | C |
| fixpi | `files/etc/network/interfaces.d/eth1.conf` | delete | pici static 192.168.100.100. Copied into the root, but ifupdown is masked there | – | C |
| fixpi | `files/scripts/maintenance.sh`, `production.sh` | **delete** | TODO stubs (`# TODO: Implement based on original monorepo logic`); the only caller is `when: false` | – | X |
| fixpi | `files/scripts/chroot-mount-pi-fs.bash` | move to `nfsroot_netboot`; stop installing it on the gateway | used only by the CI-only tasks (useradd, nfs-common, sunxi kernel bake) | clear | C M |
| fixpi, onpi, wssh | `notes.txt` | delete, or fold into the role README | pici notes | – | X |
| onpi | `tasks/arty_blink.yml`, `arty_here.yml`, `arty_wire.yml`, `tmux.yml`, `pistat.yml` | **delete** | nothing includes them (checked) | – | X C |
| onpi | `tasks/tweeks.yml` | `tasks/pi-dirs.yml` | Uploads/Downloads dirs | clear | X C |
| onpi | `tasks/nonfs.yml` | `tasks/nfs-version.yml` | drops `nfsvers=4.2` from cmdline | clear | X C |
| onpi | `tasks/stale_root.yml` | **`tasks/nfsroot-watchdog.yml`** (was `watchdog.yml`) | nfsroot-watchdog | `watchdog.yml` could mean the hardware watchdog or the fleet PoE watchdog; use the package name | X C |
| onpi | `tasks/fpga_verify.yml` | **`tasks/fpgas-verify.yml`** (was `fpga-check.yml`) | the FPGA boot check | the product is `fpgas-verify` (package + unit); `fpga-check.yml` adds a third name and reads like a `verify/` file | X C |
| onpi | `tasks/tftpd.yml`, `apt.yml`, `fleet.yml`, `tt.yml` | keep | | clear | X C |
| onpi | `templates/fleet.toml.j2` | move to `nfsroot_site`: its only reader is `fixpi/tasks/fleet-site.yml` | | cross-role read | C |
| site | `tasks/js_player.yml`, `pibdemos.yml`, `pibup.yml`, `pibfpgas.yml`, `switch.yml` | **delete** | empty or placeholder includes | – | X |
| site | `tasks/pib.yml` | **delete** | not included by anything | – | X |
| site | `tasks/index.yml` | fold into `django.yml` | creates `static_dir` | – | X |
| site | `tasks/snmp.yml` | `tasks/poe-env.yml` | PoE switch env in /etc/environment | clear | X |
| site | `tasks/apt.yml` | `tasks/packages.yml` | | clear | X |
| site | `tasks/fpgas-online-site.yml` | `tasks/install.yml` | pip-installs site + poe | clear | X |
| site | `tasks/pistat.yml` | keep; its `send_stat.conf` source must follow `pxe` → `dnsmasq` | redis + a dnsmasq drop-in from `../pxe/files` | cross-role read | C |
| site | `templates/includes/{pibfpgas,pibup,pistat,snmp_switch}.conf.j2` | keep | named after the site repo's Django apps | | M |
| pxe | `templates/pibs.conf.j2` | `templates/mac-hosts.conf.j2` (dest stays `pibs.conf`) | legacy MAC-table DHCP hosts | clear | X |
| pxe | `templates/interfaces-static.j2` | **delete** | unused (checked) | – | X |
| pxe | `files/rpi.conf`, `files/send_stat.conf` | keep | dest file names on the gateway | | M |
| stream_server | `templates/pib.conf.j2` | `templates/rtmp-app.conf.j2` (dest stays) | RTMP application `pib` | clear | X |
| stream_server | `tasks/base.yml`, `tasks/back.yml` | `tasks/nginx-rtmp.yml`, `tasks/hls.yml` | base = nginx + RTMP module setup; back = HLS storage, the RTMP app and the HLS include | `rtmp.yml` undersold base (it installs nginx) | X |
| wssh | `files/etc/systemd/system/gunicorn.service`, `gunicorn.socket` | **delete** | unused copies (checked) | – | X |
| wssh | `files/etc/nginx/includes/wssh.conf` (used as a template) | `templates/wssh.conf.j2` | | clear | X |
| img | `files/img2files.sh` | keep | | | – |
| nspawn_pi | `files/nfsroot_kernels.py` | keep | installed as `/usr/local/sbin/nfsroot-kernels` | | C (`tests/test_nfsroot_kernels.py` reads the path) |
| mqtt | `templates/fpgas-fleet.conf.j2` | keep | | | – |

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
| `/etc/nginx/includes/pib-*.conf`, `/etc/nginx/rtmp/pib.conf`, `/etc/dnsmasq.d/{base,ports,pibs,switch,rpi,send_stat}.conf` | site, wssh, stream_server, webrtc, pxe | gateway |
| `/etc/fpgas/switches.yml`, `/opt/fpgas-switch/venv`, `/usr/local/venv/wssh`, `/usr/local/sbin/{maintenance,production}.sh`, `/usr/local/sbin/chroot-mount-pi-fs.bash`, `/usr/local/sbin/nfsroot-kernels`, `/usr/local/bin/jump-shell` | switch_vlans, wssh, fixpi, nspawn_pi, jump | gateway |
| `/etc/fpgas-online/{tt-boards.yaml,fleet.toml}` | fixpi, onpi, ttsite | root and gateway |

### 2.9 `tests/` and `docs/` directories

| current | proposed | what it is | confusion check | blast |
|---|---|---|---|---|
| `tests/ci/` | **`ci/`** | not tests: the NFS root build/publish/promote tooling (`nfsroot_publish.py`, `_inputs.py`, `_stages.py`) | minor: a top-level `ci/` can read as CI config (that is `.github/`). Alt `tools/nfsroot/`. Keep `ci/` | C (workflows, tests import it) |
| `tests/vm/`, `tests/lab/`, `tests/inventory/`, `tests/test_*.py` | keep | | clear | – |
| `docs/superpowers/{plans,specs,runbooks}` | keep; move `runbooks/` to `docs/runbooks/` | runbooks are operator docs, not plan artefacts | clear | X |
| `docs/hardware/`, `docs/rebuilds/` | keep | | | – |
| dated plans/specs under `docs/superpowers/` | **do not rewrite** | they are historical and quote old names on purpose | | – |

### 2.10 The `fixpi` split (decision 1)

Today the gateway run repeats almost all of fixpi on the pulled image; only
the ARM-code tasks are gated to CI by `fixpi_image_build`. After the split,
each task runs in **one** place. Generic changes then reach the gateways only
through a new image, which is the intent: one owner per file. A gateway that
pins an older image keeps that image's generic layer.

| fixpi source | tasks | new home | runs |
|---|---|---|---|
| `netboot.yml` | back up stock files; write `cmdline.txt`/`cmdline-pi5.txt` and `fstab` (they hard-code `10.21.0.1` and the `nfs_root` path, so they are generic); disable SysRq; pi and ansible users + sudo; hostname service; chroot `resolv.conf`; nfs-common; kernel payload sync; enable ssh.service; disable `regenerate_ssh_host_keys`; mask `userconfig` | `nfsroot_netboot/tasks/main.yml` | CI |
| `netboot.yml` | SSH host keys (generate when absent) | `nfsroot_site/tasks/host-keys.yml` | gw |
| `netboot.yml` | timesyncd → `eth_local_address` (a per-site value) | `nfsroot_site/tasks/timesyncd.yml` | gw |
| `netboot.yml` | `/srv/tftp`, `bootcode.bin` link, per-serial links (legacy MAC-table sites) | `nfsroot_site/tasks/tftp.yml` | gw |
| `sunxi-image.yml` | bake the armmp kernel into the image | `nfsroot_netboot/tasks/sunxi-kernel.yml` | CI |
| `sunxi.yml` | publish the kernel, initrd and DTBs to TFTP, install `device-tree-compiler`, fix up the I2C nodes, write the U-Boot PXE config | `nfsroot_site/tasks/sunxi-tftp.yml` | gw |
| `nogrow.yml` | resize/swap off | `nfsroot_netboot/tasks/no-resize.yml` | CI |
| `tweeks.yml` | the four `boot/config.txt` edits | `nfsroot_netboot/tasks/config-txt.yml` | CI |
| `tweeks.yml` | console-setup, rfkill warning, remove `/etc/hostname`, `/etc` overrides, prompt, ifupdown mask | `nfsroot_netboot/tasks/root-edits.yml` | CI |
| `tweeks.yml` | `pistat_host` in `/etc/environment` | `nfsroot_site/tasks/environment.yml` | gw |
| `tweeks.yml` | `resolve.conf` | delete (decision 8) | – |
| `tweeks.yml` | brltty, tio, dialout, getty mask, "Is server pi", `/dev/serial0` | new role `serial_monitor` (decision 7) | gw |
| `userconf.yml` | static sshd password drop-in, `boot/ssh` | `nfsroot_netboot/tasks/sshd.yml` | CI |
| `userconf.yml` | pi password + Pi reboot, console banner (shows the password), `.ssh` dirs, pi/root user keypairs (per site, never in the public image) | `nfsroot_site/tasks/logins.yml` | gw |
| `userconf.yml` | the server user's own ssh key | `nfsroot_site/tasks/logins.yml`, on the gateway, where the key is used. `server_user` would fit the account better, but it runs in web.yml on the `web` group, which is not the gateway after the tweed split | gw |
| `userconf.yml` | "Get fixed sshswitch" (`when: false`) | delete | – |
| `ansible-home.yml`, `authorized_keys.yml`, `github_keys.yml` | automation user's `.ssh`, authorized_keys, GitHub keys | `nfsroot_site/tasks/{ansible-home,authorized-keys,github-keys}.yml` | gw |
| `tt-site.yml`, `fleet-site.yml` | TT catalogue, fleet.toml | `nfsroot_site` (same names) | gw |
| `manage.yml` + `maintenance.sh`/`production.sh` | stub scripts, `when: false` switch | delete | – |
| `verify/image.yml` | ansible user checks on the image | `nfsroot_netboot/tasks/verify.yml` (ci-nfsroot.yml) | CI |
| `verify/main.yml` | cmdline, TFTP, sunxi, ansible-user and authorized_keys checks on the served root | `nfsroot_site/tasks/verify.yml` (verify-server.yml); drop the maintenance-script checks | gw |
| `verify/pi.yml` | NFS mount, 10.21 address, python3 | delete: nothing includes it (verify-pi covers the same checks) | – |

The split also retires `fixpi_image_build`, `fixpi_generate_host_keys`,
`fixpi_server_monitor` and CI's `--skip-tags pipw,keys`. `site.yml` must
keep running `jump` and `server_user` before `nfsroot_site`, which reads
their public keys.

---

## 3. Task-name cleanups per role (flagged only)

The roles written recently (apt_cache, apt_client, nfsroot_generation,
operators, server_user, ssh_key_fetch, sshd, switch_vlans, webrtc, firewall,
lldp) are fine apart from the rows below. Rows marked **(del)** go away with
dead code. The "role" column uses today's names.

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
| fixpi | Resolve.conf | (del) |
| fixpi | Add time to bash prompt | Add the time to the pi user's prompt |
| fixpi | Config.txt disable onboard Wi-Fi and Bluetooth, enable uart | Disable Wi-Fi and Bluetooth, enable the UART |
| fixpi | Config.txt write-protect the bootloader EEPROM | Write-protect the bootloader EEPROM |
| fixpi | Config.txt enable the 40-pin header UART on Pi 5 | Enable the Pi 5 header UART |
| fixpi | Config.txt dwc2 peripheral mode on Pi 4 / Pi 5 (USB gadget console) | Enable the USB gadget console on Pi 4 and 5 |
| fixpi → serial_monitor | Is server pi | Check whether the gateway is a Pi |
| fixpi → serial_monitor | Enable /dev/serial0 | Enable /dev/serial0 on a Pi gateway |
| fixpi → serial_monitor | Apt remove brltty | Remove brltty |
| fixpi → serial_monitor | Install packages on server | Install tio |
| fixpi → serial_monitor | Let tio connect to the tty and see pi boot messages | Add the server user to dialout |
| fixpi → serial_monitor | Disable (mask) getty systemd service | Mask the ttyAMA0 getty |
| fixpi | Create issue.d dir | Create /etc/issue.d in the NFS root |
| fixpi | Display IP, pw and things on console | Install the console login banner |
| fixpi | Enable sshd | Enable sshd at boot (boot/ssh) |
| fixpi | Get fixed sshswitch (`when: false`) | (del) |
| fixpi | Sshd password settings | Install the sshd password-login drop-in |
| fixpi | Generate ssh keys for server user | Generate the server user's ssh key on the gateway |
| fixpi | Create .ssh dirs | Create root's and pi's .ssh dirs |
| fixpi | Generate ssh keys for pi users pi and root | Generate ssh keys for pi and root |
| fixpi | Set perms (use numeric UID — pi user is UID 1000 on RPi OS) | Give the .ssh dirs to pi (uid 1000) |
| fixpi | Install scripts to manage pi states | (del) |
| fixpi | Put the pi boot system into maintenance mode | (del) |
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
| onpi | Tftpd port systemd | Move the Pi's atftpd socket to tftpd_port |
| onpi | Tftpd port conf | Move the Pi's atftpd to tftpd_port |
| onpi | Pi tftp dir user writable | Let pi write to the Pi's /srv/tftp |
| onpi | Create home/pi/updownload | Create pi's Uploads and Downloads dirs |
| onpi | Tt_boards from the server host | Read tt_boards from the gateway |
| onpi | Tiny Tapeout bridge packages (generic) | Install the TT bridge packages |
| onpi | Tiny Tapeout site catalogue (when this site has TT boards) | Set the TT site catalogue |
| onpi | Nfsroot-watchdog settings for fpgas.online | Configure nfsroot-watchdog |
| onpi | Install the FPGA boot check for every board, and the images it checks against | Install fpgas-verify and its images |
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
| mqtt | Fleet broker config | Configure the fleet listener |
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
| wssh | Install wssh deb packages | Install the webssh packages |
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
| `conference_name: pib`: `/etc/nginx/includes/pib-*.conf`, `/etc/nginx/rtmp/pib.conf`, nginx log names | nginx vhost `include` lines | keep the value. A value change needs a task that removes the old files. |
| RTMP application `pib` (`webrtc_rtmp_source_app`) | fpgas-online-cam publishers on every Pi (in the NFS root) | cross-repo. Keep. |
| `/srv/www/pib`, Django project `pib`, apps `pistat`/`pibup`/`pibfpgas` | fpgas.online-site | owned by the site repo. Keep. |
| unit names `wssh`, `gunicorn`, `uvicorn`, `daphne`, `fleet-consumer`, `mediamtx` | running gateways, monitoring, runbooks | keep. Renaming them means stopping and disabling the old units in the same run. |
| `/etc/dnsmasq.d/*.conf` file names | the running dnsmasq. A renamed file leaves the old one loaded. | keep. Only template **source** names change. |
| `/etc/environment` keys (`pistat_host`, `SNMP_SWITCH_*`, `mpi_port`, `pi_ports`, `nfs_*`) | setup-pi (in the root), fpgas-online-poe | keep |
| accounts `pi` (jump), `ansible`, the server user | ssh from operators and the web terminal | keep |
| inventory host `fpgas.online` → `welland.fpgas.online` | see 4.1 | decided: its own announced PR (step 34) |
| retired var names in `-e` files and host_vars off-repo (e.g. ten64 vars.json; an old `site_poe_package_override` name already exists in operator notes) | branch deploys | with each var rename PR, add the old name to a **retired-vars guard** (a task at the top of site.yml/web.yml that fails if a retired name is defined), so an old override fails loudly and is not silently ignored |
| role-dir renames of `img`, `nspawn_pi` | the CI base/upgrade **stage keys** (`nfsroot_inputs.py`) | expect a one-off full RasPiOS download + upgrade-stage rebuild on that PR. Other image-role renames only produce a new image. Check that `nfsroot_diff.py` shows no content change before merging. |
| cross-role file reads (`../pxe/files`, `../onpi/templates`, `../ttsite/templates`) | `site/tasks/pistat.yml`, `fixpi/tasks/{fleet-site,tt-site}.yml`, `onpi/tasks/tt.yml` | update them in the PR that renames the role they point at. A missed one fails at run time, not at lint. |
| tags | runbooks, operator notes, `tests/vm/run_tests.py` | not renamed; removed by #157 (section 2.4) |

---

### 4.1 Host rename `fpgas.online` → `welland.fpgas.online` (decision 5)

Its own PR, announced to the operators before it merges, near the end of the
sequence (step 34). After it merges, an old `--limit fpgas.online` matches no
host: Ansible only warns and runs nothing, so the change must be announced.

| touches | detail |
|---|---|
| `ansible/inventory/hosts` | the host line in `[gateway]` and `[web]` (`[pxe]` is gone by then) |
| `ansible/inventory/host_vars/fpgas.online.yml` | `git mv` to `welland.fpgas.online.yml`. The vaulted values move unchanged; no re-encryption |
| `--limit fpgas.online` in repo docs | `README.md` (2 commands), the header comments of `web.yml` and `ansible.cfg` (`--limit fpgas.online,pi`, which also names the dead `pi` group), `roles/apt_cache/README.md`, and the runbooks under `docs/superpowers/runbooks/` (tweed web deploy, Orange Pi netboot). Dated plans and specs are not rewritten |
| comments that name the file | `ansible/ssh.cfg` (vault note), `ansible.cfg` (vaulted hosts), `tests/inventory/host_vars/test-vm.yml` (3 comments) |
| known_hosts pins | **no change needed**: `refresh-known-hosts.yml` pins `10.99.21.2`, and ssh connects by `ansible_host`, not by the inventory name. Check that the first run after the rename asks for no host key |
| jump keypair comment | `jump` writes `pi@{{ inventory_hostname }} (jump account)` as the key comment. Check that `openssh_keypair` only rewrites the comment and does not regenerate the key: a new key would change every board's authorized_keys. The `.pub` that `nfsroot_site` authorizes changes by its comment only, so the root gets one new generation |
| off-repo | ten64 `.worktrees/main-deploy` commands (`--limit fpgas.online`), `-e @vars.json` (check for the host name), the operator notes and procedures (web-tier deploy, NFS root update), any cron or script on ten64 that uses the name |
| code | nothing matches `hostvars['fpgas.online']` or the quoted name (checked) |

---

## 5. Execution order

Each step is one small PR that stands alone and is based on main. **One role per PR, one PR open at a time**, merged before the next is opened. Every PR updates the role's README, CLAUDE.md, runbook references, `verify-server.yml` includes, cross-role file reads and `tests/` in the same change. Dated plans and specs are not rewritten. #157 (tag removal) should land before step 9, so that no rename PR has to handle tags.

| # | PR | contents | blast |
|---|---|---|---|
| 1 | naming conventions + guard | add section 1 to CLAUDE.md; add the empty retired-vars guard task | X |
| 2 | delete dead site tasks | `site/tasks/{pib,js_player,pibdemos,pibup,pibfpgas,switch}.yml`; fold `index.yml` | X |
| 3 | delete dead onpi tasks | `onpi/tasks/{arty_*,tmux,pistat}.yml` | C (new image, no content change) |
| 4 | delete dead inventory | orphan host_vars, `[pxe]` group, the unused vars in 2.5.1 (incl. `domain`), `nspawn_pi_sshd_port`, unused pxe handlers + `interfaces-static.j2`, wssh gunicorn copies, verify-pi's dead `:pi` | X |
| 5 | delete dead fixpi code | `resolve.conf.j2` + its task + its `nfsroot_manifest.py` entry (decision 8); `eth1.conf`; `manage.yml` + the stub scripts; the sshswitch task; `verify/pi.yml` | C |
| 6 | handler names | the 2.6 renames | X |
| 7 | new role `serial_monitor` | move brltty, tio, dialout, getty mask and `/dev/serial0` out of fixpi; delete `fixpi_server_monitor` (decision 7) | X C |
| 8 | ACME email | `letsencrypt_account_email: admin@fpgas.online` in group_vars and ps1's host_vars (decision 9). The value is only passed to `certbot certonly` on first issue, so also run `certbot update_account --email admin@fpgas.online` once per gateway (a task, or a noted manual step) | D |
| 9 | `vlan_ports` → `vlan_ifaces` | dir, task names | X |
| 10 | `wssh` → `webssh` | role only; the unit stays `wssh` | X |
| 11 | `netif` → `nics` | | C |
| 12 | `nfs` → `nfs_server` | | X |
| 13 | new role `chrony` | chrony tasks + handler out of `pxe` (decision 4) | X |
| 14 | `pxe` → `dnsmasq` | + `dhcp_range`, `pxe_test_clients`, `site/tasks/pistat.yml`'s path | D |
| 15 | `site` → `website` | + `site_*` → `website_*`, group_vars `site.yml` → `website.yml`, task files | D C |
| 16 | `ttsite` → `tt_website` | + `ttsite_*`, `ttsite_domain` → `tt_fqdn`, drop `ttsite_pi_network`, the two `../ttsite/` reads | D C |
| 17 | extract `nfsroot_site` from `fixpi` | move the gateway tasks per 2.10; site.yml runs `nfsroot_site`; `fixpi` becomes CI-only; drop `fixpi_image_build`, `fixpi_generate_host_keys` and CI's `--skip-tags pipw,keys`; `fixpi_ansible_*` → `nfsroot_ansible_*` | D C |
| 18 | `fixpi` → `nfsroot_netboot` | rename the now CI-only role + its task files | C |
| 19 | `fpgas_apt` → `nfsroot_apt` | + `apt_cache/tasks/nfsroot.yml` → `root-sources.yml` and the two README notes (decision 2) | C |
| 20 | `cam_pi` → `nfsroot_cam` | | C |
| 21 | `onpi` → `nfsroot_packages` | + `tftpd_port` moved in, `fleet.toml.j2` moved to `nfsroot_site` | C |
| 22 | `nspawn_pi` → `nfsroot_chroot` | + `nfsroot_build_deb_cache` | C (upgrade stage rebuild) |
| 23 | `img` → `nfsroot_image` | + `img_nfsroot_image` → `nfsroot_image_ref` | D C (base stage rebuild) |
| 24 | RasPiOS vars | `srv.yml` → `nfsroot.yml`, `img_host`/`dir_date`/... → `raspios_*`, `dist` → `raspios_release` | C D |
| 25 | `nfs_root` → `nfsroot_dir` | one mechanical PR (~230 refs) | D C |
| 26 | account vars | `user` → `pi_user`, `user_name` → `server_user_name`, `pi_pw` → `pi_password` | D C |
| 27 | LAN vars | `pib_network*` → `lan_ip4_base`/`lan_ip6_base`, `pib_domain` → `lan_domain`; `dnsmasq_auth_zone` defaults to `lan_domain` and is removed from welland's host_vars (decision 10) | D |
| 28 | name vars | `domain_name` → `site_fqdn`, `streaming_frontend_*`, `conference_name` → `nginx_file_prefix`, `fleet_broker` → `fleet_enabled` | D |
| 29 | switch vars | `switch` → `snmp_switch`, `nos` → `pis`, `switches_manage` | D C |
| 30 | groups | `nbp` → `gateway`, `pig` → `web`, CI `pi` → `pi_chroot` | D C |
| 31 | runner task file | `ci-nfsroot-runner.yml` → `tasks/ci-runner.yml` | C |
| 32 | `tests/ci/` → `ci/` | | C |
| 33 | task-name sweep for the kept roles | section 3 rows for apt_cache, jump, sshd, and the verify prefix | X |
| 34 | host `fpgas.online` → `welland.fpgas.online` | announced; section 4.1 | D M |
| U | **uhubctl into the NFS root + verify-pi check** | its own PR, not part of any rename; section 5.1. Can land any time, ideally after step 3 | C |

Steps 17 to 23 each produce a new NFS root image, so check the image after each. Steps 24 to 30 touch many files at once, so rebase each onto main right before merging.

### 5.1 uhubctl: switch USB power on the Pis (decision 6)

**Where it must run: on each Pi, from the NFS root.** The FPGA boards (Arty,
Fomu, TT, the NeTV2 JTAG adapters) plug into each Pi's own USB ports, so only
that Pi can switch their power; the gateway has no USB path to them. The
`[uhubctl]` group and its gateway play date from pici, where a Pi server drove
an external D-Link DUB-H7 hub. The `uhubctl` binary is already in the root
(`onpi/tasks/apt.yml`), but nothing configures or tests it.

Hardware limits (from uhubctl's documentation; confirm on ps1 pi7/pi9, the
known-good boards, before relying on them):

| board | what switches | risk |
|---|---|---|
| Pi 4B, Pi 5 | the four USB-A ports are **ganged**: they switch together, and both root hubs (USB2 and USB3) must be switched | every USB device on that Pi drops, including a USB camera grabber (sw1 p12/14/16/18) |
| Pi 3B/3B+ and earlier | the Ethernet chip is on the switched hub | cutting power drops the NFS root and hangs the Pi: **never switch** |
| Orange Pi H3, the VM test Pi | no switchable hub expected | – |

| work item | detail |
|---|---|
| role | `uhubctl` runs in `ci-nfsroot.yml` against `pi_chroot`, after the Pi packages. It installs uhubctl (drop it from onpi's package list). No udev rule at first: `pi` already has passwordless sudo. Retarget or drop `uhubctl_usb_hubs` (it names the D-Link hub) |
| delete | site.yml's "Configure USB hub power control" play, verify-server's "Verify uhubctl" play, the `[uhubctl]` group in both inventories |
| verify-pi, passive check (every run) | run `uhubctl` with no action, which only lists hubs. On a Pi 4/5, assert that it reports a hub with power switching. On other models and the VM Pi, skip with a message instead of failing. Take the model from `/proc/device-tree/model` in the existing collector call |
| verify-pi, active check (opt-in) | runs only when **all** hold: `verify_pi_usb_power_cycle: true` for that Pi; the model is a Pi 4/5; no login session other than the verifier's (`loginctl list-sessions`, which also covers the web terminal, since it logs in over ssh); no process holds a USB serial/JTAG device (`fuser` on `/dev/ttyUSB*`, `/dev/ttyACM*`, `/dev/bus/usb`); no openFPGALoader/openocd running. It records `lsusb`, cycles both root hubs (`uhubctl -a cycle -d 3`), waits up to 30 s, and asserts the same devices came back. An `always:` block runs `uhubctl -a on`, so a failed check never leaves the ports off. Afterwards it restarts `cam.service` if a USB grabber was on the hub |
| why it does not disturb a board in use | off by default; skipped when anyone is logged in or a tool holds the board; never on a Pi 3 or earlier; tried first on the known-good boards. When it does run, the FPGA reloads from its flash, which is the same result as the PoE reset the site already offers |
| VM test | rpi-qemu has no switchable hub, so the passive check takes its skip path and the active check is never enabled |

---

## 6. Decisions (settled)

| # | question | decision | where it lands |
|---|---|---|---|
| 1 | `fixpi`: rename whole or split? | split: **`nfsroot_netboot`** (CI: make the root netbootable) + **`nfsroot_site`** (gateway: per-site values and boot-file publishing) | 2.1, 2.10, steps 17–18 |
| 2 | `fpgas_apt`, `cam_pi` | **`nfsroot_apt`**, **`nfsroot_cam`**; `apt_cache/tasks/nfsroot.yml` → `root-sources.yml` + README notes | 2.1, steps 19–20 |
| 3 | `mqtt` | **keep** | 2.1 |
| 4 | `pxe` | **`dnsmasq`** + a new **`chrony`** role | 2.1, steps 13–14 |
| 5 | host `fpgas.online` | **`welland.fpgas.online`**, its own announced PR near the end | 4.1, step 34 |
| 6 | `uhubctl` | **keep**; move it into the NFS root and add a safe verify-pi check | 5.1, step U |
| 7 | `fixpi_server_monitor` tasks | move to a new gateway role, **`serial_monitor`** | 2.1, step 7 |
| 8 | `resolve.conf.j2` | **delete** with its task and manifest entry | step 5 |
| 9 | `letsencrypt_account_email` | **`admin@fpgas.online`** | step 8 |
| 10 | `dnsmasq_auth_zone` vs `lan_domain` | `dnsmasq_auth_zone` **defaults to `lan_domain`**; set it only where they differ | step 27 |

## 7. Changes from the first draft

| from | to | reason |
|---|---|---|
| tags: rename to role names (section 2.4, convention row, PR steps) | tags removed by #157; no tag renames | owner decision. Kept only the #157 notes: `pipw,keys` → reuse `fixpi_image_build`; `hw-camera,hw-fpga` → `verify_pi_camera`/`verify_pi_fpga`; plus the `run_tests.py` use that #157 missed |
| `mqtt` → `fleet_broker` | keep `mqtt` | the broker is shared with sensors2mqtt, and `fleet_broker` is the bool var being retired |
| `wssh` → `web_terminal` | `webssh` | names the component (pip `webssh`); `web_terminal` could mean the Django page or ttyd |
| `vlan_ports` → `port_vlans` | `vlan_ifaces` | `port_vlans` beside `switch_vlans` reads as switch-side, and it reuses the name of the shared filter |
| `pxe` → `dnsmasq` | `dnsmasq` + a new `chrony` role | the role also runs chrony, so the package name alone was inaccurate |
| CI group `pi` → `pi_root` | `pi_chroot` | `pi_root` reads as the Pi's root account |
| `ci-nfsroot*.yml` → `nfsroot-*.yml` | keep | `ci-` is the only WHERE signal; `nfsroot-image.yml` would collide with the role `nfsroot_image` |
| `domain` → `apex_domain` | delete | nothing reads it |
| `pib_network` → `lan_prefix` | `lan_ip4_base` | "prefix" reads as CIDR or prefix length |
| `pib_network6_base` → `lan_prefix6` | `lan_ip6_base` | pairs with `lan_ip4_base` |
| `conference_name` → `nginx_prefix` | `nginx_file_prefix` | nginx's `--prefix`/`-p` means the install prefix |
| `switch` → `poe_switch` | `snmp_switch` | matches the live `SNMP_SWITCH_*` keys and `snmp_switch.conf`; `switches` are PoE switches too |
| `img_path` → `raspios_dir` | `raspios_url_path` | `_dir` reads as a local directory |
| `ttsite_domain` → `tt_website_domain` | `tt_fqdn` | an inventory var read by three roles, so it takes a topic prefix; pairs with `site_fqdn` |
| `ttsite_pi_network` → `tt_website_lan_prefix` | delete; read `lan_ip4_base` | a hard-coded copy of `pib_network` |
| `tt_commander_*` → `tt_website_commander_*` | keep | inventory vars with the `tt_` topic prefix already |
| `fixpi_server_user_pubkey` → `nfsroot_config_server_pubkey` | `nfsroot_config_server_user_pubkey` | `server_pubkey` reads as a host key |
| `onpi_fpga_verify_package` → `nfsroot_packages_fpga_check` | `nfsroot_packages_boards_package` | the value is a board-set package; `_fpga_check` reads as a bool |
| `fixpi/tasks/tweeks.yml` → `tweaks.yml` | split: `config-txt.yml` + `root-edits.yml` | "tweaks" breaks the draft's own rule on task-file names |
| `fixpi/tasks/userconf.yml` → `pi-user.yml` | `logins.yml` | it also handles root and the gateway's server user |
| `onpi/tasks/stale_root.yml` → `watchdog.yml` | `nfsroot-watchdog.yml` | "watchdog" could mean the hardware watchdog or the fleet PoE watchdog |
| `onpi/tasks/fpga_verify.yml` → `fpga-check.yml` | `fpgas-verify.yml` | the product is `fpgas-verify` |
| `stream_server/tasks/base.yml` → `rtmp.yml` | `nginx-rtmp.yml` | it installs nginx as well |
| group_vars `ttsite.yml` → `tt_website.yml` | `tt.yml` | its vars are `tt_*` inventory vars |

**Changes from the second draft** (the owner's decisions, section 6):

| from | to | reason |
|---|---|---|
| `fixpi` → `nfsroot_config` | split: `nfsroot_netboot` (CI) + `nfsroot_site` (gateway) | decision 1; names tested in 2.1, task map in 2.10 |
| `fixpi_*` vars → `nfsroot_config_*` | `nfsroot_netboot_*` / `nfsroot_site_*` per 2.10; `fixpi_image_build`, `fixpi_generate_host_keys` deleted | the split makes the switches unneeded |
| `fixpi_ansible_user`, `fixpi_ansible_uid` | `nfsroot_ansible_user`, `nfsroot_ansible_uid` (inventory) | both halves and verify-pi read them |
| `fixpi_server_monitor` → delete with its tasks | tasks → new role `serial_monitor` | decision 7; `serial_console` would read as providing a console |
| `fpgas_apt` → keep | `nfsroot_apt` | decision 2 |
| `cam_pi` → keep | `nfsroot_cam` | decision 2 |
| `apt_cache/tasks/nfsroot.yml` → keep | `root-sources.yml` | removes the clash with `nfsroot_apt` |
| `pxe` → `dnsmasq` (chrony undecided) | `dnsmasq` + new role `chrony` | decision 4 |
| `uhubctl` → keep or delete | keep; runs in the NFS root; `[uhubctl]` group and gateway plays deleted | decision 6; section 5.1 |
| `maintenance.sh`, `production.sh`, `manage.yml` → `mode-scripts.yml` | delete | they are TODO stubs; the only caller is `when: false` |
| `chroot-mount-pi-fs.bash` → keep, installed on the gateway | moves to `nfsroot_netboot`, not installed on the gateway | only CI-only tasks use it |
| `fixpi/tasks/verify/pi.yml` → (not listed) | delete | nothing includes it |
| `letsencrypt_account_email` value | `admin@fpgas.online` (+ `certbot update_account`) | decision 9 |
| `dnsmasq_auth_zone` set per host | defaults to `lan_domain` | decision 10 |
| execution order: 29 steps | 34 steps + the separate uhubctl PR (U) | new steps for dead fixpi code, `serial_monitor`, the ACME email, `chrony`, and the two-step fixpi split |

**Factual errors in the first draft**, now corrected in the tables above:

| draft claim | fact |
|---|---|
| `domain` is used for apt repo URLs and dnsmasq | nothing reads it |
| `streaming_frontend_hostname` feeds `domain_name` in one host_vars file | two live files (`fpgas.online.yml`, `tests/.../test-vm.yml`) + orphan `gator.yml` |
| `fixpi_server_monitor` gates the `/boot/firmware/config.txt` task | it gates brltty, tio, dialout and getty. "Is server pi" and "Enable /dev/serial0" run on every build host, gated only by a stat |
| `fixpi`: CI makes the root netbootable, the gateway applies the site layer | both runs do nearly all of it; only the ARM-code tasks are CI-only. `manage.yml` installs its scripts on the gateway, not in the root |
| `nspawn_pi` is CI only | its verify runs on the gateway through verify-server.yml and installs `nfsroot-kernels` there |
| `groups['nbp']` is read in `onpi/tasks/tt.yml` and verify-pi | also in `fpgas_apt/defaults/main.yml` |
| renaming CI's `pi` group affects verify-pi's default `pi_live:pi` | it does not; the `:pi` is already dead (no `pi` group in the production or test inventories) |
| `mqtt`: broker for fleet self-registration | shared with sensors2mqtt |
| `switch`: the legacy single switch | welland's per-port scheme also uses it, for the SNMP PoE env |
| `site/tasks/pib.yml` is an empty include | nothing includes it |
| the only cross-role reference is the `ttsite` path in `nfsroot_inputs.py` | four `{{ role_path }}/../<role>/` reads (pxe, onpi, ttsite ×2) |
| tag notes: `mp` is in dead files; `fpgas-apt` appears 6× in onpi | `mp` is in live `apt.yml`; `fpgas-apt` appears 10× |
| tag uses outside docs are CI and README only (also #157) | `tests/vm/run_tests.py` runs `--tags server_user` and passes `--skip-tags` |

Claims checked and confirmed: `onpi` runs only in the CI chroot (never on a
Pi); `resolve.conf.j2` writes `/etc/resolve.conf` and is in
`nfsroot_manifest.py`; the nine unused inventory vars; `nspawn_pi_sshd_port`
is unused; the pxe `Restart network-manager`/`Restart networking` handlers
are never notified; `interfaces-static.j2` and the wssh gunicorn copies are
unused; the onpi `arty_*`/`tmux`/`pistat` task files are never included; the
tags `pi`, `onpi` and `cam` select nothing in `site.yml`.
