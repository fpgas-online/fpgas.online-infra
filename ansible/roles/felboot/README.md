# felboot

Bakes Allwinner **FEL**-mode boot tooling into the published Pi NFS root image,
so any Pi in the rack can start an Orange Pi over USB.

## Problem

Orange Pi H3 boot here has two halves, and only one of them is in this repo.

The **network** half is already done: `fixpi/tasks/sunxi-image.yml` puts a Debian
`linux-image-armmp` kernel in every image (`ci-nfsroot.yml` asserts it), and
`fixpi/tasks/sunxi.yml` publishes the kernel, initrd, DTBs and a U-Boot
distro-boot PXE config to the server's TFTP root. Once a board is running U-Boot,
it can netboot.

The **USB** half was not. An Allwinner SoC with no bootable media falls into FEL
mode: it exposes a USB device (`1f3a:efe8`) and waits for a host to push SPL +
U-Boot into SRAM over it. Getting a board to the point where U-Boot exists at all
needs `sunxi-fel` running on a host physically cabled to it — one of the Pis —
and `sunxi-tools` appears nowhere in this repository.

So that tooling existed only as hand-made state on whichever Pi was doing the
job, which does not survive that Pi being re-imaged. On 2026-09-25 exactly that
happened: `pi-sw2-p30` came back with regenerated SSH host keys and no working
`authorized_keys`, and the Orange Pi on `s3300-1 p20` stopped returning from a
PoE cycle. `p21`–`p24` are still up only because they are running older boots and
have not needed to re-enter FEL since — they are one power event away from the
same state.

Putting the tooling in the image makes it a property of the platform rather than
of one board's history.

## What it installs

| Package | Provides | Why |
|---|---|---|
| `sunxi-tools` | `sunxi-fel` | The host half of the Allwinner FEL protocol |

RasPiOS bookworm carries `sunxi-tools` for both `armhf` and `arm64`, so the
image's stock apt sources are enough — no extra source, no pinning. The role then
asserts `/usr/bin/sunxi-fel` exists, because the binary is what an operator
needs, not the package record.

## Turning it on and off

One line in inventory (`group_vars/`, `host_vars/`, or `inventory-ci-nfsroot`
for the image build):

```yaml
felboot_enabled: false
```

`ci-nfsroot.yml` gates the role on `felboot_enabled`, so `false` installs nothing
and skips the role's assertion with it. The default is `true`.

The assertion lives **inside** the role rather than in `ci-nfsroot.yml`'s
`dpkg-query` package loop, which is unconditional: adding it there would fail the
image build for anyone who turned the feature off.

To run or skip it on its own:

```bash
uv run ansible-playbook ansible/ci-nfsroot.yml --tags felboot
uv run ansible-playbook ansible/ci-nfsroot.yml --skip-tags felboot
```

## What this role deliberately does NOT do

It installs the **tooling** only — no U-Boot payload, no per-board `sunxi-fel`
invocation, and no service that FEL-boots a board at Pi startup or on a timer.

That is on purpose. Whatever was set up on `pi-sw2-p30` was never recorded in
this repository and no copy of it survived the rebuild, so a service written here
would be a guess at what it used to do. Getting the tooling into the image is the
part that is certainly correct and certainly missing. Reconstructing the payload
and the trigger needs someone who knows what `p30` was running — and ideally a
decision about whether the FEL host should be a *declared* role in inventory
rather than whichever board happens to be cabled up.
