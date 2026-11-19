#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pyyaml==6.0.2"]
# ///
"""Apply a robot's desktop.yaml to this host's login account.

    uv run scripts/desktop_config.py [--validate | --dry-run] <robot dir>

Two settings, both for the account the desktop logs into (run as that user):

- `env`: variables exported from a block this script owns at the end of the
  login shell's rc file (~/.zshrc for zsh, ~/.bashrc for bash), so terminals
  get them. Apps started from desktop icons don't read it and don't see them.
- `display`: a screen rotation, through a GNOME autostart entry this script
  owns (~/.config/autostart/rammp-display.desktop), applied now as well. The
  entry waits a few seconds after login: run straight away, GNOME applies its
  own monitor config after it and the rotation is lost (snorlax, 2026-11-19).

Exit status: 0 ok, 1 a change failed, 2 the config is invalid and nothing was
changed.
"""

from __future__ import annotations

import argparse
import os
import pwd
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

CONFIG_NAME = "desktop.yaml"
BEGIN = "# >>> rammp-deployments desktop_config >>>"
END = "# <<< rammp-deployments desktop_config <<<"
AUTOSTART_NAME = "rammp-display.desktop"
ROTATIONS = ("normal", "left", "right", "inverted")
LOGIN_DELAY_S = 5
RC_FILES = {"zsh": ".zshrc", "bash": ".bashrc"}  # same export syntax in both


class ConfigError(Exception):
    """The file is not usable. Nothing changed."""


@dataclass(frozen=True)
class Env:
    name: str
    value: str  # with a leading ~/ already written as $HOME/
    prepend: bool  # True: put value in front of the variable's existing value


@dataclass(frozen=True)
class Display:
    output: str
    rotate: str


@dataclass(frozen=True)
class Desktop:
    robot: str
    env: tuple[Env, ...] = ()
    display: Display | None = None


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


def parse_env(where: str, value: object) -> tuple[Env, ...]:
    """Each entry is {name, value} or {name, prepend}."""
    if not isinstance(value, list) or not value:
        raise ConfigError(f"{where}: expected a non-empty list")
    found = []
    for i, entry in enumerate(value):
        at = f"{where}[{i}]"
        _keys(at, entry, ["name"], ["value", "prepend"])
        modes = [k for k in ("value", "prepend") if k in entry]
        if len(modes) != 1:
            raise ConfigError(f"{at}: needs exactly one of value, prepend")
        name = _string(f"{at}.name", entry["name"])
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ConfigError(f"{at}.name: {name!r} is not a shell variable name")
        text = _string(f"{at}.{modes[0]}", entry[modes[0]])
        # Written inside double quotes: these would be expanded or end the string.
        if any(c in text for c in '"$`\\'):
            raise ConfigError(f'{at}.{modes[0]}: no " $ ` or \\ allowed')
        if text.startswith("~/"):
            text = "$HOME/" + text[2:]
        found.append(Env(name, text, modes[0] == "prepend"))
    names = [e.name for e in found]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise ConfigError(f"{where}: duplicate name {', '.join(dupes)}")
    return tuple(found)


def parse(data: object, where: str, robot: str) -> Desktop:
    top = _keys(where, data, [], ["env", "display"])
    if not top:
        raise ConfigError(f"{where}: expected env, display or both")
    env = parse_env(f"{where}.env", top["env"]) if "env" in top else ()
    display = None
    if "display" in top:
        d = _keys(f"{where}.display", top["display"], ["output", "rotate"])
        rotate = d["rotate"]
        if rotate not in ROTATIONS:
            raise ConfigError(
                f"{where}.display.rotate: expected one of {', '.join(ROTATIONS)}, got {rotate!r}"
            )
        display = Display(_string(f"{where}.display.output", d["output"]), rotate)
    return Desktop(robot, env, display)


def load(robot_dir: Path) -> Desktop:
    path = robot_dir / CONFIG_NAME
    if not path.is_file():
        raise ConfigError(f"{path}: not found")
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise ConfigError(f"{path}: {e}") from e
    return parse(data, str(path), robot_dir.resolve().name)


