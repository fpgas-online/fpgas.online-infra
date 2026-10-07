# What runs on a Pi host

**You operate the fleet and want to know what is installed on every welland Pi, and which page has its
services, its model differences and its camera.** Every welland Pi boots the same read-only NFS root, the one
fpgas.online-infra builds ([Netboot and the NFS root](netboot.md)); nothing is installed at boot. ps1's
blades boot another root, with none of these packages ([The ps1 gateway and switch](https://docs.fpgas.online/en/latest/sites/ps1-gateway.html)). CI builds that root, the gateway pulls it, and the
gateway's `fixpi` role adds only the site layer: the `pi` password, the `authorized_keys` files and the SSH
host keys ([Updating the NFS root](netboot/update-root.md#how-the-root-is-built), [Accounts and
logins](https://docs.fpgas.online/en/latest/setup/access.html)).

## The other pages

<a id="services"></a>
- [Services and boot settings](pi/services.md): the systemd units, and what `config.txt` and `cmdline.txt` set.

<a id="boot-time-configuration"></a>
- [Boot-time configuration](pi/services.md#boot-time-configuration).

<a id="model-differences"></a>
- [Model differences and serial consoles](pi/models.md): Pi 3B+, Pi 4, Pi 5, CM4 and CM5, and freeing the header UART.

<a id="raspberry-pi-5"></a>
- [Raspberry Pi 5](pi/models.md#the-models).

<a id="compute-module-4-versus-compute-module-5"></a>
- [Compute Module 4 versus Compute Module 5](pi/models.md#the-models).

<a id="raspberry-pi-3-and-3b"></a>
- [Raspberry Pi 3 and 3B+](pi/models.md#the-models).

<a id="serial-consoles"></a>
- [Serial consoles](pi/models.md#freeing-the-header-uart-for-a-board).

<a id="camera"></a>
- [Camera](https://github.com/fpgas-online/fpgas.online-cam/blob/main/docs/camera.md).

- [Where each fact on these pages comes from](pi/sources.md): the files in the infra, setup-pi, cam, tt and test-designs repositories.


## Packages

From fpgas.online-infra main, read 2026-10-07: the roles `fpgas_apt` (the repositories), `onpi` (`tasks/apt.yml`,
`main.yml`, `fpga_verify.yml`, `litepcie.yml`, `tt.yml`, `stale_root.yml`) and `cam_pi`. They run in the CI
build of the root, never on a running Pi.

**The fpgas.online repositories.** `fpgas_apt` adds `https://fpgas.online/apt` (suite `bookworm`; its key is
refused unless its fingerprint is `878EC72910E22EA0E989EAA9BEB0EC3124E538E4`; the key is dearmoured into
`/usr/share/keyrings/fpgas-online.gpg` and the source is suite `bookworm`, component `main`), the
[fpgas.online-fpga-tools](https://github.com/fpgas-online/fpgas.online-fpga-tools) archive, the rpi-hwid
archive and the nfsroot-watchdog archive, through the gateway's apt cache where there is one. How the main
repository is built is on [Packages](https://docs.fpgas.online/en/latest/packages.html).

| Package | Role, task | What it is |
| --- | --- | --- |
| `fpgas-online-setup-pi` | `onpi`, `main.yml` | The Pi's own units and files: the USB gadget console, the `.link` interface names, the login banner scripts, the shell skeleton, the sshd drop-in. |
| `fpgas-online-all-boards` | `onpi`, `fpga_verify.yml` | The boot check, `fpgas-verify`, for every board kind, and the test images it checks against ([Checking a board: fpgas-verify](https://docs.fpgas.online/en/latest/verify/fpgas-verify.html)). |
| `fpgas-online-acorn-litepcie-common`, `-utils`, and the driver for each kernel in the root that a Pi 5 or CM5 boots (6.12 on) | `onpi`, `litepcie.yml` | The LitePCIe driver and tools for an Acorn on a Pi 5 or a CM5; the build stops if the root has no such kernel the driver is packaged for. |
| `fpgas-online-tt`, `fpgas-online-tt-demos` | `onpi`, `tt.yml` | The Tiny Tapeout daemon `fpgas-tt`, which owns `/dev/ttboard` and offers it as a WebSocket on port 8765, and the demo bitstreams under `/usr/share/fpgas-tt/demos`, which the daemon keeps on the Pi and loads into an FPGA board when asked ([The Tiny Tapeout stack](https://docs.fpgas.online/en/latest/setup/tinytapeout.html)). It is installed on every Pi and runs where the site runs the Tiny Tapeout site (welland); a Pi without a board waits for one. |
| `fpgas-online-cam` | `cam_pi` | `fpgas-cam.service`, the camera stream ([Camera](https://github.com/fpgas-online/fpgas.online-cam/blob/main/docs/camera.md)). |
| `nfsroot-watchdog` | `onpi`, `stale_root.yml` | Reboots the Pi on its own after a root update ([Updating the NFS root](netboot/update-root.md)). |
| `openfpgaloader-fpgasonline-git`, `openocd-fpgasonline-git` | `onpi`, `apt.yml` | openFPGALoader and OpenOCD with the fpgas.online patches: the `rp1pio` cable (JTAG through the Pi 5's and CM5's RP1 PIO), the NeTV2 and Tiny Tapeout FPGA boards, `--flash-info`, and an upstream new enough for `--read-dna`. They replace the retired `openfpgaloader-rp1pio` and `openocd-rp1pio` from mithro/rp1-jtag. |
| `python3-rpi-hwid` | `onpi`, `apt.yml` | The hardware identity probe the boot check uses (for example to tell a Tiny Tapeout ASIC host from an FPGA one). |

`fpgas-online-setup-pi` also pulls in, through `depends:` in its `nfpm.yaml`, packages that no `onpi` task
names: `sunxi-tools` (for `sunxi-fel`), `expect` (for the Arty detection script), `zsh` (for the shell skeleton
it ships), `python3` and `python3-paho-mqtt` (for the fleet agent). Its other two `depends:` entries, `tmux` and
`vim`, are also installed by `apt.yml`. `expect` reaches a Pi only this way: no task in `onpi` installs it
(fpgas.online-setup-pi main and fpgas.online-infra main, read 2026-10-07).

The fpgas.online packages and the two JTAG tools are installed `state: latest`: they are rolling releases, and
a root build takes the newest. The Tiny Tapeout packages are installed on purpose that way, gated on
`tt_install | default(onpi_tt_boards is defined)`: a site that defines `tt_boards` gets them, and a build with
none, such as the CI NFS root build, forces them in with `tt_install: true`. The daemon and the demos are
generic fleet content, and a Pi with no `/dev/ttboard` waits for one. Enabling `fpgas-tt.service` is gated on
`tt_boards` alone, because a service enabled everywhere would change behaviour on a site without the Tiny
Tapeout site. The daemon reads no site catalogue: the root carries no `/etc/fpgas-online/tt-boards.yaml`
(`tt.yml` removes it, and stops if the root's `fpgas-online-tt` is older than 0.0.post64; `onpi/tasks/tt.yml`,
read 2026-10-07). Which versions a given root holds is read in the root itself; the welland root
of 6 October 2026 is on [The welland gateway](https://docs.fpgas.online/en/latest/sites/welland-gateway.html).

**From Debian** (`onpi`, `tasks/apt.yml`, and `cam_pi`):

| Packages | Why |
| --- | --- |
| `overlayroot` | The tmpfs layer over the read-only root (`overlayroot=tmpfs` on the kernel command line). |
| `lldpd` | Advertises the Pi's name on its link, so the switch's LLDP table says which Pi is on which port instead of trusting the cabling. A running Pi picks this up only after a reboot, because the NFS root is a read-only lower layer. |
| `atftpd`, `atftp` | A TFTP server and client on the Pi. `onpi`'s `tftpd.yml` then moves the port from 69 to `tftpd_port` (6069, in the inventory's `group_vars/all/ci.yml`) in both `atftpd.socket` (`ListenDatagram=`) and `/etc/default/atftpd`, and makes `/srv/tftp` writable by the `pi` user. |
| `fxload`, `openwince-jtag`, `uhubctl`, `i2c-tools` | USB firmware loading, older JTAG tools, USB hub port control, I²C (the identity probe uses it). |
| `tio`, `minicom`, `picocom`, `screen`, `tmux`, `vim`, `git`, `tree`, `ack`, `rsync`, `sshfs` | Serial terminals and a shell to work in. |
| `nmap`, `tcpdump` | Network diagnosis. |
| `ssh-import-id`, `software-properties-common` | Installed by `apt.yml`; the Pis' keys are written by the gateway instead ([Accounts and logins](https://docs.fpgas.online/en/latest/setup/access.html)). |
| `python3-full`, `python3-venv`, `python3-pip`, `python3-dev`, `pipx`, `python3-serial`, `python3-rpi.gpio`, `python3-numpy`, `python3-tqdm` | Python and the libraries the test scripts use. |
| `build-essential`, `dkms`, `libfreetype6-dev`, `libjpeg-dev` | So a `pip install` with a C extension builds on the Pi. |
| `jq`, `lm-sensors`, `gstreamer1.0-tools`, the GStreamer plugin sets `base`, `good`, `bad`, `ugly` and `base-apps`, `gstreamer1.0-libcamera`, `rpicam-apps-lite` | The camera pipeline (`cam_pi`). `jq` is there because the publisher script reads the Pi's default route with `ip -json route` ([`gst-libcam.sh`](https://github.com/fpgas-online/fpgas.online-cam/blob/main/gst-libcam.sh) line 72, read 2026-10-07). |

`vim-tiny` is removed first: `dpkg-divert` refuses to rename its help file over the full `vim`'s. The
role's comment quotes the error from an earlier root, naming `/usr/share/vim/vim82/doc/help.txt.vim-tiny`
(`ansible/roles/onpi/tasks/apt.yml`); the path on the current root was not re-checked.
`mpremote` and `uv` are installed with `pipx` into `/opt/pipx`, with their commands in `/usr/local/bin`: the
tasks pin `PIPX_HOME` and `PIPX_BIN_DIR`, because a bare `pipx install` puts the environments wherever the
calling environment points and the commands on no user's `PATH`, and an earlier root built on the gateway and
the CI-built one disagreed (the same file's comment, read 2026-10-07; fpgas.online-infra issue #34).

## Sources

fpgas.online-infra main, read 2026-10-07: the roles `fpgas_apt`, `onpi` and `cam_pi`, as named in each row. The
file behind every other fact on the Pi pages is listed on [Where each fact on these pages comes from](pi/sources.md).
