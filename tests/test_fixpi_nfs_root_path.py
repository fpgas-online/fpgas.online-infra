"""fixpi writes the NFS root only through `nfs_root`, never a literal path.

fixpi applies the site layer to whichever tree the run works on: today the
served root, and with the NFS root versions layout
(docs/superpowers/specs/2026-10-02-nfsroot-versions-design.md) a fresh
`work/<id>/` directory that `nfs_root` names. A task that spells out
`/srv/nfs/...` instead writes the wrong tree: userconf.yml's chown of pi's
.ssh did, and on a versioned site would have left pi's keys owned by root.

This fails if any fixpi task, template or file contains a literal
`/srv/nfs`. (The spec's later static test of everything deploy.yml includes
covers more roles and variables; this one only guards fixpi.)
"""

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FIXPI = REPO / "ansible" / "roles" / "fixpi"


def test_fixpi_has_no_literal_nfs_root_path():
    files = [p for p in FIXPI.rglob("*") if p.is_file() and p.name != "README.md"]
    assert files, f"no files under {FIXPI}"
    found = [
        f"{p.relative_to(REPO)}:{n}"
        for p in files
        for n, line in enumerate(p.read_text(errors="replace").splitlines(), 1)
        if "/srv/nfs" in line
    ]
    assert not found, f"literal /srv/nfs instead of {{{{ nfs_root }}}}: {found}"
