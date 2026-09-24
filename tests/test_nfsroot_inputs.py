"""tests/ci/nfsroot_inputs.py must cover everything the image build reads.

A role or cross-role file missing from INPUTS would let a PR that changes
it reuse an image built without the change -- CI would then boot a root
that is not the PR's. These checks walk ci-nfsroot.yml's roles (and the
roles they include) and every `role_path }}/../<role>/...` reference.
"""
import re
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests" / "ci"))
import nfsroot_inputs  # noqa: E402

ROLES = REPO / "ansible" / "roles"
ROLE_REF = re.compile(r"""(?:include_role|import_role):\s*\n\s+name:\s*([\w/-]+)""")
ROLE_PATH_REF = re.compile(r"role_path \}\}/\.\./([\w/.-]+)")


def playbook_roles(path: Path) -> set[str]:
    roles = set()
    for play in yaml.safe_load(path.read_text()):
        for r in play.get("roles", []):
            roles.add(r["role"] if isinstance(r, dict) else r)
    roles.update(ROLE_REF.findall(path.read_text()))
    return roles


def role_closure(roles: set[str]) -> tuple[set[str], set[str]]:
    """Roles reachable through include_role, and cross-role file references."""
    seen, files, todo = set(), set(), list(roles)
    while todo:
        role = todo.pop()
        if role in seen:
            continue
        seen.add(role)
        for f in (ROLES / role).rglob("*.yml"):
            text = f.read_text()
            todo.extend(ROLE_REF.findall(text))
            for ref in ROLE_PATH_REF.findall(text):
                # role_path/../<ref> relative to roles/<role>
                target = (ROLES / role / ".." / ref).resolve().relative_to(REPO)
                files.add(str(target))
    return seen, files


def covered(rel: str) -> bool:
    return any(rel == p or rel.startswith(p.rstrip("/") + "/") for p in nfsroot_inputs.INPUTS)


def test_every_build_role_is_an_input():
    roles, _ = role_closure(playbook_roles(REPO / "ansible" / "ci-nfsroot.yml"))
    missing = sorted(r for r in roles if not covered(f"ansible/roles/{r}"))
    assert not missing, f"roles the image build runs but nfsroot_inputs.INPUTS omits: {missing}"


def test_every_cross_role_file_is_an_input():
    _, files = role_closure(playbook_roles(REPO / "ansible" / "ci-nfsroot.yml"))
    missing = sorted(f for f in files if not covered(f))
    assert not missing, f"files the build roles read from other roles, not in INPUTS: {missing}"


def test_inputs_exist():
    missing = [p for p in nfsroot_inputs.INPUTS if not (REPO / p).exists()]
    assert not missing, f"INPUTS names paths that do not exist: {missing}"


def test_key_is_stable_and_changes_with_content(tmp_path, monkeypatch):
    a = nfsroot_inputs.key(week="2026-W39")
    assert a == nfsroot_inputs.key(week="2026-W39")
    assert a != nfsroot_inputs.key(week="2026-W40")


def test_base_inputs_exist_and_are_image_inputs():
    for rel in nfsroot_inputs.BASE_INPUTS:
        assert (REPO / rel).exists(), rel
        assert covered(rel), f"{rel} picks the base image but is not an image input"
    assert len(nfsroot_inputs.base_key()) == 20


def test_base_vars_name_the_downloaded_image():
    """The rendered values are the file img/tasks/build.yml downloads."""
    got = nfsroot_inputs.base_vars()
    groups = [yaml.safe_load((REPO / rel).read_text()) or {}
              for rel in nfsroot_inputs.BASE_VAR_FILES]
    srv = {k: v for g in groups for k, v in g.items()}
    date = str(srv["dir_date"])
    assert got["dist"] == srv["dist"]
    assert got["img_path"] == srv["img_path"].replace("{{dir_date}}", date)
    assert got["img_name"] == f"{date}-raspios-{srv['dist']}-armhf-lite.img"
    assert got["zip_name"] == got["img_name"] + ".xz"
    assert not any("{{" in v for v in got.values()), got


def test_base_vars_cover_build_yml():
    """Every variable the download/extract tasks use is a base var (or a
    mirror/path the image does not depend on)."""
    used = set(re.findall(r"\{\{\s*(\w+)\s*\}\}",
                          (REPO / "ansible/roles/img/tasks/build.yml").read_text()))
    not_identity = {"img_host", "cache_dir", "nfs_root"}
    missing = used - set(nfsroot_inputs.BASE_VARS) - not_identity
    assert not missing, f"build.yml uses {missing}, which base_key() ignores"


def test_base_key_ignores_unrelated_srv_vars(monkeypatch):
    before = nfsroot_inputs.base_key()
    real = nfsroot_inputs.Path.read_text

    def edited(self, *a, **kw):
        text = real(self, *a, **kw)
        if self.name == "srv.yml":
            text += "\ntftp_root: /somewhere/else\nimg_host: https://mirror.invalid\n"
        return text

    monkeypatch.setattr(nfsroot_inputs.Path, "read_text", edited)
    assert nfsroot_inputs.base_key() == before


def test_base_key_changes_with_the_image(monkeypatch):
    before = nfsroot_inputs.base_key()
    real = nfsroot_inputs.Path.read_text

    def bumped(self, *a, **kw):
        text = real(self, *a, **kw)
        if self.name == "srv.yml":
            text += "\ndir_date: 2099-01-01\n"
        return text

    monkeypatch.setattr(nfsroot_inputs.Path, "read_text", bumped)
    assert nfsroot_inputs.base_key() != before
