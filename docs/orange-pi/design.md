# Why the Orange Pis boot the way they do

**You operate the welland fleet and want the reasons behind the Orange Pi boot: why they share the Pi root, how
the FEL boot works, and where the U-Boot image comes from.** For how it works step by step see [Orange Pi H3
hosts](../orange-pi.md#how-it-works). From the spec of 2026-08-28
([`docs/superpowers/specs/2026-08-28-orange-pi-netboot-design.md`](../superpowers/specs/2026-08-28-orange-pi-netboot-design.md))
and the earlier docs page; the numbers are the spike's. The FEL and U-Boot sections were checked against
fpgas.online-setup-pi main on 2026-10-07.

## A shared root plus a second kernel

The 2026-08-28 spike chose a **shared NFS root plus a second kernel** over a
dedicated Orange Pi root. The reason it is possible at all is that the Raspbian
bookworm armhf userland runs unchanged on an ARMv7 H3, leaving only the kernel,
initrd and DTB board-specific. The reason it was preferred is that the boards
then get every fleet change for free, from one image pipeline, one `pi` play,
one set of packages and one `verify-pi`. The cost is one extra kernel
package in the root (in the spike of 2026-08-28: about 250 MB of modules and a one-off 30-minute
qemu-emulated `update-initramfs`; the root is now built natively on an arm64 CI runner, so the second figure no
longer applies), a `sunxi/` TFTP directory and one PXE file —
against a dedicated root, which would have doubled the roughly 100-minute apt
converge (as measured in the spike) and forked every Pi role. An Armbian root and per-MAC `pxelinux.cfg`
files were rejected for the same reason, the latter kept as the escape hatch if
a different sunxi board ever appears.

## The FEL mechanism

The FEL mechanism is what makes SD-less boards bootable at all: the H3 BROM
falls into USB FEL mode on every power-up and enumerates as `1f3a:efe8`, so the
hub host's udev rule (`60-fpgas-felboot.rules`, matching that VID/PID and adding
`SYSTEMD_WANTS`) starts `fpgas-felboot@%k.service`, and `fpgas-felboot.sh` reads
`busnum`/`devnum` out of sysfs and runs `sunxi-fel --dev <bus>:<dev> uboot
u-boot-sunxi-with-spl.bin`, retrying three times. About 3 s after a PoE cycle
the board is back in FEL and is re-booted with no operator action. The unit uses
`%i` rather than `%I` deliberately: systemd unescapes `%I` and turns the USB
name `1-1.2.2` into `1/1.2.2`, found on hardware on 2026-08-28.

## The vendored U-Boot

Raspbian ships no `u-boot-sunxi`, so the image is vendored in
`fpgas.online-setup-pi` under `felboot/u-boot/orangepi_pc_plus/`, copied
unmodified from Debian's `u-boot-sunxi_2025.01-3+deb13u1_armhf.deb`
(GPL-2.0-or-later). The README pins both hashes: sha256
`9f10a3532457b71006053028b013e7c73f86f55788872c8fba40ba1aa45f53cb` for the
559488-byte `.bin` and
`c34a1e756612a10ba3fa2abe4bdc6c0dd685cc472628879b0fe153251073c36a` for the
`.deb`, with a refresh procedure of download, `dpkg-deb -x`, copy, update the
hashes. The `orangepi_pc_plus` build runs on these PC boards because they share
the H3 and the 1 GB DRAM; the extra eMMC and wifi nodes are harmless, and it
only has to reach distro-boot.

## Where the design changed before it shipped

Two details of the design drifted before it shipped, and the shipped form is
what this page documents. The spec proposed a separate `fpgas-online-felboot`
package and a `felboot@<bus:dev>.service`; what exists is
`fpgas-online-setup-pi` shipping `fpgas-felboot@<usb kernel device>.service`, so
any Pi with FEL devices on its USB is a boot host. The spec counted four
boards; a fifth (p22) was resolved the evening the spec was written, and p18 and p19 were added on 2026-09-04:
seven in all.

> [!NOTE]
> **Open question.** Confirm the board variant by physical inspection. The spec, the mapping document
> and the vendored U-Boot README all record the same open question: these are
> 1 GB H3 boards running an `orangepi_pc_plus` U-Boot and a
> `sun8i-h3-orangepi-pc` device tree, and nobody has yet looked at one to say
> whether it is an Orange Pi PC or a PC Plus. `sunxi_boards` records all seven as
> `model: orangepi-pc` (infra main, read 2026-10-07).
