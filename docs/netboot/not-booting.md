# When a Pi does not boot

**A fleet Pi does not come back after a power cycle or an update, and you want to find where its boot
stops.** You need a login on the site's gateway. A netbooted Pi has no disk to look at; these are the ways to
watch one boot, in the order to try them. The Orange Pis boot the same root by another route: [Orange Pi H3
hosts](https://docs.fpgas.online/en/latest/setup/orange-pi.html). They take the same DHCP, TFTP root and NFS
root, so the lease check (1) applies to them unchanged; the netconsole and gateway-serial methods (3, 7) do not,
because their command line carries no `netconsole=` and no UART of theirs is wired to the gateway. Their console is
the USB gadget log captured on their hub host (from the earlier docs page, not re-checked).

## 1. Did it get an address?

dnsmasq logs every DHCP exchange (`log-dhcp`) to the gateway's journal, and its lease file is
`/var/lib/misc/dnsmasq.leases` (`roles/pxe/templates/dnsmasq-base.conf.j2`, fpgas.online-infra main). The absolute
path is deliberate: dnsmasq daemonises and `chdir()`s to `/` before opening a relative lease, pid or log path, and
silently fails if one is given relative (from the earlier docs page, not re-checked). On the
gateway, for the Pi on welland's switch 2, port 46:

```console
$ # by the Pi's MAC (from Hosts and boards at welland): a Pi with no address has only its MAC
$ sudo journalctl -u dnsmasq --since -15min | grep -i dhcp | grep -i 88:a2:9e:45:85:77
$ # or every DHCP line on the port's own interface (switch 2, port 46 is VLAN 2246, v2246)
$ sudo journalctl -u dnsmasq --since -15min | grep -i dhcp | grep v2246
```

No DHCP at all from the port: the Pi has no power or no link (read the port's PoE state, [Power-cycling a
board](../network/power-cycle.md)), or the port is not in its VLAN ([Converging the switches](../network/switches.md)).

## 2. Did it fetch its files?

The TFTP log is in the same journal: each file sent, and each file not found.

```console
$ sudo journalctl -u dnsmasq --since -15min | grep -i tftp | grep 10.21.2.46
```

A Pi 4 or 5 asks for `<serial>/start4.elf` and then, finding nothing, the same names without the prefix
([Where TFTP serves from](../netboot.md#where-tftp-serves-from)); those "not found" lines are normal. A Pi 5 (and a CM5)
also asks for `kernel_2712.img` and, not finding it, fetches `kernel8.img`: that is what the ps1 gateway's log
showed for a CM5 on 5 October 2026. The same files fetched again every two minutes or
so means the Pi is rebooting in a loop: the kernel starts and something later fails. That was seen at ps1 on
5 October 2026 ([The ps1 gateway and switch](https://docs.fpgas.online/en/latest/sites/ps1-gateway.html)).

## 3. The kernel's own log, over the network

The kernel command line carries `netconsole=@/,@10.21.0.1/`, which sends the kernel log to the gateway over
UDP ([The kernel command line](../netboot.md#the-kernel-command-line)). Listen on the gateway (port 6666 is
netconsole's default) while the Pi boots:

```console
$ nc -u -l 6666
```

The gateway's rebuild log of 2026-08-25 records this both failing and working within one night: try it, do
not rely on it. That log's entry C1-3 calls netconsole's dynamic cmdline form "a dead cmdline feature" that never
transmits, and C1-3b then reports netconsole working on the next boot; the log's fallback was to plant a unit in the
NFS root that streams the journal early.

## 4. A console on the Pi's USB-C port (Pi 4 and Pi 5)

The served `config.txt` puts a Pi 4's or Pi 5's USB-C port in gadget mode (`dtoverlay=dwc2,dr_mode=peripheral`,
`roles/fixpi/tasks/tweeks.yml`). fpgas-online-setup-pi's USB console then offers two serial ports on that
cable: the kernel log on the Pi's `ttyGS0` and a login on `ttyGS1` (`verify-pi.yml` checks
`serial-getty@ttyGS1`). A laptop plugged into the port sees them as USB serial devices (on Linux, `/dev/ttyACM*`);
fpgas.online has not yet read a fleet Pi this way. The fleet Pis take their power over PoE, so the port is
free. Not on a Pi 3 or a Zero: there the gadget controller is the only USB controller.

## 5. Stale files after an update

A Pi that is up but fails new logins or commands with `Stale file handle` booted the root before it was
replaced ([The NFS root is shared and read-only](../netboot.md#the-nfs-root-is-shared-and-read-only)). Its
watchdog reboots it on its own; power-cycle it to have it back sooner.

## 6. It gets an address and its files, and still does not come up

Then the kernel or the root fails after TFTP. The kernel's own log (3) or the USB console (4) shows where it
stops. A Pi that fetches its files again every couple of minutes is in a boot loop. Leaving its port switched off
needs the site owner's word; report it with what the journal shows.

## 7. Serial from the gateway

`roles/fixpi/tasks/tweeks.yml` sets the gateway up as the watching station: it installs `tio`, adds the operator
account to the `dialout` group so `tio` can open the tty, masks `serial-getty@ttyAMA0` so a getty does not eat the
boot messages, and removes `brltty`, which grabs serial ports greedily and makes `ftdi_sio` lose the connection
([Debian #667616](https://bugs.debian.org/667616)). When the gateway is itself a Pi, it also adds
`dtoverlay=disable-bt` to the gateway's own `/boot/firmware/config.txt` to free `/dev/serial0`. This needs a UART
of the Pi wired to the gateway; the Orange Pis have none (from the earlier docs page; the `tweeks.yml` tasks read on main, 2026-10-07).
