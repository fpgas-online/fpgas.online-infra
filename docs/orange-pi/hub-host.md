# The Orange Pi hub host

**You operate the welland fleet and want to know what the Orange Pis' hub host is, how it boots, what was
configured on it by hand, and which device links it carries.** If it is down, no Orange Pi can boot ([Recover a
board](recover.md#recover-a-board)). From the earlier docs page and infra's runbook and hardware doc of
2026-08-28; what was read again is marked.

## The hub host

`pi-sw2-p30` is a Raspberry Pi 5 Rev 1.1, 1 GB, serial `3c1fc2b41d68ae81`, eth0 `98:fe:54:13:f5:9c`, on switch 2
port 30 (read 2026-08-28). Its USB tree is one Realtek RTS5411 hub at `1-1` with four RTS5411 sub-hubs `1-1.1`
to `1-1.4`, giving 16 downstream ports; the USB-3 twin at `2-1` (0bda:0411) has nothing attached.

- **It boots its own microSD.** Since the evening of 2026-08-28 it is not an NFS-root Pi: its card carries the
  `welland-ansible-rpi` fleet-bootstrap-arm64 image (Raspberry Pi OS trixie, cloud-init accounts `tim` and
  `ansible`, hostname `rpi5-new-13f59c`), and its Pi 5 EEPROM was set to `BOOT_ORDER=0xf21`: SD first, netboot
  only if the card fails. That was verified both ways on 2026-08-28: with the card in, the bootloader fetched
  nothing from the gateway; with netboot deliberately broken, the card booted and both accounts logged in. The
  gateway still hands it `10.21.2.30` and `pi-sw2-p30` on the per-port VLAN, so the boards' addressing is
  unchanged.
- **Corrections to the 2026-08-28 text.** The card then was 8 GB. On 2026-09-05 that card was made into the
  EEPROM recovery card for two Pi 5s, and the host runs from a 32 GB microSD (an operator's note of
  that day, not re-checked). On 5 October 2026 a deploy found it SD-booted with the `pi` key refused, and left it
  out of the power-cycle wave (the deploy record of that day).

Three things were hand-configured on that OS, to be captured by `welland-ansible-rpi` when the host is enrolled
there (from the earlier docs page, not re-checked on the host):

- **The apt source** `https://fpgas.online/apt trixie main` (key in `/usr/share/keyrings/fpgas-online.gpg`),
  with `fpgas-online-setup-pi` and `sunxi-tools` installed. This is what FEL-boots the Orange Pis; it was proven
  on this OS: a PoE-cycled board was back in 72 s.
- **The NetworkManager profile** `netplan-eth0` with `ipv4.never-default yes` and `ipv6.never-default yes`,
  because eth0 (the gateway's VLAN) has no internet and `wlan0` carries the default route.
- **Verification.** `verify-pi.yml` is written for hosts that boot the NFS root and does not apply to this
  host as it is: its FEL-boot marker check runs only on a host named `pi-sw2-p30`, and this host's own hostname
  is `rpi5-new-13f59c` ([Check a board](recover.md#check-a-board)). Moving that check to the fleet repository is
  open.

Check the host's packages with a plain `dpkg -l fpgas-online-setup-pi sunxi-tools` over ssh on the host itself:
the `chroot` check in [Deploying](add.md#deploying) looks at the shared NFS root, which the boards run, not at
this host.

<a id="udev-symlinks-on-the-hub-host"></a>
## udev symlinks on the hub host

Recorded 2026-08-28. `/etc/udev/rules.d/70-fpgas-opi-ports.rules` on the hub
host (source of truth: `welland-ansible-rpi`
`inventory/host_vars/rpi5-new-13f59c.yml`, `hw_udev_files`) names each FEL
device by everything known about it. The links exist only while a board sits in
FEL — that is, from power-on until `fpgas-felboot@` loads U-Boot — so they are
the handle for anything that has to talk to a board *before* it boots.

| Hub port | `/dev/fpgas/opi/…` symlinks (all → `/dev/bus/usb/001/NNN`) |
| --- | --- |
| 1-1.2.2 | `sw2-p20`, `usb-1-1.2.2`, `sid-02c00181-34304620-79058814-541b0614`, `mac-02-81-bf-f6-b7-99` |
| 1-1.3.1 | `sw2-p21`, `usb-1-1.3.1`, `sid-02c00081-35b04620-79058814-502c0194`, `mac-02-81-31-f4-6e-48` |
| 1-1.3.2 | `sw2-p22`, `usb-1-1.3.2`, `sid-02c00081-35d04620-79058814-401c0a94`, `mac-02-81-2e-b7-a3-4e` |
| 1-1.2.3 | `sw2-p23`, `usb-1-1.2.3`, `sid-02c00181-34504620-79058814-40260714`, `mac-02-81-1f-e1-45-1d` |
| 1-1.2.4 | `sw2-p24`, `usb-1-1.2.4`, `sid-02c00081-35e04620-79058814-48230714`, `mac-02-81-f5-c0-a6-10` |
| any other | `usb-<port>` (`FPGAS_SWITCH_PORT=unknown`) |
| 1-1.1.4 (PL2303) | `/dev/fpgas/serial/hub-1-1.1.4` → `ttyUSB0` |

`udevadm info` on the device also carries `FPGAS_SWITCH_PORT=sw2-pNN`. Verified
by masking p24's felboot instance, holding it in FEL and reading the links.

> [!NOTE]
> The table is the five boards recorded on 2026-08-28. pi-sw2-p18 and pi-sw2-p19 (`1-1.3.4`, `1-1.3.3`) joined
> on 2026-09-04 and are not in the recorded table, so for them only the fallback `usb-<port>` link is known. The
> rule's source is `welland-ansible-rpi` `inventory/host_vars/rpi5-new-13f59c.yml`, `hw_udev_files`; it was not
> re-read on the host.
