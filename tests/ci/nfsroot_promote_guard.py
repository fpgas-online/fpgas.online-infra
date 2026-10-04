#!/usr/bin/env python3
"""Refuse to move bookworm-armhf backwards.

Runs of the VM test on main overlap (vm-test.yml's concurrency), so an
older run can finish after a newer one. Its Promote job runs this first:
the tag moves only if the last successful promotion was not of a newer
build. "Newer" is by main's history, not by finishing order:

  - the last promoted commit strictly descends from this run's commit, or
  - it is the same commit (e.g. a push and a scheduled run of one main
    head) and that run was created after this one, so its packages are
    fresher.

The last promotion is the newest run of this workflow on main whose
"Point bookworm-armhf at the tested image" step succeeded (the Promote
jobs themselves run one at a time: vm-test.yml's job-level concurrency).

Writes the step output promote=true/false and says why in the job summary.
Needs GH_TOKEN (actions: read) and a full-history checkout.

usage: nfsroot_promote_guard.py
"""
import json
import os
import subprocess
import sys

PROMOTE_STEP = "Point bookworm-armhf at the tested image"


def gh(path: str) -> dict:
    out = subprocess.run(["gh", "api", path], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def is_ancestor(a: str, b: str) -> bool:
    """True if commit a is b or an ancestor of it (False if either is unknown)."""
    return subprocess.run(["git", "merge-base", "--is-ancestor", a, b]).returncode == 0


def last_promotion(repo: str, workflow: int | str, this_run: int) -> tuple[str, str, int] | None:
    """(head_sha, created_at, run id) of the newest earlier successful promotion."""
    runs = gh(f"repos/{repo}/actions/workflows/{workflow}/runs?branch=main&per_page=50")
    for run in sorted(runs["workflow_runs"], key=lambda r: r["created_at"], reverse=True):
        if run["id"] == this_run or run["event"] == "pull_request":
            continue
        for job in gh(f"repos/{repo}/actions/runs/{run['id']}/jobs")["jobs"]:
            for step in job.get("steps") or []:
                if step["name"] == PROMOTE_STEP and step["conclusion"] == "success":
                    return run["head_sha"], run["created_at"], run["id"]
    return None


def should_promote(ours: tuple[str, str], last: tuple[str, str] | None,
                   ancestor=is_ancestor) -> tuple[bool, str]:
    """Decide from (sha, created_at) of this run and of the last promotion."""
    if last is None:
        return True, "no earlier promotion found"
    (sha, created), (last_sha, last_created) = ours, last
    if last_sha == sha:
        if last_created > created:
            return False, f"a later run of {sha[:7]} (created {last_created}) already promoted"
        return True, f"a newer build of {sha[:7]} than the last promotion"
    if ancestor(sha, last_sha):
        return False, f"{last_sha[:7]}, a newer commit than {sha[:7]}, is already promoted"
    return True, f"{sha[:7]} is newer than the promoted {last_sha[:7]}"


def main() -> int:
    repo = os.environ["GITHUB_REPOSITORY"]
    this_run = int(os.environ["GITHUB_RUN_ID"])
    run = gh(f"repos/{repo}/actions/runs/{this_run}")
    workflow = run["workflow_id"]
    last = last_promotion(repo, workflow, this_run)
    go, why = should_promote((run["head_sha"], run["created_at"]),
                             last[:2] if last else None)
    lines = [f"## bookworm-armhf: {'promoting' if go else 'NOT promoting'} this run's image",
             "", f"- {why}"]
    if last:
        lines.append(f"- last promotion: run {last[2]} ({last[0][:7]}, created {last[1]})")
    text = "\n".join(lines)
    print(text, flush=True)
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(path, "a") as f:
            f.write(text + "\n")
    if out := os.environ.get("GITHUB_OUTPUT"):
        with open(out, "a") as f:
            f.write(f"promote={'true' if go else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
