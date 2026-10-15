#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pyyaml==6.0.2", "depthai==3.10.0", "numpy==2.2.6"]
# ///
"""Apply a robot's oak_d.yaml to its OAK-D PoE cameras' bootloader network config.

    uv run scripts/oak_d_config.py [--validate | --dry-run] <robot dir>

Each camera is found by device ID through depthai discovery, the same lookup
the ROS driver does, so it must be on a subnet this host has an address on.
Only the network fields are rewritten; everything else is written back as read.
A write restarts the camera, so a camera that already matches is not written.

dhcp stores no fallback address. On bootloader <=0.0.29 a stored address makes
the camera boot straight onto it without ever sending a DHCP request, even with
staticIpv4 false.

The bootloader is opened with allowFlashingBootloader=False: this never writes
firmware.

Exit status: 0 ok, 1 a camera failed, 2 the config is invalid and nothing was
changed.
"""

from __future__ import annotations

import argparse
import ipaddress
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

CONFIG_NAME = "oak_d.yaml"
NETWORK_FIELDS = ("staticIpv4", "ipv4", "ipv4Mask", "ipv4Gateway")


class ConfigError(Exception):
    """The file is not usable. Nothing changed."""


class CameraError(Exception):
    """One camera could not be applied. The others still are."""


@dataclass(frozen=True)
class Camera:
    name: str
    device_id: str
    method: str  # "static" or "dhcp"
    address: str = ""  # CIDR, static only
    gateway: str = ""  # dotted, static only, may be empty


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


def parse_ipv4(where: str, value: object) -> tuple[str, str, str]:
    """Returns (method, CIDR address or "", gateway or "")."""
    ipv4 = _keys(where, value, ["method"], ["address", "gateway"])
    method = ipv4["method"]
    if method == "dhcp":
        _keys(where, ipv4, ["method"])
        return "dhcp", "", ""
    if method != "static":
        raise ConfigError(f"{where}.method: expected static or dhcp, got {method!r}")
    _keys(where, ipv4, ["method", "address"], ["gateway"])
    text = _string(f"{where}.address", ipv4["address"])
    if "/" not in text:
        raise ConfigError(f"{where}.address: {text!r} needs a prefix, e.g. {text}/24")
    try:
        address = ipaddress.IPv4Interface(text)
    except ValueError as e:
        raise ConfigError(f"{where}.address: {e}") from e
    gateway = ""
    if "gateway" in ipv4:
        try:
            gw = ipaddress.IPv4Address(_string(f"{where}.gateway", ipv4["gateway"]))
        except ValueError as e:
            raise ConfigError(f"{where}.gateway: {e}") from e
        if gw not in address.network:
            raise ConfigError(f"{where}.gateway: {gw} is outside {address.network}")
        gateway = str(gw)
    return "static", str(address), gateway


def parse(data: object, where: str) -> list[Camera]:
    entries = _keys(where, data, ["cameras"])["cameras"]
    if not isinstance(entries, dict) or not entries:
        raise ConfigError(
            f"{where}.cameras: expected a non-empty mapping of node name to camera"
        )
    cameras = []
    for name, entry in entries.items():
        at = f"{where}.cameras.{name}"
        _keys(at, entry, ["device_id", "ipv4"])
        device_id = entry["device_id"]
        if not isinstance(device_id, str) or not device_id:
            raise ConfigError(
                f'{at}.device_id: expected a string in quotes, e.g. "194430107187F42E00", got {device_id!r}'
            )
        method, address, gateway = parse_ipv4(f"{at}.ipv4", entry["ipv4"])
        cameras.append(Camera(str(name), device_id, method, address, gateway))
    ids = [c.device_id for c in cameras]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ConfigError(f"{where}: duplicate device_id {', '.join(dupes)}")
    return cameras


def load(robot_dir: Path) -> list[Camera]:
    path = robot_dir / CONFIG_NAME
    if not path.is_file():
        raise ConfigError(f"{path}: not found")
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise ConfigError(f"{path}: {e}") from e
    return parse(data, str(path))


def _encode(dotted: str) -> int:
    """The bootloader stores an IPv4 address as a little-endian uint32."""
    return int.from_bytes(ipaddress.IPv4Address(dotted).packed, "little")


def _decode(value: int) -> str:
    return str(ipaddress.IPv4Address(value.to_bytes(4, "little")))


def wanted_network(cam: Camera) -> dict[str, bool | int]:
    if cam.method == "dhcp":
        return {"staticIpv4": False, "ipv4": 0, "ipv4Mask": 0, "ipv4Gateway": 0}
    address = ipaddress.IPv4Interface(cam.address)
    return {
        "staticIpv4": True,
        "ipv4": _encode(str(address.ip)),
        "ipv4Mask": _encode(str(address.netmask)),
        "ipv4Gateway": _encode(cam.gateway) if cam.gateway else 0,
    }


