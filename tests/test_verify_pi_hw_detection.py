"""verify-pi finds the camera and the FPGA on the Pi, instead of hw-* tags (issue #157).

Every Pi runs every check in verify-pi.yml. The camera and FPGA checks look
for that hardware on the Pi itself and assert on what they find: nothing
attached passes and says so, something attached that does not work fails.
These tests fail if:
  - anything still tags a task hw-camera / hw-fpga, or skips them,
  - an inventory tells verify-pi what hardware to expect (verify_pi_fpga_expect),
  - the camera probe stops finding what fpgas-cam's find_camera() finds, or
    counts the wrong sockets as the stream,
  - any camera or FPGA outcome gives the wrong verdict, run through the
    tasks exactly as verify-pi.yml writes them.
"""

import base64
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
ANSIBLE = REPO / "ansible"
VERIFY_PI = ANSIBLE / "verify-pi.yml"

PLAY = yaml.safe_load(VERIFY_PI.read_text())[0]


def _task(name: str) -> dict:
    matches = [t for t in PLAY["tasks"] if t.get("name") == name]
    assert len(matches) == 1, f"verify-pi.yml has {len(matches)} tasks named {name!r}"
    return dict(matches[0])


# --- no tags, no expectations ------------------------------------------------


def test_nothing_uses_the_hw_camera_or_hw_fpga_tags():
    live = [*ANSIBLE.rglob("*.yml"), *(REPO / "tests").rglob("*.py"), *(REPO / ".github").rglob("*.yml"),
            REPO / "README.md", *(REPO / "docs/superpowers/runbooks").glob("*.md")]
    for f in live:
        if f == Path(__file__):
            continue
        text = f.read_text()
        assert not re.search(r"\bhw-(camera|fpga)\b", text), f"{f} still mentions the hw-camera/hw-fpga tags"


def test_no_inventory_tells_verify_pi_what_hardware_to_expect():
    for f in [*ANSIBLE.rglob("*.yml"), *(REPO / "tests/inventory").rglob("*")]:
        if f.is_file():
            assert "verify_pi_fpga_expect" not in f.read_text(), f


def test_every_camera_and_fpga_check_runs_untagged():
    names = [
        "Assert fpgas-cam service enabled",
        "Wait for the camera found on this Pi to stream",
        "What the camera probe found",
        "What the camera checks decided",
        "Say that this Pi has no camera, so there is no stream to check",
        "Read fpgas-cam's status and journal",
        "Assert the camera streams if, and only if, this Pi has one",
        "Assert FPGA demo bitstreams present",
        "Wait for this boot's FPGA check (fpgas-verify) to finish",
        "What this boot's fpgas-verify run left",
        "Whether fpgas-verify ran and wrote this boot's report",
        "Read fpgas-verify's status and journal",
        "Assert fpgas-verify ran and wrote this boot's report",
        "What the FPGA boot check found",
        "Whether an FPGA board is attached",
        "Say that this Pi has no FPGA board, so there is none to check",
        "Read fpgas-verify's status and journal for the board that did not pass",
        "Assert the FPGA boot check passed on the board it found",
    ]
    for name in names:
        assert "tags" not in _task(name), name


# --- the camera probe ----------------------------------------------------------


def _probe() -> dict:
    namespace: dict = {}
    exec(PLAY["vars"]["verify_pi_camera_probe"], namespace)  # noqa: S102 -- the playbook's own code
    return namespace


def _tcp_row(remote_port: int, state: str) -> str:
    return f"   0: 0102150A:A1B2 0100150A:{remote_port:04X} {state} 00000000:00000000 00:00000000 00000000  1000 0 1 1"


def _tree(tmp_path: Path, subdevs=(), usb=(), tcp=(), tcp6=()) -> dict:
    v4l, byid, net = tmp_path / "v4l", tmp_path / "by-id", tmp_path / "net"
    for d in (v4l, byid, net):
        d.mkdir()
    for i, name in enumerate(subdevs):
        (v4l / f"v4l-subdev{i}").mkdir()
        (v4l / f"v4l-subdev{i}" / "name").write_text(name + "\n")
    for dev in usb:
        (byid / dev).write_text("")
    header = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
    (net / "tcp").write_text(header + "".join(r + "\n" for r in tcp))
    if tcp6 is not None:
        (net / "tcp6").write_text(header + "".join(r + "\n" for r in tcp6))
    return {"v4l": str(v4l), "byid": str(byid), "net": str(net)}


