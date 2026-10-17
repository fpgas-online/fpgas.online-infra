# Naming proposal

Status: proposal only. Nothing here has been renamed yet (PR #164).
Based on `origin/main` at `4de0b24` (2026-09-29). Second draft: every proposed
name was checked for confusion (see the "confusion check" column and the last
section). Third draft: the owner's decisions are applied (section 6).
Reviewed against `origin/main` at `1887743` (2026-09-29); the corrections
are listed at the end of section 7. Fourth draft: the conventions were
checked against the Ansible docs, ansible-lint and the Red Hat good
practices (section 1.1), and the owner's decisions 15–22 are applied.

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
| Role names | Name the role by its **function** (`firewall`, `timesync`, `nfs_server`), or by the daemon when the role manages exactly one (`dnsmasq`, `lldp`, `webssh`), in `snake_case`, 1–2 words (decision 21). Do not prefix by host. Roles that exist to build or maintain the Pi NFS root get the prefix `nfsroot_` (as `nfsroot_generation` already has): `nfsroot_image`, `nfsroot_chroot`, `nfsroot_netboot`, `nfsroot_site`, `nfsroot_apt`, `nfsroot_cam`, `nfsroot_packages`. The one exception is `uhubctl`: it runs in the NFS root but keeps its tool name, with no `nfsroot_` prefix (decision 6). |
| Where a role runs | Record where a role runs in the playbook that uses it, not in its name. The gateway roles are in `site.yml`, the web tier roles are in `web.yml`, and the image roles are in `ci-nfsroot*.yml`. Each role's single `README.md` says which of these runs it. |
| Variable prefix | A role's documented inputs (defaults, vars) are `<role>_<name>`. Its `register` and `set_fact` results are `__<role>_<name>` (decision 17). ansible-lint `var-naming[no-role-prefix]` enforces the role part, so renaming a role renames all its vars. The two underscores are a convention only: Ansible gives such a variable no privacy. |
| Inventory vars | Vars that several roles read (site facts) use a short **topic prefix**: `nfsroot_`, `raspios_`, `lan_`, `site_`, `eth_uplink_`, `eth_local_`, `fleet_`, `tt_`, `pi_`, `nginx_`, `django_`, `snmp_`. Roles read them directly. This is a documented deviation from GPA 4.1.4 and 4.1.15, which want every role input role-prefixed and given a default (decision 16). The lint constraint: a topic-named var is set only in inventory. It is never defined inside a role (defaults, `register`, `set_fact`) and never passed through `include_role … vars:`, because lint demands the role prefix in each of those places. A test covers the other direction: an inventory var may start with `<role>_` only if that role's defaults declare it. The word `site` is used **only** for the location (welland, ps1) and never for the Django app. |
| Cross-role files | None (decision 19). A role reads only its own `files/` and `templates/`. A file that several roles need lives in the play-level `ansible/templates/` directory, which Ansible searches after the role's own. A test forbids `role_path }}/..` anywhere in the tree. Today's four reads are removed, not re-pointed: `send_stat.conf` moves into `dnsmasq`, `fleet.toml.j2` into `nfsroot_site`, and `tt-boards.yaml.j2` (three readers) to `ansible/templates/`. |
| Groups in roles | A role's tasks and templates do not name an inventory group. The role takes the host through a role default, e.g. `nfsroot_packages_gateway_host: "{{ groups['gateway'][0] }}"` (decision 20). Playbooks may name groups. |
| Tags | None. See #157. |
| Handler names | `Restart <unit>` or `Reload <unit>`, using the systemd unit name. A handler name is defined once per play, or every definition of it is identical (decision 15): Ansible runs only the last one loaded, whichever role notified it. A test enforces this. Add a ` (<role>)` suffix, spelt as the role directory, only when two roles in one play need different actions under the same name. |
| Task names | See the style rule below. |
| Per-Pi variables | None (owner rule): every Pi should be as identical as possible, so no inventory var describes one Pi or one board. A check that differs between Pis decides from what it detects on the Pi (model, attached hardware, what is in use), with the same logic everywhere. Existing per-Pi vars are removed and replaced with detection, not renamed. |
| Task files | Name each task file for what it does, in `kebab-case` (GPA 3.1 uses hyphens for files, and the Ansible sample layout has `tasks/webservers-extra.yml`), e.g. `pi-password.yml` rather than `userconf.yml`. Never `tweaks.yml`, `misc.yml` or similar. |
| Entry points | A task file that a playbook or another role calls with `tasks_from` is public interface. Each role's README lists its entry points, and renaming one is never blast X. |
| Booleans | Positive names. A new boolean ends in `_enabled` when it switches a feature on (`fleet_enabled`) and in `_manage` when it means "this role may touch X" (`switch_vlans_manage`). |
| Argument specs | Every role has a `meta/argument_specs.yml` that lists its inputs for each entry point (decision 22). |

**Task-name style rule**

1. Start with an imperative verb and use sentence case, e.g. "Install nfs-kernel-server". Do not use noun phrases like "Site directories" or "Vhost".
2. Name the object, not the file, unless the file is the object (`/etc/exports`).
3. Say where the task acts when that is not the play's host, e.g. "... in the NFS root" or "... on the gateway".
4. Keep names to 60 characters or fewer. Reasons belong in a `#` comment, not in the name.
5. For verification, use "Check X" for the probe that registers and "Assert X" for the assertion. Do not prefix with "Verify:", because the include is already named "Verify <role>".
6. Make every name unique within its role, so there are no two "Enable services" or two "Systemd files".
7. No slang or pici leftovers ("cuz", "netbootie", "and friends", "Apt update/upgrade", "Tweeks").
8. Put Jinja only at the end of a task name, and never in a play or handler name (ansible-lint `name[template]`).
9. Do not prefix a name with its task file (`sub | ...`). GPA 4.1.19 and ansible-lint's opt-in `name[prefix]` ask for that prefix; this repo deliberately does not adopt it, because Ansible already prints the role and `-vv` prints the file.

### 1.1 Convention sources

What each rule rests on. `ADOC` is `https://raw.githubusercontent.com/ansible/ansible-documentation/devel/docs/docsite/rst/`, `LINT` is `https://raw.githubusercontent.com/ansible/ansible-lint/main/src/ansiblelint/rules/`, and `GPA` is `https://redhat-cop.github.io/automation-good-practices/` (Red Hat's "Good Practices for Ansible", one page, cited by section number).

**The Ansible docs were read from their rst sources** (`ansible/ansible-documentation`, branch `devel`, commit `7c56be6c`, 2026-09-29), because docs.ansible.com blocks automated fetches; the rendered pages were not read. The ansible-lint rule texts were read from the pinned 26.9.0 package, and the lint behaviour below was tested on a scratch tree at the `production` profile.

| rule | rests on | source |
|---|---|---|
| Role name characters | lint `role-name` (`^[a-z][a-z0-9_]*$`); "Role names are now limited to contain only lowercase alphanumeric characters, plus `_` and start with an alpha character" | `LINT/role_name.md`; `ADOC/dev_guide/developing_collections_structure.rst`; GPA 4.1.4 ("Do not use dashes in role names") |
| Role named by function | "Design roles focused on the functionality provided, not the software implementation" | GPA 4.1.1 |
| `nfsroot_` family prefix | the `object[_feature]_action` pattern, which sorts related roles together | GPA 9.1 |
| Role prefix on role vars | lint `var-naming[no-role-prefix]`; checked in defaults, vars, `register`, `set_fact` and `include_role … vars:`, not in inventory | `LINT/var_naming.md`; GPA 4.1.4 |
| `__<role>_<name>` for results | "internal variables (those that are not expected to be set by users) are to be prefixed by two underscores"; Ansible itself gives underscore names no privacy | GPA 4.1.4; `ADOC/playbook_guide/playbooks_variables.rst` |
| Topic-named inventory vars (deviation) | GPA wants "All defaults and all arguments to a role" role-prefixed, each with a default in `defaults/main.yml` | GPA 4.1.4, 4.1.15 |
| Valid and reserved var names | letters, numbers and underscores; no Python or playbook keywords; lint `var-naming[no-reserved]`, `[read-only]` | `ADOC/playbook_guide/playbooks_variables.rst`; `LINT/var_naming.md` |
| Group names | "Group names should follow the same guidelines as" valid variable names; ansible-core warns when a group and a host share a name | `ADOC/inventory_guide/intro_inventory.rst` |
| No group names in roles | "store the host name(s) in a (list) variable, or at least make the group name a parameter of your role" | GPA 4.1.18 |
| Handler names unique per play | "Each handler should have a globally unique name. If multiple handlers are defined with the same name, only the last one loaded into the play ... can be notified and executed" | `ADOC/playbook_guide/playbooks_handlers.rst` |
| Shared files in `ansible/templates/` | the search order ends "in the current play file's directory" | `ADOC/playbook_guide/playbook_pathing.rst` |
| No cross-role reads | "Roles that have hard dependencies on external roles or variables have limited flexibility" (lint's `no-relative-paths` does not catch the `role_path` form) | GPA 4.1.1 |
| Task names: imperative, sentence case, Jinja last | "Write task names in the imperative"; lint `name[casing]`, `name[template]` | GPA 9.1, 9.3; `LINT/name.md` |
| No task-file prefix (non-adoption) | "Prefix task names in sub-tasks files of roles"; lint `name[prefix]` is opt-in | GPA 4.1.19; `LINT/name.md` |
| Kebab-case files, `.yml`, `.j2` | "hyphens for repos and files"; `tasks/webservers-extra.yml`; "templates end in .j2". GPA 9.1 says snake_case for YAML files, so GPA disagrees with itself | GPA 3.1, 9.1, 9.2; `ADOC/tips_tricks/sample_setup.rst`; `ADOC/tips_tricks/shared_snippets/role_directory.txt` |
| `site.yml` as the top playbook | "`site.yml` # main playbook" (GPA 3.3 prefers verb-noun names) | `ADOC/tips_tricks/sample_setup.rst` |
| Booleans | "Use positive boolean variable names" | GPA 9.2 |
| Argument specs | role argument validation in `meta/argument_specs.yml` | `ADOC/playbook_guide/playbooks_reuse_roles.rst`; GPA 4.1.20 |
| `tasks_from` entry points | a documented feature; GPA warns against consumers that depend on "the role names within, and the file names" | `ADOC/playbook_guide/playbooks_reuse_roles.rst`; GPA 4.1.1 |
| One README per role | "Create a meaningful README file for every role" | GPA 4.1.17 |

---

## 2. Inventory and proposals

### 2.1 Roles (29 today, 32 after the split and the two new roles)

"Runs" says where: **gw** = gateway (site.yml), **web** = web tier (web.yml), **CI** = the image build (`ci-nfsroot*.yml`; `chroot` = inside the root through the chroot connection).

| current | proposed | runs | what it is | confusion check | blast |
|---|---|---|---|---|---|
| `apt_cache` | keep; rename `tasks/nfsroot.yml` → `tasks/root-sources.yml` | gw | apt-cacher-ng + nginx TLS front end; `root-sources.yml` rewrites the NFS root's apt sources (including those `nfsroot_apt` added) to go through the cache | clear. The task-file rename removes the clash with the role `nfsroot_apt`; both READMEs say "nfsroot_apt adds the repos in CI; apt_cache points them at this site's cache on the gateway". The file stays in `apt_cache` because it needs `apt_cache_remaps` and `apt_cache_host` | – |
| `apt_client` | keep | gw | the gateway's own apt proxy config (`01site-proxy`) | clear | – |
| `automation_user` | keep | gw | the `ansible` automation account | clear | – |
| `cam_pi` | **`nfsroot_cam`** | CI chroot | installs GStreamer and `fpgas-online-cam` into the NFS root, enables `fpgas-cam.service` | clear: the family prefix says it is the camera software inside the root, apart from `stream_server`/`webrtc` on the web tier | C |
| — (new, from PR #45) | **`timesync`** (decision 11; replaces this proposal's `chrony` role) | gw | chrony as the board LAN's NTP server plus the fake-hwclock reference, moved out of `pxe` by #45 | #45's name; kept | X |
| `firewall` | keep | gw | nftables rules + IPv4/IPv6 forwarding | clear (alt `nftables` names the tool, not the job) | – |
| `fixpi` | **split** into `nfsroot_netboot` + `nfsroot_site` (map in 2.10) | CI + gw | file-level edits to the root and its boot/TFTP tree. Today both runs do almost all of it; only the ARM-code tasks are gated to CI by `fixpi_image_build` | see the two rows below | D C M |
| — (from `fixpi`) | **`nfsroot_netboot`** | CI only | turns the RasPiOS tree into a read-only netboot root: cmdline/fstab, users and sudo, sshd on, first-boot/resize/swap off, config.txt, sunxi kernel bake, nfs-common | candidates: `nfsroot_netboot` (risk: "netboot" also names the whole DHCP/TFTP chain, and fpgas.online-netboot-pi; the `nfsroot_` prefix confines it to the root, and its main file is already `netboot.yml`); `nfsroot_base` (collides with the CI **base stage**, `ci-nfsroot-base.yml`); `nfsroot_boot` (reads as the `boot/` partition only); `nfsroot_diskless` (accurate, but not a word used anywhere in the project). **Pick `nfsroot_netboot`** | C |
| — (from `fixpi`) | **`nfsroot_site`** | gw only | applies this location's values to the pulled root and publishes its boot files: pi password, logins and keys, ssh host keys, authorized_keys, TT catalogue, fleet.toml, `pistat_host`, timesyncd server, the legacy TFTP tree and the sunxi TFTP payload | candidates: `nfsroot_site` (site = location, the convention's meaning; matches the existing "site layer", `tt-site.yml`, `fleet-site.yml`; the old `site` role is gone by then); `nfsroot_local` (clashes with `eth-local` and Ansible's `local` connection); `nfsroot_values` (misses the TFTP publishing and host keys); `nfsroot_deploy` (reads as the pull, which is `nfsroot_image`). **Pick `nfsroot_site`** | D |
| `fpgas_apt` | **`nfsroot_apt`** | CI chroot only, once `nfsroot_generation` stops including its `tasks/nfsroot-watchdog.yml` on the gateway (decision 13) | adds the fpgas.online, fpga-tools and nfsroot-watchdog apt repos inside the NFS root | clear once `apt_cache/tasks/nfsroot.yml` is renamed `root-sources.yml` (row above). Could read as "installs packages"; its README says "repos and keys only; packages are `nfsroot_packages`". `nfsroot_generation` writes the gateway's own nfsroot-watchdog source itself | C (+ `nfsroot_generation/tasks/install.yml`) |
| `img` | **`nfsroot_image`** | CI + gw | CI base stage: downloads and extracts RasPiOS (`build.yml`). Gateway: podman pull of the GHCR image and extraction to `nfs_root` (`prefetch.yml`, `pull.yml`) | minor risk: could read as "builds/publishes the image" (that is the workflow + `tests/ci`). Alternatives: `nfsroot_fetch` (collides with `ansible.builtin.fetch`), `nfsroot_pull` (wrong for CI's download), `nfsroot_extract` (misses the prefetch). Keep `nfsroot_image`: both halves turn an image into the root tree | D C |
| `jump` | keep | gw | the restricted jump account (`pi`) used to hop to the Pis | minor: can read as a verb; the account name `pi` also equals the Pi login (`pi_user`). Alt `ssh_jump`. Keep | – |
| `lldp` | keep | gw | lldpd | clear | – |
| `mqtt` | **keep** (decided) | web | mosquitto with the fleet listener config; shared with sensors2mqtt | clear. `fleet_broker` was rejected: the broker is shared, and it is the bool var being retired | – |
| `netif` | **`nics`** | gw | names the two NICs by MAC (`eth-uplink`/`eth-local`), uplink networkd, static resolv.conf, reboot after a rename | risk: a reader may look here for the eth-local addresses (they are in `vlan_ifaces`). Alternatives: `uplink` (misses the naming), `network` (too broad; reads as `networking.service`), `nic_names` (misses the uplink). Keep `nics`; say "eth-local is addressed by vlan_ifaces" in the README | X. `tests/test_netif.py` tests the filter plugin `filter_plugins/netif.py`, not the role; it is affected only if that file is renamed too (and `filter_plugins/` is an image input) |
| `nfs` | **`nfs_server`** | gw | nfs-kernel-server: exports the NFS root on eth-local only, creates the export dirs | clear: `_server` keeps it apart from the `nfsroot_*` roles and from `nfs-common` in the root | X |
| `nfsroot_generation` | keep | gw | takes the root-update lock, bumps the generation, releases the lock (nfsroot-watchdog-server) | clear | – |
| `nspawn_pi` | **`nfsroot_chroot`** | CI (+ gw verify) | CI: bind mounts, policy-rc.d, the deb cache, initramfs suppression and kernel pruning so apt can run inside the root. Its verify runs on the **gateway** (verify-server.yml) and installs `/usr/local/sbin/nfsroot-kernels` there. No systemd-nspawn any more | clear: matches the `community.general.chroot` connection it prepares | C D |
| `onpi` | **`nfsroot_packages`** | CI chroot | apt upgrade, setup-pi, TT bridge, fleet units, the Pi's own atftpd port, FPGA boot check, nfsroot-watchdog, drops `nfsvers` from cmdline.txt. Never runs on a Pi (only in the CI chroot) | risk: "packages INTO the root, or packages that SERVE it?" The `nfsroot_` family means "the root's content"; the NFS server is `nfs_server`, so it reads INTO. Not unique: `nfsroot_cam` and `nfsroot_apt` also install into the root; this is the general set. Alternatives: `nfsroot_software` (vaguer), `pi_packages` (leaves the family), `nfsroot_pi` (reads as the host). Keep | D C |
| `operators` | keep | gw | operator accounts, keys, sudo, retired accounts | clear | – |
| `pxe` | **`dnsmasq`** (decided; chrony moves to `timesync` in #45) | gw | dnsmasq DHCP/TFTP/auth DNS (`base.conf`, `ports.conf`, legacy MAC tables, the "Raspberry Pi Boot" service). The Pis do not PXE | clear once chrony is out: named after the one daemon it manages | D C (`site/tasks/pistat.yml` reads `../pxe/files/send_stat.conf`; step 14 moves that drop-in task into this role, decision 19) |
| — (new) | **`serial_monitor`** | gw | the gateway side of watching a Pi's serial console: remove brltty, install tio, add the server user to `dialout`, mask the ttyAMA0 getty; on a Pi gateway, enable `/dev/serial0`. Moved out of fixpi's `tweeks.yml`; replaces `fixpi_server_monitor` | candidates: `serial_monitor` (says "watch serial lines"); `serial_console` (reads as *providing* a console on the gateway, the opposite of masking its getty); `tio` (the role does more than install tio); `console_tap` (unfamiliar). **Pick `serial_monitor`**. Minor risk: "monitor" as in monitoring; the README says "serial lines, not metrics" | X |
| `server_user` | keep | web | the host's own admin login (`user_name`), incl. the in-place rename from `videoteam` | minor: "server" beside the `gateway` group. It runs in web.yml, so a host-neutral word is right. Keep | – |
| `site` | **`website`** | web | nginx vhost + certbot, the fpgas-online-site Django install, gunicorn/uvicorn/daphne, fleet consumer, pistat (redis + a dnsmasq drop-in) and the PoE env | clear. Minor risk: could be read as the apex fpgas.online landing site; the README says "this location's Django site". Alternatives: `web_app`, `django_site` (reads as Django's Sites framework), `django` (reads as "installs Django"). Keep `website` | D C (heavy) |
| `ssh_key_fetch` | keep | gw | library role: downloads `gh:`/`lp:` keys, fails when a download is empty | clear | – |
| `sshd` | keep | gw | pubkey-only drop-in with a lockout guard | clear | – |
| `stream_server` | keep | web | nginx-rtmp ingest from the Pi cameras + HLS output | clear | – |
| `switch_vlans` | keep | gw | installs `fpgas-switch-setup` and `switches.yml`, converges the **switches'** VLANs | clear once its gateway-side sibling is `vlan_ifaces` | – |
| `ttsite` | **`tt_website`** | web | the tinytapeout.fpgas.online vhost, Commander embed bundles, the TT board catalogue in Django | clear, pairs with `website`. `tinytapeout` would read as the TT bridge software (that is in `nfsroot_packages`) | D C (inputs list; its `tt-boards.yaml.j2` moves to `ansible/templates/`, which ends the two `../ttsite/templates` reads, decision 19) |
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
| — (new) | `tasks/retired-vars.yml` | the retired-vars guard, imported in `pre_tasks` of every entry playbook (section 4) | clear | C (the three `ci-nfsroot*.yml` import it) |
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
| group `nbp` | **`gateway`** | the netboot gateway (tweed, val2). In the CI inventory it is the build runner (`localhost`) standing in for it. Read by `hostvars[groups['nbp'][0]]` in `onpi/tasks/tt.yml`, `fpgas_apt/defaults/main.yml` and `verify-pi.yml`. After step 30 the two roles take the host from a role default and name no group (decision 20) | clear; matches the tweed-split design, where the gateway VM keeps these roles. In CI it means "the host holding the root", which is what the shared lookups need. Alt `server` (clashes with `server_user`, too generic) | D (`--limit nbp`) C |
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
| group_vars file `srv.yml` | `nfsroot.yml` | the RasPiOS image vars, `nfs_root`, `tftp_root`, `user` | clear. Alt `raspios.yml` (misses `nfs_root`/`tftp_root`) | C (listed in `nfsroot_inputs.py` INPUTS, UPGRADE_INPUTS and BASE_VAR_FILES, so the base stage rebuilds; `tests/test_nfsroot_inputs.py` names it; `.github/actions/nfsroot-setup/action.yml` keys the RasPiOS download cache on `hashFiles(.../srv.yml)`; symlinked in 2 inventories) |
| group_vars file `ci.yml` | **delete**, with its two symlinks (CI and test inventories) | holds only `tftpd_port`, which moves to a role default (2.5.1) | clear | C (in INPUTS and UPGRADE_INPUTS: upgrade-stage rebuild) |
| group_vars file `site.yml` | `website.yml` (+ the separate copy in `tests/inventory/group_vars/all/`) | `django_dir`, `static_dir`, `django_project_name`, `letsencrypt_account_email` | `site` is the location word | X |
| group_vars file `ttsite.yml` | `tt.yml` (+ the separate copy in `tests/inventory/group_vars/all/`) | `ttsite_domain` + Commander pins, which become `tt_*` inventory vars (2.5.1) | clear | X |
| group_vars `streaming.yml`, `ssh_keys.yml`, `controller.yml`, `firewall.yml`, `all.yml` | keep | | clear | – |
| `inventory-ci-nfsroot/group_vars/all/zz-ci-overrides.yml` | keep | | clear | – |

### 2.4 Tags: removed, not renamed (#157)

The tags are being removed (#157), so this proposal renames none. Role-rename
PRs leave the old tags alone. Ideally #157 lands first, so no rename PR has to
touch a tag. Notes for the #157 work:

| tag use | where | what should replace it |
|---|---|---|
| `--skip-tags pipw,keys` | `.github/workflows/nfsroot-build.yml`, comment in `ci-nfsroot.yml` | The fixpi split (decision 1) removes it: the pi password and keys move to `nfsroot_site`, which CI never runs. If #157 lands first, gate `userconf.yml` and `ansible-home.yml` on `not fixpi_image_build` as a stopgap. |
| `--skip-tags hw-camera,hw-fpga` | `README.md` (verify-pi) | On-Pi detection, no variables: the camera and FPGA checks run only when the Pi detects a camera or an FPGA board (#157 part 3, branch `tags-verify-pi-hw`). |
| `web.yml --check --tags server_user`, and the `--skip-tags` option | `tests/vm/run_tests.py` (lines 385, 392, 651, 701) | **Missed by #157's table.** Replace it with a small playbook that runs only `server_user` in check mode, and drop the option. |
| `always` | site.yml, verify-pi, server_user, apt_cache, nfsroot_generation | #157 drops them with their partial-run workarounds |
| docs that describe tags | comments in `verify-server.yml` (`--skip-tags django`), `web.yml`, `verify-pi.yml` header, `nspawn_pi/tasks/verify/main.yml` (`--tags nspawn-pi`) | rewrite them in #157 |
| tag commands in the runbooks | `docs/superpowers/runbooks/2026-08-23-tweed-web-deploy.md` (`--tags site,ttsite,wssh`, `--tags ttsite`, `--tags pi,fpgas-apt,onpi`, `web.yml --tags django`), `2026-08-28-orange-pi-netboot.md` (`--tags fixpi,netboot,sunxi,sunxi-kernel,onpi,fpgas-apt`, `--skip-tags hw-camera,hw-fpga`) | **also missed by #157's table**; rewrite them in #157 |

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
| `domain_name` | **`site_fqdn`** | this location's public name (`welland.fpgas.online`); certbot, vhost, Django Site, `pistat_host` in the root, `apt_cache_host` (`apt.{{ domain_name }}`, apt_cache defaults + README), verify-pi's fleet-lookup `Host:` header. CI sets the apex as a placeholder | clear under the convention (site = location). Alt `web_fqdn` | D |
| `streaming_frontend_hostname` | delete; set `site_fqdn` directly | feeds `domain_name` in `fpgas.online.yml` and `tests/.../test-vm.yml` (+ orphan `gator.yml`) | – | D |
| `streaming_frontend_aliases` | `site_aliases` | extra names for ALLOWED_HOSTS | clear | D |
| `pib_network` | **`lan_ip4_base`** (was `lan_prefix`) | IPv4 prefix string that octets are appended to: `10.21` on per-port sites, `10.21.0` on ps1 | `lan_prefix` reads as a CIDR or a prefix length. The value's shape differs between the two schemes; note it in the var's comment | D (filter plugin, 4 roles: vlan_ports, firewall, pxe, site) |
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
| `switch` | **`snmp_switch`** (was `poe_switch`) | dict: the SNMP PoE target (creds, `mac`, `oid`, `mpi_port`) + `nos`. Read only on the legacy MAC-table scheme (ps1): `site/tasks/snmp.yml` needs `switch.mpi_port`, and pxe/firewall/fixpi read it only when `switches` is undefined. welland and test-vm still set it, but nothing reads it there (their PoE env comes from `switches`, via `gunicorn-poe.conf.j2`): **delete those two blocks** (decision 12); only legacy single-switch sites (ps1) keep it, renamed | `poe_switch` reads as "the PoE switch" when every entry in `switches` is also a PoE switch. `snmp_switch` matches the live `SNMP_SWITCH_*` env keys and `snmp_switch.conf` | D C (tests/vm, 26 site refs) |
| `switch.nos` | `snmp_switch.pis` | the Pi list (port, MAC, serial) for the legacy MAC-table scheme | clear. Alt `.boards` (board = the FPGA) | D |
| `switches` | keep | per-port-VLAN switch list | clear | – |
| `switches_manage` | `switch_vlans_manage` | lets `switch_vlans` push config | clear | D |
| `eth_uplink*`, `eth_local*` | keep | NIC names/addresses | clear | – |
| `sunxi_boards`, `sunxi_default_dtb` | keep for now; see open O7 | Orange Pi boards (per board: `host`, `usb`, `hat_uuid`) and DTB | clear (alt `opi_boards`) | – |
| `streaming`, `tt_boards`, `tt_install` | keep | | clear | – |
| `ttsite_domain` | **`tt_fqdn`** (was `tt_website_domain`) | TT vhost name. An **inventory** var read by `ttsite`, `site` and `webrtc`, so it takes a topic prefix, not a role prefix | pairs with `site_fqdn` | D |
| `tt_commander_embed_version/_sha256`, `tt_commander_legacy_embed_version/_sha256` | keep | Commander bundle pins (inventory, `tt_` topic prefix) | clear | – |
| `ttsite_certbot` | `tt_website_certbot` | follows the role | clear | D |
| `pxe_test_clients` | `dnsmasq_test_clients` | extra DHCP hosts for tests | clear | D |
| `dnsmasq_auth_zone/_glue/_subnet/_interface` | keep (role-prefixed once `pxe` → `dnsmasq`). **`dnsmasq_auth_zone` defaults to `lan_domain`** in the role defaults (decided); set it in host_vars only where they differ (today they are equal on welland, so remove it there; test-vm differs and keeps it). `dnsmasq-base.conf.j2` gates the whole auth block on `dnsmasq_auth_zone is defined`, which a default makes always true: move the gate to `dnsmasq_auth_glue is defined`, or ps1 (no auth vars, no `lan_domain`) fails | dnsmasq authoritative zone | clear | D |
| `vault_ansible_ssh_private_key`, `vault_switch1_snmp_rw_community`, `vault_switch2_snmp_rw_community` | keep | vaulted values in welland's host_vars | clear | – |
| `ssh_imports`, `ssh_imports_revoked`, `ssh_public_keys`, `ssh_public_keys_revoked`, `operators_accounts` | keep | shared jump/operator key inputs | clear | – |
| `nfsroot_build_deb_cache` | `nfsroot_chroot_deb_cache` | CI deb cache dir | clear | C |
| `fixpi_image_build`, `fixpi_generate_host_keys` | **delete** | CI/gateway switches that the split makes unneeded: `nfsroot_netboot` runs only in CI, host keys are generated only by `nfsroot_site` | – | C |
| `fixpi_server_monitor` | **delete**; its tasks move to the `serial_monitor` role (decided) | gated brltty, tio, `dialout` and the getty mask on the build host. The `/boot/firmware/config.txt` edit beside them was gated by a stat only | – | C |
| `fixpi_ansible_user`, `fixpi_ansible_uid` | **`nfsroot_ansible_user`, `nfsroot_ansible_uid`** in group_vars `nfsroot.yml` (`srv.yml` until step 24 renames it) | the automation account inside the root. Both halves of the split and `verify-pi.yml` read it, so it becomes an inventory var with the topic prefix | clear | C |
| `img_pull_retries`, `img_pull_delay` | `nfsroot_image_pull_*` | set in the test-vm host_vars | clear | C |
| `automation_user_manage`, `sshd_pubkey_only`, `server_user_*`, `apt_client_*`, `apt_cache_enabled`, `firewall_dns_query_sources`, `webrtc_*`, `site_under_construction`, `site_require_fpga_verified` | keep (the `site_*` ones → `website_*`) | | the `site_*` ones collide with the location word until renamed | D (site_* only) |
| `verify_pi_hosts` | keep | the play's target pattern (a run option, not per-Pi data) | clear | – |
| `verify_pi_fpga_expect`, `verify_pi_header_uart_console` | **remove, replace with detection** (per-Pi vars, convention 1). Their only setter is `tests/inventory/host_vars/test-pi.yml`, which then goes too: expect `missing` when no FPGA board is detected; judge the console from the served `cmdline.txt` / what the Pi detects, not from a flag (open O6) | per-Pi expectations for the virtual Pi | – | C |
| — | (no `verify_pi_usb_power_cycle`) | the active USB check decides by detection (5.1) | – | – |

#### 2.5.2 Role defaults (122 vars)

Renaming a role renames every var in its defaults to `<new role>_<name>` (the lint rule) and every `register`/`set_fact` name to `__<new role>_<name>` (decision 17). Only the name changes that go beyond that prefix swap are listed here.

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
| | `fpgas_apt_nfsroot_watchdog_{url,upstream_url,suite,key_fingerprint}` | `nfsroot_apt_watchdog_*` (not `nfsroot_apt_nfsroot_watchdog_*`) | the double prefix reads badly; the role prefix already says NFS root | C |
| cam_pi → nfsroot_cam | `cam_pi_*` (if any) | `nfsroot_cam_*` | clear | C |
| img → nfsroot_image | `img_nfsroot_image` | `nfsroot_image_ref` | clear (OCI "image reference") | D (branch-deploy override) |
| | `img_cache_dir` (`/var/cache/pib`) | `nfsroot_image_raspios_cache` (value `/var/cache/raspios`) | clear | C (the value is also hard-coded in `.github/actions/nfsroot-setup/action.yml`: `install -d` and the actions/cache path; change both or the download cache silently stops hitting) |
| | `img_pull_*` | `nfsroot_image_pull_*` | | C |
| onpi → nfsroot_packages | `onpi_nfsroot_watchdog_*` (7) | `nfsroot_packages_watchdog_*` | minor: "watchdog" alone could mean the fleet PoE watchdog (poe #8); the role prefix gives the context | D (`dry_run` is an `-e` knob) C |
| | `onpi_fpga_verify_package` | **`nfsroot_packages_boards_package`** (was `_fpga_check`) | the value is a board-set package (`fpgas-online-all-boards`) that pulls in fpgas-verify; `_fpga_check` reads as a bool | C |
| nspawn_pi → nfsroot_chroot | `nspawn_pi_sshd_port` | **delete** | unused (nspawn era) | X |
| | `nspawn_pi_root`, `nspawn_pi_kernel_*`, `nspawn_pi_max_kernel_trees`, `nspawn_pi_suppress_initramfs` | `nfsroot_chroot_*` | clear | C |
| netif → nics | `netif_uplink_manage`, `netif_allow_reboot` | `nics_uplink_manage`, `nics_allow_reboot` | clear | D (low: not set in inventory) |
| site → website | `site_certbot`, `site_under_construction`, `site_require_fpga_verified`, `site_package`, `site_poe_package`, `site_poe_package_override`, `site_upload_max_body_size` | `website_*` | clear. `site_package`/`site_poe_package`/`site_poe_package_override` are **documented `-e` overrides** for branch deploys (`_override` is undefined by default, so only the retired-vars guard catches an old name; add `website_poe_package_override` to the defaults, commented out, so every input is listed in one place) | D C |
| ttsite → tt_website | `ttsite_boards_path`, `ttsite_daemon_port`, `ttsite_ws_read_timeout` | `tt_website_*` | clear | D |
| | `ttsite_pi_network` | **delete**; read `lan_ip4_base` | a hard-coded `"10.21"` copy of `pib_network` | D |
| uhubctl | `uhubctl_usb_hubs` | keep the name; retarget from the D-Link DUB-H7 to the Pi onboard hubs (5.1) | clear | C |
| everything else (`apt_cache_*`, `apt_client_*`, `automation_user_*`, `firewall_*`, `jump_*`, `nfsroot_generation_*`, `operators_*`, `server_user_*`, `sshd_*`, `ssh_key_fetch_*`, `switch_vlans_*`, `webrtc_*`) | keep the defaults; `register`/`set_fact` names become `__<role>_<name>` in step 35 (decision 17) | | | X |

### 2.6 Handlers (31)

| current | role(s) | proposed | notes | blast |
|---|---|---|---|---|
| `Reload-systemd` | cam_pi, onpi, stream_server | `Reload systemd` | site and wssh already use `Reload systemd`. `cam_pi` (systemd module) and `onpi` (`command`, because the chroot has no running systemd) differ and share the CI play, so today onpi's body runs for both. Step 5a gives `cam_pi` onpi's body first | X |
| `Udev-reload` | uhubctl | `Reload udev` | | X |
| `Exportfs` | nfs | `Reload NFS exports` | runs `exportfs -r` | X |
| `Reload sshd for pubkey-only` | sshd | `Reload sshd` | its body is the same as jump's `Reload sshd` (both reload `ssh`), so the rule wants one name and no suffix; its comment stays | X |
| `Restart network-manager`, `Restart networking` | pxe | **delete** | nothing notifies them (checked) | X |
| `Restart chrony` | pxe | moves to #45's `timesync` role | | X |
| `Restart dnsmasq` | site | **delete** in step 14, when the `send_stat.conf` drop-in moves into `dnsmasq` and notifies that role's own handler (decision 19) | its `state:` is commented out, so today it restarts nothing; step 5a restores `state: restarted` until step 14 removes it | X |
| `Restart apt-cacher-ng (apt-cache)`, `Reload nginx (apt-cache)` | apt_cache | `Restart apt-cacher-ng`, `Reload nginx` | no other role in a gateway play defines either name, so the rule asks for no suffix (and `apt-cache` is not how the role is spelt) | X |
| `Reload nginx` | site, ttsite, wssh, stream_server, webrtc | keep | all five are in the web play, so only the last one loaded runs. `site`'s body does `state: restarted`, the other four `reloaded`; step 5a makes `site`'s `reloaded`, and then all five are identical | – |
| `Restart nginx`, `Restart mediamtx`, `Restart mosquitto`, `Restart wssh`, `Restart fleet-consumer`, `Restart django services`, `Reload nftables`, `Reload networkd`, `Reload sshd`, `Reload systemd` | various | keep | | – |

Step 5a also adds `tests/test_handler_names.py`, which fails when two roles
in one play define the same handler name with different bodies (decision 15).

### 2.7 Task files, templates and files inside roles

| role | current | proposed | what it is | confusion check | blast |
|---|---|---|---|---|---|
| fixpi | all task files | split between `nfsroot_netboot` and `nfsroot_site`, with renames; see **2.10** | | | C D |
| fixpi | `templates/resolve.conf.j2` + its task | **delete** (decided) | writes `/etc/resolve.conf` (misspelt, so nothing reads it); also drop its `tests/ci/nfsroot_manifest.py` entry | – | C |
| fixpi | `files/etc/network/interfaces.d/eth1.conf` | delete | pici static 192.168.100.100. Copied into the root, but ifupdown is masked there | – | C |
| fixpi | `files/scripts/maintenance.sh`, `production.sh` | **delete** | TODO stubs (`# TODO: Implement based on original monorepo logic`); the only caller is `when: false` | – | X |
| fixpi | `files/scripts/chroot-mount-pi-fs.bash` | move to `nfsroot_netboot`; stop installing it on the gateway | used only by the CI-only tasks (useradd, groupadd, nfs-common, the sunxi keyring and kernel bake), which call it by name from `PATH`. `manage.yml` is its **only installer** (on the CI runner too), so deleting `manage.yml` must keep an install task for it | clear | C M |
| fixpi | `templates/boot/config.txt.j2` | decide with open PR #55: delete, or `nfsroot_netboot` | **not listed before**: nothing on main reads it (config.txt comes from the image and `tweeks.yml` edits it); PR #55 makes it the owned template | – | X |
| fixpi, onpi, wssh | `notes.txt` | delete, or fold into the role README | pici notes | – | X |
| onpi | `tasks/arty_blink.yml`, `arty_here.yml`, `arty_wire.yml`, `tmux.yml`, `pistat.yml` | **delete** | nothing includes them (checked) | – | X C |
| onpi | `tasks/tweeks.yml` | `tasks/pi-dirs.yml` | Uploads/Downloads dirs | clear | X C |
| onpi | `tasks/nonfs.yml` | `tasks/nfs-version.yml` | drops `nfsvers=4.2` from cmdline | clear | X C |
| onpi | `tasks/stale_root.yml` | **`tasks/nfsroot-watchdog.yml`** (was `watchdog.yml`) | nfsroot-watchdog | `watchdog.yml` could mean the hardware watchdog or the fleet PoE watchdog; use the package name | X C |
| onpi | `tasks/fpga_verify.yml` | **`tasks/fpgas-verify.yml`** (was `fpga-check.yml`) | the FPGA boot check | the product is `fpgas-verify` (package + unit); `fpga-check.yml` adds a third name and reads like a `verify/` file | X C |
| onpi | `tasks/tftpd.yml`, `apt.yml`, `fleet.yml`, `tt.yml` | keep | | clear | X C |
| onpi | `templates/fleet.toml.j2` | move to `nfsroot_site` with `fleet-site.yml` (step 17): its only reader is `fixpi/tasks/fleet-site.yml` | | removes the cross-role read | C |
| site | `tasks/js_player.yml`, `pibdemos.yml`, `pibup.yml`, `pibfpgas.yml`, `switch.yml` | **delete** | empty or placeholder includes | – | X |
| site | `tasks/pib.yml` | **delete** | not included by anything | – | X |
| site | `tasks/index.yml` | fold into `django.yml` | creates `static_dir` | – | X |
| site | `tasks/snmp.yml` | `tasks/poe-env.yml` | PoE switch env in /etc/environment | clear | X |
| site | `tasks/apt.yml` | `tasks/packages.yml` | | clear | X |
| site | `tasks/fpgas-online-site.yml` | `tasks/install.yml` | pip-installs site + poe | clear | X |
| site | `tasks/pistat.yml` | keep the redis part; the dnsmasq drop-in task moves into `dnsmasq`, beside its `files/send_stat.conf` (decision 19) | redis + a dnsmasq drop-in from `../pxe/files` | removes the cross-role read, and the website role no longer notifies dnsmasq | C |
| ttsite | `templates/tt-boards.yaml.j2` | `ansible/templates/tt-boards.yaml.j2` (decision 19) | the TT catalogue template; read by `ttsite`, `fixpi/tasks/tt-site.yml` and `onpi/tasks/tt.yml` | play-level, so all three readers use the bare name. Add the new path to `nfsroot_inputs.py` INPUTS in place of the `ttsite` one | C |
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
| `fpgas-hostname-hosts.service`, `fpgas-cam.service`, `fpgas-tt.service`, `fpgas-verify.service` (enabled), nfsroot-watchdog units | fixpi, cam_pi, onpi | NFS root |
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
| `netboot.yml` | timesyncd → `eth_local_address` | none: PR #45 deletes these tasks (the root runs chrony from its `pi-clock` role) and lands first (decision 11) | – |
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
| `userconf.yml` | console banner (`/etc/issue.d` + `banner.issue` from `files/etc/issue`: "fpgas.online Pi", tty and IP; no password, nothing per site) | `nfsroot_netboot/tasks/root-edits.yml` | CI |
| `userconf.yml` | pi password (hash, check, set) + the "reboot the Pis" warning, removal of `boot/userconf.txt` and the password marker, `.ssh` dirs, pi/root user keypairs (per site, never in the public image), chown of `home/pi/.ssh` ("Set perms") | `nfsroot_site/tasks/logins.yml` | gw |
| `userconf.yml` | the server user's own ssh key | `nfsroot_site/tasks/logins.yml`, on the gateway, where the key is used. `server_user` would fit the account better, but it runs in web.yml on the `web` group, which is not the gateway after the tweed split | gw |
| `userconf.yml` | "Get fixed sshswitch" (`when: false`) | delete | – |
| `ansible-home.yml`, `authorized_keys.yml`, `github_keys.yml` | automation user's `.ssh`, authorized_keys, GitHub keys | `nfsroot_site/tasks/{ansible-home,authorized-keys,github-keys}.yml` | gw |
| `tt-site.yml`, `fleet-site.yml` | TT catalogue, fleet.toml | `nfsroot_site` (same names). `fleet.toml.j2` comes with them; `tt-boards.yaml.j2` is read from `ansible/templates/` (decision 19) | gw |
| `manage.yml` + `maintenance.sh`/`production.sh` | stub scripts, `when: false` switch | delete | – |
| `verify/image.yml` | ansible user checks on the image | `nfsroot_netboot/tasks/verify.yml` (ci-nfsroot.yml) | CI |
| `verify/main.yml` | cmdline, TFTP, sunxi, ansible-user and authorized_keys checks on the served root | `nfsroot_site/tasks/verify.yml` (verify-server.yml); drop all three script checks (`maintenance.sh`, `production.sh`, and `chroot-mount-pi-fs.bash`, which the gateway no longer gets) | gw |
| `verify/pi.yml` | NFS mount, 10.21 address, python3 | delete: nothing includes it (verify-pi covers the same checks) | – |
| `files/etc/` (whole tree) | copied wholesale by "Etc overrides": `issue`, `ssh/sshd_config.d/password.conf`, `sysctl.d/99-fpgas-no-sysrq.conf` (both also copied by their own tasks), `network/interfaces.d/eth1.conf` (deleted, 2.7) | `nfsroot_netboot/files/etc/` | CI |
| `files/fpgas-hostname-hosts.{service,sh}`, `files/scripts/chroot-mount-pi-fs.bash` | hostname service; chroot helper | `nfsroot_netboot/files/` | CI |
| `templates/boot/cmdline*.txt.j2`, `templates/etc/fstab.j2`, `templates/apt/debian-armmp.*` | netboot cmdline/fstab; armmp kernel source | `nfsroot_netboot/templates/` | CI |
| `templates/boot/default-arm-sunxi.j2` | U-Boot PXE config (uses `eth_local_address`) | `nfsroot_site/templates/` | gw |
| `templates/boot/config.txt.j2`, `templates/resolve.conf.j2`, `files/scripts/{maintenance,production}.sh`, `notes.txt` | unused / dead | delete (2.7; `config.txt.j2` with PR #55) | – |
| `README.md` | pici notes | rewrite one README per new role | – |

The split also retires `fixpi_image_build`, `fixpi_generate_host_keys`,
`fixpi_server_monitor` and CI's `--skip-tags pipw,keys`. `site.yml` must
keep running `jump` before `nfsroot_site`, which reads jump's public key,
and `server_user` before it, which creates the `user_name` account whose
key `logins.yml` generates (`ansible.builtin.user` would otherwise create
the account itself). `site.yml`'s early "Download the NFS root's GitHub
keys" include (`tasks_from: github_keys.yml`) and
`tests/test_nfsroot_authorized_keys.py` (which asserts that include) move
to `nfsroot_site` with it.

**Transition (the first gateway converge after step 17, while the pulled
image is still one built before it).** Old images already carry every
generic change except what CI skipped under `--skip-tags pipw,keys`, i.e.
`userconf.yml` and `ansible-home.yml`. Of those, the map moves only
`boot/ssh` and the `/etc/issue.d` banner to CI, and both are harmless when
missing (`ssh.service` is enabled directly; `/etc/issue` already carries
the same banner through "Etc overrides"). The password drop-in is in old
images already, through "Etc overrides". So a gateway may converge an older
or pinned image after step 17 without losing anything that matters.

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
| retired var names in `-e` files and host_vars off-repo (e.g. ten64 vars.json; an old `site_poe_package_override` name already exists in operator notes) | branch deploys | with each var rename PR, add the old name to the **retired-vars guard**, `tasks/retired-vars.yml`: one `ansible.builtin.assert` looped over a `{old: new}` map, with `that: query('ansible.builtin.varnames', '^' ~ item.key ~ '$') \| length == 0` and a `fail_msg` that names the new var. It is imported in `pre_tasks` of every entry playbook (`site.yml`, `web.yml`, `verify-server.yml`, `verify-pi.yml` and the three `ci-nfsroot*.yml`), so an old name in `-e` or in host_vars fails loudly and is not silently ignored. `meta/argument_specs.yml` cannot do this job: it accepts names it does not list (tested) |
| CI stage keys (`nfsroot_inputs.py`) | **base stage** (full RasPiOS download, then the upgrade stage): `BASE_VARS` (`dist`, `img_path`, `img_name`, `zip_name`, looked up by name), `BASE_VAR_FILES` (`srv.yml`, `zz-ci-overrides.yml`) and `BASE_FILES` (`ci-nfsroot-base.yml`, which has `hosts: nbp` and `nfs_root`; `ci-nfsroot-runner.yml`; `img/tasks/build.yml`, which reads `dist`, `img_*`, `zip_name`, `nfs_root`; `img/files/img2files.sh`). **Upgrade stage**: `UPGRADE_INPUTS` (`ci-nfsroot-upgrade.yml`, which has `hosts: nbp`/`pi` and `nfs_root`; the runner file; `roles/nspawn_pi`; `inventory-ci-nfsroot/`; group_vars `ci.yml`, `srv.yml`, `ssh_keys.yml`; `ansible.cfg`; `requirements.yml`) | base-stage rebuild on steps 1 (the stage playbooks import the guard, and `tasks/retired-vars.yml` joins `BASE_FILES`), 23, 24, 25, 30 and 31; upgrade-stage rebuild on steps 7, 17, 21, 22, 26, 28 and 34 (each edits a file above). `BASE_VARS`/`BASE_VAR_FILES` must be renamed with the vars (step 24). Every other image-input change only produces a new image. Check that `nfsroot_diff.py` shows no content change before merging. |
| cross-role file reads (`../pxe/files`, `../onpi/templates`, `../ttsite/templates`) | `site/tasks/pistat.yml`, `fixpi/tasks/{fleet-site,tt-site}.yml`, `onpi/tasks/tt.yml` | remove them, do not re-point them (decision 19): `send_stat.conf` in step 14, `tt-boards.yaml.j2` in step 16, `fleet.toml.j2` in step 17. Step 17 then adds a test that forbids `role_path }}/..`, because a missed read fails at run time, not at lint. |
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
| `--limit fpgas.online` in repo docs | `README.md` (2 commands), the header comments of `web.yml` and `ansible.cfg` (`--limit fpgas.online,pi`, which also names the dead `pi` group), `roles/apt_cache/README.md`, `docs/access.md` (3 commands + its link to the host_vars file; added after this proposal's base), and the runbooks under `docs/superpowers/runbooks/` (tweed web deploy, Orange Pi netboot). Dated plans and specs are not rewritten |
| comments that name the file | `ansible/ssh.cfg` (vault note), `ansible.cfg` (vaulted hosts), `tests/inventory/host_vars/test-vm.yml` (3 comments) |
| known_hosts pins | **no change needed**: `refresh-known-hosts.yml` pins `10.99.21.2`, and ssh connects by `ansible_host`, not by the inventory name. Check that the first run after the rename asks for no host key |
| jump keypair comment | `jump` writes `pi@{{ inventory_hostname }} (jump account)` as the key comment. Check that `openssh_keypair` only rewrites the comment and does not regenerate the key: a new key would change every board's authorized_keys. The `.pub` that `nfsroot_site` authorizes changes by its comment only, so the root gets one new generation |
| off-repo | ten64 `.worktrees/main-deploy` commands (`--limit fpgas.online`), `-e @vars.json` (check for the host name), the operator notes and procedures (web-tier deploy, NFS root update), any cron or script on ten64 that uses the name |
| code | nothing matches `hostvars['fpgas.online']` or the quoted name (checked) |

---

## 5. Execution order

Each step is one small PR that stands alone and is based on main. **One role per PR, one PR open at a time**, merged before the next is opened. Every PR updates the role's README, CLAUDE.md, the top-level README, `docs/access.md` (it names `fixpi`, `img`, `pi_pw`, `user_name`, `onpi_nfsroot_watchdog_*` and the `fixpi_*` key vars), runbook references, `verify-server.yml` includes and `tests/` in the same change. Every role-rename PR also renames that role's `register`/`set_fact` names to `__<role>_<name>` (decision 17), adds its `meta/argument_specs.yml` (decision 22) and lists its `tasks_from` entry points in its README. Dated plans and specs are not rewritten. #157 (tag removal) should land before step 9, so that no rename PR has to handle tags.

| # | PR | contents | blast |
|---|---|---|---|
| 1 | naming conventions + guard | add section 1 to CLAUDE.md; add `tasks/retired-vars.yml` with an empty map and import it in every entry playbook (section 4); add the inventory-prefix test (decision 16) with an allow-list of today's 12 offenders (`firewall_dns_query_sources`, `firewall_internal_networks`, `firewall_rules`, `fixpi_generate_host_keys`, `fixpi_server_monitor`, `img_host`, `img_name`, `img_path`, `nfs_root`, `ttsite_certbot`, `ttsite_domain`, `webrtc_additional_hosts`), which later steps empty | X C (base stage rebuild: the stage playbooks import the guard) |
| 2 | delete dead site tasks | `site/tasks/{pib,js_player,pibdemos,pibup,pibfpgas,switch}.yml`; fold `index.yml` | X |
| 3 | delete dead onpi tasks | `onpi/tasks/{arty_*,tmux,pistat}.yml` | C (new image, no content change) |
| 4 | delete dead inventory | orphan host_vars, `[pxe]` group, the unused vars in 2.5.1 (incl. `domain`), `nspawn_pi_sshd_port`, unused pxe handlers + `interfaces-static.j2`, wssh gunicorn copies, verify-pi's dead `:pi` | X |
| 5 | delete dead fixpi code | `resolve.conf.j2` + its task + its `nfsroot_manifest.py` entry (decision 8); `eth1.conf`; `manage.yml` + the stub scripts; the sshswitch task; `verify/pi.yml`. **Keep a task that installs `chroot-mount-pi-fs.bash`** (move it out of `manage.yml`): the CI build's useradd, nfs-common and sunxi tasks call it from `PATH`. Drop the `maintenance.sh`/`production.sh` checks from `verify/main.yml`, or verify-server fails on the fresh VM | C |
| 5a | handler bug fixes + test (decision 15) | a bug-fix PR, before any handler is renamed: `site`'s "Reload nginx" → `state: reloaded`; `cam_pi`'s "Reload-systemd" takes onpi's `command` body; `site`'s "Restart dnsmasq" gets its `state: restarted` back; add `tests/test_handler_names.py` (2.6) | C (new image: `cam_pi` is an image input) |
| 6 | handler names | the 2.6 renames, including `Reload sshd` in `sshd` and the two `apt_cache` names without their suffix | X |
| 7 | new role `serial_monitor` | move brltty, tio, dialout, getty mask and `/dev/serial0` out of fixpi; delete `fixpi_server_monitor` (decision 7). Run it **after** the web tier import (e.g. in the last play, where fixpi runs it today): its `ansible.builtin.user` task creates `user_name` if missing, and on a fresh host (the VM test) that pre-empts `server_user`'s rename from `server_user_rename_from`, which fails when the new home already exists | X C (upgrade-stage rebuild: CI inventory edit) |
| 8 | ACME email | `letsencrypt_account_email: admin@fpgas.online` in group_vars and ps1's host_vars (decision 9). The value is only passed to `certbot certonly` on first issue, so also run `certbot update_account --email admin@fpgas.online` once per gateway (a task, or a noted manual step) | D |
| 9 | `vlan_ports` → `vlan_ifaces` | dir, task names | X |
| 10 | `wssh` → `webssh` | role only; the unit stays `wssh` | X |
| 11 | `netif` → `nics` | | X |
| 12 | `nfs` → `nfs_server` | | X |
| 13 | (dropped) | PR #45 moves chrony out of `pxe` into `timesync` and lands before step 14 (decision 11) | – |
| 14 | `pxe` → `dnsmasq` | + `dhcp_range`, `pxe_test_clients`; the `send_stat.conf` drop-in task moves in from `site/tasks/pistat.yml`, and `site`'s "Restart dnsmasq" handler goes (decision 19) | D |
| 15 | `site` → `website` | + `site_*` → `website_*`, `static_dir` → `django_static_dir`, group_vars `site.yml` → `website.yml`, task files; `website_poe_package_override` commented out in the defaults | D C |
| 16 | `ttsite` → `tt_website` | + `ttsite_*`, `ttsite_domain` → `tt_fqdn`, drop `ttsite_pi_network`; `tt-boards.yaml.j2` moves to `ansible/templates/`, its three readers use the bare name, and `nfsroot_inputs.py` INPUTS follows (decision 19) | D C |
| 17 | extract `nfsroot_site` from `fixpi` | move the gateway tasks per 2.10, with `fleet.toml.j2` from `onpi/templates`; add the test that forbids `role_path }}/..` (the last cross-role read is gone, decision 19); site.yml runs `nfsroot_site`; `fixpi` becomes CI-only; drop `fixpi_image_build`, `fixpi_generate_host_keys` and CI's `--skip-tags pipw,keys`; `fixpi_ansible_*` → `nfsroot_ansible_*` (into `srv.yml` for now; step 24 renames the file) | D C (upgrade-stage rebuild) |
| 18 | `fixpi` → `nfsroot_netboot` | rename the now CI-only role + its task files | C |
| 19 | `fpgas_apt` → `nfsroot_apt` | + `apt_cache/tasks/nfsroot.yml` → `root-sources.yml` and the two README notes (decision 2); `fpgas_apt_nfsroot_watchdog_*` → `nfsroot_apt_watchdog_*`. **Before it**, a separate PR gives `nfsroot_generation` its own task for the gateway's nfsroot-watchdog apt source and drops its include of `fpgas_apt` (decision 13) | C |
| 20 | `cam_pi` → `nfsroot_cam` | | C |
| 21 | `onpi` → `nfsroot_packages` | + `tftpd_port` moved in (delete group_vars `ci.yml`, its two symlinks and its INPUTS/UPGRADE_INPUTS entries) | C (upgrade-stage rebuild) |
| 22 | `nspawn_pi` → `nfsroot_chroot` | + `nfsroot_build_deb_cache` | C (upgrade stage rebuild) |
| 23 | `img` → `nfsroot_image` | + `img_nfsroot_image` → `nfsroot_image_ref`; `/var/cache/pib` in `.github/actions/nfsroot-setup/action.yml`; `tests/test_nfsroot_promotion.py` (reads `roles/img/defaults`) | D C (base stage rebuild) |
| 24 | RasPiOS vars | `srv.yml` → `nfsroot.yml`, `img_host`/`dir_date`/... → `raspios_*`, `dist` → `raspios_release`; also `nfsroot_inputs.py` (`BASE_VARS`, `BASE_VAR_FILES`, INPUTS, UPGRADE_INPUTS), `tests/test_nfsroot_inputs.py`, the action's `hashFiles(.../srv.yml)` cache key and both symlinks | C D (base stage rebuild) |
| 25 | `nfs_root` → `nfsroot_dir` | one mechanical PR (~250 refs, incl. `img/tasks/build.yml` and both stage playbooks) | D C (base stage rebuild) |
| 26 | account vars | `user` → `pi_user`, `user_name` → `server_user_name`, `pi_pw` → `pi_password` (`tests/vm/run_tests.py` reads `pi_pw` from test-vm's host_vars by name) | D C (upgrade-stage rebuild: `user` is in `srv.yml`) |
| 27 | LAN vars | `pib_network*` → `lan_ip4_base`/`lan_ip6_base`, `pib_domain` → `lan_domain`; `dnsmasq_auth_zone` defaults to `lan_domain` and is removed from welland's host_vars (decision 10); move the template's auth-block gate off `dnsmasq_auth_zone is defined` (2.5.1) | D |
| 28 | name vars | `domain_name` → `site_fqdn`, `streaming_frontend_*`, `fixture_path` → `site_fixture`, `conference_name` → `nginx_file_prefix`, `fleet_broker` → `fleet_enabled` | D (upgrade-stage rebuild: CI inventory sets `domain_name`) |
| 29 | switch vars | delete the dead `switch:` blocks in welland's and test-vm's host_vars; ps1's `switch` → `snmp_switch`, `nos` → `pis`; `switches_manage` (decision 12) | D C |
| 30 | groups | `nbp` → `gateway`, `pig` → `web`, CI `pi` → `pi_chroot` (incl. `hosts:` in all three `ci-nfsroot*.yml`, README's `--limit nbp,uhubctl,pig` / `nbp,pi`). Roles stop naming the group (decision 20): `nfsroot_packages/tasks/tt.yml` reads a new default `nfsroot_packages_gateway_host`, and `nfsroot_apt/defaults/main.yml` builds its cache URL from a new default `nfsroot_apt_gateway_host`; each default is `{{ groups['gateway'][0] }}`, empty when the group is. `apt_cache/README.md` is updated to match. `verify-pi.yml` is a playbook and keeps `groups['gateway']` | D C (base stage rebuild: `ci-nfsroot-base.yml`) |
| 31 | runner task file | `ci-nfsroot-runner.yml` → `tasks/ci-runner.yml` | C (base stage rebuild: it is in `BASE_FILES`) |
| 32 | `tests/ci/` → `ci/` | + `tests/inventory/test-hosts` → `tests/inventory/hosts` (2.3; `run_tests.py`, verify-pi's usage line). `nfsroot_inputs.py` finds the repo as `parents[2]`, which becomes `parents[1]` | C |
| 33 | task-name sweep for the kept roles | section 3 rows for apt_cache, jump, sshd, mqtt, and the verify prefix; `stream_server`'s task files and template (2.7); the play names in 2.2 | X |
| 34 | host `fpgas.online` → `welland.fpgas.online` | announced; section 4.1 | D M |
| 35 | kept roles: `__` names, argument specs, entry points | one PR per role that is not renamed (`apt_cache`, `apt_client`, `automation_user`, `firewall`, `jump`, `lldp`, `mqtt`, `nfsroot_generation`, `operators`, `server_user`, `ssh_key_fetch`, `sshd`, `stream_server`, `switch_vlans`, `uhubctl`, `webrtc`): `register`/`set_fact` names → `__<role>_<name>` (decision 17), add `meta/argument_specs.yml` (decision 22), list the entry points in the README. `firewall` and `webrtc` also declare `firewall_dns_query_sources` and `webrtc_additional_hosts` in their defaults (commented out where a default would switch the role on), which empties the inventory-prefix test's allow-list. These PRs depend on no rename and can land any time after step 1 | X (C where the role is an image input) |
| U | **uhubctl into the NFS root + verify-pi check** | its own PR, not part of any rename; section 5.1. Can land any time, ideally after step 3 | C |

Every step that touches an image input (`nfsroot_inputs.py` INPUTS: steps 1, 3, 5, 5a, 6, 7, 16 to 26, 28, 30 to 32, 34 and U, 27 if it edits `filter_plugins/port_vlans.py`, and the step 35 PRs for roles that are image inputs) produces a new NFS root image, so check the image after each (`nfsroot_diff.py`). Steps 1, 23, 24, 25, 30 and 31 also rebuild the CI base stage (a full RasPiOS download), and steps 7, 17, 21, 22, 26, 28 and 34 the upgrade stage (section 4). Steps 24 to 30 touch many files at once, so rebase each onto main right before merging.

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
| verify-pi, passive check (every run) | run `uhubctl` with no action, which only lists hubs (with `become`: without the udev rule uhubctl needs root to open the hubs, and verify-pi runs `become: false`). On a Pi 4/5, assert that it reports a hub with power switching. On other models and the VM Pi, skip with a message instead of failing. Take the model from `/proc/device-tree/model` in the existing collector call |
| verify-pi, active check (detection-based, same logic on every Pi) | runs only when **all** of these are detected on the Pi: the model (`/proc/device-tree/model`) is a Pi 4/5; uhubctl finds a hub with power switching; no login session other than the verifier's (`loginctl list-sessions`, which also covers the web terminal, since it logs in over ssh); no process holds a USB serial/JTAG device (`fuser` on `/dev/ttyUSB*`, `/dev/ttyACM*` and the device nodes `/dev/bus/usb/*/*`: `fuser` on the directory itself checks nothing beneath it); no openFPGALoader/openocd running. It records `lsusb`, cycles both root hubs (`uhubctl -a cycle -d 3`), waits up to 30 s, and asserts the same devices came back. An `always:` block runs `uhubctl -a on`, so a failed check never leaves the ports off. Afterwards it restarts `fpgas-cam.service` if a USB grabber was on the hub |
| why it does not disturb a board in use | no per-Pi switch: it is skipped whenever the Pi detects anyone logged in or a tool holding the board; never on a Pi 3 or earlier; tried first on the known-good boards. When it does run, the FPGA reloads from its flash, which is the same result as the PoE reset the site already offers |
| VM test | rpi-qemu has no switchable hub, so both checks detect no switchable hub and take their skip path |

### 5.2 Open PRs that touch the same names (2026-09-29)

"One PR open at a time" applies to this sequence only; these PRs from other
sessions are already open and collide with it. Decision 18 reverses
decision 14: those sessions are told the conventions now, and each PR
renames its own new roles **before it merges** (R1–R6 below). It has to:
ansible-lint is blocking, and a hyphenated role directory fails `role-name`.
Each PR still lands on its own schedule, and the rename step it collides
with is rebased onto it. A step that renames something an open PR still
touches leaves that PR for its owner to rebase.

| PR | touches | collides with |
|---|---|---|
| #55 config.txt single owner | `fixpi/tasks/{netboot,tweeks}.yml`, revives `templates/boot/config.txt.j2`, verify-server | steps 5, 17, 18 (the split map's `config-txt.yml`); the unused-template row in 2.7 |
| #45 pi-clock-ntp | moves chrony out of `pxe` into a new gateway role `timesync` (the same move as step 13, under another name); replaces the root's timesyncd with chrony (new image role `pi-clock`) and deletes fixpi's timesyncd tasks | step 13 (`chrony`) and 2.10's `nfsroot_site/tasks/timesyncd.yml`; `pi-clock` breaks convention 1 (kebab case, no `nfsroot_` prefix) |
| #37 rpi-trixie | `srv.yml`, `fixpi/tasks/netboot.yml`, `roles/fpgas-apt` (pre-underscore path), `onpi/tasks/apt.yml`, `tests/vm/run_tests.py` | steps 17–19, 21, 24 (`dist`) |
| #122 felboot, #123 usbboot | `ci-nfsroot.yml`, new image roles `felboot`, `usbboot` | convention 1 would name them `nfsroot_felboot`/`nfsroot_usbboot`; step 30 (`hosts: pi`) |
| #88 fleet-watchdog | `site.yml`, `verify-server.yml`, `host_vars/fpgas.online.yml`, new role `fleet-watchdog` | steps 30 and 34; kebab-case role name |
| #58 board-access | `site/tasks/pistat.yml`, `site.yml`, `verify-server.yml`, new role `board-access` | steps 14, 15, 30; kebab-case role name |
| #56 split-prep | `inventory/hosts`, `host_vars/fpgas.online.yml`, `ttsite/templates/tt-boards.yaml.j2`, new groups `site_welland`/`site_ps1` | steps 16, 30, 34 |
| #59 hypervisor, #54 welland pull, #51 fpgas-apt domain | `inventory/hosts`; `host_vars/fpgas.online.yml`; `roles/fpgas-apt` (pre-underscore path) | steps 30, 34; step 19 |
| #129 NFS root generations spec | per-generation root dirs under the `nfs_root` path | section 4's "keep `/srv/nfs/rpi/<dist>`"; step 25 |

Renames each open PR makes itself, before it merges (decision 18). They are
no longer steps of this sequence:

| # | PR | rename inside that PR | why |
|---|---|---|---|
| R1 | #45 | image role `pi-clock` → `nfsroot_clock` (`timesync` keeps its name) | fails `role-name`; family prefix |
| R2 | #122 | `felboot` → `nfsroot_felboot` | family prefix |
| R3 | #123 | `usbboot` → `nfsroot_usbboot` | family prefix |
| R4 | #88 | `fleet-watchdog` → `fleet_watchdog` | fails `role-name` |
| R5 | #58 | `board-access` → `board_access` | fails `role-name` |
| R6 | #37, #51 | nothing new: they use the pre-underscore `roles/fpgas-apt` path and must be rebased onto `fpgas_apt`/`nfsroot_apt` by their owners | – |

The new roles in these PRs also follow section 1 from the start: `__` names
for results, `meta/argument_specs.yml`, and handler names that do not clash
within a play.

---

## 6. Decisions (settled)

| # | question | decision | where it lands |
|---|---|---|---|
| 1 | `fixpi`: rename whole or split? | split: **`nfsroot_netboot`** (CI: make the root netbootable) + **`nfsroot_site`** (gateway: per-site values and boot-file publishing) | 2.1, 2.10, steps 17–18 |
| 2 | `fpgas_apt`, `cam_pi` | **`nfsroot_apt`**, **`nfsroot_cam`**; `apt_cache/tasks/nfsroot.yml` → `root-sources.yml` + README notes | 2.1, steps 19–20 |
| 3 | `mqtt` | **keep** | 2.1 |
| 4 | `pxe` | **`dnsmasq`**; chrony goes to #45's **`timesync`** (decision 11) | 2.1, step 14 |
| 5 | host `fpgas.online` | **`welland.fpgas.online`**, its own announced PR near the end | 4.1, step 34 |
| 6 | `uhubctl` | **keep**; move it into the NFS root and add a safe verify-pi check | 5.1, step U |
| 7 | `fixpi_server_monitor` tasks | move to a new gateway role, **`serial_monitor`** | 2.1, step 7 |
| 8 | `resolve.conf.j2` | **delete** with its task and manifest entry | step 5 |
| 9 | `letsencrypt_account_email` | **`admin@fpgas.online`** | step 8 |
| 10 | `dnsmasq_auth_zone` vs `lan_domain` | `dnsmasq_auth_zone` **defaults to `lan_domain`**; set it only where they differ | step 27 |
| 11 | #45 vs the `chrony` role | adopt #45's **`timesync`**; #45 lands first; the `chrony` step is dropped; 2.10 follows #45's removal of fixpi's timesyncd tasks | 2.1, 2.10, step 13 |
| 12 | welland/test-vm `switch:` | **delete** the dead blocks; legacy single-switch sites' `switch` → **`snmp_switch`** | 2.5.1, step 29 |
| 13 | `nfsroot_apt` on the gateway | move the gateway use out: `nfsroot_generation` installs its own nfsroot-watchdog source; vars `nfsroot_apt_watchdog_*` | 2.1, 2.5.2, step 19 |
| 14 | other sessions' open PRs | ~~do not ask them to rename; rename their roles **after** they merge, as extra steps~~ **reversed by decision 18** | 5.2 (R1–R6) |
| 15 | handler names | a handler name is defined once per play, or every definition is identical. A bug-fix PR before the renames fixes `site`'s "Reload nginx" (it restarts), `cam_pi`'s differing "Reload-systemd" and the "Restart dnsmasq" that restarts nothing (was O5), and adds a test. The off-rule names are fixed: `Reload sshd for pubkey-only` → `Reload sshd`, and the `(apt-cache)` suffixes go | 1, 2.6, steps 5a, 6 |
| 16 | shared inventory vars | keep the short topic names, read directly by roles, as a **documented deviation** from GPA 4.1.4/4.1.15. A topic-named var is never defined inside a role and never passed through `include_role … vars:`. The missing prefixes `pi_`, `nginx_`, `django_`, `snmp_` are **declared** (not renamed: `django_dir`, `django_project_name` and the live `SNMP_SWITCH_*` keys already use them). A test: an inventory var may start with `<role>_` only if that role's defaults declare it | 1, steps 1, 35 |
| 17 | internal variables | **`__<role>_<name>`** for `register` and `set_fact` results; documented inputs stay `<role>_<name>`. Each rename PR does its role; step 35 does the kept roles. A convention only: Ansible gives no privacy | 1, 2.5.2, section 5 intro, step 35 |
| 18 | other sessions' open PRs (reverses 14) | hyphenated role names fail the blocking `role-name` lint, so those sessions are told the convention now and rename inside their own PRs: `nfsroot_clock`, `fleet_watchdog`, `board_access`, `nfsroot_felboot`, `nfsroot_usbboot` | 5.2 (R1–R6) |
| 19 | cross-role file reads | **remove** them: `send_stat.conf` moves into `dnsmasq`, `fleet.toml.j2` into `nfsroot_site`, `tt-boards.yaml.j2` to a play-level `ansible/templates/`. A test forbids `role_path }}/..` | 1, 2.6, 2.7, 4, steps 14, 16, 17 |
| 20 | group names in roles | roles stop naming inventory groups; a role default carries the host (`nfsroot_packages_gateway_host`, `nfsroot_apt_gateway_host`) | 1, 2.3, step 30 |
| 21 | role naming rule | name a role by its function, or by the daemon when it manages exactly one. The proposed names stay (`dnsmasq`, `webssh`, `mqtt`, `lldp`, `firewall`) | 1 |
| 22 | argument specs | `meta/argument_specs.yml` per role, added in its rename PR; step 35 covers the kept roles | 1, section 5 intro, step 35 |

**Open** (for the owner):

| # | question | options / note | where |
|---|---|---|---|
| O1 | removal of `boot/userconf.txt` and the password marker | on the gateway (`nfsroot_site`, as the map has it now) or in CI (`nfsroot_netboot`), since the image is the only source of those files after the split | 2.10 |
| O2 | `docs/hardware/` (dated, but not a plan or spec) names `fixpi` paths | update it in the rename PRs, or leave it as history | section 5 intro |
| O3 | stale gateway files after the split: `/usr/local/sbin/{maintenance,production}.sh`, `/usr/local/sbin/chroot-mount-pi-fs.bash` | add a one-off `state: absent` task (in `nfsroot_site`), or remove them by hand | 2.8, steps 5, 17 |
| O4 | uhubctl hub locations | name them per model (Pi 4: `-l 1-1` and `-l 2`; Pi 5: its root hubs) instead of relying on uhubctl acting on every hub when `-l` is missing | 5.1 |
| O5 | (settled: decision 15 fixes the handler bugs in step 5a) | – | 2.6 |
| O6 | detection to replace `verify_pi_fpga_expect` and `verify_pi_header_uart_console` (test-pi's only host_vars) | FPGA: accept fpgas-verify's `missing` exactly when no board is detected on USB/JTAG. Console: the VM's U-Boot appends `console=ttyAMA0`; check the served `cmdline.txt` rather than `/proc/cmdline`, or detect that no FPGA is on the header UART. Design it in #157 part 3 or a follow-up | 2.5.1 |
| O7 | other per-board inventory data: `sunxi_boards` (host, USB path, `hat_uuid`, used by verify-pi's placement assert), `tt_boards` (the TT catalogue, per switch/port), legacy `switch.nos` / `snmp_switch.pis` (per-Pi MAC and serial) | the rule says "almost all": decide which are site data the gateway needs (DHCP, TFTP, the site's catalogue) and which verify-pi can replace with detection | 2.5.1 |

## 7. Changes from the first draft

| from | to | reason |
|---|---|---|
| tags: rename to role names (section 2.4, convention row, PR steps) | tags removed by #157; no tag renames | owner decision. Kept only the #157 notes: `pipw,keys` → reuse `fixpi_image_build`; `hw-camera,hw-fpga` → on-Pi detection, no variables (#157 part 3); plus the `run_tests.py` use that #157 missed |
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
| `fixpi_server_user_pubkey` → `nfsroot_config_server_pubkey` | `nfsroot_config_server_user_pubkey` (now `nfsroot_site_server_user_pubkey`, 2.5.2) | `server_pubkey` reads as a host key |
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
| `pxe` → `dnsmasq` (chrony undecided) | `dnsmasq` + new role `chrony` (since replaced by #45's `timesync`, decision 11) | decision 4 |
| `uhubctl` → keep or delete | keep; runs in the NFS root; `[uhubctl]` group and gateway plays deleted | decision 6; section 5.1 |
| `maintenance.sh`, `production.sh`, `manage.yml` → `mode-scripts.yml` | delete | they are TODO stubs; the only caller is `when: false` |
| `chroot-mount-pi-fs.bash` → keep, installed on the gateway | moves to `nfsroot_netboot`, not installed on the gateway | only CI-only tasks use it |
| `fixpi/tasks/verify/pi.yml` → (not listed) | delete | nothing includes it |
| `letsencrypt_account_email` value | `admin@fpgas.online` (+ `certbot update_account`) | decision 9 |
| `dnsmasq_auth_zone` set per host | defaults to `lan_domain` | decision 10 |
| execution order: 29 steps | 34 steps + the separate uhubctl PR (U) | new steps for dead fixpi code, `serial_monitor`, the ACME email, `chrony`, and the two-step fixpi split |

**Changes after the convention check** (fourth draft; sources in 1.1, decisions 15–22):

| from | to | reason |
|---|---|---|
| role rule: name the role after the component | by function, or by the daemon when the role manages exactly one | GPA 4.1.1; the old wording contradicted the `firewall` row (decision 21) |
| every role var, `register` and `set_fact` name is `<role>_<name>` | inputs `<role>_<name>`, results `__<role>_<name>` | GPA 4.1.4 (decision 17) |
| topic prefixes: 8 listed | 12: `pi_`, `nginx_`, `django_`, `snmp_` declared; the lint constraint and the documented deviation stated; a test | the proposal used four prefixes it had not declared (decision 16) |
| cross-role reads follow the role they point at | removed; shared file in `ansible/templates/`; a test | they fail at run time, not at lint (decision 19) |
| `hostvars[groups['nbp'][0]]` → `groups['gateway']` in two roles | a role default carries the host | GPA 4.1.18 (decision 20) |
| handlers: `Reload nginx` "same action everywhere"; suffix pattern "fine" | once per play or identical; step 5a fixes three bodies and adds a test | `site` restarts while four roles reload; Ansible runs only the last handler loaded (decision 15, was O5) |
| `Reload sshd for pubkey-only` kept | `Reload sshd` | same body as jump's, so the rule wants one name |
| `Restart apt-cacher-ng (apt-cache)`, `Reload nginx (apt-cache)` kept | no suffix | nothing else in a gateway play defines either name |
| `Restart dnsmasq` in `site` kept | deleted in step 14 | its drop-in task moves into `dnsmasq` |
| `site/tasks/pistat.yml` kept whole | its dnsmasq drop-in moves into `dnsmasq` | decision 19 |
| `ttsite/templates/tt-boards.yaml.j2` (not listed) | `ansible/templates/tt-boards.yaml.j2` | three readers (decision 19) |
| `fleet.toml.j2` moves in step 21 | moves in step 17, with `fleet-site.yml` | so the no-cross-role-read test can land in step 17 |
| task files kebab-case "the most common style in the repo today" | kebab-case per GPA 3.1 and the Ansible sample layout | on main the multi-word task files are 9 kebab and 9 snake |
| task-name rules 1–7 | + rule 8 (Jinja last) and rule 9 (`name[prefix]` deliberately not adopted) | lint `name[template]`; GPA 4.1.19 |
| (no rule) | booleans: `_enabled` for features, `_manage` for "this role may touch X" | GPA 9.2 fixes only "positive" |
| (no rule) | `tasks_from` entry points are public interface, listed in each README | GPA 4.1.1 |
| (no rule) | `meta/argument_specs.yml` per role | GPA 4.1.20 (decision 22) |
| retired-vars guard: "a task at the top of site.yml/web.yml" | `tasks/retired-vars.yml`: an `assert` over a `{old: new}` map with the `varnames` lookup, in `pre_tasks` of all seven entry playbooks | argument specs accept unlisted names, so they cannot guard |
| `fixture_path` → `site_fixture`, `static_dir` → `django_static_dir` had no step | steps 28 and 15 | missed |
| `website_poe_package_override` undefined and unlisted | commented out in the defaults | GPA 4.1.15 |
| decision 14: other sessions' PRs land as written, R1–R5 run afterwards | decision 18: they rename inside their own PRs | hyphenated role names fail the blocking `role-name` lint |
| 34 steps + U + R1–R6 | 34 steps + 5a + 35 + U; R1–R6 are no longer steps | step 5a (handler fixes), step 35 (kept roles) |

**Factual errors in the first draft**, now corrected in the tables above:

| draft claim | fact |
|---|---|
| `domain` is used for apt repo URLs and dnsmasq | nothing reads it |
| `streaming_frontend_hostname` feeds `domain_name` in one host_vars file | two live files (`fpgas.online.yml`, `tests/.../test-vm.yml`) + orphan `gator.yml` |
| `fixpi_server_monitor` gates the `/boot/firmware/config.txt` task | it gates brltty, tio, dialout and getty. "Is server pi" and "Enable /dev/serial0" run on every build host, gated only by a stat |
| `fixpi`: CI makes the root netbootable, the gateway applies the site layer | both runs do nearly all of it; only the ARM-code tasks are CI-only. `manage.yml` installs its scripts on whichever host runs fixpi (the gateway and the CI runner), not in the root |
| `nspawn_pi` is CI only | its verify runs on the gateway through verify-server.yml and installs `nfsroot-kernels` there |
| `groups['nbp']` is read in `onpi/tasks/tt.yml` and verify-pi | also in `fpgas_apt/defaults/main.yml` |
| renaming CI's `pi` group affects verify-pi's default `pi_live:pi` | it does not; the `:pi` is already dead (no `pi` group in the production or test inventories) |
| `mqtt`: broker for fleet self-registration | shared with sensors2mqtt |
| `switch`: the legacy single switch | right after all: welland and test-vm set it, but nothing reads it on a per-port host (this correction was itself wrong; see the review corrections below) |
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

**Corrections from the code review** (2026-09-29, against `1887743`):

| claim | fact |
|---|---|
| `cam_pi` enables `cam.service` (2.1, 2.8, 5.1) | the unit is `fpgas-cam.service` |
| `fpgas_apt` runs only in the CI chroot | `nfsroot_generation/tasks/install.yml` also includes its `nfsroot-watchdog.yml` on the gateway |
| `switch` is read on welland for the PoE env | nothing reads it on a per-port host; only ps1 (legacy) uses it |
| the fixpi console banner shows the password, so it is per site | it shows "fpgas.online Pi", the tty and the IP; moved to `nfsroot_netboot` |
| `netif` → `nics` touches `tests/test_netif.py` | that test covers `filter_plugins/netif.py`, not the role |
| group_vars `ci.yml` folds into the CI `all.yml` | its only var moves to a role default, so the file and its symlinks go |
| `pib_network` is read by 5 roles | 4 (vlan_ports, firewall, pxe, site) + the filter plugin |
| only the `img`/`nspawn_pi` renames rebuild CI stages | steps 23, 24, 25, 30, 31 rebuild the base stage; 7, 17, 21, 22, 26, 28, 34 the upgrade stage |
| step 5 can delete `manage.yml` outright | it is the only installer of `chroot-mount-pi-fs.bash`, which the CI tasks need on `PATH` |
| 2.10 assigned every fixpi task | it missed "Remove userconf.txt", "Set perms", and all files, templates and the README |
| missed references | `docs/access.md` (new), `site_poe_package_override`, `apt_cache_host` (from `domain_name`), the action's `/var/cache/pib` and `srv.yml` cache key, `nfsroot_inputs.py`'s `BASE_VARS`, `run_tests.py`'s `pi_pw`, the runbooks' tag commands, the `vault_*` keys, the unused `config.txt.j2`, the open PRs in 5.2 |
