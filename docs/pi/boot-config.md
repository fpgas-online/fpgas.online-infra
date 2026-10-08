# Boot-time configuration

**You operate the fleet and want to know what the boot files every welland Pi reads set, to understand or change one.** This is the root fpgas.online-infra builds (main, read 2026-10-07); the systemd units are on [Services on a Pi host](services.md).

The firmware reads `config.txt` and `cmdline.txt` from the read-only TFTP root at every boot, so a root user
can change the running Pi for one session but never across a reboot. `roles/fixpi/tasks/tweeks.yml` adds to the
served `config.txt`:

```text
dtoverlay=disable-wifi
dtoverlay=disable-bt
eeprom_write_protect=1
[pi5]
dtoverlay=uart0-pi5
cmdline=cmdline-pi5.txt
[pi4]
dtoverlay=dwc2,dr_mode=peripheral
[pi5]
dtoverlay=dwc2,dr_mode=peripheral
[all]
```

It also removes `enable_uart=1` and `uart_2ndstage=1` if a root still has them: the firmware's UART logging
went to the header pins (GPIO 14/15), which must carry no boot output (#261). The header UART does not need
them: `disable-bt` keeps the Pi 3B+/4's PL011 there, and `uart0-pi5` the Pi 5's.

What each line does, the two command lines (`console=tty1`; `console=ttyAMA10,115200` on a Pi 5) and the
EEPROM lock: [Netboot and the NFS root](../netboot.md#the-kernel-command-line).

**Radios.** `dtoverlay=disable-wifi` and `dtoverlay=disable-bt` switch the onboard radios off on both Pi 4 and
Pi 5: on a Pi 5 the firmware maps `disable-wifi` to `disable-wifi-pi5` and `disable-bt` to `disable-bt-pi5`
through `overlay_map.dtb`, so the same two lines disable both radios on both models (`disable-wifi`: a Pi 4
disables `&mmc`/`&mmcnr`, a Pi 5 `&sdio2`; `disable-bt`: a Pi 4 `&bt`, a Pi 5 `&bluetooth`). `config.txt` is
served from the read-only TFTP root, so the lines are applied at every boot: a user with root can bring a radio
up for the life of a session, but never across a reboot (comment in `fixpi/tasks/tweeks.yml`, read 2026-10-07).

**USB-C gadget mode.** `dtoverlay=dwc2,dr_mode=peripheral` is applied on the Pi 4 and Pi 5 only. Their USB-C
port is a dwc2 OTG controller that the firmware leaves in host mode; in peripheral mode a laptop on that port
gets the kernel log and a login. It is safe on exactly these two models because their USB-A ports hang off
separate controllers (the VL805 on a Pi 4, the RP1 on a Pi 5), so nothing is lost, whereas on a Pi 3 or a Zero
dwc2 is the only USB there is, and on a 3B+ that includes the Ethernet. The fleet Pis take their power over PoE,
so the USB-C port is free. Orange Pi H3 boards need nothing here: `musb` autoloads (`tweeks.yml`).

**`core_freq`** is not set. `config.txt.j2` carries it commented out, as `# core_freq=250`. The infra
technical-debt notes (`TECHDEBT.md`, items 2 and 3) say why the value is contested. Dropping the core clock to
250 was an attempt at the intermittent (roughly 1 in 50) stuck-boot problem, on the theory that PoE power was
marginal, and it may have helped a little. But one Pi with a camera fails with a camera error at 250 or at
anything below 500, and `core_freq=500` fixed that. The note ends undecided: 500 might bring the PoE boot
problem back, or the PoE problem might never have been real. The decision whether to set `core_freq=500` is
therefore still open, and one of the two failure modes stays possible either way (`TECHDEBT.md`, read 2026-10-07).

**Edits to the root itself.** `tweeks.yml` also edits the root, mostly to stop units failing where nobody can
see them. It deletes `console-setup.service` from `multi-user.target.wants` (a failed console-setup on every
boot) and `profile.d/wifi-check.sh` (a "Wi-Fi is blocked by rfkill" warning on every login), and deletes
`/etc/hostname` so the DHCP-supplied name wins. It masks `networking.service` and `ifupdown-pre.service` to
`/dev/null`: `ifupdown` is unused with `ip=dhcp` plus NetworkManager, and `ifupdown-pre.service` sat through its
full two-minute `udevadm settle` on an Orange Pi H3 before anything else could start (spike of 2026-08-28:
userspace 2 m 16 s, of which `ifupdown-pre` 2 m 02 s; the stuck udev event underneath was never identified). It
writes `pistat_host` into `/etc/environment`, where the [units shipped by `fpgas-online-setup-pi`](setup-pi-units.md) read the server
name. The `fpgas-hostname-hosts.service` unit and the `timesyncd` drop-in that points the Pi's clock at the
gateway are `fixpi`'s too, from `netboot.yml`.
