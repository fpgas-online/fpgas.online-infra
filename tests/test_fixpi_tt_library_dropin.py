"""The fpgas-tt VGA library drop-in reaches the root a gateway serves.

onpi/tasks/tt.yml only runs where onpi_tt_boards is defined, which the public
CI image build never has; a gateway's site.yml runs fixpi/tasks/tt-site.yml
instead. Both must write the same drop-in from the one onpi template and the
one onpi_tt_library_max_bytes default.
"""

from pathlib import Path

import jinja2
import yaml

ANSIBLE = Path(__file__).resolve().parent.parent / "ansible"
TT_SITE = ANSIBLE / "roles/fixpi/tasks/tt-site.yml"
TEMPLATE = ANSIBLE / "roles/onpi/templates/fpgas-tt-library.conf.j2"
DEFAULTS = ANSIBLE / "roles/onpi/defaults/main.yml"
DROPIN = "etc/systemd/system/fpgas-tt.service.d/library.conf"


def _tasks():
    return yaml.safe_load(TT_SITE.read_text())


def test_tt_site_writes_the_drop_in_from_the_onpi_template():
    templates = [t["ansible.builtin.template"] for t in _tasks() if "ansible.builtin.template" in t]
    assert len(templates) == 1
    assert templates[0]["src"].endswith("/../onpi/templates/fpgas-tt-library.conf.j2")
    assert templates[0]["dest"].endswith(DROPIN)


def test_tt_site_creates_the_drop_in_directory_and_reads_the_onpi_default():
    text = TT_SITE.read_text()
    assert "fpgas-tt.service.d" in text
    assert "../onpi/defaults/main.yml" in text
    assert "onpi_tt_library_max_bytes" in text


def test_the_variable_has_one_definition():
    assert "onpi_tt_library_max_bytes" in yaml.safe_load(DEFAULTS.read_text())
    for path in (ANSIBLE / "roles/fixpi").rglob("*.yml"):
        for line in path.read_text().splitlines():
            assert not line.startswith("onpi_tt_library_max_bytes:"), path


def test_the_template_renders_the_environment_line():
    cap = yaml.safe_load(DEFAULTS.read_text())["onpi_tt_library_max_bytes"]
    out = jinja2.Environment().from_string(TEMPLATE.read_text()).render(
        onpi_tt_library_max_bytes=cap, ansible_managed="x"
    )
    assert f"Environment=FPGAS_TT_LIBRARY_MAX_BYTES={cap}" in out


def test_an_inventory_override_wins_over_the_onpi_default():
    text = TT_SITE.read_text()
    assert "onpi_tt_library_max_bytes | default(fixpi_tt_onpi_defaults.onpi_tt_library_max_bytes)" in text
