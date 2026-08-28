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
* Running Pis do not see NFS-root changes until they reboot: PoE-cycle
  pi-sw2-p30 (port 30) so it picks up the felboot package, then the boards.

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
