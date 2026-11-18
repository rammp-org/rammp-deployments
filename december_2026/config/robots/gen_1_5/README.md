# gen_1_5

The Gen1.5 wheelchair: a Kinova Gen3 with a Robotiq 2F-85 on the 30° adapter plate
(`mount.yaml`), the sensor puck at the wrist, run from `snorlax` as
[flatbot](../flatbot/README.md) is. Profiles: `gen_1_5` (the chair in every planner world)
and `gen_1_5_empty_world` (the arm alone).

## Shared with flatbot

The host, the arm and the cameras are flatbot's, so these are symlinks to flatbot's files:
`cyclonedds.xml`, `network.yaml`, `arm.yaml`, `oak_d.yaml`. Cabling, applying them after a
reflash, and switching the wrist camera are in [flatbot's README](../flatbot/README.md).
Files the containers read through `config/robots/current` (`curobo/`, `transforms/`) are
copies: a link out of this directory does not resolve inside them.

## Its own

- `curobo/`: the planner config. The chair is a 3D scan of the Gen1.5
  (`wheelchair-clean-mm.stl`, 2026-11-17) boxed by this repo's `scripts/scan_to_boxes.py`
  into the seat layer: one box under the seat, one for the backrest, 12 for the rest
  (armrests, joystick, front casters). The occupant is flatbot's tape-measured profile with
  the legs cut to our user's, 7 in beyond the cushion's front. `mebot_layers.yaml` is the
  input; regenerate the layers from a RAMMP-CuRobo checkout:

  ```bash
  make layers DATA=<this directory>/curobo/mebot_layers.yaml OUTDIR=<this directory>/curobo
  ```

  There is no Gen1.5 URDF, and the generator needs one: its inputs pin the Gen1.2 drive
  base at the Gen1.5's drive axle, which puts the floor on the scanned ground. Its
  `layer_mebot_platform.yaml` is written but not in `planner.yaml`; the under-seat box
  covers the base. `robot.yaml` and `arm_kinematics.json` equal flatbot's: the same arm,
  mount and puck.

- `transforms/`: placeholders until the chair is calibrated. `world.yaml`'s
  `base_link -> arm_mount` is estimated from the scan, with `base_link` where flatbot's MIB
  puts it; `cameras.yaml` is flatbot's, right for the wrist camera on the puck but not for
  the scene camera, which sits at the joystick module on this chair.

## Not done yet

- The cushion and the footrests are not in the planner's chair.
- The scene camera's mount and `base_link -> arm_mount`: measure or calibrate them on the chair.
- A rest pose clear of the chair: flatbot's puts the arm's upper arm 100 mm into the
  arm-side armrest, so the planner refuses plans that start there.
