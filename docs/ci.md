# How CI works

CI answers one question for every push and pull request: **would a fresh
deploy of tweed from this checkout work?**

Tweed is the server that network-boots ("netboots") the Raspberry Pis
attached to the FPGA boards. A deploy of tweed works when a Pi can netboot
from the new server, come up correctly, and register itself with the
server's fleet. CI tests exactly that. It:

1. deploys a brand-new server, a stand-in for tweed, with
   [`ansible/site.yml`](../ansible/site.yml), exactly as a real deploy
   does;
2. powers on a virtual Raspberry Pi, which netboots from that server; and
3. checks both sides, including that the Pi appears online in the server's
   fleet.

A netbooted Pi mounts its root filesystem over the network from the
server. The server does not build that filesystem itself: it downloads a
prebuilt **Pi root image**. So CI builds that image too, from the same
checkout, and the test server downloads it. That way a change to the Pi's
roles is tested in the root the virtual Pi boots. The image build supports
the test; it is not a goal of its own.
[Section 2](#2-job-1-the-pi-root-image-nfsroot-buildyml) covers how the
image is built, and how CI avoids rebuilding it when nothing in it has
changed.

On `main`, one more thing happens after the test passes: the tested image
becomes the one production pulls
([section 4](#4-promotion-the-image-production-pulls)). An image that has
not netbooted a Pi never reaches production.

Three workflows are involved:

| Workflow | File | Runs on | Typical duration | Example run |
|---|---|---|---|---|
| VM Integration Tests | [`vm-test.yml`](../.github/workflows/vm-test.yml) | every push to `main`, every PR to `main`, a schedule (asked for hourly, see [§2.3](#23-why-there-are-several-ways-to-produce-it)), manual dispatch | **12–16 min** of work, median about 14½; see [§6](#6-timings) for the cases that take longer | [36944922891](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36944922891) (`main`, scheduled, 14:21) |
| nfsroot build (the Pi root image) | [`nfsroot-build.yml`](../.github/workflows/nfsroot-build.yml) | only when the VM test calls it. It has no triggers of its own | 10 s, ~3½ min, ~6½ min or 9–10 min: see [§2.3](#23-why-there-are-several-ways-to-produce-it) | the `nfsroot /` jobs of any VM test run |
| Lint | [`lint.yml`](../.github/workflows/lint.yml) | every push to `main` and every PR | ~2½ min | [36702339845](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36702339845) |

Every duration in this document was measured on a real run, and links to
the run or job it came from. Estimates are marked as such. The document
gives no counts of tests, tasks or checks, and no package versions: those
change with almost every PR. The linked runs and files show the current
ones.

---

## 1. The big picture

This section shows how the jobs fit together, so that the later sections
make sense on their own.

Every push to `main` and every pull request starts two workflows: Lint and
the VM test. The VM test workflow runs **two jobs at the same time**:

- **Job 1** produces the Pi root image. It builds the image from the
  checkout, or reuses an earlier one when nothing that goes into the image
  has changed. It publishes the image to the GitHub container registry
  (**GHCR**, `ghcr.io`).
- **Job 2** is the test itself. It deploys a new server and boots a
  virtual Pi from it. The server downloads the image from GHCR.

On `main` only, a third job, **Promote**, runs once both have passed. It
points the tag production pulls at the image Job 2 just tested.

### Overview

```mermaid
%%{init: {"themeVariables": {"fontSize": "20px"}}}%%
flowchart TB
    trigger(["push, pull request<br/>or schedule"])
    lint["<b>Lint</b><br/>yamllint, ansible-lint,<br/>unit tests · ~2½ min"]
    job1["<b>Job 1</b><br/>Pi root image<br/>10 s – 9 min"]
    job2["<b>Job 2</b><br/>deploy tweed,<br/>boot a virtual Pi<br/>~14 min"]
    ghcr[("GHCR<br/>nfsroot:ci-RUN_ID")]
    promote["<b>Promote</b><br/>main only · 15 s"]
    prod[("GHCR<br/>nfsroot:bookworm-armhf<br/>(production)")]
    trigger --> lint
    trigger --> job1
    trigger --> job2
    job1 -- publishes --> ghcr
    ghcr -. "pulled by the<br/>server" .-> job2
    job2 -- "passed" --> promote
    promote --> prod
```

The two jobs meet at one point, the dotted arrow: the server in Job 2
downloads the image that Job 1 published. The server needs the image only
in the last play of `site.yml`, about 8 minutes after the job starts, and
the download takes 30–65 s. So Job 1 can take up to about 7 minutes
without delaying anything. On scheduled runs Job 1 waits for the stages
job first ([§2.7](#27-the-stages-job-scheduled-runs)), which puts it
right at that limit ([§6.2](#62-scheduled-runs)).

### Job 1: which image the test gets

Job 1 first computes a fingerprint of every file that goes into the image.
This fingerprint is the **inputs key**. The job then takes the cheapest
path that still gives an image matching this checkout:

- **Reuse:** an image with the same inputs key already exists. The job
  gives it this run's tags and builds nothing.
- **Warm:** the inputs changed, but the Raspberry Pi OS (RasPiOS) base did
  not. The job starts from the image production currently pulls and
  applies only the changes.
- **Stage:** the RasPiOS base changed, or the run asks for a clean build.
  The job starts from a published **stage image**: RasPiOS with every
  package already upgraded. If no stage image exists yet for this base, it
  builds one first.

```mermaid
%%{init: {"themeVariables": {"fontSize": "20px"}}}%%
flowchart TB
    key["Fingerprint every file<br/>that goes into the image"]
    q1{"Image with this<br/>fingerprint exists?"}
    q2{"production's image<br/>uses the same RasPiOS?"}
    q3{"upgraded stage<br/>published?"}
    reuse["<b>REUSE</b><br/>copy its tags<br/>~10 s"]
    warm["<b>WARM</b><br/>update production's<br/>image · ~3½ min"]
    unpacked["<b>STAGE</b><br/>unpack it, build<br/>on it · ~6½ min"]
    inline["<b>STAGE, built inline</b><br/>RasPiOS → upgrade<br/>→ build · ~9 min"]
    key --> q1
    q1 -- yes --> reuse
    q1 -- no --> q2
    q2 -- yes --> warm
    q2 -- no --> q3
    q3 -- yes --> unpacked
    q3 -- no --> inline
    classDef fast fill:#d8f3dc,stroke:#2d6a4f,color:#1b4332
    classDef mid fill:#fff3bf,stroke:#b08900,color:#5c4400
    classDef slow fill:#ffe3e3,stroke:#c92a2a,color:#7d1a1a
    class reuse,warm fast
    class unpacked mid
    class inline slow
```

Most runs take the reuse or warm path (green). Both finish before the
server needs the image. The stage path (yellow) finishes around the time
the server needs it, so the server may wait up to about 2 minutes. The
inline path (red) runs when a PR changes how the stages are built; there
the server waits 2–3 minutes for the image.
[Section 2.3](#23-why-there-are-several-ways-to-produce-it) explains why
there are several paths.

### Job 2: the test

Job 2 runs the production deploy on a fresh server VM, then boots the
virtual Pi and checks both machines. The times are from
[run 36944922891](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36944922891/job/110644729419),
a scheduled run started 2026-10-02 00:13 UTC, on commit 82721eb. Its
total, 14:21, is close to the median, 14:16, of
the eight runs on that commit that neither waited for a runner nor did a
full rebuild ([§6.4](#64-where-the-time-goes)).

```mermaid
%%{init: {"themeVariables": {"fontSize": "20px"}}}%%
flowchart TB
    setup["Job setup<br/>40 s"]
    srv["Boot a fresh<br/>Debian 13 server VM,<br/>account checks · 26 s"]
    s1["site.yml: rename NICs, reboot,<br/>first apt refresh<br/>1 min 8 s"]
    s2["start pulling the<br/>image in the background<br/>7 s"]
    s3["server roles<br/>firewall, NFS, DHCP/TFTP …<br/>2 min 55 s"]
    s4["web.yml<br/>Django, web terminal,<br/>streaming, fleet broker<br/>2 min 43 s"]
    s5["NFS root: unpack<br/>the image, add<br/>the site layer<br/>1 min 54 s"]
    pion(["Power on<br/>the virtual Pi"])
    boot["Pi netboots<br/>DHCP → TFTP → kernel<br/>→ NFS root<br/>1 min 51 s"]
    vp["verify-pi.yml<br/>incl. <b>registered<br/>with the fleet</b><br/>2 min 8 s"]
    vs["verify-server.yml<br/>runs alongside"]
    result(["pass / fail<br/>~14 min in total"])
    setup --> srv --> s1 --> s2 --> s3 --> s4 --> s5 --> pion
    pion --> boot --> vp --> result
    pion --> vs --> result
```

- Everything from `site.yml` to the NFS root is the production playbook,
  run exactly as it runs on tweed, with nothing skipped.
- The **site layer** is the part of the Pi root that differs per site:
  passwords, SSH keys and site configuration. The published image has none
  of it; each server adds its own (see [§2.1](#21-what-the-image-is-and-why-ci-builds-it)).
- The Pi is powered on only after the server is fully deployed, just as
  real boards are power-cycled over PoE after a deploy.
- The server's checks run while the Pi boots, so they add no time.

---

## 2. Job 1: the Pi root image ([`nfsroot-build.yml`](../.github/workflows/nfsroot-build.yml))

This section covers the job that produces the Pi root image: what the
image contains, where it is published, how the job decides how much to
build, and what the build does. Read it when a change touches the Pi's
software, or when this job fails.

### 2.1 What the image is, and why CI builds it

Every netbooted Pi mounts the same root filesystem read-only over NFS, from
the server's `/srv/nfs/rpi/bookworm`. Writes go to a tmpfs overlay (a
RAM-backed layer on top of the read-only root). The tree holds two
directories:

- `boot/` is what the Pis fetch over TFTP: the kernels, device trees and
  `config.txt`.
- `root/` is the Raspberry Pi OS userland, with the fpgas.online packages
  and settings baked in.

The server used to build this tree itself. It ran the Pi roles inside the
ARM root under `qemu-user` emulation, which took about two hours per
rebuild. Since [issue #34](https://github.com/fpgas-online/fpgas.online-infra/issues/34)
and [PR #63](https://github.com/fpgas-online/fpgas.online-infra/pull/63), a
GitHub **arm64 runner** builds it instead. The runner executes the Pi's
32-bit ARM programs natively, with no emulation. The measurements showing
the runner is fit for this are in the trial-run PR,
[PR #40](https://github.com/fpgas-online/fpgas.online-infra/pull/40).
The runner publishes the result as a container image. Every server, tweed
and the CI test server alike, just downloads it
([`img/tasks/pull.yml`](../ansible/roles/img/tasks/pull.yml)).

The published image is **site-agnostic**: it contains no passwords, SSH
keys or site configuration. Each server adds its own site layer after
downloading the image, in the last play of `site.yml`
([`fixpi`](../ansible/roles/fixpi/tasks/main.yml)).

### 2.2 Where it is published: container registry tags (not git tags)

The image lives in GHCR at
[`ghcr.io/fpgas-online/nfsroot`](https://github.com/fpgas-online/fpgas.online-infra/pkgs/container/nfsroot).
It is an OCI (standard container) image, about 4 GB uncompressed and
about 1.6 GB compressed with zstd, split into **4 layers** of about equal
size.
Its filesystem holds `boot/` and `root/`.

Why 4 layers: GHCR serves a freshly pushed blob at only ~35–80 MB/s per
connection, and podman downloads an image's layers in parallel. Measured
with the real root, a cold `podman pull` takes 48–75 s for one layer and
23–33 s for four; eight layers gain nothing more, because podman applies
the layers one at a time. Splitting does not speed up the push. The
numbers are in the comment above `LAYERS` in
[`nfsroot_publish.py`](../tests/ci/nfsroot_publish.py), and in
[PR #144](https://github.com/fpgas-online/fpgas.online-infra/pull/144).
Each layer also carries the real entries of every directory above its
contents, so the split cannot change a directory's mode or owner; a test
in the Lint workflow round-trips the split through podman and tar
([§5](#5-lint-lintyml)).

The "tags" below are **image tags in that registry**: the name after the
`:` in `podman pull ghcr.io/fpgas-online/nfsroot:<tag>`. **Nothing is
tagged in git.**

[`tests/ci/nfsroot_publish.py`](../tests/ci/nfsroot_publish.py) (`tags()`)
gives each image several tags. The examples are from the `main` run
[36296701678](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36296701678/job/108556769990):

| Tag | Set by | Purpose | Example |
|---|---|---|---|
| `bookworm-armhf-YYYYMMDD-<sha7>` | every build | pinnable identity of this build; the workflow's `image` output. Pin one in a deploy for reproducibility | `bookworm-armhf-20260927-e56c662` ([e56c662](https://github.com/fpgas-online/fpgas.online-infra/commit/e56c662)) |
| `ci-<run_id>` | every build | the tag the VM test pulls. Its name is known before the build starts, so the VM test can start at the same time. Also the tag Promote copies | `ci-36296701678` |
| `inputs-<key>` | every build, pushed **last** | the inputs key: a fingerprint of everything that went into the image, so later runs can find it and reuse it ([§2.4](#24-how-the-path-is-chosen)) | `inputs-571171820156379b8eb3` |
| `bookworm-armhf` | **only the Promote job**, on `main`, after the test passed ([§4](#4-promotion-the-image-production-pulls)) | what production pulls unless a deploy pins another tag ([`img_nfsroot_image`](../ansible/roles/img/defaults/main.yml)), and the starting point for warm builds | `bookworm-armhf` |

The two stage images ([§2.3](#23-why-there-are-several-ways-to-produce-it))
live in the same repository under their own tags: `base-<base_key>`,
`upgraded-<upgraded_key>`, and `upgraded-<upgraded_key>-<UTC hour>` for
each refresh.

Every tag must point at a single image manifest
([OCI manifest](https://github.com/opencontainers/image-spec/blob/main/manifest.md)),
never at an [image index](https://github.com/opencontainers/image-spec/blob/main/image-index.md)
(a list of per-architecture manifests). `assert_plain_manifest()` in
`nfsroot_publish.py` checks this after each push.

The reason is architecture:

- The image is labelled arm64, and the servers are amd64.
- `podman pull` accepts a plain manifest of any architecture, but refuses
  an index that has no amd64 entry.
- `docker buildx imagetools create` produces exactly such an index, and it
  once broke the rolling tag.

[PR #104](https://github.com/fpgas-online/fpgas.online-infra/pull/104) fixed
that by copying tags with [skopeo](https://github.com/containers/skopeo)
`copy --preserve-digests` instead.

### 2.3 Why there are several ways to produce it

This section explains why Job 1 does not simply build the image from
nothing every time.

**The problem.** A full build starts from the RasPiOS release image,
whose packages are more than a year behind the archive. Downloading it,
upgrading every package and then running the Pi roles took about 9½
minutes ([example job](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36052538261/job/107811379717)).
The deploy test needs the image about 7 minutes in, so every such run
waited, and took 15–17½ minutes.

**The observation.** Most pull requests don't change what goes into the
image: they touch server roles, the web tier or tests. Most of those that
do change it only a little, such as one role or one package. Only a new
RasPiOS release, or a change to how RasPiOS is unpacked, changes its
foundation. And the slowest part of a full build, upgrading RasPiOS's
packages, is the same for every checkout.

So the build does the least work that still gives an image that is correct
for this checkout:

| Path | Used when | What it does | Why it is safe | Build job |
|---|---|---|---|---|
| **Reuse** | nothing that goes into the image changed since an earlier build this hour (same `inputs-<key>`) | copies the existing image's tags, and builds nothing | same inputs in the same hour produce the same image | [10 s](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36296701678/job/108556769990) |
| **Warm** | something changed, but the RasPiOS base is the same | unpacks the image production pulls, then runs the Pi roles over it, so only the difference gets applied | the roles are idempotent Ansible: running them on an already-configured tree only changes what differs. Tweed converged its live root this way for years | [4 min 21 s](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36294289660/job/108550142792) |
| **Stage** | the RasPiOS base changed, or a clean build was asked for | unpacks the published **upgraded stage** for this base, then runs every Pi role on it | the stage is exactly the tree a full build reaches before the Pi roles: RasPiOS with `apt upgrade` applied | [6 min 2 s](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36286653956/job/108528600883) |
| **Stage, built inline** | as above, but no stage exists yet for this base | builds the two stages first (download and unpack RasPiOS, then upgrade it), then runs every Pi role | the only way to switch to a new base, and it proves a clean build still works | [8 min 36 s](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36240570194/job/108400154022) |

**The two stage images** are built by
[`tests/ci/nfsroot_stages.py`](../tests/ci/nfsroot_stages.py):

- **base** (`base-<base_key>`,
  [`ci-nfsroot-base.yml`](../ansible/ci-nfsroot-base.yml)): the RasPiOS
  release image, downloaded and unpacked into `boot/` and `root/`.
- **upgraded** (`upgraded-<upgraded_key>`,
  [`ci-nfsroot-upgrade.yml`](../ansible/ci-nfsroot-upgrade.yml)): the base
  with every package upgraded, and nothing of ours added. It is about
  2 GB uncompressed and about 1 GB compressed.

A PR build that has to build them inline publishes only the upgraded
stage, so every later run for the same base unpacks it.

Three rules stop the shortcuts from producing a stale image:

- **The UTC hour is part of the inputs key.** The roles install whatever
  the package repositories serve at the time. So even an unchanged
  checkout gets a fresh (warm) build every hour it is tested, which picks
  up new packages.
- **Scheduled runs refresh the upgraded stage.** The VM test workflow has
  a schedule, `47 * * * *`. Its **stages** job runs first
  ([§2.7](#27-the-stages-job-scheduled-runs)) and rebuilds the upgraded
  stage on the published base, so a stage build starts from today's
  packages. The build job then builds the image as usual, and the run
  tests and promotes it.
- **About once a day, a scheduled run starts from scratch.** The first
  scheduled run after the published base stage turns 24 hours old
  rebuilds both stages from the RasPiOS download, and builds the image on
  them instead of warm. A warm image is layered on top of older ones, and
  can carry leftovers (removed files, old configuration) that a clean
  build would not have. This limits that drift to about a day, and keeps
  the full path tested.

**GitHub's schedule is best effort.** The schedule asks for hourly, but
from 2026-09-25 14:10 to 2026-10-02 18:41 UTC GitHub started only 36
scheduled runs, about one every 5 hours, and dropped the rest. The gaps
between them ranged from 10 minutes to 8½ hours. So production's image
can trail the package repositories by up to about 8½ hours. The daily
clean build goes by the base stage's age, not by which cron fired, so a
late or dropped run cannot skip it
([PR #133](https://github.com/fpgas-online/fpgas.online-infra/pull/133)).
A manual **from_scratch** run ([§7](#7-running-it-yourself)) also
publishes a new base stage, which restarts the 24 hours. For example,
after the from-scratch run
[36314142462](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36314142462)
on 2026-09-27 10:56 UTC, the next clean scheduled build was
[36436296084](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36436296084),
on 2026-09-28 14:28 UTC.

### 2.4 How the path is chosen

The build job tries each path in turn and takes the first that applies.

1. **Reuse?** The step "Reuse an image built from the same inputs" runs
   `nfsroot_publish.py --reuse`. It computes `key()` in
   [`tests/ci/nfsroot_inputs.py`](../tests/ci/nfsroot_inputs.py): a SHA-256
   of the UTC hour plus the content of every git-tracked file listed in
   `INPUTS`. Those are:
   - [`ci-nfsroot.yml`](../ansible/ci-nfsroot.yml) and the stage playbooks
     [`ci-nfsroot-base.yml`](../ansible/ci-nfsroot-base.yml),
     [`ci-nfsroot-upgrade.yml`](../ansible/ci-nfsroot-upgrade.yml) and
     [`ci-nfsroot-runner.yml`](../ansible/ci-nfsroot-runner.yml);
   - [`inventory-ci-nfsroot/`](../ansible/inventory-ci-nfsroot/) and the
     production group_vars it symlinks;
   - [`filter_plugins/`](../ansible/filter_plugins/) and
     [`ansible.cfg`](../ansible.cfg);
   - the roles [`img`](../ansible/roles/img/),
     [`fixpi`](../ansible/roles/fixpi/),
     [`nspawn_pi`](../ansible/roles/nspawn_pi/),
     [`fpgas_apt`](../ansible/roles/fpgas_apt/),
     [`cam_pi`](../ansible/roles/cam_pi/),
     [`onpi`](../ansible/roles/onpi/) and
     [`ssh_key_fetch`](../ansible/roles/ssh_key_fetch/) (`fixpi` includes
     it for the keys layer, which the build skips);
   - the TT catalogue template;
   - the build machinery itself: `nfsroot-build.yml`,
     [`.github/actions/nfsroot-setup`](../.github/actions/nfsroot-setup/action.yml),
     `tests/ci/`, `requirements.yml`, `pyproject.toml` and `uv.lock`.

   Run `uv run tests/ci/nfsroot_inputs.py --list` to print the list.

   If `inputs-<key>` already exists in the registry, its manifest is copied
   to this run's tags and every later step is skipped. For example, the
   `main` push after a PR merge usually reuses the PR's image, because the
   merged files are identical. Reuse is skipped when the stages job has
   just rebuilt the stages from scratch, because that run exists to build
   on them.

   [`tests/test_nfsroot_inputs.py`](../tests/test_nfsroot_inputs.py) fails
   if the build starts reading a role or file that `INPUTS` does not
   cover, so the key cannot silently go stale.
2. **Warm?** The step "Start from main's latest image (warm build)" runs
   [`tests/ci/nfsroot_warm.py`](../tests/ci/nfsroot_warm.py).
   - Every image carries the label `org.fpgas-online.nfsroot.base-key`,
     recording which RasPiOS base it descends from.
   - The **base key** (`base_key()`) covers only the image's identity:
     `dist`, `img_path`, `img_name` and `zip_name` as rendered from
     [`srv.yml`](../ansible/inventory/group_vars/all/srv.yml) and
     [`zz-ci-overrides.yml`](../ansible/inventory-ci-nfsroot/group_vars/all/zz-ci-overrides.yml),
     plus the files that download and unpack it: `ci-nfsroot-base.yml`,
     `ci-nfsroot-runner.yml`,
     [`img/tasks/build.yml`](../ansible/roles/img/tasks/build.yml) and
     [`img2files.sh`](../ansible/roles/img/files/img2files.sh).
   - If the label on `bookworm-armhf` matches this checkout's base key, the
     script copies that image down and untars its layers into
     `/srv/nfs/rpi/bookworm`. Since
     [PR #119](https://github.com/fpgas-online/fpgas.online-infra/pull/119),
     `bookworm-armhf` is always an image that passed the VM test, so a warm
     build never starts from an untested image.
3. **Otherwise the stage path.** The script logs
   `… descends from base X, this checkout's is Y: building on the upgraded RasPiOS stage instead`,
   and the step "Start from the upgraded RasPiOS stage" runs
   `nfsroot_stages.py ensure`:
   - it unpacks `upgraded-<upgraded_key>` if it is published. The
     **upgraded key** is the base key plus the files the upgrade reads
     (`UPGRADE_INPUTS`);
   - otherwise it unpacks the base stage (or builds it, downloading
     RasPiOS from the
     [`raspios-base`](https://github.com/fpgas-online/apt/releases/tag/raspios-base)
     release mirror, because downloads.raspberrypi.org has stalled CI for
     an hour), upgrades it, and publishes the upgraded stage.

### 2.5 Steps of the build job

The table lists every step of the job, with times from one warm and one
inline-stage run. Use it to see which step a failure or slowdown is in.

| Step | What it does | Warm ([job](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36294289660/job/108550142792)) | Stage, inline ([job](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36240570194/job/108400154022)) |
|---|---|---|---|
| Log in to GHCR | `docker login` with the workflow's `GITHUB_TOKEN` (`packages: write`) | 2 s | 1 s |
| Reuse … | [§2.4](#24-how-the-path-is-chosen) step 1; everything below runs only if nothing was reused | 1 s | 0 s |
| Set up the build | the [`nfsroot-setup`](../.github/actions/nfsroot-setup/action.yml) action: Python venv, the pinned Ansible collections (cached by [`requirements.yml`](../requirements.yml)'s hash), the RasPiOS download cache, and apt's downloaded `.deb`s from earlier builds (one cache entry per UTC day) | 16 s | 8 s |
| Start from main's latest image (warm build) | [§2.4](#24-how-the-path-is-chosen) step 2 | 62 s | 3 s (base mismatch) |
| Start from the upgraded RasPiOS stage | [§2.4](#24-how-the-path-is-chosen) step 3 | — | 184 s (built both stages) |
| **Build the NFS root** | `sudo ansible-playbook -i inventory-ci-nfsroot/hosts ci-nfsroot.yml`, in full ([§2.6](#26-what-ci-nfsrootyml-does)) | **107 s** | **261 s** |
| Tidy / save the deb cache | only on a cache miss: keep only the `.deb`s (apt's root-owned `lock` and `partial/` broke the save), then save | — | — |
| Write nfsroot manifest | [`nfsroot_manifest.py`](../tests/ci/nfsroot_manifest.py) lists packages, boot file hashes and configs, uploaded as the `nfsroot-manifest` artifact | 4 s | 3 s |
| Publish to GHCR | `nfsroot_publish.py`: tar the tree into 4 zstd layers, `skopeo copy` to each tag, then check each tag is a plain manifest | 61 s | 51 s |

### 2.6 What [`ci-nfsroot.yml`](../ansible/ci-nfsroot.yml) does

This playbook is the build itself. It runs the same Pi roles a deploy used
to run on the server, in the same order. The difference is that the "Pi" is
the unpacked tree on the runner. Ansible reaches it through the
[`community.general.chroot`](https://github.com/ansible-collections/community.general/blob/main/plugins/connection/chroot.py)
connection, which runs tasks inside a directory tree as if it were a
separate machine
([`inventory-ci-nfsroot/hosts`](../ansible/inventory-ci-nfsroot/hosts)).

The tree it works on is already in `/srv/nfs/rpi/bookworm`: the warm
image, or the upgraded stage. The playbook first refuses to run unless
`fixpi_image_build` is true (see the end of this section). Then it:

1. **Prepares the runner** ([`ci-nfsroot-runner.yml`](../ansible/ci-nfsroot-runner.yml)):
   disables any registered `qemu-arm` binfmt handler. (A binfmt handler
   tells the kernel to run foreign binaries through an emulator.) A runner
   image once shipped one, and it made the runner emulate ARM code it can
   run natively, slowing every apt run 4–10×.
2. **Checks the tree is in place**, and fails if `root/etc/os-release` is
   missing.
3. **Runs the generic [`fixpi`](../ansible/roles/fixpi/) layer**, with
   `fixpi_image_build: true`
   ([`all.yml`](../ansible/inventory-ci-nfsroot/group_vars/all/all.yml)).
4. **Prepares the chroot** with
   [`nspawn_pi/tasks/chroot-prep.yml`](../ansible/roles/nspawn_pi/tasks/chroot-prep.yml):
   - mounts `/proc`, `/sys`, `/dev` and friends;
   - adds a `policy-rc.d` so no services start;
   - holds initramfs rebuilds until the end;
   - sets dpkg `force-unsafe-io` and pauses man-db;
   - bind-mounts the deb cache over `/var/cache/apt/archives`.
5. **Runs the Pi roles against the chroot:**
   [`fpgas_apt`](../ansible/roles/fpgas_apt/),
   [`cam_pi`](../ansible/roles/cam_pi/) and
   [`onpi`](../ansible/roles/onpi/).
6. **Syncs the kernel payload, then cleans up.** It first syncs the
   upgraded kernel payload into `boot/`, so the pruner keeps what the Pis
   will really boot. Then [`nspawn_pi/tasks/stop.yml`](../ansible/roles/nspawn_pi/tasks/stop.yml)
   rebuilds stale initramfs images in parallel
   ([`nfsroot_kernels.py`](../ansible/roles/nspawn_pi/files/nfsroot_kernels.py)),
   prunes superseded kernels and unmounts everything.
7. **Makes the image site-agnostic and finishes `boot/`.** It points
   `resolv.conf` at the site gateway, deletes any SSH host keys that
   package installs generated and any `authorized_keys`, because every
   site brings its own. Then it re-syncs `boot/`.
8. **Asserts before anything is published:**
   - every board family's boot files exist (`kernel*.img` and the Pi 3, 4
     and 5 DTBs);
   - the key packages are `install ok installed`;
   - the `pi` user exists;
   - the image carries **none of the site layer**: no password hash for
     `pi`, no `authorized_keys`, no user keypairs and no `userconf.txt`.

**No tags anywhere.** The playbook runs in full, like every playbook in
CI and in a deploy ([issue #157](https://github.com/fpgas-online/fpgas.online-infra/issues/157)). The password and
SSH-key tasks belong to the site layer, which each server's own `fixpi`
run adds in the last play of `site.yml`; the VM test runs that play in
full. The image build leaves them out through a variable, not a tag:

- `fixpi` skips the site layer when `fixpi_image_build` is true, which
  only [`inventory-ci-nfsroot`](../ansible/inventory-ci-nfsroot/group_vars/all/all.yml)
  sets;
- the playbook refuses to start without that variable, so a run with the
  wrong inventory cannot write the runner's password and keys into the
  public image;
- the asserts above fail the build if any of the site layer is in the
  image anyway.

Until [PR #166](https://github.com/fpgas-online/fpgas.online-infra/pull/166) the build ran with `--skip-tags pipw,keys`,
the last tag skip in CI.
[`tests/test_no_tags.py`](../tests/test_no_tags.py) fails if a tag, or a
`--tags`/`--skip-tags` argument, comes back.

### 2.7 The stages job (scheduled runs)

On scheduled runs and on a manual dispatch with `from_scratch`, the
**stages** job runs before the build job, on another arm64 runner. It runs
[`nfsroot_stages.py`](../tests/ci/nfsroot_stages.py) in one of two modes:

| Mode | When | What it does | Time |
|---|---|---|---|
| refresh (`scheduled` → `upgraded`) | the published base stage is less than 24 hours old | unpacks the base stage, upgrades it, publishes a new upgraded stage | 3:03–3:37 over the 12 refreshes from 2026-09-29 to 2026-10-02, e.g. [3:23](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36944922891/job/110644729720) |
| full (`scheduled` → `all`, or `all` on dispatch) | the base stage is 24 hours old or more, missing, or has no build-time label | downloads RasPiOS, publishes a new base stage, upgrades it, publishes a new upgraded stage | 4:00–4:49 over the 4 full rebuilds in the same period, e.g. [4:08](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36741674923/job/109977344123) |

The stages job logs its decision, e.g. `base stage age: 18.9 h; rebuilding
upgraded`. It writes `scratch=true` after a full rebuild. The build job
then skips reuse and the warm start, and builds on the fresh stages.

The build job starts only when the stages job has finished. So on every
scheduled run, the image is published about 7 minutes after the run
starts even when the build itself is warm, which is about when the
server needs it ([§6.2](#62-scheduled-runs)).

---

## 3. Job 2: deploy and boot ([`vm-test.yml`](../.github/workflows/vm-test.yml), job "Server + Pi PXE Boot")

This section walks through the test job in order: the job's setup steps,
then what the test harness does step by step, then how the test
inventory differs from production. Read it when the VM test fails or gets
slower.

The example times come from the scheduled `main` run
[36944922891, job 110644729419](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36944922891/job/110644729419)
(2026-10-02 00:13 UTC).

### 3.1 Job setup (38–62 s)

The job runs in a `node:22-trixie` container with `--device=/dev/kvm`,
because the Pi emulator, `qemu-rpi-system-arm`, is built for Debian trixie.
It needs newer libraries than the runner's Ubuntu 24.04 has.

| Step | What it does |
|---|---|
| Initialize containers | pulls `node:22-trixie` |
| Install system dependencies | `qemu-system-x86`, `qemu-utils`, `cloud-image-utils`, `systemd-container`, `openssh-client`, `curl` |
| Install qemu-rpi packages | from rpi-qemu's signed apt repo (`https://fpgas.online/rpi-qemu/trixie/`), with two **hard minimum-version gates** (the versions are in the workflow file): `qemu-rpi-system-arm` must have the fix for a whole-VM freeze ([rpi-qemu#16](https://github.com/fpgas-online/rpi-qemu/pull/16)), and `qemu-rpi-pxeboot` must have the boot-directory fallback ([rpi-qemu#18](https://github.com/fpgas-online/rpi-qemu/pull/18)) |
| Enable KVM | the server VM uses KVM hardware acceleration; the Pi is always emulated in software (QEMU's TCG) |
| `uv sync`, collection cache, VM image cache | the [Debian 13 cloud image](https://cloud.debian.org/images/cloud/trixie/latest/) is cached under the key `vm-images-trixie-v1` |
| **Run VM integration tests** | `uv run tests/vm/run_tests.py --phase all --nfsroot-image ghcr.io/fpgas-online/nfsroot:ci-<run_id>` (13 min 18 s) |
| Show serial log tails | only on failure: prints the last 200 lines of every serial log into the job log |
| Upload serial logs | always: the `serial-logs` artifact |

The job timeouts, 180 min for this job and 60 min for each image job, are
far above normal run times. The real guards are the fail-fast checks inside
the harness, described below.

### 3.2 What [`run_tests.py`](../tests/vm/run_tests.py) does, step by step

Times are seconds since the harness started.

**0 s: the virtual switch.** `AccessPortSwitch(2101)` in
[`tests/vm/vswitch.py`](../tests/vm/vswitch.py) listens on two local
sockets:

- The server's second network card connects to the **trunk** port.
- The Pi's network card connects to the **access** port, where its frames
  get tagged into VLAN 2101.

VLAN 2101 is switch 1, port 1 in the per-port VLAN scheme
(`2000 + 100·switch + port`,
[`filter_plugins/port_vlans.py`](../ansible/filter_plugins/port_vlans.py)).
This is what a real Netgear S3300 access port does.

**0–12 s: the server VM** (`phase_server`; VM details in
[`tests/vm/vm_manager.py`](../tests/vm/vm_manager.py) `boot_server`).

- **Disk:** the harness uses the cached Debian 13 cloud image (tweed runs
  Debian 13) with a 20 GB overlay.
- **Key:** it generates a fresh ed25519 key for the run.
- **cloud-init seed** ([`tests/vm/cloud_init.py`](../tests/vm/cloud_init.py)):
  - installs **no packages**, because the roles install what they need,
    as on a fresh tweed;
  - brings up DHCP on the kernel's name for the first NIC;
  - pre-seeds a self-signed certificate at
    `/etc/letsencrypt/live/test.fpgas.online`;
  - recreates the accounts an older tweed has, for the deploy to fix:
    the server account under its old name, the `pi` and `tim` accounts
    trusting the revoked static keys, and the leftover `piroot` account
    from the removed `nspawn_pi` role, with its sudoers file and chroot
    login shell.
- **QEMU:** KVM with every runner CPU and 8 GB RAM. Disk writes skip host
  flushes (`cache=unsafe`), because the VM is thrown away afterwards.
  - NIC 1 is QEMU user networking (uplink, and SSH on port 2222), with
    `ipv6=off`.
  - NIC 2 is the VLAN trunk.
- **Readiness:** the harness waits for SSH, then for
  `cloud-init status --wait`, and fails if its exit code is non-zero.

**12–26 s: account checks before the deploy.** The fresh server still
accepts password logins, as a freshly installed tweed does. The harness
records that, checks that the revoked static keys and the whole `piroot`
account are there, and runs the `server_user` role in `--check` mode,
which must report the pending account rename without making it. The
dry run is its own one-role playbook,
[`tests/vm/server_user_check.yml`](../tests/vm/server_user_check.yml),
not a tagged run of `web.yml`.

**26–552 s: `ansible-playbook site.yml -i tests/inventory/test-hosts --limit test-vm -e img_nfsroot_image=…:ci-<run_id>`.**
This is the production playbook, run whole: the harness has **no tag
options, no `--become` and no key override**. The SSH key comes from
[`tests/inventory/group_vars/all/controller.yml`](../tests/inventory/group_vars/all/controller.yml),
and privilege escalation from [`ansible.cfg`](../ansible.cfg), exactly as
in production. The plays run in this order:

| # | Play | What happens | Time |
|---|---|---|---|
| 1 | [`apt_client`](../ansible/roles/apt_client/), [`netif`](../ansible/roles/netif/), then the install part of [`nfsroot_generation`](../ansible/roles/nfsroot_generation/tasks/install.yml) | `apt_client` writes the host's apt proxy settings (none in the test, see [§3.3](#33-how-the-test-inventory-differs-from-production-and-why)). `netif` renames the fresh VM's `enp0s2`/`enp0s3` to `eth-uplink`/`eth-local` by MAC address, moves the uplink onto a static systemd-networkd config and **reboots**: the path a newly installed tweed takes. Then `nfsroot-watchdog-server` is installed from its own apt repository, which is the server's first apt refresh (37 s). It moved here in [PR #159](https://github.com/fpgas-online/fpgas.online-infra/pull/159), so that the server's own apt source is rewritten before anything else refreshes the package lists | 68 s |
| 2 | [`img/tasks/prefetch.yml`](../ansible/roles/img/tasks/prefetch.yml) | installs podman and rsync, then starts `podman pull` **in the background**. Before #159 the first apt refresh happened here, and this play took about 40 s | 7 s |
| 3 | [`automation_user`](../ansible/roles/automation_user/), [`operators`](../ansible/roles/operators/), [`jump`](../ansible/roles/jump/), [`sshd`](../ansible/roles/sshd/), [`lldp`](../ansible/roles/lldp/), [`firewall`](../ansible/roles/firewall/), [`vlan_ports`](../ansible/roles/vlan_ports/), [`switch_vlans`](../ansible/roles/switch_vlans/), [`nfs`](../ansible/roles/nfs/), [`apt_cache`](../ansible/roles/apt_cache/), [`pxe`](../ansible/roles/pxe/) | real apt and pip installs on a fresh OS. `pxe` (dnsmasq DHCP/TFTP) comes before the web tier, because the site role drops config into `/etc/dnsmasq.d` | 2 min 55 s |
| 4 | [`uhubctl`](../ansible/roles/uhubctl/) | no hosts, same as production | 0 s |
| 5 | [`web.yml`](../ansible/web.yml) | [`server_user`](../ansible/roles/server_user/), [`site`](../ansible/roles/site/) (Django), [`wssh`](../ansible/roles/wssh/) (web terminal), [`stream_server`](../ansible/roles/stream_server/), [`mqtt`](../ansible/roles/mqtt/) (fleet broker), [`webrtc`](../ansible/roles/webrtc/), [`ttsite`](../ansible/roles/ttsite/) (tinytapeout) | 2 min 43 s |
| 6 | NFS root (last play) | see below | 1 min 54 s |

The last play does this, in order:

0. **Downloads the operators' GitHub keys** for the root's
   `authorized_keys` ([`fixpi/tasks/github_keys.yml`](../ansible/roles/fixpi/tasks/github_keys.yml),
   through [`ssh_key_fetch`](../ansible/roles/ssh_key_fetch/)). It is the
   one step of the root update that depends on another site, so it runs
   first: a GitHub outage then fails the run before the lock is taken and
   the live root touched ([PR #161](https://github.com/fpgas-online/fpgas.online-infra/pull/161)).
1. **Takes the NFS root update lock**
   ([`nfsroot_generation/begin.yml`](../ansible/roles/nfsroot_generation/tasks/begin.yml)),
   so booted boards never reboot into a half-built root
   ([PR #97](https://github.com/fpgas-online/fpgas.online-infra/pull/97)).
2. **Unpacks the image**
   ([`img/tasks/pull.yml`](../ansible/roles/img/tasks/pull.yml)). It waits
   for the background download and reports how long the download itself
   took: `pulled in 49 s (attempt 28)` in this run. The background loop
   had polled for about 4½ minutes, because on a scheduled run the image
   is published only after the stages job
   ([§2.7](#27-the-stages-job-scheduled-runs)). Then it runs the
   authoritative `podman pull`, and `podman image mount` +
   `rsync -aHAX --delete --checksum` copy the image into
   `/srv/nfs/rpi/bookworm` (29 s in this run; up to 75 s in others).
3. **Points the root's apt at the site's cache**
   ([`apt_cache/tasks/nfsroot.yml`](../ansible/roles/apt_cache/tasks/nfsroot.yml)).
4. **Applies the site layer** ([`fixpi`](../ansible/roles/fixpi/)):
   - the `pi` password and SSH keys, plus SSH host keys;
   - [`fleet.toml`](../ansible/roles/fixpi/tasks/fleet-site.yml);
   - the TT catalogue;
   - `pistat_host`;
   - the Orange Pi DTBs.
5. **Publishes the new root generation and releases the lock**
   ([`nfsroot_generation`](../ansible/roles/nfsroot_generation/)).

The play recap must end with `failed=0`; the playbook took 8 min 47 s
in this run. The tasks the recap counts as skipped are ones whose `when:`
condition is false on this host; nothing is skipped by tag.

**The pull retries only errors that can go away.** The retryable errors,
`img_pull_retryable` in
[`img/defaults/main.yml`](../ansible/roles/img/defaults/main.yml), are:

- `manifest unknown`, meaning the image isn't published yet;
- timeouts and connection resets;
- TLS errors, 5xx responses and 429s.

The test inventory raises the retry budget to 45 minutes
([`test-vm.yml`](../tests/inventory/host_vars/test-vm.yml)), so it covers
an image build that is still running. Any other error fails the play at
once: for example, an image podman can't use, or an auth failure.

**552 s: account checks after the deploy.** sshd must now offer only
public-key login (a password-only login is refused), the revoked static
keys must be gone, the account rename must have happened, and the
`piroot` account, its group, home, sudoers file and login shell must all
be gone ([PR #149](https://github.com/fpgas-online/fpgas.online-infra/pull/149)).

**553 s: the Pi is powered on** (`start_pi`). This is the moment a real
deploy's boards would be power-cycled over PoE. The emulator runs
`qemu-rpi-system-aarch64 -M raspi4b`, from
[rpi-qemu](https://github.com/fpgas-online/rpi-qemu), with the
`qemu-rpi-pxeboot` firmware: U-Boot plus an emulation of the Pi 4's network
boot sequence. The virtual Pi has no disk. From here, three things run
**at the same time**:

- **[`verify-server.yml`](../ansible/verify-server.yml)** runs in a
  background thread. It only reads, and its output is printed at the end.
  It runs every server role's own `verify/main.yml`, plus these checks:
  - the per-port VLAN interface, `dnsmasq --test`, the firewall's forward
    policy and `ports.conf`;
  - the NFS root's packages, and the nfsroot watchdog on both the server
    and the root;
  - that **the update lock was released**;
  - the `pi` user and its authorized_keys;
  - the site-layer files (`fleet.toml`, `tt-boards.yaml`);
  - the Wi-Fi-disable and EEPROM-write-protect lines in `config.txt`;
  - the web tier.

  Its run time is hidden behind the Pi phase.
- **The Pi boots.** `wait_for_pi_boot` watches both serial consoles:
  - **555 s:** DHCP from dnsmasq on `v2101`, then TFTP from the root's
    `boot/`. The firmware asks for `<serial>/start4.elf` first. When it
    doesn't find one, it falls back to the top-level directory, as the
    real bootloader does
    ([`TFTP_PREFIX`](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#TFTP_PREFIX):
    *"If neither start4.elf nor start.elf are found in the prefixed
    directory then the prefix is cleared"*).
  - **560 s:** the address from DHCP must be **10.21.1.1**, the per-port
    address of switch 1 port 1. Any other address fails the test.
  - **570 s:** the kernel starts ("Booting Linux"). If the firmware prints
    "No kernel image found" twice, the test fails at once instead of
    waiting 10 minutes.
  - **570–664 s:** the NFS root is mounted with overlayroot and userland
    starts, emulated at roughly a twentieth of real speed. The Pi counts
    as ready when SSH as `pi`, through the server, works.
- **664–792 s: [`verify-pi.yml`](../ansible/verify-pi.yml)** runs,
  alongside a password login as `pi` (`pi_password_login_works`). The
  password login is the path the board page's web terminal uses.
  - The playbook runs without sudo and without fact gathering. Each
    separate task costs seconds on the emulated Pi, so one task, "Collect
    the Pi's state" (79 s), runs a single Python script on the Pi that
    returns everything the checks need.
  - It checks:
    - the NFS and overlay mounts;
    - the per-port address and hostname;
    - that the Pi can ping the server;
    - sshd, and that the password is usable;
    - overlayroot and lldpd;
    - that the nfsroot watchdog is armed;
    - the fpgas.online openFPGALoader/OpenOCD builds;
    - the TT service and the demo bitstreams;
    - **the camera and the FPGA board, which it looks for on the Pi
      itself** ([PR #167](https://github.com/fpgas-online/fpgas.online-infra/pull/167)). It finds a camera the way
      `fpgas-cam` does, and reads what the boot check `fpgas-verify`
      ([PR #137](https://github.com/fpgas-online/fpgas.online-infra/pull/137)) found. Hardware that is there must work:
      a camera must be streaming, a board must have passed. A Pi with
      neither passes, and the log says so. The virtual Pi has neither,
      so it passes with `No camera on pi-sw1-p1: …` and
      `No FPGA board on pi-sw1-p1: fpgas-verify looked and found none`.
      Nothing is skipped by tag or by an inventory setting;
    - that ifupdown isn't failing;
    - the fleet agent.
  - **Fleet registration:** on the server, it fetches `/fleet/<serial>/`
    from the Django app. The page must show `badge online` and the Pi's
    **current boot id**, which proves this boot registered.
  - The tasks its recap counts as skipped are ones whose `when:` is
    false on the emulated Pi 4B:
    - checks for Orange Pi boards, the Pi 5 header UART and the USB
      gadget console;
    - the two tasks that wait for, and report on, a camera that was
      found;
    - a diagnostic that runs only when the watchdog check is about to
      fail;
    - "no kernel console on the header UART", which the test inventory
      turns off ([§3.3](#33-how-the-test-inventory-differs-from-production-and-why)).

**792–798 s: teardown.** The harness prints both results, and exits
non-zero if either side or the password login failed. On a `verify-pi`
failure it first prints the server's network view of the Pi (neighbour
table, ping, VLAN counters, dnsmasq/NFS journal) and the Pi's last 200
console lines.

### 3.3 How the test inventory differs from production, and why

The test uses its own Ansible inventory. This section lists where it
differs from production, so you can tell which production settings the
test does not cover.

[`tests/inventory`](../tests/inventory/) symlinks these production
group_vars:
[`ci.yml`](../ansible/inventory/group_vars/all/ci.yml),
[`firewall.yml`](../ansible/inventory/group_vars/all/firewall.yml),
[`srv.yml`](../ansible/inventory/group_vars/all/srv.yml),
[`ssh_keys.yml`](../ansible/inventory/group_vars/all/ssh_keys.yml) and
[`streaming.yml`](../ansible/inventory/group_vars/all/streaming.yml).

The rest is test-only:
[`all.yml`](../tests/inventory/group_vars/all/all.yml),
[`site.yml`](../tests/inventory/group_vars/all/site.yml),
[`ttsite.yml`](../tests/inventory/group_vars/all/ttsite.yml),
[`controller.yml`](../tests/inventory/group_vars/all/controller.yml),
[`host_vars/test-vm.yml`](../tests/inventory/host_vars/test-vm.yml) and
[`host_vars/test-pi.yml`](../tests/inventory/host_vars/test-pi.yml).
Compare them with [tweed's host_vars](../ansible/inventory/host_vars/fpgas.online.yml).
They differ from production only where the test environment forces them
to:

| Difference | Why |
|---|---|
| `test.fpgas.online` names; documentation-range and TEST-NET addresses | there is no real DNS |
| `site_certbot: false`, `ttsite_certbot: false`; cloud-init seeds a self-signed certificate | no public DNS or inbound port 80 for Let's Encrypt. The HTTPS vhost code path still renders |
| `switches_manage: false` | there is no physical switch to configure over SNMP. The switch CLI install and config rendering still run |
| uplink static 10.0.2.15/24 via 10.0.2.2, DNS 10.0.2.3 | QEMU user networking's fixed addresses |
| `img_pull_retries` / `img_pull_delay` raised | the image may still be building when the pull starts |
| one switch with one access port; `tt_boards` with the virtual Pi as `fpga-1`; a dummy `sunxi_boards` row | exercises the TT, fleet and Orange Pi config paths |
| `controller.yml` names `tests/vm/workdir/test_key` | the per-run key, instead of `~/.ssh/fpgas.online-ansible` |
| no `apt_client_proxy` / `apt_client_https_cache`: the server's apt fetches directly | tweed's apt proxy (`apt_client_proxy` in its host_vars) is on a private address that a GitHub runner cannot reach. The role still runs, and removes any proxy setting |
| `verify_pi_header_uart_console: true` ([`test-pi.yml`](../tests/inventory/host_vars/test-pi.yml)) | the emulator's boot firmware puts the kernel console on the header UART (`console=ttyAMA0`), because that serial port is the only record of the virtual Pi's boot. On a real Pi that UART goes to the FPGA, and `verify-pi` fails a console on it. `fixpi`'s own verify still checks the `cmdline.txt` that real Pis get |

---

## 4. Promotion: the image production pulls

Production's servers pull `ghcr.io/fpgas-online/nfsroot:bookworm-armhf`
unless a deploy pins another tag
([`img_nfsroot_image`](../ansible/roles/img/defaults/main.yml)). Since
[PR #119](https://github.com/fpgas-online/fpgas.online-infra/pull/119),
**no build moves that tag.** Only the **Promote to bookworm-armhf** job in
`vm-test.yml` does, and only:

- on `main` (pushes, schedules and dispatches, never PRs);
- after both the image build and the VM test succeeded. A failed or
  cancelled run skips it, and production stays on the last image that
  passed.

It runs `nfsroot_publish.py --promote ghcr.io/fpgas-online/nfsroot:ci-<run_id>`:
a `skopeo copy --preserve-digests` of the exact image the virtual Pi
booted, then the plain-manifest check. It takes about 15 s
([example job](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36296701678/job/108558498863)),
and prints the new digest in the job summary.

Runs on `main` overlap: each has its own concurrency group, so none waits
for or cancels another (a PR's runs share one group, and a new push
cancels the old run). So an older run can finish after a newer one. To
keep the tag moving forward anyway
([PR #151](https://github.com/fpgas-online/fpgas.online-infra/pull/151)):

- the Promote jobs run one at a time (a job-level concurrency group);
- each first runs
  [`nfsroot_promote_guard.py`](../tests/ci/nfsroot_promote_guard.py). It
  finds the last successful promotion through the Actions API, and lets
  the copy run only if this run is newer: a newer commit on `main`
  (checked with `git merge-base --is-ancestor`), or a later run of the same
  commit, whose packages are fresher. Otherwise the copy is skipped, and
  the job summary says why, for example
  `91bfdc5 is newer than the promoted c3874fb`
  ([example run](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36306604591)).

For example, on 2026-09-29 the scheduled run
[36507110122](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36507110122) (commit `fab6b03`) started a
minute before the push run
[36507218355](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36507218355) for #156's merge (`cad28f0`),
but finished after it. Its Promote job skipped the copy with
`cad28f0, a newer commit than fab6b03, is already promoted`.

Runs that started before #151 merged still ran the old, unguarded
workflow. On 2026-09-27 one of them,
[36306587951](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36306587951), finished last and moved the
tag back to an image from one commit earlier. That image had passed the
test too; the next guarded run moved the tag forward again.

If a third promotion arrives while one runs and one waits, GitHub replaces
the waiting one, which then shows as cancelled; the newest build still
gets promoted.

[`tests/test_nfsroot_promotion.py`](../tests/test_nfsroot_promotion.py)
fails if any build starts publishing `bookworm-armhf` again, or if the
promotion loses its dependency on the test.

---

## 5. Lint ([`lint.yml`](../.github/workflows/lint.yml))

The Lint workflow checks YAML style and Ansible best practice, and runs the
unit tests of the CI tooling. Every step can fail it. It has two jobs,
which run at the same time
([example run](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36702339845), 2 min 30 s).

**`ansible-lint` job** ([example](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36702339845/job/109844459855), 47 s):

| Step | What it does | Time |
|---|---|---|
| Install linters | `uv sync`: ansible-lint and yamllint, both pinned in [`pyproject.toml`](../pyproject.toml)'s dev group, so CI and a local `uv run ansible-lint` report the same thing | 1 s |
| Install Ansible collections | the pinned collections from [`requirements.yml`](../requirements.yml), cached. Without them ansible-lint cannot parse playbooks that use `ansible.posix` modules, and silently skips them | 1 s (cache hit) |
| `uv run yamllint -c .yamllint.yml ansible/` ([config](../.yamllint.yml)) | YAML style | 2 s |
| `uv run ansible-lint`, in `ansible/` ([config](../.ansible-lint)) | Ansible best practice, with no rule skipped | 36 s |

**`pytest` job** ([example](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36702339845/job/109844459529), 2 min 26 s):
`uv run pytest -q tests` runs the unit tests in [`tests/`](../tests/)
(129 s in the example). Among them:

- [`tests/test_nfsroot_inputs.py`](../tests/test_nfsroot_inputs.py) fails a
  PR that makes the image build read a file the inputs key does not cover
  ([§2.4](#24-how-the-path-is-chosen)).
- [`tests/test_nfsroot_promotion.py`](../tests/test_nfsroot_promotion.py)
  guards the promotion rules ([§4](#4-promotion-the-image-production-pulls)).
- [`tests/test_no_tags.py`](../tests/test_no_tags.py) fails if a task
  carries an Ansible tag, or the harness or a live doc passes `--tags` or
  `--skip-tags` ([§2.6](#26-what-ci-nfsrootyml-does)).
- [`tests/test_fixpi_site_layer.py`](../tests/test_fixpi_site_layer.py)
  holds every task that writes the `pi` password or a key behind the
  `fixpi_image_build` gate, so none can reach the public image.
- [`tests/test_ssh_key_fetch.py`](../tests/test_ssh_key_fetch.py) runs the
  key download against a local server, including the empty and failed
  answers that must fail the play.
- [`tests/test_nfsroot_layers_podman.py`](../tests/test_nfsroot_layers_podman.py)
  packs a root-owned tree with the real layer split, then checks that
  podman's overlay merge and an in-order `tar -x` both reproduce it entry
  for entry. It needs sudo and podman, which this job has
  (`NFSROOT_PODMAN_TEST=1`), and skips elsewhere.

> **Known gap:** the tests in
> [`tests/test_nfsroot_generation.py`](../tests/test_nfsroot_generation.py)
> are **skipped in CI** (pytest's summary line in the job log counts them
> as skipped). They run the
> `nfsroot_generation` role against the `nfsroot-generation` command, which
> lives in [fpgas-online/nfsroot-watchdog](https://github.com/fpgas-online/nfsroot-watchdog),
> and they find it only through `NFSROOT_GENERATION` or a sibling checkout
> of that repo. The pytest job has neither, so they run only on machines
> that happen to have `nfsroot-watchdog` checked out next to this repo.

To fix a lint failure, fix the flagged code. Use a scoped
`# noqa: <rule>` with a reason only when the construct is deliberate.

> **History:** ansible-lint was advisory from 2026-04-04 (commit
> [dd68a50](https://github.com/fpgas-online/fpgas.online-infra/commit/dd68a50)) until 2026-09-25. By then it reported 397
> violations ([log](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36063159621/job/107846648901)), 411
> with the collections installed, while the job stayed green. PRs
> [#109](https://github.com/fpgas-online/fpgas.online-infra/pull/109) to [#112](https://github.com/fpgas-online/fpgas.online-infra/pull/112) fixed them, and
> [#115](https://github.com/fpgas-online/fpgas.online-infra/pull/115) made it blocking. The rules
> still skipped then were fixed one PR at a time, until
> [#156](https://github.com/fpgas-online/fpgas.online-infra/pull/156) emptied the `skip_list`.

---

## 6. Timings

This section answers "how long will CI take for my change?", with the
measured runs behind the answer. The target is 15 minutes, and CI does
not meet it yet: of the 56 successful runs from 2026-09-29 01:40 to
2026-10-02 20:00 UTC, **26 took longer** (median 14:50). Leaving out the
13 runs in which a job waited for a runner, 13 of 43 took longer (median
14:24). The causes:

- **The test job varies by about ±2 minutes from run to run,** around a
  median of 13:48, so even runs that build no image sometimes go over
  ([§6.1](#61-runs-by-image-path), [§6.4](#64-where-the-time-goes);
  [issue #172](https://github.com/fpgas-online/fpgas.online-infra/issues/172)).
- **The daily full rebuild** takes 17½–22 minutes
  ([§6.2](#62-scheduled-runs);
  [issue #173](https://github.com/fpgas-online/fpgas.online-infra/issues/173)).
- **PRs whose image is built inline** take 16–17 minutes
  ([§6.1](#61-runs-by-image-path);
  [issue #174](https://github.com/fpgas-online/fpgas.online-infra/issues/174)).
- **Waiting for a free runner** delayed 13 runs, by 2 to 36 minutes
  ([§6.3](#63-waiting-queues-and-runners)). Seven of them started on
  2026-10-02 between 13:47 and 18:41 UTC, when another repository filled
  the organisation's 20 concurrent jobs
  ([issue #182](https://github.com/fpgas-online/fpgas.online-infra/issues/182)).

"Total" is from the run's creation to its completion: what a PR author
waits for.

### 6.1 Runs by image path

Two periods are shown, because the test job got about 45 s slower with
[PR #137](https://github.com/fpgas-online/fpgas.online-infra/pull/137)
(merged 2026-09-27 11:14 UTC; [§6.4](#64-where-the-time-goes)).

**Before #137:** every successful PR run from 2026-09-25 07:21 UTC (when
the stage images landed, [PR #114](https://github.com/fpgas-online/fpgas.online-infra/pull/114))
to 2026-09-27 05:00 UTC:

| Path | Runs | Build job | Best | Median | Worst | Over 15 min |
|---|---|---|---|---|---|---|
| reuse | 7 | 10–14 s | 10:58 | 12:30 | 14:42 | 0 |
| warm | 23 | 3:10–4:21 | 9:28 | 12:56 | 18:40 ¹ | 1 ¹ |
| stage, unpacked | 2 | 5:56–6:02 | 12:39 | 12:45 | 12:52 | 0 |
| **stage, built inline** | 7 | 8:30–9:13 | 14:11 | **15:21** | **16:10** | **4** |

¹ [Run 36209310049](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36209310049):
its VM job waited 6 min 8 s for a free runner. The work itself took about
12½ minutes.

**Now:** every successful PR, push and manual run from 2026-09-29 01:40
to 2026-10-02 20:00 UTC (scheduled runs are in [§6.2](#62-scheduled-runs)).
The last column counts the runs over 15 minutes in which a job waited
more than a minute for a runner; [§6.3](#63-waiting-queues-and-runners)
lists them:

| Path | Runs | Build job | Best | Median | Worst | Over 15 min | Of those, waited for a runner |
|---|---|---|---|---|---|---|---|
| reuse | 16 | 9–15 s | 11:07 | 14:40 | 40:08 | **7** | 3 ² |
| warm | 21 | 3:11–4:02 | 12:11 | 14:25 | 50:30 | **6** | 5 ³ |
| stage, unpacked | 2 | 6:05–6:28 | 18:40 | — | 22:08 | 2 | 2 ⁴ |
| **stage, built inline** | 1 | 10:02 | — | **16:46** | — | **1** | 0 ⁵ |

² The four that did not wait are [36510435368](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36510435368) (16:35),
[36568653672](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36568653672) (15:47),
[36509847933](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36509847933) (15:32) and
[36745444881](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36745444881) (15:22).
They did not wait for the image either: the test job itself took that
long. The nine other reuse runs that did not wait took 11:07–14:55.

³ The one that did not wait is [36995207259](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36995207259)
(16:13): its test job took 15:17. The 15 other warm runs that did not
wait took 12:11–14:49.

⁴ In [36564087355](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36564087355)
(22:08) the test job took 15:32, and the server also waited 2:00 for the
image. In [36565308568](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36565308568)
(18:40) the test job took 14:20.

⁵ [36534310607](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36534310607)
([PR #165](https://github.com/fpgas-online/fpgas.online-infra/pull/165)):
the server waited 2:40 for the image.

The **inline** path is the slow one: its image build takes 8½–10 minutes,
so the server waits 2–3 minutes for the image. It runs when a PR changes
a file in the base key ([§2.4](#24-how-the-path-is-chosen)) and no stage
exists yet for the new key. Edits that cannot change the image count
too: #165 only removed tags, and
[#156](https://github.com/fpgas-online/fpgas.online-infra/pull/156) only
added `mode:` to two tasks in
[`img/tasks/build.yml`](../ansible/roles/img/tasks/build.yml).
[Issue #135](https://github.com/fpgas-online/fpgas.online-infra/issues/135)
proposes ignoring comment and formatting changes.

### 6.2 Scheduled runs

Every successful scheduled run from 2026-09-29 01:40 to 2026-10-02 20:00
UTC. "Server waited" is how long the last play of `site.yml` sat waiting
for the image to be published and downloaded:

| Run | Started (UTC) | Stages job | Build job | VM test job | Server waited | Total |
|---|---|---|---|---|---|---|
| [36537059589](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36537059589) | 2026-09-29 07:30 | **full** [4:01](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36537059589/job/109303405669) | [5:59](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36537059589/job/109304733618) | [17:15](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36537059589/job/109303405370) | 3:14 | **17:37** |
| [36582031222](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36582031222) | 2026-09-29 14:21 | **full** [4:00](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36582031222/job/109452411177) ⁶ | [5:43](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36582031222/job/109456377088) | [21:23](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36582031222/job/109452409833) | 8:26 | **21:50** |
| [36620692256](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36620692256) | 2026-09-29 19:38 | refresh [3:05](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36620692256/job/109585021867) | [3:41](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36620692256/job/109586293947) | [13:16](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36620692256/job/109585021511) | 0:40 | 13:42 |
| [36643903238](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36643903238) | 2026-09-29 23:12 | refresh [3:05](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36643903238/job/109662305287) | [3:26](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36643903238/job/109663218211) | [15:21](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36643903238/job/109662304904) | 0 | **15:40** |
| [36659617330](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36659617330) | 2026-09-30 02:23 | refresh [3:33](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36659617330/job/109711252686) | [3:22](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36659617330/job/109712113504) | [15:50](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36659617330/job/109711252481) | 0 | **16:16** |
| [36693276700](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36693276700) | 2026-09-30 09:00 | refresh [3:21](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36693276700/job/109815203066) | [3:42](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36693276700/job/109816421383) | [14:30](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36693276700/job/109815202863) | 0 | 14:52 |
| [36741674923](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36741674923) | 2026-09-30 16:05 | **full** [4:08](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36741674923/job/109977344123) | [5:49](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36741674923/job/109979136168) | [17:21](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36741674923/job/109977343578) | 4:00 | **17:52** |
| [36776514024](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36776514024) | 2026-09-30 21:00 | refresh [3:10](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36776514024/job/110095680828) | [3:26](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36776514024/job/110096949282) | [13:23](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36776514024/job/110095680418) | 0 | 13:46 |
| [36798161680](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36798161680) | 2026-10-01 00:50 | refresh [3:29](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36798161680/job/110166260274) | [3:38](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36798161680/job/110167172384) | [13:53](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36798161680/job/110166259892) | 0:36 | 14:11 |
| [36832473473](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36832473473) | 2026-10-01 07:48 | refresh [3:22](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36832473473/job/110272006603) | [3:38](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36832473473/job/110273087517) | [13:43](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36832473473/job/110272005970) | 0:49 | 14:43 |
| [36886104035](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36886104035) | 2026-10-01 15:40 | refresh [3:35](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36886104035/job/110449683878) | [4:31](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36886104035/job/110451307086) | [15:19](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36886104035/job/110449683418) | 1:09 | **15:53** |
| [36922058426](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36922058426) | 2026-10-01 20:30 | **full** [4:49](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36922058426/job/110570255405) | [6:05](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36922058426/job/110572265450) | [18:06](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36922058426/job/110570255175) | 4:20 | **18:27** |
| [36944922891](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36944922891) | 2026-10-02 00:13 | refresh [3:23](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36944922891/job/110644729720) | [3:34](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36944922891/job/110645682617) | [14:03](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36944922891/job/110644729419) | 0 | 14:21 |
| [36972503965](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36972503965) | 2026-10-02 06:13 | refresh [3:03](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36972503965/job/110729252478) ⁷ | [4:04](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36972503965/job/110732668445) | [15:44](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36972503965/job/110729252247) | 5:03 | **35:45** |
| [37011333845](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/37011333845) | 2026-10-02 13:11 | refresh [3:27](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/37011333845/job/110851530228) | [3:41](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/37011333845/job/110852793328) | [14:47](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/37011333845/job/110851529795) | 0:09 | **15:10** |
| [37049118180](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/37049118180) | 2026-10-02 18:41 | refresh [3:37](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/37049118180/job/110977790436) ⁸ | [3:25](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/37049118180/job/110984148316) | [17:07](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/37049118180/job/110977790116) | 4:03 | **32:40** |

⁶ Its stages job started 5:05 late, waiting for a runner, which is why
the server waited so long.

⁷ Every job of this run waited for a runner: the VM test job 16:48, the
stages job 11:10, the build job 8:21 and the Promote job 3:00.

⁸ Three jobs of this run waited for a runner: the VM test job 14:17, the
stages job 13:27 and the build job 4:19.

- **Refresh runs** (12): the ten that did not wait for a runner took
  13:42–16:16, median 14:48, and four of them went over 15 minutes. Their
  build job waits for the stages job, so the image is published about 7
  minutes after the run starts, which is when the server needs it. In
  four of those ten, the server waited 36–69 s for it.
- **Full rebuilds** (4): **17:37–21:50**. The stages job (4–5 min) and
  the image build (about 6 min) run one after the other, so the image
  exists only 10–12 minutes in, and the server waits 3–4½ minutes for it.
  The two on 2026-09-29 ran because #156 and then #165 had changed the
  base key, so no base stage existed for the new key (`base stage age:
  unknown; rebuilding all`). The other two ran by age (25.6 h and 28.4 h).

### 6.3 Waiting: queues and runners

**Queueing behind other `main` runs is gone.** From
[PR #119](https://github.com/fpgas-online/fpgas.online-infra/pull/119)
until [PR #151](https://github.com/fpgas-online/fpgas.online-infra/pull/151)
(merged 2026-09-27), runs on `main` never overlapped, so merges that
landed close together queued behind each other, for up to 17 minutes
([36281006120](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36281006120),
30:28 in total). Since #151 each `main` run has its own concurrency
group, and none waits for another.

**Waiting for a free runner remains.** The `fpgas-online` organisation
is on GitHub's Free plan, which allows
[20 concurrent GitHub-hosted jobs](https://docs.github.com/en/actions/reference/limits)
for all its repositories together, arm64 and x64 alike. When other
repositories fill those 20, every job here queues behind them, and any job
of a run can be the one that waits. On 2026-10-02,
`fpgas.online-test-designs` kept all 20 busy for hours, with 100-210 jobs
queued; during the longest waits this repository held no runners at all
([issue #182](https://github.com/fpgas-online/fpgas.online-infra/issues/182)).
Nothing in this repository's workflows changes that. From 2026-09-29
01:40 to 2026-10-02 20:00 UTC, 13 of the 56 successful runs had a job
start more than a minute after it was created:

| Run | Started (UTC) | Jobs that waited, and how long | Total |
|---|---|---|---|
| [36564087355](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36564087355) | 2026-09-29 11:49 | build 5:22, VM test 3:21, Promote 2:54 | 22:08 |
| [36565308568](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36565308568) | 2026-09-29 12:00 | VM test 4:19, build 2:27 | 18:40 |
| [36582031222](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36582031222) | 2026-09-29 14:21 | stages 5:05 | 21:50 |
| [36702340569](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36702340569) | 2026-09-30 10:25 | Promote 4:20 | 16:51 |
| [36972503965](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36972503965) | 2026-10-02 06:13 | VM test 16:48, stages 11:10, build 8:21, Promote 3:00 | 35:45 |
| [36982886123](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36982886123) | 2026-10-02 08:14 | VM test 1:53 | 18:16 |
| [37015370658](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/37015370658) | 2026-10-02 13:47 | Promote 7:31 | 23:23 |
| [37017138832](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/37017138832) | 2026-10-02 14:02 | VM test 8:00, build 7:15 | 22:28 |
| [37017651504](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/37017651504) | 2026-10-02 14:07 | VM test 8:47 | 22:22 |
| [37020489834](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/37020489834) | 2026-10-02 14:31 | VM test 25:31, build 24:41 | 40:08 |
| [37028406410](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/37028406410) | 2026-10-02 15:38 | VM test 35:40, build 26:38 | 50:30 |
| [37035108506](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/37035108506) | 2026-10-02 16:36 | VM test 4:53, build 2:46 | 18:39 |
| [37049118180](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/37049118180) | 2026-10-02 18:41 | VM test 14:17, stages 13:27, build 4:19 | 32:40 |

A late stages or build job delays the image, so the server waits for it
inside the test job. A late Promote job delays only the run's completion:
the test result is already known.

### 6.4 Where the time goes

The test job, split into segments. The table compares the reference run
of [§3](#3-job-2-deploy-and-boot-vm-testyml-job-server--pi-pxe-boot) with
a run from before #137, and gives the range over the ten runs on commit
82721eb (`main` from 2026-09-30 to 2026-10-02) that did not do a full
rebuild:
[36702340569](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36702340569),
[36745444881](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36745444881),
[36748296341](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36748296341),
[36776514024](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36776514024),
[36798161680](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36798161680),
[36832473473](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36832473473),
[36854676867](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36854676867),
[36886104035](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36886104035),
[36944922891](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36944922891) and
[36972503965](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36972503965).

| Segment | Before #137 ([36296701678](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36296701678/job/108556769748)), s | Now ([36944922891](https://github.com/fpgas-online/fpgas.online-infra/actions/runs/36944922891/job/110644729419)), s | Share now | Range over ten runs, s |
|---|---|---|---|---|
| job setup (container, apt, qemu-rpi, caches) | 49 | 40 | 5 % | 38–62 |
| server VM boot + cloud-init | 12 | 12 | 1 % | 10–18 |
| account checks before the deploy | 14 | 14 | 2 % | 11–15 |
| `site.yml`: first play (netif, reboot, first apt refresh) | 33 | 68 | 8 % | 34–93 |
| `site.yml`: start the background pull | 41 | 7 | 1 % | 5–8 |
| `site.yml`: server roles | 130 | 175 | 21 % | 92–203 |
| `site.yml`: web tier | 156 | 163 | 19 % | 118–180 |
| `site.yml`: NFS root play | 137 | 114 | 14 % | 98–128, and 169–416 when the server waited for the image |
| account checks after the deploy | 1 | 1 | 0 % | 1–2 |
| Pi: power-on → kernel | 12 | 17 | 2 % | 12–17 |
| Pi: userland boot (emulated) | 95 | 94 | 11 % | 75–97 |
| `verify-pi` (with `verify-server` running alongside) | 91 | 128 | 15 % | 103–128 |
| teardown and post steps | 12 | 11 | 1 % | 10–15 |
| **whole job** | **783** | **843** | | 709–944, median 828 |

What changed, and what is noise:

- **`verify-pi` is about 25 s slower since #137.** Its "Collect the Pi's
  state" task took 48–54 s in the last four `main` runs before #137, and
  69–83 s in #137's own PR runs and in all ten successful runs measured
  right after it (79 s in the reference run). #137 replaced the Acorn
  boot check with `fpgas-verify`; the collector script barely changed.
  The cause is not yet pinned down.
- **The first apt refresh moved, at no net cost.**
  [PR #159](https://github.com/fpgas-online/fpgas.online-infra/pull/159)
  moved it from the second play to the first
  ([§3.2](#32-what-run_testspy-does-step-by-step)). The two plays
  together took 74 s before and 75 s now; the background image pull
  starts about 15 s later than it did.
- **The server and web tier roles vary by more than a minute from run
  to run,** mostly in apt and pip installs: the server roles took 92 s
  in one run and 203 s in another, on the same commit. This variance,
  not a change, is the difference between a 12-minute run and a
  16-minute one.
- **The NFS root play** is where the job waits for the image: on the
  full rebuild, on the stage paths, on scheduled runs, and whenever an
  image job waits for a runner.

### 6.5 Which case will my PR hit?

| Your change touches… | Image path | Expected VM test total |
|---|---|---|
| nothing in `INPUTS` (server roles, web tier, `tests/vm`, docs) | reuse, if an image for these inputs was built this hour; otherwise warm | ~11–16½ min, typically about 14½ |
| a Pi role, `ci-nfsroot.yml`, `uv.lock`, … | warm | ~12–16 min, typically about 14: the build finishes before the server needs the image |
| the upgrade (`ci-nfsroot-upgrade.yml`, `nspawn_pi`, …) | stage: the upgraded stage is rebuilt inline on the published base | ~15–16½ min (estimate: between the stage rows of [§6.1](#61-runs-by-image-path)) |
| the RasPiOS base: `dist`, `img_path`, `img_name`, `zip_name`, `ci-nfsroot-base.yml`, `ci-nfsroot-runner.yml`, `img/tasks/build.yml` or `img2files.sh` | stage, built inline | **~16–17 min**: over the target, because the server waits for the image |

Add any wait for a free runner ([§6.3](#63-waiting-queues-and-runners)).

Any other edit to [`srv.yml`](../ansible/inventory/group_vars/all/srv.yml)
gets a warm build
([PR #108](https://github.com/fpgas-online/fpgas.online-infra/pull/108)).
The `main` push after merging a base-key change takes the stage path on
the stage its PR published.

---

## 7. Running it yourself

You can run the same test locally. `--nfsroot-image` names the Pi root
image the server should download.

```bash
uv sync
# needs qemu-system-x86, qemu-utils, cloud-image-utils and the qemu-rpi
# packages (versions as in vm-test.yml); /dev/kvm makes the server fast
uv run tests/vm/run_tests.py --phase all \
    --nfsroot-image ghcr.io/fpgas-online/nfsroot:bookworm-armhf
# reproduce a CI run's exact image:
uv run tests/vm/run_tests.py --phase all \
    --nfsroot-image ghcr.io/fpgas-online/nfsroot:ci-36296701678
# server only, and keep the VM for poking at:
uv run tests/vm/run_tests.py --phase server --keep-vm --nfsroot-image …
```

To rebuild the image from scratch in CI, run the VM test workflow by hand
with **from_scratch** ticked (Actions → VM Integration Tests → Run
workflow). It rebuilds both stages and builds the image on them.

To compare a CI image with a live server's root:

1. Run [`tests/ci/nfsroot_manifest.py`](../tests/ci/nfsroot_manifest.py)` <root> <out>`
   on the server.
2. Diff the result against the build's `nfsroot-manifest` artifact with
   [`tests/ci/nfsroot_diff.py`](../tests/ci/nfsroot_diff.py).

---

## 8. When it fails: where to look

Find the symptom in the first column, then check the place in the last
column.

| Symptom | Likely cause | Where to look |
|---|---|---|
| `Pull the nfsroot image` fails at once | a pull error that can't be retried, e.g. a tag that became an image index ([§2.2](#22-where-it-is-published-container-registry-tags-not-git-tags)) | VM job log |
| the pull keeps retrying | the image job failed or is still running | the `nfsroot / build` job of the same run |
| `Report the background pull` shows a long `pulled in N s` | GHCR serving slowly; normal is ~25–45 s | VM job log |
| `the firmware found no kernel over TFTP (twice)` | `boot/` empty or wrong, or the TFTP root is misconfigured | `serial-logs` artifact, `pi-serial.log.uboot` |
| `the Pi got X from DHCP, expected 10.21.1.1` | a per-port DHCP or VLAN addressing bug | `pi-serial.log.uboot`; the dnsmasq journal in the failure dump |
| Pi SSH never becomes ready | the NFS root or overlayroot failed, or userland hung | `serial-logs` artifact, `pi-serial.log` (kernel console) |
| `Server has registered this Pi in the fleet` fails | agent not running, `fleet.toml` missing, or the MQTT broker or consumer broken | verify-pi output; the fleet agent state reported by the "Collect the Pi's state" task |
| verify-server fails | one of the roles' own `verify/main.yml` assertions | the "verify-server.yml output" block, printed after the Pi phase |
| `before converge` / `after converge` account check fails | the server account or sshd hardening changed | the `[server]` lines at the start and end of the harness output |
| build log: `… descends from base X, this checkout's is Y: building on the upgraded RasPiOS stage instead` | the base key changed, or the production image has no label | expected after a base change: the run takes the stage path |
| `nfsroot / stages` fails | the RasPiOS download, or `apt upgrade` in the base | the stages job log; the run's image is not built or promoted |
| `Promote to bookworm-armhf` fails | GHCR auth, or the copied tag is not a plain manifest | the promote job log. Production stays on the previous image |
| Promote is green but `bookworm-armhf` did not move | the guard found a newer build already promoted ([§4](#4-promotion-the-image-production-pulls)): expected when runs finish out of order | the promote job summary ("NOT promoting this run's image", then the reason) |
| `No ssh keys for <account> from gh:<user>: … after 3 retries` | GitHub (or Launchpad) did not serve an account's public keys. [`ssh_key_fetch`](../ansible/roles/ssh_key_fetch/) downloads `https://github.com/<user>.keys`, retries 3 times, then fails the play at the download. In the last play this happens before the NFS root is touched. It replaced `ssh-import-id`, whose rate-limited GitHub API calls failed two `main` runs on 2026-09-27 and 2026-09-28 ([PR #161](https://github.com/fpgas-online/fpgas.online-infra/pull/161)) | the VM job log, the `ssh_key_fetch` task that failed; re-run the job |
