# tinytapeout.fpgas.online infra (phase 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Roll phase 2 out: the Pi NFS root gets `fpgas-online-tt-demos` (the demo bitstreams) next to the updated `fpgas-online-tt` daemon, tweed gets the Commander embed `0.2.0` and the site release with the FPGA gallery, and `verify-*.yml` checks it.

**Architecture:** Only inventory pins and two role tweaks: `onpi/tasks/tt.yml` installs both debs; `tt_commander_embed_version/sha256` bumped; `ttsite` verify asserts `/api/board/<slug>/designs` answers on the TT host for an fpga board; `verify-pi.yml` asserts the demos dir is populated in the NFS root. Then the runbook steps: `web.yml` (tweed) and the Pi play (`site.yml --tags pi,fpgas-apt,onpi` — nspawn start → fpgas-apt/onpi → stop) followed by a Pi reboot (or `apt` in the overlay for an immediate test).

**Tech Stack:** Ansible (existing repo), nfpm debs from the apt repo.

**Spec:** design §7.3, §8, §11 phase 2. Depends on: fpgas.online-tt deb ≥ the FPGA-API release (`/designs`), `fpgas-online-tt-demos` deb present in the apt repo, fork release `embed-v0.2.0` (sha256 recorded in the SDD ledger of the fork plan), site `main` with the gallery.

## Global Constraints

- Repo `fpgas-online/fpgas.online-infra`; branch in `.worktrees/`; PR to `main`; yamllint green (the VM test is currently blocked by the external `fpgas.online` Pages redirect — note it in the PR; ansible-lint advisory). Commit trailer as in the other plans.
- `tt_commander_embed_version: "0.2.0"` and `tt_commander_embed_sha256: "e4e6d71b4acd52a9a0dcbdf869b7928a74d130250d1a25cde546fee7a1b06b39"` (fork release `embed-v0.2.0`, `.sha256` asset) in `host_vars/fpgas.online.yml` (the test inventory keeps `""`).
- `roles/onpi/tasks/tt.yml`: `apt: name: [fpgas-online-tt, fpgas-online-tt-demos] state: latest` (so re-running the Pi play picks up new rolling debs).
- `roles/ttsite/tasks/verify/main.yml`: for the first live `kind: fpga` board (if any), `uri GET http://127.0.0.1/api/board/<slug>/designs` with `Host: {{ ttsite_domain }}` → status 200 or 502 (502 = Pi unreachable is acceptable in the VM); assert the body is JSON with either `designs` or `error`.
- `verify-pi.yml`: `stat {{ nfs_root }}/root/usr/share/fpgas-tt/demos/index.json` exists when `tt_boards` defines an fpga board (production); skip in the VM (no apt access there — `when: not ci | default(false)` like neighbouring checks, or tag it `hw-fpga` which the VM run skips).
- Runbook additions in `docs/superpowers/runbooks/2026-08-23-tweed-web-deploy.md`: phase-2 rollout order and the "immediate test without reboot" recipe (`ssh pi-sw2-p33 apt install fpgas-online-tt fpgas-online-tt-demos` in the overlay, `systemctl restart fpgas-tt`).

---

### Task 1: Inventory pins + role tweaks + verify + runbook

- [ ] `host_vars/fpgas.online.yml`: embed version/sha256 → 0.2.0.
- [ ] `roles/onpi/tasks/tt.yml`: install both packages (`state: latest`).
- [ ] `roles/ttsite/tasks/verify/main.yml`: designs API check (as above).
- [ ] `ansible/verify-pi.yml`: demos index check (tagged `hw-fpga`).
- [ ] Runbook section "Phase 2 (FPGA)".
- [ ] `uv run yamllint -c .yamllint.yml ansible/ tests/`; `--syntax-check` both inventories; commit; PR; merge on green lint.

### Task 2 (controller): rollout + verification on hardware

- [ ] `uv run ansible-playbook -i ansible/inventory ansible/web.yml --limit fpgas.online --skip-tags snmp -e @tmp/extra-vars.yml` (embed 0.2.0 + site main).
- [ ] Pi side: `uv run ansible-playbook -i ansible/inventory ansible/site.yml --limit fpgas.online,pi --tags pi,fpgas-apt,onpi --skip-tags snmp -e @tmp/extra-vars.yml` (bakes both debs into the NFS root) — then on each FPGA Pi `apt install fpgas-online-tt fpgas-online-tt-demos && systemctl restart fpgas-tt` in the overlay for an immediate test (or reboot).
- [ ] Verify: `curl https://tinytapeout.fpgas.online/api/board/fpga-1/designs` lists the demos; browser: gallery renders, Run loads a demo (Commander shows the enable log), upload of a built `.bin` works, Commander project list = daemon designs, pinout tab shows the demo pinout.
