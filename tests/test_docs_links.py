"""The Markdown under docs/ keeps its links and its old addresses working.

Relative links between the pages, and the #anchors on them, must resolve the way GitHub renders
them. The operator pages moved here from fpgas.online-docs on 2026-10-07 (docs.fpgas.online
imports them), and links to the old pages' sections must still land: each old section id has to
stay on the page the old link opens, as a heading or an `<a id>` line.
"""

import re
from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parent.parent / "docs"

# The section ids of the pages as they were in fpgas.online-docs (commit 88e23f1, docs/setup/),
# before they moved here. An old link to <page>#<id> opens the page here of the same name.
OLD_IDS = {
    "netboot.md": [
        "eeprom-write-protect",
        "historical-tooling",
        "how-the-root-is-built",
        "legitimately-updating-an-eeprom-later",
        "netboot-and-the-nfs-root",
        "per-model-effectiveness",
        "sources",
        "the-boot-chain",
        "the-export-is-read-only",
        "the-kernel-command-line",
        "the-nfs-root-is-shared-and-read-only",
        "the-provisioning-container",
        "updating-a-running-fleet",
        "verify",
        "when-a-pi-does-not-boot",
        "where-tftp-serves-from",
    ],
    "gateway.md": [
        "checking-and-reconnecting",
        "deploying",
        "hosts",
        "rebuilding-from-scratch",
        "sources",
        "testing-without-hardware",
        "the-gateway-host",
        "the-tags-do-not-match-the-role-names",
        "web-topology-at-welland",
        "what-runs-on-the-gateway",
    ],
    "network.md": [
        "interface-naming-on-the-pi",
        "network-and-power",
        "poe-power-control",
        "runnable-welland-poe-cycle",
        "sources",
        "switches",
        "two-addressing-schemes",
        "verifying-isolation",
        "why-per-port",
    ],
    "pi.md": [
        "boot-time-configuration",
        "camera",
        "compute-module-4-versus-compute-module-5",
        "model-differences",
        "packages",
        "raspberry-pi-3-and-3b",
        "raspberry-pi-5",
        "serial-consoles",
        "services",
        "sources",
        "what-runs-on-a-pi-host",
    ],
    "orange-pi.md": [
        "adding-a-board",
        "board-mapping",
        "deploying-and-reconverging",
        "design",
        "how-it-works",
        "known-issues",
        "no-usb-host-attached-does-not-block-or-delay-the-boot",
        "orange-pi-h3-hosts",
        "reading-a-console",
        "recovery",
        "sources",
        "the-hub-host",
        "udev-symlinks-on-the-hub-host",
        "verifying",
    ],
    "access.md": [
        "accounts-and-logins",
        "changing-who-has-access",
        "logging-in",
        "the-gateway-tweed",
        "the-pis",
        "where-the-keys-come-from-and-when-github-is-down",
        # This page's own sections before its split into docs/access/ (2026-10-07).
        "adding-or-removing-a-person",
        "at-a-glance",
        "sshd",
        "the-pi-nfs-root",
        "tweed",
        "verifying",
        "where-the-keys-come-from",
        "who-owns-authorized_keys-and-why-a-key-change-reboots-the-fleet",
    ],
    "upstream-gateway.md": [
        "checking-a-site-against-this-page",
        "clients-inside-the-site",
        "deploying-from-outside-the-site",
        "dns",
        "inbound-ipv4",
        "ipv6",
        "outbound-from-the-gateway",
        "the-uplink",
        "what-a-site-needs-from-its-upstream-network",
        "what-the-upstream-is-never-asked-for",
    ],
}

FENCE = re.compile(r"^(```|~~~).*?^\1[ \t]*$", re.M | re.S)
HEADING = re.compile(r"^#{1,6} +(.*?)[ \t#]*$", re.M)
ANCHOR = re.compile(r'<a id="([^"]+)"></a>')
LINK = re.compile(r"\]\(<?([^)\s>]+)>?(?: \"[^\"]*\")?\)")


def unfenced(text: str) -> str:
    return FENCE.sub("", text)


def slug(heading: str) -> str:
    """GitHub's heading id: lower case, punctuation other than - and _ dropped, spaces to -."""
    heading = re.sub(r"`|\*\*|\*|\[([^\]]*)\]\([^)]*\)", r"\1", heading)
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def ids(path: Path) -> set[str]:
    text = unfenced(path.read_text())
    found: set[str] = set(ANCHOR.findall(text))
    seen: dict[str, int] = {}
    for match in HEADING.finditer(text):
        base = slug(match.group(1))
        n = seen.get(base, 0)
        seen[base] = n + 1
        found.add(base if n == 0 else f"{base}-{n}")
    return found


PAGES = sorted(DOCS.rglob("*.md"))


@pytest.mark.parametrize("page", PAGES, ids=lambda p: str(p.relative_to(DOCS)))
def test_relative_links_resolve(page: Path) -> None:
    broken = []
    for target in LINK.findall(unfenced(page.read_text())):
        if re.match(r"[a-z][a-z0-9+.-]*:", target):
            continue
        path, _, fragment = target.partition("#")
        dest = (page.parent / path).resolve() if path else page
        if not dest.exists():
            broken.append(f"{target}: no such file")
        elif fragment and dest.suffix == ".md" and fragment not in ids(dest):
            broken.append(f"{target}: no such id")
    assert not broken, broken


@pytest.mark.parametrize("page", sorted(OLD_IDS))
def test_old_ids_stay_on_their_page(page: str) -> None:
    missing = sorted(set(OLD_IDS[page]) - ids(DOCS / page))
    assert not missing, f"old links to {page}#… would land at the top: {missing}"
