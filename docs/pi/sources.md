# Where each fact on the Pi pages comes from

**You operate the fleet and want to check a fact on the Pi pages against the file it was read from, or to know
which file to change.** These are the files behind [What runs on a Pi host](../pi.md), [Services on a Pi host](services.md), [Units shipped by
fpgas-online-setup-pi](setup-pi-units.md), [Boot-time configuration](boot-config.md) and [Pi models and serial consoles](models.md). The list is from the earlier docs page,
corrected against fpgas.online-infra main, fpgas.online-setup-pi main, fpgas.online-cam main and
fpgas.online-tt main, read 2026-10-07.

## fpgas.online-infra

- [`ansible/site.yml`](../../ansible/site.yml): the "Update the Pi NFS root" play. `img` pulls the CI-built
  root and `fixpi` applies the site layer. `ansible/ci-nfsroot.yml` runs `fpgas_apt`, `cam_pi` and `onpi` in CI.
  The old `piroot` chroot target is gone.
- [`ansible/roles/onpi/tasks/main.yml`](../../ansible/roles/onpi/tasks/main.yml): the include list (`apt.yml`,
  `nonfs.yml`, `tt.yml`, `fleet.yml`, `tftpd.yml`, `tweeks.yml`, `litepcie.yml`, `fpga_verify.yml`,
  `stale_root.yml`), the comment saying the site owns the root's `authorized_keys`, and the
  `fpgas-online-setup-pi` install, whose comment lists the pistat reporter scripts and services and the Arty
  detection services it provides. The role no longer carries separate pistat or Arty enablement tasks: the files
  `pistat.yml`, `arty_here.yml`, `arty_wire.yml`, `arty_blink.yml` and `tmux.yml` are gone.
- [`ansible/roles/onpi/tasks/apt.yml`](../../ansible/roles/onpi/tasks/apt.yml): the Debian package list, the
  `lldpd` rationale, the `vim-tiny` removal, and the pinned `pipx` locations for `mpremote` and `uv`.
- [`ansible/roles/onpi/tasks/tt.yml`](../../ansible/roles/onpi/tasks/tt.yml): `fpgas-online-tt` and
  `fpgas-online-tt-demos` at `state: latest`, gated on `tt_install` or `tt_boards`; `fpgas-tt.service` enabled
  when `tt_boards` is defined; the removal of `tt-boards.yaml`.
- [`ansible/roles/onpi/tasks/tftpd.yml`](../../ansible/roles/onpi/tasks/tftpd.yml),
  [`nonfs.yml`](../../ansible/roles/onpi/tasks/nonfs.yml) and
  [`tweeks.yml`](../../ansible/roles/onpi/tasks/tweeks.yml): the atftpd port rewrite and `/srv/tftp` ownership,
  the `nfsvers=4.2` safety net, and the `pi` home directories.
- [`ansible/roles/fixpi/tasks/tweeks.yml`](../../ansible/roles/fixpi/tasks/tweeks.yml): every `config.txt` line
  on the Services page and the reasoning behind each, plus the `pistat_host` entry in `/etc/environment`, the
  deleted `console-setup.service`, `wifi-check.sh` and `/etc/hostname`, and the masked `networking.service` and
  `ifupdown-pre.service`.
- [`ansible/roles/fixpi/tasks/netboot.yml`](../../ansible/roles/fixpi/tasks/netboot.yml):
  `fpgas-hostname-hosts.service` and its `multi-user.target.wants` symlink, the `timesyncd.conf.d/fpgas.conf`
  drop-in pointing at the gateway, and the `userconfig.service` mask.
- [`ansible/roles/fixpi/files/fpgas-hostname-hosts.service`](../../ansible/roles/fixpi/files/fpgas-hostname-hosts.service)
  and [`fpgas-hostname-hosts.sh`](../../ansible/roles/fixpi/files/fpgas-hostname-hosts.sh): the `sudo`/DNS stall
  this exists to prevent, and why the logic is in a script rather than in `ExecStart`.
- [`ansible/roles/fixpi/templates/boot/config.txt.j2`](../../ansible/roles/fixpi/templates/boot/config.txt.j2):
  the base file, with `core_freq` commented out.
