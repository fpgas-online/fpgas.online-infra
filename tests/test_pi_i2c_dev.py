"""The Pi root loads i2c-dev, and verify-pi checks that every I2C bus has its /dev node (issue #200).

On 2026-10-08 an Orange Pi (pi-sw2-p19) and a Raspberry Pi 5 (pi-sw2-p43)
both had their I2C buses in the kernel but no /dev/i2c-N at all: nothing in
the root loaded i2c-dev, so the Orange Pis' HAT ID EEPROM read failed with
"Could not open file `/dev/i2c-1'". These tests fail if:
  - the root stops loading i2c-dev at boot (onpi's modules-load.d file and
    the task that installs it),
  - verify-pi stops collecting the buses and their /dev nodes, or stops
    asserting one node per bus.
"""

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
ONPI = REPO / "ansible/roles/onpi"
CONF = ONPI / "files/etc/modules-load.d/fpgas-i2c-dev.conf"
VERIFY_PI = REPO / "ansible/verify-pi.yml"


def test_the_root_loads_i2c_dev_at_boot():
    modules = [line.strip() for line in CONF.read_text().splitlines()
               if line.strip() and not line.lstrip().startswith("#")]
    assert modules == ["i2c-dev"]
    (task,) = yaml.safe_load((ONPI / "tasks/i2c.yml").read_text())
    assert task["ansible.builtin.copy"] == {
        "src": "etc/modules-load.d/fpgas-i2c-dev.conf",
        "dest": "/etc/modules-load.d/fpgas-i2c-dev.conf",
        "mode": "0644",
    }
    includes = [t.get("ansible.builtin.include_tasks") for t in yaml.safe_load((ONPI / "tasks/main.yml").read_text())]
    assert "i2c.yml" in includes


def test_verify_pi_asserts_a_dev_node_per_bus():
    text = VERIFY_PI.read_text()
    assert '"i2c_buses": sorted(os.listdir("/sys/class/i2c-adapter"))' in text
    assert '"i2c_dev_nodes": sorted(n for n in os.listdir("/dev") if n.startswith("i2c-"))' in text
    pi_play = yaml.safe_load(text)[1]
    (task,) = [t for t in pi_play["tasks"] if t["name"] == "Assert every I2C bus has its /dev/i2c-N node"]
    assert task["ansible.builtin.assert"]["that"] == (
        "verify_pi_i2c_buses | difference(verify_pi_i2c_dev_nodes) | length == 0")
    assert "when" not in task