def test_probe_finds_a_bound_csi_sensor_and_nothing_else(tmp_path):
    camera = _probe()["camera"]
    paths = _tree(tmp_path, subdevs=["ov5647 10-0036", "rp1-cfe-csi2", "imx219 4-0010"])
    assert camera(**paths) == {"csi": ["ov5647 10-0036", "imx219 4-0010"], "usb": [], "rtmp": 0}


def test_probe_finds_a_usb_capture_device(tmp_path):
    camera = _probe()["camera"]
    grabber = "usb-MACROSILICON_USB_Video_1234-video-index0"
    paths = _tree(tmp_path, usb=[grabber, "usb-MACROSILICON_USB_Video_1234-video-index1"])
    assert camera(**paths) == {"csi": [], "usb": [str(tmp_path / "by-id" / grabber)], "rtmp": 0}


def test_probe_finds_nothing_on_a_pi_without_a_camera(tmp_path):
    camera = _probe()["camera"]
    assert camera(**_tree(tmp_path, tcp6=None)) == {"csi": [], "usb": [], "rtmp": 0}
    # And on a host with no video4linux class at all.
    assert camera(v4l=str(tmp_path / "none"), byid=str(tmp_path / "none"), net=str(tmp_path / "none")) == {
        "csi": [], "usb": [], "rtmp": 0}


def test_probe_counts_only_established_rtmp_connections(tmp_path):
    camera = _probe()["camera"]
    paths = _tree(tmp_path, tcp=[_tcp_row(1935, "01"), _tcp_row(1935, "02"), _tcp_row(22, "01")],
                  tcp6=[_tcp_row(1935, "01"), _tcp_row(1935, "0A")])
    assert camera(**paths)["rtmp"] == 2


# --- the verdicts, through verify-pi.yml's own tasks ----------------------------

CAMERA_TASKS = [
    "Wait for the camera found on this Pi to stream",
    "What the camera probe found",
    "What the camera checks decided",
    "Say that this Pi has no camera, so there is no stream to check",
    "Assert the camera streams if, and only if, this Pi has one",
]
FPGA_TASKS = [
    "What the FPGA boot check found",
    "Whether an FPGA board is attached",
    "Say that this Pi has no FPGA board, so there is none to check",
    "Read fpgas-verify's status and journal for the board that did not pass",
    "Assert the FPGA boot check passed on the board it found",
]
BOARD_JOURNAL = "journal: fpgas-verify checked the board"


def _fpga_tasks() -> list[dict]:
    """FPGA_TASKS, the journal read answered without the Pi (no systemctl or sudo here)."""
    tasks = [_task(n) for n in FPGA_TASKS]
    journal = tasks[FPGA_TASKS.index("Read fpgas-verify's status and journal for the board that did not pass")]
    journal["ansible.builtin.shell"] = f"echo '{BOARD_JOURNAL}'"
    journal["become"] = False
    return tasks


def _run(tmp_path: Path, tasks: list[dict], facts: dict) -> tuple[int, str]:
    playbook = tmp_path / "play.yml"
    playbook.write_text(yaml.safe_dump([{
        "hosts": "localhost", "connection": "local", "gather_facts": False,
        "vars": {"ansible_hostname": "pi-sw1-p1", **facts},
        "tasks": tasks,
    }]))
    (tmp_path / "ansible.cfg").write_text("[defaults]\n")
    env = dict(os.environ, ANSIBLE_CONFIG=str(tmp_path / "ansible.cfg"),
               ANSIBLE_STDOUT_CALLBACK="ansible.builtin.default", ANSIBLE_NOCOLOR="1",
               ANSIBLE_COLLECTIONS_PATH=str(tmp_path / "no-collections"),
               ANSIBLE_LOCALHOST_WARNING="False", ANSIBLE_INVENTORY_UNPARSED_WARNING="False")
    result = subprocess.run(["ansible-playbook", "-i", "localhost,", str(playbook)], env=env,
                            cwd=tmp_path, stdin=subprocess.DEVNULL, capture_output=True, text=True)
    return result.returncode, result.stdout + result.stderr


