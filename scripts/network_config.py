#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pyyaml==6.0.2"]
# ///
"""Apply a robot's network.yaml to this host's NICs through NetworkManager.

    uv run scripts/network_config.py [--validate | --dry-run] <robot dir>

Each entry names a NetworkManager profile this script owns, the NIC it binds
to, and its IPv4 method. The script creates or updates that profile, turns off
autoconnect on every other profile bound to the same NIC (one left on takes the
NIC back at boot), and brings the profile up when the NIC has a link.

It runs as the user and calls `sudo nmcli` only for changes: `sudo uv run`
finds no uv on root's PATH and would build a second environment.

Exit status: 0 ok, 1 a change failed, 2 the config or a preflight check is
invalid and nothing was changed.
"""

from __future__ import annotations

import argparse
import ipaddress
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

CONFIG_NAME = "network.yaml"


class ConfigError(Exception):
    """The file, or the host it is applied to, is not usable. Nothing changed."""


@dataclass(frozen=True)
class Interface:
    profile: str
    device: str
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


def parse(data: object, where: str) -> list[Interface]:
    entries = _keys(where, data, ["interfaces"])["interfaces"]
    if not isinstance(entries, list) or not entries:
        raise ConfigError(f"{where}.interfaces: expected a non-empty list")
    interfaces = []
    for i, entry in enumerate(entries):
        at = f"{where}.interfaces[{i}]"
        _keys(at, entry, ["profile", "device", "ipv4"])
        method, address, gateway = parse_ipv4(f"{at}.ipv4", entry["ipv4"])
        interfaces.append(
            Interface(
                profile=_string(f"{at}.profile", entry["profile"]),
                device=_string(f"{at}.device", entry["device"]),
                method=method,
                address=address,
                gateway=gateway,
            )
        )
    for field in ("profile", "device"):
        values = [getattr(i, field) for i in interfaces]
        dupes = sorted({v for v in values if values.count(v) > 1})
        if dupes:
            raise ConfigError(f"{where}: duplicate {field} {', '.join(dupes)}")
    return interfaces


def load(robot_dir: Path) -> list[Interface]:
    path = robot_dir / CONFIG_NAME
    if not path.is_file():
        raise ConfigError(f"{path}: not found")
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise ConfigError(f"{path}: {e}") from e
    return parse(data, str(path))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--validate", action="store_true", help="check the file and exit")
    mode.add_argument("--dry-run", action="store_true", help="print changes, make none")
    parser.add_argument("robot_dir", type=Path)
    args = parser.parse_args(argv)

    try:
        interfaces = load(args.robot_dir)
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if args.validate:
        print(f"{args.robot_dir / CONFIG_NAME}: ok — {len(interfaces)} interfaces")
        return 0
    return apply(interfaces, dry_run=args.dry_run)


def apply(interfaces: list[Interface], dry_run: bool) -> int:
    raise NotImplementedError  # Task 2


if __name__ == "__main__":
    sys.exit(main())
