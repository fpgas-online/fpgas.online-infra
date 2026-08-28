# Runbook: Orange Pi H3 boards on the Welland rack

Design: `docs/superpowers/specs/2026-08-28-orange-pi-netboot-design.md`.
Mapping: `docs/hardware/2026-08-28-orange-pi-h3-boards.md`.

## How it works (one paragraph)

The boards have no SD/eMMC, so on power-up the Allwinner BROM waits in USB FEL
mode. Their OTG cables go to the hub host **pi-sw2-p30**; its
`fpgas-online-setup-pi` package ships a udev rule that starts
`fpgas-felboot@<usb-device>.service`, which loads U-Boot with `sunxi-fel`.
U-Boot's distro-boot DHCPs (the per-port VLAN scheme hands it
`pi-sw2-p<port>` / `10.21.2.<port>`), fetches
`pxelinux.cfg/default-arm-sunxi` + `sunxi/{vmlinuz,initrd.img,dtbs/…}` from
tweed's TFTP root, and boots the **shared** Pi NFS root with the Debian
`armmp` kernel that `fixpi/tasks/sunxi.yml` bakes into it.

## Deploy / re-converge (tweed)

```bash
cd ~/github/fpgas-online/fpgas.online-infra          # main, or the PR worktree
uv run ansible-playbook -i ansible/inventory ansible/site.yml \
  --limit fpgas.online,pi \
  --tags fixpi,netboot,sunxi,sunxi-kernel,onpi,fpgas-apt
```

* `--limit` must include `pi` (the nspawn provisioning host) or the NFS root
  is not touched.
* The first run installs the kernel into the root: ~30 min under qemu
  (`update-initramfs`). Later runs skip it (`creates:` guard); kernel
  upgrades arrive through `onpi`'s apt upgrade like every other package.
* Check afterwards on tweed:
  `ls /srv/nfs/rpi/bookworm/boot/sunxi /srv/nfs/rpi/bookworm/boot/pxelinux.cfg`
  and `chroot /srv/nfs/rpi/bookworm/root dpkg -l fpgas-online-setup-pi sunxi-tools`.
* Running Pis do not see NFS-root changes until they reboot — worse, files the
  converge replaced become `Stale file handle` on a running host and udev drops
  the felboot rule for hot-plugged boards. **Always PoE-cycle pi-sw2-p30 (port 30)
  after a converge**, then the boards.

## Verify

```bash
uv run ansible-playbook -i 10.21.2.20,10.21.2.21,10.21.2.23,10.21.2.24,10.21.2.30, \
  ansible/verify-pi.yml -u pi -e verify_pi_hosts=all --skip-tags hw-camera,hw-fpga
```

`hw-sunxi` asserts each Orange Pi runs an `armmp` kernel on `armv7l` and that
the hub host has a `fpgas-felboot@<usb>` instance for every board declared
for it in `sunxi_boards`.

## Recovery

* Board unreachable → PoE-cycle its switch port
  (`ngsw --config ~/.config/ngsw/inventory.toml --switch s3300-1 --write-community public poe <port> off -y --force`, then `on`).
  It re-enumerates in FEL on the hub host within ~3 s and is FEL-booted
  automatically. `journalctl -u 'fpgas-felboot@*'` on pi-sw2-p30 shows the
  attempts.
* Hub host unreachable → PoE-cycle port 30; every board re-enumerates when the
  hub host comes back and is booted then.
* No early console: `netconsole=` is inert on sunxi (the ethernet driver is an
  initramfs module). The H3's UART0 3-pin header at 115200 8N1 is the only way
  to see U-Boot/kernel output; the PL2303 on the hub is not wired to any board.

## Adding a board

1. Cable it: PoE port on s3300-1, OTG micro-USB to the hub host.
2. On the hub host: `sudo sunxi-fel --list` shows the new device with its SID;
   `ls -l /sys/bus/usb/devices/ | grep <busnum>-` tells you its USB path.
   The felboot service will already have booted it — read its MAC from the
   switch (`ngsw … --json macs`, VLAN 22xx of its port).
3. Add it to `sunxi_boards` in `ansible/inventory/host_vars/fpgas.online.yml`
   (`switch, port, host, usb, model, mac`) and to the inventory sheet tool
   (`welland-ansible-rpi` `tools/rpi_hardware_sheet.py`: `FPGAS_PORT_MAC` +
   a `KNOWN_BOARDS` entry) so the RPi Hardware sheet names it.
4. A different sunxi board model needs its own U-Boot build (vendor it in
   `fpgas.online-setup-pi/felboot/u-boot/`) and possibly a per-MAC
   `pxelinux.cfg/01-<mac>` with its DTB — U-Boot looks for that file first.

## Known issue

* **pi-sw2-p21** sometimes crawls on its first boot after a power cycle and
  never reaches sshd (see the hardware doc). A second PoE cycle fixes it.
  Until that board has a serial console attached, treat "p21 not up after
  5 min" as "cycle it again", not as an infrastructure fault.

## Hub host pi-sw2-p30 boots from SD (2026-08-28 evening)

The hub host no longer runs the shared NFS root. Its 8 GB microSD carries the
`welland-ansible-rpi` **fleet-bootstrap-arm64** image (Raspberry Pi OS trixie,
cloud-init users `tim`/`ansible`, hostname `rpi5-new-13f59c`) and the Pi 5
EEPROM is `BOOT_ORDER=0xf21` — **SD first, netboot only if the card fails**
(verified: with the card the bootloader fetches nothing from tweed; with
netboot deliberately broken the card booted and both users logged in).
tweed still hands it `10.21.2.30` / `pi-sw2-p30` on the per-port VLAN.

Hand-configured on that OS, to be captured by `welland-ansible-rpi` when the
host is enrolled there (it is a fleet host now, not an NFS-root Pi):

* apt source `https://fpgas.online/apt trixie main` (key in
  `/usr/share/keyrings/fpgas-online.gpg`) and `fpgas-online-setup-pi` +
  `sunxi-tools` installed — this is what FEL-boots the Orange Pis
  (`fpgas-felboot@<usb>.service`; proven on this OS: a PoE-cycled board was
  back in 72 s).
* NM profile `netplan-eth0`: `ipv4.never-default yes`, `ipv6.never-default yes`
  — eth0 (tweed's VLAN) has no internet; wlan0 (`ansells-iot`) carries the
  default route.
* Consequence for this runbook: `verify-pi.yml` no longer applies to p30 (no
  `pi` user, not the NFS root); the `hw-sunxi` hub-host check must target it
  as `tim`/`ansible` or move to the fleet repo.

Changing the EEPROM on a *netbooted* Pi 5: the bootloader looks for
`pieeprom.sig`/`pieeprom.upd` at the TFTP **root** (not in its serial dir), so
use a per-interface `tftp-root=<dir>,v22NN` in dnsmasq to serve the update to
one Pi only. The same trick with an empty dir forces one Pi's netboot to fail
(SD-fallback test). On the SD OS `rpi-eeprom-config --apply` flashes directly.
