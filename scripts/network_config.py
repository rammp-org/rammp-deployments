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
import shlex
import subprocess
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


@dataclass(frozen=True)
class Profile:
    uuid: str
    name: str
    type: str
    interface: str
    autoconnect: str
    mac: str


def _run(cmd: list[str]) -> str:
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def _get(fields: list[str], *target: str) -> dict[str, str]:
    """`nmcli -g` prints one value per line, with ':' escaped as '\\:'."""
    lines = _run(["nmcli", "-g", ",".join(fields), *target]).split("\n")
    return {f: v.replace("\\:", ":") for f, v in zip(fields, lines)}


def profiles() -> list[Profile]:
    found = []
    for uuid in _run(["nmcli", "-g", "UUID", "con", "show"]).split():
        g = _get(
            [
                "connection.id",
                "connection.type",
                "connection.interface-name",
                "connection.autoconnect",
            ],
            "con",
            "show",
            uuid,
        )
        mac = ""
        if g["connection.type"] == "802-3-ethernet":
            mac = _get(["802-3-ethernet.mac-address"], "con", "show", uuid)[
                "802-3-ethernet.mac-address"
            ]
        found.append(
            Profile(
                uuid,
                g["connection.id"],
                g["connection.type"],
                g["connection.interface-name"],
                g["connection.autoconnect"],
                mac,
            )
        )
    return found


def _sys(device: str, name: str) -> str:
    try:
        return Path(f"/sys/class/net/{device}/{name}").read_text().strip()
    except OSError:  # carrier is unreadable while the link is administratively down
        return ""


def settings(iface: Interface) -> dict[str, str]:
    return {
        "connection.interface-name": iface.device,
        "connection.autoconnect": "yes",
        "ipv4.method": "manual" if iface.method == "static" else "auto",
        "ipv4.addresses": iface.address,
        "ipv4.gateway": iface.gateway,
    }


def plan(
    iface: Interface, existing: list[Profile]
) -> tuple[list[list[str]], list[str]]:
    """The nmcli commands that make this entry true, and a note per side effect."""
    want = settings(iface)
    pairs = [x for kv in want.items() for x in kv]
    commands, notes = [], []
    mine = [p for p in existing if p.name == iface.profile]
    if len(mine) > 1:
        raise ConfigError(
            f"{iface.profile}: {len(mine)} profiles have this name; remove the extras"
        )
    if not mine:
        commands.append(
            [
                "nmcli",
                "con",
                "add",
                "type",
                "ethernet",
                "con-name",
                iface.profile,
                "ifname",
                iface.device,
                *pairs[2:],
            ]
        )
    else:
        if mine[0].type != "802-3-ethernet":
            raise ConfigError(
                f"{iface.profile}: existing profile is {mine[0].type}, not ethernet"
            )
        if _get(list(want), "con", "show", mine[0].uuid) != want:
            commands.append(["nmcli", "con", "mod", mine[0].uuid, *pairs])
    mac = _sys(iface.device, "address").lower()
    for p in existing:
        bound = p.interface == iface.device or (p.mac and p.mac.lower() == mac)
        if p.name != iface.profile and bound and p.autoconnect == "yes":
            commands.append(
                ["nmcli", "con", "mod", p.uuid, "connection.autoconnect", "no"]
            )
            notes.append(f"autoconnect off on {p.name!r}")
    return commands, notes


def apply(interfaces: list[Interface], dry_run: bool) -> int:
    # Preflight: everything that can be refused is refused before the first change.
    missing = [
        i.device for i in interfaces if not Path(f"/sys/class/net/{i.device}").exists()
    ]
    if missing:
        print(f"error: no such NIC on this host: {', '.join(missing)}", file=sys.stderr)
        return 2
    try:
        existing = profiles()
        plans = [plan(i, existing) for i in interfaces]
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except (OSError, subprocess.CalledProcessError) as e:
        print(f"error: cannot read NetworkManager state: {e}", file=sys.stderr)
        return 2

    failed = 0
    for iface, (commands, notes) in zip(interfaces, plans):
        prefix = f"{iface.device} ({iface.profile})"
        if dry_run:
            for cmd in commands:
                print(f"{prefix}: would run: sudo {shlex.join(cmd)}")
            if not commands:
                print(f"{prefix}: unchanged")
            continue
        try:
            for cmd in commands:
                subprocess.run(
                    ["sudo", *cmd], check=True, capture_output=True, text=True
                )
            for note in notes:
                print(f"{prefix}: {note}")
            if _sys(iface.device, "carrier") != "1":
                print(
                    f"{prefix}: {'saved' if commands else 'unchanged'}; no link, comes up when cabled"
                )
                continue
            active = _get(["GENERAL.CONNECTION"], "dev", "show", iface.device)[
                "GENERAL.CONNECTION"
            ]
            if commands or active != iface.profile:
                subprocess.run(
                    ["sudo", "nmcli", "--wait", "60", "con", "up", iface.profile],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            state = _get(
                ["GENERAL.CONNECTION", "IP4.ADDRESS"], "dev", "show", iface.device
            )
            status = "applied" if commands or active != iface.profile else "unchanged"
            print(
                f"{prefix}: {status}; active {state['GENERAL.CONNECTION']!r}, IPv4 {state['IP4.ADDRESS'] or 'none'}"
            )
        except subprocess.CalledProcessError as e:
            print(
                f"{prefix}: FAILED: {shlex.join(e.cmd)}: {(e.stderr or '').strip()}",
                file=sys.stderr,
            )
            failed += 1
    return 1 if failed else 0


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


if __name__ == "__main__":
    sys.exit(main())
