# Reading, checking and recovering an Orange Pi

**An Orange Pi at welland is misbehaving or did not come back, and you want its console, a check of its
health, and the way to get it back.** You need logins on the gateway and on the hub host, `pi-sw2-p30`.
Which board is on which port, with its HAT UUID and MAC: [Orange Pi H3 hosts](../orange-pi.md#which-board-is-where).

## Read its console

There is no early console you can reach: the H3's debug UART is not wired, and the kernel's netconsole cannot
bind on these boards. Once Linux is up, each board's OTG cable presents a `0525:a4a7 Linux-USB Serial Gadget`
(`fpgas.online usb-console`) to the hub host with two CDC-ACM ports.
`/dev/serial/by-path/platform-xhci-hcd.0-usb-0:<hub port>:2.0` is the kernel log (`fpgas-usb-console.service` on
the board, running `dmesg --follow`) and `...:2.2` is a login getty. The hub host's
`fpgas-usb-console-log@ttyACM*.service` appends the log port to `/var/log/fpgas-usb-console/<hub port>.log`
from the moment the gadget enumerates, so these are the only console the boards have (units:
[Units shipped by fpgas-online-setup-pi](../pi/services.md#units-shipped-by-fpgas-online-setup-pi)). Every path
is keyed by the board's USB path, not its hostname or its switch port: port 21 is `1-1.3.1`
([the table](../orange-pi.md#which-board-is-where)).

```console
$ ssh <you>@10.21.2.30                    # the hub host
$ ls -l /dev/serial/by-path/ | grep ':2.0'   # :2.0 is the kernel log, :2.2 the getty
$ tail -f /var/log/fpgas-usb-console/1-1.3.1.log   # captured from the first byte
$ picocom /dev/serial/by-path/platform-xhci-hcd.0-usb-0:1.3.1:2.2   # the login getty
$ ls /run/fpgas-felboot/                  # one marker per board U-Boot was loaded into this boot
$ journalctl -u 'fpgas-felboot@*'         # the FEL-boot attempts
```

(fpgas-online-setup-pi `README.md`; the marker is what `verify-pi.yml` checks for each board.) The marker is
there because the journal is no record to rely on: it is volatile, it rotates within minutes under the
systemd debug logging, and a finished oneshot instance is unloaded from `systemctl` (`verify-pi.yml`, the
comment above the marker check, read 2026-10-07).

- **A healthy boot, on the clock.** A board reaches `multi-user` about
  35–52 s after power-on and its gadget enumerates on the hub host at about 55 s,
  so the log file starts appearing then (2026-08-28 and 2026-08-29); sshd answers
  at about 96 s (2026-08-29). A board PoE-cycled while the SD-booted hub host was
  already up was back in 72 s (2026-08-28 evening). Anything much past that is the
  flake under [Known issues](#known-issues), not a slow boot.
- The board replays its whole ring buffer to every *USB attach*. A second reader
  on an already-attached port sees only new lines; to replay again, restart
  `fpgas-usb-console` on the board.
- Open the port once, in raw mode — `picocom`, `tio` or the logger. An `stty -F`
  before a `cat` opens and closes the port in cooked mode and drops the start of
  the stream.
- A board that crawls on its first boot now leaves its kernel log in
  `/var/log/fpgas-usb-console/<hub port>.log` on the hub host, which is how the
  audio-codec Oops below was finally found.

Verified 2026-08-29 on pi-sw2-p21: `ttyACM0`/`ttyACM1` at `1.3.1`, 1.1 MB of log
replayed in 4 s, and `pi-sw2-p21 login:` on the second port. A cold PoE cycle of
pi-sw2-p20 the same day was captured from `[    0.000000] Booting Linux` — the
gadget enumerated 55 s after power-on and the logger wrote `1-1.2.2.log` from
the first byte. The capture-from-enumeration design exists because the cmdline
carries `systemd.log_level=debug`, under which the 1 MB ring buffer wraps within
minutes and a late reader cannot recover the early boot.

### No USB host attached does not block or delay the boot

Tested 2026-08-29 on pi-sw2-p20 by disabling its hub port in sysfs
(`/sys/bus/usb/devices/1-1.2:1.0/1-1.2-port2/disable`) one second after
`fpgas-felboot` loaded U-Boot, so the OTG link was dead for the whole boot:

| Check | Result |
| --- | --- |
| `systemd-analyze` | `15.693s (kernel) + 48.248s (userspace) = 1min 3.941s`, `graphical.target` after 40.8 s |
| `systemctl is-system-running` | `running`, `systemctl --failed` empty |
| gadget | `/sys/class/udc/musb-hdrc.2.auto`, `/dev/ttyGS0`, `/dev/ttyGS1` present |
| units | `fpgas-usb-console.service` and `serial-getty@ttyGS1.service` both `active` |
| sshd | reachable 96 s after power-on |

Re-enabling the port made the gadget enumerate immediately (`0525:a4a7`,
`ttyACM2`/`ttyACM3` at `1-1.2.2`) and the log resumed streaming. Note that the
board had by then been up for two minutes with `systemd.log_level=debug` and the
ring buffer had already wrapped past `Booting Linux` — exactly why the hub host
captures from enumeration rather than reading on demand.

## Check a board

`verify-pi.yml` checks an Orange Pi as it checks a Raspberry Pi, and adds: that it runs the armmp kernel from
the shared root, on `armv7l`; that the audio codec modules are not loaded; and that the board on the port carries the HAT
UUID of its row in `sunxi_boards` (`verify-pi.yml` on fpgas.online-infra main). Run it on the board's address
([Deploying to a gateway](../gateway/deploy.md#4-check) for where to run it from).

`verify-pi.yml` also has a check of the FEL-boot markers (that `/run/fpgas-felboot/` holds a marker for the USB
path of every board in `sunxi_boards` whose `host` is that host), but it runs only on a host whose own hostname
equals that `host` field, `pi-sw2-p30` (`ansible/verify-pi.yml`, infra main, read 2026-10-07). The hub host's
own hostname is `rpi5-new-13f59c` (the gateway's DHCPv6 leases, read 4 and 5 October 2026), so the check runs
nowhere today: on 6 October 2026 `verify-pi.yml` ran on ports 19, 21, 22 and 24 and skipped it on all four (the
deploy record of that day; [infra issue #240](https://github.com/fpgas-online/fpgas.online-infra/issues/240)). To check the markers, read them on the hub host as above
([The hub host](hub-host.md#the-hub-host)).

On 6 October 2026 the four boards that came back all failed one check: the HAT's ID EEPROM could not be read,
because there is no `/dev/i2c-1` (fpgas.online-infra issue #200, open). `verify-pi.yml` stops at a board's first
failure, so on these boards the HAT UUID check and every check after it do not run: until #200 is fixed, the
board's identity is not checked, and you must check its MAC yourself (below).

## Recover a board

1. **After a root update, wait first.** Each board's watchdog reboots it into the new root on its own: on
   6 October 2026 the Orange Pis were back by 08:57 Adelaide time, 36 minutes after the swap
   ([Updating the NFS root](../netboot/update-root.md)).
2. **Check the port is the board you mean.** On the gateway, for port 21:

   ```console
   $ ip -4 neigh show 10.21.2.21
   $ grep ' 10.21.2.21 ' /var/lib/misc/dnsmasq.leases
   ```

   The MAC must be the board's from [the table](../orange-pi.md#which-board-is-where). If another MAC answers,
   stop.
3. **Power-cycle its port** on switch 2 ([Power-cycling a board](../network/power-cycle.md#at-welland)). Visitors
   may be using it; a cycle ends their session. The board comes back in FEL mode on the hub host, which loads
   U-Boot into it, within about 3 s; `journalctl -u 'fpgas-felboot@*'` on the hub host shows the attempts (three
   tries, two seconds apart: `fpgas-felboot.sh`). The H3's UART0 3-pin header at 115200 8N1 is the only way to
   see U-Boot or early kernel output; the PL2303 USB serial adapter on the hub host (`1-1.1.4`) is not wired to
   any board's header, so the earliest thing anyone can see is the gadget console, which starts once Linux is
   up (from the earlier docs page, not re-checked).

   Or, with the `ngsw` CLI from `python3-netgear-switch-library` and an inventory file that holds the switch's
   write community (never paste the community into a command or a page; from the earlier docs page, not
   re-checked, and not what the welland power cycles of 5 and 6 October 2026 used):

   ```console
   $ # off, then on -- the board is back in FEL about 3 s later
   $ ngsw --config ~/.config/ngsw/inventory.toml --switch s3300-1 poe 20 off -y --force
   $ ngsw --config ~/.config/ngsw/inventory.toml --switch s3300-1 poe 20 on -y --force
   ```
4. **Not back after 5 minutes**: read its console log (above). On 2026-08-28 about one cold FEL boot in four
   crawled and never reached `sshd`, and a second cycle brought each of those back in about 90 s (the record
   on the published page of September 2026); cycle once more before calling it a fault.
5. **The hub host is down** (no board can boot): power-cycle port 30. Every board on its hub is FEL-booted
   again when it comes back.

## Known issues

Dated observations. The first three were recorded on the published page of September 2026 and have no other
record kept; the last two are recorded here.

- **Slow first boot after a cold FEL boot** (2026-08-28, three boots on p21 and p24): the board mounts the root
  but reads it so slowly that `sshd` never starts. A second power cycle fixed each one.
- **A kernel Oops in the audio codec's probe** (2026-09-02, on three boards): the codec modules are now
  blacklisted in the root (`fixpi`, `sunxi-image.yml`), and `verify-pi.yml` checks they stay unloaded.
- **Boards dying 19 to 36 minutes into uptime** (2026-09-02, four boards): no cause found; the console logs on
  the hub host will show the next one.
- **Ports 18, 20 and 23 did not answer on 6 October 2026** after the root update; not looked into.
- **No `/dev/i2c-1`**, so the HAT's ID EEPROM cannot be read (fpgas.online-infra issue #200, open).
