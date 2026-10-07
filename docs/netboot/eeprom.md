# The EEPROM lock: what Raspberry Pi says, and how to check it

**You operate the fleet and want the wording of Raspberry Pi's `eeprom_write_protect` documentation, and how the lock is checked in CI and on a board.** What the lock is and why the fleet sets it is on [Netboot and the NFS root](../netboot.md#eeprom-write-protect); upgrading a locked board is on the [bootloader EEPROM pages](https://docs.fpgas.online/en/latest/setup/bootloader-eeprom.html).

## What Raspberry Pi says

From Raspberry Pi's `config.txt` documentation, section `eeprom_write_protect` (from the earlier docs page, not re-checked against the current text):

> This option must be used in conjunction with the EEPROM `/WP` pin which
> controls updates to the EEPROM `Write Status Register`. Pulling `/WP` low
> (CM4 `EEPROM_nWP` or on a Raspberry Pi 4 `TP5`) does NOT write-protect the
> EEPROM unless the `Write Status Register` has also been configured.
>
> [...]
>
> On Raspberry Pi 5 `/WP` is pulled low by default and consequently
> write-protect is enabled as soon as the `Write Status Register` is configured.
> To clear write-protect pull `/WP` high by connecting `TP14` and `TP1`.

## Checking

On a running board, `vcgencmd bootloader_config` and `sudo rpi-eeprom-update` show the protected config and the update state; the read-only steps are on [Raspberry Pi 5: check](https://docs.fpgas.online/en/latest/setup/bootloader-eeprom-pi5-check.html).

`sudo rpi-eeprom-update -a` is expected not to reach the flash on a protected board. fpgas.online has not run it; a netboot self-update against a protected Pi 5 wrote nothing and reported nothing (from the earlier docs page; see [what was measured](https://docs.fpgas.online/en/latest/setup/bootloader-eeprom.html)).

The build is checked in CI too: `verify-server.yml` asserts that the built NFS-root `config.txt` contains `eeprom_write_protect=1` (from the earlier docs page, not re-checked against `verify-server.yml`).

A change to the setting takes effect when a board next netboots the rebuilt image. Confirm on one board before relying on it fleet-wide.
