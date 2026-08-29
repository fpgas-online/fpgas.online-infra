# Orange Pi H3 netboot on the fpgas.online Welland rack — spike results and design

Status: **spike complete, design awaiting Tim's approval** (2026-08-28).
Companion: `docs/hardware/2026-08-28-orange-pi-h3-boards.md` (port/USB/MAC mapping).

## 1. Questions asked, answers found

| Question | Answer | Evidence |
|---|---|---|
| Can the Orange Pis use the **same NFS root** as the Raspberry Pis? | **Yes.** The Raspbian bookworm *armhf* userland runs unchanged on the H3 (ARMv7). Only the kernel + initrd + DTB are board-specific, and they are separate files under the TFTP root. | Board booted a byte-identical copy of `/srv/nfs/rpi/bookworm/root` with Debian's `linux-image-6.1.0-50-armmp`: `Linux pi-sw2-p20 6.1.0-50-armmp armv7l`, root = overlayroot over `10.21.0.1:/srv/nfs/opi-spike/root` (nfs), `fpgas-tt`/`lldpd`/`ssh`/`timesyncd` active, `dpkg --print-architecture` = armhf. |
| Does installing a foreign kernel into that root disturb the Pi fleet? | **No.** Raspbian's `z50-raspi-firmware` postinst hook prints *"Unsupported kernel version (6.1.0-50-armmp) - skipping setup"* and leaves `/boot/firmware` untouched; the Pi kernels/initramfs are untouched. | `dpkg -i` output + before/after listing of `boot/firmware` (identical). |
| What must be added to **tftp/PXE**? | Only files, **no dnsmasq or switch changes**: `pxelinux.cfg/default-arm-sunxi` + a `sunxi/` directory (kernel, initrd, DTBs) in the existing TFTP root. U-Boot's distro-boot does DHCP (the per-port VLAN scheme hands it `10.21.2.20` / `pi-sw2-p20` exactly like a Pi), then fetches `pxelinux.cfg/01-<mac>`, IP-hex names, `default-arm-sunxi`, `default-arm`, `default` from the DHCP server's TFTP. | tweed dnsmasq log 05:29:28–05:29:41: config, `sunxi/vmlinuz` (5.4 MB), `sunxi/initrd.img` (27 MB), `sunxi/dtbs/sun8i-h3-orangepi-pc.dtb` served in 13 s; kernel `ip=dhcp` at 05:29:58; NFS session `ESTAB 10.21.0.1:2049 ← 10.21.2.20`. |
| How do the boards get **into** U-Boot with no SD card? | The H3 BROM falls into USB **FEL** mode on every power-up (they enumerate as `1f3a:efe8`). The hub host runs `sunxi-fel --dev <bus:dev> uboot u-boot-sunxi-with-spl.bin`; ~3 s after a PoE cycle the board is back in FEL and can be re-booted. | `sunxi-fel ver` → `soc=00001680(H3)` on all four; FEL device reappeared 3 s after PoE on; U-Boot 2025.01 (`orangepi_pc_plus` build from Debian `u-boot-sunxi`) ran on every board. |
| Which boards / where? | Four **Orange Pi PC** (H3, 1 GB — `Memory: 992 MB`, so not a 512 MB "One"; PC vs PC Plus needs a physical look) on s3300-1 ports 20/21/23/24, OTG-cabled to pi-sw2-p30's hub at 1-1.2.2 / 1-1.3.1 / 1-1.2.3 / 1-1.2.4. | Mapping doc. |

Things that did **not** work / need attention:

* `netconsole=@/,@10.21.0.1/` on the Pi cmdline is inert on sunxi: `dwmac-sun8i` is a
  module loaded from the initramfs, so built-in netconsole finds no interface. Serial is the
  only early console — and the PL2303 on the hub is not wired to any of the four UART0 headers.
* `ifupdown-pre.service` (`udevadm settle`) waits its full **2 min** on the H3 — the whole
  userspace boot is 2 m 16 s of which everything else is ~14 s. Some udev event never completes
  (`sunxi-mmc` with no card? musb?). Must be found during implementation; a Pi 5 boots userspace
  in 7 s on the same root.
