# What is in the NFS root

**You operate the fleet and want to know what the shared NFS root contains and which role puts each thing there, so that you can tell where a boot problem or a surprise on a Pi comes from.** How the root gets onto a gateway is on [Updating the NFS root](update-root.md); the boot chain is on [Netboot and the NFS root](../netboot.md). Everything here is from fpgas.online-infra, main, read 2026-10-07 (`ansible/roles/fixpi/tasks/`, `ansible/inventory/group_vars/all/srv.yml`), unless a line says "(from the earlier docs page, not re-checked)".

<a id="the-pinned-image"></a>
## The pinned image

The root is built from a pinned Raspberry Pi OS image, `2024-07-04-raspios-bookworm-armhf-lite`, downloaded from `downloads.raspberrypi.org`, so a rebuild is reproducible rather than "whatever is current today". `group_vars/all/srv.yml` pins it (`dir_date: 2024-07-04`, `dist: bookworm`). That CI still builds from this pin is from the earlier docs page, not re-checked.

## What `fixpi` does to the tree

`fixpi` applies everything that makes the stock image a netboot root. It runs in the CI image build (`fixpi_image_build`) and, for the site layer, on the gateway. In rough order:

- **Boot files.** Saves the stock `cmdline.txt`, `user-data` and `fstab` aside as `.org`, then templates the netboot versions over them.
- **Radios off.** Appends to `config.txt`: `dtoverlay=disable-wifi` and `dtoverlay=disable-bt` to turn the onboard radios off, and removes `enable_uart=1` and `uart_2ndstage=1`, so the firmware sends nothing to the header UART pins (#261). The two overlay lines cover both generations, because on a Pi 5 (BCM2712) the firmware auto-remaps them to `disable-wifi-pi5` and `disable-bt-pi5` via `overlay_map.dtb`. On a Pi 4 they disable `&mmc`/`&mmcnr` and `&bt`; on a Pi 5, `&sdio2` (so WLAN never enumerates) and `&bluetooth`. Since `config.txt` is served read-only over TFTP, these are re-applied on every boot: a root user can bring a radio up for the life of a session, but the change never survives a reboot.
- **The `pi` user.** Creates it directly, with a `sudoers.d` drop-in (`010_pi-nopasswd`) granting passwordless sudo. On stock Raspberry Pi OS that comes from the first-boot `userconf` mechanism, which this root never runs; without the drop-in, Ansible privilege escalation over SSH times out and every Pi reports UNREACHABLE.
- **No first-boot wizard.** Masks `userconfig.service`. Because `/boot/firmware` is mounted `noauto`, the wizard never sees its config file, finds no answers, and drops into an interactive setup that blocks `multi-user.target` forever. This is not a QEMU artefact: a real headless Pi hangs on it identically. The same `noauto` is why `ssh.service` is enabled directly in the root rather than left to `sshswitch.service`, which looks for a flag file it can never see.
- **Packages in the chroot.** Runs `apt update` and `apt install -y nfs-common overlayroot` through `chroot-mount-pi-fs.bash`, a helper that does the whole bind-mount-and-chroot inside a private mount namespace. The private namespace is deliberate: with plain binds under systemd's shared `/`, the cleanup unmount propagated back and unmounted the *host's* `devpts`, leaving the gateway unable to allocate ptys until it was remounted (tweed rebuild, 2026-08-25, entry B1-10 in `docs/rebuilds/2026-08-25-tweed-rebuild.md`).
- **Hostname in `/etc/hosts`.** Installs a small unit (`fpgas-hostname-hosts.service`) that puts the Pi's own DHCP-assigned hostname into `/etc/hosts`, so sudo's per-invocation name lookup is instant instead of stalling on DNS and tripping Ansible's escalation timeout (from the earlier docs page, not re-checked).
- **Time.** Points the Pi's `timesyncd` at the gateway (`timesyncd.conf.d/fpgas.conf`), matching the DHCP NTP option.
- **Firmware payload.** Syncs the whole firmware payload from `root/boot/firmware/` into `boot/`, excluding the templated `cmdline.txt`, `cmdline-pi5.txt` and `config.txt`.
- **SSH host keys.** Pre-generates them on the gateway, and disables `regenerate_ssh_host_keys.service`, which would otherwise delete and rebuild them on first boot: minutes per key under QEMU emulation, with sshd blocked the whole time. That CI skips the pre-generation, so that a published image does not ship keys shared by every site that consumes it, is from the earlier docs page, not re-checked.
- **No resizing or swap.** `nogrow.yml` diverts `rpi-resize.service`'s helper link, the zram swap generator config and `rpi-swap-generator` with `dpkg-divert`; masks `systemd-growfs@`, `systemd-growfs-root`, `rpi-remove-swap-file@`, `rpi-resize-swap-file`, `swap.target`, `rpi-resize` and `systemd-networkd-persistent-storage`; and removes the `dphys-swapfile` enable link. None of it makes sense on a read-only NFS root.

> [!NOTE]
> Sync the *whole* firmware payload, not just the initramfs. Historically `fixpi` copied only `initramfs8` across, so after a kernel upgrade inside the chroot the TFTP boot directory served `kernel8.img` 6.6.31 with a 6.12.96 initramfs. The result was nondeterministic: panic-hangs, boot loops, and half-booted Pis with sshd never coming up. Found and fixed during the tweed rebuild on 2026-08-25 (entry C1-3 of the rebuild record).

## Packages come last

`fpgas_apt` adds the fpgas.online APT repositories (see [Packages](https://docs.fpgas.online/en/latest/packages.html)), then `cam_pi` and `onpi` install the camera and Pi packages, all inside the root, none on a running Pi (`ansible/ci-nfsroot.yml`, "Install the Pi roles into the NFS root").

<a id="the-ci-inventory"></a>
## The CI build

CI builds the root on an arm64 runner (`ansible/ci-nfsroot.yml`, with `ci-nfsroot-base.yml`, `-runner.yml` and `-upgrade.yml`, against the inventory `ansible/inventory-ci-nfsroot`), over Ansible's `community.general.chroot` connection, and publishes it as an OCI image. Its first stage
(`ansible/ci-nfsroot-base.yml`) runs the `img` role's `tasks/build.yml`: it installs `xz-utils` (a minimal
Debian install has none: rebuild record B1-5), downloads the pinned Raspberry Pi OS image, and extracts it
with `files/img2files.sh` (`xz -dk`, `losetup --partscan`, mount both partitions, rsync them into the root).
Then it runs `fixpi` on the build host, then `fpgas_apt`, `cam_pi` and `onpi` inside the root (`ansible/ci-nfsroot.yml`, main, read 2026-10-07). The gateway no longer runs those roles: it pulls the image, and the pull leaves the site's `authorized_keys` files and SSH host keys alone (the rsync excludes in `roles/img/tasks/pull.yml`). `fixpi` then applies the per-site layer.