- [`ansible/roles/fixpi/templates/boot/cmdline.txt.j2`](../../ansible/roles/fixpi/templates/boot/cmdline.txt.j2)
  and [`cmdline-pi5.txt.j2`](../../ansible/roles/fixpi/templates/boot/cmdline-pi5.txt.j2):
  `console=serial0,115200` versus `console=ttyAMA10,115200`.
- [`ansible/roles/cam_pi/tasks/main.yml`](../../ansible/roles/cam_pi/tasks/main.yml): the GStreamer package set
  and the `fpgas-cam.service` enable.
- [`ansible/roles/stream_server/templates/live-hls.conf.j2`](../../ansible/roles/stream_server/templates/live-hls.conf.j2)
  and [`nginx-rtmp.conf.j2`](../../ansible/roles/stream_server/templates/nginx-rtmp.conf.j2): the `/live` HLS
  location and its `no-cache` header.
- [`ansible/roles/fpgas_apt/tasks/main.yml`](../../ansible/roles/fpgas_apt/tasks/main.yml) and
  [`defaults/main.yml`](../../ansible/roles/fpgas_apt/defaults/main.yml): the repository URL, suite and
  dearmoured keyring.
- [`ansible/inventory/group_vars/all/ci.yml`](../../ansible/inventory/group_vars/all/ci.yml): `tftpd_port: 6069`.
- [`TECHDEBT.md`](../../TECHDEBT.md): items 2 and 3, the `core_freq` PoE-versus-camera trade.

## fpgas.online-setup-pi

