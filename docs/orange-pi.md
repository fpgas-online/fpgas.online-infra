# Orange Pi H3 hosts

**You operate the welland fleet and want to know how its Orange Pi boards boot and which board is on which
port.** To read a board's console, recover one or add one, use the pages under [The tasks](#the-tasks). The
Orange Pis carry no FPGA.

## The tasks

<a id="reading-a-console"></a>
- [Reading a console, checking and recovering a board](orange-pi/recover.md).

<a id="verifying"></a>
- [Checking a board](orange-pi/recover.md#check-a-board).

<a id="recovery"></a>
- [Recovering a board](orange-pi/recover.md#recover-a-board).

<a id="known-issues"></a>
- [Known issues](orange-pi/recover.md#known-issues).

<a id="adding-a-board"></a>
- [Adding or moving a board, and deploying](orange-pi/add.md).

<a id="deploying-and-reconverging"></a>
- [Deploying](orange-pi/add.md#deploying).

<a id="the-hub-host"></a>
- [The hub host](#the-hub-host), below.

<a id="design"></a>
- [How they boot](#how-it-works), below.


## How it works

An Orange Pi PC (Allwinner H3) has no SD card or eMMC here, so at power-on its boot ROM waits in USB FEL
mode. Its OTG cable goes to the hub host, `pi-sw2-p30`, where `fpgas-felboot@.service` (from
fpgas-online-setup-pi) sees it appear and loads U-Boot into it over USB, leaving a marker in
`/run/fpgas-felboot/<usb path>`. U-Boot then netboots from the gateway: it fetches `pxelinux.cfg/default-arm-sunxi`
by TFTP, which loads Debian's armmp kernel, its initrd and the board's device tree from `sunxi/`, and mounts
the same NFS root as the Raspberry Pis, read-only with a tmpfs over it.

- The armmp kernel is installed into the shared root by the CI image build (`sunxi-image.yml`, which also
  blacklists the H3's audio codec modules); the gateway's `sunxi.yml` publishes the kernel, initrd and device
  trees to TFTP `sunxi/`, enables the header's I²C controllers in those device trees, and writes the U-Boot
  PXE configuration.
- A board's name and address follow its switch port, as for every welland Pi: `pi-sw2-p19` at `10.21.2.19`.
- After boot, the same OTG cable carries a USB serial console back to the hub host (below).

The command line has no `netconsole=` (the network driver is a module in the initrd, so the kernel's
netconsole cannot bind), and its `console=ttyS0` is the H3's debug UART header, which is not wired.

<a id="board-mapping"></a>
## Which board is where

Each board is identified by its Digilent Pmod HAT's UUID (`hat_uuid`, read from the HAT's ID EEPROM) and its
MAC; its port and USB path are where it is plugged in now, and change if it is recabled. All on switch 2, all
`orangepi-pc`, all FEL-booted from `pi-sw2-p30` (`sunxi_boards`, read from the hardware 2026-09-04):

| Port | Name | HAT UUID | MAC | USB path on the hub host |
|---|---|---|---|---|
| 18 | pi-sw2-p18 | 66196f35-58f5-4b03-b479-d9eb1f696204 | 02:81:0b:12:20:44 | 1-1.3.4 |
| 19 | pi-sw2-p19 | 547291f7-1440-4be9-a49a-c3081fa92984 | 02:81:e1:ce:7d:46 | 1-1.3.3 |
| 20 | pi-sw2-p20 | 0d05d999-1d10-49e7-b94a-8c2252816633 | 02:81:bf:f6:b7:99 | 1-1.2.2 |
| 21 | pi-sw2-p21 | 02b54c27-053b-463d-a74a-e6614129ed88 | 02:81:31:f4:6e:48 | 1-1.3.1 |
| 22 | pi-sw2-p22 | 6c12f955-093c-4272-a94d-8824363bffaa | 02:81:2e:b7:a3:4e | 1-1.3.2 |
| 23 | pi-sw2-p23 | 55fc28c9-257a-4d0d-8888-117834bd52ab | 02:81:1f:e1:45:1d | 1-1.2.3 |
| 24 | pi-sw2-p24 | 2577845e-7668-460a-a9d1-2c97373b1da9 | 02:81:f5:c0:a6:10 | 1-1.2.4 |

On 6 October 2026 four of them answered after the root update: ports 19, 21, 22 and 24, with the MACs above
([Hosts and boards at welland](https://docs.fpgas.online/en/latest/sites/welland-boards.html#what-was-up-on-6-october-2026)). Ports 18, 20 and 23
did not.

## The hub host

`pi-sw2-p30` is a Raspberry Pi with the seven OTG cables on its USB hub. fpgas-online-setup-pi gives it two
jobs: FEL-booting any Allwinner board that appears (`fpgas-felboot@.service`), and capturing each board's
kernel log from the USB serial console into `/var/log/fpgas-usb-console/<usb path>.log` from the first byte
(`fpgas-usb-console-log@.service`). If it is down, no Orange Pi can boot.

## Sources

fpgas.online-infra main, read 2026-10-07: `sunxi_boards` in `host_vars/fpgas.online.yml`, the `fixpi` tasks
`sunxi.yml` and `sunxi-image.yml`, `templates/boot/default-arm-sunxi.j2`, `verify-pi.yml`. fpgas.online-setup-pi
main: `README.md`.
