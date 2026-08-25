# tweed fresh-rebuild log

Goal: confirm CI is a faithful proxy for real hardware. Every issue hit during a
rebuild attempt is recorded here; each then gets a why-CI-missed-it analysis and
a CI fix that demonstrably detects it, landed via PR. Iterate until a fresh
install reaches a fully working system with zero unrecorded intervention.

Working-system definition: verify-server.yml + verify-pi.yml green against real
hardware (no skip-tags except hw ones that apply), Pis PXE-boot from the NFS
root, Django site + tinytapeout page served via welland.fpgas.online.

Ground rules (Tim, 2026-08-25):
- Only welland.fpgas.online goes down; root site (GitHub Pages) + ps1 unaffected.
- Hotfix-and-continue within an attempt; every intervention recorded here.
- No data migration: public repos + their published assets only.
- Disk selection stays remote: BIOS boots PXE first, falls through to current
  disk; the served pxelinux config decides (installer / chain to new disk /
  local boot).
- Track per-task wall-clock on every run (profile_tasks callback from run 4;
  task_timer.py tailer for run 3) — slow tasks are fix candidates and the
  hardware-vs-CI timing comparison is itself parity data (Tim, 2026-08-25).

## Attempt 1 — 2026-08-25

- infra: main @ 1db0b5b (worktree .worktrees/tweed-rebuild)
- /srv/pxe @ b701cf8; preseed http://10.99.21.1/pxe/hosts/tweed.preseed (HTTP 200)
- Target disk: /dev/disk/by-id/ata-Samsung_SSD_850_EVO_250GB_S21NNXAG532383L (spare)
- Fallback disk: ...532419V (current bookworm system, untouched)

### Preflight findings

| # | Issue | Workaround | CI/infra follow-up |
|---|-------|------------|--------------------|
| P1 | gdoc2netcfg has no `password` credential for bmc.tweed | BMC answers Supermicro default ADMIN/ADMIN | add credential to store; consider changing default pw |
| P2 | /srv/tftp had no chain.c32 (needed to PXE-chainload a chosen disk) | copied from syslinux-common 6.04 (matches deployed pxelinux) | commit note in /srv/pxe docs |
| P3 | README/ansible.cfg document vault pass at ~/.config/fpgas-online/vault-pass but the real file is ~/.config/welland-ansible/vault-pass | symlinked fpgas-online/vault-pass -> welland-ansible/vault-pass | fix README or move file |
| P4 | Direct `ipmitool sol activate` conflicts with the conserver console server (console `tweed` -> 10.1.5.131, auto-logs /var/log/conserver/tweed.log) | use conserver; SOL capture comes free | document in PXE-BOOT.md |
| P5 | pxe-set-boot override auto-expires after 10 min — power cycle must happen inside that window | cycled immediately after arming | consider a --ttl flag or note in docs |

Kickoff: 2026-08-25 ~16:06 local — `pxe-set-boot tweed trixie-install` (override to 16:16),
IPMI `chassis bootdev pxe` + `power cycle`. Console: /var/log/conserver/tweed.log.

### Install-phase issues

| # | Issue | Evidence | Workaround | CI/infra follow-up |
|---|-------|----------|------------|--------------------|
| A1-1 | PXE never reached the installer: only LAN1's (eth-local `…4A`, gsm7252ps-s2 port 47, PVID 21 — no DHCP server there while tweed is down) PXE OpROM was enabled in BIOS; LAN2 (eth-uplink `…4B`, port 48, PVID 121 transit where dnsmasq@tfpgas listens) had `Load Onboard LAN 2 Option ROM = Disabled`. One attempt, PXE-E51, fell through to old disk. dnsmasq@tfpgas journal empty = no DISCOVER arrived. | conserver log 16:07–16:09; FDB port 47/48; PVIDs 21/121; BIOS PCIe/PCI/PnP screen | FIXED 16:25 via conserver-driven BIOS session (pexpect+pyte, `bootdev bios`): LAN1 OpROM→Disabled, LAN2 OpROM→Enabled, F4 save. Board is X9SPV-F/LN4F (not X9SCV). | CI gap: hardware-only property, untestable in QEMU; document BIOS requirement in PXE-BOOT.md + add runbook preflight "verify PXE CLIENT MAC = uplink" |

