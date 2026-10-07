# Sources for netboot and the NFS root

**You want to check a statement on the netboot pages against the file or record it came from.** The list is from the earlier docs page; every entry was checked to exist on fpgas.online-infra main on 2026-10-07, but what each file is said to show was not re-checked.

## In fpgas.online-infra (main)

- [`README.md`](https://github.com/fpgas-online/fpgas.online-infra/blob/main/README.md): architecture overview, PXE boot chain, package table.
- [`ansible/site.yml`](https://github.com/fpgas-online/fpgas.online-infra/blob/main/ansible/site.yml): role order, and the "Update the Pi NFS root" play (GitHub keys, update lock, `img`, `apt_cache`, `fixpi`, new generation).
- [`ansible/inventory/group_vars/all/srv.yml`](https://github.com/fpgas-online/fpgas.online-infra/blob/main/ansible/inventory/group_vars/all/srv.yml): pinned image name and date, `dist`, `nfs_root`, the `tftp_root` expression.
- [`ansible/inventory/host_vars/ps1.fpgas.online.yml`](https://github.com/fpgas-online/fpgas.online-infra/blob/main/ansible/inventory/host_vars/ps1.fpgas.online.yml): the `switch.nos` list and the shape of its entries.
- [`ansible/roles/pxe/templates/dnsmasq-base.conf.j2`](https://github.com/fpgas-online/fpgas.online-infra/blob/main/ansible/roles/pxe/templates/dnsmasq-base.conf.j2): DHCP/TFTP on the internal NIC, `bind-dynamic`, `log-dhcp`, the pinned absolute `dhcp-leasefile` path, the NTP option.
- [`ansible/roles/nfs/templates/exports.j2`](https://github.com/fpgas-online/fpgas.online-infra/blob/main/ansible/roles/nfs/templates/exports.j2): both exports read-only.
- [`ansible/roles/img/tasks/main.yml`](https://github.com/fpgas-online/fpgas.online-infra/blob/main/ansible/roles/img/tasks/main.yml), [`pull.yml`](https://github.com/fpgas-online/fpgas.online-infra/blob/main/ansible/roles/img/tasks/pull.yml) (the image pull, and the rsync excludes that keep the site's host keys and `authorized_keys` across pulls) and [`files/img2files.sh`](https://github.com/fpgas-online/fpgas.online-infra/blob/main/ansible/roles/img/files/img2files.sh) (vendored, not the deploy path).
- [`ansible/roles/fixpi/tasks/main.yml`](https://github.com/fpgas-online/fpgas.online-infra/blob/main/ansible/roles/fixpi/tasks/main.yml): the task-file order within `fixpi`.
- [`manage.yml`](https://github.com/fpgas-online/fpgas.online-infra/blob/main/ansible/roles/fixpi/tasks/manage.yml): `qemu-user-static`, and the vendored `maintenance.sh` / `production.sh` / `chroot-mount-pi-fs.bash`.
- [`netboot.yml`](https://github.com/fpgas-online/fpgas.online-infra/blob/main/ansible/roles/fixpi/tasks/netboot.yml): TFTP symlinks, cmdline and fstab templating, the `pi` user and its sudo drop-in, `nfs-common` and `overlayroot`, the firmware-payload sync, ssh enablement, the masked wizard, pre-generated host keys.
- [`tweeks.yml`](https://github.com/fpgas-online/fpgas.online-infra/blob/main/ansible/roles/fixpi/tasks/tweeks.yml): `config.txt` edits (onboard Wi-Fi and Bluetooth off, the Pi 5 overlay remapping, `eeprom_write_protect=1`, the `[pi5]` header-UART and `cmdline=` stanza, the Pi 4 / Pi 5 USB gadget console) and the gateway-side serial watch (`brltty` removed, `tio` installed, operator in `dialout`, `serial-getty@ttyAMA0` masked).
- [`nogrow.yml`](https://github.com/fpgas-online/fpgas.online-infra/blob/main/ansible/roles/fixpi/tasks/nogrow.yml): root-resize and swap machinery disabled.
- [`templates/boot/cmdline.txt.j2`](https://github.com/fpgas-online/fpgas.online-infra/blob/main/ansible/roles/fixpi/templates/boot/cmdline.txt.j2) and [`cmdline-pi5.txt.j2`](https://github.com/fpgas-online/fpgas.online-infra/blob/main/ansible/roles/fixpi/templates/boot/cmdline-pi5.txt.j2): the kernel command lines.
- [`templates/etc/fstab.j2`](https://github.com/fpgas-online/fpgas.online-infra/blob/main/ansible/roles/fixpi/templates/etc/fstab.j2): `/` and `/boot/firmware` as `noauto,ro` NFS v3 mounts.
- [`files/scripts/chroot-mount-pi-fs.bash`](https://github.com/fpgas-online/fpgas.online-infra/blob/main/ansible/roles/fixpi/files/scripts/chroot-mount-pi-fs.bash): the private-mount-namespace chroot helper.
- [`ansible/roles/operators/defaults/main.yml`](https://github.com/fpgas-online/fpgas.online-infra/blob/main/ansible/roles/operators/defaults/main.yml): `piroot` and `/usr/local/bin/chroot-shell` as retired.
- [`docs/superpowers/runbooks/2026-08-31-eeprom-write-protect.md`](https://github.com/fpgas-online/fpgas.online-infra/blob/main/docs/superpowers/runbooks/2026-08-31-eeprom-write-protect.md): the EEPROM write-protect section.
- [`docs/rebuilds/2026-08-25-tweed-rebuild.md`](https://github.com/fpgas-online/fpgas.online-infra/blob/main/docs/rebuilds/2026-08-25-tweed-rebuild.md): kernel/initramfs mismatch (C1-3), `nfsvers=3,tcp` (C1-3c), the host devpts unmount (B1-10), Pi clocks with no LAN NTP (C2-2), the two conflicting readings of netconsole (C1-3 versus C1-3b).

## In other repositories

- [`docs/verify-hardware.md`](https://github.com/fpgas-online/fpgas.online-test-designs/blob/main/docs/verify-hardware.md) in fpgas.online-test-designs: PoE reset, the two-minute PXE boot, and re-uploading everything because the tmpfs is empty after a reboot.
- [fpgas.online-netboot-pi](https://github.com/fpgas-online/fpgas.online-netboot-pi): `README.md` for the predecessor scripts, `pinet/cmdline.txt` and `pinet/fstab` for the maintenance-mode configuration, `scripts/maintenance.sh` and `scripts/production.sh` for the mode switch ([Historical tooling](history.md)).
- [fpgas.online-tools](https://github.com/fpgas-online/fpgas.online-tools): `README.md` for the DHCP and netconsole utilities.

## Related pages

[Welland](https://docs.fpgas.online/en/latest/sites/welland.html) (tweed on trixie, Pi 5 console, stale NFS handles), [The ps1 gateway and switch](https://docs.fpgas.online/en/latest/sites/ps1-gateway.html) (one trixie root), [Compute blades](https://docs.fpgas.online/en/latest/sites/ps1-boards.html) (`console=tty1` on the trixie root), [Packages](https://docs.fpgas.online/en/latest/packages.html).
