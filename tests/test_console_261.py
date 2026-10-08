"""Consoles off the header pins, on the dedicated UARTs (#261, Tim 2026-10-08).

Tim's rule: the Orange Pi and the Pi 5 keep the kernel console on their
dedicated console UART; the 40-pin header's UART pins work as a UART and as
GPIO, with no console login, kernel or boot messages on them. These tests fail
if:
  - config.txt gets enable_uart=1 or uart_2ndstage=1 back (firmware logging to
    GPIO 14/15 on a Pi 3B+/4; the Pi 5's kernel debug fallback onto them),
  - the Orange Pi's header UART (UART3, pins 8/10) stops being enabled in the
    published DTB, or the console moves off ttyS0 (UART0, the debug header),
  - verify-pi stops finding the header UART per SoC by its device-tree node
    (serial0 on a Raspberry Pi, UART3 on an Allwinner H3), or stops asserting it
    is free of the console and present on an Orange Pi.
"""

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
FIXPI = REPO / "ansible/roles/fixpi"
VERIFY_PI = REPO / "ansible/verify-pi.yml"


def _tasks(path: Path) -> list[dict]:
    return yaml.safe_load(path.read_text())


def test_config_txt_carries_no_firmware_uart_logging():
    tasks = _tasks(FIXPI / "tasks/tweeks.yml")
    added = [i for t in tasks if (t.get("ansible.builtin.lineinfile") or {}).get("state", "present") == "present"
             for i in (t.get("with_items") or [])]
    assert not [i for i in added if str(i).startswith(("enable_uart", "uart_2ndstage"))], added
    (drop,) = [t for t in tasks if t["name"] == "Config.txt drop the firmware UART logging lines"]
    assert drop["ansible.builtin.lineinfile"]["state"] == "absent"
    assert drop["ansible.builtin.lineinfile"]["regexp"] == "^{{ item }}="
    assert set(drop["with_items"]) == {"enable_uart", "uart_2ndstage"}


def test_no_docs_page_says_the_lines_are_added():
    for page in (REPO / "docs").rglob("*.md"):
        if "superpowers" in page.parts:
            continue
        for line in page.read_text().splitlines():
            if line.strip() in ("enable_uart=1", "uart_2ndstage=1"):
                raise AssertionError(f"{page}: lists {line.strip()} as a config.txt line")


def test_the_orange_pi_header_uart_is_enabled_and_the_console_stays_on_the_debug_header():
    defaults = yaml.safe_load((FIXPI / "defaults/main.yml").read_text())
    assert defaults["fixpi_sunxi_uart_nodes"] == ["/soc/serial@1c28c00"]
    assert "fixpi_sunxi_i2c_nodes + fixpi_sunxi_uart_nodes" in (FIXPI / "tasks/sunxi.yml").read_text()
    append = (FIXPI / "templates/boot/default-arm-sunxi.j2").read_text()
    assert "console=ttyS0,115200" in append


def test_verify_pi_finds_the_header_uart_per_soc_and_checks_it():
    text = VERIFY_PI.read_text()
    assert 'if b"allwinner,sun8i-h3" in compatible:' in text
    assert 'return "/soc/serial@1c28c00"' in text
    assert '"header_uart": header_uart_tty()' in text
    pi_play = yaml.safe_load(text)[1]
    tasks = {t["name"]: t for t in pi_play["tasks"]}
    console = tasks["Assert no kernel console is on the header UART"]
    assert "verify_pi_consoles.header_uart" in console["vars"]["header_uarts"]
    present = tasks["Assert the header UART is enabled on an Orange Pi"]
    assert present["when"] == "verify_pi_consoles.header_node == '/soc/serial@1c28c00'"
    assert "serial0" not in console["vars"]["header_uarts"]
