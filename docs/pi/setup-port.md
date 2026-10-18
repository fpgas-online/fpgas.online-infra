# The setup / config port

**You operate the welland fleet and need a Pi with nothing locked: to flash its bootloader EEPROM, use its Wi-Fi
or Bluetooth, or change a board's SPI flash.** Plug the Pi into **port 1 of either fleet switch** and power it
(or power-cycle that port). It boots the fleet's root with the restrictions below lifted (the table says which,
and the one that may not lift on a Pi 5), and the site never offers it to visitors: its page and its dashboard row say **failed, setup port**. Move it to any other port and its
next boot is a normal fleet boot again; nothing it did on port 1 stays on the Pi except what was written to its
EEPROM or to a board's flash.

Tim's instruction (18 Oct 2026): "Make port 1 (or the first PoE controllable port) a special "setup / config"
port. Any device plugged into that port should boot with fully open permissions around spiflash / eeprom /
wlan0 / etc. The device should never be shown on the front page of fpgas.online (It should always be marked as
failed)."

## How the Pi knows it is on the setup port

```text
 Pi on sw1 port 1 or sw2 port 1                    gateway (dnsmasq, one VLAN per port)
 ------------------------------                    -------------------------------------
 bootloader asks TFTP on VLAN v2101 / v2201  --->  tftp-root=<nfs root>/boot-setup,v2101 (and v2201)
                                             <---  config.txt:  radios on, eeprom_write_protect=0,
                                                                dtparam=spi=on, dtparam=i2c_arm=on
                                             <---  cmdline.txt: ... fpgas.setup_port=1 systemd.restore_state=0
 kernel + the fleet's NFS root (read-only, the same as every Pi)
 udev rule 90-fpgas-setup-port-rfkill (only with fpgas.setup_port=1): each radio unblocked as it appears

 Pi on any other port: tftp-root=<nfs root>/boot (the fleet's): radios off, eeprom_write_protect=1
```

The gateway decides, by the port the Pi's boot requests arrive on: each switch port is its own VLAN, and
dnsmasq serves the setup port's VLAN its own boot tree (`roles/pxe/templates/ports.conf.j2`). No variable names
a Pi, and the Pi stores nothing. The port is `setup_port` in the site's host variables (welland: `1`), the same
number on every switch; a site with no `setup_port` has no setup port.

The setup tree is built from the fleet's on every root update, inside the update lock
(`roles/fixpi/tasks/setup-port.yml`, `files/scripts/setup_port_tree.py`): every file is a hard link to the fleet's,
except `config.txt`, `cmdline.txt` and `cmdline-pi5.txt`. The build stops, and the tree served before stays
served, if the fleet's `config.txt` no longer carries exactly one of each line it lifts, or already carries a line it
adds. `verify-server.yml` checks the served tree, and that dnsmasq serves it on the setup port of every switch and on
no other port. The CI VM test builds and checks a setup tree on its port 2; its virtual Pi boots on port 1, from the
fleet's tree. dnsmasq's per-interface `tftp-root` is the mechanism the hand-made EEPROM service tree on welland's
sw2 port 48 has used since 3 Oct 2026 (`tftp-root=<tree>,v2248`).

## Every restriction, and what the setup port does with it

