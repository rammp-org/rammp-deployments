# Tilted arm mount (rammp-deployments) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Each robot declares its arm mount in `config/robots/<robot>/mount.yaml`, a host picks its robot with `scripts/set_robot.py` (a git-ignored `config/robots/current` symlink), and the manifest hands that file to `arm_state_publisher`, which becomes the only renderer of the arm's model.

**Architecture:** One more self-contained PEP 723 `uv` script beside `network_config.py` and `oak_d_config.py`, with the same `--validate`/`--dry-run` modes, one line of output per item and the same exit codes. It validates `mount.yaml` and replaces the `current` link with an atomic rename. CI validates every `mount.yaml` in the existing robot-config loop. The manifest bind-mounts `current` read-only into `arm_state_publisher` and passes `mount_file:=`; the driver stops taking `--urdf`. The image-tag bump is the last task, gated on the upstream releases.

**Tech Stack:** Python 3.10, uv ≥0.6 with inline script metadata, PyYAML 6.0.2, sheppy (`main`) inline `container:` blocks, GitHub Actions with `astral-sh/setup-uv@v6`, pre-commit (ruff v0.3.0, mdformat 0.7.17, actionlint v1.7.12).

**Spec:** rammp-deployments: `docs/superpowers/specs/2026-10-15-tilted-arm-mount-design.md`

## Global Constraints

