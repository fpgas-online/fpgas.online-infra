# Netboot and the NFS root

**You operate the fleet and want to know how a Pi gets its kernel and its root filesystem from the gateway,
so that you can reason about a boot problem or a change to the root.** To put a new root on a gateway, or to
find out why a Pi does not boot, go to the pages listed under [The tasks](#the-tasks).

A fleet Pi boots from the network, not from an SD card. (The Orange Pis boot the same root by another route, through a hub host: [Orange Pi H3 hosts](https://docs.fpgas.online/en/latest/setup/orange-pi.html).) Its boot ROM asks for DHCP,
fetches its firmware and kernel over TFTP, and mounts one shared, read-only NFS root from the site's gateway,
with a tmpfs on top that is thrown away at every reboot. This page describes what fpgas.online-infra builds
(main, read 2026-10-07). ps1's gateway was not built by it as it stands. The [ps1 site
page](https://docs.fpgas.online/en/latest/sites/ps1.html) describes what ps1 provides.

## The tasks

<a id="updating-a-running-fleet"></a>
- [Updating the NFS root](netboot/update-root.md): a new root on a gateway, and every Pi on it.

<a id="how-the-root-is-built"></a>
- [How the root is built](netboot/update-root.md#how-the-root-is-built): CI builds it, the gateway pulls it.

<a id="the-provisioning-container"></a>
- [The provisioning container](netboot/update-root.md#how-the-root-is-built): gone. The root used to be built
  on the gateway through a `piroot` account. The `nfsroot-build.yml` CI workflow builds it instead, and that account is deleted.

<a id="when-a-pi-does-not-boot"></a>
- [When a Pi does not boot](netboot/not-booting.md): where its boot stops.

- [What is in the NFS root](netboot/root.md): the pinned image, what `fixpi` does to the tree, the CI build.

- [EEPROM write protect](#eeprom-write-protect), below: the lock the served `config.txt` sets. [Raspberry Pi's
  wording and the checks](netboot/eeprom.md).

<a id="historical-tooling"></a>
- [Historical tooling](netboot/history.md): the repositories that came before the roles.

<a id="sources"></a>
- [Sources](netboot/sources.md): the files and records these pages come from.


## The boot chain

1. The Pi powers on and its boot ROM broadcasts DHCP.
2. dnsmasq on the gateway answers with an address and the gateway as TFTP server. It binds with
   `bind-dynamic`, which starts even when one of its interfaces is missing or down and binds each as it
   appears; with `bind-interfaces` dnsmasq aborts at start in that case (the template's comment,
   `roles/pxe/templates/dnsmasq-base.conf.j2`). welland's gateway had 88 per-port interfaces on 6 October 2026.
3. The Pi fetches its firmware, `config.txt`, `cmdline.txt`, the kernel, the device tree and the initramfs over
   TFTP.
4. The kernel mounts the NFS root read-only and puts a tmpfs over it (`overlayroot=tmpfs`).
5. Everything the Pi runs is already in that root; nothing is installed at boot.

dnsmasq also names the gateway as the NTP server: the Pis have no route to the internet, and without it their
clocks stay on the `fake-hwclock` date (`roles/pxe`; found at the welland gateway's rebuild of 2026-08-25).

## Where TFTP serves from

`tftp_root` in `inventory/group_vars/all/srv.yml`:

```jinja
tftp_root: "{{ (nfs_root ~ '/boot') if switches is defined else '/srv/tftp' }}"
```

At welland (`switches` defined) dnsmasq serves the root's own `boot/` directly. A Pi 4 or 5 asks for
`<serial>/<file>` first and, when that is not there, asks again without the prefix (Raspberry Pi's bootloader
setting `TFTP_PREFIX`, as that file's comment quotes it), so no Pi is registered by serial number: plug it into
its port and it boots.

On a MAC-table gateway, `/srv/tftp` holds one link per Pi serial number pointing at the root's `boot/`
(`roles/fixpi/tasks/netboot.yml`); adding a Pi means adding its entry to `switch.nos` in the gateway's
`host_vars` and converging, so its link is made. `/srv/tftp` also holds a `bootcode.bin` link at the top, to the
root's `boot/bootcode.bin`, because (the task's comment in `netboot.yml`) "pi netboot not too smart".
Each entry in `switch.nos` is `{port, mac, sn, model, loc, cable_color}` (the shape in `host_vars/ps1.fpgas.online.yml`, main, read 2026-10-07).

## The kernel command line

`roles/fixpi/templates/boot/cmdline.txt.j2`, as served to every Pi but a Pi 5, at welland
(`nfs_root` is `/srv/nfs/rpi/bookworm`). It is one line; here it is split at each setting:

```text
root=/dev/nfs
nfsroot=10.21.0.1:/srv/nfs/rpi/bookworm/root,nfsvers=3,tcp ro
ip=dhcp rootwait consoleblank=0
netconsole=@/eth-uplink,@10.21.0.1/
overlayroot=tmpfs
console=tty1
systemd.log_level=debug systemd.log_target=kmsg log_buf_len=1M printk.devkmsg=on
```

- **`nfsvers=3,tcp`**: the welland gateway's rebuild record (`docs/rebuilds/2026-08-25-tweed-rebuild.md` in fpgas.online-infra,
  entry C1-3c) found its NFS server (Debian 13) serving version 3 over TCP only, while the initramfs's mount
  tool defaults to UDP, so the command line asks for TCP. The root mount still failed after that; the cause
  the record names (C1-4) was an empty export table on the gateway, since fixed in the `nfs` role (fpgas.online-infra commit 7c353ec).
  The bookworm VM that CI uses does not reproduce the UDP hang, so it was found only on real hardware (rebuild
  record, 2026-08-25). The flag is in the shared template for every site, because there is one template.

- **`netconsole=@/eth-uplink,@10.21.0.1/`**: the kernel log over UDP to the gateway. It names `eth-uplink`
  because netconsole is a module (`netconsole.ko` in every Pi kernel of the root), which initramfs-tools loads
  from this option *after* udev has renamed the NIC. With no interface named, netconsole takes `eth0`, which
  no longer exists by then. A VM run on 16 October 2026 logged "netconsole: eth0 doesn't exist, aborting"
  right after "eth-uplink: renamed from eth0 (while UP)" (fpgas.online-infra #300). On a Pi 3B+ it has been
  `eth-uplink` since before #299, so the old form never bound there either.

- **`overlayroot=tmpfs`**: the writable layer ([below](#the-nfs-root-is-shared-and-read-only)).

- **`console=tty1`**: the kernel console is on the screen, not on a serial port, so bytes that an FPGA board sends on the Pi's
  serial pins never reach a console. The root also sets `kernel.sysrq = 0` (`roles/fixpi/tasks/netboot.yml`,
  "FPGA serial must not trigger SysRq").

A Pi 5 boots `cmdline-pi5.txt` instead: the same line with `console=ttyAMA10,115200`, the Pi 5's own debug
UART. `roles/fixpi/tasks/tweeks.yml` adds to the served `config.txt`:

```text
[pi5]
dtoverlay=uart0-pi5
cmdline=cmdline-pi5.txt
```

so the 40-pin header UART (`/dev/ttyAMA0`, wired to the FPGA on an Acorn host) is enabled and free of the
console. The same file adds `dtoverlay=disable-wifi`, `dtoverlay=disable-bt` and `eeprom_write_protect=1`,
and removes `enable_uart=1` and `uart_2ndstage=1`, so the firmware sends nothing to the header pins (#261).
It also puts the Pi 4's and Pi 5's USB-C port in gadget mode
([When a Pi does not boot](netboot/not-booting.md)). `config.txt` is served read-only, so these apply at every
boot.

The PS1 Compute Blades boot a separate trixie root and have `console=tty1` with `serial-getty@ttyAMA0` inactive:
[Compute blades](https://docs.fpgas.online/en/latest/sites/ps1-boards.html) (from the earlier docs page, not re-checked).

<a id="the-network-after-boot"></a>
## The network after boot: systemd-networkd

`ip=dhcp` makes the kernel take the port's address before userspace, because the NFS root is mounted over it. From
then on systemd-networkd manages the network, IPv4 and IPv6 (fpgas.online-infra #230; decided 14 October
2026). NetworkManager, `ifupdown` and `isc-dhcp-client` are purged from the root
(`roles/onpi/tasks/networkd.yml`). Before that change, NetworkManager only took over the kernel's configuration
("connected (externally)") and ran no DHCP client, so no board ever renewed its lease.

**Which interface.** The interface that carries the NFS root is called `eth-uplink` on every model: the Pi 3B+,
Pi 4B and CM4, Pi 5 and CM5, and the Orange Pi PC (fpgas.online-infra #299). Setup-pi's `11-eth-uplink.link`
renames the board's onboard NIC in the initramfs, before `configure_networking` (see [interface
naming](network.md#interface-naming-on-the-pi)), so the switches page shows `eth-uplink` as every board's LLDP
port. At each boot `fpgas-uplink-network.service` runs before networkd, takes the interface that routes to the
NFS server named in `nfsroot=`, and checks that it is `eth-uplink`. It writes
`/run/systemd/network/10-fpgas-uplink.network`, which matches that interface alone by its MAC address. The MAC is
the filter: nothing on the root creates a VLAN, bridge or veth that would share it. Every other interface stays
unmanaged, for example the USB adapter that an FPGA's Ethernet test configures for itself.

If there is no route to the server, the interface that routes there is not called `eth-uplink` (the `.link` did
not match this board), or the interface has no MAC, the unit fails and networkd manages nothing.
The kernel's address still carries the root. `systemd-networkd-wait-online` then waits its full 2 minutes for a
managed interface that never comes, so everything ordered after `network-online.target` (the fleet agent among
them) starts 2 minutes late. The failed unit is in `systemctl --failed`.

**What the file sets** (`roles/onpi/files/usr/local/sbin/fpgas-uplink-network`; the reasons are from systemd
252's source, the version in the root):

| Setting | Why |
|---|---|
| `DHCP=ipv4` | networkd's DHCP client renews the lease at T1 (half the 5-minute per-port lease), so the gateway's lease shows that the board is alive |
| `KeepConfiguration=yes` | networkd never drops the address or routes that the kernel set. It gives the DHCP address an infinite lifetime, ignores lease expiry ("DHCPv4 connection considered critical"), and keeps the address when it stops or restarts. It must be set here: networkd's own "yes on a network root" default checks `/` with `statfs`, and on these boards `/` is the tmpfs overlay (`overlayroot=tmpfs`), not NFS (systemd 252 `path_is_network_fs`) |
| `IgnoreCarrierLoss=yes` | a lost carrier would otherwise make networkd remove the address it took over from the kernel, and `KeepConfiguration=` does not cover that case |
| `ClientIdentifier=mac` | every board has the same machine-id, so the default client identifier, which is derived from it, would be the same on all of them |
| `SendRelease=no` | a networkd restart does not make the gateway forget the board |
| `UseHostname=yes`, `UseNTP=yes` | the name `pi-sw<s>-p<p>` and the time server both come from the gateway's DHCP |
| `IPv6AcceptRA=no` plus `[Address]` `<base><s, two digits>::<p>/128` and `[Route]` `Gateway=<base><s, two digits>::ffff` | the port's IPv6 address and default route are static. They are computed from the port's IPv4 address, `<net>.<s>.<p>`, and the site's `pib_network6_base` (fixpi writes it to `/etc/fpgas-online/ipv6-base`). Advertised routes are not used: the gateway refreshes its advertisements on one port interface per switch prefix only, so an advertised route lapses (PR #251) |

**Never taking the link down.** networkd must not take the uplink down or drop its address, at start, on a
restart, on a carrier event, ever. That is what killed the NFS root on the blades at ps1. Their root is a different
trixie root, which nothing in this repository builds. There, `systemd-network-generator` turned `ip=dhcp` into a
DHCP-only `.network` (in our root's systemd 252 it is `91-default.network`), and networkd's network-root default did not apply under the overlay. networkd
dropped the kernel's address as soon as it started, the root became unreachable, and the hardware watchdog reset
the boards in a loop (systemd/systemd #44156, closed upstream as not a systemd bug). The guards:

- `systemd-network-generator` is masked. Enabling networkd would otherwise enable it too, through
  `Also=` in systemd 252's unit.
- networkd `Requires=` and starts `After=` `fpgas-uplink-network.service`, so it does not start at all if our
  file was not written.
- The uplink's file sets nothing that makes networkd 252 take a link down: no `[Link] MACAddress=`, no bond,
  no `ActivationPolicy=down`, no `BindCarrier=`, no MTU.

verify-pi checks each of these:

- `networkctl status` names `10-fpgas-uplink.network` for the uplink.
- No other `.network` exists.
- The generator is masked.
- The uplink's `carrier_down_count` has not changed since before networkd started (`fpgas-uplink-network`
  records it in `/run/fpgas-uplink-network.carrier`).

The VM test also does each of these:

- It restarts networkd, with the gateway pinging every 0.2 s. No gap of more than 1 s is allowed, and the
  kernel log may show no link loss or NFS trouble.
- It runs the negative control: the generator's DHCP-only file in place of ours does lose the board.
- It boots the same root a second time and runs verify-pi again.

**The overlay root.** Everything above is in the built root (`onpi`, `fixpi`) or is regenerated under `/run` at
every boot: the uplink's `.network`, the carrier record, the leases, the state spool. Nothing on a running board
installs, enables or writes configuration. verify-pi checks the overlay's tmpfs upper layer for any file
under the network configuration's paths. Anything written there would be gone at the next boot
(fpgas.online-infra #271).

**Time.** systemd-timesyncd takes its server from the DHCP ntp-server option, which networkd passes on. That is
its one source. `timesyncd.conf.d/fpgas.conf` names the gateway only as `FallbackNTP=` (`roles/fixpi/tasks/netboot.yml`).
DNS is unchanged: `/etc/resolv.conf` is the gateway's address, which `ansible/ci-nfsroot.yml` writes, and networkd
(without systemd-resolved) never writes the file.

**State changes go to the site.** networkd-dispatcher runs `/usr/local/sbin/fpgas-network-state` at every
administrative and operational state the uplink enters: every state the dispatcher knows (`onpi_networkd_states`).
The dispatcher starts after the fleet agent, so states the uplink passed through before then are not sent. At its
start it reports only the state each interface is in (its `--run-startup-triggers`), which at boot is normally
`configured` and `routable`.
The script sends one `network-state` record to the site on the fleet event topic,
`fpgas/<site>/pi/<serial>/event`. The record holds:

- the states;
- every address with its lifetime;
- the default routes;
- the DHCP lease, including the last ACK time from networkd's lease file;
- the hostname, the serial, the MAC, and the switch and port.

The site keeps it as a boot event. A record the broker does not acknowledge, such as the one sent while the cable
is out, waits under `/run/fpgas-network-state`. It goes out ahead of the next record, and within a minute anyway
through `fpgas-network-state-flush.timer`. Each run stops after 30 s, and the dispatcher waits for it before
handling the next state change.

**What a lease line means.** A renewal changes no state, so it sends no record. The gateway's dnsmasq logs a
`DHCPACK` for every renewal, so a current lease line means the board took or renewed its address within the last
5 minutes. That is its boot only if it booted within those 5 minutes. A board running a root from before this
change never renews, and its lease line lasts only 5 minutes after it boots. verify-pi checks for a renewal: a
`DHCPACK` for the board at least 30 s after networkd last took its lease (at boot, or again after a networkd
restart).

**After the gateway's DHCP was down past the lease** the board still holds its address, so the gateway must not
ping an address before offering it: the board would answer, and dnsmasq would refuse it its own address
("no address available") until it rebooted. The gateway runs dnsmasq with `no-ping`
(`roles/pxe/templates/dnsmasq-base.conf.j2`); a per-port range has one address for one device, so the ping could
only ever find the board itself. `no-ping` applies to every range dnsmasq serves, because it has no per-range form.
So the VLAN 1 quarantine range and a site's `dhcp_range` pool (ps1's 10.21.0.128 to .254) also lose the
duplicate-address check. A device that holds one of those addresses statically could be handed the same address.
Addresses fixed by `dhcp-host` entries were never pinged.

## The NFS root is shared and read-only

There is one root per site, at `/srv/nfs/rpi/<dist>/{boot,root}` (`nfs_root`; `dist: bookworm`). Both halves
are exported read-only to the Pi network, and the Pi's own `/etc/fstab` mounts `/` and `/boot/firmware`
read-only and `noauto`. Every Pi mounts the same root.

<a id="the-export-is-read-only"></a>
The export (`roles/nfs/templates/exports.j2`):

```text
{{ nfs_root }}/boot {{ eth_local_address }}/{{ eth_local_netmask }}(ro,sync,no_subtree_check,no_root_squash)
{{ nfs_root }}/root {{ eth_local_address }}/{{ eth_local_netmask }}(ro,sync,no_subtree_check,no_root_squash)
```

The `noauto` on `/boot/firmware` has consequences all over the build: [What is in the NFS root](netboot/root.md).

> [!WARNING]
> The writable layer is a tmpfs. Everything written on a Pi is gone at the next reboot or power cycle,
> including anything copied to `/home/pi`. A bitstream that loaded a minute ago fails to open after a reboot
> because the file is not there any more: openFPGALoader prints `Open file … FAIL`. Copy it again.

Automation has to account for this. The hardware verification script in `fpgas.online-test-designs` power-cycles a
Pi to recover a Fomu whose DFU bootloader has timed out, and after the Pi comes back (roughly two minutes) it
re-uploads every file, because the tmpfs is empty again, and re-runs its pre-test, because the `serial-getty` mask
is lost too (from the earlier docs page, not re-checked).

A Pi that booted before the root was replaced keeps file handles into the old files: every replaced file
answers `Stale file handle` (`ESTALE`). That broke `dpkg-query`, and, since `authorized_keys` was among the
replaced files, key-based SSH, on every board at once (measured on `pi-sw2-p33` at welland on 2026-09-24;
`roles/nfsroot_generation/README.md`). A Pi has to reboot to use a new root: [Updating the NFS
root](netboot/update-root.md) is how that happens.

An earlier case: on 2026-08-30 an upgrade of `fpgas-online-cam` took the cameras off air on eleven boards with
`ESTALE` on the replaced files, and days later the Tiny Tapeout hosts still had `dpkg-query` reporting a stale file
handle. Only a reboot clears it. See [Known
faults](https://docs.fpgas.online/en/latest/sites/welland.html) on the Welland page (from the earlier docs page, not re-checked).

## EEPROM write protect

Users have root on the fleet Pis, on purpose (from the earlier docs page, not re-checked). Everything a user
changes on a fleet Pi's root is gone at the next reboot, but not the bootloader EEPROM: the flash holding the
Pi's second-stage bootloader and its settings (`BOOT_ORDER`, `NET_INSTALL_*` and the rest; the earlier docs
page called it the one piece of a Pi's state outside the NFS root, not re-checked). A user with root could
rewrite it with `rpi-eeprom-update`, `rpi-eeprom-config` or `flashrom` and change how the board boots
for good. (A board's own flash, an Acorn's for example, is a separate matter, on its board's pages.) So the
served `config.txt` carries `eeprom_write_protect=1` (`roles/fixpi/tasks/tweeks.yml`), which tells the
bootloader to set the flash's Write Status Register to protect the whole chip, at every boot.

<a id="per-model-effectiveness"></a>
How much that protects depends on the model (the role's own comment, and Raspberry Pi's `config.txt`
documentation, section `eeprom_write_protect`):

- **Raspberry Pi 5:** by Raspberry Pi's description the flash's `/WP` pin is pulled low by default, so the
  setting alone stops the standard tools and is a hardware lock: clearing it needs the `TP14` and `TP1` pads
  joined. Whether root on the Pi can clear it anyway has not been tried: the W25Q16JV datasheet says the `/WP`
  function is disabled when `QE=1`, which these flashes have, so it may be possible. Treat the lock as
  protection against accident and the standard tools, not as proven against a determined user (both from
  [Not yet known](https://docs.fpgas.online/en/latest/setup/bootloader-eeprom.html#not-yet-known),
  fpgas.online-docs `docs/setup/bootloader-eeprom.md` lines 152-161 at 62ec741, read 2026-10-07).
- **Raspberry Pi 4:** `/WP` (`TP5`) is not pulled low by default, so the setting stops the standard tools,
  but a root user could clear the register; pulling `TP5` low makes it a hardware lock.
- **Compute Module 4:** `/WP` is the module's `EEPROM_nWP` pin (Raspberry Pi's documentation), and what it is
  tied to depends on the carrier board. For a Compute Module 5 no source is recorded here: [Compute
  Module](https://docs.fpgas.online/en/latest/setup/bootloader-eeprom-compute-module.html) has what was measured.

The setting takes effect from the `config.txt` a board boots with: the fleet's served one for a netbooted Pi,
`/boot/firmware/config.txt` on a board that boots its own storage.

Values: `1` protects the whole flash, `0` clears the protection, `-1` (the default) does nothing. How to check
a board and how to upgrade a locked one: [a Raspberry Pi 5](https://docs.fpgas.online/en/latest/setup/bootloader-eeprom-pi5.html), [a Compute
Module](https://docs.fpgas.online/en/latest/setup/bootloader-eeprom-compute-module.html), and [what was measured](https://docs.fpgas.online/en/latest/setup/bootloader-eeprom.html).

<a id="verify"></a>
<a id="legitimately-updating-an-eeprom-later"></a>
Raspberry Pi's wording, the CI check, and when a change takes effect: [The EEPROM lock](netboot/eeprom.md).
