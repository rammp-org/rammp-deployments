"""Markdown tables from pbench results: each config's reps pooled as mean (min-max).

python3 analyze.py results [results-pr8b ...] > tables.md
"""

import json
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

CAM = "/wrist_camera/color/image_raw"
DETS = "/perception/object_detections"
SAM3 = "/perception/sam3/object_detections"
LIFTED = "/perception/object_detections_3d"


RAILS = (
    "VDD_GPU_SOC",
    "VDD_CPU_CV",
    "VIN_SYS_5V0",
)  # AGX Orin's three rails; their sum is the module


def power_w(path):
    """Mean of the three rails' sum over a run's tegrastats log, in W."""
    sums = []
    for line in path.read_text().splitlines():
        vals = [re.search(r"%s (\d+)mW" % k, line) for k in RAILS]
        if all(vals):
            sums.append(sum(int(v.group(1)) for v in vals) / 1000)
    return round(statistics.mean(sums), 2) if sums else None


def load(dirs):
    runs = defaultdict(list)
    for d in dirs:
        for f in sorted(Path(d).glob("*.json")):
            r = json.loads(f.read_text())
            t = f.with_suffix(".tegrastats")
            r["power_w"] = power_w(t) if t.exists() else None
            runs[r["run"].split(".rep")[0]].append(r)
    return runs


def fmt(xs, nd=0):
    xs = [x for x in xs if x is not None]
    if not xs:
        return "–"
    m = statistics.mean(xs)
    s = "%.*f" % (nd, m)
    if len(xs) > 1 and max(xs) - min(xs) >= 10**-nd:
        s += " (%.*f–%.*f)" % (nd, min(xs), nd, max(xs))
    return s


def topic(rs, t, key):
    out = []
    for r in rs:
        v = r["probe"].get("topics", {}).get(t)
        if not v or not v.get("count"):
            out.append(None)
        elif key == "hz":
            out.append(v["hz"])
        else:
            out.append(v["latency_ms"][key])
    return out


def proc(rs, name):
    return [r["processes"].get(name, {}).get("cpu_cores") for r in rs]


def tegra(rs, key, sub="mean"):
    return [
        r["tegrastats"].get(key, {}).get(sub)
        if isinstance(r["tegrastats"].get(key), dict)
        else r["tegrastats"].get(key)
        for r in rs
    ]


def perception_cpu(rs):
    return [
        sum(
            v["cpu_cores"]
            for k, v in r["processes"].items()
            if k.startswith("pbench-") and k != "pbench-probe"
        )
        for r in rs
    ]


def table(runs, names, cols):
    head = "| run | n | " + " | ".join(c[0] for c in cols) + " |"
    lines = [head, "|" + "---|" * (len(cols) + 2)]
    for n in names:
        rs = runs.get(n)
        if rs:
            lines.append(
                "| %s | %d | " % (n, len(rs))
                + " | ".join(c[1](rs) for c in cols)
                + " |"
            )
    return "\n".join(lines)


def main():
    runs = load(sys.argv[1:])
    W = lambda rs: fmt([r["power_w"] for r in rs], 1)
    seg = [
        ("seg Hz", lambda rs: fmt(topic(rs, DETS, "hz"), 1)),
        ("seg p50 ms", lambda rs: fmt(topic(rs, DETS, "p50"))),
        ("seg p95 ms", lambda rs: fmt(topic(rs, DETS, "p95"))),
        ("segmenter CPU cores", lambda rs: fmt(proc(rs, "pbench-segmenter"), 2)),
        ("GPU %", lambda rs: fmt(tegra(rs, "gpu_pct"), 1)),
        ("CPU cores busy (all)", lambda rs: fmt(tegra(rs, "cpu_cores_busy"), 2)),
        ("power W", W),
    ]
    print("## PR #6: segmenter alone\n")
    print(table(runs, [n for n in runs if n.startswith(("r6-rf", "r6-yo"))], seg))
    print("\n## PR #6: SAM3 tracking alone and the stack\n")
    stack = [
        ("seg Hz", lambda rs: fmt(topic(rs, DETS, "hz"), 1)),
        ("SAM3 Hz", lambda rs: fmt(topic(rs, SAM3, "hz"), 2)),
        ("3D Hz", lambda rs: fmt(topic(rs, LIFTED, "hz"), 1)),
        ("segmenter", lambda rs: fmt(proc(rs, "pbench-segmenter"), 2)),
        ("sam3", lambda rs: fmt(proc(rs, "pbench-sam3"), 2)),
        ("boxer", lambda rs: fmt(proc(rs, "pbench-boxer"), 2)),
        ("perception CPU cores", lambda rs: fmt(perception_cpu(rs), 2)),
        ("GPU %", lambda rs: fmt(tegra(rs, "gpu_pct"), 1)),
        ("power W", W),
    ]
    print(
        table(runs, [n for n in runs if n.startswith(("r6-sam3", "r6-stack"))], stack)
    )
    print("\n## PR #8: SAM3 camera-to-result latency\n")
    s8 = [
        ("SAM3 Hz", lambda rs: fmt(topic(rs, SAM3, "hz"), 2)),
        ("SAM3 p50 ms", lambda rs: fmt(topic(rs, SAM3, "p50"))),
        ("SAM3 p95 ms", lambda rs: fmt(topic(rs, SAM3, "p95"))),
        ("SAM3 max ms", lambda rs: fmt(topic(rs, SAM3, "max"))),
        ("seg p50 ms", lambda rs: fmt(topic(rs, DETS, "p50"))),
        ("sam3 CPU", lambda rs: fmt(proc(rs, "pbench-sam3"), 2)),
        ("GPU %", lambda rs: fmt(tegra(rs, "gpu_pct"), 1)),
    ]
    print(table(runs, sorted(n for n in runs if n.startswith("r8-")), s8))
    print("\n## #107: flatbot stack, RF-DETR always on\n")
    fin = [
        ("seg Hz", lambda rs: fmt(topic(rs, DETS, "hz"), 1)),
        ("seg p50 ms", lambda rs: fmt(topic(rs, DETS, "p50"))),
        ("SAM3 Hz", lambda rs: fmt(topic(rs, SAM3, "hz"), 2)),
        ("SAM3 p50 ms", lambda rs: fmt(topic(rs, SAM3, "p50"))),
        ("3D Hz", lambda rs: fmt(topic(rs, LIFTED, "hz"), 1)),
        ("3D p50 ms", lambda rs: fmt(topic(rs, LIFTED, "p50"))),
        ("camera p50 ms", lambda rs: fmt(topic(rs, CAM, "p50"))),
        ("perception CPU cores", lambda rs: fmt(perception_cpu(rs), 2)),
        ("camera CPU", lambda rs: fmt(proc(rs, "sheppy-wrist_camera"), 2)),
        ("GPU %", lambda rs: fmt(tegra(rs, "gpu_pct"), 1)),
        ("RAM MB", lambda rs: fmt(tegra(rs, "ram_mb"))),
        ("power W", W),
        ("Tj °C", lambda rs: fmt(tegra(rs, "tj_c"), 1)),
    ]
    print(
        table(
            runs,
            [
                n
                for n in ("idle", "f-rf5", "f-rf7", "f-rf15", "f-rf5-load70", "f-yo5")
                if n in runs
            ],
            fin,
        )
    )


if __name__ == "__main__":
    main()
