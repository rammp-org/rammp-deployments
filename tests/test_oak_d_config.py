from __future__ import annotations

import enum
from types import SimpleNamespace

import pytest
import yaml
from conftest import load_script

oak = load_script("oak_d_config")

WRIST_ID = "194430107187F42E00"
PUCK_IP = "10.20.0.50"


class State(enum.Enum):
    X_LINK_ANY_STATE = 0
    X_LINK_BOOTED = 1
    X_LINK_UNBOOTED = 2
    X_LINK_BOOTLOADER = 3


class Protocol(enum.Enum):
    X_LINK_TCP_IP = 0


class FakeDeviceInfo:
    def __init__(
        self, name: str = "", device_id: str = "", state=State.X_LINK_ANY_STATE
    ):
        self.name = name
        self.deviceId = device_id
        self.state = state
        self.protocol = None


def fake_dai(broadcast: list[FakeDeviceInfo]):
    return SimpleNamespace(
        XLinkDeviceState=State,
        XLinkProtocol=Protocol,
        DeviceInfo=FakeDeviceInfo,
        Device=SimpleNamespace(getAllAvailableDevices=lambda: list(broadcast)),
    )


def camera(reach_at: str = PUCK_IP, method: str = "static") -> oak.Camera:
    address = f"{PUCK_IP}/24" if method == "static" else ""
    gateway = "10.20.0.1" if method == "static" else ""
    return oak.Camera("wrist_camera", WRIST_ID, method, address, gateway, reach_at)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(oak.time, "sleep", lambda s: None)


def test_reach_at_parsed():
    data = yaml.safe_load(
        f"""
cameras:
  wrist_camera:
    device_id: "{WRIST_ID}"
    reach_at: {PUCK_IP}
    ipv4: {{method: static, address: {PUCK_IP}/24, gateway: 10.20.0.1}}
"""
    )
    (cam,) = oak.parse(data, "oak_d.yaml")
    assert cam.reach_at == PUCK_IP


def test_reach_at_absent_is_empty():
    data = {"cameras": {"c": {"device_id": "X", "ipv4": {"method": "dhcp"}}}}
    (cam,) = oak.parse(data, "oak_d.yaml")
    assert cam.reach_at == ""


def test_reach_at_invalid_rejected():
    data = {
        "cameras": {
            "c": {"device_id": "X", "reach_at": "10.20.0", "ipv4": {"method": "dhcp"}}
        }
    }
    with pytest.raises(oak.ConfigError, match=r"reach_at"):
        oak.parse(data, "oak_d.yaml")


def test_locate_prefers_broadcast(monkeypatch):
    seen = FakeDeviceInfo("192.168.4.13", WRIST_ID, State.X_LINK_BOOTLOADER)

    def no_unicast(ip):
        raise AssertionError("unicast search must not run when broadcast found it")

    monkeypatch.setattr(oak, "_unicast_search", no_unicast)
    assert oak._locate(fake_dai([]), camera(), {WRIST_ID: seen}) is seen


def test_locate_falls_back_to_reach_at(monkeypatch):
    monkeypatch.setattr(
        oak, "_unicast_search", lambda ip: [(WRIST_ID, ip, "X_LINK_BOOTLOADER")]
    )
    info = oak._locate(fake_dai([]), camera(), {})
    assert info.name == PUCK_IP
    assert info.state is State.X_LINK_BOOTLOADER
    assert info.protocol is Protocol.X_LINK_TCP_IP


def test_locate_without_reach_at_is_none(monkeypatch):
    monkeypatch.setattr(oak, "_unicast_search", lambda ip: [])
    assert oak._locate(fake_dai([]), camera(reach_at=""), {}) is None


def test_locate_refuses_other_device_at_reach_at(monkeypatch):
    other = "19443010219A974800"
    monkeypatch.setattr(
        oak, "_unicast_search", lambda ip: [(other, ip, "X_LINK_BOOTLOADER")]
    )
    with pytest.raises(
        oak.CameraError, match=f"{other}.*{WRIST_ID}|{WRIST_ID}.*{other}"
    ):
        oak._locate(fake_dai([]), camera(), {})


def test_wait_static_uses_unicast(monkeypatch):
    answers = iter(
        [
            [(WRIST_ID, PUCK_IP, "X_LINK_BOOTLOADER")],
            [],
            [(WRIST_ID, PUCK_IP, "X_LINK_BOOTLOADER")],
        ]
    )
    asked = []

    def search(ip):
        asked.append(ip)
        return next(answers)

    monkeypatch.setattr(oak, "_unicast_search", search)
    assert oak._wait_for_restart(fake_dai([]), camera()) == PUCK_IP
    assert asked == [PUCK_IP] * 3


def test_wait_dhcp_uses_broadcast(monkeypatch):
    back = FakeDeviceInfo("192.168.4.13", WRIST_ID, State.X_LINK_BOOTLOADER)
    monkeypatch.setattr(
        oak, "_unicast_search", lambda ip: pytest.fail("no unicast for dhcp")
    )
    rounds = iter([[back], [], [back]])  # still up, rebooting, back with a lease
    dai = fake_dai([])
    dai.Device.getAllAvailableDevices = lambda: next(rounds)
    assert oak._wait_for_restart(dai, camera(method="dhcp")) == "192.168.4.13"


def test_unicast_search_parses_child_output(monkeypatch):
    calls = {}

    def run(cmd, **kw):
        calls["env"] = kw["env"]
        out = f'[2026-11-17] [warning] depthai noise\n[["{WRIST_ID}", "{PUCK_IP}", "X_LINK_BOOTED"]]\n'
        return SimpleNamespace(returncode=0, stdout=out, stderr="")

    monkeypatch.setattr(oak.subprocess, "run", run)
    assert oak._unicast_search(PUCK_IP) == [(WRIST_ID, PUCK_IP, "X_LINK_BOOTED")]
    assert calls["env"]["DEPTHAI_DEVICE_NAME_LIST"] == PUCK_IP


def test_unicast_search_child_failure_is_reported(monkeypatch):
    def run(cmd, **kw):
        err = "Traceback ...\nModuleNotFoundError: No module named 'depthai'\n"
        return SimpleNamespace(returncode=1, stdout="", stderr=err)

    monkeypatch.setattr(oak.subprocess, "run", run)
    with pytest.raises(oak.CameraError, match="No module named 'depthai'"):
        oak._unicast_search(PUCK_IP)


def test_unicast_search_timeout_is_no_answer(monkeypatch):
    def run(cmd, **kw):
        raise oak.subprocess.TimeoutExpired(cmd, kw["timeout"])

    monkeypatch.setattr(oak.subprocess, "run", run)
    assert oak._unicast_search(PUCK_IP) == []
