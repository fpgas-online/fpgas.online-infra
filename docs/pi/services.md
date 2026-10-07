# Services and boot settings on a Pi host

**You operate the fleet and want to know which systemd units run on every welland Pi and what its boot files
set, to understand or change one.** This is the root fpgas.online-infra builds (main, read 2026-10-07: the
roles `onpi`, `cam_pi` and `fixpi`), which welland's Pis boot; ps1's blades boot another root ([The ps1
gateway and switch](https://docs.fpgas.online/en/latest/sites/ps1-gateway.html)). What is installed is on [What runs on a Pi host](../pi.md).

## The units the root enables

| Unit | Enabled by | What it does | On which Pi |
|---|---|---|---|
| `fpgas-verify.service` | `onpi`, `fpga_verify.yml` | The boot check of the Pi's FPGA board; publishes its result to the site ([fpgas-verify](https://docs.fpgas.online/en/latest/verify/fpgas-verify.html)) | every Pi |
| `fpgas-cam.service` | `cam_pi` | The camera stream ([Camera](https://github.com/fpgas-online/fpgas.online-cam/blob/main/docs/camera.md)); stays stopped when the Pi has no camera | every Pi |
| `fpgas-tt.service` | `onpi`, `tt.yml` | The Tiny Tapeout daemon, `fpgas-tt --device /dev/ttboard`; it reads no board catalogue | a site that runs the Tiny Tapeout site (`tt_boards` defined; welland) |
| `fpgas-fleet-agent.service` and `fpgas-fleet-event@…` (network-online, time-synced, ssh-up, cam-streaming, tt-daemon-up) | `onpi`, `fleet.yml` | Report the Pi and its boot stages to the site's server; inert until the site writes `/etc/fpgas-online/fleet.toml` | every Pi, once the site writes that file |
| `nfsroot-watchdog` units | the `nfsroot-watchdog` package, `onpi`, `stale_root.yml` | Reboot the Pi after a root update ([Updating the NFS root](../netboot/update-root.md)) | every Pi |
| `fpgas-hostname-hosts.service` | `fixpi` | Writes the Pi's DHCP name into `/etc/hosts`, so `sudo` does not wait on DNS | every Pi |
| `ssh.service` | `fixpi` | Remote login | every Pi |
| `systemd-timesyncd.service` | `fixpi` (a drop-in) | Time from the gateway | every Pi |
| `atftpd.socket` | the Debian package; `onpi`, `tftpd.yml`, moves its port off 69 | TFTP on the Pi | every Pi |
| `lldpd.service` | the Debian package | Names the Pi to its switch over LLDP | every Pi |

`fixpi` also masks the first-boot wizard (`userconfig.service`), which would block the boot because it cannot
see `/boot/firmware`, and the root-resize and swap services, which make no sense on a read-only NFS root.

The units shipped by `fpgas-online-setup-pi` for the USB console are under [When a Pi does not
boot](../netboot/not-booting.md).

## Boot-time configuration

The firmware reads `config.txt` and `cmdline.txt` from the read-only TFTP root at every boot, so a root user
can change the running Pi for one session but never across a reboot. `roles/fixpi/tasks/tweeks.yml` adds to the
served `config.txt`:

```text
dtoverlay=disable-wifi
dtoverlay=disable-bt
enable_uart=1
uart_2ndstage=1
eeprom_write_protect=1
[pi5]
dtoverlay=uart0-pi5
cmdline=cmdline-pi5.txt
[pi4]
dtoverlay=dwc2,dr_mode=peripheral
[pi5]
dtoverlay=dwc2,dr_mode=peripheral
[all]
```

What each line does, the two command lines (`console=tty1`; `console=ttyAMA10,115200` on a Pi 5) and the
EEPROM lock: [Netboot and the NFS root](../netboot.md#the-kernel-command-line).
