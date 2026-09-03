# Runbook: tweed becomes a hypervisor (Phase 3 cut-over)

Date written: 2026-09-02, for the tweed-split Phase 3 cut-over
(`tweed-split-design/docs/05-implementation-plan.md` Phase 3, PR contract 07 §Hypervisor).
Status: **DRAFT — not yet executed.** One planned outage of the site
(~45 min target if the NFS root is pre-seeded; ~3 h worst case if not).

Roles/plays this runbook drives: `ansible/hypervisor.yml` (role `hypervisor` +
operators + lldp) and `ansible/vms.yml` (role `libvirt-vm`), inventory host
`tweed-hv` (`ansible/inventory/host_vars/tweed-hv.yml`). Neither play is in
`site.yml`; nothing here runs by accident.

## 0. Pre-flight (days before, no outage)

- [ ] This PR merged; CI green.
- [ ] Decisions **D-1…D-4** taken (BIOS, RAM, transit, disk —
      `tweed-split-design/docs/06-decisions.md`). In particular **D-3**: with
      the transit still a /30, the hypervisor host and the gw VM cannot both
      hold 10.99.21.2. Until the transit is widened (/29 or per-VM rows on
      ten64), pick one: give the gw VM the address and manage the hypervisor
      over IPv6/IPMI SOL only, or renumber. `hypervisor_uplink_address` and
      `hypervisor_vms.gw-welland.ssh_probe_host` in host_vars must match the
      decision.
- [ ] Install the hypervisor preseed on ten64:
      copy the file content from
      `docs/superpowers/specs/2026-09-02-tweed-hv-preseed.md` to
      `ten64:/srv/pxe/hosts/tweed.preseed` (it REPLACES the 2026-08 monolith
      variant). **Verify the target-disk serial first** (step 3).
- [ ] `ANSIBLE_VAULT_PASSWORD_FILE=~/.config/fpgas-online/vault-pass` and the
      `fpgas.online-ansible` private key in `~/.ssh/fpgas.online-ansible` on
      the control node (ten64), as for every tweed run.
- [ ] Fleet ledger: capture a pre-cut-over census (which Pis are up, PoE
      states) to diff against at the end.

## 1. Hardware visit: BIOS VT-x (+ RAM), one reboot (D-1/D-2)

tweed shipped with VT-x **disabled**: dmesg says
`x86/cpu: VMX (outside TXT) disabled by BIOS` and there is no `/dev/kvm`
(live survey `data/live/tweed/NOTES.md`). The `hypervisor` role asserts
`/dev/kvm` and fails with a pointer here.

1. IPMI SOL to the BMC (10.1.5.131, GSM7252PS-s2 port 46):
   `ipmitool -I lanplus -H 10.1.5.131 -U <user> sol activate`
   (credentials via gdoc2netcfg on ten64). KVM-over-IP works too.
2. If fitting the second 8 GB DDR3 ECC SO-DIMM (D-2), do it in the same
   visit — both changes need the same reboot.
3. Reboot into BIOS setup (X9SPV-F, BIOS 2.0a): Advanced → CPU Configuration
   → **Intel Virtualization Technology → Enabled**. (VT-d is already on.)
4. Boot the current system once and confirm:
   `ls -l /dev/kvm` exists and `dmesg | grep -i vmx` no longer says disabled;
   `free -g` shows 16 GB if RAM was fitted.

This can be done well ahead of the cut-over — it does not change behaviour of
the running monolith.

## 2. Pre-seed the NFS-root volume (no outage; hours before)

The gw VM re-exports the same Pi NFS root. Copying it while the old system
still serves keeps the outage short (rsync again during the outage only picks
up deltas).

On ten64 (needs `qemu-utils`):

```bash
qemu-img create -f qcow2 /srv/scratch/nfsroot.qcow2 20G
sudo modprobe nbd max_part=4
sudo qemu-nbd --connect /dev/nbd0 /srv/scratch/nfsroot.qcow2
sudo mkfs.ext4 -L nfsroot /dev/nbd0
sudo mount /dev/nbd0 /mnt
sudo rsync -aHAX --numeric-ids ansible@10.99.21.2:/srv/nfs /mnt/
sudo rsync -aHAX --numeric-ids ansible@10.99.21.2:/var/cache/pib /mnt/
sudo umount /mnt && sudo qemu-nbd --disconnect /dev/nbd0
```

(Sizing: `/srv/nfs/rpi/bookworm` is 4.5 GB today; 20 G leaves bake headroom.)

## 3. OUTAGE START — PXE-install the hypervisor

Target disk: the **old bookworm SSD** (`ata-Samsung_SSD_850_EVO_250GB_S21NNXAG532419V`).
The live monolith (trixie, installed 2026-08) stays untouched on
`…S21NNXAG532383L` as the rollback. **Verify before installing** — on live
tweed: `lsblk -o NAME,SERIAL,MOUNTPOINTS` and check which serial carries `/`.
The preseed pins the target by-id; if the serials differ from the above, STOP
and fix the preseed.

