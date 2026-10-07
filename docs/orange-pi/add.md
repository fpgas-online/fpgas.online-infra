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
3. Read its identity once it is up: its MAC from the gateway's neighbour table for `10.21.2.<port>`, and its
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
