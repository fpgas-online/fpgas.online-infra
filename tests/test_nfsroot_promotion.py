"""The rolling Pi root tag only ever names an image that passed the VM test.

ghcr.io/fpgas-online/nfsroot:bookworm-armhf is what production pulls unless
a deploy pins another tag (ansible/roles/img/defaults/main.yml). No build
may publish it: only vm-test.yml's promote job moves it, on main, after the
virtual Pi has netbooted the image and registered with the server.
"""
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests" / "ci"))
import nfsroot_promote_guard  # noqa: E402
import nfsroot_publish  # noqa: E402

WORKFLOWS = REPO / ".github" / "workflows"
ROLLING = f"{nfsroot_publish.IMAGE}:{nfsroot_publish.DIST}-armhf"


def workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text())


def triggers(wf: dict) -> dict:
    # PyYAML reads the bare key `on` as the boolean True.
    return wf.get("on", wf.get(True))


def test_no_build_publishes_the_rolling_tag(monkeypatch):
    for ref in ("main", "some-pr-branch"):
        monkeypatch.setenv("GITHUB_REF_NAME", ref)
        monkeypatch.setenv("NFSROOT_EXTRA_TAG", "ci-1")
        dated, inputs, others = nfsroot_publish.tags()
        assert ROLLING not in [dated, inputs] + others


def test_the_rolling_tag_matches_the_production_default():
    defaults = yaml.safe_load(
        (REPO / "ansible" / "roles" / "img" / "defaults" / "main.yml").read_text())
    assert defaults["img_nfsroot_image"] == ROLLING


def test_the_build_workflow_runs_only_under_the_vm_test():
    assert set(triggers(workflow("nfsroot-build.yml"))) == {"workflow_call"}
    assert workflow("vm-test.yml")["jobs"]["nfsroot"]["uses"] == \
        "./.github/workflows/nfsroot-build.yml"


def test_scheduled_runs_decide_scratch_by_age_not_by_cron():
    # GitHub starts schedules late or not at all, so nothing may depend on
    # which cron fired: one schedule, and the stages job picks the mode.
    assert len(triggers(workflow("vm-test.yml"))["schedule"]) == 1
    build = workflow("nfsroot-build.yml")
    text = (WORKFLOWS / "nfsroot-build.yml").read_text()
    assert "github.event.schedule" not in text
    assert "'scheduled'" in str(build["jobs"]["stages"]["steps"])
    steps = {s.get("id"): s for s in build["jobs"]["build"]["steps"]}
    scratch = "needs.stages.outputs.scratch != 'true'"
    assert scratch in steps["reuse"]["if"]
    assert scratch in steps["warm"]["if"]


def test_promotion_needs_the_build_and_the_boot_test_on_main():
    wf = workflow("vm-test.yml")
    promote = wf["jobs"]["promote"]
    assert set(promote["needs"]) == {"nfsroot", "vm-test"}
    # Default `needs` semantics: skipped unless both succeeded. An
    # always()/failure()/!cancelled() in the condition would defeat that.
    assert "()" not in promote["if"]
    assert "refs/heads/main" in promote["if"]
    step = promote["steps"][-1]["run"]
    assert "--promote" in step
    # The tag the boot test pulled, not the dated tag every build of the
    # commit that day overwrites.
    assert "ci-${{ github.run_id }}" in step
    assert "ci-${{ github.run_id }}" in \
        str(wf["jobs"]["vm-test"]["steps"])


def test_main_runs_overlap_and_only_prs_supersede():
    # A group per PR (a new push cancels the old run), a group per run on
    # main: main runs never queue behind or cancel each other.
    concurrency = workflow("vm-test.yml")["concurrency"]
    assert concurrency["group"] == \
        "vm-test-${{ github.event_name == 'pull_request' && github.ref || github.run_id }}"
    assert concurrency["cancel-in-progress"] is True


def test_promotions_run_one_at_a_time_behind_the_guard():
    promote = workflow("vm-test.yml")["jobs"]["promote"]
    assert promote["concurrency"] == {"group": "promote-bookworm-armhf",
                                      "cancel-in-progress": False}
    steps = promote["steps"]
    names = [s.get("name") for s in steps]
    guard = names.index("Check no newer image has been promoted")
    copy = names.index(nfsroot_promote_guard.PROMOTE_STEP)
    assert guard < copy
    assert "nfsroot_promote_guard.py" in steps[guard]["run"]
    assert steps[copy]["if"] == "steps.guard.outputs.promote == 'true'"
    # The guard finds earlier promotions by that step's name, and compares
    # commits with git, so it needs the Actions API and full history.
    assert promote["permissions"]["actions"] == "read"
    assert steps[0]["with"]["fetch-depth"] == 0
