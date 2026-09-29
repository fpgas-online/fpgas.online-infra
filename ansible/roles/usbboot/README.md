# usbboot

Bakes Raspberry Pi **USB device-boot** tooling (`rpiboot`) into the published Pi
NFS root image, so any Pi in the rack can bring up a stranded Pi over USB.

This is the Raspberry Pi counterpart to the `felboot` role, which does the same
job for Allwinner boards. Same shape, same toggle pattern, different silicon.

## Problem

Every board in this rack netboots, which means none of them has a bootable SD
card. Raspberry Pi's own documentation describes what such a board does:

> USB device boot is available on the Compute Module series, Zero series, and
> Model A variants of the flagship series. […] When this boot mode is activated
> (usually after a failure to boot from the SD card), the Raspberry Pi puts its
> USB port into device mode and awaits a USB reset from the host.

— `documentation/asciidoc/computers/raspberry-pi/boot-usb.adoc`

So a board whose netboot does not come up is not necessarily dead: on the models
above it is sitting on the USB bus as `0a5c:2763` waiting for a host to hand it a
bootloader. `rpiboot` is the host side of that exchange, and it was not installed
anywhere in this repository — so the one thing that can talk to a stranded board
was missing from the image every board runs.

## What it installs

| Package | Provides | Why |
|---|---|---|
| `rpiboot` | `/usr/bin/rpiboot`, `/usr/share/rpiboot/*` | The host half of USB device boot |

The role asserts **both** the binary and `/usr/share/rpiboot/msd/bootcode.bin`
exist. Both matter, for different reasons: the binary is what the operator runs,
and the `msd` payload is what it pushes. A package that installed the tool
without its firmware directory would satisfy `dpkg` and then fail at exactly the
moment it is needed.

`msd` is the legacy interface, and it is the one the older boards require —
`raspberrypi/usbboot`'s `Readme.md` lists the Zero series, 1A+ and CM1 under
"Devices which require the legacy `msd` firmware loading interface", while newer
boards use the Linux-based `mass-storage-gadget`. The package ships both.

## Turning it on and off

One line in inventory (`group_vars/`, `host_vars/`, or `inventory-ci-nfsroot`
for the image build):

```yaml
usbboot_enabled: false
```

`ci-nfsroot.yml` gates the role on `usbboot_enabled`, so `false` installs nothing
and skips the assertions with it. The default is `true`.

The assertions live **inside** the role rather than in `ci-nfsroot.yml`'s
`dpkg-query` package loop, which is unconditional: adding them there would fail
the image build for anyone who turned the feature off.

To run or skip it on its own:

```bash
uv run ansible-playbook ansible/ci-nfsroot.yml --tags usbboot
uv run ansible-playbook ansible/ci-nfsroot.yml --skip-tags usbboot
```

## Know what it can and cannot reach

Worth reading before relying on it in a rescue, because the two halves of the
tool have opposite requirements:

- **Pushing a bootloader** works on a board with no bootable media — that is
  precisely the condition that puts the board into device mode.
- **`rpiboot -d msd`, which exposes the board's SD/eMMC as a USB mass-storage
  device, needs media that is present but not bootable.** If the card is absent
  there is nothing to expose; if the card boots, the ROM never enters device mode
  and `rpiboot` never sees the board. It is the right tool for a corrupt card,
  not a missing one.
- The legacy `msd` firmware "does not provide a console login or diagnostics"
  (`usbboot` `Readme.md`), so on a Zero-class board it gives you storage access
  and nothing else. It will not show you why a board failed to boot.

None of that limits this role — installing the tooling is unambiguously right —
but it does mean `rpiboot` is not a general-purpose "board won't boot" answer,
and a runbook that treats it as one will disappoint someone at 2am.
