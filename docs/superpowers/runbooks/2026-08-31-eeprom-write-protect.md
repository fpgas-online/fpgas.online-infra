# Bootloader EEPROM write protection

**Status:** enabled via `eeprom_write_protect=1` in the netboot `config.txt`
(`ansible/roles/fixpi/tasks/tweeks.yml`).

## Why

The fleet Pis netboot from a read-only NFS root with a tmpfs overlay: everything
a user changes is reverted on reboot, and root access is deliberately available.
The bootloader EEPROM (the SPI flash holding the second-stage bootloader and its
config — `BOOT_ORDER`, `NET_INSTALL_*`, etc.) is the **one piece of per-board
state that does not live in the NFS root and therefore does not revert**. Without
protection, a user with root can `rpi-eeprom-update`/`rpi-eeprom-config` (or
`flashrom` directly) and leave a persistent change to how the board boots. This
is the only persistent-tampering surface on an otherwise ephemeral device, so it
must be locked.

## What the setting does

`eeprom_write_protect=1` in `config.txt` tells the bootloader to configure the
SPI flash **Write Status Register** to protect the entire device. Because the
served `config.txt` is read-only (TFTP root) it is re-applied on every boot.

From the official `config.txt` documentation:

> This option must be used in conjunction with the EEPROM `/WP` pin which
> controls updates to the EEPROM `Write Status Register`. Pulling `/WP` low
> (CM4 `EEPROM_nWP` or on a Raspberry Pi 4 `TP5`) does NOT write-protect the
> EEPROM unless the `Write Status Register` has also been configured.
>
> On Raspberry Pi 5 `/WP` is pulled low by default and consequently
> write-protect is enabled as soon as the `Write Status Register` is configured.
> To clear write-protect pull `/WP` high by connecting `TP14` and `TP1`.

Values: `1` = protect entire EEPROM, `0` = clear protection, `-1` = do nothing
(default).

## Per-model effectiveness

| Model | `/WP` default | Effect of `eeprom_write_protect=1` |
|-------|---------------|-------------------------------------|
| **Pi 5** (BCM2712) | pulled **low** by default | **Hardware-enforced immediately.** Even root cannot clear the Write Status Register or reflash without physically bridging `TP14`↔`TP1`. Tamper-resistant. |
| **Pi 4** (BCM2711) | `TP5`, **not asserted** by default | Blocks the standard tooling (`rpi-eeprom-update`, `rpi-eeprom-config --apply`) and accidental changes, but a determined root user could clear the Write Status Register. **Pull `TP5` low** to make it hardware-enforced. |

So on the Pi 5 the software setting alone is a real lock; on the Pi 4 it raises
the bar and, combined with grounding `TP5`, becomes a real lock too.

## Verify

On a running board:

```bash
# Should show the protected config and refuse to write.
vcgencmd bootloader_config
sudo rpi-eeprom-update            # reports current state
sudo rpi-eeprom-update -a         # on a protected board the flash write fails
```

The build is checked in CI: `verify-server.yml` asserts the built NFS-root
`config.txt` contains `eeprom_write_protect=1`.

## Legitimately updating an EEPROM later

Because protection is re-applied from the read-only image every boot, you cannot
just clear it on the board. To update a board's bootloader EEPROM:

1. On the server, temporarily set `eeprom_write_protect=0` (or `-1`) in the
   served `config.txt` — either fleet-wide in `fixpi` or for one board via a
   per-board boot config — and rebuild/deploy.
2. Reboot the target board so it boots with protection cleared.
   - On a **Pi 4** with `TP5` grounded, or a **Pi 5**, you must additionally undo
     the hardware assertion (Pi 5: bridge `TP14`↔`TP1`) before the flash will
     accept writes.
3. `sudo rpi-eeprom-update -a` (or `rpi-eeprom-config --apply`), reboot, confirm.
4. Restore `eeprom_write_protect=1` in the served `config.txt`, redeploy, reboot.

## Rollout note

Takes effect when a board next netboots the rebuilt image. The bootloader reads
`eeprom_write_protect` from `config.txt`; on netboot that is the TFTP-served
copy. Confirm on one board (reboot, then `sudo rpi-eeprom-update -a` should fail
to write) before relying on it fleet-wide.
