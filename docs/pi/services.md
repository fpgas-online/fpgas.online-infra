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

The units shipped by `fpgas-online-setup-pi` are in [Units shipped by fpgas-online-setup-pi](#units-shipped-by-fpgas-online-setup-pi),
below; how to use the USB console is under [When a Pi does not boot](../netboot/not-booting.md).

## Units shipped by fpgas-online-setup-pi

From fpgas.online-setup-pi main (units, udev rules, `nfpm.yaml`), read 2026-10-07. None of these is enabled by a
task in `onpi`; udev starts the first four.

| Unit | What it does | Started by |
|---|---|---|
| `fpgas-usb-console.service` | `dmesg --follow --color=never` onto `/dev/ttyGS0`. `dmesg` prints the whole ring buffer (the command line sets `log_buf_len=1M`) and then follows `/dev/kmsg`, so a laptop on the USB-C port gets the kernel log from the start of boot. With no host attached the gadget buffers 8 KiB and then blocks this writer only; on detach the port hangs up and the restart replays the log for the next host. The kernels lack `CONFIG_U_SERIAL_CONSOLE`, so `console=ttyGS0` would be inert. | `70-fpgas-usb-console.rules`, when `ttyGS0` appears |
| `serial-getty@ttyGS1.service` | The login console on the second gadget port. Separate from `ttyGS0` because `agetty` flushes its tty on start, which would drop queued log data. | the same rule, when `ttyGS1` appears |
| `fpgas-usb-console-log@.service` | Host side only: captures an attached board's log port to `/var/log/fpgas-usb-console/` from the first byte, because the board's ring buffer wraps within minutes under debug logging. | `71-fpgas-usb-console-host.rules`, one instance per matching `ttyACM*` (vendor `fpgas.online`, model `usb-console`, interface 00) |
| `fpgas-felboot@.service` | Host side only: `sunxi-fel uboot` into an Allwinner board that enumerated in BROM FEL mode (`1f3a:efe8`) on this Pi's USB, so a PoE-cycled Orange Pi netboots without an operator. `StopWhenUnneeded=yes`; the instance name uses `%i`, not `%I`. | `60-fpgas-felboot.rules` |
| `fpgas-pistat-ssh.service` | One-shot `curl` to `https://${pistat_host}/pistat/stat/%l/ssh/`, bound to `ssh.service`. | nothing |
| `fpgas-pistat-cam.service` | The same for `/cam/`, but ordered after `cam.target` and bound to `cam.service`, which does not exist (the unit is `fpgas-cam.service`), so it would stay inert. | nothing |
| `fpgas-pistat-info.service` | Reports the device-tree model string (`/sys/firmware/devicetree/base/model`), so the server knows which Pi model answered on that port. | nothing |
| `fpgas-pistat-shutdown.service` | `RemainAfterExit` unit whose `ExecStop` reports `/shutdown/` on the way down. | nothing |
| `fpgas-arty-here.service` | Meant to report whether an Arty is attached. `ExecStart` is `/usr/local/bin/arty_here.sh`, but the deb installs `fpgas-arty-here.sh`, which in turn calls `/usr/local/bin/arty_here.exp`, installed as `fpgas-arty-here.exp`. | nothing; would not run if it were |
| `fpgas-arty-wire.service` | Meant to check the Pi-to-Arty wiring. `ExecStart` is `/usr/local/bin/arty_wire.sh` (installed as `fpgas-arty-wire.sh`) and it is ordered `After=arty_here.target`, which does not exist. | nothing; would not run if it were |
| `fpgas-arty-blink.service` | Meant to run the Arty counter demo from `/home/pi/Demos/counter_test`. `ExecStart` is `/usr/local/bin/arty_blink.sh` (installed as `fpgas-arty-blink.sh`), ordered `After=arty_wire.target`, also nonexistent. | nothing; would not run if it were |

### The pistat and Arty units

The task files that used to enable them (`pistat.yml`, `arty_here.yml`, `arty_wire.yml`, `arty_blink.yml`) are no
longer in fpgas.online-infra main (read 2026-10-07). The fleet agent (`fpgas-fleet-agent`,
`fpgas-fleet-event@…`, in the table above) is what reports the Pi and its boot stages now.

> [!NOTE]
> Enabling the Arty three is not a one-line fix. Their unit bodies were never updated when the package took
> over installation, so all three would fail with 203/EXEC: each `ExecStart` names the pre-package script path
> while `nfpm.yaml` installs the `fpgas-`-prefixed name, and the deb has no `scripts:` block, no postinstall and
> no compatibility symlink. `arty_here.sh` has the same problem one level down, and `arty_wire` and
> `arty_blink` order themselves after `arty_here.target` and `arty_wire.target`, which exist nowhere in the
> package. The unit bodies need the `fpgas-` names and those two `After=` targets removed before enabling them
> would achieve anything. This is read from the file names, not run.

The four pistat units do not share the path problem: their `ExecStart` lines call `/usr/bin/curl` (and
`/usr/bin/bash` for `info`) directly, so enabling them is enough, except that `fpgas-pistat-cam.service` stays
inert because it is bound to `cam.service`, which was renamed `fpgas-cam.service`.

**Open decision.** Whether the pistat path is still wanted. If it is, fix the unit bodies in
[fpgas.online-setup-pi](https://github.com/fpgas-online/fpgas.online-setup-pi) and add an include in
`onpi/tasks/main.yml` that enables the `fpgas-`-prefixed units.

Two smaller oddities in the same package. The `pistat-scripts/` Python files are installed onto the Pi as
`/usr/local/bin/fpgas-pistat-*.py`, but they are server-side code: `send.py` imports `asgiref` (channels), and
`send_stat.py` is a dnsmasq `--dhcp-script` with the shebang `#!/srv/www/pib/venv/bin/python3`, a path that does
not exist on a Pi (`send_ncc.py` has the same shebang, from the earlier docs page, not re-checked). The `.link`
files that name the two Ethernet interfaces come from this package too, under [interface
naming](../network.md#interface-naming-on-the-pi).

<a id="boot-time-configuration"></a>
What `config.txt` and `cmdline.txt` set: [Boot-time configuration](boot-config.md).
