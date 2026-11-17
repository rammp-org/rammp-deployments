# Wrist camera through the Kinova Vision Puck

**Date:** 2026-11-17 **Status:** draft for review

## Why

flatbot's wrist OAK-D Pro PoE (`194430107187F42E00`) is cabled to the ESP32-P4 sensor
network on `eno1` today and gets its address by DHCP. Kinova supplied a Vision Puck that
carries the camera's Ethernet through the arm's slip ring instead, so the cable no longer
runs outside the arm. The config should move to the Puck, and going back to the sensor
network should stay easy: switching is rare and done by one person who knows the robot.

## How the Puck works (from Kinova's procedure and scripts)

The camera sits on the arm's internal subnet, **10.20.0.0/24**, and the arm base routes
between it and its own port (192.168.1.10). The camera holds a static 10.20.0.50/24,
gateway 10.20.0.1. The arm's wrist Ethernet port is off by default; it is turned on
through the Kortex API (`InterconnectConfig.SetEthernetConfiguration` on the
Interconnect's `ETHERNET_DEVICE_EXPANSION`), forced to 100 Mbit/s. A host reaches the
camera by a route to 10.20.0.0/24 via the arm. depthai discovery is a broadcast and
does not cross that route, so the camera has to be addressed by IP.

## Goals

- flatbot runs the wrist camera through the Puck, with every step in config and scripts
  this repo already applies (`network_config.py`, `oak_d_config.py`, a profile).
- Going back to the sensor network is a short, documented edit, not a rewrite.

**Non-goals:** a single switch shared by every file; switching gen_1_5 or dojo; changing
camera tuning (resolution, codecs, bandwidth).

**Anti-goal:** a switch that silently half-applies. A missed step must fail loudly.

## Approaches

- **A. Both setups in the files, one commented, plus a runbook (chosen).** The Puck
  setup is live; the sensor-network setup sits beside it, commented. Two edits to switch
  (`oak_d.yaml`, the profile), and a missed one fails loudly: the camera node exits
  after 60 s with "no camera matches driver.i_ip / driver.i_device_id".
- B. Named setups in `oak_d.yaml` with a `use:` key. One word in that file, but the
  profile still changes separately, and the script grows a selection layer. Not worth
  it for a rare switch.
- C. One switch file read by every script and the profile. Generators or cross-file
  checks; overkill at this frequency.

## Design

**Always on** (harmless when the camera is on the sensor network):

- `network.yaml`: `flatbot-arm` gains `routes: [{to: 10.20.0.0/24, via: 192.168.1.10}]`.
  `network_config.py` learns an optional `routes:` list per interface, validated (CIDR
  destination, gateway inside the interface's subnet) and applied as NetworkManager
  `ipv4.routes`.
- New `arm.yaml` + `scripts/arm_config.py`: the wrist Ethernet port enabled, 100 Mbit/s,
  **half duplex** (Kinova, 2026-11-17; `duplex:` stays switchable). Modeled on Kinova's `VisionPkConf.py`: find the Interconnect with
  `DeviceManager.ReadAllDevices`, read the port config, change only enabled/speed/duplex,
  write it back, read it back to confirm. Same shape as the other scripts: `--validate`,
  `--dry-run`, no write when it already matches, exit 0/1/2. Runs under uv on
  Python 3.9 with Kinova's `kortex_api` 2.8.0 wheel from their public artifactory (2.8.0
  matches the SDK the arm driver image links). Credentials are not in the file: the
  arm's default admin login is taken from the environment, with the default as fallback.

**Switched** (Puck live, sensor network commented):

- `oak_d.yaml`: `wrist_camera` is `static 10.20.0.50/24, gateway 10.20.0.1`. The DHCP
  entry stays beside it, commented.
- `profiles/flatbot.yaml` (and `flatbot_empty_world.yaml`): override
  `driver.i_ip: 10.20.0.50` on `wrist_camera`, commented for the sensor network. The
  manifest's `oak_d_pro_docker` lists `driver.i_ip: ""` in `params:` so the override is
  allowed; `""` keeps discovery by device ID, the driver's default.

**Reaching a camera behind the arm:** `oak_d_config.py` takes an optional per-camera
`reach_at:` (an IP). When set, it connects to that address directly instead of
discovering, checks the device ID matches, and waits for the camera to come back at its
new address by IP rather than by discovery. That is what lets the script flash the
camera back to DHCP from behind the Puck. Without `reach_at`, behaviour is unchanged.

**Runbook** in `flatbot/README.md`, "Switching the wrist camera": the order for each
direction (flash the camera on the link it is on now, then move the cable, then flip the
profile and restart the node), and that 10.20.0.50 only answers once the cable is in the
Puck and `arm_config.py` has run.

**Docs fixed:** the README and `oak_d.yaml` comments that say the camera would be static
on 192.168.1.x.

## Left to the builder

- Exact yaml shape for `routes:` and `arm.yaml`, following the existing scripts' parsing
  style and error messages.
- Unit tests for the new parsing (no hardware in CI); `validate.yml` runs the new
  `--validate`.

## Verify on snorlax (attended)

1. `network_config.py`: the route is present (`ip route get 10.20.0.50` via enp3s0).
2. `arm_config.py`: the port reads back enabled, 100M, half.
3. `oak_d_config.py` while the camera is still on `eno1`: writes static 10.20.0.50.
4. Cable into the Puck: `ping 10.20.0.50`; `sheppy up flatbot`; `/wrist_camera` topics
   publish; bandwidth stays under the link (the node measured 63-83 Mbit/s on
   2026-10-27).

## Open with Kinova

Whether the wrist port setting survives a power cycle, and whether 10.20.0.1 is the
base's address on the wrist subnet. If the setting does not persist, `arm_config.py`
becomes a step before each `sheppy up`.
