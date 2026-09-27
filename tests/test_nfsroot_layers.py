"""nfsroot_publish.plan_layers: splitting the Pi root into layers podman pulls in parallel.

The layers are applied in order on top of each other (podman's overlay
storage on the servers, or tar -x in nfsroot_warm.extract), so the split
must lose nothing and change nothing: every entry in exactly one layer
(directories may repeat), each layer carrying the real directories above
its entries, hard links kept together.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests" / "ci"))
import nfsroot_publish  # noqa: E402
from nfsroot_publish import ancestors, plan_layers  # noqa: E402


def tree() -> list:
    """A find-ordered tree: dirs before contents, a hard link pair far apart, an empty dir."""
    e = [(b"boot", "d", 0, "1:1", 2), (b"boot/kernel8.img", "f", 900, "1:2", 1),
         (b"root", "d", 0, "1:3", 2), (b"root/tmp", "d", 0, "1:4", 2),
         (b"root/tmp/empty", "d", 0, "1:5", 2), (b"root/usr", "d", 0, "1:6", 2),
         (b"root/usr/bin", "d", 0, "1:7", 2), (b"root/usr/bin/a", "f", 500, "1:8", 2)]
    e += [(b"root/usr/lib/f%d" % i, "f", 100, "1:%d" % (100 + i), 1) for i in range(40)]
    e.insert(8, (b"root/usr/lib", "d", 0, "1:9", 2))
    e += [(b"root/usr/sbin", "d", 0, "1:10", 2), (b"root/usr/sbin/a-link", "f", 500, "1:8", 2),
          (b"root/usr/sbin/sym", "l", 1, "1:11", 1)]
    return e


def test_every_entry_once_and_nothing_else():
    entries = tree()
    layers = plan_layers(entries, 8)
    members = [m for layer in layers for m in layer]
    non_dirs = [p for p, kind, *_ in entries if kind != "d"]
    assert sorted(p for p in members if p in non_dirs) == sorted(non_dirs)
    assert len([p for p in members if p in non_dirs]) == len(non_dirs)
    assert set(members) == {p for p, *_ in entries}


def test_each_layer_carries_its_ancestors_first():
    for layer in plan_layers(tree(), 8):
        seen = set()
        for member in layer:
            assert all(a in seen for a in ancestors(member)), member
            seen.add(member)


def test_hard_links_share_a_layer():
    for layer in plan_layers(tree(), 8):
        assert (b"root/usr/bin/a" in layer) == (b"root/usr/sbin/a-link" in layer)


def test_empty_directories_are_kept():
    assert any(b"root/tmp/empty" in layer for layer in plan_layers(tree(), 8))


def test_layers_are_balanced():
    sizes = {p: size for p, kind, size, *_ in tree() if kind == "f"}
    layers = plan_layers(tree(), 4)
    assert len(layers) == 4
    loads = [sum(sizes.get(m, 0) for m in layer) for layer in layers]
    # 6400 bytes of files in 4 layers; the 900-byte kernel and the linked
    # pair make exact halves impossible, but nothing should be starved.
    assert min(loads) > 0.5 * sum(loads) / 4, loads


def test_one_layer_is_the_whole_tree():
    (layer,) = plan_layers(tree(), 1)
    assert set(layer) == {p for p, *_ in tree()}


def test_the_image_is_split():
    assert nfsroot_publish.LAYERS > 1
