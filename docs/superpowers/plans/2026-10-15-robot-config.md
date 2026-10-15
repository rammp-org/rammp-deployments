# Robot config (host network and OAK-D addressing) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `config/robots/flatbot/` holds flatbot's NIC and OAK-D addressing, and two `uv` scripts apply it to the robot, so a reflashed Jetson is set up with two commands.

**Architecture:** Two self-contained PEP 723 `uv` scripts in `scripts/`, each reading one YAML file from a robot directory. `network_config.py` drives NetworkManager with `nmcli` (reads as the user, writes through `sudo`). `oak_d_config.py` finds cameras by device ID with depthai discovery and rewrites only the network fields of each bootloader config. Both validate the whole file before any change, are idempotent, and have `--validate` (CI) and `--dry-run` modes. A new pattern page on the docs site records where every kind of setting lives.

**Tech Stack:** Python 3.10 (the Jetson's), uv ≥0.6 with inline script metadata, PyYAML 6.0.2, depthai 3.10.0, NetworkManager 1.36 `nmcli`, GitHub Actions with `astral-sh/setup-uv@v6`.

**Spec:** `docs/superpowers/specs/2026-10-15-robot-config-design.md`

## Global Constraints

- Branch: `feature/61-robot-config`; squash-merge to `dev` (CONTRIBUTING.md). Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Each script is self-contained: PEP 723 header, no shared module, no `pyproject.toml`.
- `requires-python = ">=3.10"`. Pins: `pyyaml==6.0.2`; OAK script adds `depthai==3.10.0` and `numpy==2.2.6`.
- `network_config.py` runs as the user and prefixes only mutating `nmcli` calls with `sudo`. Never `sudo uv run`.
- `oak_d_config.py` always opens the bootloader with `allowFlashingBootloader=False`; it never writes firmware.
- A `dhcp` camera gets `staticIpv4: false` and `ipv4`, `ipv4Mask`, `ipv4Gateway` all `0`. No fallback address, ever (it disables DHCP on bootloader ≤0.0.29).
- Exit codes, both scripts: `0` all ok, `1` an item failed while applying, `2` the config or a preflight check is invalid (nothing was changed).
- `--validate` touches nothing but the file: no `nmcli`, no `/sys`, no depthai import, no network.
- Profiles the network script did not create are only ever changed to `connection.autoconnect no`; never modified otherwise, never deleted.
- Ruff and mdformat (pre-commit) pass on every commit: `pre-commit run --files <changed files>`.
- No `config/nodes/`. No pytest.

## Review Focus

- **A NIC named in `network.yaml` that this host does not have** (another Jetson): the run stops before any `nmcli` change, exit 2, naming the device. Pinned in Task 2, Step 6.
- **A device ID written unquoted, or all digits**, so YAML loads it as a number: `--validate` rejects it and says to quote it. Pinned in Task 3, Step 2.
- **A static `gateway` outside the `address` subnet, or an address with no `/prefix`**: `--validate` rejects it with the offending value. Pinned in Task 1, Step 2.
- **Running either script a second time**: every item reports `unchanged` and nothing is written (no camera restart, no profile re-up). Pinned in Task 6, Steps 3 and 6.
- **A camera held by a running camera node, or not reachable on any host subnet**: that camera fails with the likely causes listed, the others are still applied, exit 1. Pinned in Task 4, Step 4 and Task 6, Step 7.

______________________________________________________________________

### Task 1: `network.yaml` and `network_config.py --validate`

**Files:**

- Create: `december_2026/config/robots/flatbot/network.yaml`
- Create: `scripts/network_config.py`

**Interfaces:**

- Produces: `load(robot_dir: Path) -> list[Interface]` raising `ConfigError`; `Interface(profile: str, device: str, method: str, address: str, gateway: str)` where `method` is `"static"` or `"dhcp"`, `address` is CIDR or `""`, `gateway` is dotted or `""`. CLI: `network_config.py [--validate | --dry-run] ROBOT_DIR`.

- [ ] **Step 1: Write the flatbot config**

`december_2026/config/robots/flatbot/network.yaml`:

```yaml
# flatbot's Jetson AGX Orin: what each NIC is for.
# Applied by: uv run scripts/network_config.py december_2026/config/robots/flatbot
interfaces:
  # Kinova Gen3 base, direct cable. No DHCP server on this link; the arm is 192.168.1.10.
  - profile: flatbot-arm
    device: enp3s0
    ipv4:
      method: static
      address: 192.168.1.11/24 # no gateway: the arm's network goes nowhere else
  # ESP32-P4 sensor network: DHCP server at 192.168.4.1; both OAKs are here.
  - profile: flatbot-sensors
    device: eno1
    ipv4:
      method: dhcp
```

- [ ] **Step 2: Write the bad configs that must be rejected (the failing checks)**

```bash
T=$(mktemp -d); mkdir -p $T/{nogw,noprefix,dupdev,unknown,empty,missing}
cat > $T/nogw/network.yaml <<'EOF'
interfaces:
  - {profile: a, device: eth9, ipv4: {method: static, address: 10.0.0.2/24, gateway: 10.0.1.1}}
EOF
cat > $T/noprefix/network.yaml <<'EOF'
interfaces:
  - {profile: a, device: eth9, ipv4: {method: static, address: 10.0.0.2}}
EOF
cat > $T/dupdev/network.yaml <<'EOF'
interfaces:
  - {profile: a, device: eth9, ipv4: {method: dhcp}}
  - {profile: b, device: eth9, ipv4: {method: dhcp}}
EOF
cat > $T/unknown/network.yaml <<'EOF'
interfaces:
  - {profile: a, device: eth9, ipv4: {method: dhcp, address: 10.0.0.2/24}}
EOF
: > $T/empty/network.yaml
echo $T
```

Run: `for d in nogw noprefix dupdev unknown empty missing; do uv run scripts/network_config.py --validate $T/$d; echo "exit $?"; done`
Expected now: every run fails because the script does not exist.

- [ ] **Step 3: Write the script's config half**

`scripts/network_config.py`:

```python
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


def _keys(where: str, value: object, required: list[str], optional: list[str] = ()) -> dict:
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
```

- [ ] **Step 4: Run the checks**

Run: `for d in nogw noprefix dupdev unknown empty missing; do uv run scripts/network_config.py --validate $T/$d; echo "exit $?"; done`
Expected, in order, each with `exit 2`:

```
error: …/nogw/network.yaml.interfaces[0].ipv4.gateway: 10.0.1.1 is outside 10.0.0.0/24
error: …/noprefix/network.yaml.interfaces[0].ipv4.address: '10.0.0.2' needs a prefix, e.g. 10.0.0.2/24
error: …/dupdev/network.yaml: duplicate device eth9
error: …/unknown/network.yaml.interfaces[0].ipv4: unknown key address
error: …/empty/network.yaml: expected a mapping
error: …/missing/network.yaml: not found
```

Run: `uv run scripts/network_config.py --validate december_2026/config/robots/flatbot; echo "exit $?"`
Expected: `december_2026/config/robots/flatbot/network.yaml: ok — 2 interfaces` and `exit 0`.

- [ ] **Step 5: Lint and commit**

```bash
pre-commit run --files scripts/network_config.py december_2026/config/robots/flatbot/network.yaml
git add scripts/network_config.py december_2026/config/robots/flatbot/network.yaml
git commit -m "feat(december_2026): flatbot network.yaml and network_config --validate (#61)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

______________________________________________________________________

### Task 2: `network_config.py` applies with `nmcli`

**Files:**

- Modify: `scripts/network_config.py` (replace the `apply` stub; add the nmcli helpers above it)

**Interfaces:**

- Consumes: `Interface`, `ConfigError`, `main()` from Task 1.
- Produces: `apply(interfaces: list[Interface], dry_run: bool) -> int` (returns the exit status).

Prerequisite for every hardware step from here on: a copy of this branch on snorlax, e.g. `rsync -a --exclude .git ./ snorlax:rammp-deployments-61/` from the repo root, re-run after each change, and the hardware commands run from `~/rammp-deployments-61` over SSH (Tailscale/Wi-Fi, never over `eno1`).

- [ ] **Step 1: Confirm the stub fails**

Run (on snorlax, from the repo checkout): `uv run scripts/network_config.py --dry-run december_2026/config/robots/flatbot`
Expected: `NotImplementedError`.

- [ ] **Step 2: Replace the `apply` stub with the implementation**

Delete the `def apply(...): raise NotImplementedError` stub and insert this block above `main()`:

```python
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
            ["connection.id", "connection.type", "connection.interface-name", "connection.autoconnect"],
            "con", "show", uuid,
        )
        mac = ""
        if g["connection.type"] == "802-3-ethernet":
            mac = _get(["802-3-ethernet.mac-address"], "con", "show", uuid)["802-3-ethernet.mac-address"]
        found.append(
            Profile(uuid, g["connection.id"], g["connection.type"], g["connection.interface-name"],
                    g["connection.autoconnect"], mac)
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


def plan(iface: Interface, existing: list[Profile]) -> tuple[list[list[str]], list[str]]:
    """The nmcli commands that make this entry true, and a note per side effect."""
    want = settings(iface)
    pairs = [x for kv in want.items() for x in kv]
    commands, notes = [], []
    mine = [p for p in existing if p.name == iface.profile]
    if len(mine) > 1:
        raise ConfigError(f"{iface.profile}: {len(mine)} profiles have this name; remove the extras")
    if not mine:
        commands.append(["nmcli", "con", "add", "type", "ethernet", "con-name", iface.profile,
                         "ifname", iface.device, *pairs[2:]])
    else:
        if mine[0].type != "802-3-ethernet":
            raise ConfigError(f"{iface.profile}: existing profile is {mine[0].type}, not ethernet")
        if _get(list(want), "con", "show", mine[0].uuid) != want:
            commands.append(["nmcli", "con", "mod", mine[0].uuid, *pairs])
    mac = _sys(iface.device, "address").lower()
    for p in existing:
        bound = p.interface == iface.device or (p.mac and p.mac.lower() == mac)
        if p.name != iface.profile and bound and p.autoconnect == "yes":
            commands.append(["nmcli", "con", "mod", p.uuid, "connection.autoconnect", "no"])
            notes.append(f"autoconnect off on {p.name!r}")
    return commands, notes


def apply(interfaces: list[Interface], dry_run: bool) -> int:
    # Preflight: everything that can be refused is refused before the first change.
    missing = [i.device for i in interfaces if not Path(f"/sys/class/net/{i.device}").exists()]
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
                subprocess.run(["sudo", *cmd], check=True, capture_output=True, text=True)
            for note in notes:
                print(f"{prefix}: {note}")
            if _sys(iface.device, "carrier") != "1":
                print(f"{prefix}: {'saved' if commands else 'unchanged'}; no link, comes up when cabled")
                continue
            active = _get(["GENERAL.CONNECTION"], "dev", "show", iface.device)["GENERAL.CONNECTION"]
            if commands or active != iface.profile:
                subprocess.run(["sudo", "nmcli", "--wait", "60", "con", "up", iface.profile],
                               check=True, capture_output=True, text=True)
            state = _get(["GENERAL.CONNECTION", "IP4.ADDRESS"], "dev", "show", iface.device)
            status = "applied" if commands or active != iface.profile else "unchanged"
            print(f"{prefix}: {status}; active {state['GENERAL.CONNECTION']!r}, "
                  f"IPv4 {state['IP4.ADDRESS'] or 'none'}")
        except subprocess.CalledProcessError as e:
            print(f"{prefix}: FAILED: {shlex.join(e.cmd)}: {(e.stderr or '').strip()}", file=sys.stderr)
            failed += 1
    return 1 if failed else 0
```

- [ ] **Step 3: Lint**

Run: `pre-commit run --files scripts/network_config.py`
Expected: ruff and ruff-format pass (ruff-format may rewrap lines; re-run until clean).

- [ ] **Step 4: Dry-run on snorlax**

Run (in a checkout of this branch on snorlax): `uv run scripts/network_config.py --dry-run december_2026/config/robots/flatbot; echo "exit $?"`
Expected: `would run: sudo nmcli con add … flatbot-arm ifname enp3s0 …`, the same for `flatbot-sensors` on `eno1`, and `connection.autoconnect no` for the `kinova` UUID and the `Wired connection 1` UUID (`Wired connection 2` is already off). `exit 0`. Nothing changes: `nmcli con show` lists no `flatbot-*` profile.

- [ ] **Step 5: Unknown NIC is refused before any change**

```bash
mkdir -p $T/badnic && cat > $T/badnic/network.yaml <<'EOF'
interfaces:
  - {profile: flatbot-arm, device: enp3s0, ipv4: {method: static, address: 192.168.1.11/24}}
  - {profile: x, device: eth9, ipv4: {method: dhcp}}
EOF
```

- [ ] **Step 6: Run it**

Run: `uv run scripts/network_config.py $T/badnic; echo "exit $?"` (a real run, not dry)
Expected: `error: no such NIC on this host: eth9`, `exit 2`, and `nmcli con show` still lists no `flatbot-arm` — the valid first entry was not applied.

- [ ] **Step 7: Commit**

```bash
git add scripts/network_config.py
git commit -m "feat(december_2026): network_config applies NIC profiles with nmcli (#61)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

______________________________________________________________________

### Task 3: `oak_d.yaml` and `oak_d_config.py --validate`

**Files:**

- Create: `december_2026/config/robots/flatbot/oak_d.yaml`
- Create: `scripts/oak_d_config.py`

**Interfaces:**

- Produces: `load(robot_dir: Path) -> list[Camera]` raising `ConfigError`; `Camera(name: str, device_id: str, method: str, address: str, gateway: str)`; `wanted_network(cam: Camera) -> dict[str, bool | int]` with keys `staticIpv4`, `ipv4`, `ipv4Mask`, `ipv4Gateway` in bootloader encoding; `describe(network: dict) -> str`. CLI: `oak_d_config.py [--validate | --dry-run] ROBOT_DIR`.

- [ ] **Step 1: Write the flatbot config**

`december_2026/config/robots/flatbot/oak_d.yaml`:

```yaml
# OAK-D PoE cameras on flatbot, by device ID (MxID). Keys are the manifest's node names.
# Applied by: uv run scripts/oak_d_config.py december_2026/config/robots/flatbot
#
# dhcp stores no fallback address: on bootloader <=0.0.29 a stored address
# disables DHCP even with staticIpv4 false.
cameras:
  # On the ESP32-P4 sensor network while it is cabled there. Through the arm's
  # wrist Ethernet it is on the arm's link, which has no DHCP server: then it
  # becomes static on 192.168.1.x.
  wrist_camera:
    device_id: "194430107187F42E00"
    ipv4:
      method: dhcp
  scene_camera:
    device_id: "19443010219A974800"
    ipv4:
      method: dhcp
```

- [ ] **Step 2: Write the bad configs that must be rejected**

```bash
mkdir -p $T/oak/{unquoted,dupid,fallback,nogw}
cat > $T/oak/unquoted/oak_d.yaml <<'EOF'
cameras:
  wrist_camera: {device_id: 194430107187, ipv4: {method: dhcp}}
EOF
cat > $T/oak/dupid/oak_d.yaml <<'EOF'
cameras:
  a: {device_id: "194430107187F42E00", ipv4: {method: dhcp}}
  b: {device_id: "194430107187F42E00", ipv4: {method: dhcp}}
EOF
cat > $T/oak/fallback/oak_d.yaml <<'EOF'
cameras:
  a: {device_id: "194430107187F42E00", ipv4: {method: dhcp, address: 192.168.1.200/24}}
EOF
cat > $T/oak/nogw/oak_d.yaml <<'EOF'
cameras:
  a: {device_id: "194430107187F42E00", ipv4: {method: static, address: 192.168.1.200/24, gateway: 10.0.0.1}}
EOF
```

Run: `for d in unquoted dupid fallback nogw; do uv run scripts/oak_d_config.py --validate $T/oak/$d; echo "exit $?"; done`
Expected now: fails, the script does not exist.

- [ ] **Step 3: Write the script's config half**

`scripts/oak_d_config.py`:

```python
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


def _keys(where: str, value: object, required: list[str], optional: list[str] = ()) -> dict:
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
        raise ConfigError(f"{where}.cameras: expected a non-empty mapping of node name to camera")
    cameras = []
    for name, entry in entries.items():
        at = f"{where}.cameras.{name}"
        _keys(at, entry, ["device_id", "ipv4"])
        device_id = entry["device_id"]
        if not isinstance(device_id, str) or not device_id:
            raise ConfigError(f'{at}.device_id: expected a string in quotes, e.g. "194430107187F42E00", '
                              f"got {device_id!r}")
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


def apply(cameras: list[Camera], dry_run: bool) -> int:
    raise NotImplementedError  # Task 4


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the checks**

Run: `for d in unquoted dupid fallback nogw; do uv run scripts/oak_d_config.py --validate $T/oak/$d; echo "exit $?"; done`
Expected, in order, each `exit 2`:

```
error: …/unquoted/oak_d.yaml.cameras.wrist_camera.device_id: expected a string in quotes, e.g. "194430107187F42E00", got 194430107187
error: …/dupid/oak_d.yaml: duplicate device_id 194430107187F42E00
error: …/fallback/oak_d.yaml.cameras.a.ipv4: unknown key address
error: …/nogw/oak_d.yaml.cameras.a.ipv4.gateway: 10.0.0.1 is outside 192.168.1.0/24
```

Run: `uv run scripts/oak_d_config.py --validate december_2026/config/robots/flatbot; echo "exit $?"`
Expected: `…/oak_d.yaml: ok — 2 cameras`, `exit 0`.

- [ ] **Step 5: Check the address encoding against values read off the cameras**

Run:

```bash
uv run --no-project --with pyyaml==6.0.2 python -I -c '
import importlib.util, sys
spec = importlib.util.spec_from_file_location("oak", "scripts/oak_d_config.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
c = m.Camera("w", "X", "static", "192.168.1.200/24", "192.168.1.1")
print(m.wanted_network(c), m.describe(m.wanted_network(c)))
print(m.describe({"staticIpv4": False, "ipv4": 3355551936, "ipv4Mask": 16777215, "ipv4Gateway": 0}))'
```

Expected (values read from the wrist camera on 2026-10-14/15):
`{'staticIpv4': True, 'ipv4': 3355551936, 'ipv4Mask': 16777215, 'ipv4Gateway': 16885952} static 192.168.1.200 mask 255.255.255.0 gw 192.168.1.1`
then `dhcp, fallback 192.168.1.200`.

- [ ] **Step 6: Lint and commit**

```bash
pre-commit run --files scripts/oak_d_config.py december_2026/config/robots/flatbot/oak_d.yaml
git add scripts/oak_d_config.py december_2026/config/robots/flatbot/oak_d.yaml
git commit -m "feat(december_2026): flatbot oak_d.yaml and oak_d_config --validate (#61)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

______________________________________________________________________

### Task 4: `oak_d_config.py` applies with depthai

**Files:**

- Modify: `scripts/oak_d_config.py` (replace the `apply` stub)

**Interfaces:**

- Consumes: `Camera`, `CameraError`, `NETWORK_FIELDS`, `wanted_network`, `describe` from Task 3.

- Produces: `apply(cameras: list[Camera], dry_run: bool) -> int`.

- [ ] **Step 1: Replace the `apply` stub**

```python
def _discover(dai, ids: list[str], attempts: int = 3) -> dict:
    """Discovery is a UDP broadcast and can miss a reply; retry until every ID answers."""
    found = {}
    for _ in range(attempts):
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
        raise CameraError(f"in state {info.state.name} at {info.name}; stop the node using it and retry")
    want = wanted_network(cam)
    with dai.DeviceBootloader(info, allowFlashingBootloader=False) as bl:
        data = bl.readConfigData()
        current = {k: data["network"][k] for k in NETWORK_FIELDS}
        if current == want:
            return f"unchanged at {info.name} ({describe(current)}, bootloader {bl.getVersion()})"
        if dry_run:
            return f"would change {describe(current)} -> {describe(want)} (at {info.name})"
        data["network"].update(want)
        ok, err = bl.flashConfigData(data)
        if not ok:
            raise CameraError(f"config write failed: {err}")
        readback = {k: bl.readConfigData()["network"][k] for k in NETWORK_FIELDS}
        if readback != want:
            raise CameraError(f"read back {describe(readback)}, expected {describe(want)}")
    # The bootloader connection is closed here, so the camera can reset.
    address = _wait_for_restart(dai, cam.device_id)
    if address is None:
        raise CameraError(
            f"config written ({describe(want)}) but the camera did not come back within 60 s; "
            "with dhcp, check the server answers it; with static, check this host has an "
            "address on that subnet"
        )
    if cam.method == "static" and address != str(ipaddress.IPv4Interface(cam.address).ip):
        raise CameraError(f"came back at {address}, expected {cam.address}")
    if cam.method == "dhcp" and address.startswith("169.254."):
        raise CameraError(f"came back at {address}: no DHCP lease, fell back to link-local")
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
        except (CameraError, RuntimeError) as e:  # depthai raises RuntimeError on connect failures
            print(f"{prefix}: FAILED: {e}", file=sys.stderr)
            failed += 1
    return 1 if failed else 0
```

- [ ] **Step 2: Lint**

Run: `pre-commit run --files scripts/oak_d_config.py`
Expected: pass.

- [ ] **Step 3: Dry-run on snorlax**

Run: `uv run scripts/oak_d_config.py --dry-run december_2026/config/robots/flatbot; echo "exit $?"`
Expected: both cameras `unchanged at 192.168.4.x (dhcp, bootloader 0.0.2x)`; `exit 0`. (Both were cleared to plain DHCP on 2026-10-15.)

- [ ] **Step 4: A camera that is not there fails alone**

```bash
mkdir -p $T/oak/ghost && cat > $T/oak/ghost/oak_d.yaml <<'EOF'
cameras:
  scene_camera: {device_id: "19443010219A974800", ipv4: {method: dhcp}}
  ghost: {device_id: "000000000000000000", ipv4: {method: dhcp}}
EOF
uv run scripts/oak_d_config.py --dry-run $T/oak/ghost; echo "exit $?"
```

Expected: `scene_camera … unchanged …`, then `ghost (000000000000000000): FAILED: not found by discovery. …`, `exit 1`.

- [ ] **Step 5: Commit**

```bash
git add scripts/oak_d_config.py
git commit -m "feat(december_2026): oak_d_config writes OAK-D network config by device ID (#61)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

______________________________________________________________________

### Task 5: CI validates every robot config

**Files:**

- Modify: `.github/workflows/validate.yml` (append steps after "Validate every manifest")

- [ ] **Step 1: Append the steps**

```yaml
      # Robot configs are applied by uv scripts on the robot; CI can only prove
      # each one parses and means something. --validate touches nothing else.
      - uses: astral-sh/setup-uv@v6
        with:
          version: "0.12.17"

      - name: Validate every robot config
        run: |
          shopt -s nullglob
          status=0
          for robot in */config/robots/*/; do
            if [ -f "$robot/network.yaml" ]; then
              uv run scripts/network_config.py --validate "$robot" || status=1
            fi
            if [ -f "$robot/oak_d.yaml" ]; then
              uv run scripts/oak_d_config.py --validate "$robot" || status=1
            fi
          done
          exit "$status"
```

- [ ] **Step 2: Lint the workflow and run the step locally**

Run: `pre-commit run --files .github/workflows/validate.yml`
Expected: actionlint passes.

Run the step's script from the repo root with bash.
Expected: two `ok` lines for flatbot, exit 0. Then break `december_2026/config/robots/flatbot/oak_d.yaml` (remove a quote), re-run, see exit 1, and restore it with `git checkout -- december_2026/config/robots/flatbot/oak_d.yaml`.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/validate.yml
git commit -m "ci: validate every robot config with the uv scripts (#61)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

______________________________________________________________________

### Task 6: Hardware check on snorlax and the flatbot README

**Files:**

- Create: `december_2026/config/robots/flatbot/README.md`
- Modify: `CONTRIBUTING.md` (intro paragraph; CI table; new "Robot config" section)

Prerequisite: the branch is pushed, or copied, to a checkout on snorlax. The arm stays unplugged; `eno1` is cabled to the ESP32-P4 network. Run over Tailscale/Wi-Fi, not over `eno1`.

- [ ] **Step 1: Apply the network config**

Run: `uv run scripts/network_config.py december_2026/config/robots/flatbot; echo "exit $?"`
Expected: one sudo prompt. Then `autoconnect off on 'kinova'` and `autoconnect off on 'Wired connection 1'`, `enp3s0 (flatbot-arm): saved; no link, comes up when cabled`, `eno1 (flatbot-sensors): applied; active 'flatbot-sensors', IPv4 192.168.4.x/24`. `exit 0`.

- [ ] **Step 2: Confirm the state with nmcli**

Run: `nmcli -f NAME,DEVICE,AUTOCONNECT con show`
Expected: `flatbot-sensors` active on `eno1`. `flatbot-arm` present with autoconnect yes. `kinova` and `Wired connection 1/2` show autoconnect no.

- [ ] **Step 3: Second run is a no-op**

Run: the same command again.
Expected: `unchanged` for both, no sudo prompt, exit 0.

- [ ] **Step 4: Survives a reboot**

Reboot snorlax (ask first; it is shared). After boot, run `nmcli -g GENERAL.CONNECTION dev show eno1` and `ip -br -4 addr show eno1`.
Expected: `flatbot-sensors`, with a `192.168.4.x` lease.

- [ ] **Step 5: OAK dry-run after the reboot**

Run: `uv run scripts/oak_d_config.py --dry-run december_2026/config/robots/flatbot`
Expected: both `unchanged`, exit 0.

- [ ] **Step 6: Exercise a write on one camera, then restore it**

Copy the robot dir to `$T/flatbot-static` and set `scene_camera` to `{method: static, address: 192.168.4.201/24}`. Run `oak_d_config.py` against it.
Expected: `applied static 192.168.4.201 mask 255.255.255.0; now at 192.168.4.201`.

Then run against the real `december_2026/config/robots/flatbot`.
Expected: `scene_camera … applied dhcp; now at 192.168.4.x`, `wrist_camera … unchanged`. A second run reports both `unchanged`.

- [ ] **Step 7: A camera held by a node**

Start the scene camera's container from the manifest (or any depthai process connected to it). Run the dry-run.
Expected: `scene_camera … FAILED: not found by discovery … held by a running camera node …` (or `in state X_LINK_BOOTED`), `wrist_camera … unchanged`, exit 1. Stop the container afterwards.

- [ ] **Step 8: Write the flatbot README**

`december_2026/config/robots/flatbot/README.md`:

````markdown
# flatbot

The bench robot for the December 2026 demo: a Kinova Gen3 on a 2F-85, two OAK-D PoE
cameras, and a Jetson AGX Orin (`snorlax`).

## Cabling

| Jetson NIC | Goes to                                 | Addressing                                       |
| ---------- | --------------------------------------- | ------------------------------------------------ |
| `enp3s0`   | Kinova Gen3 base, direct                | static `192.168.1.11/24`, no gateway; arm `.10`  |
| `eno1`     | ESP32-P4 sensor network (PoE for OAKs)  | DHCP from `192.168.4.1`                          |

Both OAKs are on the sensor network today. When the wrist camera is cabled through the
arm's wrist Ethernet, it is on the `enp3s0` link instead, which has no DHCP server; set
its entry in `oak_d.yaml` to `static` on `192.168.1.x` first.

## After a reflash

From a checkout of this repo on the Jetson, over Wi-Fi or Tailscale — not over a NIC
being reconfigured:

```bash
uv run scripts/network_config.py december_2026/config/robots/flatbot
uv run scripts/oak_d_config.py   december_2026/config/robots/flatbot
```

Each takes `--dry-run` to show what it would change. Both are safe to re-run.

`network_config` asks for sudo once. It turns off autoconnect on any other profile bound
to the same NICs (a leftover one takes the NIC back at boot) but does not delete them.

`oak_d_config` finds each camera by device ID, so the camera must be reachable: on a
subnet the Jetson has an address on, and not held by a running camera node. A camera
still on an old static address elsewhere needs a temporary address on that subnet
first, e.g. `sudo ip addr add 192.168.1.11/24 dev eno1`.

## Cameras

| Node           | Device ID            | Bootloader |
| -------------- | -------------------- | ---------- |
| `wrist_camera` | `194430107187F42E00` | 0.0.29     |
| `scene_camera` | `19443010219A974800` | 0.0.28     |

depthai offers a bootloader update to 0.0.29 for the scene camera. It is not needed for
DHCP and is not done by these scripts.
````

- [ ] **Step 9: Update CONTRIBUTING.md**

In the intro, replace:

```
This repo follows the RAMMP module workflow, but it is not a module: it holds no
code that runs on the robot. It is the **integration repo** — the sheppy
manifests that say which containers a deployment starts, at which versions, with
which flags. Everything it names is built and published somewhere else.
```

with:

```
This repo follows the RAMMP module workflow, but it is not a module: it builds
nothing that sheppy runs. It is the **integration repo** — the sheppy manifests
that say which containers a deployment starts, at which versions, with which
flags, and the robot configs and scripts that set a robot up before sheppy
starts anything. Everything a manifest names is built and published somewhere
else.
```

In the "What CI gates" table, change the `validate.yml` row's label to
`` `validate.yml` — every manifest through sheppy, every robot config `` (keep the four ✅).

After "Pinning", add:

````markdown
## Robot config

Setup that happens outside any node lives in `<deployment>/config/robots/<robot>/`, one
file per script, applied on the robot:

| File           | Script                       | Sets                                    |
| -------------- | ---------------------------- | --------------------------------------- |
| `network.yaml` | `scripts/network_config.py`  | the host's NICs, through NetworkManager |
| `oak_d.yaml`   | `scripts/oak_d_config.py`    | each OAK-D PoE camera's addressing      |

```bash
uv run scripts/network_config.py december_2026/config/robots/flatbot
```

Node configuration is not here: parameters and their defaults are the manifest's
`params:`, per-robot values are `profiles/<robot>.yaml`, large static config ships in
the module's image. The robot name joins the two: `config/robots/flatbot/` and
`profiles/flatbot.yaml`.
````

- [ ] **Step 10: Lint and commit**

```bash
pre-commit run --files CONTRIBUTING.md december_2026/config/robots/flatbot/README.md
git add CONTRIBUTING.md december_2026/config/robots/flatbot/README.md
git commit -m "docs: flatbot robot README and robot config in CONTRIBUTING (#61)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

______________________________________________________________________

### Task 7: Pattern page on the docs site

**Files (in `~/atdev/rammp-org.github.io`, a separate PR):**

- Create: `website/content/patterns/configuring-a-deployment.mdx`
- Modify: `website/content/patterns/_meta.js`
- Modify: `website/content/patterns/index.mdx`

Branch from `origin/main`. Before naming the branch, check that repo's issues (`gh issue list -R rammp-org/rammp-org.github.io`) for one this closes, and name it `feature/<issue>-configuring-a-deployment`. If there is none, ask Swapnil whether to open one before creating the branch. PR #10 touches the same `_meta.js` and `index.mdx`; whichever merges second rebases.

- [ ] **Step 1: Write the page**

`website/content/patterns/configuring-a-deployment.mdx`:

````mdx
---
title: Configuring a deployment
---

# Configuring a deployment

A deployment has settings at several levels: which node runs, what its parameters
are, what changes from one robot to the next, and setup that has to happen before
any node starts, like which NIC the arm is on. When each of these can live
anywhere, the same value ends up in three places, and the one that wins is the one
nobody looked at.

This page is where each kind of setting goes.

## The convention

| Setting                                          | Lives in                                         |
| ------------------------------------------------ | ------------------------------------------------ |
| Which alternative of a node runs                 | the manifest's `alternatives`, a profile's `selections` |
| A node parameter and its default                 | the manifest's `params:`                         |
| A value that differs per robot or per run        | `profiles/<robot>.yaml` `overrides`, or `p` in the TUI |
| Large, static module configuration               | the module's image                               |
| State that survives a container restart          | `~/.config/<module>`, mounted at `/config` ([Storing state across runs](/patterns/storing-state-across-runs)) |
| Setup of the robot outside any node              | `config/robots/<robot>/` in the deployment repo, applied by a script |

**Parameters go through sheppy.** A value goes in `params:` if anyone would change it
for a robot or from the TUI. sheppy only overrides what `params:` lists, so a value
buried in `command:` cannot be changed per robot.

**The robot name joins the two per-robot places.** `config/robots/flatbot/` and
`profiles/flatbot.yaml` describe the same robot.

**Robot setup is applied, not launched.** It is the part sheppy cannot do: NetworkManager
profiles, a camera's stored network config. Each file has one script that applies it,
validates the whole file before changing anything, is safe to re-run, and has a
`--dry-run`.

## A deployment that does this

rammp-deployments' `december_2026/config/robots/flatbot/` holds `network.yaml`, the
Jetson's two NICs (the arm on a static link, the sensor network on DHCP), and
`oak_d.yaml`, each OAK-D PoE camera's addressing keyed by its manifest node name and
found by device ID. After a reflash:

```bash
uv run scripts/network_config.py december_2026/config/robots/flatbot
uv run scripts/oak_d_config.py   december_2026/config/robots/flatbot
```

CI runs both with `--validate` on every robot, so a broken robot config fails the PR
the way a broken manifest does.

## What does not belong here

- **Node parameters in `config/robots/`.** They are sheppy's: `params:` and profiles.
- **A separate directory of node config files.** It competes with profiles and cannot
  be overridden from the TUI. Large static config ships in the module's image.
- **State.** A calibration the operator performs is
  [state](/patterns/storing-state-across-runs), not config.
- **Secrets.**

## Related

- **[Profiles](/sheppy/profiles)** — per-robot selections and overrides.
- **[Storing state across runs](/patterns/storing-state-across-runs)** — the per-chair
  state convention.
- **[OAK-D Pro PoE](/oak-d-pro-poe)** — the camera, and why its network config is
  stored on the device.
````

- [ ] **Step 2: Register it**

`_meta.js`: add `'configuring-a-deployment': 'Configuring a deployment'` after `'storing-state-across-runs'`. `index.mdx`: add under Pages:

```markdown
- **[Configuring a deployment](/patterns/configuring-a-deployment)** — where each kind
  of setting goes: the manifest, a profile, the module's image, per-chair state, or the
  robot's own config.
```

- [ ] **Step 3: Build and check links**

Run (in `website/`): `npm ci && CI=1 npm run build && npm run check:links && npm test`
Expected: build succeeds, links ok, tests pass. If `/sheppy/profiles` is not a valid route, use the route the build reports for sheppy's profiles page.

- [ ] **Step 4: Commit**

```bash
git add website/content/patterns/
git commit -m "docs: Configuring a deployment pattern

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
