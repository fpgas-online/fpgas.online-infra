"""roles/site/templates/poe.env.j2: the switches' communities as systemd reads an EnvironmentFile."""

from pathlib import Path

import jinja2

TEMPLATES = Path(__file__).resolve().parents[1] / "ansible/roles/site/templates"


def render(switches):
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(TEMPLATES), undefined=jinja2.StrictUndefined)
    return env.get_template("poe.env.j2").render(ansible_managed="test", switches=switches)


def test_each_community_is_double_quoted_with_backslash_and_quote_escaped():
    text = render([{"index": 1, "snmp_rw_community": 'a"b\\c d'}, {"index": 2, "snmp_rw_community": "plain"}])
    lines = [line for line in text.splitlines() if line.startswith("FPGAS_")]
    assert lines == ["FPGAS_SWITCHES_CONFIG=/etc/fpgas/switches.yml",
                     'FPGAS_SWITCH_COMMUNITY_1="a\\"b\\\\c d"',
                     'FPGAS_SWITCH_COMMUNITY_2="plain"']


def test_a_switch_without_a_community_gets_no_line():
    assert "FPGAS_SWITCH_COMMUNITY_3" not in render([{"index": 3}])
