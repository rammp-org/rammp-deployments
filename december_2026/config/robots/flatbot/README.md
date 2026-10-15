# flatbot

The bench robot for the December 2026 demo: a Kinova Gen3 on a 2F-85, two OAK-D PoE
cameras, and a Jetson AGX Orin (`snorlax`).

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
```

Each takes `--dry-run` to show what it would change. Both are safe to re-run.

`network_config` asks for sudo once. It turns off autoconnect on any other profile bound
to the same NICs (a leftover one takes the NIC back at boot) but does not delete them.

`oak_d_config` finds each camera by device ID, so the camera must be reachable: on a
subnet the Jetson has an address on, and not held by a running camera node. A camera
still on an old static address elsewhere needs a temporary address on that subnet
first, e.g. `sudo ip addr add 192.168.1.11/24 dev eno1`.

## Cameras

| Node           | Device ID            | Bootloader |
| -------------- | -------------------- | ---------- |
| `wrist_camera` | `194430107187F42E00` | 0.0.29     |
| `scene_camera` | `19443010219A974800` | 0.0.28     |

depthai offers a bootloader update to 0.0.29 for the scene camera. It is not needed for
DHCP and is not done by these scripts.
