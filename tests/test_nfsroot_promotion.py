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


def test_the_daily_schedule_is_the_build_workflows_scratch_cron():
    crons = [s["cron"] for s in triggers(workflow("vm-test.yml"))["schedule"]]
    assert workflow("nfsroot-build.yml")["env"]["SCRATCH_CRON"] in crons


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


def test_main_runs_are_never_cancelled_mid_run():
    concurrency = workflow("vm-test.yml")["concurrency"]
    assert concurrency["cancel-in-progress"] == \
        "${{ github.event_name == 'pull_request' }}"
