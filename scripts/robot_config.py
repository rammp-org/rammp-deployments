#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
"""Set this host up as a robot: run each config script the robot has a file for.

    uv run scripts/robot_config.py [--validate | --dry-run] <robot dir>

In this order, each only when its file is in the robot dir:

    network.yaml  network_config.py   NICs, through NetworkManager (asks for sudo)
    arm.yaml      arm_config.py       the arm's own settings (needs the arm on)
    oak_d.yaml    oak_d_config.py     the OAK cameras' network config
    mount.yaml    set_robot.py        makes this robot the host's active robot
    desktop.yaml  desktop_config.py   terminal env and screen rotation

--validate and --dry-run are passed to each. A step that fails is reported and
the rest still run: an arm that is off doesn't stop the cameras being set.
Each script stays runnable on its own.

Host tuning (config/hosts/<host>/) is per host and runs as root: run it on its
own, as the robot's README says.

Exit status: the worst of the steps (2 invalid config, 1 a change failed, 0 ok).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
STEPS = [
    ("network.yaml", "network_config.py"),
    ("arm.yaml", "arm_config.py"),
    ("oak_d.yaml", "oak_d_config.py"),
    ("mount.yaml", "set_robot.py"),
    ("desktop.yaml", "desktop_config.py"),
]


def steps(robot_dir: Path) -> list[str]:
    """The scripts to run for this robot, in order."""
    return [script for config, script in STEPS if (robot_dir / config).is_file()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--validate", action="store_true", help="check the files and exit"
    )
    mode.add_argument("--dry-run", action="store_true", help="print changes, make none")
    parser.add_argument("robot_dir", type=Path)
    args = parser.parse_args(argv)

    if not args.robot_dir.is_dir():
        print(f"error: {args.robot_dir}: not a directory", file=sys.stderr)
        return 2
    todo = steps(args.robot_dir)
    if not todo:
        print(f"error: {args.robot_dir}: no config files", file=sys.stderr)
        return 2
    flags = ["--validate"] if args.validate else ["--dry-run"] if args.dry_run else []

    results = []
    for script in todo:
        print(f"== {script}", flush=True)
        code = subprocess.run(
            ["uv", "run", str(SCRIPTS / script), *flags, str(args.robot_dir)]
        ).returncode
        results.append((script, code))

    print("== summary")
    for script, code in results:
        print(f"{script}: {'ok' if code == 0 else f'FAILED (exit {code})'}")
    return max(min(code, 2) for _, code in results)


if __name__ == "__main__":
    sys.exit(main())
