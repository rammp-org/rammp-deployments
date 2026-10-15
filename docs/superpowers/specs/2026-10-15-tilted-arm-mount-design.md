# Tilted arm mount: one gravity-aligned frame across driver, ROS 2 and cuRobo

**Date:** 2026-10-15 **Status:** draft for review **Builds on:**
[robot configuration](2026-10-15-robot-config-design.md) (#61) **Related:**
[RAMMP-CuRobo#20](https://github.com/rammp-org/RAMMP-CuRobo/issues/20) (plan against our
model), [kinova-gen3-ros2#14](https://github.com/rammp-org/kinova-gen3-ros2/issues/14)
(driver reduces the model itself),
[kinova-gen3-driver#40](https://github.com/rammp-org/kinova-gen3-driver/issues/40)
(gravity-comp hold droops)

## Problem

flatbot and gen_1_5 now mount the arm on `new_adapter_plate_v3`, which leans it **30°
forward** (toward the chair's +X). dojo's arm is level. Nothing in the stack knows:

- **Driver.** Pinocchio applies gravity along −Z of the URDF root, which today is the
  arm's own `base_link`. On a tilted arm that is 30° off, so every mode that feeds
  forward `g(q)` (joint/Cartesian impedance, torque, and the compliant modes built on
  them) commands the wrong holding torque, and `ArmState.ee_wrench` reports a phantom
  wrench. **flatbot runs like this today** (see [Until this lands](#until-this-lands)).
- **cuRobo.** It has no gravity; "up" exists only as the assumption that `base_link` +Z
  is vertical. `HOLD_LEVEL` frees rotation about base Z, so on a tilted base it tips the
  cup it was asked to keep level, and the planner's own verifier measures against the
  same tilted axis and approves the plan. The code already says so
  (`constraints.py`, `PlanToPose.action`).
- **ROS 2.** Cartesian goals and state are in `base_link`, so "+Z" is up the slope.

## Decisions

| Question                     | Decision                                                                                      |
| ---------------------------- | --------------------------------------------------------------------------------------------- |
| Frame for all Cartesian I/O  | `arm_mount`: Z against gravity (when the chair is level), X forward, origin at the arm's base |
| Where the mount is declared  | `config/robots/<robot>/mount.yaml`, rotation only                                             |
| How a host picks its robot   | `config/robots/current` symlink, set by `scripts/set_robot.py`, git-ignored                   |
| Who renders the model        | `arm_state_publisher` only, at launch; it publishes `/arm/robot_description`                  |
| How driver and cuRobo get it | Both read `/arm/robot_description` (latched) at startup; no URDF file is passed to either     |
| Arm vs. chair names          | Arm links and joints get the `arm_` prefix; the chair owns `base_link` (REP-105)              |
| Mount translation            | Not modelled. `arm_mount` shares the base's origin; chair → `arm_mount` is calibration's job  |

## Frames

```
base_link (chair, future) ──[calibration / static TF, out of scope]──▶ arm_mount
arm_mount ──[fixed: mount rpy]──▶ arm_base_link ──▶ arm_shoulder_link ──▶ … ──▶ arm_end_effector_link ──▶ arm_tool_frame
```

- `arm_mount` is the root of the arm's URDF. Its origin is `arm_base_link`'s origin, so the
  only thing between them is the mount rotation, and the arm stack never needs the plate's
  height or offset.
- On dojo the mount is identity: `arm_mount` and `arm_base_link` coincide and every
  Cartesian number is unchanged. On flatbot and gen_1_5 the rotation is +30° about Y, so
  `arm_base_link`'s +Z points along (0.5, 0, 0.866) in `arm_mount`.
- **`arm_mount` is level with the chair, not with gravity.** On a ramp it is off by the
  ramp angle, and so are gravity compensation and `HOLD_LEVEL`. See
  [Known limitations](#known-limitations).

## Data flow

```
config/robots/current/mount.yaml ──bind-mount──▶ arm_state_publisher (xacro at launch)
                                                     │ /arm/robot_description
                                                     │ (latched, 13-DOF, root arm_mount)
                                    ┌────────────────┼────────────────┐
                                    ▼                ▼                ▼
                    driver (reduce to 7)          cuRobo        /tf (arm_* frames)
```

One render, one model. The driver and cuRobo cannot disagree with each other or with TF.
Launch order does not matter: the topic is latched, so each consumer gets the model
whenever it appears, and fails after a timeout with an error naming
`arm_state_publisher` if it never does.

## rammp-deployments

### `mount.yaml`

```yaml
# config/robots/flatbot/mount.yaml
robot: flatbot              # must equal the directory name
# Orientation of arm_base_link in arm_mount. arm_mount is gravity-aligned (on level
# ground) and shares arm_base_link's origin, so there is no translation.
rpy_deg: [0.0, 30.0, 0.0]   # new_adapter_plate_v3: 30° forward lean
```

| robot   | host    | `rpy_deg`          |
| ------- | ------- | ------------------ |
| dojo    | abra    | `[0.0, 0.0, 0.0]`  |
| flatbot | snorlax | `[0.0, 30.0, 0.0]` |
| gen_1_5 | —       | `[0.0, 30.0, 0.0]` |

Validation (in `set_robot.py` and CI, alongside the existing robot-config checks):
`robot` matches the directory, `rpy_deg` is three finite numbers, unknown keys are errors.

### The active robot: `config/robots/current`

The repo describes every robot; which one a host *is* belongs to the host, not to git. So
the choice is a git-ignored symlink, `december_2026/config/robots/current → flatbot`,
applied by a script in the style of `network_config.py`:

```
uv run scripts/set_robot.py december_2026/config/robots/flatbot   # make flatbot active
uv run scripts/set_robot.py december_2026/config/robots           # print the active robot
```

- Validates the target's `mount.yaml` before touching the link.
- Replaces the link atomically and prints the change (`dojo → flatbot`). Idempotent;
  `--dry-run` prints without changing anything.
- Refuses to replace a `current` that is a real directory rather than a link, and says
  how to remove it. (Docker creates an empty directory at a missing bind-mount source,
  so a sheppy start before `set_robot` leaves one behind.)

This is tooling beside sheppy rather than in it. sheppy profiles can override node
params but not `volumes`, and inline `container` blocks are not interpolated, so a
profile cannot name the robot's directory. Extending sheppy (profile variables) was
considered and deferred to keep this change simple.

The convention this sets: **nodes read embodiment facts from `config/robots/current`**.
The mount is its first user; moving other per-host paths onto it (e.g. teleop's
`/home/abra/.config/rammp_teleop`) is not part of this change.

### Robot-config spec

Add a row to the "where each kind of setting lives" table:

| Setting                                          | Lives in                                                  | Owner     |
| ------------------------------------------------ | --------------------------------------------------------- | --------- |
| Embodiment facts that nodes read (the arm mount) | `config/robots/<robot>/`, mounted read-only via `current` | this repo |

### Manifest

- `arm_state_publisher`: mount `./config/robots/current:/config/robot:ro`, and launch with
  `mount_file:=/config/robot/mount.yaml`.
- `kinova_gen3_driver`: drop `--urdf` and its path.
- Bump the `kinova-gen3-ros2` and `rammp-curobo` image tags.
- Comment that `arm`, `arm_state_publisher` and `planner` are selected together: the
  driver and planner cannot start without `/arm/robot_description`.
- The flatbot README's "After a reflash" steps gain the `set_robot.py` line.

## kinova-gen3-ros2

### Description

- The xacro root link `world` becomes `arm_mount`.
- `prefix` is always `arm_` for this description.
- New required xacro arg `mount_file`, read with `xacro.load_yaml`; `rpy_deg` (converted
  with `radians()`) goes on `load_arm`'s `<origin rpy>`.
- New fixed link `arm_tool_frame` on `arm_end_effector_link`, at the same offset as
  cuRobo's bundled model's `tool_frame` (≈0.120 m beyond the flange; take the exact
  transform from that URDF). Upstream `kortex_description` has no `tool_frame`, and
  cuRobo positions every `PlanToPose` goal there.
- **Build-time model expansion is removed**, so the image holds no flat model to pick up
  by mistake. `description.launch.py` renders at launch, takes `mount_file` as a
  **required** argument (no default), strips the `ros2_control` block as
  `expand_model.py` does today, and logs `embodiment: <robot> (mount rpy_deg [...])`
  so the active robot is the first thing in the log.
- RSP's `robot_description` is remapped to `/arm/robot_description`. `/tf` and
  `/joint_states` stay global.
- A small render script stays available for bench use (`--mount <file> --out <urdf>`),
  for the driver's standalone apps.

### Driver node (`kinova_gen3_node`)

- `--urdf` is removed. At startup, before the RT loop exists, the node reads
  `/arm/robot_description` (transient-local), with a timeout and an error naming
  `arm_state_publisher`.
- Builds `Dynamics` from that XML; the driver reduces it to the 7 arm joints (below).
- `--ee-frame` defaults to `arm_end_effector_link`. A frame missing from the model already
  throws in `Dynamics`.
- `/joint_states` names come from the model (the 7 arm joints) plus the gripper joint,
  `arm_robotiq_85_left_knuckle_joint`, checked to exist in the URDF at startup. The
  hard-coded list in `ros2_backend.cpp:189,218` is removed.
- `GoToEEPose` requires `frame_id == "arm_mount"` (`goto_ee_pose_server.h:25`). A goal in
  `base_link` gets its own message:
  `frame_id 'base_link' is no longer accepted: Cartesian goals are in 'arm_mount' (gravity-aligned); arm_base_link is tilted 30° on flatbot and gen_1_5`.
  Like every `PlannedMoveServer` rejection, this reaches the server log; the client only
  sees "rejected" (see [Known limitations](#known-limitations)).

## kinova-gen3-driver

- **No change to the dynamics maths.** Pinocchio attaches the URDF root to its universe
  frame and applies gravity there; with `arm_mount` as the root, gravity compensation,
  FK, the Jacobian, `ee_wrench` and Cartesian setpoints are all in `arm_mount`.
- `Dynamics` gains a constructor from URDF XML (`pinocchio::urdf::buildModelFromXML`) and
  reduces the model to the arm (`pinocchio::buildReducedModel`), locking every joint not
  on the chain from the root to the EE frame at `q = 0`. For the gripper that is exactly
  today's frozen file: `type="fixed"` and locking at zero both lump the child bodies into
  the parent (kinova-gen3-ros2#14). The `nv == 7` check stays and now applies after
  reduction.
- The standalone apps **require `--urdf`**: no default, a usage error without it. A flat
  default would silently apply level gravity on flatbot.
- `models/gen3_7dof_2f85.urdf` remains as a test fixture only.
- New test: for a set of configurations, `g(q)` from the fixture with a fixed root joint
  rotated +30° about Y equals `g(q)` from the flat fixture with Pinocchio's gravity vector
  rotated by −30° about Y. This pins that the mount is handled by the model, not by luck.
- New test: the reduced 13-DOF model's `g(q)`, FK and Jacobian match the 7-DOF frozen
  fixture's.

## rammp-interfaces-ros2

No fields change; the documented frames do. Version bump, since the meaning changes.

| File                           | Change                                               |
| ------------------------------ | ---------------------------------------------------- |
| `action/GoToEEPose.action:1-2` | `base_link` → `arm_mount` (still rejected otherwise) |
| `msg/EeState.msg:13`           | `base_link` → `arm_mount`                            |
| `msg/PoseSetpoint.msg:1`       | "the base frame" → `arm_mount`                       |
| `msg/TwistSetpoint.msg:1`      | "base frame" → `arm_mount`                           |

## RAMMP-CuRobo

This builds on the URDF input already being added to core; it does not design that input.
What this change needs from and adds to it:

- The ROS node waits on `/arm/robot_description` (latched, timeout, clear error), writes it
  to a temp file and passes it to core. Core stays file-based.
- Kinematics: `base_link: arm_mount`, `ee_link: arm_tool_frame`. The 6 Robotiq joints are
  locked open with cuRobo's `lock_joints`, the configuration the sphere shell assumes.
- `joint_names` becomes `arm_joint_1 … arm_joint_7` (`config.py:21-28`, `gen3.yaml:16`,
  the robot YAML). The retract seed's values are unchanged.
- Joint limits now come from our URDF: the same values the driver enforces.
- **Spheres are moved, not re-tuned.** Arm-link spheres take the `arm_` names (same
  upstream link frames). The gripper shell and pad spheres move from NVIDIA's
  `robotiq_arg2f_*` links to upstream `robotiq_85_*` links by re-expressing each centre
  through the fixed transform between the old link and the new one, with the gripper
  open. `bake_robot_config.py` is updated to do this.
- Verification: at a set of configurations, every sphere's position in `arm_mount` matches
  the old model's within 1 mm, `arm_tool_frame` matches NVIDIA's `tool_frame` within 1 mm
  and 0.1°, and the existing self-collision checks pass.
- `PlanToPose.target` is documented as `arm_mount`. The `HOLD_LEVEL` caveat in
  `constraints.py` and `PlanToPose.action` ("silently false the moment it is not") is
  replaced with the actual limitation: level relative to the chair, not the world. The
  hold maths and the verifier do not change.
- **Not in this change:** per-robot collision worlds (a separate PR), and wrist-camera
  spheres.

### Ordering on flatbot

Once cuRobo plans in `arm_mount`, flatbot's current world is wrong: its `pedestal` box is
axis-aligned under a base that is now tilted 30°, so start states are likely to collide
with it. **On flatbot, the cuRobo change deploys together with the per-robot world PR.**
dojo is unaffected (identity mount).

## What changes for consumers

| Consumer                               | dojo                                                                   | flatbot, gen_1_5                                            |
| -------------------------------------- | ---------------------------------------------------------------------- | ----------------------------------------------------------- |
| Joint-space anything                   | nothing                                                                | nothing                                                     |
| `/joint_states` readers that use names | `joint_N` → `arm_joint_N`                                              | same                                                        |
| `ExecuteJointTrajectory` callers       | nothing (the driver ignores `joint_names` and applies points in order) | same                                                        |
| `GoToEEPose` callers                   | `frame_id` `base_link` → `arm_mount`                                   | same, and the numbers change (intended)                     |
| `/setpoint/pose`, `/setpoint/twist`    | nothing                                                                | reinterpreted in `arm_mount`, silently (no frame field)     |
| `/ee_state` readers                    | nothing                                                                | numbers change (intended)                                   |
| Recorded Cartesian data                | nothing                                                                | re-record: saved EE poses, the Quest's `quest_r_align.json` |

## Rollout

1. **dojo (abra), the no-op check.** Land the driver, kinova-gen3-ros2 and interfaces
   changes; `set_robot.py … dojo`. Expect: only names change. Compare `g(q)` and FK
   against the current model at a handful of poses; they must match.
1. **flatbot (snorlax).** `set_robot.py … flatbot`. Expect:
   - the gravity-comp hold stops drooping (the pass criterion in #40);
   - `arm_base_link`'s +Z in `arm_mount` has a positive X component (leans forward);
   - teleop +Z moves the gripper straight up;
   - then re-record the Quest alignment and any saved Cartesian poses.
1. **cuRobo on flatbot**, together with the per-robot world PR.

### Until this lands

flatbot is running the level model on a 30° arm. Keep it on joint position mode; don't
trust gravity-comp holds, impedance or torque modes, or `ee_wrench` on it.

## Known limitations

- **Ramps.** `arm_mount` is level with the chair. On a slope, gravity compensation and
  `HOLD_LEVEL` are off by the slope angle. Fixing this needs live gravity (the base
  IMU), which is out of scope.
- **Wrong active robot.** `current` pointing at the wrong robot is not detected; the
  startup log line is the only signal. A check against the base IMU was considered and
  rejected: it would refuse to start on a ramp and depends on a noisy sensor.
- **Rejections the client can't read.** A ROS 2 goal rejection carries no reason; the
  `base_link` message reaches the server log only.
- **Streaming setpoints have no frame.** An un-migrated `/setpoint/pose` or
  `/setpoint/twist` client on flatbot is reinterpreted in `arm_mount` without warning.

## Out of scope

The base IMU check; `ControlConfig.SetGravityVector` (the driver runs low-level
servoing, where the firmware's gravity vector is unused); wrist-camera collision
spheres; per-robot cuRobo worlds; the chair → `arm_mount` transform; sheppy profile
variables; the driver's positional handling of `joint_names`.
