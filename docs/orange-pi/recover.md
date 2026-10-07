# Reading, checking and recovering an Orange Pi

**An Orange Pi at welland is misbehaving or did not come back, and you want its console, a check of its
health, and the way to get it back.** You need logins on the gateway and on the hub host, `pi-sw2-p30`.
Which board is on which port, with its HAT UUID and MAC: [Orange Pi H3 hosts](../orange-pi.md#which-board-is-where).

## Read its console

There is no early console you can reach: the H3's debug UART is not wired, and the kernel's netconsole cannot
bind on these boards. Once Linux is up, the board's OTG cable carries a USB serial console to the hub host,
which records it from the first byte. On `pi-sw2-p30`, by the board's USB path (port 21 is `1-1.3.1`):

```console
$ less /var/log/fpgas-usb-console/1-1.3.1.log
$ ls /run/fpgas-felboot/                  # one marker per board U-Boot was loaded into this boot
$ journalctl -u 'fpgas-felboot@*'         # the FEL-boot attempts
```

(fpgas-online-setup-pi `README.md`; the marker is what `verify-pi.yml` checks for each board.)

## Check a board

`verify-pi.yml` checks an Orange Pi as it checks a Raspberry Pi, and adds: that it runs the armmp kernel from
the shared root; that the hub host has a FEL-boot marker for every board it serves; that the audio codec
modules are not loaded; and that the board on the port carries the HAT UUID of its row in `sunxi_boards`
(`verify-pi.yml` on fpgas.online-infra main). Run it on the board's address ([Deploying to a
gateway](../gateway/deploy.md#4-check) for where to run it from).

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
   U-Boot into it; `journalctl -u 'fpgas-felboot@*'` on the hub host shows it.
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
