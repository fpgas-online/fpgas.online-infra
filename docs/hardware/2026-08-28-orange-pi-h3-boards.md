# Orange Pi H3 boards on the fpgas.online Welland rack — port / USB / MAC mapping

Established 2026-08-28 by (1) switching each candidate PoE port off for 25 s and
watching which FEL device vanished from the hub host's USB tree, then (2)
FEL-booting U-Boot on each board and reading the MAC the switch learned.
Both methods agreed for all four boards.

## Hub host

* **pi-sw2-p30** — Raspberry Pi 5 Rev 1.1, 1 GB, serial 3c1fc2b41d68ae81,
  eth0 98:fe:54:13:f5:9c, 10.21.2.30, s3300-1 port 30. Netboots the shared
  Raspbian bookworm NFS root (overlayroot=tmpfs).
* USB: one Realtek RTS5411 hub at `1-1` with four RTS5411 sub-hubs `1-1.1`…`1-1.4`
  (16 downstream ports). A USB-3 twin (`2-1`, 0bda:0411) has nothing attached.

## Boards (five; all Allwinner H3, `sunxi-fel ver` soc=0x1680; U-Boot `orangepi_pc_plus` DRAM init OK)

| Hub port (sysfs) | Switch port (s3300-1 = "sw2") | VLAN | IP / hostname (per-port scheme) | MAC (U-Boot, derived from SID) | SID (`sunxi-fel sid`) |
|---|---|---|---|---|---|
| 1-1.2.2 | 1/g20 | 2220 | 10.21.2.20 `pi-sw2-p20` | 02:81:bf:f6:b7:99 | 02c00181:34304620:79058814:541b0614 |
| 1-1.3.1 | 1/g21 | 2221 | 10.21.2.21 `pi-sw2-p21` | 02:81:31:f4:6e:48 | 02c00081:35b04620:79058814:502c0194 |
| 1-1.2.3 | 1/g23 | 2223 | 10.21.2.23 `pi-sw2-p23` | 02:81:1f:e1:45:1d | 02c00181:34504620:79058814:40260714 |
| 1-1.2.4 | 1/g24 | 2224 | 10.21.2.24 `pi-sw2-p24` | 02:81:f5:c0:a6:10 | 02c00081:35e04620:79058814:48230714 |
| 1-1.3.2 | 1/g22 | 2222 | 10.21.2.22 `pi-sw2-p22` | 02:81:2e:b7:a3:4e | 02c00081:35d04620:79058814:401c0a94 |

* Power: PoE from s3300-1 through a PoE splitter (1.1–1.2 W while idle in FEL,
  ~1.5 W with U-Boot running). The micro-USB OTG cable does **not** power them:
  cutting PoE made the USB device disappear within seconds.
* Link speed once U-Boot is up: 100 Mbit/s (H3 internal fast-ethernet PHY).
* Exact board variant (OPi PC / PC Plus / One) still needs a physical check —
  U-Boot's `orangepi_pc_plus` build and the `sun8i-h3-orangepi-pc` DT both run;
  the kernel log (SY8106A regulator present? RAM size?) will narrow it down.

## Other USB devices on the hub

| Hub port | Device | Notes |
|---|---|---|
| 1-1.1.4 | Prolific PL2303 USB-serial (067b:2303) → `/dev/ttyUSB0` | Received **nothing** at 115200 8N1 while each of the four boards ran U-Boot, so it is not wired to any of their UART0 headers (or is wired TX/RX-swapped). |

## Ports still unexplained on s3300-1

* **1/g22** was resolved on 2026-08-28 evening: its OTG cable was plugged into hub
  port 1-1.3.2, felboot picked it up and it is `pi-sw2-p22` (5th board above).
* **1/g16**: nothing is connected (Tim, 2026-08-28). The switch nevertheless
  reported `delivering` 1.1–1.4 W for days — a stale PoE-controller reading; a
  PoE off/on cleared it to `searching`/0 mW. Lesson: cycle a port before
  believing a small phantom load.
