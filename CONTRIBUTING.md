# Contributing

This repo follows the RAMMP module workflow, but it is not a module: it builds
nothing that sheppy runs. It is the **integration repo** — the sheppy manifests
that say which containers a deployment starts, at which versions, with which
flags, and the robot configs and scripts that set a robot up before sheppy
starts anything. Everything a manifest names is built and published somewhere
else.

That shapes what the gates can honestly claim. CI can prove a manifest is
something sheppy will load; only hardware can prove it is something that works.

## Pre-commit hooks

Style is enforced automatically before each commit — Python via Ruff, Markdown
via mdformat, the workflows via actionlint, plus general file hygiene and a YAML
parse of every manifest.

Run this once after cloning:

```bash
uv tool install pre-commit
pre-commit install
```

Without `uv`: `pip install pre-commit && pre-commit install`.

Hook revisions are pinned. Updating them is a deliberate PR
(`pre-commit autoupdate`), never silent drift.

## Branches

- `main` — what a demo machine deploys from. Updated from `dev` by PR.
- `dev` — staging ground. Feature PRs land here.
- `feature/<brief-description>` — forked from the latest `dev`. Use
  `bug/<brief-description>` for fixes.

## Merge strategy

**It differs by target branch, and getting it wrong on the promotion PR costs
you the next promotion's reviewability.**

- `feature/*`, `bug/*` → `dev`: **squash**. One commit per unit of work keeps
  `dev` readable, and the branch's intermediate commits are noise once it lands.
- `dev` → `main`: **merge commit, never squash.** A squash creates a brand-new
  commit on `main` instead of recording `dev`'s commits as ancestors, so the two
  long-lived branches stop sharing history. Nothing breaks and no content is
  lost — but GitHub diffs a PR from its merge base, so the *next* promotion PR
  re-shows every change already on `main` as though it were new, and it
  compounds each time. The one PR that most needs to be reviewable becomes the
  least.
- `main` → `dev` (a back-merge, after a hotfix): **merge commit**, same reason.

A ruleset on `main` enforces this — the squash button is not offered on a PR
targeting it, and `main` takes no direct pushes. `dev` is deliberately left
unrestricted, because a back-merge into it needs a merge commit while ordinary
feature work wants a squash.

## What CI gates

|                                                                    | PR → `dev` | push `dev` | PR → `main` | push `main` |
| ------------------------------------------------------------------ | ---------- | ---------- | ----------- | ----------- |
| `lint.yml` — pre-commit, all files                                 | ✅         | ✅         | ✅          | ✅          |
| `validate.yml` — every manifest through sheppy, every robot config | ✅         | ✅         | ✅          | ✅          |

Both are minutes. There is no expensive tier here because nothing in this repo
is built.

`validate.yml` runs `scripts/validate_manifest.py`, which loads each manifest
through sheppy's own loader rather than a copied schema — so an inline
`container:` block is translated exactly as launch will translate it. A compose
key sheppy does not know is an error. A key it *ignores* — `depends_on`,
`restart`, `healthcheck` — is also an error here, and that is deliberate: those
load green and then silently do not happen, which on a deployment is worse than
a typo.

## Pinning

**Every image reference is a release tag. Never `:main`, never `:latest`.**

A manifest is a claim about what a demo machine will run. A moving tag makes
that claim untrue the moment the upstream repo merges something, and the
manifest that was supposed to record the change does not change. When you cannot
pin — the upstream has not cut a release yet — say so in a comment at the
reference, so the gap is visible rather than assumed.

The same goes the other way: a module's own
`rammp-alternative*.yaml` fragment is the source for what belongs in a node
here. Transcribe it, keep its comments where they still apply, and note where
you diverged and why — the fragments build images locally, and this repo pulls
published ones.

## Robot config

Setup that happens outside any node lives in `<deployment>/config/robots/<robot>/`, one
file per script, applied on the robot:

| File           | Script                      | Sets                                                                                   |
| -------------- | --------------------------- | -------------------------------------------------------------------------------------- |
| `network.yaml` | `scripts/network_config.py` | the host's NICs, through NetworkManager                                                |
| `oak_d.yaml`   | `scripts/oak_d_config.py`   | each OAK-D PoE camera's addressing                                                     |
| `mount.yaml`   | `scripts/set_robot.py`      | the arm's mount rotation; makes this the host's active robot (`config/robots/current`) |
| `transforms/`  | the `static_tf` node        | fixed and calibrated transforms (camera mounts), on `/tf_static` (rammp-tools image)   |
| `curobo/`      | the `planner` node          | the robot's own planner config and obstacles (`planner.yaml`, `world.yaml`, layers)    |

```bash
uv run scripts/network_config.py december_2026/config/robots/flatbot
```

`mount.yaml`, `transforms/` and `curobo/` are also read by nodes: `set_robot.py` points
`config/robots/current` at the robot, and `arm_state_publisher`, `static_tf` and the
nightly `planner` bind-mount it read-only at `/config/robot`. Only per-robot data that a node needs as
a file goes there. Everything else a node is configured with is a parameter (below).
The robot name joins the two: `config/robots/flatbot/` and `profiles/flatbot.yaml`.

## Node parameters

| Value                            | Where it goes                                  |
| -------------------------------- | ---------------------------------------------- |
| A parameter and its default      | the alternative's `params:` in the manifest    |
| Its value on one robot           | `profiles/<robot>.yaml`, under `overrides:`    |
| Its value for one run            | `p` in the sheppy TUI                          |
| Large config that rarely changes | a file in the module's image, named by a param |

Put every value someone might change per robot in `params:`, with its default. A
profile override or a TUI edit applies only to a key the selected alternative
lists there; `validate_manifest.py` fails a profile that overrides anything else.
A value written into `command:` can only be changed by editing the manifest.

For a docker alternative:

- sheppy writes `params:` to a params file and appends
  `--ros-args --params-file /sheppy/params.yaml` to the command. The file is
  keyed by `ros_node_name`, or `/**` without one. Set `ros_node_name` when a
  parameter must land on one particular node, such as the arm's read-only
  `arbitration_mode`.
- With no `command:`, those args *replace* the image's CMD. That works only when
  the image's entrypoint runs the node itself, as the camera images' does.
  Otherwise give the command explicitly, ending at the node's executable.
- `ros2 launch` rejects the appended args and the container exits at start. An
  alternative that runs a launch file takes no `params:`; its values are launch
  arguments in `command:`. `validate_manifest.py` fails `params:` on one, and on
  a `process` alternative, where sheppy ignores them.

Exception: the arm driver's `--ip` and `--cpu` are command-line flags, not ROS
parameters, so they stay in `command:` (rammp-org/kinova-gen3-ros2#57).

## On hardware

**Attended only.** Nothing in this repo moves an arm by itself, but every
manifest here is a thing that starts a driver that can. Bring a deployment up
with a human on the physical e-stop.