| A1-2 | d-i invisible and stuck: installer APPEND ends `console=tty0`, so the d-i UI goes to VGA — over SOL we see nothing after early boot. Attempt 1b sat >7 min with no preseed fetch and the uplink not answering ARP (netcfg stuck at a video-console dialog, probably NIC choice/DHCP failure with 2 of 4 NICs carrying link). Remote-only operation requires the serial console to be primary. | nginx access.log (no /pxe fetch after 16:27:13), tcpdump br-tfpgas (unanswered ARP for .2), conserver quiet after "bootconsole disabled" | sed permanent+override pxelinux cfg: `console=tty0 console=ttyS2,115200n8` (ttyS2 last) for all boot labels; power cycle and retry | /srv/pxe docs + permanent config are hand-managed on ten64: record required console order in PXE-BOOT.md; consider netcfg/choose_interface=eth-uplink MAC pin in preseed |

| A1-3 | Attempt 1c: trixie d-i kernel hangs ~13 s into boot, before /init runs (no "Loading, please wait", no "Freeing unused kernel memory", console dead — no echo on keypress, no netconsole packets, transit silent). Same stall explains 1b. Prime suspect: legacy `netconsole=+@/,6666@10.99.21.1/…` cmdline arg (init-time network touch, no source dev, 4 NICs with modular drivers) — tolerated by older d-i kernels, wedges the trixie one; `earlyprintk=` redundant beside `earlycon=`. | conserver log stops 16:37:45 @13.07 s uptime both runs; tcpdump udp 6666 empty; ping/ARP dead | 1d: stripped `netconsole=`/`earlyprintk=` from active+permanent pxelinux configs, re-armed, cycled — **still hangs at the same point (~8.7 s, USB HID last), netconsole RULED OUT**. Note: every kernel boot so far ran after the 16:25 BIOS save; A/B test = boot old disk to separate BIOS-change fallout from trixie-d-i-specific breakage. Trixie netboot build (2026-07-06 mirror copy) was never boot-tested on this hardware. | A/B 16:49-16:51: old bookworm system boots to login fine with the BIOS changes → machine healthy, hang is d-i-netboot-specific. A/B 16:52-16:56: **bookworm-install hangs at the identical point (USB HID ~9.4 s)** → two kernel series failing identically = shared APPEND args, not the trixie build. Suspects: `console=ttyS0`/`console=ttyS1` (dead UARTs — kernel console writes to them wedge/crawl via wait_for_xmitr timeouts) and/or `earlycon`. 1e: trixie APPEND reduced to `console=ttyS2,115200n8` only (earlycon/ttyS0/ttyS1/tty0 dropped) in both configs. | if trixie-d-i-specific: try current trixie netboot build; possibly upstream bug |

| A1-4 | With the kernel unwedged (1e), d-i reached netcfg and showed "[!!] Network autoconfiguration failed": `interface=auto` picks the first carrier-up NIC = eth-local (`…4A`, no DHCP server while tweed is down) and netcfg does NOT fall back to other NICs. Same class as A1-1 but at the d-i layer. | 1e serial UI 16:59; dialog screenshot in conserver log | 1f: `interface=0c:c4:7a:16:3b:4b` (pin by MAC) in both installer APPENDs, both configs | permanent config now carries the pin; document in PXE-BOOT.md. A1-3 root cause CONFIRMED = extra `console=ttyS0/S1` + `earlycon` args (dead UARTs wedge kernel console writes); minimal `console=ttyS2,115200n8` boots fine. |

| A1-5 | Per-host preseed never applied: early_command.sh uses `$(hostname)`, which in d-i is netcfg's reverse-DNS first label of the DHCP address — gdoc2netcfg PTRs start with `ipv4.` → fetched `hosts/ipv4.preseed` (404) → tweed.preseed (disk pin!) skipped → interactive partman instead of auto. The legacy `eth0-*.preseed` filenames are fossils of the same PTR-derived behavior. | nginx 17:04:09 `GET /pxe/hosts/ipv4.preseed 404`; partman dialog on serial | early_command.sh now parses `hostname=` from /proc/cmdline first (committed in /srv/pxe) | preseed pipeline gap: nothing verified the per-host preseed actually applies; add an early_command echo of applied files to the d-i syslog + runbook check |

