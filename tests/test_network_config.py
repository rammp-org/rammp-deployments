from __future__ import annotations

import pytest
import yaml
from conftest import load_script

nc = load_script("network_config")

ARM = """
interfaces:
  - profile: flatbot-arm
    device: enp3s0
    ipv4:
      method: static
      address: 192.168.1.11/24
      routes:
        - to: 10.20.0.0/24
          via: {via}
"""


def parse(text: str):
    return nc.parse(yaml.safe_load(text), "network.yaml")


def test_routes_parsed():
    (iface,) = parse(ARM.format(via="192.168.1.10"))
    assert iface.routes == (("10.20.0.0/24", "192.168.1.10"),)


def test_no_routes_is_empty():
    (iface,) = parse(
        "interfaces: [{profile: p, device: d, ipv4: {method: static, address: 10.0.0.2/24}}]"
    )
    assert iface.routes == ()


def test_route_via_outside_subnet_rejected():
    with pytest.raises(nc.ConfigError, match=r"via.*outside 192\.168\.1\.0/24"):
        parse(ARM.format(via="10.20.0.1"))


def test_route_to_must_be_network():
    text = ARM.format(via="192.168.1.10").replace("10.20.0.0/24", "10.20.0.5/24")
    with pytest.raises(nc.ConfigError, match=r"routes\[0\]\.to"):
        parse(text)


def test_route_unknown_key_rejected():
    text = ARM.format(via="192.168.1.10") + "          metric: 5\n"
    with pytest.raises(nc.ConfigError, match="unknown key metric"):
        parse(text)


def test_routes_on_dhcp_rejected():
    text = """
interfaces:
  - profile: p
    device: d
    ipv4:
      method: dhcp
      routes: [{to: 10.20.0.0/24, via: 192.168.1.10}]
"""
    with pytest.raises(nc.ConfigError):
        parse(text)


def test_settings_routes():
    (iface,) = parse(ARM.format(via="192.168.1.10"))
    assert nc.settings(iface)["ipv4.routes"] == "10.20.0.0/24 192.168.1.10"


def _plan_with_current(monkeypatch, iface, routes_as_read: str):
    current = dict(nc.settings(iface))
    current["ipv4.routes"] = routes_as_read
    monkeypatch.setattr(nc, "_get", lambda fields, *target: dict(current))
    monkeypatch.setattr(nc, "_sys", lambda device, name: "")
    mine = nc.Profile("uuid-1", iface.profile, "802-3-ethernet", iface.device, "yes", "")
    return nc.plan(iface, [mine], {iface.profile})


def test_plan_unchanged_when_nmcli_spells_routes_differently(monkeypatch):
    (iface,) = parse(ARM.format(via="192.168.1.10"))
    commands, _ = _plan_with_current(monkeypatch, iface, "10.20.0.0/24 192.168.1.10 0")
    assert commands == []


def test_plan_modifies_when_route_missing(monkeypatch):
    (iface,) = parse(ARM.format(via="192.168.1.10"))
    commands, _ = _plan_with_current(monkeypatch, iface, "")
    assert len(commands) == 1 and commands[0][:3] == ["nmcli", "con", "mod"]
    assert "10.20.0.0/24 192.168.1.10" in commands[0]
