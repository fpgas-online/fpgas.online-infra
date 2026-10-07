# Historical tooling

**You operate the fleet and found `fpgas.online-netboot-pi` or `fpgas.online-tools` and want to know whether they describe what the fleet runs.** They do not: they are the hand-run predecessors of the Ansible roles. Sources for the live system: [Sources for netboot](sources.md).

## What came before the roles

Before the Ansible roles, the netboot root was built and switched by hand from two repositories:

- [fpgas.online-netboot-pi](https://github.com/fpgas-online/fpgas.online-netboot-pi): image extraction, NFS root preparation, the QEMU/chroot wrapper, TFTP serial-number symlinks, and maintenance/production mode switching.
- [fpgas.online-tools](https://github.com/fpgas-online/fpgas.online-tools): DHCP log analysis and a netconsole client for capturing Pi boot spew.

fpgas.online-infra now vendors netboot-pi's scripts: `img2files.sh` in the `img` role (`roles/img/files/img2files.sh`), and `maintenance.sh`, `production.sh` and the chroot helper in `fixpi` (`roles/fixpi/files/scripts/`, all present on main, read 2026-10-07). The two repositories are history, not the running system. The vendored `img2files.sh` is not the deploy path: the `img` role pulls the CI-built image (`roles/img/tasks/pull.yml`).

> [!WARNING]
> Do not read `fpgas.online-netboot-pi`'s `pinet/` directory as documentation of what the fleet boots. Those files are a **trixie** root with `overlayroot=` empty and both NFS mounts read-write: that is maintenance mode, the state `maintenance.sh` puts the fleet into so that a single Pi can apt-install into the shared root. welland's fleet boots a **bookworm** root with `overlayroot=tmpfs` and both mounts read-only. (ps1 serves a trixie root, [The ps1 gateway and switch](https://docs.fpgas.online/en/latest/sites/ps1-gateway.html), but still read-only with the overlay.)