| Restriction on a fleet Pi | Where it is set | On the setup port |
| --- | --- | --- |
| Wi-Fi off: `dtoverlay=disable-wifi` (wlan0 never appears) | `config.txt`, `roles/fixpi/tasks/tweeks.yml` | Line left out: wlan0 appears |
| Bluetooth off: `dtoverlay=disable-bt` | `config.txt`, `tweeks.yml` | Line left out. On a Pi 3B+ or Pi 4 Bluetooth then has the PL011 UART, and the header UART (the mini UART) stays off, because `enable_uart` is unset and defaults to 0 when the mini UART is the primary UART (Raspberry Pi `config.txt` documentation). A Pi 5's header UART (`dtoverlay=uart0-pi5`) is unchanged |
| Radio soft block (rfkill) | Raspberry Pi OS's saved rfkill state, restored by `systemd-rfkill` | Not restored (`systemd.restore_state=0`); the udev rule `90-fpgas-setup-port-rfkill.rules` writes `soft=0` on each radio as it appears. `rfkill`, `iw` and `wpasupplicant` are in the root (every Pi's) |
| Bootloader EEPROM write protection: `eeprom_write_protect=1` | `config.txt`, `tweeks.yml` | `eeprom_write_protect=0`. **May not unlock a locked Pi 5:** see below |
| Header SPI and I2C buses off (`dtparam=spi=on` and `dtparam=i2c_arm=on` are not set) | Raspberry Pi OS's `config.txt`, which the fleet serves as it is | Both added: `/dev/spidev0.*` and the header I2C bus appear |
| Root, `sudo` | `roles/fixpi/tasks/netboot.yml` | Already unrestricted on every Pi: `pi ALL=(ALL) NOPASSWD: ALL`. Nothing to lift |
| Device node owners and modes (I2C, GPIO, SPI) | Raspberry Pi OS's own; fpgas.online adds no udev rule (`roles/onpi/tasks/i2c.yml` only loads `i2c-dev`) | Not changed: `pi` reaches every node through `sudo`, as on every Pi |
| A board's own SPI flash (Acorn, Arty, Fomu, NeTV2) | No fpgas.online code leaves it write-protected (the Fomu check holds its `/WP` low only while it has the bus; `designs/_host/fomu_header_id.py` in fpgas.online-test-designs) | Nothing to lift |
| Magic SysRq off (`kernel.sysrq = 0`) | `roles/fixpi/files/etc/sysctl.d/99-fpgas-no-sysrq.conf` | **Kept.** It is not a permission: it stops bytes from an FPGA on the header UART from rebooting the Pi |
| Changes do not survive a reboot (read-only root, `overlayroot=tmpfs`) | `cmdline.txt` | **Kept.** It is what returns the Pi to the fleet's rules when it moves to another port. Only the EEPROM and a board's flash keep what is written to them |
| The Pi reboots itself after a root update (`nfsroot-watchdog`) | `roles/onpi/tasks/stale_root.yml` | **Kept.** Without it a Pi on the old root fails new programs after an update. It never reboots while the gateway's update lock is held; a flash written in the minutes after an update can still be cut short by it |
| Tiny Tapeout demo boards: no file of ours is written to them | Tim's rule of 5 Oct 2026 (`fpgas-tt`, the boot check) | **Kept.** It binds our code, not the person at the Pi, who may write to a board on any port |

## The Pi 5 bootloader EEPROM

Serving `eeprom_write_protect=0` is all the boot files can do, and it is not known to unlock a Pi 5. On
3 Oct 2026 serving `=0` over netboot did **not** clear a Pi 5 that was already locked: its status register stayed
`0xbc` in four boots, with the `TP14`-`TP1` bridge believed fitted. Why is not known (Raspberry Pi's documentation
ties the Pi 5 lock to `/WP`, which is pulled low on a Pi 5; whether that is the cause was not established). Nothing
fpgas.online runs writes the flash's status register from Linux. On a Pi 4 the effect of `=0` has not been measured.
The first Pi 5 booted on the setup port is checked with the read-only status register read on
[Raspberry Pi 5: check](https://docs.fpgas.online/en/latest/setup/bootloader-eeprom-pi5-check.html), and the result
is recorded here.

## What the site does

The site never offers a machine whose registered name is `pi-sw<s>-p<setup port>`: it is not on the front page or
any board list, and its status is **failed** with the reason **setup port** on its `/fleet/<serial>/` page and its
dashboard row. The gateway tells the site the port with `FPGAS_SETUP_PORT` in `/etc/environment`
(`roles/site/tasks/django.yml`).

## Sources

fpgas.online-infra main and this change: `roles/fixpi/tasks/tweeks.yml`, `netboot.yml`, `setup-port.yml`,
`roles/pxe/templates/ports.conf.j2`, `roles/onpi/tasks/i2c.yml` and `apt.yml`, `verify-server.yml`. The 3 Oct EEPROM
measurement: [The EEPROM lock](../netboot/eeprom.md) and the bootloader EEPROM pages it links.
