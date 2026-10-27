#!/usr/bin/env python3
"""Validate every sheppy manifest in this repo against sheppy itself.

This repo's whole product is manifests, so the only gate worth having is the
one that asks the thing that will run them. sheppy has no `validate` verb yet
(rammp-org/sheppy#14), so this reaches into its loader directly — the same
interim the module repos take in their own fragment checks.

`load_manifest` already runs each launcher's `validate()`, which for an inline
`container:` block translates it exactly as launch will: an unknown compose key
is an ERROR here rather than a surprise on the robot.

Warnings are re-derived separately because `validate()` drops them, and they
are the more interesting half. sheppy IGNORES `depends_on`, `restart` and
`healthcheck` — it owns lifecycle — so a manifest that quietly relies on one
loads green and then does not do what it says.

Each manifest's profiles (`profiles/*.yaml` beside it) are checked against it
too. sheppy DROPS a selection naming an alternative the manifest lacks, and an
override of a param the alternative does not declare, with only a warning, so
a stale profile brings a robot up missing a node or with a default it meant
to override. Here every such warning is an error.
"""

import glob
import os
import sys

from sheppy.launch.docker.compose import service_to_docker_args
from sheppy.manifest.loader import load_manifest
from sheppy.profiles.reconcile import reconcile
from sheppy.profiles.store import ProfileStore


def params_problems(alt) -> list[str]:
    """`params:` that sheppy accepts but that never reach a node.

    sheppy ignores params on a `process` alternative. On a docker one it appends
    `--ros-args --params-file` to the command, which Humble's `ros2 launch`
    rejects ("unrecognized arguments"), so the container exits at start.
    """
    if not alt.params:
        return []
    if alt.kind == "process":
        return ["params on a process alternative are ignored; put them in its command"]
    command = alt.raw.get("container", {}).get("command") or []
    if isinstance(command, str):
        command = command.split()
    if alt.kind == "docker" and any(
        word == "launch" or word.endswith((".launch.py", ".launch.xml"))
        for word in command
    ):
        return [
            "params with a `ros2 launch` command: ros2 launch rejects the "
            "--ros-args sheppy appends; run the node's executable instead"
        ]
    return []


def check(path: str) -> int:
    result = load_manifest(path)
    problems = 0

    for err in result.errors:
        print(f"  {path}: {err.location}: {err.message}")
        problems += 1

    # A failed load leaves no manifest to walk for warnings.
    if not result.ok:
        return problems

    for node in result.manifest.nodes:
        for alt in node.alternatives:
            for problem in params_problems(alt):
                print(f"  {path}: {node.name}/{alt.id}: {problem}")
                problems += 1
            container = alt.raw.get("container")
            if alt.kind != "docker" or not isinstance(container, dict):
                continue
            *_, warns = service_to_docker_args(container)
            for warn in warns:
                print(f"  {path}: {node.name}/{alt.id}: warning: {warn}")
                problems += 1

    store = ProfileStore(os.path.join(os.path.dirname(path), "profiles"))
    names = store.list_profiles()
    for name in names:
        loaded = store.load(name)
        issues = list(loaded.errors)
        if loaded.profile is not None:
            issues += reconcile(loaded.profile, result.manifest).warnings
        for issue in issues:
            print(f"  {path}: profile {name}: {issue}")
            problems += 1

    if not problems:
        nodes = len(result.manifest.nodes)
        alts = sum(len(n.alternatives) for n in result.manifest.nodes)
        print(
            f"  {path}: ok — {nodes} nodes, {alts} alternatives, {len(names)} profiles"
        )
    return problems


def main() -> int:
    paths = sys.argv[1:] or sorted(glob.glob("*/sheppy-manifest.yaml"))
    if not paths:
        print("no manifests found", file=sys.stderr)
        return 1
    return 1 if sum(check(p) for p in paths) else 0


if __name__ == "__main__":
    sys.exit(main())