def describe(network: dict) -> str:
    if not network["staticIpv4"]:
        fallback = f", fallback {_decode(network['ipv4'])}" if network["ipv4"] else ""
        return f"dhcp{fallback}"
    gateway = f" gw {_decode(network['ipv4Gateway'])}" if network["ipv4Gateway"] else ""
    return f"static {_decode(network['ipv4'])} mask {_decode(network['ipv4Mask'])}{gateway}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--validate", action="store_true", help="check the file and exit")
    mode.add_argument("--dry-run", action="store_true", help="print changes, make none")
    parser.add_argument("robot_dir", type=Path)
    args = parser.parse_args(argv)

    try:
        cameras = load(args.robot_dir)
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if args.validate:
        print(f"{args.robot_dir / CONFIG_NAME}: ok — {len(cameras)} cameras")
        return 0
    return apply(cameras, dry_run=args.dry_run)


def _discover(dai, ids: list[str], attempts: int = 5) -> dict:
    """Retry until every ID answers. Discovery is a UDP broadcast that can miss a
    reply, and a camera drops out of it for about 3 s after any bootloader
    connection to it closes, including the read of a previous run."""
    found = {}
    for attempt in range(attempts):
        if attempt:
            time.sleep(2)
        for info in dai.Device.getAllAvailableDevices():
            found[info.deviceId] = info
        if all(i in found for i in ids):
            break
    return found


def _wait_for_restart(dai, device_id: str, timeout: float = 60.0) -> str | None:
    """A config write resets the camera: wait for it to drop off, then come back."""
    deadline = time.monotonic() + timeout
    gone_by = time.monotonic() + 20
    while time.monotonic() < gone_by:
        if device_id not in _discover(dai, [device_id], attempts=1):
            break
        time.sleep(1)
    while time.monotonic() < deadline:
        info = _discover(dai, [device_id], attempts=1).get(device_id)
        if info is not None:
            return info.name
        time.sleep(2)
    return None


def _apply_one(dai, cam: Camera, info, dry_run: bool) -> str:
    if info.state != dai.XLinkDeviceState.X_LINK_BOOTLOADER:
        raise CameraError(
            f"in state {info.state.name} at {info.name}; stop the node using it and retry"
        )
    want = wanted_network(cam)
    with dai.DeviceBootloader(info, allowFlashingBootloader=False) as bl:
        data = bl.readConfigData()
        current = {k: data["network"][k] for k in NETWORK_FIELDS}
        if current == want:
            return f"unchanged at {info.name} ({describe(current)}, bootloader {bl.getVersion()})"
        if dry_run:
            return (
                f"would change {describe(current)} -> {describe(want)} (at {info.name})"
            )
        data["network"].update(want)
        ok, err = bl.flashConfigData(data)
        if not ok:
            raise CameraError(f"config write failed: {err}")
        readback = {k: bl.readConfigData()["network"][k] for k in NETWORK_FIELDS}
        if readback != want:
            raise CameraError(
                f"read back {describe(readback)}, expected {describe(want)}"
            )
    # The bootloader connection is closed here, so the camera can reset.
    address = _wait_for_restart(dai, cam.device_id)
    if address is None:
        raise CameraError(
            f"config written ({describe(want)}) but the camera did not come back within 60 s; "
            "with dhcp, check the server answers it; with static, check this host has an "
            "address on that subnet"
        )
    if cam.method == "static" and address != str(
        ipaddress.IPv4Interface(cam.address).ip
    ):
        raise CameraError(f"came back at {address}, expected {cam.address}")
    if cam.method == "dhcp" and address.startswith("169.254."):
        raise CameraError(
            f"came back at {address}: no DHCP lease, fell back to link-local"
        )
    return f"applied {describe(want)}; now at {address}"


def apply(cameras: list[Camera], dry_run: bool) -> int:
    import depthai as dai  # only when applying: --validate must not need it

    found = _discover(dai, [c.device_id for c in cameras])
    failed = 0
    for cam in cameras:
        prefix = f"{cam.name} ({cam.device_id})"
        info = found.get(cam.device_id)
        if info is None:
            print(
                f"{prefix}: FAILED: not found by discovery. It is powered off, on a subnet this "
                "host has no address on, or held by a running camera node (stop it first).",
                file=sys.stderr,
            )
            failed += 1
            continue
        try:
            print(f"{prefix}: {_apply_one(dai, cam, info, dry_run)}")
        except (
            CameraError,
            RuntimeError,
        ) as e:  # depthai raises RuntimeError on connect failures
            print(f"{prefix}: FAILED: {e}", file=sys.stderr)
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
