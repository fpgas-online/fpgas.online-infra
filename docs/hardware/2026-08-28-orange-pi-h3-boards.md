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
* **1/g16**: PoE `delivering` 1.1–1.4 W, link down, no USB device reacted when it
  was powered off for 25 s. Same power signature as an idle H3 in FEL — but Tim
  counts five Orange Pis, all now accounted for, so this is something else.
  Physical check needed.
* **1/g43, 1/g44, 1/g47**: 7–9 W, never link (cycled one at a time with 12 s off on
  2026-08-28: p44/p47 come back to the same draw with no link in 90 s; p43 came
  back at 7 W and then dropped to `searching`/0 mW after ~80 s). Physical check.

## Known issue: pi-sw2-p21 (USB 1-1.3.1) slow first boot

Observed 2026-08-28 in 2 of 3 first boots after a power cycle: U-Boot loads,
the kernel boots and mounts the NFS root (kernel DHCP seen on tweed), but
userspace crawls — ~40 MB read from NFS in 10 min versus ~170 MB in 2 min on
its siblings — and sshd never starts. A second PoE cycle boots it normally in
~87 s every time. p20/p23/p24 never showed this. Board-specific (marginal DRAM
on a cold start is the leading guess); it needs a serial console on **that**
board's UART0 to see what is happening. Recovery = PoE-cycle port 21.

## Deployment result (2026-08-28)

Hub-host reboot → `fpgas-felboot@` fired for all four boards within its 19 s
boot; staggered PoE cycles → p20 49 s, p23 52 s, p24 35 s to `multi-user`
(kernel 16 s + userspace 19–37 s) on the production root with kernel
`6.1.0-50-armmp`; `verify-pi.yml` (`hw-sunxi` included) green on the four
boards and the hub host (p21 after its second cycle).
