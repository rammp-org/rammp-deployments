# perf: compute benchmark of the perception stack

Measures the december_2026 stack on one Jetson (rammp-deployments#106): each always-on detector candidate
next to SAM3, the camera, the planner and BoxerNet, as sheppy runs them. Results and conclusions are on the
docs site (Perception compute budget).

| File            | What it does                                                                                       |
| --------------- | -------------------------------------------------------------------------------------------------- |
| `bench.py`      | runs the matrix: writes a manifest + profile from `december_2026/`, `sheppy up`, measures, saves JSON |
| `ros_probe.py`  | topic rate and camera-to-result latency, SAM3 tracking and detect calls; runs in the SAM3 image     |
| `owl_node.py`   | OWLv2 and NanoOWL as scene segmenters (no image exists for either); bench only                     |
| `quality/`     | detector quality check against SAM3 (YOLOE, RF-DETR, NanoOWL); see its README                     |
| `report.py`     | markdown tables from the results                                                                   |
| `grab_frame.py` | one scene camera frame as a JPEG, to see what the bench looked at                                  |
| `replay_camera.py` | records the scene camera (color, registered depth, intrinsics, TF) and replays it where none is attached |
| `gpu_load.py`   | synthetic GPU load at a set duty cycle, standing in for the planner and modules                    |
| `profile_segmenter.py` | one scene segmenter outside ROS on recorded frames: per-stage time, CPU per thread, cProfile |
| `patches/`      | node files from open rammp-perception PRs, mounted over the image's by the runs that test them     |
| `cyclonedds-local.xml` | DDS on loopback for every node (all on one Jetson)                                         |

**flatbot (OAKs, RF-DETR always on):** `reports/2026-10-27-snorlax-oak/` has the stack at the OAK's 15 fps, measured
next to a live sheppy stack with plain `docker run`. `bench.py` below still assumes the dojo's Gemini 336L at 30 fps,
abra's camera TF and BoxerNet's unmerged `max_rate_hz` patch, so it does not run flatbot's stack as is.

## Run (abra)

From this directory on the workstation:

```bash
uv run --project ~/.claude/skills/hardware-loop ~/.claude/skills/hardware-loop/scripts/hil.py sync
uv run --project ~/.claude/skills/hardware-loop ~/.claude/skills/hardware-loop/scripts/hil.py exec -- \
  bash -c 'cd ~/perf-bench && python3 perf/bench.py all && python3 perf/report.py results'
```

`sync` mirrors `perf/` and `december_2026/` into `~/perf-bench` only; abra's `~/rammp-deployments` is not
touched. `python3 perf/bench.py --list` shows the runs; `all` is about 60 runs, three hours. Each run brings the
stack to its profile, waits for every measured topic (a first start builds TensorRT engines), warms up 15 s
and measures 60 s.

On a machine with no camera (snorlax before its OAKs were cabled), record on abra and replay:

```bash
python3 perf/replay_camera.py record recording.npz          # on abra, scene camera up
BENCH_CAMERA=replay BENCH_RECORDING=~/perf-bench/data/recording.npz \
  BENCH_SAM3_WEIGHTS=~/perf-bench/data/models/sam3 python3 perf/bench.py ...
```

## What is measured, and how

- **Rate and latency**: `ros_probe.py` subscribes raw and decodes only the leading header. Latency is receive
  time minus `header.stamp`. Every node copies the camera frame's stamp, so latency runs from camera capture to
  result, camera driver included.
- **System**: `tegrastats` every 500 ms (GPU load `GR3D_FREQ`, per-core CPU, RAM, the three power rails,
  junction temperature), plus per-container CPU and memory from cgroup v2. Bench host processes are read from
  `/proc`.
- **Stand-ins**: the arm and its TF are mocks, because abra's arm was not on the network. The wrist camera
  is absent. The mount TF is abra's calibration, which BoxerNet needs for gravity. Containers use the loopback
  Cyclone config shipped here.
- **Config through the manifest**: `sam3_hz` and `boxer_hz` go into the alternative's `params`, not a profile
  override. sheppy drops an override for a param the alternative does not declare, so a profile override
  would silently do nothing.

Needs on the Jetson: sheppy at `~/.local/bin`, the images the manifest pins, and for the OWL runs
`~/RAMMP-box-opening` (OWLv2) and `~/detector-bench` (NanoOWL venv and engine), which only abra has.
