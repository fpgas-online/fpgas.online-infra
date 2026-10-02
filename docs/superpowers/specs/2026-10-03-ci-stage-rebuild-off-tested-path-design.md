# CI: take the stage rebuild off the tested path

Issue: [#173](https://github.com/fpgas-online/fpgas.online-infra/issues/173).
Status: design, for review. Nothing here is implemented.

## The problem

Scheduled VM test runs rebuild the RasPiOS stage images, and today the
image build waits for that rebuild:

```
stages job (3-5 min) ──► build job (3½-6 min) ──► image published
                                                    │
VM test job: deploy ... needs the image ~7½ min in ─┘
```

- **Ordinary scheduled runs** refresh only the upgraded stage (about 3
  minutes). The image build then waits for it, although it does not use
  it: it starts from production's image (a warm build). The only thing it
  takes from the stages job is the answer to "is this the daily clean
  build?". So the image is published about 7 minutes in, right when the
  server needs it. From 2026-09-29 to 2026-10-02, the server waited 36-69 s
  in 4 of the 10 refresh runs that did not queue for a runner, and 4 of
  those 10 took over 15 minutes.
- **The daily clean build**, the first scheduled run once the base stage
  is 24 hours old, downloads RasPiOS and rebuilds both stages (4-5
  minutes). Then it builds the image on them (about 6 minutes). The image
  exists only 10-12 minutes in, and the run takes 17½-22 minutes. All 4 in
  that period went over 15 minutes.

Two sequential arm64 jobs also mean two chances to queue for a runner
before the image exists.

## What must stay true

These come from the CI goal and from the decisions Tim made on
2026-10-03:

1. **A clean image is tested before it is promoted.** An image built on
   freshly rebuilt stages goes through the same VM test and Promote job as
   every other image. Nothing reaches `bookworm-armhf` untested.
2. **Runs during a rebuild use the current image.** While stages are being
   rebuilt, every other run (PRs, pushes, other scheduled runs) keeps
   building on production's current image, exactly as it does today.
3. **A failed stage rebuild blocks that run's promotion.** If the stages
   job fails, production stays on its last image, even though the tested
   image was built without the stages job.
4. **The clean build still happens at least about once a day**, and it
   still proves that the path from the RasPiOS download works.
5. **Nothing depends on which cron slot fires.** GitHub starts the hourly
   schedule about every 5 hours and drops the rest. Every decision must go
   by state in the registry, not by the time of day.
6. **The deploy under test is unchanged.** This design changes only how
   the image is produced. `site.yml`, the harness and the test inventory
   are untouched.

## The design

### 1. The stages job runs beside the build, not before it

The stages job moves out of `nfsroot-build.yml` and becomes its own job in
`vm-test.yml`. It runs on scheduled runs, and on a dispatch that asks for
it. It runs **in parallel** with the image build and the VM test. Its
logic does not change (`nfsroot_stages.py scheduled`): it refreshes the
upgraded stage, or rebuilds both stages from the RasPiOS download once
the base stage is 24 hours old.

The build job no longer `needs` it, and no longer reads its `scratch`
output.

**Why a separate job in the same run, not a separate workflow:**
decision 3 needs the stages job's result before promotion, and only a job
in the same run can feed into the Promote job's `needs`. One cron also
means one schedule that GitHub can drop, not two.

The stages job takes 3-5 minutes, less than the VM test (about 14), so
waiting for it before promoting delays nothing unless it queues for a
runner.

### 2. The clean build moves to the next scheduled run

Since the stages job now finishes after the build has started, the run
that rebuilds the stages cannot also build on them. The next scheduled
run does. It finds the new stages already published, builds the image on
them, and tests and promotes it like any other image.

This needs one new image label, recording which base stage an image's
line of builds started from:

- `org.fpgas-online.nfsroot.base-built`: the build time
  (`org.fpgas-online.nfsroot.built`) of the base stage the image's line
  of builds started from.
- **Builds on a stage** set it from that stage's base. The upgraded stage
  carries its base's build time too, so the build can read it from the
  stage it unpacked.
- **Warm builds** copy it from the image they started from.
- **Reuse** copies the whole manifest, so the label comes along.

The build job decides like this on a **scheduled** run:

| Production's image (`bookworm-armhf`) | The published stages | The build |
|---|---|---|
| `base-built` equals the base stage's build time | any | warm, as today |
| `base-built` differs or is missing | the upgraded stage carries the base stage's current build time | **clean**: unpack the upgraded stage, build on it, set `base-built` |
| `base-built` differs or is missing | the upgraded stage carries an older base (a stage rebuild is still running, or failed half-way) | warm, as today; the next scheduled run tries again |

On **every other run** (PRs, pushes, dispatches without `clean_build`),
the build is chosen exactly as today: reuse, then warm, then the stage
paths when the RasPiOS base key itself changed. A PR that runs while new
stages wait to be used still starts from production's image (decision 2).

Reuse is skipped on a scheduled run that decided on a clean build, as
today's `scratch=true` does.

A clean build that fails its test, or is not promoted, leaves
`bookworm-armhf` on the old line. The next scheduled run then sees the
same difference and tries again, with no extra state.

**Rejected alternative:** the stages job starts a follow-up VM test run
the moment a full rebuild finishes (a `workflow_dispatch` from the
workflow, which `GITHUB_TOKEN` is allowed to trigger). The clean image
would be tested about 20 minutes after the rebuild instead of about 5
hours later. It costs one extra run a day and a workflow that starts
another, and a dropped dispatch would lose the clean build until the next
day. Tim's decisions do not need the shorter delay, so this stays out
unless he asks for it.

### 3. Promotion

The Promote job `needs` the stages job too. Because the stages job is
skipped on most runs, the condition is written out:

```yaml
needs: [stages, nfsroot, vm-test]
if: >-
  !cancelled()
  && github.ref == 'refs/heads/main' && github.event_name != 'pull_request'
  && needs.nfsroot.result == 'success'
  && needs.vm-test.result == 'success'
  && needs.stages.result != 'failure'
```

The promotion guard (`nfsroot_promote_guard.py`) is unchanged.

### 4. Manual dispatch

Today's single `from_scratch` input rebuilds both stages and builds the
image on them in the same run. It becomes two inputs, which can be ticked
together:

- `rebuild_stages`: run the stages job in `all` mode (from the RasPiOS
  download). Its image build is chosen as on any other run.
- `clean_build`: build the image on the published upgraded stage instead
  of warm, and set `base-built`.

Ticking both builds the clean image on the stages that were published
before the run, while new ones are built beside it; the next scheduled
run then builds on the new ones.

## Expected effect

| Run | Now | After | Why |
|---|---|---|---|
| ordinary scheduled run | image at ~7 min; server waited 36-69 s in 4 of 10; median 14:48 | image at ~3½-4 min, like a warm PR build (median 13:52 without runner waits) | the build no longer waits for the stages job |
| run that rebuilds the stages from the download | 17½-22 min | same as an ordinary scheduled run | the rebuild runs beside the build |
| the next scheduled run, which builds clean | (did not exist) | about 13-15 min: an "unpack the published stage" build, 6-6¾ min | measured once without a runner wait: 13:13, the server waiting 1:27 for the image |

The clean-build run remains **close to the 15-minute target**. Its image
build (6-6¾ minutes) is about what the server allows (~6½ minutes). That
is #174's fourth idea, shaving the unpacked-stage build, and it should be
done with or after this change. Variance in the test job itself is #172.

Every scheduled run now uses two arm64 runners at the same time instead
of one after the other. If arm64 runners are what runs queue for, that
could make queueing worse; the runner-wait investigation running now
will say whether it is.

## Bootstrapping

No image carries `base-built` today. So the first scheduled run after
this merges sees "missing", finds the upgraded stage for the current
base, and does a clean build. From then on, the label is set.

## Testing

- **Unit tests** for the decision table above, in the style of the
  existing `tests/test_nfsroot_*.py`: every row, plus a missing label, an
  unreadable image and an unpublished stage.
- **Workflow structure tests**, extending `tests/test_nfsroot_promotion.py`:
  Promote needs the stages job and fails closed on its failure; the build
  job does not need the stages job; the stages job runs only on schedule
  and on `rebuild_stages` dispatches.
- **On the PR branch:** a dispatch with `clean_build` and one with
  `rebuild_stages` (dispatch runs from a branch never promote, because
  Promote runs only on `main`).
- **After merge:** schedules run only on `main`, so the scheduled
  behaviour can only be seen there. Watch the first scheduled runs: the
  bootstrap clean build, an ordinary run, and the first run after a stage
  rebuild from the download. Report their timings against the table
  above.

## What else changes

- `docs/ci.md` §2.3, §2.4, §2.5, §2.7, §4 and §6.2 describe the current
  order, and are updated in the implementing PR.
- The comments at the top of `vm-test.yml` and `nfsroot-build.yml`, and
  the docstrings of `nfsroot_stages.py` and `nfsroot_warm.py`, describe it
  too.
- `CLAUDE.md`'s description of the scheduled runs.

## Out of scope

- Making PRs that change the stage-build files faster (#174, #135).
- The test job's own duration and variance (#172).
- Runner queueing (being investigated separately).
