# Review rules

What a reviewer checks on a RAMMP capability engine PR or branch. Written for
an agent, readable by a person. The `rammp-review` skill in
`skills/rammp-review/` runs a review against this file.

The rules are known examples, not the whole list. Each section opens with the
principle the rules protect. Check the rules, then look for anything else in
the diff that breaks the same principle, and report it under that section.

## How to review

1. Read the whole diff, then enough of the surrounding code to follow each
   changed path end to end: the `CapabilityEngine` base class, the launch file,
   the fragments, the Dockerfile.
1. Check each section below.
1. Before reporting a finding, confirm it by tracing the actual code path.
   Report only what you are confident is real. A false finding costs the
   maintainer more than a missed one.
1. Skip what CI already enforces in rammp-kitchen-capability-engines: required
   files, fragment schema, mock/real topic parity, pinned `FROM`, CODEOWNERS,
   the `/status` topic, and the mock exiting on SIGTERM. If CI has not run on
   the PR, say so in one line.
1. Find the target. A PR into `main`, or a change to a deployment manifest, is
   a deployment: M2 applies in full. Anything else gets a one-line "pin before
   deploy" note instead.

## Severity

| Level          | Meaning                                                       |
| -------------- | ------------------------------------------------------------- |
| **must-fix**   | Wrong or unsafe. Breaks the robot, the UI, or another engine. |
| **should-fix** | Violates a rule or pattern here. Works today, costs later.    |
| **ask**        | Needs the maintainer's judgment. Give a recommendation.       |
| **consider**   | Optional improvement.                                         |

## Output

The review is exactly these parts, in order, and nothing else:

1. The `Review against …` link line.
1. Only if they apply, one line each: `CI has not run.` and
   `Pin before deploy: <images>.`
1. The severity headings in the order above, each followed by its findings.
   Omit a heading with no findings.

Each finding is two lines:

- Line 1: `` `path:line` `` `[rule ID]` (or `[section]` for a finding outside
  the numbered rules), then the defect and its consequence in one sentence of
  at most 20 words.
- Line 2: `Fix:` (or `Recommend:` for **ask**), then one line of at most 20
  words or one code span.

One finding per root cause. When one cause shows up on several lines, cite the
first line and name the others in the same sentence.

```markdown
Review against [RAMMP review rules](https://github.com/rammp-org/rammp-deployments/blob/dev/REVIEW_RULES.md)

**must-fix**
- `capabilities/pour/pour/node.py:88` [C2] Cancel raises but the trajectory goal keeps running.
  Fix: `self._traj.cancel_goal_async()` and wait for it before re-raising.

**should-fix**
- `capabilities/pour/pour/node.py:31` [R1] Wrist camera subscribed in `__init__`, never released.
  Fix: subscribe at the start of `execute()`, destroy in `finally`.

**ask**
- `capabilities/pour/pour/node.py:12` [R2] Loads its own YOLO model; scene segmentation already runs on the Jetson.
  Recommend: use `/perception/object_detections` unless this model is required.
```

A review with no findings is one line:

```markdown
Review against [RAMMP review rules](https://github.com/rammp-org/rammp-deployments/blob/dev/REVIEW_RULES.md): no findings.
```

## Messages

**Principle.** Humble has no type hash. Under Cyclone, a node built against a
different version of a message still connects and silently misreads the bytes.
Nothing fails loudly, so the types and names have to be right by construction.

- **M1** (must-fix) Use the types in `rammp-interfaces-ros2`. Never redefine a
  shared type locally. Interfaces only one capability uses stay in that
  capability's package.
- **M2** (must-fix) Interfaces come from the `rammp-base` image, or are built
  from a tag or commit SHA. Never from a branch (`git clone --branch dev`).
  Images may be `:dev` or `:nightly` while developing; a deployment pins every
  image to a release tag or digest.
- **M3** (must-fix) Every topic the code uses appears in the fragment's
  `publishes`/`subscribes`, and exists in the deployment manifest under that
  exact name and type. Every service or action it calls exists under that exact
  name in the interface docs of the node that serves it; fragments list topics
  only. A name off by one segment (`depth/image_raw` vs `depth/image_rect_raw`)
  connects to nothing and raises no error.