def _camera(csi=(), usb=(), rtmp=0, active="active", sub="running", status="0"):
    return {"csi": list(csi), "usb": list(usb), "rtmp": rtmp,
            "unit": {"ActiveState": active, "SubState": sub, "Result": "success" if status == "0" else "exit-code",
                     "ExecMainStatus": status, "NRestarts": "0"}}


NO_CAMERA_GAVE_UP = _camera(active="failed", sub="failed", status="78")
NO_CAMERA_LOOKING = _camera()
CSI_STREAMING = _camera(csi=["ov5647 10-0036"], rtmp=1)
USB_STREAMING = _camera(usb=["/dev/v4l/by-id/usb-MS2109-video-index0"], rtmp=1)
CSI_RESTARTING = _camera(csi=["ov5647 10-0036"], active="activating", sub="auto-restart", status="1")
CSI_UP_NOT_CONNECTED = _camera(csi=["ov5647 10-0036"])
UNSEEN_BUT_STREAMING = _camera(rtmp=1)


@pytest.mark.parametrize(("collected", "second_look", "passes", "says_no_camera"), [
    pytest.param(NO_CAMERA_GAVE_UP, None, True, True, id="no camera, fpgas-cam gave up (78): pass"),
    pytest.param(NO_CAMERA_LOOKING, None, True, True, id="no camera, fpgas-cam still looking: pass"),
    pytest.param(CSI_STREAMING, None, True, False, id="CSI camera streaming: pass"),
    pytest.param(USB_STREAMING, None, True, False, id="USB grabber streaming: pass"),
    pytest.param(CSI_RESTARTING, CSI_STREAMING, True, False, id="CSI camera streams on the second look: pass"),
    pytest.param(CSI_RESTARTING, CSI_RESTARTING, False, False, id="CSI camera, fpgas-cam restarting: fail"),
    pytest.param(CSI_UP_NOT_CONNECTED, CSI_UP_NOT_CONNECTED, False, False,
                 id="CSI camera, fpgas-cam up but not connected: fail"),
    pytest.param(UNSEEN_BUT_STREAMING, None, False, False, id="no camera found, yet streaming: fail"),
])
def test_camera_verdict(tmp_path, collected, second_look, passes, says_no_camera):
    tasks = [_task(n) for n in CAMERA_TASKS]
    # The second look, without the Pi: echo what it would find, once.
    wait = tasks[0]
    wait["ansible.builtin.command"] = {"argv": ["echo", json.dumps(second_look or {})]}
    wait.update(retries=0, delay=0)
    rc, output = _run(tmp_path, tasks, {"verify_pi_camera_collected": collected})
    assert (rc == 0) == passes, output
    assert ("TASK [Wait for the camera found on this Pi to stream]" in output
            and "skipping: [localhost]" not in output.split("TASK [Wait")[1].split("TASK [")[0]) == (
        second_look is not None), output
    assert ("No camera on pi-sw1-p1" in output) == says_no_camera, output


def _report(**report) -> dict:
    return {"content": base64.b64encode(json.dumps({"schema_version": 2, **report}).encode()).decode()}


ARTY = {"board": "arty", "variant": "a7-35t", "found": {"usb": "1-1.2"}}


