# Design Review: pici repo split spec
**Reviewed**: 2026-04-03
**Spec**: `docs/superpowers/specs/2026-04-03-pici-repo-split-design.md`
**Reviewer**: Claude (code-reviewer agent)

## Overall Assessment

The spec is well-structured with a clear architecture diagram, sensible
artifact strategy, and thorough migration phases. The "source repos produce
artifacts, infra consumes them" principle is sound. Below are issues found by
cross-referencing the actual `carlfk/pici` file tree.

---

## Issues

### HIGH severity

**1. Missing files: site role non-pib content**
- Path: `ansible/roles/site/files/nginx/fpgas.online.conf`, `ansible/roles/site/files/nginx/certbot.sh`
- The spec's fpgas.online-site section only lists `files/pib/` and `files/js/`.
  The `files/nginx/` directory (fpgas.online.conf, certbot.sh) is not assigned
  to any repo. These are deployment configs that belong in infra.
- Fix: Add these explicitly to the fpgas.online-infra file list under the site
  role, or note they stay in infra as deployment configuration.

**2. Missing files: site role Jinja2 templates and service units**
- Path: `ansible/roles/site/templates/daphne.service.j2`, `daphne.socket.j2`, `gunicorn.service.j2`, `gunicorn.socket.j2`, `nginx.conf.j2`, `uvicorn.service.j2`
- The spec says the site role stays in infra but never mentions these 6 Jinja2
  templates. They configure daphne/gunicorn/uvicorn/nginx and are critical for
  deployment.
- Fix: Add to fpgas.online-infra's site role listing. These must stay in infra
  (they use ansible variables), but the omission from the spec makes it look
  like they were forgotten.

**3. Missing files: onpi FPGA-specific services not accounted for in setup-pi**
- Path: `ansible/roles/onpi/files/arty_blink/`, `ansible/roles/onpi/files/is_arty/`, `ansible/roles/onpi/files/is_wire/`
- The spec says onpi's "zsh/tmux configs, profile scripts, systemd units move
  to setup-pi" but never mentions the arty_blink, is_arty, or is_wire
  subdirectories. These contain FPGA board detection scripts and services
  (arty_here.exp, arty_here.service, arty_wire.service, arty_blink.service +
  their shell scripts).
- Fix: Explicitly list these in fpgas.online-setup-pi. They are Pi-side
  software that detects/manages FPGA boards.

**4. Missing files: fixpi scripts not fully enumerated**
- Path: `ansible/roles/fixpi/files/scripts/maintenance.sh`, `mktftpln.sh`, `production.sh`
- The spec mentions `chroot-mount-pi-fs.bash` with "etc." but does not list
  `maintenance.sh`, `mktftpln.sh`, or `production.sh`. Need to decide: do these
  go to setup-pi or netboot-pi?
- Fix: Enumerate all four scripts and assign each. `chroot-mount-pi-fs.bash`
  logically goes to netboot-pi (it is a chroot tool). The others
  (maintenance/production mode switching, TFTP symlinks) are operator tools
  that likely belong in netboot-pi or tools.

**5. Missing file: fixpi/files/pipw.sh not assigned**
- Path: `ansible/roles/fixpi/files/pipw.sh`
- This file exists in fixpi but is not mentioned anywhere in the spec.
- Fix: Identify its purpose and assign to setup-pi or tools.

**6. pistat/scripts/send.py not mentioned in open question**
- Path: `ansible/roles/site/files/pib/pistat/scripts/send.py`
- Open question 1 lists send_serial.py, send_stat.py, send_ncc.py but misses
  `send.py` itself (which appears to be the base/shared sender).
- Fix: Add send.py to the open question list. This is likely the common module
  the others depend on.

### MEDIUM severity

**7. Django sub-apps already have individual pyproject.toml files**
- Path: `pibfpgas/pyproject.toml`, `pibdemos/pyproject.toml`, `pibup/pyproject.toml`, `pistat/pyproject.toml`, `snmp_switch/pyproject.toml`
- The spec proposes a single `pyproject.toml` for fpgas.online-site. But each
  Django sub-app already has its own `pyproject.toml` with `src/` layout. The
  spec should acknowledge this existing structure and state whether the split
  repo will use a single top-level pyproject.toml (monolithic package) or keep
  them as separate installable sub-packages within a workspace.
- Fix: Add a "Packaging structure" subsection to fpgas.online-site that
  addresses how the existing per-app pyproject.toml files will be handled. The
  simplest approach: one top-level pyproject.toml that lists the sub-apps as
  packages via `packages = find:` or explicit `[tool.setuptools.packages]`.

**8. Per-app nginx conf files not addressed**
- Path: `pibdemos/nginx/pibdemos.conf`, `pibfpgas/nginx/pibfpgas.conf`, `pibup/nginx/pibup.conf`, `pistat/nginx/pistat.conf`, `snmp_switch/nginx/snmp_switch.conf`
- Each Django sub-app ships its own nginx location config. The spec does not say
  whether these move to the site pip package or stay in infra. They are
  non-Jinja2 static configs, so by the spec's own rule they should move out of
  infra. But nginx configs on the server arguably belong with deployment.
- Fix: Decide and document. Recommendation: ship them inside the pip package as
  data files and have the infra role symlink/copy them, OR move them to infra
  as templates even if they currently have no variables.

**9. pistat/dnsmasq/send_stat.conf not assigned**
- Path: `ansible/roles/site/files/pib/pistat/dnsmasq/send_stat.conf`
- This dnsmasq config relates to pistat's status reporting. It is neither
  mentioned in the site repo listing nor in the setup-pi listing.
