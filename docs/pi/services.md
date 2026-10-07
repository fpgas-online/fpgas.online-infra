# Services on a Pi host

**You operate the fleet and want to know which systemd units run on every welland Pi, to understand or
change one.** This is the root fpgas.online-infra builds (main, read 2026-10-07: the
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
| `systemd-timesyncd.service` | `fixpi` (a drop-in) | Time from the gateway: the Pis have no internet and `timesyncd` does not reliably consume the DHCP ntp-server option, so without the drop-in every Pi's clock sits on the `fake-hwclock` date (comment in `fixpi/tasks/netboot.yml`) | every Pi |
| `atftpd.socket` | the Debian package; `onpi`, `tftpd.yml`, moves its port off 69 | TFTP on the Pi | every Pi |
| `lldpd.service` | the Debian package | Names the Pi to its switch over LLDP | every Pi |

`fixpi` also masks the first-boot wizard (`userconfig.service`), which would block the boot because it cannot
see `/boot/firmware`, and the root-resize and swap services, which make no sense on a read-only NFS root.

The units get into the root through the include chain of `roles/onpi/tasks/main.yml`: `apt.yml`, the
`fpgas-online-setup-pi` install, `nonfs.yml`, `tt.yml`, `fleet.yml`, `tftpd.yml`, `tweeks.yml`, `litepcie.yml`
(only when `onpi_litepcie_modules`), `fpga_verify.yml`, `stale_root.yml` (read 2026-10-07). The site owns the
root's `authorized_keys` and the image carries none, so there is no key task.

<a id="units-shipped-by-fpgas-online-setup-pi"></a>
<a id="the-pistat-and-arty-units"></a>
The units shipped by `fpgas-online-setup-pi`, and why the pistat and Arty units do not run, are on [Units shipped by
fpgas-online-setup-pi](setup-pi-units.md); how to use the USB console is under [When a Pi does not
boot](../netboot/not-booting.md).

<a id="boot-time-configuration"></a>
What `config.txt` and `cmdline.txt` set: [Boot-time configuration](boot-config.md).