@pytest.mark.parametrize(("report", "passes", "says_no_board"), [
    pytest.param({"msg": "not readable or missing"}, False, False, id="no report: fail"),
    pytest.param(_report(result="pass", mode="auto", chosen_by="auto: USB/PCI IDs",
                         boards=[{**ARTY, "result": "pass"}]), True, False, id="board found and passed: pass"),
    pytest.param(_report(result="missing", mode="auto", boards=[],
                         reason="none of the installed boards (arty, fomu) was found",
                         state={"recorded": False}), True, True, id="no board attached: pass"),
    pytest.param(_report(result="missing", mode="arty", boards=[], reason="no Arty A7 found"),
                 False, False, id="configured board missing: fail"),
    pytest.param(_report(result="missing", mode="auto", boards=[], reason="none found",
                         state={"changes": ["arty: recorded, not found now"]}),
                 False, False, id="board recorded here before, now gone: fail"),
    pytest.param(_report(result="fail", mode="auto", boards=[{**ARTY, "result": "fail", "reason": "uart test"}]),
                 False, False, id="board found, check failed: fail"),
    pytest.param(_report(result="degraded", mode="auto", boards=[{**ARTY, "result": "degraded"}]),
                 False, False, id="board found, degraded: fail"),
    pytest.param(_report(result="changed", mode="auto", boards=[{**ARTY, "result": "pass"}],
                         state={"changes": ["arty.serial: was 'a', now 'b'"]}),
                 False, False, id="board changed: fail"),
    pytest.param(_report(result="error", mode="auto", boards=[], reason="no FPGA board is configured"),
                 False, False, id="check could not run: fail"),
])
def test_fpga_verdict(tmp_path, report, passes, says_no_board):
    rc, output = _run(tmp_path, _fpga_tasks(), {"verify_pi_fpga_verify_report": report})
    assert (rc == 0) == passes, output
    # fpgas-verify's journal is read, and shown, only for a found board that did not pass.
    assert (BOARD_JOURNAL in output) == (not passes and "content" in report), output
    assert ("No FPGA board on pi-sw1-p1" in output) == says_no_board, output


# --- Ansible checks that fpgas-verify works ------------------------------------
#
# verify-pi waits for this boot's fpgas-verify run to finish, then checks it
# ran, exited as fpgas-verify does and wrote this boot's report, before the
# verdict above is judged. The probe is run here against a fake systemctl and
# a scratch report; its output then goes through verify-pi.yml's own tasks.

RUN_TASKS = [
    "Wait for this boot's FPGA check (fpgas-verify) to finish",
    "What this boot's fpgas-verify run left",
    "Whether fpgas-verify ran and wrote this boot's report",
    "Read fpgas-verify's status and journal",
    "Assert fpgas-verify ran and wrote this boot's report",
]

BOOTED_RUN = "Sat 2026-10-03 01:00:00 UTC"  # when this boot's fpgas-verify run started
BOOTED_RUN_ISO = "2026-10-03T01:00:00+00:00"
FINISHED_ISO = "2026-10-03T01:00:33+00:00"


def _fpga_verify_probe() -> dict:
    namespace: dict = {}
    exec(PLAY["vars"]["verify_pi_fpga_verify_probe"], namespace)  # noqa: S102 -- the playbook's own code
    return namespace


def _show(active, sub, result, status, exited=True, start=BOOTED_RUN) -> str:
    """`systemctl show` of fpgas-verify.service, as TZ=UTC prints it."""
    return "\n".join([
        f"ActiveState={active}", f"SubState={sub}", f"Result={result}", f"ExecMainStatus={status}",
        f"ExecMainStartTimestamp={start}",
        f"ExecMainExitTimestamp={'Sat 2026-10-03 01:00:33 UTC' if exited else ''}",
        f"ExecMainExitTimestampMonotonic={'95000000' if exited else '0'}",
        "ActiveEnterTimestamp=", "InactiveEnterTimestamp=",
    ]) + "\n"


QUEUED = _show("inactive", "dead", "success", "0", exited=False, start="")
RUNNING = _show("activating", "start", "success", "0", exited=False)
PASSED = _show("active", "exited", "success", "0")
NO_PASS = _show("failed", "failed", "exit-code", "1")
NEVER_RAN = _show("inactive", "dead", "success", "0", exited=False, start="")
KILLED = _show("failed", "failed", "timeout", "9")
START_JOB = "123 fpgas-verify.service start waiting\n"