* **1/g43, 1/g44, 1/g47**: resolved 2026-08-28 evening — cycling each one at a time
  with 12 s off brought all three back (they take >90 s; p43's PoE draw dipping to
  0 mW mid-way is the Pi 5's own power-on reset). They are `pi-sw2-p43/p44/p47`,
  Raspberry Pi 5s (98:fe:54:13:e0:75 / 13:e0:f5 / 13:f5:75). Hung boots, like p7.

## Known issue: slow first boot after a cold FEL boot (seen on p21 ×2, p24 ×1)

Observed 2026-08-28 in 2 of 3 first boots after a power cycle: U-Boot loads,
the kernel boots and mounts the NFS root (kernel DHCP seen on tweed), but
userspace crawls — ~40 MB read from NFS in 10 min versus ~170 MB in 2 min on
its siblings — and sshd never starts. A second PoE cycle boots it normally in
~87 s every time. First thought board-specific to p21, but p24 did the same on
2026-08-28 evening (after being held in FEL for ~20 s), so it is a general
~1-in-4 cold-FEL-boot flake — a UART0 console on any board would show what
stalls. Recovery = PoE-cycle the port (the runbook's normal path).

## Deployment result (2026-08-28)

Hub-host reboot → `fpgas-felboot@` fired for all four boards within its 19 s
boot; staggered PoE cycles → p20 49 s, p23 52 s, p24 35 s to `multi-user`
(kernel 16 s + userspace 19–37 s) on the production root with kernel
`6.1.0-50-armmp`; `verify-pi.yml` (`hw-sunxi` included) green on the four
boards and the hub host (p21 after its second cycle).

## udev symlinks on the hub host (2026-08-28)

`/etc/udev/rules.d/70-fpgas-opi-ports.rules` on rpi5-new-13f59c (source of truth:
`welland-ansible-rpi` `inventory/host_vars/rpi5-new-13f59c.yml`, `hw_udev_files`)
names each FEL device by everything we know about it. The links exist while the
board sits in FEL, i.e. from power-on until `fpgas-felboot@` loads U-Boot:

| Hub port | `/dev/fpgas/opi/…` symlinks (all → `/dev/bus/usb/001/NNN`) |
|---|---|
| 1-1.2.2 | `sw2-p20`, `usb-1-1.2.2`, `sid-02c00181-34304620-79058814-541b0614`, `mac-02-81-bf-f6-b7-99` |
| 1-1.3.1 | `sw2-p21`, `usb-1-1.3.1`, `sid-02c00081-35b04620-79058814-502c0194`, `mac-02-81-31-f4-6e-48` |
| 1-1.3.2 | `sw2-p22`, `usb-1-1.3.2`, `sid-02c00081-35d04620-79058814-401c0a94`, `mac-02-81-2e-b7-a3-4e` |
| 1-1.2.3 | `sw2-p23`, `usb-1-1.2.3`, `sid-02c00181-34504620-79058814-40260714`, `mac-02-81-1f-e1-45-1d` |
| 1-1.2.4 | `sw2-p24`, `usb-1-1.2.4`, `sid-02c00081-35e04620-79058814-48230714`, `mac-02-81-f5-c0-a6-10` |
| any other | `usb-<port>` (`FPGAS_SWITCH_PORT=unknown`) |
| 1-1.1.4 (PL2303) | `/dev/fpgas/serial/hub-1-1.1.4` → `ttyUSB0` |

`udevadm info` on the device also carries `FPGAS_SWITCH_PORT=sw2-pNN`. Verified by
holding p24 in FEL (felboot instance masked) and reading the links.

## USB gadget console (2026-08-29)

Once Linux runs, each board's OTG cable presents a `0525:a4a7 Linux-USB Serial
Gadget` (`fpgas.online usb-console`) to the hub host with two CDC-ACM ports;
`/dev/serial/by-path/platform-xhci-hcd.0-usb-0:<hub port>:2.0` is the kernel
log (`fpgas-usb-console.service` on the board, `dmesg --follow`) and `…:2.2`
a login getty. The hub host's `fpgas-usb-console-log@ttyACM*.service` appends
the log port to `/var/log/fpgas-usb-console/<hub port>.log` (e.g.
`1-1.3.1.log` = pi-sw2-p21) from the moment the gadget enumerates.
Verified 2026-08-29 on pi-sw2-p21: `ttyACM0/1` at `1.3.1`, 1.1 MB of log
replayed in 4 s, `pi-sw2-p21 login:` on the second port.
