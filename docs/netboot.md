# Netboot and the NFS root

**You operate the fleet and want to know how a Pi gets its kernel and its root filesystem from the gateway,
so that you can reason about a boot problem or a change to the root.** To put a new root on a gateway, or to
find out why a Pi does not boot, go to the pages listed under [The tasks](#the-tasks).

A fleet Pi boots from the network, not from an SD card. (The Orange Pis boot the same root by another route, through a hub host: [Orange Pi H3 hosts](https://docs.fpgas.online/en/latest/setup/orange-pi.html).) Its boot ROM asks for DHCP,
fetches its firmware and kernel over TFTP, and mounts one shared, read-only NFS root from the site's gateway,
with a tmpfs on top that is thrown away at every reboot. This page describes what fpgas.online-infra builds
(main, read 2026-10-07). ps1's gateway was not built by it as it stands: [The ps1 gateway and
switch](https://docs.fpgas.online/en/latest/sites/ps1-gateway.html) has what was read there.

## The tasks

<a id="updating-a-running-fleet"></a>
- [Updating the NFS root](netboot/update-root.md): a new root on a gateway, and every Pi on it.

<a id="how-the-root-is-built"></a>
- [How the root is built](netboot/update-root.md#how-the-root-is-built): CI builds it, the gateway pulls it.

<a id="the-provisioning-container"></a>
- [The provisioning container](netboot/update-root.md#how-the-root-is-built): gone. The root used to be built
  on the gateway through a `piroot` account; it is now built in CI, and that account is deleted.

<a id="when-a-pi-does-not-boot"></a>
- [When a Pi does not boot](netboot/not-booting.md): where its boot stops.

- [What is in the NFS root](netboot/root.md): the pinned image, what `fixpi` does to the tree, the CI build.

- [EEPROM write protect](#eeprom-write-protect), below: the lock the served `config.txt` sets. [Raspberry Pi's
  wording and the checks](netboot/eeprom.md).

- [Historical tooling](netboot/history.md): the repositories that came before the roles.

- [Sources](netboot/sources.md): the files and records these pages come from.


## The boot chain

1. The Pi powers on and its boot ROM broadcasts DHCP.
2. dnsmasq on the gateway answers with an address and the gateway as TFTP server. It binds with
   `bind-dynamic`, which starts even when one of its interfaces is missing or down and binds each as it
   appears; with `bind-interfaces` dnsmasq aborts at start in that case (the template's comment,
   `roles/pxe/templates/dnsmasq-base.conf.j2`). welland's gateway had 88 per-port interfaces on 6 October 2026.
3. The Pi fetches its firmware, `config.txt`, `cmdline.txt`, the kernel, the device tree and the initramfs over
   TFTP.
4. The kernel mounts the NFS root read-only and puts a tmpfs over it (`overlayroot=tmpfs`).
5. Everything the Pi runs is already in that root; nothing is installed at boot.

dnsmasq also names the gateway as the NTP server: the Pis have no route to the internet, and without it their
clocks stay on the `fake-hwclock` date (`roles/pxe`; found at the welland gateway's rebuild of 2026-08-25).

## Where TFTP serves from

`tftp_root` in `inventory/group_vars/all/srv.yml`:

```jinja
tftp_root: "{{ (nfs_root ~ '/boot') if switches is defined else '/srv/tftp' }}"
```

At welland (`switches` defined) dnsmasq serves the root's own `boot/` directly. A Pi 4 or 5 asks for
`<serial>/<file>` first and, when that is not there, asks again without the prefix (Raspberry Pi's bootloader
setting `TFTP_PREFIX`, as that file's comment quotes it), so no Pi is registered by serial number: plug it into
its port and it boots.

On a MAC-table gateway, `/srv/tftp` holds one link per Pi serial number pointing at the root's `boot/`
(`roles/fixpi/tasks/netboot.yml`); adding a Pi means adding its entry to `switch.nos` in the gateway's
`host_vars` and converging, so its link is made. `/srv/tftp` also holds a `bootcode.bin` link at the top, to the
root's `boot/bootcode.bin`, because (the task's comment in `netboot.yml`) "pi netboot not too smart".
Each entry in `switch.nos` is `{port, mac, sn, model, loc, cable_color}` (the shape in `host_vars/ps1.fpgas.online.yml`, main, read 2026-10-07).

## The kernel command line

`roles/fixpi/templates/boot/cmdline.txt.j2`, as served to every Pi but a Pi 5, at welland
(`nfs_root` is `/srv/nfs/rpi/bookworm`). It is one line; here it is split at each setting:

```text
root=/dev/nfs
nfsroot=10.21.0.1:/srv/nfs/rpi/bookworm/root,nfsvers=3,tcp ro
ip=dhcp rootwait consoleblank=0
netconsole=@/,@10.21.0.1/
overlayroot=tmpfs
console=tty1
systemd.log_level=debug systemd.log_target=kmsg log_buf_len=1M printk.devkmsg=on
```

- **`nfsvers=3,tcp`**: the welland gateway's rebuild record (`docs/rebuilds/2026-08-25-tweed-rebuild.md` in fpgas.online-infra,
  entry C1-3c) found its NFS server (Debian 13) serving version 3 over TCP only, while the initramfs's mount
  tool defaults to UDP, so the command line asks for TCP. The root mount still failed after that; the cause
  the record names (C1-4) was an empty export table on the gateway, since fixed in the `nfs` role (fpgas.online-infra commit 7c353ec).
  The bookworm VM that CI uses does not reproduce the UDP hang, so it was found only on real hardware (rebuild
  record, 2026-08-25). The flag is in the shared template for every site, because there is one template.

- **`overlayroot=tmpfs`**: the writable layer ([below](#the-nfs-root-is-shared-and-read-only)).

- **`console=tty1`**: the kernel console is on the screen, not on a serial port, so bytes that an FPGA board sends on the Pi's
  serial pins never reach a console. The root also sets `kernel.sysrq = 0` (`roles/fixpi/tasks/netboot.yml`,
  "FPGA serial must not trigger SysRq").

A Pi 5 boots `cmdline-pi5.txt` instead: the same line with `console=ttyAMA10,115200`, the Pi 5's own debug
UART. `roles/fixpi/tasks/tweeks.yml` adds to the served `config.txt`:

```text
[pi5]
dtoverlay=uart0-pi5
cmdline=cmdline-pi5.txt
```

so the 40-pin header UART (`/dev/ttyAMA0`, wired to the FPGA on an Acorn host) is enabled and free of the
console. The same file adds `dtoverlay=disable-wifi`, `dtoverlay=disable-bt`, `enable_uart=1`,
`uart_2ndstage=1` and `eeprom_write_protect=1`, and puts the Pi 4's and Pi 5's USB-C port in gadget mode
([When a Pi does not boot](netboot/not-booting.md)). `config.txt` is served read-only, so these apply at every
boot.

The PS1 Compute Blades boot a separate trixie root and have `console=tty1` with `serial-getty@ttyAMA0` inactive:
[Compute blades](https://docs.fpgas.online/en/latest/sites/ps1-boards.html) (from the earlier docs page, not re-checked).

## The NFS root is shared and read-only

There is one root per site, at `/srv/nfs/rpi/<dist>/{boot,root}` (`nfs_root`; `dist: bookworm`). Both halves
are exported read-only to the Pi network, and the Pi's own `/etc/fstab` mounts `/` and `/boot/firmware`
read-only and `noauto`. Every Pi mounts the same root.

<a id="the-export-is-read-only"></a>
The export (`roles/nfs/templates/exports.j2`):

```text
{{ nfs_root }}/boot {{ eth_local_address }}/{{ eth_local_netmask }}(ro,sync,no_subtree_check,no_root_squash)
{{ nfs_root }}/root {{ eth_local_address }}/{{ eth_local_netmask }}(ro,sync,no_subtree_check,no_root_squash)
```

The `noauto` on `/boot/firmware` has consequences all over the build: [What is in the NFS root](netboot/root.md).

> [!WARNING]
> The writable layer is a tmpfs. Everything written on a Pi is gone at the next reboot or power cycle,
> including anything copied to `/home/pi`. A bitstream that loaded a minute ago fails to open after a reboot
> because the file is not there any more: openFPGALoader prints `Open file … FAIL`. Copy it again.

Automation has to account for this. The hardware verification script in `fpgas.online-test-designs` power-cycles a
Pi to recover a Fomu whose DFU bootloader has timed out, and after the Pi comes back (roughly two minutes) it
re-uploads every file, because the tmpfs is empty again, and re-runs its pre-test, because the `serial-getty` mask
is lost too (from the earlier docs page, not re-checked).

A Pi that booted before the root was replaced keeps file handles into the old files: every replaced file
answers `Stale file handle` (`ESTALE`). That broke `dpkg-query`, and, since `authorized_keys` was among the
replaced files, key-based SSH, on every board at once (measured on `pi-sw2-p33` at welland on 2026-09-24;
`roles/nfsroot_generation/README.md`). A Pi has to reboot to use a new root: [Updating the NFS
root](netboot/update-root.md) is how that happens.

An earlier case: on 2026-08-30 an upgrade of `fpgas-online-cam` took the cameras off air on eleven boards with
`ESTALE` on the replaced files, and days later the Tiny Tapeout hosts still had `dpkg-query` reporting a stale file
handle. Only a reboot clears it. See [Known
faults](https://docs.fpgas.online/en/latest/sites/welland.html) on the Welland page (from the earlier docs page, not re-checked).

## EEPROM write protect

Everything a user changes on a fleet Pi's root is gone at the next reboot, but not the bootloader EEPROM: the
flash holding the Pi's second-stage bootloader and its settings (`BOOT_ORDER` and the rest). A user with root
could rewrite it with `rpi-eeprom-update`, `rpi-eeprom-config` or `flashrom` and change how the board boots
for good. (A board's own flash, an Acorn's for example, is a separate matter, on its board's pages.) So the
served `config.txt` carries `eeprom_write_protect=1` (`roles/fixpi/tasks/tweeks.yml`), which tells the
bootloader to set the flash's Write Status Register to protect the whole chip, at every boot.

<a id="per-model-effectiveness"></a>
How much that protects depends on the model (the role's own comment, and Raspberry Pi's `config.txt`
documentation, section `eeprom_write_protect`):

- **Raspberry Pi 5:** the flash's `/WP` pin is pulled low by default, so the register setting is enforced by
  the hardware; clearing it needs the `TP14` and `TP1` pads joined.
- **Raspberry Pi 4:** `/WP` (`TP5`) is not pulled low by default, so the setting stops the standard tools,
  but a root user could clear the register; pulling `TP5` low makes it a hardware lock.
- **Compute Module 4:** `/WP` is the module's `EEPROM_nWP` pin (Raspberry Pi's documentation), and what it is
  tied to depends on the carrier board. For a Compute Module 5 no source is recorded here: [Compute
  Module](https://docs.fpgas.online/en/latest/setup/bootloader-eeprom-compute-module.html) has what was measured.

The setting takes effect from the `config.txt` a board boots with: the fleet's served one for a netbooted Pi,
`/boot/firmware/config.txt` on a board that boots its own storage.

Values: `1` protects the whole flash, `0` clears the protection, `-1` (the default) does nothing. How to check
a board and how to upgrade a locked one: [a Raspberry Pi 5](https://docs.fpgas.online/en/latest/setup/bootloader-eeprom-pi5.html), [a Compute
Module](https://docs.fpgas.online/en/latest/setup/bootloader-eeprom-compute-module.html), and [what was measured](https://docs.fpgas.online/en/latest/setup/bootloader-eeprom.html).

<a id="verify"></a>
<a id="legitimately-updating-an-eeprom-later"></a>
Raspberry Pi's wording, the CI check, and when a change takes effect: [The EEPROM lock](netboot/eeprom.md).