def _poll(tmp_path: Path, show: str, report=None, jobs: str = "") -> str:
    """What the probe prints for one poll: `systemctl show`/`list-jobs` and the report as given."""
    poll = tmp_path / f"poll{len(list(tmp_path.glob('poll*')))}"
    poll.mkdir()
    (poll / "show").write_text(show)
    (poll / "jobs").write_text(jobs)
    systemctl = poll / "systemctl"
    systemctl.write_text(f'#!/bin/sh\ncase "$1" in\n  show) cat {poll}/show; echo "TZ=$TZ"; echo "LC_ALL=$LC_ALL";;\n'
                         f'  list-jobs) cat {poll}/jobs;;\nesac\n')
    systemctl.chmod(0o755)
    path = poll / "verify.json"
    if report is not None:
        path.write_bytes(report if isinstance(report, bytes) else json.dumps(report).encode())
    out = _fpga_verify_probe()["fpga_verify"](report=str(path), systemctl=str(systemctl))
    return json.dumps(out)


def _verify_json(result, checked_at=BOOTED_RUN_ISO, **report) -> dict:
    return {"schema_version": 2, "result": result, "checked_at": checked_at, **report}


NO_BOARD = _verify_json("missing", mode="auto", boards=[], reason="none of the installed boards was found",
                        state={"recorded": False})
BOARD_PASSED = _verify_json("pass", mode="auto", boards=[{**ARTY, "result": "pass"}])
BOARD_FAILED = _verify_json("fail", mode="auto", boards=[{**ARTY, "result": "fail", "reason": "uart test"}])


def test_probe_reads_the_unit_in_utc_then_the_report(tmp_path):
    out = json.loads(_poll(tmp_path, PASSED, BOARD_PASSED, jobs=START_JOB))
    assert out["unit"]["TZ"] == "UTC"
    assert out["unit"]["LC_ALL"] == "C"  # English day names for strptime's %a
    assert out["unit"]["ActiveState"] == "active"
    assert out["unit"]["ExecMainStartEpoch"] == 1790989200.0  # 2026-10-03T01:00:00Z
    assert out["unit"]["ExecMainExitEpoch"] == 1790989233.0  # 2026-10-03T01:00:33Z
    assert out["jobs"] == ["123 fpgas-verify.service start waiting"]
    assert json.loads(base64.b64decode(out["report"]["content"])) == BOARD_PASSED
    assert out["parsed"] == {"result": "pass", "checked_at": BOOTED_RUN_ISO, "checked_at_epoch": 1790989200.0}


def test_probe_says_what_is_wrong_with_a_report(tmp_path):
    assert "msg" in json.loads(_poll(tmp_path, PASSED))["report"]
    assert json.loads(_poll(tmp_path, PASSED, b"{\"result\": \"pa"))["parsed"]["error"].startswith("not JSON")
    assert json.loads(_poll(tmp_path, PASSED, b"[]"))["parsed"]["error"] == "not a JSON object"
    no_time = json.loads(_poll(tmp_path, PASSED, {"result": "pass"}))
    assert no_time["parsed"]["checked_at_epoch"] is None
    unset = json.loads(_poll(tmp_path, NEVER_RAN))
    assert unset["unit"]["ExecMainStartEpoch"] is None


def _run_check(tmp_path: Path, polls: list[str], retries: int = 3) -> tuple[int, str]:
    """verify-pi.yml's fpgas-verify tasks and FPGA verdict, the wait answering with `polls` in turn."""
    script = tmp_path / "polls.py"
    script.write_text(
        "import json, pathlib, sys\n"
        f"PROBE_CRASHED, PROBE_SILENT = {PROBE_CRASHED!r}, {PROBE_SILENT!r}\n"
        f"polls = json.loads({json.dumps(json.dumps(polls))})\n"
        f"seen = pathlib.Path({str(tmp_path / 'seen')!r})\n"
        "n = int(seen.read_text()) if seen.exists() else 0\n"
        "seen.write_text(str(n + 1))\n"
        "poll = polls[min(n, len(polls) - 1)]\n"
        "if poll == PROBE_CRASHED:\n"
        "    sys.exit('Traceback (most recent call last):\\nNameError: name fpga_verify is not defined')\n"
        "print(poll, end='' if poll == PROBE_SILENT else '\\n')\n")
    tasks = [_task(n) for n in RUN_TASKS] + _fpga_tasks()
    tasks[0]["ansible.builtin.command"] = {"argv": [sys.executable, str(script)]}
    tasks[0].update(retries=retries, delay=0)
    tasks[3]["ansible.builtin.shell"] = "echo 'journal: fpgas-verify finished'"
    tasks[3]["become"] = False
    return _run(tmp_path, tasks, {
        "verify_pi_fpga_verify_probe": PLAY["vars"]["verify_pi_fpga_verify_probe"],
        "verify_pi_fpga_verify_results": PLAY["vars"]["verify_pi_fpga_verify_results"],
    })


