#!/usr/bin/env -S uv run --script
# /// script
# requires-python = "==3.9.*"
# dependencies = [
#   "pyyaml==6.0.2",
#   # kortex_api requires protobuf <=3.20.
#   "protobuf==3.20.0",
#   # Kinova's public artifactory; 2.8.0 is the SDK the arm driver image links.
#   "kortex_api @ https://artifactory.kinovaapps.com/artifactory/generic-public/kortex/API/2.8.0/kortex_api-2.8.0.post5-py3-none-any.whl",
# ]
# ///
"""Apply a robot's arm.yaml to its Kinova Gen3, through the Kortex API.

    uv run scripts/arm_config.py [--validate | --dry-run] <robot dir>

Today that is one setting: the wrist Ethernet port (the Interconnect's
expansion port), which the Kinova Vision Puck uses to carry a camera through
the arm. It is off by default. This follows Kinova's VisionPkConf.py: find the
Interconnect, read the port's configuration, change enabled/speed/duplex only,
and write it back. The write is read back to confirm, and skipped when the arm
already matches.

Whether the arm keeps this setting across a power cycle is not yet known
(asked of Kinova, 2026-11-17). Until it is, re-run this after powering the arm.

Kortex ships for Python 3.9, hence the pin; uv fetches that Python. The login
is the arm's: KINOVA_USERNAME and KINOVA_PASSWORD, default admin/admin.

Exit status: 0 ok, 1 the arm could not be reached or a change failed, 2 the
config is invalid and nothing was changed.
"""

from __future__ import annotations

import argparse
import contextlib
import ipaddress
import os
import socket
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import yaml

CONFIG_NAME = "arm.yaml"
PORT = 10000  # Kortex TCP
# arm.yaml spelling -> Kortex enum name
SPEEDS = {"10M": "ETHERNET_SPEED_10M", "100M": "ETHERNET_SPEED_100M"}
DUPLEXES = {"half": "ETHERNET_DUPLEX_HALF", "full": "ETHERNET_DUPLEX_FULL"}


class ConfigError(Exception):
    """The file is not usable. Nothing changed."""


class ArmError(Exception):
    """The arm answered, but not as expected."""


@dataclass(frozen=True)
class WristEthernet:
    enabled: bool
    speed: str  # a key of SPEEDS
    duplex: str  # a key of DUPLEXES


@dataclass(frozen=True)
class Arm:
    address: str
    wrist_ethernet: WristEthernet


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


def _choice(where: str, value: object, choices: dict) -> str:
    if value not in choices:
        raise ConfigError(
            f"{where}: expected one of {', '.join(choices)}, got {value!r}"
        )
    return value


def parse(data: object, where: str) -> Arm:
    arm = _keys(where, data, ["address", "wrist_ethernet"])
    try:
        address = str(ipaddress.IPv4Address(str(arm["address"])))
    except ValueError as e:
        raise ConfigError(f"{where}.address: {e}") from e
    at = f"{where}.wrist_ethernet"
    port = _keys(at, arm["wrist_ethernet"], ["enabled", "speed", "duplex"])
    if not isinstance(port["enabled"], bool):
        raise ConfigError(
            f"{at}.enabled: expected true or false, got {port['enabled']!r}"
        )
    wrist = WristEthernet(
        enabled=port["enabled"],
        speed=_choice(f"{at}.speed", port["speed"], SPEEDS),
        duplex=_choice(f"{at}.duplex", port["duplex"], DUPLEXES),
    )
    return Arm(address, wrist)


def load(robot_dir: Path) -> Arm:
    path = robot_dir / CONFIG_NAME
    if not path.is_file():
        raise ConfigError(f"{path}: not found")
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise ConfigError(f"{path}: {e}") from e
    return parse(data, str(path))


def describe(w: WristEthernet) -> str:
    return f"{'enabled' if w.enabled else 'disabled'} {w.speed} {w.duplex}"


