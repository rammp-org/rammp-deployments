"""The daily demo: every kitchen engine from engines `dev`, and the profile that brings them up.

    uv run --with pytest==8.3.4 --with pyyaml==6.0.2 pytest tests/test_daily.py -q

sheppy's loader (scripts/validate_manifest.py) checks that all of it loads; these pin the
rules it can't know: which image and pull policy, dry runs by default, the fleet DDS config,
where each engine's runs land, and what the daily profile overrides.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

DECEMBER = Path(__file__).resolve().parent.parent / "december_2026"

ENGINES = (
    "box_opening",
    "button_press",
    "fridge",
    "grab_container",
    "microwave",
    "microwave_place",
    "place_container",
)

FLEET_DDS_URI = "file:///etc/cyclonedds/fleet.xml,file:///etc/cyclonedds/robot.xml"
DDS_VOLUMES = (
    "./config/robots/current/cyclonedds.xml:/etc/cyclonedds/robot.xml:ro",
    "./config/dds/cyclonedds-fleet.xml:/etc/cyclonedds/fleet.xml:ro",
)


def _manifest() -> dict:
    return yaml.safe_load((DECEMBER / "sheppy-manifest.yaml").read_text())


def node(name: str) -> dict:
    found = [n for n in _manifest()["nodes"] if n["name"] == name]
    assert found, f"no node {name!r} in the manifest"
    return found[0]


def alt(node_name: str, alt_id: str) -> dict:
    found = [a for a in node(node_name)["alternatives"] if a["id"] == alt_id]
    assert found, f"no alternative {alt_id!r} on node {node_name!r}"
    return found[0]


def daily() -> dict:
    return yaml.safe_load((DECEMBER / "profiles" / "daily.yaml").read_text())


def data_host(cap: str) -> str:
    # box_opening keeps the folder it already has on snorlax (its reach-zone cache and run
    # history); every other engine's runs land under the daily tree, out of /tmp.
    if cap == "box_opening":
        return "/home/snorlax/.ros/box_opening"
    return f"/home/snorlax/daily/data/{cap}"


@pytest.mark.parametrize("cap", ENGINES)
def test_every_engine_has_dev_and_mock(cap):
    ids = {a["id"] for a in node(cap)["alternatives"]}
    assert {f"{cap}_dev", f"{cap}_mock"} <= ids


@pytest.mark.parametrize("cap", ENGINES)
def test_dev_alternatives_run_dev_with_prefer_latest(cap):
    container = alt(cap, f"{cap}_dev")["container"]
    assert (
        container["image"]
        == f"ghcr.io/rammp-org/rammp-kitchen-capability-engines/{cap}:dev"
    )
    assert container["pull_policy"] == "prefer_latest"


@pytest.mark.parametrize("cap", ENGINES)
def test_dev_alternatives_dry_run_by_default(cap):
    params = alt(cap, f"{cap}_dev")["params"]
    assert params["allow_execute"] is False
    assert params["use_orchestrator"] is True


@pytest.mark.parametrize("cap", ENGINES)
def test_dev_command_is_installed_executable(cap):
    command = alt(cap, f"{cap}_dev")["container"]["command"]
    assert command == [f"/module_ws/install/{cap}/lib/{cap}/{cap}_node"]


@pytest.mark.parametrize("cap", ENGINES)
def test_dev_alternatives_speak_fleet_dds(cap):
    container = alt(cap, f"{cap}_dev")["container"]
    assert container["environment"]["CYCLONEDDS_URI"] == FLEET_DDS_URI
    assert container["environment"]["ROS_DOMAIN_ID"] == "0"
    for volume in DDS_VOLUMES:
        assert volume in container["volumes"]


@pytest.mark.parametrize("cap", ENGINES)
def test_dev_data_dir_on_daily_tree(cap):
    dev = alt(cap, f"{cap}_dev")
    volumes = dev["container"]["volumes"]
    for key in ("data_dir", "state_dir", "log_dir"):
        if key in dev["params"]:
            assert f"{data_host(cap)}:{dev['params'][key]}" in volumes
    assert not [v for v in volumes if v.startswith(f"/tmp/{cap}")]


def test_arm_observes_contact():
    assert (
        alt("arm", "kinova_gen3_driver_nightly")["params"]["contact_detection"]
        == "observe"
    )


def test_daily_selects_every_engine_dev():
    selections = daily()["selections"]
    for cap in ENGINES:
        assert selections[cap] == f"{cap}_dev"
    assert selections["sam3"] == "sam3_docker"
    assert selections["arm"] == "kinova_gen3_driver_nightly"


def test_daily_runs_engines_standalone():
    overrides = daily()["overrides"]
    for cap in ENGINES:
        assert overrides[cap]["use_orchestrator"] is False


def test_daily_never_enables_execute():
    for node_name, params in daily()["overrides"].items():
        assert not params.get("allow_execute"), node_name


def test_fridge_real_runs_a_local_tag_daily_never_pulls():
    # fridge_dev pulls ghcr's :dev at every start; a local build on the same tag would be replaced.
    container = alt("fridge", "fridge_real")["container"]
    assert (
        container["image"]
        == "ghcr.io/rammp-org/rammp-kitchen-capability-engines/fridge:local"
    )
    assert container["pull_policy"] == "never"


# The eating tasks: capability engines outside the engines repo, on Docker Hub with version tags
# only, so the manifest pins a release and a PR moves the pin.
EATING = {"feeding": "rammp_feeding_docker", "drinking": "rammp_drinking_docker"}


@pytest.mark.parametrize("name", EATING)
def test_eating_engines_pin_a_release(name):
    image = alt(name, EATING[name])["container"]["image"]
    assert re.fullmatch(
        rf"docker\.io/debajyotidasgupta/rammp-{name}:\d+\.\d+\.\d+", image
    ), image


@pytest.mark.parametrize("name", EATING)
def test_eating_engines_have_a_mock(name):
    assert alt(name, f"{name}_mock")


@pytest.mark.parametrize("name", EATING)
def test_eating_engines_speak_fleet_dds(name):
    container = alt(name, EATING[name])["container"]
    assert container["environment"]["CYCLONEDDS_URI"] == FLEET_DDS_URI
    for volume in DDS_VOLUMES:
        assert volume in container["volumes"]


@pytest.mark.parametrize("name", EATING)
def test_eating_engines_share_the_feeding_state(name):
    container = alt(name, EATING[name])["container"]
    assert "/srv/rammp_feeding/config:/config" in container["volumes"]
    assert "/srv/rammp_feeding/runs:/runs" in container["volumes"]
    assert alt(name, EATING[name])["params"]["use_orchestrator"] is True


def test_daily_runs_the_eating_engines_standalone():
    profile = daily()
    for name, alt_id in EATING.items():
        assert profile["selections"][name] == alt_id
        assert profile["overrides"][name]["use_orchestrator"] is False
