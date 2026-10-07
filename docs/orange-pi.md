# Orange Pi H3 hosts

**You operate the welland fleet and want to know how its Orange Pi boards boot and which board is on which
port.** To read a board's console, recover one or add one, use the pages under [The tasks](#the-tasks). The
Orange Pis carry no FPGA.

## The tasks

<a id="reading-a-console"></a>
- [Reading a console, checking and recovering a board](orange-pi/recover.md).

<a id="verifying"></a>
- [Checking a board](orange-pi/recover.md#check-a-board).

<a id="recovery"></a>
- [Recovering a board](orange-pi/recover.md#recover-a-board).

<a id="known-issues"></a>
<a id="no-usb-host-attached-does-not-block-or-delay-the-boot"></a>
- [Known issues](orange-pi/recover.md#known-issues).

<a id="adding-a-board"></a>
- [Adding or moving a board, and deploying](orange-pi/add.md).

<a id="deploying-and-reconverging"></a>
- [Deploying](orange-pi/add.md#deploying).

<a id="udev-symlinks-on-the-hub-host"></a>
- [The hub host](orange-pi/hub-host.md): how it boots, what was configured on it by hand, its device links.

<a id="design"></a>
- [How they boot](#how-it-works), below, and [why they boot that way](orange-pi/design.md): the shared root,
  the FEL mechanism, the vendored U-Boot.


## How it works

An Orange Pi PC (Allwinner H3) has no SD card or eMMC here, so at power-on its boot ROM waits in USB FEL
mode. Its OTG cable goes to the hub host, `pi-sw2-p30`, where `fpgas-felboot@.service` (from
fpgas-online-setup-pi) sees it appear and loads U-Boot into it over USB, leaving a marker in
`/run/fpgas-felboot/<usb path>`. U-Boot then netboots from the gateway: it fetches `pxelinux.cfg/default-arm-sunxi`
by TFTP, which loads Debian's armmp kernel, its initrd and the board's device tree from `sunxi/`, and mounts
the same NFS root as the Raspberry Pis, read-only with a tmpfs over it.

- The root side is two files in the `fixpi` role. `tasks/sunxi-image.yml`, run by the CI image build only,
  adds a Debian bookworm armhf apt source to the root (with Debian's own keyring), pinned so that only
  `linux-image-*-armmp` and `linux-base` may come from it, installs `linux-image-armmp` into the root, and
  blacklists the H3's audio codec modules. `tasks/sunxi.yml`, run on the gateway only where `sunxi_boards` is
  defined, copies `vmlinuz-*-armmp` and `initrd.img-*-armmp` into `<tftp_root>/sunxi/`, the three
  `sun8i-h3-orangepi-{pc,pc-plus,one}.dtb` files into `<tftp_root>/sunxi/dtbs/` (where the `fdt` line below
  looks), enables the header's I²C controllers in those copies, and writes the PXE file. The install is safe for
  the Pi fleet: Raspbian's `z50-raspi-firmware` kernel hook prints "Unsupported kernel version
  (6.1.0-50-armmp) - skipping setup" and leaves `/boot/firmware` untouched (verified 2026-08-28 in the spike;
  `sunxi-image.yml`, `sunxi.yml`, infra main, read 2026-10-07).
- A board's name and address follow its switch port, as for every welland Pi: `pi-sw2-p19` at `10.21.2.19`.
- After boot, the same OTG cable carries a USB serial console back to the hub host (below).

The ROM waits in FEL mode with the USB id `1f3a:efe8`. The hub host's `fpgas-felboot@<usb-device>.service` loads
U-Boot (the `orangepi_pc_plus` build, vendored in fpgas-online-setup-pi) with `sunxi-fel`, and U-Boot's
distro-boot takes over: it asks DHCP, which hands it `pi-sw2-p<port>` and `10.21.2.<port>` by the per-port
scheme exactly as for a Pi, then fetches the PXE file and `sunxi/{vmlinuz,initrd.img,dtbs/...}` from the
gateway's TFTP root. U-Boot looks for `pxelinux.cfg/01-<mac>` first, then the IP-hex names, then
`default-arm-sunxi`, `default-arm` and `default`, so a different sunxi board model can be given its own file
without disturbing these boards. The one `fixpi` templates (`templates/boot/default-arm-sunxi.j2`, checked
against infra main, 2026-10-07) is short:

```jinja
default sunxi
timeout 10

label sunxi
  kernel sunxi/vmlinuz
  initrd sunxi/initrd.img
  fdt sunxi/dtbs/{{ sunxi_default_dtb }}
  append root=/dev/nfs nfsroot={{ eth_local_address }}:{{ nfs_root }}/root,nfsvers=3,tcp ro ip=dhcp rootwait consoleblank=0 overlayroot=tmpfs console=ttyS0,115200 systemd.log_level=debug systemd.log_target=kmsg log_buf_len=1M printk.devkmsg=on
```

`sunxi_default_dtb` is `sun8i-h3-orangepi-pc.dtb`. There is no `netconsole=` on that line, unlike the Pi [kernel
command line](netboot.md#the-kernel-command-line): `dwmac-sun8i` is an initramfs module, so the kernel's netconsole
has no interface to bind to. `console=ttyS0` is the H3's UART0 3-pin debug header, which is not wired on the
rack, and `console=ttyGS0` would be inert because `CONFIG_U_SERIAL_CONSOLE` is unset in the kernels used (the
template's header comment and `fpgas-usb-console.service`), which is why the usable console is fed from
userspace.

<a id="board-mapping"></a>
## Which board is where

Each board is identified by its Digilent Pmod HAT's UUID (`hat_uuid`, read from the HAT's ID EEPROM) and its
MAC; its port and USB path are where it is plugged in now, and change if it is recabled. All on switch 2, all
`orangepi-pc`, all FEL-booted from `pi-sw2-p30` (`sunxi_boards`, read from the hardware 2026-09-04):

| HAT UUID | MAC | Port (read 4 Sep 2026) | Name | USB path on the hub host |
|---|---|---|---|---|
| 66196f35-58f5-4b03-b479-d9eb1f696204 | 02:81:0b:12:20:44 | 18 | pi-sw2-p18 | 1-1.3.4 |
| 547291f7-1440-4be9-a49a-c3081fa92984 | 02:81:e1:ce:7d:46 | 19 | pi-sw2-p19 | 1-1.3.3 |
| 0d05d999-1d10-49e7-b94a-8c2252816633 | 02:81:bf:f6:b7:99 | 20 | pi-sw2-p20 | 1-1.2.2 |
| 02b54c27-053b-463d-a74a-e6614129ed88 | 02:81:31:f4:6e:48 | 21 | pi-sw2-p21 | 1-1.3.1 |
| 6c12f955-093c-4272-a94d-8824363bffaa | 02:81:2e:b7:a3:4e | 22 | pi-sw2-p22 | 1-1.3.2 |
| 55fc28c9-257a-4d0d-8888-117834bd52ab | 02:81:1f:e1:45:1d | 23 | pi-sw2-p23 | 1-1.2.3 |
| 2577845e-7668-460a-a9d1-2c97373b1da9 | 02:81:f5:c0:a6:10 | 24 | pi-sw2-p24 | 1-1.2.4 |

On 6 October 2026 four of them answered after the root update: ports 19, 21, 22 and 24, with the MACs above
([Hosts and boards at welland](https://docs.fpgas.online/en/latest/sites/welland-boards.html#what-was-up-on-6-october-2026)). Ports 18, 20 and 23
did not.

## The hub host

`pi-sw2-p30` is a Raspberry Pi with the seven OTG cables on its USB hub; it boots its own SD card, not the NFS
root ([The hub host](orange-pi/hub-host.md)). fpgas-online-setup-pi gives it two
jobs: FEL-booting any Allwinner board that appears (`fpgas-felboot@.service`), and capturing each board's
kernel log from the USB serial console into `/var/log/fpgas-usb-console/<usb path>.log` from the first byte
(`fpgas-usb-console-log@.service`). If it is down, no Orange Pi can boot.

## Sources

fpgas.online-infra main, read 2026-10-07: `sunxi_boards` and `sunxi_default_dtb` in `host_vars/fpgas.online.yml`
(seven `sunxi_boards` entries), the `fixpi` tasks `sunxi.yml` and `sunxi-image.yml`,
`templates/boot/default-arm-sunxi.j2`, `verify-pi.yml`. Three infra documents hold the 2026-08-28 detail:

- [`docs/superpowers/runbooks/2026-08-28-orange-pi-netboot.md`](superpowers/runbooks/2026-08-28-orange-pi-netboot.md):
  how the boot works, the converge, reading a console, the verify line, PoE recovery, adding a board, the
  cold-boot flake and the evening hub-host addendum. Written for five boards, before p18 and p19; its verify
  line is stale.
- [`docs/hardware/2026-08-28-orange-pi-h3-boards.md`](hardware/2026-08-28-orange-pi-h3-boards.md): the port, USB,
  MAC and SID mapping and how it was established, the hub host's USB tree, the PL2303, the udev symlinks, the
  gadget console and the no-host measurements, the slow-first-boot flake, the deployment result of 2026-08-28,
  the audio-codec Oops and the mid-uptime deaths of 2026-09-02. It still says the hub host netboots; it
  does not (see [The hub host](orange-pi/hub-host.md)).
- [`docs/superpowers/specs/2026-08-28-orange-pi-netboot-design.md`](superpowers/specs/2026-08-28-orange-pi-netboot-design.md):
  the shared-root decision and what it was weighed against, the FEL answer, the inert `netconsole=` and
  `console=ttyGS0`, the `ifupdown-pre` wait, the open variant question.

fpgas.online-setup-pi main:
[`README.md`](https://github.com/fpgas-online/fpgas.online-setup-pi/blob/main/README.md);
[`felboot/60-fpgas-felboot.rules`](https://github.com/fpgas-online/fpgas.online-setup-pi/blob/main/felboot/60-fpgas-felboot.rules)
(the `1f3a:efe8` match and the `SYSTEMD_WANTS` name),
[`felboot/fpgas-felboot@.service`](https://github.com/fpgas-online/fpgas.online-setup-pi/blob/main/felboot/fpgas-felboot@.service)
(`StopWhenUnneeded`, `%i` not `%I`),
[`felboot/fpgas-felboot.sh`](https://github.com/fpgas-online/fpgas.online-setup-pi/blob/main/felboot/fpgas-felboot.sh)
(the sysfs `busnum`/`devnum` lookup, three retries, the `/run/fpgas-felboot` markers) and
[`felboot/u-boot/README.md`](https://github.com/fpgas-online/fpgas.online-setup-pi/blob/main/felboot/u-boot/README.md)
(provenance, both SHA256 pins, the refresh procedure).