* `networking.service` fails on the OPi **and** on pi-sw2-p30 — pre-existing, not OPi-specific.
* PoE-only power: cutting the switch port kills the board within seconds, the OTG cable does not
  power it. Good: the fleet's existing PoE-cycle recovery works unchanged.

## 2. Recommendation

**Shared NFS root + a second kernel**, not a dedicated Orange Pi root.

* One image pipeline, one `pi` nspawn play, one set of fpgas-online packages, one verify-pi;
  the boards get every fleet change for free. A dedicated root would double the ~100 min apt
  converge and fork every Pi role.
* Cost: one extra kernel package in the root (~250 MB modules, ~30 min qemu-emulated
  `update-initramfs` on first install, guarded by `creates:`), a `sunxi/` TFTP directory and one
  PXE file.

Rejected: Armbian root (second distro to maintain, no fpgas packages), OPi-specific root
(duplication above), per-MAC `pxelinux.cfg/01-*` files (not needed — all four are the same board;
keep as the escape hatch if a different sunxi board ever appears).

## 3. Design (fpgas.online-infra)

All new renders are guarded `when: sunxi_boards is defined` — the multi-host guard rule
(ps1 / slf / CI VM must not change).

1. **Kernel in the NFS root** (`fixpi` role, new `sunxi.yml`, tag `sunxi`):
   * add `deb http://deb.debian.org/debian bookworm main` for arch armhf to the root with an apt
     **pin that allows only `linux-image-*-armmp` + `linux-base`** (priority 500) and everything
     else from that source at −1, so Raspbian stays the source of every other package;
   * `chroot-mount-pi-fs.bash … apt install linux-image-armmp` (creates guard on
     `/boot/vmlinuz-*-armmp`);
   * sync `vmlinuz-*-armmp`, `initrd.img-*-armmp`, `usr/lib/linux-image-*-armmp/sun8i-h3-*.dtb`
     into `{{ tftp_root }}/sunxi/` (same idea as the existing kernel+initramfs sync task).
2. **PXE file** (`fixpi` template `pxelinux.cfg/default-arm-sunxi.j2`): same append line as
   `cmdline.txt.j2` with `console=ttyS0,115200` and without the inert `netconsole=`;
   `fdt sunxi/dtbs/{{ sunxi_default_dtb }}`.
3. **Inventory**: `host_vars/fpgas.online.yml` gains
   ```yaml
   sunxi_default_dtb: sun8i-h3-orangepi-pc.dtb
   sunxi_boards:                      # FEL-booted from the hub host; see docs/hardware/…
     - {switch: 2, port: 20, host: pi-sw2-p30, usb: "1-1.2.2", model: orangepi-pc}
     - {switch: 2, port: 21, host: pi-sw2-p30, usb: "1-1.3.1", model: orangepi-pc}
     - {switch: 2, port: 23, host: pi-sw2-p30, usb: "1-1.2.3", model: orangepi-pc}
     - {switch: 2, port: 24, host: pi-sw2-p30, usb: "1-1.2.4", model: orangepi-pc}
   ```
4. **FEL-boot host** (new package `fpgas-online-felboot`, published through the existing apt
   pull model; installed by `onpi` into the shared root so *any* Pi with FEL devices on its USB
   becomes a boot host):
   * `sunxi-tools` (in Raspbian bookworm) + the vendored `u-boot-sunxi-with-spl.bin`
     (`orangepi_pc_plus` from Debian `u-boot-sunxi`; Raspbian does not ship it);
   * udev rule `ACTION=="add", ATTR{idVendor}=="1f3a", ATTR{idProduct}=="efe8"` →
     `systemd-run` of `felboot@<bus:dev>.service` running `sunxi-fel --dev … uboot …` (retry ×3,
     journal-logged) — a PoE cycle therefore re-boots a board with no operator action.
