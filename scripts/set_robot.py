#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pyyaml==6.0.2"]
# ///
"""Make a robot this host's active robot, or show which one is.

    uv run scripts/set_robot.py [--validate | --dry-run] <robots dir>/<robot>
    uv run scripts/set_robot.py <robots dir>

The active robot is the symlink <robots dir>/current -> <robot>. It is
git-ignored: the repo describes every robot, and which one a host is belongs to
the host. Nodes read embodiment facts (the arm mount) through it, bind-mounted
read-only, so a container started before this has run sees an empty directory.

Given a robot directory, the script validates its mount.yaml, then points
current at it with an atomic rename and prints the change. Given the robots
directory itself, it prints the active robot.

A current that is a real directory is refused, never removed: Docker creates
one, as root, when a container starts with the link missing.

Exit status: 0 ok, 1 no active robot (when asked which one is), 2 the config
or a preflight check is invalid and nothing was changed.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

CONFIG_NAME = "mount.yaml"
ROBOTS_DIR_NAME = "robots"
LINK_NAME = "current"


class ConfigError(Exception):
    """The file, or the link it would replace, is not usable. Nothing changed."""


@dataclass(frozen=True)
class Mount:
    robot: str
    rpy_deg: tuple[float, float, float]


def _keys(
    where: str, value: object, required: list[str], optional: list[str] = ()
) -> dict:
    if not isinstance(value, dict):
        raise ConfigError(f"{where}: expected a mapping")
    missing = [k for k in required if k not in value]
    if missing:
        raise ConfigError(f"{where}: missing {', '.join(missing)}")
    unknown = [str(k) for k in value if k not in required and k not in optional]
    if unknown:
        raise ConfigError(f"{where}: unknown key {', '.join(unknown)}")
    return value


def _string(where: str, value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{where}: expected a non-empty string, got {value!r}")
    return value


_RPY_MSG = "{where}.rpy_deg: expected three finite numbers in degrees, got {rpy!r}"


def parse(data: object, where: str, dir_name: str) -> Mount:
    mount = _keys(where, data, ["robot", "rpy_deg"])
    robot = _string(f"{where}.robot", mount["robot"])
    if robot != dir_name:
        raise ConfigError(
            f"{where}.robot: {robot!r} does not match the directory name {dir_name!r}"
        )
    rpy = mount["rpy_deg"]
    # bool is an int to Python; `[0, true, 0]` is a typo, not a rotation.
    if (
        not isinstance(rpy, list)
        or len(rpy) != 3
        or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in rpy)
    ):
        raise ConfigError(_RPY_MSG.format(where=where, rpy=rpy))
    try:
        values = tuple(float(v) for v in rpy)
    except OverflowError:  # an int too large for a float
        raise ConfigError(_RPY_MSG.format(where=where, rpy=rpy)) from None
    if not all(math.isfinite(v) for v in values):
        raise ConfigError(_RPY_MSG.format(where=where, rpy=rpy))
    return Mount(robot, values)


def load(robot_dir: Path) -> Mount:
    path = robot_dir / CONFIG_NAME
    if not path.is_file():
        raise ConfigError(f"{path}: not found")
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise ConfigError(f"{path}: {e}") from e
    # Resolved, so `<robots dir>/current` validates as the robot it points at.
    return parse(data, str(path), robot_dir.resolve().name)


def describe(mount: Mount) -> str:
    return f"{mount.robot} (mount rpy_deg {list(mount.rpy_deg)})"


def active(robots_dir: Path) -> str | None:
    """The robot current points at, or None if there is no link."""
    link = robots_dir / LINK_NAME
    if link.is_symlink():
        return os.readlink(link)
    if link.exists():
        raise ConfigError(
            f"{link} is a {'directory' if link.is_dir() else 'file'}, not a link. "
            "Docker creates an empty directory there when a container starts before "
            f"set_robot has run. Remove it with `sudo rmdir {link}` (rmdir refuses "
            "if anything is inside), then run set_robot again."
        )
    return None


def show(robots_dir: Path) -> int:
    try:
        robot = active(robots_dir)
        if robot is None:
            print(
                f"{robots_dir / LINK_NAME}: no active robot; set one with "
                f"`uv run scripts/set_robot.py {robots_dir / '<robot>'}`",
                file=sys.stderr,
            )
            return 1
        if not (robots_dir / LINK_NAME).exists():
            raise ConfigError(
                f"{robots_dir / LINK_NAME} -> {robot}, which does not exist"
            )
        mount = load(robots_dir / LINK_NAME)
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"{robots_dir / LINK_NAME} -> {describe(mount)}")
    return 0


def set_active(robot_dir: Path, mount: Mount, dry_run: bool) -> int:
    # Relative to the cwd, so messages name the path the way the caller typed it.
    robots_dir = Path(os.path.relpath(robot_dir.resolve().parent))
    if robots_dir.resolve().name != ROBOTS_DIR_NAME:
        print(
            f"error: {robot_dir} is not in a {ROBOTS_DIR_NAME}/ directory",
            file=sys.stderr,
        )
        return 2
    link = robots_dir / LINK_NAME
    try:
        old = active(robots_dir)
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    new = mount.robot  # a relative target, so the link survives moving the checkout
    if old == new:
        print(f"{LINK_NAME}: unchanged, {describe(mount)}")
        return 0
    change = f"{old or '(none)'} → {new}"
    if dry_run:
        print(f"{LINK_NAME}: would change {change}")
        return 0
    # Build the new link beside the old one and rename over it: rename is atomic,
    # so a reader never finds current missing or half-made.
    tmp = robots_dir / f".{LINK_NAME}.{os.getpid()}"
    os.symlink(new, tmp)
    try:
        os.replace(tmp, link)
    except OSError:
        tmp.unlink()
        raise
    print(f"{LINK_NAME}: {change}, mount rpy_deg {list(mount.rpy_deg)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--validate", action="store_true", help="check the file and exit")
    mode.add_argument("--dry-run", action="store_true", help="print changes, make none")
    parser.add_argument("path", type=Path, help="a robot directory, or the robots one")
    args = parser.parse_args(argv)

    if args.path.resolve().name == ROBOTS_DIR_NAME:
        if args.validate or args.dry_run:
            print(
                "error: --validate and --dry-run take a robot directory, "
                f"e.g. {args.path / '<robot>'}",
                file=sys.stderr,
            )
            return 2
        return show(args.path)

    try:
        mount = load(args.path)
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if args.validate:
        print(f"{args.path / CONFIG_NAME}: ok — {describe(mount)}")
        return 0
    return set_active(args.path, mount, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
