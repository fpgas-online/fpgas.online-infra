# The setup / config port

**You operate the welland fleet and need a Pi with nothing locked: to flash its bootloader EEPROM, use its Wi-Fi
or Bluetooth, or change a board's SPI flash.** Plug the Pi into **port 1 of either fleet switch** and power it
(or power-cycle that port). It boots the fleet's root with every restriction below lifted, and the site never
offers it to visitors: its page and its dashboard row say **failed, setup port**. Move it to any other port and its
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
                                             <---  config.txt:  radios on, eeprom_write_protect=0
                                             <---  cmdline.txt: ... fpgas.setup_port=1 systemd.restore_state=0
 kernel + the fleet's NFS root (read-only, the same as every Pi)
 fpgas-setup-port.service (only with fpgas.setup_port=1): rfkill unblock all

 Pi on any other port: tftp-root=<nfs root>/boot (the fleet's): radios off, eeprom_write_protect=1
```

The gateway decides, by the port the Pi's boot requests arrive on: each switch port is its own VLAN, and
dnsmasq serves the setup port's VLAN its own boot tree (`roles/pxe/templates/ports.conf.j2`). No variable names
a Pi, and the Pi stores nothing. The port is `setup_port` in the site's host variables (welland: `1`), the same
number on every switch; a site with no `setup_port` has no setup port.

The setup tree is built from the fleet's on every root update, inside the update lock
(`roles/fixpi/tasks/setup-port.yml`, `files/scripts/setup_port_tree.py`): every file is a hard link to the fleet's,
except `config.txt`, `cmdline.txt` and `cmdline-pi5.txt`. The build stops, and the tree served before stays
served, if the fleet's `config.txt` no longer carries exactly one of each line it lifts. `verify-server.yml`
checks the served tree and the two dnsmasq lines.

## Every restriction, and what the setup port does with it

| Restriction on a fleet Pi | Where it is set | On the setup port |
| --- | --- | --- |
| Wi-Fi off: `dtoverlay=disable-wifi` (wlan0 never appears) | `config.txt`, `roles/fixpi/tasks/tweeks.yml` | Line left out: wlan0 appears |
| Bluetooth off: `dtoverlay=disable-bt` | `config.txt`, `tweeks.yml` | Line left out. On a Pi 3B+ or Pi 4 the header UART is then the mini UART, as on a stock Pi |
| Radio soft block (rfkill) | Raspberry Pi OS's saved rfkill state | Not restored (`systemd.restore_state=0`); `fpgas-setup-port.service` runs `rfkill unblock all`. `rfkill`, `iw` and `wpasupplicant` are in the root |
| Bootloader EEPROM write protection: `eeprom_write_protect=1` | `config.txt`, `tweeks.yml` | `eeprom_write_protect=0` |
| Root, `sudo` | `roles/fixpi/tasks/netboot.yml` | Already unrestricted on every Pi: `pi ALL=(ALL) NOPASSWD: ALL`. Nothing to lift |
| I2C, GPIO and SPI device nodes | Raspberry Pi OS's own owners and modes; fpgas.online adds no udev rule (`roles/onpi/tasks/i2c.yml` only loads `i2c-dev`) | Not changed: `pi` reaches every node through `sudo`, as on every Pi |
| A board's own SPI flash (Acorn, Arty, Fomu, NeTV2) | No fpgas.online code leaves it write-protected (the Fomu check holds its `/WP` low only while it has the bus; `designs/_host/fomu_header_id.py` in fpgas.online-test-designs) | Nothing to lift |
| Magic SysRq off (`kernel.sysrq = 0`) | `roles/fixpi/files/etc/sysctl.d/99-fpgas-no-sysrq.conf` | **Kept.** It is not a permission: it stops bytes from an FPGA on the header UART from rebooting the Pi |
| Tiny Tapeout demo boards: no file of ours is written to them | Tim's rule of 5 Oct 2026 (`fpgas-tt`, the boot check) | **Kept.** It binds our code, not the person at the Pi, who may write to a board on any port |

## The Pi 5 bootloader EEPROM

Serving `eeprom_write_protect=0` is all the boot files can do. On a Pi 5 the flash's `/WP` pin is pulled low,
and on 3 Oct 2026 serving `=0` over netboot did not clear a Pi 5 that was already locked (its status register
stayed `0xbc` in four boots). A Pi 4, whose `/WP` is not pulled low, is unlocked by it. Whether a locked Pi 5 is
unlocked on the setup port is read on the first Pi 5 booted there, with the read-only status register check on
[Raspberry Pi 5: check](https://docs.fpgas.online/en/latest/setup/bootloader-eeprom-pi5-check.html).

## What the site does

The site never offers a machine whose registered name is `pi-sw<s>-p<setup port>`: it is not on the front page or
any board list, and its status is **failed** with the reason **setup port** on its `/fleet/<serial>/` page and its
dashboard row. The gateway tells the site the port with `FPGAS_SETUP_PORT` in `/etc/environment`
(`roles/site/tasks/django.yml`).

## Sources

fpgas.online-infra main and this change: `roles/fixpi/tasks/tweeks.yml`, `netboot.yml`, `setup-port.yml`,
`roles/pxe/templates/ports.conf.j2`, `roles/onpi/tasks/i2c.yml` and `apt.yml`, `verify-server.yml`. The 3 Oct EEPROM
measurement: [The EEPROM lock](../netboot/eeprom.md) and the bootloader EEPROM pages it links.
