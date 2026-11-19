from __future__ import annotations

from conftest import load_script

rc = load_script("robot_config")


def test_steps_follow_files_in_order(tmp_path):
    for name in ("desktop.yaml", "network.yaml", "mount.yaml"):
        (tmp_path / name).write_text("")
    assert rc.steps(tmp_path) == [
        "network_config.py",
        "set_robot.py",
        "desktop_config.py",
    ]


def test_failure_keeps_going_and_worst_wins(tmp_path, monkeypatch):
    for name in ("network.yaml", "arm.yaml", "mount.yaml"):
        (tmp_path / name).write_text("")
    codes = {"network_config.py": 0, "arm_config.py": 1, "set_robot.py": 0}
    ran = []

    class Done:
        def __init__(self, code):
            self.returncode = code

    def run(cmd):
        script = cmd[2].rsplit("/", 1)[-1]
        ran.append((script, cmd[3:]))
        return Done(codes[script])

    monkeypatch.setattr(rc.subprocess, "run", run)
    assert rc.main(["--dry-run", str(tmp_path)]) == 1
    assert [s for s, _ in ran] == list(codes)
    assert all(flags == ["--dry-run", str(tmp_path)] for _, flags in ran)