- Branch: `feature/tilted-arm-mount`, created from `docs/tilted-arm-mount` (which carries the spec and this plan on top of `feature/61-robot-config`, whose scripts and CI loop this builds on). Squash-merge to `dev` after #61 (CONTRIBUTING.md). Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- The script is self-contained: PEP 723 header, `requires-python = ">=3.10"`, `dependencies = ["pyyaml==6.0.2"]`, no shared module, no `pyproject.toml`, no pytest. Checks are shell runs against temp directories, as in #61.
- Exit codes, as the #61 scripts: `0` ok, `1` nothing to report (no active robot, when asked which one is), `2` the config or a preflight check is invalid and nothing was changed.
- `mount.yaml` has exactly two keys: `robot` (must equal the robot directory's name) and `rpy_deg` (three finite numbers). Unknown keys are errors. No translation is modelled.
- Values, verbatim from the spec: dojo `[0.0, 0.0, 0.0]`; flatbot `[0.0, 30.0, 0.0]`; gen_1_5 `[0.0, 30.0, 0.0]`.
- `current` is a relative symlink (target is the bare robot name), replaced by an atomic rename, and git-ignored. A `current` that is a real directory is refused with a message saying how to remove it; the script never deletes it.
- Manifest: `./config/robots/current:/config/robot:ro` on `arm_state_publisher` (sheppy anchors `./` paths in inline blocks to the manifest's directory, so this is `december_2026/config/robots/current`), `mount_file:=/config/robot/mount.yaml` on its launch command, no `--urdf` on the driver.
- Image tags are release tags, never `:main`/`:latest`/`:nightly` (CONTRIBUTING.md "Pinning").
- Verification is offline: no arm is powered. The only host changes on abra or snorlax are in the labelled **Rollout (human)** step at the end of Task 4. Everything else runs locally, in temp directories, or in `/tmp` on abra with nothing left behind.
- `pre-commit run --files <changed files>` passes on every commit (re-run once if mdformat or ruff-format rewrites a file).
- Out of scope: sheppy changes, the IMU, cuRobo itself, per-robot worlds, camera spheres, moving any other per-host path onto `current`.

## Review Focus

- **`current` is a real directory**, because sheppy started `arm_state_publisher` before `set_robot` ever ran and Docker created it as root: `set_robot` refuses (exit 2) with the exact `sudo rmdir` command, and touches nothing. Pinned in Task 1, Step 6.
- **The path given is `config/robots/current` itself**, which also happens whenever the CI loop runs on a machine with a robot set (the glob `*/config/robots/*/` matches the link): `--validate` checks the target robot's file and passes, and setting it reports `unchanged`. Pinned in Task 1, Step 5 and Task 2, Step 2.
- **`current` dangles** (its robot directory was renamed or removed): asking which robot is active is an error naming the target, and setting a robot replaces the dangling link. Pinned in Task 1, Step 6.
- **A `mount.yaml` copied from another robot without changing `robot:`** (gen_1_5 starts life as a copy of flatbot's): rejected with both names. Pinned in Task 1, Step 2.
- **A rotation that YAML loads as something other than three numbers** — `.nan`, `true`, `"30"`, two elements — is rejected rather than rendered into the model. Pinned in Task 1, Step 2.

______________________________________________________________________

### Task 1: `mount.yaml` for every robot and `set_robot.py`

**Files:**

- Create: `december_2026/config/robots/dojo/mount.yaml`
- Create: `december_2026/config/robots/flatbot/mount.yaml`
- Create: `december_2026/config/robots/gen_1_5/mount.yaml`
- Create: `scripts/set_robot.py`
- Create: `.gitignore`

dojo/ and gen_1_5/ get only `mount.yaml`. Nothing requires a README or the other files: the CI loop checks each file only if it exists (Task 2), and a robot directory with only a mount is a complete robot config.

**Interfaces:**

- Produces: `load(robot_dir: Path) -> Mount` raising `ConfigError`; `Mount(robot: str, rpy_deg: tuple[float, float, float])`. CLI: `set_robot.py [--validate | --dry-run] <robots dir>/<robot>` and `set_robot.py <robots dir>`. Output lines Tasks 2 and 4 rely on: `<path>/mount.yaml: ok — <robot> (mount rpy_deg [r, p, y])`, `current: <old> → <new>, mount rpy_deg [...]`, `current: unchanged, <robot> (...)`, `<robots dir>/current -> <robot> (...)`.

- [ ] **Step 1: Write the bad configs that must be rejected (the failing checks)**

Run from the repo root:

```bash
SR=$PWD/scripts/set_robot.py
T=$(mktemp -d); cd $T
mkdir -p config/robots/{dojo,flatbot} elsewhere/x \
  bad/robots/{wrongname,nan,short,boolish,string,extra,empty,missing}
printf 'robot: dojo\nrpy_deg: [0.0, 0.0, 0.0]\n'      > config/robots/dojo/mount.yaml
printf 'robot: flatbot\nrpy_deg: [0.0, 30.0, 0.0]\n'  > config/robots/flatbot/mount.yaml
printf 'robot: x\nrpy_deg: [0.0, 0.0, 0.0]\n'         > elsewhere/x/mount.yaml
printf 'robot: flatbot\nrpy_deg: [0, 30, 0]\n'        > bad/robots/wrongname/mount.yaml
printf 'robot: nan\nrpy_deg: [0, .nan, 0]\n'          > bad/robots/nan/mount.yaml
printf 'robot: short\nrpy_deg: [0, 30]\n'             > bad/robots/short/mount.yaml
printf 'robot: boolish\nrpy_deg: [0, true, 0]\n'      > bad/robots/boolish/mount.yaml
printf 'robot: string\nrpy_deg: [0, "30", 0]\n'       > bad/robots/string/mount.yaml
printf 'robot: extra\nrpy_deg: [0, 30, 0]\nxyz: [0, 0, 0.1]\n' > bad/robots/extra/mount.yaml
: > bad/robots/empty/mount.yaml
for d in wrongname nan short boolish string extra empty missing; do
  uv run -q $SR --validate bad/robots/$d; echo "exit $?"
done
```

Expected now: every run fails because `scripts/set_robot.py` does not exist. Stay in `$T` for Steps 2–6; `$SR` is the script's absolute path.

- [ ] **Step 2: Write the script**

`scripts/set_robot.py`:

```python
#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pyyaml==6.0.2"]
# ///
"""Make a robot this host's active robot, or show which one is.

    uv run scripts/set_robot.py [--validate | --dry-run] <robots dir>/<robot>
    uv run scripts/set_robot.py <robots dir>

The active robot is the symlink <robots dir>/current -> <robot>. It is
git-ignored: the repo describes every robot, and which one a host is belongs to
the host. Nodes read embodiment facts (the arm mount) through it, bind-mounted
read-only, so a container started before this has run sees an empty directory.

Given a robot directory, the script validates its mount.yaml, then points
current at it with an atomic rename and prints the change. Given the robots
directory itself, it prints the active robot.

A current that is a real directory is refused, never removed: Docker creates
one, as root, when a container starts with the link missing.

Exit status: 0 ok, 1 no active robot (when asked which one is), 2 the config
or a preflight check is invalid and nothing was changed.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

CONFIG_NAME = "mount.yaml"
ROBOTS_DIR_NAME = "robots"
LINK_NAME = "current"


class ConfigError(Exception):
    """The file, or the link it would replace, is not usable. Nothing changed."""


@dataclass(frozen=True)
class Mount:
    robot: str
    rpy_deg: tuple[float, float, float]


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


def parse(data: object, where: str, dir_name: str) -> Mount:
    mount = _keys(where, data, ["robot", "rpy_deg"])
    robot = _string(f"{where}.robot", mount["robot"])
    if robot != dir_name:
        raise ConfigError(
            f"{where}.robot: {robot!r} does not match the directory name {dir_name!r}"
        )
    rpy = mount["rpy_deg"]
    # bool is an int to Python; `[0, true, 0]` is a typo, not a rotation.
    if (
        not isinstance(rpy, list)
        or len(rpy) != 3
        or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in rpy)
        or not all(math.isfinite(v) for v in rpy)
    ):
        raise ConfigError(
            f"{where}.rpy_deg: expected three finite numbers in degrees, got {rpy!r}"
        )
    return Mount(robot, tuple(float(v) for v in rpy))


def load(robot_dir: Path) -> Mount:
    path = robot_dir / CONFIG_NAME
    if not path.is_file():
        raise ConfigError(f"{path}: not found")
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise ConfigError(f"{path}: {e}") from e
    # Resolved, so `<robots dir>/current` validates as the robot it points at.
    return parse(data, str(path), robot_dir.resolve().name)


def describe(mount: Mount) -> str:
    return f"{mount.robot} (mount rpy_deg {list(mount.rpy_deg)})"


def active(robots_dir: Path) -> str | None:
    """The robot current points at, or None if there is no link."""
    link = robots_dir / LINK_NAME
    if link.is_symlink():
        return os.readlink(link)
    if link.exists():
        raise ConfigError(
            f"{link} is a {'directory' if link.is_dir() else 'file'}, not a link. "
            "Docker creates an empty directory there when a container starts before "
            f"set_robot has run. Remove it with `sudo rmdir {link}` (rmdir refuses "
            "if anything is inside), then run set_robot again."
        )
    return None


def show(robots_dir: Path) -> int:
    try:
        robot = active(robots_dir)
        if robot is None:
            print(
                f"{robots_dir / LINK_NAME}: no active robot; set one with "
                f"`uv run scripts/set_robot.py {robots_dir / '<robot>'}`",
                file=sys.stderr,
            )
            return 1
        if not (robots_dir / LINK_NAME).exists():
            raise ConfigError(
                f"{robots_dir / LINK_NAME} -> {robot}, which does not exist"
            )
        mount = load(robots_dir / LINK_NAME)
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"{robots_dir / LINK_NAME} -> {describe(mount)}")
    return 0


def set_active(robot_dir: Path, mount: Mount, dry_run: bool) -> int:
    # Relative to the cwd, so messages name the path the way the caller typed it.
    robots_dir = Path(os.path.relpath(robot_dir.resolve().parent))
    if robots_dir.resolve().name != ROBOTS_DIR_NAME:
        print(
            f"error: {robot_dir} is not in a {ROBOTS_DIR_NAME}/ directory",
            file=sys.stderr,
        )
        return 2
    link = robots_dir / LINK_NAME
    try:
        old = active(robots_dir)
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    new = mount.robot  # a relative target, so the link survives moving the checkout
    if old == new:
        print(f"{LINK_NAME}: unchanged, {describe(mount)}")
        return 0
    change = f"{old or '(none)'} → {new}"
    if dry_run:
        print(f"{LINK_NAME}: would change {change}")
        return 0
    # Build the new link beside the old one and rename over it: rename is atomic,
    # so a reader never finds current missing or half-made.
    tmp = robots_dir / f".{LINK_NAME}.{os.getpid()}"
    os.symlink(new, tmp)
    try:
        os.replace(tmp, link)
    except OSError:
        tmp.unlink()
        raise
    print(f"{LINK_NAME}: {change}, mount rpy_deg {list(mount.rpy_deg)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--validate", action="store_true", help="check the file and exit")
    mode.add_argument("--dry-run", action="store_true", help="print changes, make none")
    parser.add_argument("path", type=Path, help="a robot directory, or the robots one")
    args = parser.parse_args(argv)

    if args.path.resolve().name == ROBOTS_DIR_NAME:
        if args.validate or args.dry_run:
            print(
                "error: --validate and --dry-run take a robot directory, "
                f"e.g. {args.path / '<robot>'}",
                file=sys.stderr,
            )
            return 2
        return show(args.path)

    try:
        mount = load(args.path)
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if args.validate:
        print(f"{args.path / CONFIG_NAME}: ok — {describe(mount)}")
        return 0
    return set_active(args.path, mount, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
```

Run the Step 1 loop again.
Expected, in order, each followed by `exit 2`:

```
error: bad/robots/wrongname/mount.yaml.robot: 'flatbot' does not match the directory name 'wrongname'
error: bad/robots/nan/mount.yaml.rpy_deg: expected three finite numbers in degrees, got [0, nan, 0]
error: bad/robots/short/mount.yaml.rpy_deg: expected three finite numbers in degrees, got [0, 30]
error: bad/robots/boolish/mount.yaml.rpy_deg: expected three finite numbers in degrees, got [0, True, 0]
error: bad/robots/string/mount.yaml.rpy_deg: expected three finite numbers in degrees, got [0, '30', 0]
error: bad/robots/extra/mount.yaml: unknown key xyz
error: bad/robots/empty/mount.yaml: expected a mapping
error: bad/robots/missing/mount.yaml: not found
```

- [ ] **Step 3: No active robot, dry run, first set (real link, in `$T` only)**

```bash
r() { uv run -q $SR "$@"; echo "exit $?"; }
r config/robots
r --dry-run config/robots/flatbot; ls config/robots
r config/robots/flatbot; readlink config/robots/current
```

Expected:

```
config/robots/current: no active robot; set one with `uv run scripts/set_robot.py config/robots/<robot>`
exit 1
current: would change (none) → flatbot
exit 0
dojo
flatbot
current: (none) → flatbot, mount rpy_deg [0.0, 30.0, 0.0]
exit 0
flatbot
```

`ls` after the dry run shows no `current`; `readlink` after the real run prints the bare name `flatbot` (relative).

- [ ] **Step 4: Idempotent, switch, show, and the modes that are refused**

```bash
r config/robots/flatbot
r config/robots/dojo
r config/robots
r --validate config/robots
r elsewhere/x
ls -a config/robots
```

Expected:

```
current: unchanged, flatbot (mount rpy_deg [0.0, 30.0, 0.0])
exit 0
current: flatbot → dojo, mount rpy_deg [0.0, 0.0, 0.0]
exit 0
config/robots/current -> dojo (mount rpy_deg [0.0, 0.0, 0.0])
exit 0
error: --validate and --dry-run take a robot directory, e.g. config/robots/<robot>
exit 2
error: elsewhere/x is not in a robots/ directory
exit 2
```

and `ls -a` lists exactly `. .. current dojo flatbot`: no `.current.<pid>` temp link is left behind.

- [ ] **Step 5: `current` given as the robot directory**

```bash
r config/robots/dojo
r --validate config/robots/current
r config/robots/current
```

Expected: `current: unchanged, …` (already dojo), then `config/robots/current/mount.yaml: ok — dojo (mount rpy_deg [0.0, 0.0, 0.0])`, then `current: unchanged, dojo (mount rpy_deg [0.0, 0.0, 0.0])`, all `exit 0`.

- [ ] **Step 6: A dangling link and a real directory**

```bash
rm config/robots/current; ln -s gone config/robots/current
r config/robots
r config/robots/flatbot
rm config/robots/current; mkdir config/robots/current
r config/robots/flatbot
r config/robots
ls -ld config/robots/current
rmdir config/robots/current; r config/robots/flatbot
```

Expected:

```
error: config/robots/current -> gone, which does not exist
exit 2
current: gone → flatbot, mount rpy_deg [0.0, 30.0, 0.0]
exit 0
error: config/robots/current is a directory, not a link. Docker creates an empty directory there when a container starts before set_robot has run. Remove it with `sudo rmdir config/robots/current` (rmdir refuses if anything is inside), then run set_robot again.
exit 2
error: config/robots/current is a directory, not a link. … (same message)
exit 2
drwxr-xr-x … config/robots/current
current: (none) → flatbot, mount rpy_deg [0.0, 30.0, 0.0]
exit 0
```

The `ls -ld` line proves the refused directory was left alone.

- [ ] **Step 7: Write the three robot configs and the ignore rule**

`cd` back to the repo root.

`december_2026/config/robots/dojo/mount.yaml`:

```yaml
# The arm's mount on dojo (abra): level on the bench.
# Activated by: uv run scripts/set_robot.py december_2026/config/robots/dojo
robot: dojo # must equal the directory name
# Orientation of arm_base_link in arm_mount. arm_mount is gravity-aligned (on level
# ground) and shares arm_base_link's origin, so there is no translation.
rpy_deg: [0.0, 0.0, 0.0] # level: arm_mount and arm_base_link coincide
```

`december_2026/config/robots/flatbot/mount.yaml`:

```yaml
# The arm's mount on flatbot (snorlax).
# Activated by: uv run scripts/set_robot.py december_2026/config/robots/flatbot
robot: flatbot # must equal the directory name
# Orientation of arm_base_link in arm_mount. arm_mount is gravity-aligned (on level
# ground) and shares arm_base_link's origin, so there is no translation.
rpy_deg: [0.0, 30.0, 0.0] # new_adapter_plate_v3: 30° forward lean
```

`december_2026/config/robots/gen_1_5/mount.yaml`:

```yaml
# The arm's mount on gen_1_5 (no host yet).
# Activated by: uv run scripts/set_robot.py december_2026/config/robots/gen_1_5
robot: gen_1_5 # must equal the directory name
# Orientation of arm_base_link in arm_mount. arm_mount is gravity-aligned (on level
# ground) and shares arm_base_link's origin, so there is no translation.
rpy_deg: [0.0, 30.0, 0.0] # new_adapter_plate_v3: 30° forward lean
```

`.gitignore` (new; the repo has none):

```
# The active robot is the host's choice, not the repo's: scripts/set_robot.py
# makes this link on each host.
/december_2026/config/robots/current
```

- [ ] **Step 8: Validate the real configs and check the ignore rule**

```bash
for r in dojo flatbot gen_1_5; do
  uv run -q scripts/set_robot.py --validate december_2026/config/robots/$r; echo "exit $?"
done
C=$(mktemp -d); rsync -a --exclude .git ./ $C/; git -C $C init -q
uv run -q scripts/set_robot.py $C/december_2026/config/robots/flatbot
git -C $C status --short | grep current; git -C $C check-ignore -v december_2026/config/robots/current
rm -rf $C
```

Expected: three `ok` lines (`dojo (mount rpy_deg [0.0, 0.0, 0.0])`, `flatbot (mount rpy_deg [0.0, 30.0, 0.0])`, `gen_1_5 (mount rpy_deg [0.0, 30.0, 0.0])`), each `exit 0`. Then `current: (none) → flatbot, …`, no `current` line from `git status`, and `.gitignore:3:/december_2026/config/robots/current	december_2026/config/robots/current`. The link is made in the throwaway copy `$C`, never in this checkout.

- [ ] **Step 9: Lint and commit**

```bash
pre-commit run --files .gitignore scripts/set_robot.py december_2026/config/robots/*/mount.yaml
git add .gitignore scripts/set_robot.py december_2026/config/robots/*/mount.yaml
git commit -m "feat(december_2026): mount.yaml per robot and set_robot.py

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Expected: all hooks pass (ruff, ruff-format, check-yaml).

______________________________________________________________________

### Task 2: CI validates `mount.yaml`; the docs name it

**Files:**

- Modify: `.github/workflows/validate.yml` (the "Validate every robot config" loop)
- Modify: `docs/superpowers/specs/2026-10-15-robot-config-design.md` ("Where each kind of setting lives" table)
- Modify: `CONTRIBUTING.md` ("Robot config" table)
- Modify: `december_2026/config/robots/flatbot/README.md` ("After a reflash")

**Interfaces:**

- Consumes: `scripts/set_robot.py --validate <robot dir>` from Task 1 (exit 0 with an `ok` line, exit 2 with an `error:` line).

- [ ] **Step 1: Add `mount.yaml` to the CI loop**

In `.github/workflows/validate.yml`, replace:

```yaml
            if [ -f "$robot/oak_d.yaml" ]; then
              uv run scripts/oak_d_config.py --validate "$robot" || status=1
            fi
```

with:

```yaml
            if [ -f "$robot/oak_d.yaml" ]; then
              uv run scripts/oak_d_config.py --validate "$robot" || status=1
            fi
            if [ -f "$robot/mount.yaml" ]; then
              uv run scripts/set_robot.py --validate "$robot" || status=1
            fi
```

- [ ] **Step 2: Run the loop locally, then break a file**

Run the step's script from the repo root:

```bash
bash -c 'shopt -s nullglob; status=0
for robot in */config/robots/*/; do
  if [ -f "$robot/network.yaml" ]; then uv run scripts/network_config.py --validate "$robot" || status=1; fi
  if [ -f "$robot/oak_d.yaml" ]; then uv run scripts/oak_d_config.py --validate "$robot" || status=1; fi
  if [ -f "$robot/mount.yaml" ]; then uv run scripts/set_robot.py --validate "$robot" || status=1; fi
done; exit "$status"'; echo "exit $?"
```

Expected: `ok` lines for dojo, flatbot (network, oak_d, mount) and gen_1_5, `exit 0`. If this checkout has a `current` link, it is validated too and passes as the robot it points at.

Then set `robot: dojo` in `december_2026/config/robots/flatbot/mount.yaml`, re-run: `error: december_2026/config/robots/flatbot/mount.yaml.robot: 'dojo' does not match the directory name 'flatbot'` and `exit 1`. Restore with `git checkout -- december_2026/config/robots/flatbot/mount.yaml`.

- [ ] **Step 3: Robot-config spec row**

In `docs/superpowers/specs/2026-10-15-robot-config-design.md`, after the table row that starts `| Setup of the robot outside any node`, add:

```markdown
| Embodiment facts that nodes read (the arm mount) | `config/robots/<robot>/`, mounted read-only via `current` | this repo |
```

mdformat re-pads the whole table on commit; that is expected.

- [ ] **Step 4: CONTRIBUTING row**

In `CONTRIBUTING.md` "Robot config", after the `oak_d.yaml` row, add:

```markdown
| `mount.yaml`   | `scripts/set_robot.py`      | the arm's mount rotation; makes this the host's active robot (`config/robots/current`) |
```

- [ ] **Step 5: flatbot README, "After a reflash"**

In `december_2026/config/robots/flatbot/README.md`, replace:

````markdown
```bash
uv run scripts/network_config.py december_2026/config/robots/flatbot
uv run scripts/oak_d_config.py   december_2026/config/robots/flatbot
```

Each takes `--dry-run` to show what it would change. Both are safe to re-run.
````

with:

````markdown
```bash
uv run scripts/network_config.py december_2026/config/robots/flatbot
uv run scripts/oak_d_config.py   december_2026/config/robots/flatbot
uv run scripts/set_robot.py      december_2026/config/robots/flatbot
```

Each takes `--dry-run` to show what it would change. All three are safe to re-run.

`set_robot` makes flatbot this host's active robot: `config/robots/current` points at
it, and the arm's nodes read its `mount.yaml` through that link. Run it before starting
sheppy; a start without it leaves an empty `current` directory behind, which
`set_robot` refuses and tells you how to remove.
````

- [ ] **Step 6: Lint and commit**

```bash
F=".github/workflows/validate.yml docs/superpowers/specs/2026-10-15-robot-config-design.md CONTRIBUTING.md december_2026/config/robots/flatbot/README.md"
pre-commit run --files $F || pre-commit run --files $F
git add $F
git commit -m "ci, docs: validate mount.yaml; set_robot in the robot-config docs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Expected: the first run may report `mdformat … files were modified` (table padding); the second passes, actionlint included.

______________________________________________________________________

### Task 3: Manifest wiring for the rendered model

**Do not merge this task without Task 4.** The commands below are for the new `kinova-gen3-ros2` images: on the tags pinned today, the driver has no model without `--urdf` and `description.launch.py` ignores `mount_file`.

**Files:**

- Modify: `december_2026/sheppy-manifest.yaml` (nodes `arm`, `arm_state_publisher`, `planner`)

**Interfaces:**

- Consumes: `december_2026/config/robots/current` from Task 1. From the kinova-gen3-ros2 tilted-mount change: `description.launch.py` takes a required `mount_file` and publishes the model latched on `/arm/robot_description`; `kinova_gen3_node` has no `--urdf` and reads that topic at startup. From the RAMMP-CuRobo change: the planner reads the same topic.

- [ ] **Step 1: Baseline**

Run: `uv run -q --no-project --with "sheppy @ git+https://github.com/rammp-org/sheppy@main" python scripts/validate_manifest.py; echo "exit $?"`
Expected: `  december_2026/sheppy-manifest.yaml: ok — 7 nodes, 16 alternatives`, `exit 0`.

- [ ] **Step 2: `arm`: the selected-together comment, no `--urdf`, the model topic**

Replace:

```yaml
      # The module is TWO containers — this one and the `planner` node below.
      # Three of its four action servers (go_to_ee_pose, go_to_joint_config,
      # go_to_preset) accept goals that can NEVER succeed without the planner,
      # and sheppy ignores depends_on, so the pairing lives here.
```

with:

```yaml
      # SELECT arm, arm_state_publisher AND planner TOGETHER. The driver and the
      # planner both read the arm's model from /arm/robot_description, which
      # only arm_state_publisher publishes; without it neither starts (each
      # times out with an error naming arm_state_publisher). Three of the
      # driver's four action servers (go_to_ee_pose, go_to_joint_config,
      # go_to_preset) accept goals that can NEVER succeed without the planner.
      # sheppy ignores depends_on, so the pairing lives here.
```

Replace:

```yaml
            - --ip
            - 192.168.1.10
            - --urdf
            - /module_ws/src/kinova-gen3-driver/models/gen3_7dof_2f85.urdf
```

with:

```yaml
            - --ip
            - 192.168.1.10
```

Replace:

```yaml
          - /diagnostics
        subscribes:
          - /estop
```

with:

```yaml
          - /diagnostics
        subscribes:
          - /arm/robot_description
          - /estop
```

- [ ] **Step 3: `arm_state_publisher`: mount, launch argument, topic name**

Replace:

```yaml
      # description.launch.py defaults to the articulated 13-DOF model; the
      # driver publishes robotiq_85_left_knuckle_joint and robot_state_publisher
      # derives the five mimic joints. Scoped to the arm on purpose — a mobile
      # base brings its own state publisher, and a static mount transform
      # stitches the trees.
```

with:

```yaml
      # description.launch.py renders the articulated 13-DOF model at launch,
      # rooted at arm_mount and rotated by the active robot's mount.yaml
      # (config/robots/current, set by scripts/set_robot.py), and publishes it
      # latched on /arm/robot_description; the driver and the planner read it
      # from there. The driver publishes arm_robotiq_85_left_knuckle_joint and
      # robot_state_publisher derives the five mimic joints. Scoped to the arm
      # on purpose — a mobile base brings its own state publisher, and a static
      # mount transform stitches the trees.
```

Replace:

```yaml
          user: "1000:1000"
          command: ros2 launch kinova_gen3_description description.launch.py
        publishes:
          - /robot_description
```

with:

```yaml
          user: "1000:1000"
          # The active robot's config, relative to this manifest. A missing
          # `current` link makes Docker create an empty root-owned directory
          # here and the launch fails on the missing mount.yaml; run
          # scripts/set_robot.py, which tells you how to remove it.
          volumes:
            - ./config/robots/current:/config/robot:ro
          command: ros2 launch kinova_gen3_description description.launch.py mount_file:=/config/robot/mount.yaml
        publishes:
          - /arm/robot_description
```

- [ ] **Step 4: `planner`: the model topic**

Replace:

```yaml
        # No topics: this node is reached over the /rammp_curobo/plan_to_pose
        # and /rammp_curobo/plan_to_joints ACTIONS, which the schema has no way
        # to declare. Nothing here is starved; there is just nothing to list.
        publishes: []
        subscribes: []
```

with:

```yaml
        # Otherwise reached over the /rammp_curobo/plan_to_pose and
        # /rammp_curobo/plan_to_joints ACTIONS, which the schema has no way to
        # declare. The one topic is the arm's model, read once at startup.
        publishes: []
        subscribes:
          - /arm/robot_description
```

- [ ] **Step 5: The manifest still validates**

Run: `uv run -q --no-project --with "sheppy @ git+https://github.com/rammp-org/sheppy@main" python scripts/validate_manifest.py; echo "exit $?"`
Expected: `  december_2026/sheppy-manifest.yaml: ok — 7 nodes, 16 alternatives`, `exit 0`.

Run: `grep -n -- '--urdf\|gen3_7dof_2f85\|- /robot_description' december_2026/sheppy-manifest.yaml; echo "exit $?"`
Expected: no lines, `exit 1`.

- [ ] **Step 6: The bind mount follows the link and is readable as uid 1000 (abra, offline)**

This proves the volume line on a real Docker, with today's image (the container only `cat`s the file). Nothing on abra outside `/tmp` changes.

```bash
rsync -a --exclude .git ./ abra:/tmp/tilted-mount-check/
ssh abra 'cd /tmp/tilted-mount-check &&
  uv run -q scripts/set_robot.py december_2026/config/robots/flatbot &&
  docker run --rm -u 1000:1000 --entrypoint cat \
    -v $PWD/december_2026/config/robots/current:/config/robot:ro \
    ghcr.io/rammp-org/kinova-gen3-ros2:1.1.0 /config/robot/mount.yaml;
  rm -rf /tmp/tilted-mount-check'
```

Expected: `current: (none) → flatbot, …`, then the flatbot `mount.yaml` contents ending `rpy_deg: [0.0, 30.0, 0.0] # new_adapter_plate_v3: 30° forward lean`.

- [ ] **Step 7: Lint and commit**

```bash
pre-commit run --files december_2026/sheppy-manifest.yaml
git add december_2026/sheppy-manifest.yaml
git commit -m "feat(december_2026): arm_state_publisher renders the arm from the active robot's mount

The driver drops --urdf; driver and planner read /arm/robot_description.
Needs the image bump in the next commit before it can run.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

______________________________________________________________________

### Task 4: Bump the `kinova-gen3-ros2` and `rammp-curobo` image tags (gated)

**Gate — do not start until all of these hold:**

1. The kinova-gen3-ros2 tilted-mount plan has produced a release (`gh release list -R rammp-org/kinova-gen3-ros2` shows it) and its image is published.
1. The RAMMP-CuRobo release that reads `/arm/robot_description` and plans in `arm_mount` is published.
1. For flatbot: the per-robot cuRobo world PR is merged, or merges with this one. The spec ("Ordering on flatbot") says cuRobo in `arm_mount` deploys on flatbot only together with it, and this manifest's single `planner` image serves every robot.

If any is missing, stop and report which; leave the branch at Task 3.

**Files:**

- Modify: `december_2026/sheppy-manifest.yaml` (the two `kinova-gen3-ros2` image lines and the `rammp-curobo` image line)

- [ ] **Step 1: Read the tags and check the images exist**

Set `ROS2_TAG` to the release tag produced by the kinova-gen3-ros2 tilted-mount plan, and `CUROBO_TAG` to the RAMMP-CuRobo release from gate item 2, both without the leading `v` (images are tagged `1.0.1` for release `v1.0.1`):

```bash
gh release list -R rammp-org/kinova-gen3-ros2 --limit 3
gh release list -R rammp-org/RAMMP-CuRobo --limit 3
docker manifest inspect ghcr.io/rammp-org/kinova-gen3-ros2:$ROS2_TAG > /dev/null && echo ros2 ok
docker manifest inspect ghcr.io/rammp-org/rammp-curobo:$CUROBO_TAG > /dev/null && echo curobo ok
```

Expected: `ros2 ok` and `curobo ok`. If `docker manifest inspect` is not authorised for ghcr.io locally, run the two `inspect` lines on abra over `ssh abra`.

- [ ] **Step 2: Replace the tags**

```bash
sed -i.bak -E "s|(ghcr.io/rammp-org/kinova-gen3-ros2:)[^ ]+|\1$ROS2_TAG|; s|(ghcr.io/rammp-org/rammp-curobo:)[^ ]+|\1$CUROBO_TAG|" december_2026/sheppy-manifest.yaml
rm december_2026/sheppy-manifest.yaml.bak
grep -n 'image: ghcr.io/rammp-org/\(kinova-gen3-ros2\|rammp-curobo\)' december_2026/sheppy-manifest.yaml
```

Expected: three lines — `arm` and `arm_state_publisher` on `kinova-gen3-ros2:$ROS2_TAG`, `planner` on `rammp-curobo:$CUROBO_TAG`. Both kinova lines must carry the same tag: the driver and its description ship in one image.

- [ ] **Step 3: The manifest validates**

Run: `uv run -q --no-project --with "sheppy @ git+https://github.com/rammp-org/sheppy@main" python scripts/validate_manifest.py; echo "exit $?"`
Expected: `  december_2026/sheppy-manifest.yaml: ok — 7 nodes, 16 alternatives`, `exit 0`.

- [ ] **Step 4: The new description renders each robot (abra, offline)**

Runs only `arm_state_publisher`'s container, exactly as the manifest launches it, on `ROS_DOMAIN_ID=42` so nothing else on abra sees it. It needs no arm. It pulls the new image; everything else lives in `/tmp` and is removed.

```bash
rsync -a --exclude .git ./ abra:/tmp/tilted-mount-check/
ssh abra "cd /tmp/tilted-mount-check && for r in dojo flatbot; do
  uv run -q scripts/set_robot.py december_2026/config/robots/\$r
  docker run -d --name mount-check --network host --ipc host -e ROS_DOMAIN_ID=42 -e HOME=/tmp -u 1000:1000 \
    -v \$PWD/december_2026/config/robots/current:/config/robot:ro \
    ghcr.io/rammp-org/kinova-gen3-ros2:$ROS2_TAG \
    ros2 launch kinova_gen3_description description.launch.py mount_file:=/config/robot/mount.yaml > /dev/null
  sleep 8; docker logs mount-check 2>&1 | grep 'embodiment:'
  docker exec -e ROS_DOMAIN_ID=42 mount-check bash -c 'source /opt/ros/humble/setup.bash &&
    timeout 10 ros2 topic echo --once --qos-durability transient_local /arm/robot_description' |
    grep -c 'link name=\"arm_mount\"'
  docker rm -f mount-check > /dev/null
done; rm -rf /tmp/tilted-mount-check"
```

Expected, twice: `embodiment: dojo (mount rpy_deg [0.0, 0.0, 0.0])` then `1`; `embodiment: flatbot (mount rpy_deg [0.0, 30.0, 0.0])` then `1`. (The log line's exact wording is the kinova-gen3-ros2 plan's; match on `embodiment: <robot>`.)

- [ ] **Step 5: Lint and commit**

```bash
pre-commit run --files december_2026/sheppy-manifest.yaml
git add december_2026/sheppy-manifest.yaml
git commit -m "feat(december_2026): kinova-gen3-ros2 $ROS2_TAG and rammp-curobo $CUROBO_TAG (arm_mount)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 6: Rollout (human) — the only step that changes a host**

Run by Swapnil after the PR merges, with the arm powered and a human on the e-stop, in the spec's order ("Rollout"). From the deployment checkout on each Jetson:

```bash
# abra (dojo) first: the no-op check.
uv run scripts/set_robot.py --dry-run december_2026/config/robots/dojo
uv run scripts/set_robot.py december_2026/config/robots/dojo
# snorlax (flatbot), after dojo passes.
uv run scripts/set_robot.py --dry-run december_2026/config/robots/flatbot
uv run scripts/set_robot.py december_2026/config/robots/flatbot
```

Expected: `current: (none) → dojo, mount rpy_deg [0.0, 0.0, 0.0]` on abra and `current: (none) → flatbot, mount rpy_deg [0.0, 30.0, 0.0]` on snorlax, or the `rmdir` refusal if sheppy already ran without the link. The pass criteria after starting sheppy are the spec's (`g(q)`/FK unchanged on dojo; no droop, forward lean, teleop +Z straight up on flatbot) and belong to the kinova-gen3-ros2 and kinova-gen3-driver plans.