- [`nfpm.yaml`](https://github.com/fpgas-online/fpgas.online-setup-pi/blob/main/nfpm.yaml): the authoritative
  list of what the deb installs and where, including the `fpgas-`-prefixed script and unit names and the
  `pistat-scripts` destinations; the `depends:` list (`sunxi-tools`, `zsh`, `tmux`, `vim`, `expect`, `python3`,
  `python3-paho-mqtt`), which is the only route by which `expect` reaches a Pi; and the absence of any
  `scripts:` block, so there is no postinstall to symlink the old script names.
- [`README.md`](https://github.com/fpgas-online/fpgas.online-setup-pi/blob/main/README.md): the summary of what
  the package provides and the directory layout.
- [`usb-console/70-fpgas-usb-console.rules`](https://github.com/fpgas-online/fpgas.online-setup-pi/blob/main/usb-console/70-fpgas-usb-console.rules),
  [`71-fpgas-usb-console-host.rules`](https://github.com/fpgas-online/fpgas.online-setup-pi/blob/main/usb-console/71-fpgas-usb-console-host.rules),
  [`fpgas-usb-console.service`](https://github.com/fpgas-online/fpgas.online-setup-pi/blob/main/usb-console/fpgas-usb-console.service),
  [`fpgas-usb-console-log@.service`](https://github.com/fpgas-online/fpgas.online-setup-pi/blob/main/usb-console/fpgas-usb-console-log@.service)
  and [`fpgas-usb-console.conf`](https://github.com/fpgas-online/fpgas.online-setup-pi/blob/main/usb-console/fpgas-usb-console.conf):
  the two gadget ports, why the log and the getty are separate, and the host-side capture.
- [`felboot/fpgas-felboot@.service`](https://github.com/fpgas-online/fpgas.online-setup-pi/blob/main/felboot/fpgas-felboot@.service),
  [`60-fpgas-felboot.rules`](https://github.com/fpgas-online/fpgas.online-setup-pi/blob/main/felboot/60-fpgas-felboot.rules)
  and [`fpgas-felboot.sh`](https://github.com/fpgas-online/fpgas.online-setup-pi/blob/main/felboot/fpgas-felboot.sh):
  the `1f3a:efe8` match, the `%i` escaping note, and the retry and marker behaviour.
- The `onpi/` unit files
  ([`pistat_ssh.service`](https://github.com/fpgas-online/fpgas.online-setup-pi/blob/main/onpi/pistat_ssh.service),
  [`pistat_cam.service`](https://github.com/fpgas-online/fpgas.online-setup-pi/blob/main/onpi/pistat_cam.service),
  [`pistat_info.service`](https://github.com/fpgas-online/fpgas.online-setup-pi/blob/main/onpi/pistat_info.service),
  [`pistat_shutdown.service`](https://github.com/fpgas-online/fpgas.online-setup-pi/blob/main/onpi/pistat_shutdown.service),
  [`is_arty/arty_here.service`](https://github.com/fpgas-online/fpgas.online-setup-pi/blob/main/onpi/is_arty/arty_here.service),
  [`is_wire/arty_wire.service`](https://github.com/fpgas-online/fpgas.online-setup-pi/blob/main/onpi/is_wire/arty_wire.service),
  [`arty_blink/arty_blink.service`](https://github.com/fpgas-online/fpgas.online-setup-pi/blob/main/onpi/arty_blink/arty_blink.service)):
  what each reports, the `ExecStart` paths that no longer match what `nfpm.yaml` installs, the `curl`-only
  pistat `ExecStart` lines, the `cam.target`/`cam.service` references in `pistat_cam.service`, and the
  `arty_here.target` and `arty_wire.target` orderings. The fleet units are in `onpi/fleet/`.
- [`onpi/is_arty/arty_here.sh`](https://github.com/fpgas-online/fpgas.online-setup-pi/blob/main/onpi/is_arty/arty_here.sh):
  its call to `/usr/local/bin/arty_here.exp`, installed as `fpgas-arty-here.exp`.
- [`pistat-scripts/send.py`](https://github.com/fpgas-online/fpgas.online-setup-pi/blob/main/pistat-scripts/send.py),
  [`send_stat.py`](https://github.com/fpgas-online/fpgas.online-setup-pi/blob/main/pistat-scripts/send_stat.py)
  and [`send_ncc.py`](https://github.com/fpgas-online/fpgas.online-setup-pi/blob/main/pistat-scripts/send_ncc.py):
  the Django and dnsmasq imports and the `/srv/www/pib/venv` shebang that make these server-side code. The deb
  now also installs `send_serial.py` and `ncc.py`.

## Other repositories

- fpgas.online-cam: [`README.md`](https://github.com/fpgas-online/fpgas.online-cam/blob/main/README.md) (the
  four scripts and what the deb installs), [`cam.service`](https://github.com/fpgas-online/fpgas.online-cam/blob/main/cam.service)
  (`ExecStart=/usr/local/bin/fpgas-gst-libcam.sh`, `Restart=always`),
  [`gst-libcam.sh`](https://github.com/fpgas-online/fpgas.online-cam/blob/main/gst-libcam.sh) (the pipeline, the
  `v4l2h264enc`-versus-`x264enc` choice, the RTMP destination derived from the default route, and the latency
  budget with the 2026-08-30 measurement) and [`nfpm.yaml`](https://github.com/fpgas-online/fpgas.online-cam/blob/main/nfpm.yaml)
  (the installed paths and the `fpgas-cam.service` name).
- fpgas.online-tt: [`README.md`](https://github.com/fpgas-online/fpgas.online-tt/blob/main/README.md) (the
  daemon, `/dev/ttboard`, the demo directory, and the note that every Pi runs it),
  [`debian/fpgas-tt.service`](https://github.com/fpgas-online/fpgas.online-tt/blob/main/debian/fpgas-tt.service)
  and [`debian/60-fpgas-tt.rules`](https://github.com/fpgas-online/fpgas.online-tt/blob/main/debian/60-fpgas-tt.rules)
  (the unit's user, group and arguments, and the `2e8a:0005` / `2e8a:000f` symlink rule).
- fpgas.online-site: [`pibfpgas/src/pibfpgas/templates/fpga.html`](https://github.com/fpgas-online/fpgas.online-site/blob/main/pibfpgas/src/pibfpgas/templates/fpga.html),
  the `vlc https://<domain>/live/pi<N>.m3u8` line offered on each board page (from the earlier docs page, not
  re-checked).
- fpgas.online-test-designs: [`docs/verify-hardware.md`](https://github.com/fpgas-online/fpgas.online-test-designs/blob/main/docs/verify-hardware.md),
  "Pre-Test Commands": why `mask` and not `stop`, the `rmmod spidev spi_bcm2835` GPIO 7 to 11 clash, the Pi 5
  `pinctrl set 14 a4` restoration, and the note that a PoE cycle loses the mask.