- Fix: Assign to either fpgas.online-site (if it configures the server-side
  dnsmasq for stat collection) or fpgas.online-setup-pi (if it is deployed to
  Pis).

**10. fixpi Jinja2 templates not mentioned in infra listing**
- Path: `ansible/roles/fixpi/templates/boot/cmdline.txt.j2`, `boot/config.txt.j2`, `etc/fstab.j2`, `resolve.conf.j2`
- The spec says fixpi templates that use ansible variables stay in infra. These
  four templates are the ones that stay, but they are not explicitly listed
  under the infra repo's fixpi section.
- Fix: Add an explicit "Templates staying in infra" list for fixpi, parallel to
  what was done for the cam role.

**11. fixpi/files/etc/network/interfaces.d/eth1.conf unclear classification**
- Path: `ansible/roles/fixpi/files/etc/network/interfaces.d/eth1.conf`
- The spec says host-specific interface configs stay in infra as Jinja2
  templates. But `eth1.conf` is a plain file, not a `.j2` template. It may be
  generic (goes to setup-pi) or host-specific (needs templating in infra).
- Fix: Check if eth1.conf contains host-specific values. If generic, assign to
  setup-pi deb. If host-specific, convert to `.j2` and note it stays in infra.

**12. fixpi/files/etc/systemd/network/*.link files classification**
- Path: `11-eth-uplink.link`, `12-eth-fpga.link`
- The spec says these "stay in infra as Jinja2 templates" for MAC-based naming.
  But in pici they are plain files under `files/`, not `templates/`. If they
  contain actual MAC addresses, they need to become templates.
- Fix: Verify these contain hardcoded MACs. If so, note in the spec that they
  must be converted to `.j2` templates during migration. If they are generic
  udev-style match rules, they can go to setup-pi.

**13. .gitignore not assigned**
- Path: `/home/tim/github/pici/.gitignore`
- Minor but the Python-focused `.gitignore` should be noted as not migrated (it
  is repo-specific boilerplate, each new repo gets its own).
- Fix: Add to "Content not migrated" table or just note that each new repo
  generates its own .gitignore.

### LOW severity

**14. fixpi and onpi notes.txt files not mentioned**
- Path: `ansible/roles/fixpi/notes.txt`, `ansible/roles/onpi/notes.txt`
- Developer notes within roles. Trivial but should be explicitly dropped or
  migrated.
- Fix: Add to "Content not migrated" or move to relevant repo as documentation.

**15. pibfpgas/Demos/ directory contains binary artifacts**
- Path: `pibfpgas/Demos/linux_litex/tftp/Image`, `emulator.bin`, `rootfs.cpio`, `rv32.dtb`, `top.bit` (multiple)
- Binary FPGA bitstreams and Linux images are checked into the Django app.
  Packaging these in a pip wheel will bloat the package. The spec does not
  address this.
- Fix: Add a note about handling demo bitstreams/binaries. Options: (a) keep in
  the pip package as package data, (b) host separately and download at deploy
  time, (c) move to fpgas.online-test-designs repo.

**16. pibfpgas/fixtures/ contains deployment data**
- Path: `pibfpgas/fixtures/fpgas.online.json`, `ps1.fpgas.online.json`
- Django fixtures for loading board/demo data. These are environment-specific.
  Should be called out as either shipped in the package or managed in infra.
- Fix: Note that fixtures ship with the pip package but may need per-deployment
  variants.

**17. wssh role has custom config files beyond "third-party tool"**
- Path: `ansible/roles/wssh/files/etc/nginx/includes/wssh.conf`, `etc/systemd/system/gunicorn.service`, `gunicorn.socket`, `wssh.socket`, `templates/wssh.service.j2`
- Open question 3 asks if wssh has custom config. The answer is yes: there are
  4 static config files plus 1 Jinja2 template. These are small and fine in
  infra, but the open question should be resolved with this information.
- Fix: Resolve open question 3: "No separate repo needed. The wssh role has 4
  static files and 1 template, all small. Keep in infra."

**18. Version numbering recommendation missing**
- Open question 5 asks 1.0.0 vs 0.1.0 but offers no recommendation.
- Fix: Recommend 0.1.0 since this is the initial extraction, not a stable
  public API. Reserve 1.0.0 for after the first successful end-to-end
  deployment from the split repos.

---

## Summary of gaps

| Category | Count |
|----------|-------|
| Files missing from any repo assignment | 8 (high: 1, 2, 3, 4, 5, 6; medium: 9, 11) |
| Classification ambiguity | 3 (medium: 10, 11, 12) |
| Packaging design gaps | 3 (medium: 7, 8; low: 15) |
| Open questions answerable now | 2 (low: 17, 18) |
| Trivial omissions | 2 (low: 13, 14, 16) |

## Verdict

The spec is a solid foundation. The architecture and phased migration strategy
are sound. The main gap is that the file-to-repo mapping is incomplete -- roughly
15-20 files in pici have no explicit assignment. Most of these are infra
deployment configs (service templates, nginx configs) that implicitly stay in
the infra repo, but the spec should enumerate them to avoid ambiguity during
execution. The existing per-app `pyproject.toml` structure in the Django apps
is a positive sign that the code is already partially prepared for the split,
but the spec needs to address how that maps to the proposed single pip package.

No blocking architectural issues were found. The spec is executable after
addressing the HIGH items above.