**Attempt 1g: INSTALL SUCCEEDED.** 17:08:49 cycle → 17:10:53 preseed → 17:10:54
hosts/tweed.preseed applied (200) → auto partman/base/kernel/GRUB → 17:14:53
"Rebooting into your new system" (~4.5 min install, local mirror). CORRECTION: the
post-install fall-through booted the **old** system (verified via SSH: debian 12.10,
snap mounts, eth-fpgas VLAN; the "swap on sda5" console line was the old disk
enumerated as sda that boot — device names are probe-order-unstable, identify disks
by serial only). New install on …383L needs the PXE chainload: added `local-new`
label (chain.c32 hd1) to both configs, override default for one boot. Old system
also accepts the ansible key (2025 provisioning), so SSH success ≠ new system:
**verify by /etc/debian_version + root disk serial.** Note: tweed.preseed's add-kernel-opts still carries
`earlycon=uart8250,io,0x3e8` — booted fine (A1-3's killer was ttyS0/ttyS1), but
consider dropping for consistency.

**Chainload verified 17:21:39: new system (Debian 13) boots via PXE `local-new`
(chain.c32 hd1); ansible-firstboot self-disabled cleanly.**

| A1-6 | Installed hostname is `ipv4`, not `tweed`: netcfg's DHCP/reverse-DNS name beat the preseeded `netcfg/get_hostname` (that key only pre-answers the question; DNS-derived names override it). Login banner: "Debian GNU/Linux 13 ipv4 ttyS2". | conserver 17:21:39 | fix on the box via site.yml/hostnamectl; preseed fix = `d-i netcfg/hostname string tweed` (forces regardless of DHCP/DNS) in tweed.preseed | same class as A1-5: PTR-derived identity leaking in; add to preseed + document |
| A1-7 | New system unreachable on 10.99.21.2: d-i wrote `allow-hotplug enp2s0 / iface enp2s0 inet dhcp` (installer-era name, DHCP), and on the installed boot no DHCP request ever reached the transit (lease mtime = d-i's 17:04). Interface itself is up (stable-privacy LLA). Name-fragile ifupdown config is exactly why the netif role (MAC-matched .link + static networkd) exists — but ansible can't run it without this first SSH. Bootstrap gap. | mounted LV: /etc/network/interfaces; installer syslog "Writing DHCP stanza for enp2s0" | ROOT CAUSE (diagnostic boot 17:32): the new system DOES send DHCP from `…4B` — dnsmasq@tfpgas never answers because its single-address range (10.99.21.2) was held by d-i's lease under a different client-id; MAC same, client-id differs → "no address available" → silence. Fix on ten64: `dhcp-ignore-clid` + `dhcp-host=0c:c4:7a:16:3b:4b,10.99.21.2,infinite` in /etc/dnsmasq.d/tfpgas/00-dhcp-pxe.conf, stale lease cleared. The enp2s0/ifupdown name-fragility remains a latent risk until netif's networkd config takes over. | every d-i↔OS transition would re-hit this; also applies to any single-address transit leaf; document in dnsmasq leaf conventions |
| A1-9 | Install picked LVM (base preseed default) and the VG is named `ipv4-vg` — the A1-6 hostname leak frozen into LVM metadata. Harmless functionally; fixed properly by reinstall after the netcfg/hostname preseed fix (committed: /srv/pxe). | lvs: ipv4-vg/root 224G, swap 8G | none for now (site.yml doesn't care) | next fresh install validates tweed-vg naming |
| A1-8 | No interactive console fallback: preseed locks the ansible password (`!`) and disables root login — when SSH is down the serial console cannot log in; recovery requires GRUB-edit `init=/bin/bash`. Fine for security but operationally one-way. | preseed user-setup section | GRUB serial edit if needed | consider a vaulted console password for the ansible user or a documented rescue flow |

| A1-10 | Reinstall changes host keys, but ten64's gdoc2netcfg-generated `/etc/ssh/ssh_known_hosts` still pins the old system's key (line 202) — StrictHostKeyChecking=accept-new cannot override a *changed* key, so SSH (and ansible) fail until the pinned entry is refreshed. | ssh HOST IDENTIFICATION CHANGED, offending /etc/ssh/ssh_known_hosts:202 | per-invocation GlobalKnownHostsFile override; local ~/.ssh entry re-learned | after a reinstall, re-run `gdoc2netcfg known-hosts --force`; both scan attempts (17:47, 18:10) aborted on `sqlite3 database is locked` — the `gdoc2netcfg reachability publish --daemon` process holds discovery.db permanently, so `known-hosts --force` can never run while it's up (gdoc2netcfg concurrency bug, Tim's backlog). Mitigated systematically: ansible now uses its own known_hosts via ansible/ssh.cfg (repo change staged) |

### site.yml-phase issues

(pending)

| B1-1 | site.yml run 1 failed at `switch-vlans: install fpgas-switch-setup into its venv`: ansible's pip module imports `packaging` on the target python; fresh minimal trixie has no `python3-packaging`. **CI gap: the VM test's Debian genericcloud image ships python3-packaging preinstalled — base-package parity differs from a preseeded minimal install.** ok=18 before failure; netif/firewall/vlan-ports converged. | site-run1.log | add python3-packaging to switch-vlans venv prerequisites (repo fix, applied in tweed-rebuild worktree = deviation from pinned 1db0b5b — PR to follow) | CI fix candidates: install the VM from the same d-i preseed instead of cloud image, or strip/assert the extra base packages in the test VM before converge |

| B1-2 | site.yml run 2 failed rendering switches.yml: vault decryption error. Three vault-password paths exist in the ecosystem: README/ansible.cfg document `~/.config/fpgas-online/vault-pass`, an old `~/.config/welland-ansible/vault-pass` (Jun 15) does NOT decrypt these blocks, and the actual encryption password (per session-6d4a transcript: `PW=~/.ansible/pw_file.txt` fed to encrypt_string) lives at `~/.ansible/pw_file.txt`. My symlink pointed the documented path at the WRONG file. CI gap: CI runs with no vault at all (test inventory has no vaulted vars), so password/path drift is untestable there — it only bites on production runs. | run2 log; transcripts | repoint ~/.config/fpgas-online/vault-pass -> ~/.ansible/pw_file.txt after decrypt test | consolidate to ONE documented path; consider a preflight `ansible-vault view` smoke-check task or a `site.yml` assert that vault vars decrypt before converging |

| B1-3 | Run 3: B1-2 vault fix verified (switches.yml rendered), then `switch-vlans: converge switch VLAN config` failed on BOTH switches: fpgas-switch-setup's SNMP transport execs `snmpbulkwalk` — the `snmp` (net-snmp CLI) package is not installed on a fresh system. **CI gap: the VM test sets `switches_manage: false`, so the converge step (the only consumer of net-snmp) never runs in CI.** Also noticed: the converge task's loop output prints `snmp_rw_community` values into the log — wants `no_log`/loop label for the PR. | site-run3.log 18:08 | added `snmp` to the role's prerequisite packages | CI fix candidate: a `--dry-run`/read-only smoke invocation of fpgas-switch-setup in CI (against a mock or with graceful no-switch handling) so runtime deps are exercised even when converge is skipped |

| B1-4 (perf) | profile_tasks (run 4): `vlan-ports: create vlan netdevs` = **4m54s** for 148 idempotent items (~2 s per SSH round-trip); `create vlan networks` similar. Two of these walks per run ≈ 10 min of wall-clock even with zero changes. | run4 log timing lines | enabled `pipelining = True` in ansible.cfg (hetzner-ansible parity) for future runs | if still slow: collapse the per-port files into one templated task/assemble; CI would see the same win |

**Run 4 milestones:** B1-3 fix verified — `converge switch VLAN config` ran against
the real switches: switch 1 (GSM7252PS-s2) `changed` (first live apply of the
per-port VLAN scheme), switch 2 (S3300) already `ok`; BMC and both mgmt IPs
reachable afterwards. Follow-up: diff switch 1's resulting config vs expectations
during verify phase (what exactly changed?).

| B1-5 | Run 4 died at `img: extract files from raspios.img.xz`: `xz: command not found` — minimal trixie ships no xz-utils; CI's genericcloud image has it. Same base-package parity class as B1-1. | site-run4.log rc=127 | add xz-utils to the img role's package task | same CI fix as B1-1: install the CI VM from the preseed, or assert/strip base-package deltas |

| B1-6 | Run 5: converge on switch 1 (GSM7252PS-s2) fails in the fpgas.online-poe library: `set_vlan_membership(2201, port, force=True)` → SnmpError "VLAN 2201 does not exist" — converge applies memberships without creating missing VLANs first (ordering bug), and run 4's `changed` apply may have pruned switch-2's 22xx VLANs from the GSM (downstream trunk port 50) as not-in-spec. CONFIRMED: FDB showed VLANs 2201-2248 live on the GSM at 17:32; after run 4's converge the switch has only 21/121/2101-2140 (ngsw dump 18:40, gsm-vlans-now.txt) — the apply pruned all 48 of switch 2's VLANs, breaking the port-50 trunk path for s3300 Pis (masked right now since Pis are down mid-rebuild). Run 5's own diff then wants those memberships back → self-contradictory diff + membership-before-create ordering. CI gap: converge never runs in CI (switches_manage=false) so library-vs-real-switch behaviour is fully untested. | run5 traceback; FDB 17:32 vs ngsw 18:40 | fix in fpgas.online-poe switch_setup (create VLANs before memberships; treat downstream-trunk VLANs as owned, never prune them) | fix belongs in fpgas.online-poe (create-before-membership + never prune other switches' VLAN ranges); CI needs a mock-switch converge test |

| B1-7 | Run 6 failed identically to run 5: the merged poe fix (PR #5) never reached tweed — `pip state: latest` with a direct git URL and unbumped package version reports `ok` (1.57 s) without reinstalling; installed plan.py had zero `transit-` code. CI gap: fresh VMs never exercise the upgrade path, so stale-install semantics are invisible. | run6 pip task ok + ssh grep | role task → `state: forcereinstall` (or pin commit SHA); manual force-reinstall applied for run 7 | consider versioning discipline for poe (setuptools-scm/git describe like the deb pipeline) so `latest` regains meaning |

**Run 7 landmark: server host fully converged — ok=206 changed=126 failed=0**
(netif→firewall→vlan-ports→switch-vlans (B1-6 fix verified on hardware, 48 transit
VLANs created)→nfs→img (B1-5 xz fix verified)→fixpi→pxe→entire web tier incl.
Django). Timing №1: `cam/pi apt update/upgrade` ≈ 80 min (multi-kernel initramfs
under qemu). Failure moved into the Pi play:

| B1-8 | `cam/pi: Install fpgas-online-cam` — "No package matching 'fpgas-online-cam' is available": the fpgas.online apt repo's bookworm suite serves ONLY fpgas-online-tt + tt-demos (armhf+arm64); cam (and to-verify setup-pi) debs were never ingested by the publish→pull pipeline. CI gap: the VM test skips BOTH `cam` and `fpgas-apt` tags, so repo content coverage is never exercised. | curl dists/bookworm Packages | TBD: publish cam/setup-pi debs (source-repo CI) + apt repo pull | CI candidate: a repo-content assertion (expected package list per suite) that runs even when installs are skipped |
| B1-9 | Latent, found while fixing B1-8: cam/setup-pi still use the OLD tag-gated deb workflow (April 2026, publish step never ran — no v* tag ever pushed), never migrated to the tt rolling-`debs` scheme. Worse, setup-pi's nfpm.yaml names the package `fpgas-setup-pi` (role installs `fpgas-online-setup-pi`) and builds `arch: arm64` while the Pi chroot is armhf — it could never have installed even if published. | repo contents + workflow runs | port tt's build-deb.yml + deb-version.py to both repos; fix nfpm name/arch (→ all); series tag + dispatch + apt pull | publishing-pipeline coverage: CI should assert every role-installed package exists in the repo for the target arch |

B1-8/B1-9 RESOLVED 22:0x: cam PR #1 + setup-pi PR #8 merged (Lint green), rolling
build-deb published both debs to their v0.0 releases, apt pull-debs run 32846822418
ingested them — bookworm suite now serves fpgas-online-cam, fpgas-online-setup-pi,
fpgas-online-tt, fpgas-online-tt-demos. Run 8 launched as the full-loop validation.

| B1-10 | Runs 8 & 9 died UNREACHABLE at random tty-needing tasks: "PTY allocation request failed on channel 0". NOT SSH flakiness — tweed's sshd logs `openpty: No such device`, /dev/pts empty, pty.nr=0: **the host's devpts got unmounted**, almost certainly by Pi-chroot/nspawn cleanup recursively unmounting through a /dev/pts bind. Non-tty SSH kept working, so it looked transient. CI gap: the VM test runs site.yml ONCE — mount-teardown damage only shows on the NEXT run against the same host. | tweed sshd journal; sysctl kernel.pty.nr=0 | remounted devpts; find + fix the umount in chroot-mount-pi-fs.bash / nspawn stop (--make-rslave or targeted umounts); ansible.cfg retries=3 kept | CI candidate: converge twice in the VM test (idempotency pass) — would catch host-damaging teardowns |

| B1-11 | Run 10: server fully green again (ok=189/failed=0, devpts intact — B1-10 fix verified); cam installed (B1-8 verified); pi play failed at `Install fpgas-online-setup-pi`, two-layer packaging bug: (a) build computed VERSION=0.0.post50 but nfpm produced `0.0.0~rc0` — nfpm does not expand the `${VERSION:-default}` shell syntax (likely empty → 0.0.0 + a `prerelease: rc0` field); cam probably identical; (b) the deb ships `/etc/issue`, which dpkg refuses to overwrite from base-files ("trying to overwrite '/etc/issue'"). | run10 log; build-deb run log | fix nfpm version to plain `${VERSION}`; move /etc/issue delivery to postinstall (or issue.d) | packaging never install-tested: CI candidate = `piuparts`-style install test of built debs in the build workflow |

**RUN 11, 23:17: `site.yml` CONVERGED END-TO-END — zero failures.** Pi play
ok=35 changed=15 failed=0 through the nspawn stop; fpgas-online-cam and
fpgas-online-setup-pi 0.0.post* installed from the apt repo (B1-11 verified).
Every issue A1-1…B1-11 is now fixed and hardware-verified. Converge phase done;
verify phase begins (verify-server.yml → Pi PXE boot → verify-pi.yml → live site).

### Verify-phase issues

| D1-1 | verify-pi.yml hardcodes the expected Pi address `10.21.1.1` — correct only for CI's sw1p1 virtual Pi; fails on every real Pi at any other port (found running it against pi-sw2-p33). CI gap by construction: the VM test only ever HAS sw1p1. | verify-pi run vs pi-sw2-p33 | assertion now derives 10.21.S.P from the pi-sw<S>-p<P> hostname | class: CI fixture values leaking into production checks — audit verify playbooks for other hardcoded fixture literals |
| D1-2 | Second fixture literal in verify-pi: hostname assertion hardcoded `pi-sw1-p1` — replaced with the per-port naming-scheme pattern (name↔port consistency already covered by the derived-address assert). | verify-pi run 2 | fixed | — |

**2026-08-26 01:05: `verify-pi.yml` FULL GREEN on real hardware — ok=21 failed=0
skipped=0, including hw-camera and hw-fpga assertions.** Debug artifacts cleaned
(init symlink, pristine initramfs restored both sides, breadcrumb scripts, boot
report unit, tweed listener/capture files); final production-true cycle next.

## VERIFY PHASE COMPLETE — 2026-08-26 01:25

- Production-true Pi boot clean (no debug artifacts); designs API 200.
- verify-server.yml: **ok=104 changed=0 failed=0** (full production checks incl.
  designs API against live TT hardware).
- verify-pi.yml on real pi-sw2-p33: **ok=21 failed=0 skipped=0** (hw-camera +
  hw-fpga included).
- Fleet: 13/15 documented Pis leased+pinging after staggered migration; p43/p44
  re-cycled (recheck pending). Undocumented powered s3300 ports left untouched
  for Tim: 29, 37-39, 41.
- Backlog for Tim: gdoc2netcfg known-hosts scan blocked by its reachability
  daemon's DB lock; ngsw MCP inventory lacks write communities (session TOML
  used); **p43/p44 stayed dark after two PoE cycles each — physical check
  needed (dead SD-less units, unplugged, or not Pis at all)**; Pi NTP unsynced
  and fpgas-cam stuck "activating" — being diagnosed (C2).

| C2-1 | fpgas-cam restart-loops with 203/EXEC: fpgas-cam.service ExecStart references `/usr/local/bin/gst-libcam.sh` but the deb installs the renamed `fpgas-gst-libcam.sh` — self-mismatch inside the cam repo's packaging (unit not updated when contents got the fpgas- prefix). CI gap: cam tag skipped AND no service-start smoke test of built debs. | systemctl status on pi-sw2-p33 | fix cam.service paths in fpgas.online-cam, republish, re-run pi play | build-workflow candidate: install the deb in a container and assert `systemd-analyze verify` / ExecStart paths exist |
| C2-2 | Pi clocks stuck (May 2026, fake-hwclock era): timesyncd active but unsynced — Pis have no internet (forward policy drop, by design) and the new tweed offers no LAN NTP (old tweed ran chrony, configured out-of-band — never captured in the roles). TLS/anything time-sensitive on Pis will misbehave. | timedatectl on Pi | add chrony (LAN-allowed) to a server role + dnsmasq dhcp-option ntp-server | out-of-band config the rebuild surfaced — exactly the class this exercise hunts |

**C2 CLOSED 2026-08-26 ~02:05 (clean-boot validation, cycle 15): fpgas-cam active
(0.0.post36 with the fixed ExecStart), fpgas-tt active, NTP synced against tweed's
chrony (clock real again), designs API 200. Lesson recorded en route: a running
overlayroot Pi does NOT see lower-fs changes made server-side — package/config
updates to the NFS root require a Pi reboot to take effect (relevant to fleet
update procedures).**

CI robustness note (PR #25): both VM runs flaked identically in
`ansible-galaxy collection install` — a galaxy.ansible.com API cache bug
(KeyError 'results') — before any playbook ran. The test harness re-downloads
collections from Galaxy on every run; vendoring/caching them would remove an
external SPOF from CI.

| C1-1 | verify-server: zero v* per-port VLAN interfaces on tweed — the vlan-ports files were on disk all along, but `networkctl reload` is a HANDLER and run 1 failed later in the play (handlers eaten); runs 2-11 saw unchanged files and never re-notified. networkd never reloaded → eth-local unmanaged, no VLANs. CI gap: the VM converge succeeds first try, so the failed-play-eats-handlers path never occurs there; verify-server DID catch it. | networkctl status "Network File: n/a" | manual reload (88 v-ifaces up, verified); vlan-ports role now ends `meta: flush_handlers` | systemic: any notify-based reload can be eaten by a later failure — flush_handlers after config-writing roles; CI candidate = converge-fail-then-reconverge test |
| C1-2 | verify-server: designs API 500 {"error":"internal error","detail":"PermissionError"} — NOT the server: the string lives in fpgas_tt/server.py (the PI daemon); a zombie pre-rebuild Pi at 10.21.2.33 still runs on tmpfs overlay with its NFS backend gone and errors on any disk access. www-data connects to :8765 fine; Django just relayed the half-dead daemon's error. Fix = PoE-cycle the Pi into the new NFS root (next phase anyway). WATCH: /srv/www/pib not g+w, db.sqlite3 videoteam:videoteam 644, .secret_key root:root 600 — perms landmines for later write traffic. | ssh probes; deployed-venv grep | PoE-cycle fpga-1 (s3300 port 33) | verify contract "200 or 502" can't express "daemon up, backend dead" |

| C1-3 | Pi boots nondeterministic after the first (panic-hang loops, dark boots, half-booted with sshd refused): the TFTP boot dir served **kernel8.img 6.6.31 (July 2024) with the regenerated 6.12.96 initramfs8** — fixpi only copied the initramfs across, while the chroot's raspi-firmware hook had produced a fully matched payload in root/boot/firmware that nothing synced. CI gap: the virtual Pi boots ONCE right after converge with whatever pair exists — a mismatch that boots sometimes passes; no kernel-upgrade-then-reboot path in CI. Debug detour: netconsole's dynamic form never transmits (dead cmdline feature); planted a sysinit journal-streamer unit in the NFS root (useful pattern for future Pi debugging). | byte-compare: boot/kernel8.img 9,276,375 = vmlinuz-6.6.31-v8 vs firmware/kernel8.img 9,998,468 = vmlinuz-6.12.96-v8 | rsync firmware payload into boot dir (hotfix); fixpi role now syncs the whole payload instead of initramfs-only | CI candidate: assert kernel8.img and initramfs8 module versions MATCH in verify-server |
| C1-3b | Matched pair boots further (6.12.96 kernel runs, netconsole works, NFS root mounts — 2 live conns) but EVERY boot tonight stalls inside the initramfs before switch-root: systemd never runs (proof: a correctly-planted sysinit-stage unit never executed on any boot), zero ports, silent kmsg after 15 s. Suspect overlayroot/run-init stage. CI's virtual Pi passes this exact path — real-hw delta. Debug: injected /dev/kmsg breadcrumb scripts (init-premount/nfs-premount/init-bottom) + initramfs regen so the next boot narrates its initramfs progress over netconsole. | planted unit unused; port sweeps empty | in progress | initramfs stage visibility is a permanent gap — consider keeping a (quiet) kmsg breadcrumb hook in the role |
| C1-3c | Bisection so far: `nfsvers=3,tcp` fixed the NFS mount hang (trixie nfsd serves v3 TCP-only; klibc nfsmount defaults UDP; CI's bookworm VM masks this — **cmdline + template fixed**). Boot now reaches init-bottom (~195 s — a systematic ~180 s crawl inside the mount stage, residual issue). But PID1 never runs: a /sbin/init breadcrumb wrapper never executed, overlayroot exonerated (identical stall with overlayroot=disabled), NFS session torn down post-init-bottom. Failure window = post-init-bottom scripts → run-init. Next: instrumented /init inside the initramfs itself. | breadcrumbs; wrapper silent across boots | in progress | the 180 s crawl and this stall are both invisible in CI (QEMU Pi passes) — real-hw initramfs behavior needs its own validation step |
| C1-3d | Instrumented /init: `about to run-init` fires 4× at ~191 s = ALL init candidates fail = ${rootmnt} is empty — **the NFS root mount never succeeds at all**; the ~180 s "crawl" is the mountroot retry window expiring, then init-bottom runs on an empty root and the boot panics. The `tcp` flag didn't cure it (suspect: klibc nfsmount option spelling `nfsvers=3` / silent UDP fallback). Debug install: tcpdump added to tweed. Next: capture the mount-window NFS RPC exchange to name the failing call. | 4× run-init crumbs; no NFS root in init-bottom mount table | in progress | — |
| C1-4 | **THE Pi-boot root cause**: rpc.mountd refuses every MNT — "unmatched host" — because the kernel export table is EMPTY: /etc/exports was written by the nfs role but `exportfs -ra` was an end-of-play HANDLER eaten by run 1's failure; runs 2-11 never re-notified (identical class to C1-1 networkd-reload). The 180 s "mount crawl" and every initramfs symptom cascade from this. tcpdump: 3620 portmap/mountd retry packets, 0 on 2049. CI gap: converge-succeeds-first-try masks all eaten-handler bugs. | mountd journal; exportfs -v empty | `exportfs -ra` hotfix; nfs role needs flush_handlers (same as vlan-ports) | pattern now systemic across ≥2 roles → add flush_handlers to every config-writing role + the converge-fail-reconverge CI test |

**2026-08-26 ~00:55 THE PI BOOTS END-TO-END (cycle 13): exports loaded → NFS mounts
in seconds → initramfs → overlayroot → systemd → ssh/fpgas-tt/fpgas-cam active,
overlay mounted, kernel 6.12.96-v8 — and the designs API serves 200 with real
design JSON through nginx→Django→Pi-daemon→TT board. C1-2 CLOSED. The full Pi-boot
causal chain was: kernel/initramfs mismatch (C1-3) → nfsd v3-TCP-only vs klibc UDP
default (C1-3c) → EMPTY export table from eaten handler (C1-4). Note: Pi NTP
unsynced (no RTC) — check time sync against tweed. Debug artifacts to clean before
final validation: /sbin/init wrapper, instrumented initramfs, boot-report unit,
ncl/nfscap units, Pi authorized_keys addition (ephemeral).**

**23:37 FIRST REAL PI BOOT off the rebuilt server: PoE-cycled fpga-1 (s3300 p33) →
DHCPDISCOVER tagged on v2233 → DHCPACK 10.21.2.33 pi-sw2-p33 → TFTP → NFS root →
Pi pings. The per-port VLAN scheme verified on hardware end-to-end.**

(pending)
