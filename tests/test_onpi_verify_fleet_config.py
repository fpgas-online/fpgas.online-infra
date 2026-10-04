"""The Pi root tells fpgas-verify it runs on the fleet: /etc/fpgas-verify/fleet.ini with publish = on.

fpgas-verify (fpgas.online-test-designs, verify/src/fpgas_online_verify/config.py) reads every *.ini in
/etc/fpgas-verify with configparser and takes keys from the [verify] section. A release that publishes
only when configured needs this file in the root first, so the file and the task that installs it are pinned.
"""

import configparser
from pathlib import Path

import yaml

REPO = Path(__file__).parents[1]
ROLE = REPO / "ansible/roles/onpi"
INI = ROLE / "files/etc/fpgas-verify/fleet.ini"


def test_the_file_parses_as_fpgas_verify_reads_it_and_turns_publishing_on():
    parser = configparser.ConfigParser()
    parser.read(INI)
    assert parser.get("verify", "publish", fallback=None) == "on"


def test_the_file_sets_nothing_else():
    # A key here changes every board's boot check; each one is added on purpose, with its own reason.
    parser = configparser.ConfigParser()
    parser.read(INI)
    assert parser.sections() == ["verify"]
    assert dict(parser["verify"]) == {"publish": "on"}


def test_the_role_installs_it_where_fpgas_verify_looks_before_enabling_the_check():
    tasks = yaml.safe_load((ROLE / "tasks/fpga_verify.yml").read_text())
    names = [t["name"] for t in tasks]
    copy = next(t for t in tasks if t.get("ansible.builtin.copy", {}).get("dest") == "/etc/fpgas-verify/fleet.ini")
    assert copy["ansible.builtin.copy"]["src"] == "etc/fpgas-verify/fleet.ini"
    assert copy["ansible.builtin.copy"]["mode"] == "0644"
    assert names.index(copy["name"]) < names.index("Run the FPGA boot check on every boot")
