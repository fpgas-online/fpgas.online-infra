# Pi models and serial consoles

**You operate the fleet and want to know how the Raspberry Pi models in it differ where it matters (the
serial port on the 40-pin header, the console, the USB-C port), and how to free that serial port for a board,
before you wire a board to one or debug its UART.** Each row says where it is from.

## The models

| | Pi 3B+ | Pi 4 | Pi 5 | Compute Module 4 | Compute Module 5 |
|---|---|---|---|---|---|
| Where in the fleet (the site pages' tables) | welland: NeTV2 and Fomu hosts, Tiny Tapeout ASIC hosts on switch 2 ports 6 to 8; ps1: Arty pi7, pi9 | welland: Tiny Tapeout ASIC hosts on ports 3 to 5 and the FPGA hosts on 33 to 36; ps1: Arty pi3 | welland: the Acorn hosts on switch 2 | ps1: pi14, pi18 (Compute Blades) | ps1: pi16, pi20 (Compute Blades) |
| Header UART (GPIO14/15) | `ttyAMA0` once `disable-bt` frees it | `ttyAMA0` once `disable-bt` frees it | `ttyAMA0` (RP1), enabled by `dtoverlay=uart0-pi5` | `ttyAMA0`, GPIO14/15 at alt0 (read 2026-10-07) | `ttyAMA0`, GPIO14/15 at alt4 (read 2026-10-07) |
| Kernel console in welland's root | `tty1` | `tty1` | `ttyAMA10`, the Pi 5's debug UART | not booted from it | not booted from it |
| USB-C console (gadget mode) in welland's root | no: its only USB controller is the gadget one | yes (`[pi4]`) | yes (`[pi5]`) | not booted from it | not booted from it |

Sources: the 40-pin and console rows for welland are `roles/fixpi/tasks/tweeks.yml` and the two command-line
templates in fpgas.online-infra (main, read 2026-10-07); the Compute Module rows are the `pinctrl` reads of the
four ps1 blades on 2026-10-07 ([Acorns at ps1: what was read on each
blade](https://docs.fpgas.online/en/latest/boards/acorn/installations/ps1-reads.html)). The ps1 blades boot ps1's own root, with the kernel
console on `ttyAMA0` and a login on it; that is why JTAG and the FPGA's UART cannot be used there as they are.

Why the Pi 5 differs: `dtoverlay=disable-bt` frees the header UART on a Pi 0 to 4 as a side effect, but on a
Pi 5 the firmware maps it to an overlay that only touches Bluetooth, so the header UART stays off until
`uart0-pi5` turns it on (the comment in `tweeks.yml`). Turning it on would put a `console=serial0` console onto
the FPGA's UART; the Pi 5's command line therefore names `ttyAMA10`. These console rows are what the templates
serve; a `/proc/cmdline` read on each model at welland is still to do.

Which GPIO chip carries the header, for `libgpiod` tools such as openFPGALoader's `libgpiod` cable, depends on
the model and the kernel: [Acorn on a Raspberry Pi 5: the Pi's settings](https://docs.fpgas.online/en/latest/boards/acorn/wiring/rpi-5-host.html)
has what was read on the welland Pi 5s.

## Freeing the header UART for a board

When an FPGA board will drive the header UART, nothing else may hold it. In welland's root no console or
login is on it, so check that the Pi has the port and that nothing has it open. `fuser` is in the `psmisc`
package; if the root lacks it, `sudo apt install psmisc` puts it in this boot's tmpfs.

```console
$ if [ -e /dev/serial0 ]; then sudo fuser -v "$(readlink -f /dev/serial0)"; else echo "no header UART on this Pi"; fi
```

If it printed "no header UART on this Pi", stop here: there is nothing to free.

If a login is on it (a Pi booted from another root, such as a ps1 blade), stop it for this boot. `stop` alone
is not enough, because systemd starts it again; mask it, then stop it:

```console
$ GETTY="serial-getty@$(basename "$(readlink -f /dev/serial0)").service"
$ sudo systemctl mask "$GETTY"
$ sudo systemctl stop "$GETTY"
$ sudo fuser -v "$(readlink -f /dev/serial0)"   # expect nothing
```

The mask lives in the tmpfs layer, so it is gone at the next reboot. A kernel console on that UART cannot be
moved without a reboot; on ps1's blades that is the change [Acorns at
ps1](https://docs.fpgas.online/en/latest/boards/acorn/installations/ps1.html) asks for.

> [!WARNING]
> A design that transmits on the UART while the kernel console is on it does more than print noise: on a
> Compute Blade at ps1 it produced bytes the kernel read as SysRq commands, ending in a reboot ([the kernel
> console on the FPGA UART](https://docs.fpgas.online/en/latest/boards/acorn/wiring/rpi-5-host.html#kernel-console-on-the-fpga-uart)). welland's
> root sets `kernel.sysrq = 0` as well as keeping the console off that UART.

## Other serial ports on a Pi

- **A Tiny Tapeout board's own USB serial** is `/dev/ttboard`, and the `fpgas-tt` daemon holds it open while
  it runs ([The Tiny Tapeout stack](https://docs.fpgas.online/en/latest/setup/tinytapeout.html)).
- **The USB-C console** on a Pi 4 or Pi 5, and **the kernel log over the network** (`netconsole` to the
  gateway, 10.21.0.1): [When a Pi does not boot](../netboot/not-booting.md).
