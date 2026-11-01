# flatbot

A mobile test platform for the December 2026 deployment, not the chair: a Kinova Gen3
with a Robotiq 2F-85, two OAK-D PoE cameras, and a Seeed reComputer Robotics J5012 with
GMSL (Jetson AGX Orin 64GB, `snorlax`).

## Cabling

| Jetson NIC | Goes to                                | Addressing                                      |
| ---------- | -------------------------------------- | ----------------------------------------------- |
| `enp3s0`   | Kinova Gen3 base, direct               | static `192.168.1.11/24`, no gateway; arm `.10` |
| `eno1`     | ESP32-P4 sensor network (PoE for OAKs) | DHCP from `192.168.4.1`                         |

Both OAKs are on the sensor network today. When the wrist camera is cabled through the
arm's wrist Ethernet, it is on the `enp3s0` link instead, which has no DHCP server; set
its entry in `oak_d.yaml` to `static` on `192.168.1.x` first.

## After a reflash

From a checkout of this repo on the Jetson, over Wi-Fi or Tailscale — not over a NIC
being reconfigured:

```bash
uv run scripts/network_config.py december_2026/config/robots/flatbot
uv run scripts/oak_d_config.py   december_2026/config/robots/flatbot
uv run scripts/set_robot.py      december_2026/config/robots/flatbot
sudo december_2026/config/hosts/jetson-agx-orin/tuning.sh install
sudo december_2026/config/hosts/jetson-agx-orin/time_server.sh install
```

Each of the first three takes `--dry-run` to show what it would change. All five are safe to
re-run. `tuning.sh` tunes the Jetson for the arm link ([why](../../hosts/jetson-agx-orin/README.md));
`time_server.sh` serves time to the MIB, which stamps odometry with it
([why](../../hosts/jetson-agx-orin/README.md#time-server-for-the-mib)). Each one's `check`
reports whether it is in effect.

`set_robot` makes flatbot this host's active robot: `config/robots/current` points at
it, and the arm's nodes read its `mount.yaml` through that link. Run it before starting
sheppy; a start without it leaves an empty `current` directory behind, which
`set_robot` refuses and tells you how to remove.

The host's sheppy must be at or after #149 (df89566), which resolves the relative bind-mount
path against the manifest; older versions pass it to docker unchanged, and docker refuses it
as a volume name, so `arm_state_publisher` never starts. No sheppy tag has it yet (only
`dev` does; snorlax had 84fc8e7 on 2026-10-18, which predates it), so install from `dev`:

````bash
uv tool install --force "sheppy @ git+https://github.com/rammp-org/sheppy@dev"
``` Re-running
`set_robot` while the stack is up takes effect only after `arm`, `arm_state_publisher` and
`planner` are restarted, since the bind mount is fixed at container start.

`network_config` asks for sudo once. It turns off autoconnect on any other profile bound
to the same NICs (a leftover one takes the NIC back at boot) but does not delete them.

`oak_d_config` finds each camera by device ID, so the camera must be reachable: on a
subnet the Jetson has an address on, and not held by a running camera node. A camera
still on an old static address elsewhere needs a temporary address on that subnet
first, and the address removed after:

```bash
sudo ip addr add 192.168.1.250/24 dev eno1   # an address nothing else uses
uv run scripts/oak_d_config.py december_2026/config/robots/flatbot
sudo ip addr del 192.168.1.250/24 dev eno1
````

`192.168.1.x` is also the arm's subnet on `enp3s0`: while the temporary address is up,
arm traffic can leave on the wrong NIC, so do this with the arm unplugged or idle.

## Bringing it up

```bash
sheppy up flatbot --manifest december_2026/sheppy-manifest.yaml
```

`profiles/flatbot.yaml` selects both OAKs, foxglove, and the arm, `arm_state_publisher`
and planner on the **nightly** channel (`:nightly`, pulled on every start). The release
arm images (1.1.0) predate the tilted-mount support and are right only on a level mount.
The profile gives the planner `world_empty.yaml`: dojo's bench, its default, is wrong
under a tilted `arm_mount`. The planner exits if it does not get the arm's model within
30 s, and sheppy does not restart it, so after an `arm_state_publisher` problem run
`sheppy restart planner`. Attended only: someone on the e-stop.

## Cameras

| Node           | Device ID            | Bootloader |
| -------------- | -------------------- | ---------- |
| `wrist_camera` | `194430107187F42E00` | 0.0.29     |
| `scene_camera` | `19443010219A974800` | 0.0.28     |

depthai offers a bootloader update to 0.0.29 for the scene camera. It is not needed for
DHCP and is not done by these scripts.