RAN_ASSERT = "TASK [Assert fpgas-verify ran and wrote this boot's report]"
VERDICT_ASSERT = "TASK [Assert the FPGA boot check passed on the board it found]"


def _failed_at(output: str) -> str:
    """The name of the task that failed, or ''."""
    tasks = re.findall(r"TASK \[([^\]]+)\][^\n]*\n(?:(?!TASK \[).)*?fatal: \[localhost\]", output, re.S)
    return tasks[-1] if tasks else ""


def test_waits_while_the_run_is_queued_and_running_then_judges_it(tmp_path):
    polls = [_poll(tmp_path, QUEUED, jobs=START_JOB), _poll(tmp_path, RUNNING, jobs=START_JOB),
             _poll(tmp_path, PASSED, BOARD_PASSED)]
    rc, output = _run_check(tmp_path, polls)
    assert rc == 0, output
    assert output.count("FAILED - RETRYING: [localhost]: Wait for this boot's FPGA check") == 2, output
    assert "journal: fpgas-verify finished" not in output, output


def test_a_finished_run_is_not_waited_for(tmp_path):
    rc, output = _run_check(tmp_path, [_poll(tmp_path, PASSED, BOARD_PASSED)])
    assert rc == 0, output
    assert "FAILED - RETRYING" not in output, output


def test_exit_1_with_no_board_is_fpgas_verify_working(tmp_path):
    rc, output = _run_check(tmp_path, [_poll(tmp_path, NO_PASS, NO_BOARD)])
    assert rc == 0, output
    assert "No FPGA board on pi-sw1-p1" in output, output


def test_exit_1_for_a_failed_board_fails_the_verdict_not_fpgas_verify(tmp_path):
    rc, output = _run_check(tmp_path, [_poll(tmp_path, NO_PASS, BOARD_FAILED)])
    assert rc != 0, output
    assert _failed_at(output) == "Assert the FPGA boot check passed on the board it found", output
    assert "uart test" in output, output
    assert f"fpgas-verify: ['{BOARD_JOURNAL}']" in output, output


def test_a_board_that_passed_does_not_read_the_journal(tmp_path):
    rc, output = _run_check(tmp_path, [_poll(tmp_path, PASSED, BOARD_PASSED)])
    assert rc == 0, output
    assert BOARD_JOURNAL not in output, output


