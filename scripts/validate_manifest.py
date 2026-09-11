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
"""

import glob
import sys

from sheppy.launch.docker.compose import service_to_docker_args
from sheppy.manifest.loader import load_manifest


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
            container = alt.config.get("container")
            if alt.kind != "docker" or not isinstance(container, dict):
                continue
            *_, warns = service_to_docker_args(container)
            for warn in warns:
                print(f"  {path}: {node.name}/{alt.id}: warning: {warn}")
                problems += 1

    if not problems:
        nodes = len(result.manifest.nodes)
        alts = sum(len(n.alternatives) for n in result.manifest.nodes)
        print(f"  {path}: ok — {nodes} nodes, {alts} alternatives")
    return problems


def main() -> int:
    paths = sys.argv[1:] or sorted(glob.glob("*/sheppy-manifest.yaml"))
    if not paths:
        print("no manifests found", file=sys.stderr)
        return 1
    return 1 if sum(check(p) for p in paths) else 0


if __name__ == "__main__":
    sys.exit(main())
