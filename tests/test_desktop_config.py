from __future__ import annotations

import pytest
import yaml
from conftest import load_script

dc = load_script("desktop_config")

FLATBOT = """
env:
  - name: LD_LIBRARY_PATH
    prepend: ~/Vulkan-Loader/build/loader
  - name: RMW_IMPLEMENTATION
    value: rmw_cyclonedds_cpp
display:
  output: HDMI-0
  rotate: left
"""


def parse(text: str):
    return dc.parse(yaml.safe_load(text), "desktop.yaml", "flatbot")


def test_env_block():
    block = dc.env_block(parse(FLATBOT))
    assert (
        'export LD_LIBRARY_PATH="$HOME/Vulkan-Loader/build/loader${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"'
        in block
    )
    assert 'export RMW_IMPLEMENTATION="rmw_cyclonedds_cpp"' in block
    assert block.startswith(dc.BEGIN) and block.endswith(dc.END + "\n")


def test_block_appended_then_replaced():
    block = dc.env_block(parse(FLATBOT))
    once = dc.with_block("alias ll='ls -l'\n", block)
    assert once == "alias ll='ls -l'\n\n" + block
    assert dc.with_block(once, block) == once
    other = dc.env_block(parse("env: [{name: A, value: b}]"))
    assert dc.with_block(once, other) == "alias ll='ls -l'\n\n" + other


def test_empty_block_removes():
    once = dc.with_block("x\n", dc.env_block(parse(FLATBOT)))
    assert dc.with_block(once, "") == "x\n\n"


def test_half_a_block_refused():
    with pytest.raises(dc.ConfigError, match="only one"):
        dc.with_block(dc.BEGIN + "\nexport A=b\n", "")


def test_autostart_waits_then_rotates():
    entry = dc.autostart(parse(FLATBOT).display)
    assert 'Exec=sh -c "sleep 5 && xrandr --output HDMI-0 --rotate left"' in entry


@pytest.mark.parametrize(
    "text, match",
    [
        ("env: [{name: A}]", "exactly one of value, prepend"),
        ("env: [{name: A, value: b, prepend: c}]", "exactly one"),
        ("env: [{name: 1A, value: b}]", "not a shell variable"),
        ('env: [{name: A, value: "$B"}]', "no"),
        ("env: [{name: A, value: b}, {name: A, value: c}]", "duplicate name A"),
        ("display: {output: HDMI-0, rotate: sideways}", "expected one of"),
        ("display: {output: HDMI-0}", "missing rotate"),
        ("{}", "expected env, display or both"),
        ("screen: {}", "unknown key screen"),
    ],
)
def test_invalid(text, match):
    with pytest.raises(dc.ConfigError, match=match):
        parse(text)


def test_apply_writes_and_is_idempotent(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        dc.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or _Ok()
    )
    (tmp_path / ".bashrc").write_text("x\n")
    desktop = parse(FLATBOT)
    assert dc.apply(desktop, tmp_path, dry_run=False) == 0
    entry = tmp_path / ".config/autostart" / dc.AUTOSTART_NAME
    assert entry.read_text() == dc.autostart(desktop.display)
    bashrc = (tmp_path / ".bashrc").read_text()
    assert dc.apply(desktop, tmp_path, dry_run=False) == 0
    assert (tmp_path / ".bashrc").read_text() == bashrc
    assert calls[0] == ["xrandr", "--output", "HDMI-0", "--rotate", "left"]


def test_dry_run_changes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(dc.subprocess, "run", lambda *a, **kw: pytest.fail("ran"))
    (tmp_path / ".bashrc").write_text("x\n")
    assert dc.apply(parse(FLATBOT), tmp_path, dry_run=True) == 0
    assert (tmp_path / ".bashrc").read_text() == "x\n"
    assert not (tmp_path / ".config").exists()


class _Ok:
    returncode = 0
    stderr = ""
