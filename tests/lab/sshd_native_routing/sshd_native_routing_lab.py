#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Lab: board login names routed inside the gateway's own sshd.

`ssh pi-sw2-p47@<site>` on the gateway's normal sshd (port 22) lands on that
board; `ssh <administrator>@<site>` stays an ordinary key login. See
README.md for the design and results-*.md for what was measured.

    uv run tests/lab/sshd_native_routing/sshd_native_routing_lab.py            # everything
    uv run tests/lab/sshd_native_routing/sshd_native_routing_lab.py up         # leave the lab running
    uv run tests/lab/sshd_native_routing/sshd_native_routing_lab.py down       # remove it
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lab  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", nargs="?", default="run", choices=["run", "up", "down"])
    parser.add_argument("--lookup", default="extrausers")
    parser.add_argument("--variant", default="password")
    parser.add_argument("--sandbox", default="chroot")
    parser.add_argument("--keep", action="store_true", help="leave containers and images in place afterwards")
    args = parser.parse_args()

    if args.action == "down":
        lab.teardown()
        return 0
    lab.teardown(remove_images=False)
    lab.make_keys()
    lab.build_images()
    lab.start_lab()
    if args.action == "up":
        lab.start_gateway(lookup=args.lookup, variant=args.variant, sandbox=args.sandbox)
        print(f"lab is up: gateway {lab.GW}, clients {lab.client('a')} {lab.client('b')}")
        return 0
    raise SystemExit("run: not written yet")


if __name__ == "__main__":
    sys.exit(main())