class _Wrist:
    """The Interconnect's expansion Ethernet port, over an open Kortex session."""

    def __init__(self, router):
        from kortex_api.autogen.client_stubs.DeviceManagerClientRpc import (
            DeviceManagerClient,
        )
        from kortex_api.autogen.client_stubs.InterconnectConfigClientRpc import (
            InterconnectConfigClient,
        )
        from kortex_api.autogen.messages import Common_pb2, InterconnectConfig_pb2

        self._pb = InterconnectConfig_pb2
        devices = DeviceManagerClient(router).ReadAllDevices()
        ids = [
            d.device_identifier
            for d in devices.device_handle
            if d.device_type == Common_pb2.INTERCONNECT
        ]
        if not ids:
            raise ArmError("no Interconnect module in the arm's device list")
        self._id = ids[0]
        self._client = InterconnectConfigClient(router)

    def _read(self):
        which = self._pb.EthernetDeviceIdentification()
        which.device = self._pb.ETHERNET_DEVICE_EXPANSION
        return self._client.GetEthernetConfiguration(which, self._id)

    def read_wrist(self) -> WristEthernet:
        cfg = self._read()
        speed = {v: k for k, v in SPEEDS.items()}.get(
            self._pb.EthernetSpeed.Name(cfg.speed),
            self._pb.EthernetSpeed.Name(cfg.speed),
        )
        duplex = {v: k for k, v in DUPLEXES.items()}.get(
            self._pb.EthernetDuplex.Name(cfg.duplex),
            self._pb.EthernetDuplex.Name(cfg.duplex),
        )
        return WristEthernet(cfg.enabled, speed, duplex)

    def write_wrist(self, want: WristEthernet) -> None:
        cfg = self._read()  # read-modify-write, as Kinova's sample does
        cfg.enabled = want.enabled
        cfg.speed = self._pb.EthernetSpeed.Value(SPEEDS[want.speed])
        cfg.duplex = self._pb.EthernetDuplex.Value(DUPLEXES[want.duplex])
        self._client.SetEthernetConfiguration(cfg, self._id)


@contextlib.contextmanager
def _session(address: str) -> Iterator[_Wrist]:
    # Kortex's own connect has no timeout: an arm that is off would hang it.
    try:
        socket.create_connection((address, PORT), timeout=5).close()
    except OSError as e:
        raise ConnectionError(
            f"no answer from {address}:{PORT} within 5 s ({e})"
        ) from e

    from kortex_api.autogen.messages import Session_pb2
    from kortex_api.RouterClient import RouterClient
    from kortex_api.SessionManager import SessionManager
    from kortex_api.TCPTransport import TCPTransport

    transport = TCPTransport()
    router = RouterClient(transport, RouterClient.basicErrorCallback)
    transport.connect(address, PORT)
    session = SessionManager(router)
    try:
        info = Session_pb2.CreateSessionInfo()
        info.username = os.environ.get("KINOVA_USERNAME", "admin")
        info.password = os.environ.get("KINOVA_PASSWORD", "admin")
        info.session_inactivity_timeout = 60000  # ms, as Kinova's sample
        info.connection_inactivity_timeout = 2000
        session.CreateSession(info)
        try:
            yield _Wrist(router)
        finally:
            session.CloseSession()
    finally:
        transport.disconnect()


def _apply_wrist(arm: Arm, dry_run: bool) -> str:
    want = arm.wrist_ethernet
    with _session(arm.address) as wrist:
        current = wrist.read_wrist()
        if current == want:
            return f"wrist Ethernet unchanged ({describe(current)})"
        if dry_run:
            return (
                f"wrist Ethernet would change {describe(current)} -> {describe(want)}"
            )
        wrist.write_wrist(want)
        readback = wrist.read_wrist()
        if readback != want:
            raise ArmError(
                f"wrist Ethernet read back {describe(readback)}, expected {describe(want)}"
            )
        return f"wrist Ethernet applied {describe(current)} -> {describe(want)}"


def apply(arm: Arm, dry_run: bool) -> int:
    prefix = f"arm ({arm.address})"
    try:
        print(f"{prefix}: {_apply_wrist(arm, dry_run)}")
        return 0
    except (ArmError, ConnectionError, OSError) as e:
        print(f"{prefix}: FAILED: {e}", file=sys.stderr)
    except Exception as e:  # Kortex raises KServerException/KClientException
        print(f"{prefix}: FAILED: {type(e).__name__}: {e}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--validate", action="store_true", help="check the file and exit")
    mode.add_argument("--dry-run", action="store_true", help="print changes, make none")
    parser.add_argument("robot_dir", type=Path)
    args = parser.parse_args(argv)

    try:
        arm = load(args.robot_dir)
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if args.validate:
        print(
            f"{args.robot_dir / CONFIG_NAME}: ok — wrist Ethernet {describe(arm.wrist_ethernet)}"
        )
        return 0
    return apply(arm, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
