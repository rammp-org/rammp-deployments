"""Every robot's arm_presets.yaml, which the arm driver loads at start.

A missing file, or an angle written as an integer, stops the driver before it
runs: ROS refuses a list that mixes integers and reals, and the driver refuses
an all-integer one.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROBOTS = Path(__file__).resolve().parent.parent / "december_2026" / "config" / "robots"
ROBOT_DIRS = sorted(p for p in ROBOTS.iterdir() if p.is_dir() and not p.is_symlink())


@pytest.mark.parametrize("robot", ROBOT_DIRS, ids=lambda p: p.name)
def test_home_is_seven_reals(robot):
    data = yaml.safe_load((robot / "arm_presets.yaml").read_text())
    home = data["/**"]["ros__parameters"]["presets.home"]
    assert len(home) == 7
    assert all(isinstance(v, float) for v in home), home  # bool is an int, not a float
