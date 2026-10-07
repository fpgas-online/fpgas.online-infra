# Adding or moving an Orange Pi, and deploying

**You want to add an Orange Pi PC to welland, move one to another port, or deploy a change to how they
boot.** You need the fpgas.online-infra checkout and a login on the hub host. From fpgas.online-infra main,
read 2026-10-07 (`sunxi_boards` in `host_vars/fpgas.online.yml` and its comment, `verify-pi.yml`).

## Add or move a board

A board is a row in `sunxi_boards`: its identity (`hat_uuid`, `mac`) and where it is plugged in (`switch`,
`port`, `usb`, `host`). `verify-pi.yml` checks that the board answering on each port carries its row's HAT
UUID, so a board moved without its row being changed fails the check until the row is.

1. Fit its Digilent Pmod HAT, cable its Ethernet to a free access port on switch 2 and its OTG cable to the
   hub host, `pi-sw2-p30`, and power it.
2. Read where it landed: on the hub host, the new marker in `/run/fpgas-felboot/` is its USB path. The
   inventory's own rows were read this way: PoE-cycling a port and watching which marker was rewritten.
   `sudo sunxi-fel --list` on the hub host shows the new device with its SID, and
   `ls -l /sys/bus/usb/devices/ | grep <busnum>-` gives its USB path (from the earlier docs page, not
   re-checked).
3. Read its identity once it is up: its MAC from the gateway's neighbour table for `10.21.2.<port>` (or the
   switch's MAC table, VLAN 22xx of its port), and its
   HAT UUID from the HAT's ID EEPROM. **The HAT UUID cannot be read on an Orange Pi today**: it needs
   `/dev/i2c-1`, which the boards lack (fpgas.online-infra issue #200, open). How the existing rows' UUIDs were
   read on 2026-09-04 is not recorded. Until #200 is fixed, a new board cannot be given a correct row.
4. Add or change its row in `sunxi_boards` with a pull request to fpgas.online-infra, then deploy. A row looks
   like this (port 21's):

   ```yaml
   - {hat_uuid: "02b54c27-053b-463d-a74a-e6614129ed88", mac: "02:81:31:f4:6e:48", model: orangepi-pc,
      switch: 2, port: 21, usb: "1-1.3.1", host: pi-sw2-p30}
   ```

## Deploying

A change to the Orange Pi boot (the kernel, the device trees, the U-Boot configuration) reaches them by the
ordinary whole-playbook deploy of the welland gateway, scoped only with `--limit` and `-e`, never with tags
([Deploying to a gateway](../gateway/deploy.md)): the gateway's `sunxi.yml` publishes the kernel, initrd and
device trees from the root to TFTP `sunxi/`. A change to the kernel itself comes in a new root image
([Updating the NFS root](../netboot/update-root.md)), and the boards reboot into it by their watchdog, whoever is
using them; visitors use the boards at any time, so say beforehand when you deploy.

A different sunxi board model needs its own U-Boot build, vendored in `fpgas.online-setup-pi/felboot/u-boot/`,
and possibly a per-MAC `pxelinux.cfg/01-<mac>` naming its DTB: U-Boot looks for that file first. Also, from the
earlier docs page and not re-checked: add the board to the inventory-sheet tool of `welland-ansible-rpi`
(`tools/rpi_hardware_sheet.py`: a `FPGAS_PORT_MAC` entry and a `KNOWN_BOARDS` entry) so the RPi Hardware sheet
names it, and to `hw_udev_files` in that repository's `inventory/host_vars/rpi5-new-13f59c.yml`, the source of
the hub host's [udev symlinks](hub-host.md#udev-symlinks-on-the-hub-host); without an entry the board gets only
the fallback `usb-<port>` link.

Check afterwards, on the gateway:

```console
$ # the boot payload and the PXE file
$ ls /srv/nfs/rpi/bookworm/boot/sunxi /srv/nfs/rpi/bookworm/boot/pxelinux.cfg
$ # the packages in the root the boards use -- not the hub host
$ chroot /srv/nfs/rpi/bookworm/root dpkg -l fpgas-online-setup-pi sunxi-tools
```

That `chroot` checks the shared NFS root, which is what the boards run. It says nothing about the hub host,
which has its own SD image: check `fpgas-online-setup-pi` and `sunxi-tools` there with a plain `dpkg -l` over
ssh ([The hub host](hub-host.md)).

> [!NOTE]
> A board that is already up is not in FEL mode: it presents the `0525:a4a7` USB serial gadget, not
> `1f3a:efe8`, so the felboot udev rule never matches it, nothing reboots it, and it keeps running its old copy
> of the root until its watchdog reboots it (36 minutes after the swap on 6 October 2026, 08:21 to 08:57,
> [Updating the NFS root](../netboot/update-root.md)) or its own port is power-cycled. Power-cycling the hub
> host re-triggers FEL boots only for boards that are already sitting in FEL, which is the "hub host
> unreachable" recovery: it fired for all four boards then known within the hub host's 19 s boot on 2026-08-28
> (hardware doc).