@pytest.mark.parametrize(("show", "report", "says"), [
    pytest.param(PASSED, _verify_json("pass", checked_at="2026-10-02T22:13:07+00:00", mode="auto", boards=[]),
                 "is not from this boot's run", id="report from a previous boot"),
    pytest.param(PASSED, None, "no /run/fpgas-online/verify.json", id="no report"),
    pytest.param(PASSED, b"{\"schema_version\": 2, \"resu", "verify.json does not parse", id="garbled report"),
    pytest.param(PASSED, _verify_json("ok"), "is not one fpgas-verify defines", id="unknown result"),
    pytest.param(PASSED, {"schema_version": 2, "result": "pass"}, "is not from this boot's run",
                 id="report without checked_at"),
    pytest.param(NEVER_RAN, None, "has not run this boot", id="never ran this boot"),
    pytest.param(KILLED, BOARD_PASSED, "ended as fpgas-verify never does", id="killed or timed out"),
    pytest.param(PASSED, BOARD_FAILED, "fpgas-verify exited 0 but its report says fail", id="exit and report disagree"),
    pytest.param(PASSED, _verify_json("fail", checked_at=FINISHED_ISO, mode="auto", boards=[]),
                 "fpgas-verify exited 0 but its report says fail",
                 id="report from the second the boot run exited is the boot run's"),
    pytest.param(_show("failed", "failed", "exit-code", "2"), BOARD_FAILED, "ended as fpgas-verify never does",
                 id="exit 2 (a usage error)"),
])
def test_fpgas_verify_not_working_fails_with_why(tmp_path, show, report, says):
    rc, output = _run_check(tmp_path, [_poll(tmp_path, show, report)])
    assert rc != 0, output
    assert _failed_at(output) == "Assert fpgas-verify ran and wrote this boot's report", output
    assert says in output, output
    assert "journal: fpgas-verify finished" in output, output


LATER = "2026-10-03T01:05:00+00:00"  # after the boot run exited: `sudo fpgas-verify --update` or a re-check


def test_a_manual_pass_after_a_failed_boot_run_is_fpgas_verify_working(tmp_path):
    # The boot run found the board changed (exit 1); `sudo fpgas-verify --update` then recorded it and
    # rewrote verify.json with a pass.
    report = _verify_json("pass", checked_at=LATER, mode="auto", boards=[{**ARTY, "result": "pass"}])
    rc, output = _run_check(tmp_path, [_poll(tmp_path, NO_PASS, report)])
    assert rc == 0, output


def test_a_manual_fail_after_a_passing_boot_run_fails_the_verdict_not_fpgas_verify(tmp_path):
    report = _verify_json("fail", checked_at=LATER, mode="auto",
                          boards=[{**ARTY, "result": "fail", "reason": "uart test"}])
    rc, output = _run_check(tmp_path, [_poll(tmp_path, PASSED, report)])
    assert rc != 0, output
    assert _failed_at(output) == "Assert the FPGA boot check passed on the board it found", output


PROBE_CRASHED = "<the probe crashed>"
PROBE_SILENT = ""


@pytest.mark.parametrize(("poll", "says"), [
    pytest.param(PROBE_CRASHED, "NameError: name fpga_verify is not defined", id="probe traceback"),
    pytest.param(PROBE_SILENT, 'stdout ""', id="no output"),
    pytest.param('{"unit": {"ActiveState": "acti', '{\\"unit\\": {\\"ActiveState\\": \\"acti', id="garbled output"),
])
def test_a_probe_that_fails_says_so_at_once(tmp_path, poll, says):
    rc, output = _run_check(tmp_path, [poll, _poll(tmp_path, PASSED, BOARD_PASSED)])
    assert rc != 0, output
    assert _failed_at(output) == "Assert fpgas-verify ran and wrote this boot's report", output
    assert "the probe that reads fpgas-verify.service and verify.json failed" in output, output
    assert says in output, output
    assert "has not run this boot" not in output, output
    assert "FAILED - RETRYING" not in output, output


def test_a_run_that_never_finishes_times_out_with_its_state(tmp_path):
    rc, output = _run_check(tmp_path, [_poll(tmp_path, RUNNING, jobs=START_JOB)], retries=2)
    assert rc != 0, output
    assert _failed_at(output) == "Assert fpgas-verify ran and wrote this boot's report", output
    assert "has not finished this boot after 2 polls" in output, output
    assert "activating (start)" in output and "123 fpgas-verify.service start waiting" in output, output
    assert "journal: fpgas-verify finished" in output, output


def test_the_wait_is_bounded_by_the_units_own_timeout():
    wait = _task("Wait for this boot's FPGA check (fpgas-verify) to finish")
    assert wait["delay"] == 10
    assert wait["retries"] * wait["delay"] == 1800  # fpgas-verify.service's TimeoutStartSec


def test_the_collector_no_longer_reads_the_report():
    assert "verify.json" not in _task("Collect the Pi's state")["vars"]["verify_pi_collector"]
