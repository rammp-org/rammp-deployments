# flatbot

A mobile test platform for the December 2026 deployment, not the chair: a Kinova Gen3
with a Robotiq 2F-85, two OAK-D PoE cameras, and a Jetson AGX Orin (`snorlax`).

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
```

Each takes `--dry-run` to show what it would change. All three are safe to re-run.

`set_robot` makes flatbot this host's active robot: `config/robots/current` points at
it, and the arm's nodes read its `mount.yaml` through that link. Run it before starting
sheppy; a start without it leaves an empty `current` directory behind, which
`set_robot` refuses and tells you how to remove.

The host's sheppy must be at or after #149 (df89566), which resolves the relative bind-mount
path against the manifest; older versions pass it to docker unchanged. Re-running
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
```

`192.168.1.x` is also the arm's subnet on `enp3s0`: while the temporary address is up,
arm traffic can leave on the wrong NIC, so do this with the arm unplugged or idle.

## Cameras

| Node           | Device ID            | Bootloader |
| -------------- | -------------------- | ---------- |
| `wrist_camera` | `194430107187F42E00` | 0.0.29     |
| `scene_camera` | `19443010219A974800` | 0.0.28     |

depthai offers a bootloader update to 0.0.29 for the scene camera. It is not needed for
DHCP and is not done by these scripts.
