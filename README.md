# rammp-deployments

The integration repo for RAMMP. It holds the [sheppy](https://github.com/rammp-org/sheppy) manifests
that say which containers a deployment starts, at which versions and with which flags, plus the
per-robot config and scripts that set a robot up before sheppy starts anything. It builds nothing:
every image a manifest names is published by its own module repo.

Working on this repo (branches, CI, pinning, where parameters go): [CONTRIBUTING.md](CONTRIBUTING.md).
Reviewing a module PR against RAMMP's rules: [REVIEW_RULES.md](REVIEW_RULES.md).

## Layout

```text
december_2026/                    the December 2026 deployment
  sheppy-manifest.yaml            every node and its alternatives (real and mock)
  profiles/<profile>.yaml         which alternative each node runs on a robot, and its overrides
  config/robots/<robot>/          per-robot setup: network, cameras, arm mount, transforms, planner data
  config/robots/current           -> the host's active robot (made by scripts/set_robot.py, git-ignored)
  config/hosts/jetson-agx-orin/   host tuning and the MIB's time server, for the Jetson
  config/dds/                     the fleet's Cyclone DDS config
scripts/                          apply a robot's config to a host; validate the manifests
```

Robots in `december_2026`:

| Robot     | Host      | What it is                                                                          | Docs                                                    |
| --------- | --------- | ----------------------------------------------------------------------------------- | ------------------------------------------------------- |
| `flatbot` | `snorlax` | Mobile test platform: Gen3 on a 30° mount, two OAK-D PoE                            | [README](december_2026/config/robots/flatbot/README.md) |
| `gen_1_5` | `snorlax` | Gen3 on a 30° mount with the sensor puck. Mount and DDS config only; no profile yet | —                                                       |
| `dojo`    | `abra`    | The dojo bench: Gen3 on a flat mount. No profile in the repo yet                    | —                                                       |

## Setting up a robot

The order matters: sheppy and the GHCR login come before the first `sheppy up`, and the robot's config
before any node starts. Every step is safe to re-run. The host is a Jetson on JetPack 6 (L4T R36)
with Docker and the NVIDIA container runtime, and the user that runs sheppy must be in the `docker`
group.

### 1. Install sheppy from `dev`

The manifest needs sheppy `dev` at 0a4c2d2 or later, for `pull_policy: prefer_latest` on the nightly
arm images. No sheppy release has it yet. An older sheppy either refuses to load the manifest or hands
docker a `--pull` value it rejects, and the arm never starts.

```bash
SHEPPY_REF=dev curl -LsSf https://rammp-org.github.io/sheppy/install.sh | sh
sheppy daemon stop   # when upgrading: the next command starts sheppyd on the new version
```

`sheppy --version` prints 1.0.0 for any `dev` build, so it can't tell you whether you're past 0a4c2d2.
Re-running the install is the check.

### 2. Clone this repo

```bash
git clone https://github.com/rammp-org/rammp-deployments.git ~/rammp-deployments
cd ~/rammp-deployments
git switch dev
```

Robots run `dev` for now. `main` is where a demo machine should deploy from
([CONTRIBUTING.md](CONTRIBUTING.md#branches)), but it hasn't been promoted since before flatbot
existed. Switch to `main` after the next promotion.

Run every command below from the repo root. sheppy resolves the manifest's relative bind mounts
(`./config/robots/current/...`) against the manifest's directory, so the checkout can live anywhere.

### 3. Log in to GHCR for the SAM3 image

Every image in the manifest is public except `ghcr.io/rammp-org/rammp-perception/sam3`. It carries the
`facebook/sam3` weights, and the SAM License limits redistribution, so the package is private. Without
a login, the `sam3` node fails to pull (`unauthorized` or `denied`) and every other node starts normally.

1. Get read access to the `rammp-perception/sam3` package. Ask a `rammp-org` admin if
   `docker pull` below is refused.
1. Create a GitHub personal access token (classic) with only the `read:packages` scope. GHCR doesn't
   accept fine-grained tokens.
1. Log in as the user that runs sheppy, not with `sudo`, since docker reads that user's
   `~/.docker/config.json`:

```bash
read -rs GHCR_TOKEN   # paste the token; it stays out of shell history
echo "$GHCR_TOKEN" | docker login ghcr.io -u <github-user> --password-stdin
unset GHCR_TOKEN
docker pull "$(grep -o 'ghcr.io/rammp-org/rammp-perception/sam3:[^ ]*' december_2026/sheppy-manifest.yaml)"
```

The login persists across reboots. The token is the robot's key to the weights, so don't use a token
with wider scopes, and don't push, `docker save` or otherwise copy the image anywhere public.

### 4. Apply the robot's config

Each robot's directory under `december_2026/config/robots/` has its own README with the exact steps
for its hardware. For flatbot, after a reflash, from a session that isn't over a NIC being
reconfigured (Wi-Fi or Tailscale):

```bash
uv run scripts/network_config.py december_2026/config/robots/flatbot   # the host's NICs
uv run scripts/arm_config.py     december_2026/config/robots/flatbot   # the arm's wrist Ethernet port
uv run scripts/oak_d_config.py   december_2026/config/robots/flatbot   # the OAK-Ds' addressing
uv run scripts/set_robot.py      december_2026/config/robots/flatbot   # makes flatbot this host's robot
sudo december_2026/config/hosts/jetson-agx-orin/tuning.sh install       # arm-link host tuning
sudo december_2026/config/hosts/jetson-agx-orin/time_server.sh install  # NTP for the MIB
```

`set_robot.py` is required on every host: the arm, `arm_state_publisher`, `static_tf`, the nightly
planner and every DDS-speaking node read the robot's files through `config/robots/current`. A node
started before it has run leaves an empty root-owned `current` directory behind, which `set_robot.py`
refuses and tells you how to remove. `arm_config.py` needs the arm powered on (it applies
`arm.yaml` through the arm's API), and goes before `oak_d_config.py` since flatbot's wrist camera is
reached through the arm. The scripts take `--dry-run`, and the two host scripts take `check` to report
whether they're in effect.

### 5. Build SAM3's TensorRT engines (once per machine)

Without them SAM3 runs its encoders in PyTorch: same output, slower. They're specific to the GPU and
the TensorRT version, so build them on each machine, and again when the SAM3 image moves to a new
TensorRT. It takes about 20 minutes on an AGX Orin, into the same `sam3_engines` volume the node mounts:

```bash
SAM3_IMAGE="$(grep -o 'ghcr.io/rammp-org/rammp-perception/sam3:[^ ]*' december_2026/sheppy-manifest.yaml)"
docker run --rm --runtime nvidia -v sam3_engines:/cache/engines \
  --entrypoint /ros_entrypoint.sh "$SAM3_IMAGE" ros2 run rammp_sam3 build_engines
```

Once the stack is up, `sheppy logs sam3 -n 500 | grep 'SAM3 loaded'` should say `tensorrt` for both the
detector and the tracker.

### 6. Bring it up

**Attended only:** a profile starts a driver that can move the arm. Someone stays on the e-stop.

```bash
sheppy up flatbot --manifest december_2026/sheppy-manifest.yaml
sheppy status
sheppy logs <node> -n 50
sheppy down
```

The profile is `december_2026/profiles/<profile>.yaml`; each one's header says what it brings up and
what to run first. Open the TUI with `sheppy` in `december_2026/` to change selections or parameters
for one run.

## Updating a robot

```bash
git pull
sheppy up <profile> --manifest december_2026/sheppy-manifest.yaml
```

- Pinned images change only when the manifest does. `:nightly` images (the arm, its description and
  the planner) are re-pulled on every start and fall back to the cached image when the robot is
  offline.
- If the pull touched the robot's `config/robots/<robot>/` files, re-run the script that applies them
  (step 4). Changes under `transforms/`, `curobo/` or `mount.yaml` reach a node only when it restarts
  (`sheppy restart <node>`).
- A robot with local, uncommitted calibration edits: `git stash`, `git pull`, `git stash pop`.
  Don't overwrite its checkout from a workstation with a sync that deletes, since it would take the
  edits with it.