## Resources

**Principle.** Engines share one Jetson and sit idle most of the time. Anything
an engine holds while idle (subscriptions, GPU memory, CPU loops, disk I/O) is
taken from every other node, including the arm driver.

- **R1** (should-fix) Subscribe only while working. Camera, point cloud, arm
  state and TF subscriptions are created when a goal starts and destroyed in a
  `finally` and on `hibernate`, never left open from `__init__`. Caches are
  set to `None` on stop. See
  [subscribing only while working](https://rammp-org.github.io/patterns/subscribing-only-while-working).
- **R2** (ask) An engine that runs its own detector or segmenter duplicates
  work already running on the Jetson (rammp-perception: YOLOE, RF-DETR, SAM3,
  boxer, published on `/perception/object_detections`). Recommend the
  existing node unless this model is required. If it is,
  recommend whether to keep it loaded or load per goal, from the model's size
  against Jetson memory, its load or TensorRT build time, and how often the
  capability runs.
- **R3** (must-fix) Release what you start. A tracking session, stream, or
  other request made to another node is stopped in a `finally`, so a cancel,
  failure, or exception does not leave it running.
- **R4** (should-fix) Wait without burning CPU. No `spin_once` or
  `spin_until_future_complete` inside a callback. No loop without a sleep.
  Every wait has a deadline. No timer faster than the data it reads. See
  [waiting without spinning](https://rammp-org.github.io/patterns/waiting-without-spinning)
  and
  [publishing at the rate of the data](https://rammp-org.github.io/patterns/publishing-at-the-rate-of-the-data).
- **R5** (should-fix) Bound memory and logs. No image or point cloud history
  that grows without limit. No log line per frame; use
  `throttle_duration_sec`, or log on change.
- **R6** (should-fix) No disk writes in a loop: images, `np.save`, file logs,
  recordings. Allowed only behind an explicit debug parameter that defaults to
  off.

## Arbitration

**Principle.** Exactly one owner commands the arm at a time, and the
orchestrator decides who. An engine that acts outside its turn can move the arm
while someone else owns it.

- **A1** (must-fix) Engines never call `/acquire_control`, `/release_control`
  or `/revoke_control` on the arm. The orchestrator pushes the execution token
  to the engine's own `~/acquire_control` before dispatching a goal and
  withdraws it through `~/release_control`. The engine puts that token and its
  `sender_id` on every arm command, and drops it on release.
- **A2** (must-fix) `required_resources` lists every resource `execute()`
  commands (`arm`, `gripper`, `base`), spelled as the deployment's resource
  catalog spells them. Nothing more, so other engines are not blocked.
- **A3** (must-fix) Hardware moves only inside `execute()`. Never from
  `activate`, a timer, or a subscription callback.

## Cancel and pause

**Principle.** The user must be able to stop the robot at any moment, and the
system must end up knowing exactly what happened. A cancel that is slow,
swallowed, or leaves the arm moving is a safety bug.

- **C1** (should-fix) Call `ctx.checkpoint()` between steps and inside every
  wait loop. Flag any path where more than about 2 s can pass between
  checkpoints: long `time.sleep`, a blocking `future.result()`, a long
  synchronous call.
- **C2** (must-fix) On `Cancelled`, cancel the in-flight arm, gripper or base
  goal, wait for it to settle, then let `Cancelled` propagate. Never swallow it
  (`except Exception:` around a `checkpoint()`), and never return success after
  a cancel.
- **C3** (must-fix) Pass `pausable=True` to `ctx.progress()` only for steps
  where a checkpoint is polled and stopping mid-step is safe. The default is
  `True`, so look for steps that never set it.
- **C4** (should-fix) Fail with `EngineError(code, message, recoverable=…)`.
  No `sys.exit`, no bare `raise RuntimeError`, and no goal or cancel handling
  of your own in place of the base class's.
- **C5** (must-fix) An arm goal that comes back aborted or rejected (after an
  e-stop, or after losing control) ends the action as failed. No automatic
  retry loop.
- **C6** (should-fix) World state records what actually happened. A cancelled
  or failed grasp does not write `holding`.
- **C7** (must-fix) A cancel, failure or exception leaves the hardware safe. A
  step that puts the robot in a state it must not stay in (a tilted cup, an
  open gripper under load, an arm inside an appliance) is undone in a
  `finally`, or the step is not pausable and its result says what was left.

## Orchestrator and engine

**Principle.** The orchestrator calls every engine in sequence and with
timeouts. One slow, dishonest, or malformed engine stalls selection for all of
them.

- **O1** (must-fix) `activate` is fast, has no side effects, is safe to call
  repeatedly, and tells the truth. No motion, no model loading, no blocking
  calls. It is called on every selection and after every action, with a 10 s
  timeout that fails `select_object` for every engine. An action reported
  `available` must be runnable.
- **O2** (should-fix) `hibernate` is idempotent and drops per-selection state,
  including R1's subscriptions. It is called after every non-sticky action and
  on every new selection.
- **O3** (ask) An action that can run longer than 120 s is cancelled by the
  orchestrator with `execute_timeout`. Flag it so the maintainer can decide.
- **O4** (should-fix) Validate `params`. Bad input raises
  `EngineError("bad_params", <what is wrong>)`, not a `KeyError` or `ValueError`
  that surfaces as a bare `exception`.
- **O5** (must-fix) Any JSON the engine sends (`suggested_params_json`,
  `payload_json`, world-state updates) is built with `json.dumps`, never by
  string formatting. Invalid suggested params break selection on that object
  for every engine.
- **O6** (should-fix) Check the return value of `ctx.write_world_state()` and
  `ctx.update_object()`. Keys outside the world-state schema (gripper, doors,
  objects, arm, base_pose) are rejected.

## UI and engine

**Principle.** The user sees the engine only through what it reports. If the
report is wrong, the user makes decisions on a robot they cannot see.

- **U1** (should-fix) `action_id` reads well as a button label: `fill_juice`,
  not `act_3`. The UI shows it raw.
- **U2** (should-fix) Progress `pct` is a fraction from 0 to 1 and never goes
  backwards. `message` is short and readable by the user.
- **U3** (must-fix) A question to the user mid-action (`RequestUserInput`) has
  valid `panel_json` that always includes `buttons`, a timeout, and a cancel
  path. Hold `ui_control` only while driving the UI. Never use it to choose
  which action runs; that is `activate`'s job. A malformed panel fails silently
  and hangs until the 600 s timeout.
- **U4** (should-fix) `error_code` names the failure (`grasp_slipped`, not
  `error`), and `recoverable` is true only if retrying can succeed.

## Dockerfiles

**Principle.** The image is what runs on the robot. It has to join the same
DDS graph as everything else, stop when told, and use the Jetson's GPU.

- **D1** (must-fix) Never set `RMW_IMPLEMENTATION` or `CYCLONEDDS_URI`: not in
  the Dockerfile, launch file, fragment, or code. `rammp-base` owns them, and
  overriding them takes the node off the fleet's DDS graph.
- **D2** (must-fix) The real path stops on `docker stop` within 5 s, not only
  the mock CI tests. `ros2 launch` as PID 1 needs `STOPSIGNAL SIGINT`. No
  `bash -c` wrapper that does not forward signals.
- **D3** (must-fix) On `rammp-cuda`, no `torch` or `torchvision` in
  requirements and no `pip install torch`. The base ships the Jetson CUDA
  build; pip replaces it with a PyPI build that has no Jetson CUDA, so the
  model silently runs on CPU or fails to import. For any package that depends
  on torch (ultralytics, for example), ask the author to confirm
  `torch.cuda.is_available()` inside the built image.
- **D4** (should-fix) Keep the image clean: `--no-install-recommends`, remove
  `/var/lib/apt/lists`, no `.git` in a layer, no `--symlink-install`.
- **D5** (consider) Set `OPENBLAS_NUM_THREADS=1` for numpy-heavy nodes. It cut
  one perception node from 5 cores to 0.3.

## General correctness

**Principle.** The code does what it claims, on every path, including the
failure paths.

No list. Look for the bugs any careful reviewer would: logic errors, races
between callbacks and `execute()`, unhandled `None`, wrong frames or units,
parameters read before they are declared, exceptions that escape a callback,
mock and real paths that drift apart.