def env_block(desktop: Desktop) -> str:
    lines = [
        BEGIN,
        f"# From config/robots/{desktop.robot}/{CONFIG_NAME}; desktop_config.py rewrites this block.",
    ]
    for e in desktop.env:
        if e.prepend:
            value = f"{e.value}${{{e.name}:+:${e.name}}}"
        else:
            value = e.value
        lines.append(f'export {e.name}="{value}"')
    lines.append(END)
    return "\n".join(lines) + "\n"


def with_block(text: str, block: str) -> str:
    """text with its block replaced by block, or block appended. An empty block removes it."""
    start, end = text.find(BEGIN), text.find(END)
    if (start == -1) != (end == -1) or end < start:
        raise ConfigError(f"rc file has only one of the {BEGIN!r} / {END!r} lines")
    if start != -1:
        stop = end + len(END)
        if text[stop : stop + 1] == "\n":
            stop += 1
        return text[:start] + block + text[stop:]
    if not block:
        return text
    if text and not text.endswith("\n"):
        text += "\n"
    return text + ("\n" if text else "") + block


def autostart(display: Display) -> str:
    rotate = f"xrandr --output {shlex.quote(display.output)} --rotate {display.rotate}"
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=RAMMP display rotation\n"
        "Comment=Written by rammp-deployments scripts/desktop_config.py\n"
        f'Exec=sh -c "sleep {LOGIN_DELAY_S} && {rotate}"\n'
        "X-GNOME-Autostart-enabled=true\n"
    )


def _write(path: Path, text: str, dry_run: bool) -> str:
    """Write text to path unless it is already there; returns what happened."""
    if path.is_file() and path.read_text() == text:
        return "unchanged"
    if dry_run:
        return "would write"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    if path.exists():
        tmp.chmod(path.stat().st_mode)
    tmp.replace(path)
    return "written"


def rc_file(home: Path, shell: str) -> Path:
    """The rc file interactive shells of this login shell read."""
    name = Path(shell).name
    if name not in RC_FILES:
        raise ConfigError(
            f"login shell {shell!r}: expected one of {', '.join(RC_FILES)}"
        )
    return home / RC_FILES[name]


def apply(desktop: Desktop, home: Path, shell: str, dry_run: bool) -> int:
    entry = home / ".config" / "autostart" / AUTOSTART_NAME
    try:
        rc = rc_file(home, shell)
        text = rc.read_text() if rc.exists() else ""
        new = with_block(text, env_block(desktop) if desktop.env else "")
    except (OSError, ConfigError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    failed = 0
    try:
        print(f"{rc}: {_write(rc, new, dry_run)}")
        if desktop.display:
            print(f"{entry}: {_write(entry, autostart(desktop.display), dry_run)}")
        elif entry.exists():
            if not dry_run:
                entry.unlink()
            print(f"{entry}: {'would remove' if dry_run else 'removed'}")
    except OSError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if desktop.display:
        cmd = ["xrandr", "--output", desktop.display.output]
        cmd += ["--rotate", desktop.display.rotate]
        display = os.environ.get("DISPLAY", ":0")
        if dry_run:
            print(f"would run: DISPLAY={display} {shlex.join(cmd)}")
        else:
            run = subprocess.run(
                cmd,
                env={**os.environ, "DISPLAY": display},
                capture_output=True,
                text=True,
            )
            if run.returncode:
                print(
                    f"{desktop.display.output}: FAILED to rotate now: {run.stderr.strip()}"
                    " (the autostart entry still applies it at the next login)",
                    file=sys.stderr,
                )
                failed += 1
            else:
                print(f"{desktop.display.output}: rotated {desktop.display.rotate}")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--validate", action="store_true", help="check the file and exit")
    mode.add_argument("--dry-run", action="store_true", help="print changes, make none")
    parser.add_argument("robot_dir", type=Path)
    args = parser.parse_args(argv)

    try:
        desktop = load(args.robot_dir)
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if args.validate:
        print(
            f"{args.robot_dir / CONFIG_NAME}: ok — {len(desktop.env)} env, "
            f"display {'set' if desktop.display else 'not set'}"
        )
        return 0
    shell = pwd.getpwuid(os.getuid()).pw_shell
    return apply(desktop, Path.home(), shell, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