1. Announce the outage; stop ansible cron jobs touching tweed if any.
2. On ten64: set tweed's PXE menu to the trixie installer
   (`/srv/pxe` tooling; `pxe-set-boot` — verify it works, it was flaky during
   the 2026-08 rebuild) and power-cycle / `ipmitool ... chassis power reset`.
3. Watch the install on SOL (console=ttyS2). The preseed creates a small
   root LV and leaves the bulk of the VG free (spec doc).
4. First boot: the `ansible` user answers on 10.99.21.2 (installer DHCP →
   static via converge).

## 4. Converge the hypervisor

```bash
export ANSIBLE_VAULT_PASSWORD_FILE=~/.config/fpgas-online/vault-pass
uv run ansible-playbook ansible/hypervisor.yml --limit tweed-hv
```

- First run reboots once (MAC-matched .link rename to eth-local/eth-uplink),
  then continues: bridges `br-fpgas` (MTU 1504, no address) and `br-uplink`
  (transit address), libvirt, chrony, etckeeper, operators, lldpd.
- The play fails fast if `/dev/kvm` is missing — that means step 1 was
  skipped or didn't stick.
- Storage for VM disks (per D-4; the VG is mostly free by design):

```bash
ssh ansible@10.99.21.2 sudo lvcreate -L 120G -n images tweed-vg
ssh ansible@10.99.21.2 sudo mkfs.ext4 -L vmimages /dev/tweed-vg/images
ssh ansible@10.99.21.2 'echo "/dev/tweed-vg/images /var/lib/libvirt/images ext4 defaults 0 2" | sudo tee -a /etc/fstab && sudo mount /var/lib/libvirt/images'
```

- Copy the pre-seeded volume in, then top up the delta:

```bash
scp /srv/scratch/nfsroot.qcow2 ansible@10.99.21.2:/tmp/ &&
  ssh ansible@10.99.21.2 sudo mv /tmp/nfsroot.qcow2 /var/lib/libvirt/images/
```

## 5. First gw VM (blue) = the full monolith

```bash
uv run ansible-playbook ansible/vms.yml --limit tweed-hv \
    -e vm_name=gw-welland -e vm_action=create -e vm_colour=blue \
    -e vm_hostname=tweed
ssh ansible@10.99.21.2 sudo virsh start gw-welland-blue
ssh ansible@10.99.21.2 sudo virsh console gw-welland-blue   # watch first boot
```

- The VM's NICs carry bare-metal tweed's MACs (host_vars comment): the
  switches, ten64's gdoc2netcfg rows and the Pis notice nothing.
- Inside the VM, mount the nfsroot volume (vdb) at its old paths before the
  converge (`mkdir -p /srv/nfs && mount /dev/vdb /srv/nfs` + fstab — the
  volume holds `nfs/` and `pib/` as copied in step 2; adjust mount layout to
  taste, `site.yml` only cares that `/srv/nfs/rpi/bookworm` exists).
- Converge the monolith into it (the VM answers on tweed's address, so the
  existing `fpgas.online` inventory host matches):

```bash
uv run ansible-playbook ansible/site.yml --limit fpgas.online --skip-tags pi
uv run ansible-playbook ansible/verify-server.yml --limit fpgas.online
uv run ansible-playbook ansible/verify-pi.yml
```

- Fleet census; PoE-cycle any Pi whose NFS mount didn't recover (NFSv3 hard
  mounts normally ride out the gap).

OUTAGE END. Exit criteria (05-implementation-plan): one VM functionally
identical to today; later `gw-welland-green` can be built while blue serves,
and:

```bash
uv run ansible-playbook ansible/vms.yml --limit tweed-hv \
    -e vm_name=gw-welland -e vm_action=switch \
    -e vm_switch_from=gw-welland-blue -e vm_switch_to=gw-welland-green
```

moves the nfsroot volume and swaps colours with <2 min NFS interruption.

## 6. Rollback

Boot the OTHER SSD — the untouched 2026-08 bare-metal monolith on
`…S21NNXAG532383L` — via the BIOS boot-order / one-time boot menu on IPMI SOL
(or the existing PXE chainload menu). ten64 rows, switch tables and Pis are
unchanged because the VM reused the physical MACs; the fleet recovers as soon
as the old system exports NFS again. (05-implementation-plan Phase 3
rollback.)

## 7. Follow-ups (Phase 4 prep, no outage)

- ten64: gdoc2netcfg row for `web-welland`'s new MAC `52:54:00:fa:21:04`
  (host_vars comment) before creating that VM.
- Transit widening per D-3; DNS/AAAA moves per Phase 4.
- Weekly blue/green rebuild via cron on ten64 (Phase 6).
