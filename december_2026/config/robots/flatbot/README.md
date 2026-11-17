# flatbot

A mobile test platform for the December 2026 deployment, not the chair: a Kinova Gen3
with a Robotiq 2F-85, two OAK-D PoE cameras, and a Seeed reComputer Robotics J5012 with
GMSL (Jetson AGX Orin 64GB, `snorlax`).

## Cabling

| Jetson NIC | Goes to                                | Addressing                                      |
| ---------- | -------------------------------------- | ----------------------------------------------- |
| `enp3s0`   | Kinova Gen3 base, direct               | static `192.168.1.11/24`, no gateway; arm `.10` |
| `eno1`     | ESP32-P4 sensor network (PoE for OAKs) | DHCP from `192.168.4.1`                         |

Both OAKs are on the sensor network. The wrist camera can instead be cabled through the
arm by the Kinova Vision Puck: then it sits on the arm's wrist subnet `10.20.0.0/24` at
`10.20.0.50` (gateway `10.20.0.1`), which the arm base routes. `network.yaml` routes that
subnet via the arm on `enp3s0`, and `arm.yaml` turns the arm's wrist Ethernet port on. Both
stay in place while the wrist camera is on the sensor network; see
[Switching the wrist camera](#switching-the-wrist-camera).

The wrist link is 100 Mbit/s, half duplex as Kinova asked (`arm.yaml`), shared between
both directions. The wrist camera's tuning measured 63-83 Mbit/s on a 100 Mbit/s link
(2026-10-27, 2026-11-01). If frames drop through the Puck, lower its depth rate first.

## After a reflash

From a checkout of this repo on the Jetson, over Wi-Fi or Tailscale — not over a NIC
being reconfigured:

```bash
uv run scripts/network_config.py december_2026/config/robots/flatbot
uv run scripts/arm_config.py     december_2026/config/robots/flatbot
uv run scripts/oak_d_config.py   december_2026/config/robots/flatbot
uv run scripts/set_robot.py      december_2026/config/robots/flatbot
sudo december_2026/config/hosts/jetson-agx-orin/tuning.sh install
sudo december_2026/config/hosts/jetson-agx-orin/time_server.sh install
```

Each of the first four takes `--dry-run` to show what it would change. All six are safe to
re-run. `arm_config` needs the arm on; it fetches Python 3.9 and Kinova's Kortex API on its
first run. Whether the arm keeps the wrist port setting across a power cycle is not yet
known, so re-run it after powering the arm until it is. `tuning.sh` tunes the Jetson for the arm link ([why](../../hosts/jetson-agx-orin/README.md));
`time_server.sh` serves time to the MIB, which stamps odometry with it
([why](../../hosts/jetson-agx-orin/README.md#time-server-for-the-mib)). Each one's `check`
reports whether it is in effect.

`set_robot` makes flatbot this host's active robot: `config/robots/current` points at
it, and the arm's nodes read its `mount.yaml` through that link. Run it before starting
sheppy; a start without it leaves an empty `current` directory behind, which
`set_robot` refuses and tells you how to remove. Re-running `set_robot` while the stack is
up takes effect only after `arm`, `arm_state_publisher` and `planner` are restarted, since
the bind mount is fixed at container start.

The host's sheppy must be `dev` at 0a4c2d2 or later, and the SAM3 image needs a GHCR login:
see [Setting up a robot](../../../../README.md#setting-up-a-robot) in the top-level README.

`network_config` asks for sudo once. It turns off autoconnect on any other profile bound
to the same NICs (a leftover one takes the NIC back at boot) but does not delete them.

`oak_d_config` finds each camera by device ID, so the camera must be reachable and not
held by a running camera node: on a subnet the Jetson has an address on, or at the
camera's `reach_at` (the wrist camera's is `10.20.0.50`, behind the arm). A camera still
on an old static address elsewhere needs a temporary address on that subnet first, and
the address removed after:

```bash
sudo ip addr add 192.168.1.250/24 dev eno1   # an address nothing else uses
uv run scripts/oak_d_config.py december_2026/config/robots/flatbot
sudo ip addr del 192.168.1.250/24 dev eno1
```

`192.168.1.x` is also the arm's subnet on `enp3s0`: while the temporary address is up,
arm traffic can leave on the wrong NIC, so do this with the arm unplugged or idle.

## Switching the wrist camera

Two edits, the camera's flash, and the cable. The camera's address is stored in the
camera, so it is changed while the camera is still on the link it is leaving; it then
answers only once the cable is moved. The route (`network.yaml`) and the wrist port
(`arm.yaml`) stay as they are either way. A step missed shows: the wrist camera node
exits after 60 s with "OAK driver not ready".

**Sensor network → Vision Puck**

1. In `oak_d.yaml`, make `wrist_camera`'s Puck `ipv4` block live and comment out the
   DHCP one.
1. `sheppy down`, so no node holds the camera.
1. `uv run scripts/oak_d_config.py december_2026/config/robots/flatbot` writes static
   `10.20.0.50`. It then reports the camera did not come back within 60 s: expected,
   since that address answers only behind the arm.
1. Move the wrist camera's cable from the PoE switch to the Vision Puck.
1. `uv run scripts/network_config.py …` and `uv run scripts/arm_config.py …` (`unchanged`
   once applied), then `ping 10.20.0.50`. A re-run of `oak_d_config` now reports
   `unchanged at 10.20.0.50`.
1. In `profiles/flatbot.yaml` and `profiles/flatbot_empty_world.yaml`, select
   `wrist_camera: oak_d_pro_puck_docker`, then `sheppy up flatbot …`.

**Vision Puck → sensor network**

1. In `oak_d.yaml`, make the DHCP `ipv4` block live and comment out the Puck one.
1. `sheppy down`.
1. `oak_d_config` finds the camera at its `reach_at` (`10.20.0.50`) and writes DHCP. It
   then reports the camera did not come back: expected, as there is no DHCP server
   behind the arm.
1. Move the cable to the PoE switch. The camera takes a lease from the ESP32-P4; a
   re-run of `oak_d_config` reports `unchanged at 192.168.4.x`.
1. In both profiles, select `wrist_camera: oak_d_pro_docker`, then `sheppy up`.

If the cable was moved before step 3, the camera still holds `10.20.0.50` and nothing
can reach it on the PoE switch: plug it back into the Vision Puck, run step 3, then move
it.

## Bringing it up

```bash
sheppy up flatbot --manifest december_2026/sheppy-manifest.yaml
```

`profiles/flatbot.yaml` selects both OAKs, foxglove, and the arm, `arm_state_publisher`
and planner on the **nightly** channel (`:nightly`, pulled on every start). The release
arm images (kinova-gen3-ros2 2.0.0, RAMMP-CuRobo 2.0.1) support the tilted mount as well;
flatbot stays on the nightly, which runs ahead of them (for example the sensor-puck model,
kinova-gen3-ros2#81).
The planner reads flatbot's own files from `config/robots/flatbot/curobo/` through
`current` (run `scripts/set_robot.py` first): the arm's collision model and the chair,
seat and occupant around it. Plans that start from the arm's rest pose are refused until
the mount and seat are measured: rest sits 25 mm into the person's torso and 5 mm into the
seat's backrest. The planner exits if it does not get the arm's model within
30 s, and sheppy does not restart it, so after an `arm_state_publisher` problem run
`sheppy restart planner`. Attended only: someone on the e-stop.

The planner has two configs in `curobo/`, one per profile:

| Profile               | Planner config             | Plans against                                    |
| --------------------- | -------------------------- | ------------------------------------------------ |
| `flatbot`             | `planner.yaml`             | the chair, seat and occupant, in every world     |
| `flatbot_empty_world` | `planner_empty_world.yaml` | nothing: the arm alone, until a `set_world` push |

To switch, `sheppy up` the other profile:

```bash
sheppy up flatbot_empty_world --manifest december_2026/sheppy-manifest.yaml
```

The empty world plans straight through the chair and the person in it. Use it only when
the layers get in the way (a start they refuse), and watch every motion.

## Cameras

| Node           | Device ID            | Bootloader |
| -------------- | -------------------- | ---------- |
| `wrist_camera` | `194430107187F42E00` | 0.0.29     |
| `scene_camera` | `19443010219A974800` | 0.0.28     |

depthai offers a bootloader update to 0.0.29 for the scene camera. It is not needed for
DHCP and is not done by these scripts.
