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
        "What the FPGA boot check found",
        "Whether an FPGA board is attached",
        "Say that this Pi has no FPGA board, so there is none to check",
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
    "Assert the FPGA boot check passed on the board it found",
]


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
    rc, output = _run(tmp_path, [_task(n) for n in FPGA_TASKS], {"verify_pi_fpga_verify_report": report})
    assert (rc == 0) == passes, output
    assert ("No FPGA board on pi-sw1-p1" in output) == says_no_board, output
