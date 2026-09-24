# Spec: tweed hypervisor preseed (`ten64:/srv/pxe/hosts/tweed.preseed`)

Companion to `docs/superpowers/runbooks/2026-09-02-tweed-hypervisor-cutover.md`
(step 0/3). The per-host preseed lives on ten64, outside this repo, and is
installed **manually**; this doc holds the authoritative content for the
hypervisor variant. It replaces the 2026-08 monolith-reinstall variant
(which targeted the then-blank `…383L` disk with the guided full-disk LVM
recipe).

Differences from the monolith variant:

- **Target disk flipped**: install onto the old bookworm SSD
  (`…S21NNXAG532419V`); the live 2026-08 trixie monolith on `…383L` becomes
  the rollback. VERIFY the serials against `lsblk -o NAME,SERIAL,MOUNTPOINTS`
  on live tweed before installing — d-i will erase the selected disk.
- **Partitioning**: instead of the base preseed's guided "atomic" LVM
  (one max-size root), an expert recipe builds a small root LV and leaves
  the bulk of the volume group **free** for VM storage (decision D-4: LV for
  `/var/lib/libvirt/images`, raw LVs per VM, or both — carved later, see
  runbook step 4).

File content (install as `ten64:/srv/pxe/hosts/tweed.preseed`):

```
# tweed as HYPERVISOR (Supermicro X9SPV-F, BIOS boot, IPMI SOL on COM3/ttyS2)
# Per-host override applied on top of /srv/pxe/<suite>/preseed.cfg by
# scripts/early_command.sh (debconf-set-selections), fetched by hostname
# (the permanent pxelinux menu passes hostname=tweed on the kernel cmdline).
#
# 2026-09 Phase 3 cut-over (fpgas.online-infra
# docs/superpowers/runbooks/2026-09-02-tweed-hypervisor-cutover.md): install
# onto the OLD bookworm SSD (S21NNXAG532419V); the live bare-metal monolith
# on S21NNXAG532383L stays untouched as the rollback. sda/sdb order is not
# stable across kernels, so the target is pinned by-id — VERIFY the serial
# against live lsblk before running this.
d-i partman-auto/disk string /dev/disk/by-id/ata-Samsung_SSD_850_EVO_250GB_S21NNXAG532419V
# GRUB on the NEW disk only; the rollback disk's boot sector stays intact so
# the BIOS boot-order choice decides which system boots.
d-i grub-installer/bootdev string /dev/disk/by-id/ata-Samsung_SSD_850_EVO_250GB_S21NNXAG532419V

# Hypervisor partitioning: /boot primary, one big LVM PV, VG "tweed-vg" with
# a SMALL fixed root (20G) and swap (4G) — everything else stays FREE in the
# VG for VM volumes (an ext4 LV for /var/lib/libvirt/images and/or raw LVs,
# created post-install by the runbook). Fixed min=max sizes on the LVs are
# what leaves the VG free; do not add a growing (-1) LV here.
d-i partman-auto/method string lvm
d-i partman-auto-lvm/guided_size string max
d-i partman-auto-lvm/new_vg_name string tweed-vg
d-i partman-auto/choose_recipe select hv-root
d-i partman-auto/expert_recipe string                             \
    hv-root ::                                                    \
        512 512 512 ext4                                          \
            $primary{ } $bootable{ }                              \
            method{ format } format{ }                            \
            use_filesystem{ } filesystem{ ext4 }                  \
            mountpoint{ /boot }                                   \
        .                                                         \
        1000 10000 -1 ext4                                        \
            $defaultignore{ } $primary{ }                         \
            method{ lvm } vg_name{ tweed-vg }                     \
        .                                                         \
        20480 20480 20480 ext4                                    \
            $lvmok{ } in_vg{ tweed-vg } lv_name{ root }           \
            method{ format } format{ }                            \
            use_filesystem{ } filesystem{ ext4 }                  \
            mountpoint{ / }                                       \
        .                                                         \
        4096 4096 4096 linux-swap                                 \
            $lvmok{ } in_vg{ tweed-vg } lv_name{ swap }           \
            method{ swap } format{ }                              \
        .
# Only the selected disk's LVM/md may be wiped; the rollback disk keeps its
# structures.
d-i partman-auto/purge_lvm_from_device boolean false
d-i partman/default_filesystem string ext4

# Installed-system serial console: SOL is COM3 = ttyS2 (I/O 0x3e8). The
# trixie base preseed installs console=ttyS0, which would leave SOL silent
# after the first reboot; the 8250 driver drives ONE ttyS console (first
# wins), so ttyS2 must be the only serial console listed.
d-i debian-installer/add-kernel-opts string console=tty0 console=ttyS2,115200n8 earlycon=uart8250,io,0x3e8,115200n8

# Identity: the hypervisor is tweed-hv (inventory host tweed-hv in
# fpgas.online-infra); the gw VM inherits the name "tweed".
# netcfg/hostname FORCES the name even when DHCP/reverse-DNS provides one
# (get_hostname alone lost to the PTR-derived 'ipv4', rebuild A1-6).
d-i netcfg/get_hostname string tweed-hv
d-i netcfg/get_domain string welland.mithis.com
d-i netcfg/hostname string tweed-hv
```

Notes:

- The base `/srv/pxe/trixie/preseed.cfg` supplies everything else (mirror +
  apt proxy, the `ansible` user keyed with `scripts/ansible_authorized_keys`,
  partman confirmation answers, late_command). This file only overrides.
- The base preseed's `partman-auto/choose_recipe select atomic` is overridden
  by the `hv-root` recipe above; both `choose_recipe` lines coexist in
  debconf, last-set (this per-host file) wins.
- After install, `vgs tweed-vg` should show a ~230 G VG with only ~24.5 G
  allocated. If d-i instead grew root to fill the disk, the recipe was not
  applied — check `early_command.sh` fetched this file (installer syslog).
