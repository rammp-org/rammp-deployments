from __future__ import annotations

import contextlib

import pytest
import yaml
from conftest import load_script

ac = load_script("arm_config")

FLATBOT = """
address: 192.168.1.10
wrist_ethernet:
  enabled: true
  speed: 100M
  duplex: half
"""


def parse(text: str):
    return ac.parse(yaml.safe_load(text), "arm.yaml")


def test_parse_flatbot():
    assert parse(FLATBOT) == ac.Arm(
        "192.168.1.10", ac.WristEthernet(True, "100M", "half")
    )


def test_bad_duplex_rejected():
    with pytest.raises(ac.ConfigError, match="half, full"):
        parse(FLATBOT.replace("duplex: half", "duplex: FULL"))


def test_bad_speed_rejected():
    with pytest.raises(ac.ConfigError, match="10M, 100M"):
        parse(FLATBOT.replace("speed: 100M", "speed: 1G"))


def test_enabled_must_be_bool():
    with pytest.raises(ac.ConfigError, match="enabled"):
        parse(FLATBOT.replace("enabled: true", "enabled: yes please"))


def test_unknown_key_rejected():
    with pytest.raises(ac.ConfigError, match="unknown key mtu"):
        parse(FLATBOT + "  mtu: 1500\n")


def test_bad_address_rejected():
    with pytest.raises(ac.ConfigError, match="address"):
        parse(FLATBOT.replace("192.168.1.10", "192.168.1"))


class FakeWrist:
    def __init__(self, current: ac.WristEthernet):
        self.current = current
        self.writes = []

    def read_wrist(self) -> ac.WristEthernet:
        return self.current

    def write_wrist(self, cfg: ac.WristEthernet) -> None:
        self.writes.append(cfg)
        self.current = cfg


def use_fake(monkeypatch, wrist: FakeWrist):
    @contextlib.contextmanager
    def session(address):
        yield wrist

    monkeypatch.setattr(ac, "_session", session)


def test_apply_skips_write_when_matching(monkeypatch, capsys):
    arm = parse(FLATBOT)
    wrist = FakeWrist(arm.wrist_ethernet)
    use_fake(monkeypatch, wrist)
    assert ac.apply(arm, dry_run=False) == 0
    assert wrist.writes == []
    assert "unchanged" in capsys.readouterr().out


def test_apply_writes_and_reads_back(monkeypatch, capsys):
    arm = parse(FLATBOT)
    wrist = FakeWrist(ac.WristEthernet(False, "100M", "full"))
    use_fake(monkeypatch, wrist)
    assert ac.apply(arm, dry_run=False) == 0
    assert wrist.writes == [arm.wrist_ethernet]
    out = capsys.readouterr().out
    assert "disabled 100M full -> enabled 100M half" in out


def test_apply_dry_run_does_not_write(monkeypatch, capsys):
    arm = parse(FLATBOT)
    wrist = FakeWrist(ac.WristEthernet(False, "100M", "full"))
    use_fake(monkeypatch, wrist)
    assert ac.apply(arm, dry_run=True) == 0
    assert wrist.writes == []
    assert "would change" in capsys.readouterr().out


def test_apply_readback_mismatch_fails(monkeypatch, capsys):
    arm = parse(FLATBOT)
    wrist = FakeWrist(ac.WristEthernet(False, "100M", "full"))
    wrist.write_wrist = lambda cfg: wrist.writes.append(cfg)  # the arm ignores it
    use_fake(monkeypatch, wrist)
    assert ac.apply(arm, dry_run=False) == 1
    assert "read back" in capsys.readouterr().err


def test_apply_unreachable_is_one_line(monkeypatch, capsys):
    @contextlib.contextmanager
    def session(address):
        raise ConnectionError(f"no answer from {address}:10000 within 5 s")
        yield

    monkeypatch.setattr(ac, "_session", session)
    assert ac.apply(parse(FLATBOT), dry_run=False) == 1
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1 and err[0].startswith("arm (192.168.1.10): FAILED:")
