# Design Review #2: pici repo split spec (post-update)
**Reviewed**: 2026-04-03
**Spec**: `docs/superpowers/specs/2026-04-03-pici-repo-split-design.md`
**Previous review**: `docs/superpowers/specs/2026-04-03-pici-repo-split-design-review.md`
**Reviewer**: Claude (code-reviewer agent)

## Overall Assessment

The spec update addressed all 6 HIGH and 7 MEDIUM issues from the first review.
Every previously-missing file now has an explicit repo assignment. Two minor new
issues were found; neither is blocking.

---

## Previous HIGH Issues

**1. site role nginx files (fpgas.online.conf, certbot.sh) -- RESOLVED**
Spec lines 78-79 now explicitly list both under "Jinja2 templates and deployment
configs staying in infra" > Site role. Confirmed these files exist at
`ansible/roles/site/files/nginx/`.

**2. site role Jinja2 templates (6 service templates) -- RESOLVED**
Spec lines 74-77 now list all six: `daphne.service.j2`, `daphne.socket.j2`,
`gunicorn.service.j2`, `gunicorn.socket.j2`, `uvicorn.service.j2`,
`nginx.conf.j2`. Matches the 6 files under `ansible/roles/site/templates/`.

**3. onpi FPGA services (arty_blink, is_arty, is_wire) -- RESOLVED**
Spec lines 238-241 now enumerate all three subdirectories with every file:
`arty_blink.sh` + service, `arty_here.sh` + `.exp` + service, `arty_wire.sh` +
service. Assigned to fpgas.online-setup-pi. Matches `ansible/roles/onpi/files/`.

**4. fixpi scripts fully enumerated -- RESOLVED**
Spec lines 291-298 now list all four scripts individually (`maintenance.sh`,
`production.sh`, `mktftpln.sh`, `chroot-mount-pi-fs.bash`) and assign them to
fpgas.online-netboot-pi. Matches `ansible/roles/fixpi/files/scripts/`.

**5. pipw.sh assigned -- RESOLVED**
Spec line 298 assigns `pipw.sh` to fpgas.online-netboot-pi with a clear
description ("generate random Pi user password and create userconf.txt").
Matches `ansible/roles/fixpi/files/pipw.sh`.

**6. pistat/scripts/send.py included -- RESOLVED**
Spec line 172 now lists `send.py` as "base/shared sender" in the Pi-side scripts
section. Also listed at line 253 under setup-pi source content. The "Resolved
Questions" section (line 448) enumerates all five scripts. Matches
`ansible/roles/site/files/pib/pistat/scripts/send.py`.

---

## Previous MEDIUM Issues

**7. Per-app pyproject.toml structure addressed -- RESOLVED**
Spec lines 143-147 add a "Packaging structure" paragraph: single top-level
`pyproject.toml`, existing per-app files removed, with rationale.

**8. Per-app nginx confs addressed -- RESOLVED**
Spec lines 129-130 state nginx location confs are "shipped as package data
files". Lines 101-103 say the infra role symlinks them into
`/etc/nginx/includes/`. snmp_switch nginx conf is addressed at line 135.

**9. pistat/dnsmasq/send_stat.conf assigned -- RESOLVED**
Spec lines 138-139 assign it to fpgas.online-infra, noting it is "deployed by
pxe or site role to configure dnsmasq for stat collection".

**10. fixpi Jinja2 templates explicitly listed -- RESOLVED**
Spec lines 82-84 list all four fixpi templates: `cmdline.txt.j2`,
`config.txt.j2`, `fstab.j2`, `resolve.conf.j2` under the infra role.

**11. eth1.conf classified -- RESOLVED**
Spec lines 256-257 explicitly state eth1.conf stays in infra with the note "may
need to become a Jinja2 template for per-host IPs". Confirmed: the file contains
a hardcoded `192.168.100.100` address, so the template conversion note is
correct.

**12. .link files classified -- RESOLVED**
Spec lines 248-250 assign both `.link` files to fpgas.online-setup-pi with a
clear explanation: "USB-path-based network interface naming (generic for all Pis
with same USB topology, NOT MAC-specific)". Confirmed: the files use
`Path=platform-3f980000.usb-*` matching, not MAC addresses, so the setup-pi
assignment is correct.

**13. .gitignore not assigned -- RESOLVED**
Spec line 333 adds `.gitignore` to the "Content not migrated" table with the
note "Each new repo gets its own `.gitignore` appropriate to its content."

---

## Previous LOW Issues (also resolved)

**14. fixpi/onpi/wssh notes.txt** -- RESOLVED. Added to "Content not migrated"
table (lines 335-337).

**15. pibfpgas/Demos/ binary artifacts** -- RESOLVED. Spec lines 162-165 add a
detailed note about not shipping in the pip wheel, with options for GitHub
release assets or fpgas.online-test-designs. Also added as Open Question 3
(line 468).

**16. pibfpgas/fixtures/ deployment data** -- RESOLVED. Spec lines 167-169
document that fixtures ship with the package and the infra role selects which
fixture to load.

**17. wssh role resolved** -- RESOLVED. "Resolved Questions" section item 2
(line 452) states no separate repo needed, enumerates the 4 static files + 1
template.

**18. Version numbering** -- RESOLVED. "Resolved Questions" section item 3
(line 456) recommends 0.1.0.

---

## New Issues Found

### MEDIUM

**NEW-1. snmp_switch shell scripts (allpoe.sh, allpoeoff.sh, poe.sh) not assigned**
- Path: `ansible/roles/site/files/pib/snmp_switch/scripts/allpoe.sh`,
  `allpoeoff.sh`, `poe.sh`
- The spec assigns `snmp_switch/` to fpgas.online-poe (line 187) but only
  mentions the Python source, Django views, and nginx conf. These three shell
  scripts are CLI operator tools for PoE switch management. They should be
  explicitly listed as shipping with the poe package (likely as console script
  entry points or package data).
- Fix: Add these scripts to the fpgas.online-poe file listing or note they ship
  as part of the `[cli]` extra.

### LOW

**NEW-2. fixpi/README.md not in "Content not migrated" table**
- Path: `ansible/roles/fixpi/README.md`
- The "Content not migrated" table lists `notes.txt` files but not `README.md`
  files from roles. The fixpi role has a `README.md` that is neither assigned to
  a repo nor listed as not migrated. (The ci role README is implicitly covered
  by "replaced by fpgas.online-test-designs".)
- Fix: Either note it in the "Content not migrated" table or state that role
  READMEs are absorbed into each role's documentation in infra.

---

## Remaining File Coverage Audit

Cross-referencing every file in `~/github/pici/` against the spec:

| Status | Count |
|--------|-------|
| Explicitly assigned to a repo | ~135 files |
| Explicitly in "Content not migrated" | 8 entries |
| Role tasks/handlers/templates (implicitly stay in infra) | ~40 files |
| Unassigned (NEW-1 above) | 3 files |
| Minor documentation gap (NEW-2 above) | 1 file |

All production files in pici now have a repo assignment. No files are orphaned
at the HIGH or blocking level.

---

## Verdict

All 6 HIGH and 7 MEDIUM issues from the first review are **RESOLVED**. The spec
now provides explicit file-level assignments for every production file in pici.
Two minor new issues were found (snmp_switch CLI scripts, fixpi README.md),
neither of which is blocking. The spec is ready for execution.