5. **Verify**: `verify-pi.yml` grows an `hw-sunxi` group (kernel `armmp`, DT model, `fpgas-tt`
   active) and the felboot host check (four `1f3a:efe8` seen or four U-Boot MACs in the FDB).
6. **Boot-time fix**: find the stuck udev event behind the 2 min `ifupdown-pre` wait (part of
   the implementation, measured with `systemd-analyze blame` on the board).
7. **Docs**: mapping doc (this PR) + a runbook for adding a board (SID → MAC, sheet entry).

Out of scope here: s3300-1 port descriptions (still stale for many ports), the two unexplained
1.2 W loads on p16/p22, wiring the PL2303 to a UART0 header (recommended — it is the only early
console), and the gdoc2netcfg sheet (already updated: `welland-ansible-rpi` branch
`worktree-opi-fel-boards`, published 2026-08-28 14:57 ACST).

## 4. How the spike was run (reproducible)

Scripts kept in `~/github/fpgas-online/tmp/opi-investigation/` on ten64: `poe_map.py`
(PoE off/on vs. USB tree), `opi.py` (`fel <usb-path>` FEL-boots a board through tweed →
pi-sw2-p30), `tweed_spike_setup.py` / `tweed_spike_teardown.py` (nfsroot copy + armmp kernel,
TFTP files, export line — all removed again), `boot_test.py`. Everything on tweed was restored;
pi-sw2-p30's additions (`sunxi-tools`, `~/opi/`) are listed in `pi-sw2-p30-changes.md` and
vanish on its next power cycle.

## 5. USB gadget console (added 2026-08-29)

The H3's UART0 is not wired on the rack, but every board's **OTG cable already
goes to the hub host** (it is how FEL works), so the same cable carries a USB
serial gadget once Linux runs. Facts that fixed the design:

* `# CONFIG_U_SERIAL_CONSOLE is not set` in Debian's `6.1.0-50-armmp` **and** in
  every Raspbian kernel of the root, so `console=ttyGS0` on the cmdline would be
  inert (`gs_console_init` is a stub without it). The console is therefore fed
  from userspace: `dmesg --follow` replays the ring buffer and follows `/dev/kmsg`.
* `g_serial`, `musb_sunxi`, `phy-sun4i-usb`, `dwc2` are modules everywhere;
  `musb_sunxi` autoloads from the DT (`allwinner,sun8i-h3-musb`).
* `gs_open()` never waits for a host; a write fills the gadget's 8 KiB buffer and
  then blocks **only that writer**; a host detach hangs the port up
  (`gserial_disconnect` → `tty_hangup`).

Design (`fpgas-online-setup-pi`, `usb-console/`, so every Pi has it):

1. udev `SUBSYSTEM=="udc"` add → `kmod load g_serial` (`n_ports=2`). Boards
   without a USB device controller (Pi 3, the CI VM) never load the gadget.
2. `ttyGS0` → `fpgas-usb-console.service` (`dmesg --follow`, `BindsTo` the
   device, `Restart=always`: every host attach gets the log replayed from the
   start of the ring buffer); `ttyGS1` → `serial-getty@ttyGS1`. Two ports
   because agetty's start-up `tcflush` resets the gadget's write buffer.
3. Host side (same package, `fpgas-usb-console-log@.service`): a `0525:a4a7`
   `usb-console` gadget's first ACM port is captured to
   `/var/log/fpgas-usb-console/<usb-port>.log` from the first byte, because
   with `systemd.log_level=debug` on the cmdline the 1 MB ring buffer wraps
   within minutes and a late reader cannot recover the early boot.
4. Infra: `config.txt` `[pi4]`/`[pi5]` `dtoverlay=dwc2,dr_mode=peripheral`
   (their USB-A ports are on other controllers; a Pi 3/Zero would lose its
   only USB, so those are excluded); `verify-pi` asserts the two units on any
   board with a UDC (`hw-usb-gadget`).

No host attached never blocks boot: verified on p21 (units active, writer
parked) and by the fleet boots before any host read the ports.
