from __future__ import annotations

from pathlib import Path

import yaml

MANIFEST = (
    Path(__file__).resolve().parent.parent / "december_2026" / "sheppy-manifest.yaml"
)


def _wrist_alternatives() -> dict:
    data = yaml.safe_load(MANIFEST.read_text())
    (node,) = [n for n in data["nodes"] if n["name"] == "wrist_camera"]
    return {a["id"]: a for a in node["alternatives"]}


def test_puck_alternative_is_the_oak_plus_the_name_list():
    alts = _wrist_alternatives()
    base, puck = alts["oak_d_pro_docker"], alts["oak_d_pro_puck_docker"]
    assert puck["params"] == base["params"]
    want = dict(base["container"])
    want["environment"] = {
        **base["container"]["environment"],
        "DEPTHAI_DEVICE_NAME_LIST": "10.20.0.50",
    }
    assert puck["container"] == want


def test_puck_environment_follows_the_oak_environment():
    # The Puck variant's env must be the anchored one, not a copy: editing the
    # anchor in the file text must reach both alternatives.
    text = MANIFEST.read_text()
    assert "environment: &oak_wrist_env" in text
    assert "<<: *oak_wrist_env" in text
