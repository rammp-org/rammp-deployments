# Robot configuration: host network and OAK-D addressing

**Date:** 2026-10-15 **Status:** draft for review **Related:**
[#59](https://github.com/rammp-org/rammp-deployments/issues/59) (parameter standards),
[rammp-org.github.io#10](https://github.com/rammp-org/rammp-org.github.io/pull/10)
(common patterns)

## Problem

Bringing flatbot's Jetson (snorlax) up for the December 2026 demo took a day of manual
`nmcli` and depthai work, and a reboot undid part of it. What we learned lives in a chat
log, not in the repo:

- The arm and the sensor network are on different NICs. The arm link has no DHCP server;
  the sensor network gets addresses from an ESP32-P4 at `192.168.4.1`.
- A NetworkManager profile left bound to a NIC with autoconnect on takes that NIC back at
  boot (`kinova` reclaimed `eno1` after a reboot).
- On OAK-D PoE bootloaders up to 0.0.29, a stored fallback IPv4 address disables DHCP
  entirely, even with `staticIpv4: false`. DHCP only works with the address cleared.
- The ROS driver finds an OAK by device ID (MxID) through discovery, which only reaches
  cameras on a subnet the host has an address on.

None of this is node configuration, so sheppy cannot hold it. It is setup of the robot
itself, done before sheppy starts anything.

## Where each kind of setting lives

This is the convention the repo locks in. sheppy already owns most of it.

| Setting                                          | Lives in                                      | Owner                                                                                  |
| ------------------------------------------------ | --------------------------------------------- | -------------------------------------------------------------------------------------- |
| Which alternative runs                           | manifest `alternatives`, profile `selections` | sheppy                                                                                 |
| A node parameter and its default                 | manifest `params:`                            | sheppy                                                                                 |
| A per-robot or per-run value of a node parameter | `profiles/<robot>.yaml` `overrides`, TUI `p`  | sheppy                                                                                 |
| Large, static module configuration               | the module's image                            | the module repo                                                                        |
| State that survives a container restart          | `~/.config/<module>` mounted at `/config`     | the module ([pattern](https://rammp-org.github.io/patterns/storing-state-across-runs)) |
| Setup of the robot outside any node              | `config/robots/<robot>/`, applied by a script | this repo                                                                              |

There is no `config/nodes/`. Node configuration is sheppy's or the module's; a second
place for it would compete with profiles and could not be overridden from the TUI. It can
be added when a concrete need appears.

The robot name joins the two per-robot places: `config/robots/flatbot/` and
`profiles/flatbot.yaml`.

## Layout

```
december_2026/
  sheppy-manifest.yaml
  config/
    robots/
      flatbot/
        network.yaml      # the host's NICs
        oak_d.yaml        # each OAK-D PoE camera's addressing
scripts/
  validate_manifest.py    # existing
  network_config.py       # uv script: applies <robot>/network.yaml with nmcli
  oak_d_config.py         # uv script: applies <robot>/oak_d.yaml with depthai
```

Each script is a self-contained `uv` script: a PEP 723 header pins its dependencies, and
there is no shared module or `pyproject.toml`. Run from the repo checkout on the robot:

```
uv run scripts/network_config.py december_2026/config/robots/flatbot
uv run scripts/oak_d_config.py   december_2026/config/robots/flatbot
```

## `network.yaml`

```yaml
# flatbot's Jetson AGX Orin: what each NIC is for.
interfaces:
  # Kinova Gen3 base, direct cable. No DHCP server on this link; the arm is 192.168.1.10.
  - profile: flatbot-arm
    device: enp3s0
    ipv4:
      method: static
      address: 192.168.1.11/24     # no gateway: the arm's network goes nowhere else
  # ESP32-P4 sensor network: DHCP server at 192.168.4.1; both OAKs are here.
  - profile: flatbot-sensors
    device: eno1
    ipv4:
      method: dhcp
```

- `profile` — the NetworkManager connection the script creates and owns. Profiles it did
  not create are never modified, except for autoconnect (below).
- `device` — the kernel interface name.
- `ipv4.method` — `static` (requires `address` in CIDR form, optional `gateway`) or
  `dhcp` (takes neither).
- Validation: `interfaces` non-empty; `profile` and `device` each unique; unknown keys are
  errors.

## `oak_d.yaml`

```yaml
# OAK-D PoE cameras on flatbot, by device ID (MxID). Keys match manifest node names.
cameras:
  wrist_camera:
    device_id: "194430107187F42E00"
    ipv4:
      method: dhcp
  scene_camera:
    device_id: "19443010219A974800"
    ipv4:
      method: dhcp
```

- Keys under `cameras` are the node names in `sheppy-manifest.yaml`, so which physical
  camera is `wrist_camera` is recorded once.
- `device_id` — a string (quoted so YAML never reads it as a number).
- `ipv4.method` — `dhcp` (no other keys) or `static` (`address` in CIDR form, optional
  `gateway`). There is deliberately no DHCP fallback address: on these bootloaders it
  turns DHCP off.
- Validation: `device_id` unique; unknown keys are errors.

The wrist camera is on DHCP while it is cabled to the sensor network. Once it goes through
the arm's wrist Ethernet it is on the arm's link, which has no DHCP server, and its entry
changes to `static` on `192.168.1.x`.

## Common behaviour

Both scripts:

- take one argument, the robot directory, and read their own file from it;
- validate the whole file before changing anything; any error stops the run;
- are idempotent: compare the current state to the file and change only what differs, so
  a second run reports `unchanged` for everything;
- take `--dry-run`, which prints each change it would make and makes none;
- take `--validate`, which checks the file and exits without touching the host, the
  network or sudo (used by CI);
- print one line per item and exit non-zero if any item failed.

## `network_config.py`

Dependencies: `pyyaml`. Runs as the user and calls `sudo nmcli` for each change, because
`sudo uv run` finds no `uv` on root's PATH and would build a separate root environment.

For each entry in `interfaces`:

1. Check `/sys/class/net/<device>` exists; otherwise the entry fails (another Jetson may
   name its NICs differently).
1. Create the profile if missing (`nmcli con add type ethernet`), or modify it if any
   setting differs: `connection.interface-name`, `connection.autoconnect yes`,
   `ipv4.method` (`manual` or `auto`), `ipv4.addresses`, `ipv4.gateway`. For `dhcp`, the
   addresses and gateway are cleared; for `static` without `gateway`, the gateway is
   cleared.
1. Set `connection.autoconnect no` on every other profile bound to the same device, and
   name each one. They are not deleted.
1. If the device has carrier and the profile is not already active with these settings,
   `nmcli con up` it. If it has no carrier, report that; autoconnect brings the profile up
   when the cable is.
1. Report the device, its active profile and its IPv4 addresses.

A DHCP profile also gets the server's default route (on flatbot, via `192.168.4.1` at
NetworkManager's wired metric). This design does not change that.

## `oak_d_config.py`

Dependencies: `pyyaml`, `depthai` pinned to a version whose bootloader config API is
checked on hardware during implementation (the OAK image ships `depthai-v3` 3.10.0;
discovery by ID is already verified on 3.10.0, config read and write on 2.28.0.0).
`depthai` is imported only when applying, not under `--validate`.

1. Discover all devices once. For each entry in `cameras`, find the one whose device ID
   matches. If none does, the entry fails with the likely causes: powered off, on a subnet
   the host has no address on, or held by a running camera node (stop it first).
1. Open the bootloader with `allowFlashingBootloader=False`. This tool never writes
   firmware.
1. Read the config and set only the network fields. `dhcp`: `staticIpv4` false and
   `ipv4`, `ipv4Mask`, `ipv4Gateway` zero. `static`: `staticIpv4` true and the address,
   mask and gateway (zero when omitted). Every other field is written back as read.
1. If the network fields already match, report `unchanged` and do not write; a write
   restarts the camera.
1. Write the config, read it back, and fail on any mismatch.
1. Wait up to 60 s for the camera to restart, rediscover it by device ID, and report the
   address it came back on. For `static`, a different address is a failure.

## Validation in CI

`validate.yml` gains a step after the manifest check that installs `uv` (pinned
`astral-sh/setup-uv`) and runs both scripts with `--validate` against every
`*/config/robots/*/` directory. A robot config that does not parse fails the PR, the same
way a manifest does. There are no unit tests: what goes wrong here is NetworkManager and
bootloader behaviour, which only hardware shows.

## Documentation

- `CONTRIBUTING.md`: the repo now also holds the scripts that set up a robot before
  sheppy runs; amend "holds no code that runs on the robot" to say so, and describe
  `config/robots/`.
- `december_2026/config/robots/flatbot/README.md`: what flatbot is, its cabling (which NIC
  goes where), and the two commands to run after a reflash.
- **rammp-org.github.io**, new page under Common patterns, *Configuring a deployment*, in
  the shape of the other pattern pages (problem, convention, a deployment that does it,
  what does not belong, related). Its core is the table above. Separate PR in that repo.

## Follow-up: #59 parameter audit (separate PR)

Bring every node in `sheppy-manifest.yaml` in line with the table:

- Values passed in `command:` that a robot or operator might change move to `params:`,
  e.g. the arm driver's `--ip 192.168.1.10`, where the image allows it.
- OAK nodes select their camera by `driver.i_device_id` instead of `driver.i_ip`.
- A `profiles/flatbot.yaml` selects flatbot's alternatives and overrides its per-robot
  values (camera device IDs, arm address).

It is a separate PR because it changes what the demo machine launches, and wants its own
hardware check.

## Hardware check

On snorlax, for flatbot: `--dry-run`, then a real run of each script.

- `network_config`: creates `flatbot-arm` on `enp3s0` and `flatbot-sensors` on `eno1`,
  turns off autoconnect on `kinova` and `Wired connection 1/2`, and `eno1` holds a
  `192.168.4.x` lease. A second run reports `unchanged`. A reboot keeps it.
- `oak_d_config`: both cameras report `unchanged` (already on DHCP with no fallback). Then
  set one to `static` and back to check a write, the read-back and the rediscovery.

## Out of scope

- Diagnostics (state dumps, DHCP captures, discovery listings).
- Bootloader firmware updates.
- The ESP32-P4's DHCP server configuration and lease reservations.
- IPv6.
