#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pyyaml==6.0.2"]
# ///
"""Publish a robot's fixed and calibrated transforms on /tf_static.

    python3 scripts/static_tf.py <robots dir>/<robot>/transforms
    uv run scripts/static_tf.py --validate <robots dir>/<robot>/transforms

Every *.yaml in the directory is one file in rammp-calibration's mounts.yaml
format, so a solved calibration lands here unchanged; hand-measured transforms
use the same format in a file of their own:

    mounts:
      ros__parameters:
        transforms: [scene_camera]
        scene_camera:
          parent: base_link
          child: scene_camera_link
          xyz: [0.1, 0.0, 0.5]           # metres
          quat_xyzw: [0.0, 0.0, 0.0, 1.0]

All of them go out in one latched message. A child named twice, in one file or
across files, is refused: TF would keep whichever arrived last.

Runs in the manifest's static_tf node, in rammp-base, which has rclpy and tf2;
--validate needs only PyYAML, so CI checks the files without ROS.

Exit status: 0 ok, 2 a file is invalid and nothing was published.
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml


class ConfigError(Exception):
    """A transforms file is not usable. Nothing was published."""


@dataclass(frozen=True)
class Transform:
    name: str
    parent: str
    child: str
    xyz: tuple[float, float, float]
    quat_xyzw: tuple[float, float, float, float]


def _keys(where: str, value: object, required: list[str]) -> dict:
    if not isinstance(value, dict):
        raise ConfigError(f"{where}: expected a mapping")
    missing = [k for k in required if k not in value]
    if missing:
        raise ConfigError(f"{where}: missing {', '.join(missing)}")
    return value


def _string(where: str, value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{where}: expected a non-empty string, got {value!r}")
    return value


def _numbers(where: str, value: object, n: int) -> tuple[float, ...]:
    # bool is an int to Python; `[0, true, 0]` is a typo, not a vector.
    if (
        not isinstance(value, list)
        or len(value) != n
        or not all(
            isinstance(v, (int, float)) and not isinstance(v, bool) for v in value
        )
        or not all(math.isfinite(v) for v in value)
    ):
        raise ConfigError(f"{where}: expected {n} finite numbers, got {value!r}")
    return tuple(float(v) for v in value)


def parse(data: object, where: str) -> list[Transform]:
    top = _keys(where, data, ["mounts"])
    params = _keys(f"{where}.mounts", top["mounts"], ["ros__parameters"])
    params = _keys(
        f"{where}.mounts.ros__parameters", params["ros__parameters"], ["transforms"]
    )
    names = params["transforms"]
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise ConfigError(f"{where}: transforms: expected a list of names")
    unlisted = [str(k) for k in params if k != "transforms" and k not in names]
    if unlisted:
        # An entry missing from the list would be silently skipped.
        raise ConfigError(f"{where}: not in transforms: {', '.join(unlisted)}")
    out = []
    for name in names:
        at = f"{where}.{name}"
        entry = _keys(at, params.get(name), ["parent", "child", "xyz", "quat_xyzw"])
        unknown = [
            str(k) for k in entry if k not in ("parent", "child", "xyz", "quat_xyzw")
        ]
        if unknown:
            raise ConfigError(f"{at}: unknown key {', '.join(unknown)}")
        parent = _string(f"{at}.parent", entry["parent"])
        child = _string(f"{at}.child", entry["child"])
        if parent == child:
            raise ConfigError(f"{at}: parent and child are both {parent!r}")
        q = _numbers(f"{at}.quat_xyzw", entry["quat_xyzw"], 4)
        if abs(math.sqrt(sum(v * v for v in q)) - 1.0) > 1e-3:
            raise ConfigError(f"{at}.quat_xyzw: not a unit quaternion: {list(q)}")
        out.append(
            Transform(name, parent, child, _numbers(f"{at}.xyz", entry["xyz"], 3), q)
        )
    return out


def load(directory: Path) -> list[Transform]:
    if not directory.is_dir():
        raise ConfigError(f"{directory}: not a directory")
    paths = sorted(directory.glob("*.yaml"))
    if not paths:
        raise ConfigError(f"{directory}: no *.yaml files")
    transforms: list[Transform] = []
    seen: dict[str, str] = {}
    for path in paths:
        try:
            data = yaml.safe_load(path.read_text())
        except yaml.YAMLError as e:
            raise ConfigError(f"{path}: {e}") from e
        for t in parse(data, str(path)):
            where = f"{path}.{t.name}"
            if t.child in seen:
                raise ConfigError(
                    f"{where}: child {t.child!r} is already published by {seen[t.child]}"
                )
            seen[t.child] = where
            transforms.append(t)
    return transforms


def describe(t: Transform) -> str:
    return f"{t.name}: {t.parent} -> {t.child} xyz {list(t.xyz)} quat_xyzw {list(t.quat_xyzw)}"


def publish(transforms: list[Transform]) -> None:
    # Imported here so --validate runs without ROS.
    import rclpy
    from geometry_msgs.msg import TransformStamped
    from tf2_ros.static_transform_broadcaster import StaticTransformBroadcaster

    rclpy.init()
    node = rclpy.create_node("static_tf")
    stamp = node.get_clock().now().to_msg()
    msgs = []
    for t in transforms:
        msg = TransformStamped()
        msg.header.stamp = stamp
        msg.header.frame_id = t.parent
        msg.child_frame_id = t.child
        (
            msg.transform.translation.x,
            msg.transform.translation.y,
            msg.transform.translation.z,
        ) = t.xyz
        (
            msg.transform.rotation.x,
            msg.transform.rotation.y,
            msg.transform.rotation.z,
            msg.transform.rotation.w,
        ) = t.quat_xyzw
        msgs.append(msg)
    # One sendTransform call: the broadcaster's topic is latched, so late
    # subscribers get the last message, and it must hold every transform.
    broadcaster = StaticTransformBroadcaster(node)
    broadcaster.sendTransform(msgs)
    for t in transforms:
        node.get_logger().info(describe(t))
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--validate", action="store_true", help="check and exit")
    parser.add_argument("directory", type=Path, help="a robot's transforms directory")
    args = parser.parse_args(argv)
    try:
        transforms = load(args.directory)
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if args.validate:
        for t in transforms:
            print(describe(t))
        return 0
    publish(transforms)
    return 0


if __name__ == "__main__":
    sys.exit(main())
