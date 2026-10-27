"""Markdown tables from bench.py's results/*.json.

python3 perf/report.py ~/perf-bench/results > report.md
"""

import json
import re
import sys
from pathlib import Path

from bench import BENCH_TOPIC, CAMERA, DETECTIONS, LIFTED, SAM3_TOPIC, probe_json

NAMES = {
    "yoloe26m_gpu": "YOLOE-26m-seg, GPU (TensorRT FP16)",
    "yoloe26s_gpu": "YOLOE-26s-seg, GPU (TensorRT FP16)",
    "yoloe26s_dla": "YOLOE-26s-seg, DLA0 (TensorRT FP16)",
    "rfdetr_s": "RF-DETR-Seg S, GPU (PyTorch FP16)",
    "owlv2": "OWLv2 B/16, GPU (PyTorch FP32)",
    "nanoowl_b32": "NanoOWL B/32, GPU (TensorRT FP16)",
    "nanoowl_img_b32": "NanoOWL B/32, rammp-perception image (boxes published)",
    "nanoowl_img_b16": "NanoOWL B/16, rammp-perception image (boxes published)",
}


def load(results):
    out = {}
    for p in sorted(Path(results).glob("*.json")):
        r = json.loads(p.read_text())
        errs = r.get("probe", {}).get("errors") or []
        if len(errs) == 1 and errs[0].startswith(
            "probe output: "
        ):  # results from before bench.probe_json
            r["probe"] = probe_json(errs[0][len("probe output: ") :].split(" | "))
        out[p.stem] = r
    return out


def topic(r, name):
    return r.get("probe", {}).get("topics", {}).get(name) or {}


def watts(r):
    t = r["tegrastats"]
    return (
        sum(
            t[k]["mean"]
            for k in ("vdd_gpu_soc_mw", "vdd_cpu_cv_mw", "vin_sys_5v0_mw")
            if k in t
        )
        / 1000
    )


def ram_gb(r, idle):
    return (
        r["tegrastats"]["ram_mb"]["max"] - idle["tegrastats"]["ram_mb"]["mean"]
    ) / 1024


def compute_ms(r):
    """The detector's own per-frame time from its log: the segmenter's latency median, owl_node's infer p50."""
    for line in reversed(r.get("node_logs", {}).get("scene_segmenter", [])):
        m = re.search(r"latency median ([\d.]+) ms", line) or re.search(
            r"infer p50 ([\d.]+) ms", line
        )
        if m:
            return float(m.group(1))
    return None


def detector_cpu(r):
    p = r["processes"]
    v = p.get("sheppy-scene_segmenter") or p.get("host:owl_node.py") or {}
    return v.get("cpu_cores"), v.get("mem_peak_mb")


def fmt(x, spec="%.0f"):
    return "–" if x is None else spec % x


def rate(t):
    return fmt(t.get("hz"), "%.1f")


def lat(t, q="p50"):
    return fmt((t.get("latency_ms") or {}).get(q))


def system_cols(r, idle):
    t = r["tegrastats"]
    return [
        fmt(t["gpu_pct"]["mean"]),
        fmt(t["cpu_total_pct"]),
        "%.1f" % ram_gb(r, idle),
        "%.1f" % watts(r),
        fmt(t["tj_c"]["max"]),
    ]


SYSTEM = ["GPU %", "CPU % (12 cores)", "RAM over idle (GB)", "Power (W)", "Tj max (°C)"]


def table(head, rows):
    out = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    out += ["| " + " | ".join(map(str, r)) + " |" for r in rows]
    return "\n".join(out)


def main():
    res = load(sys.argv[1])
    idle = res["idle"]
    print("## Base stack\n")
    rows = []
    for name, label in (
        ("idle", "nothing running"),
        ("base", "camera, mocks, TF, planner, foxglove"),
        ("base-sam3", "+ SAM3 loaded, idle"),
        ("base-sam3-track", "+ SAM3 tracking 1 object"),
        ("base-sam3-track5-blas1", "+ SAM3 tracking 1 object, capped at 5 Hz"),
    ):
        r = res.get(name)
        if r:
            s3 = topic(r, SAM3_TOPIC)
            rows.append(
                [label, rate(topic(r, CAMERA)), rate(s3) if "track" in name else "–"]
                + system_cols(r, idle)
            )
    print(table(["Configuration", "Camera Hz", "SAM3 track Hz"] + SYSTEM, rows))

    print(
        "\n## Always-on detectors, every camera frame (stride 1), SAM3 loaded and idle\n"
    )
    rows = []
    for d, label in NAMES.items():
        r = res.get("%s-s1" % d)
        if not r:
            continue
        t = topic(r, DETECTIONS) or topic(r, BENCH_TOPIC)
        cpu, mem = detector_cpu(r)
        rows.append(
            [
                label,
                rate(t),
                fmt(compute_ms(r)),
                lat(t),
                lat(t, "p95"),
                fmt(cpu, "%.2f"),
                fmt(mem),
            ]
            + system_cols(r, idle)
        )
    print(
        table(
            [
                "Detector",
                "Hz",
                "Node ms/frame",
                "Camera→result p50 (ms)",
                "p95",
                "Detector CPU (cores)",
                "Detector RSS (MB)",
            ]
            + SYSTEM,
            rows,
        )
    )

    sections = (
        ("-s3-track", "## Always-on at 10 Hz, SAM3 tracking uncapped, 5 detect calls"),
        ("-s3-track-boxer", "## Always-on at 10 Hz, SAM3 uncapped, BoxerNet"),
        (
            "-s3-track-load40",
            "## Always-on at 10 Hz, SAM3 uncapped, another GPU user busy 40% of the time",
        ),
        (
            "-s3-track-load70",
            "## Always-on at 10 Hz, SAM3 uncapped, another GPU user busy 70% of the time",
        ),
        (
            "-s3-track5-blas1",
            "## Target: always-on at 10 Hz, SAM3 capped at 5 Hz, OPENBLAS_NUM_THREADS=1",
        ),
        ("-s3-track5-boxer-blas1", "## Target + BoxerNet"),
        ("-s3-track5-load40-blas1", "## Target, another GPU user busy 40% of the time"),
        ("-s3-track5-load70-blas1", "## Target, another GPU user busy 70% of the time"),
    )
    for suffix, title in sections:
        rows = []
        for d, label in NAMES.items():
            r = res.get(d + suffix)
            if not r:
                continue
            t = topic(r, DETECTIONS) or topic(r, BENCH_TOPIC)
            s3 = topic(r, SAM3_TOPIC)
            det = r.get("probe", {}).get("sam3_detect") or {}
            row = [
                label,
                rate(t),
                lat(t),
                lat(t, "p95"),
                fmt(detector_cpu(r)[0], "%.2f"),
                rate(s3),
                lat(s3),
                fmt((s3.get("gap_ms") or {}).get("max")),
                fmt(det.get("ms_p50")),
                fmt(det.get("ms_max")),
            ]
            if "boxer" in suffix:
                b = topic(r, LIFTED)
                row += [rate(b), lat(b)]
            rows.append(row + system_cols(r, idle))
        if rows:
            head = [
                "Detector",
                "Hz",
                "Camera→result p50 (ms)",
                "p95",
                "Detector CPU (cores)",
                "SAM3 track Hz",
                "track p50 (ms)",
                "track max gap (ms)",
                "SAM3 detect p50 (ms)",
                "detect max",
            ]
            if "boxer" in suffix:
                head += ["Boxer Hz", "Boxer camera→3D p50 (ms)"]
            print("\n%s\n" % title)
            print(table(head + SYSTEM, rows))

    rows = []
    for name, label in (
        ("final", "finalized stack, YOLOE-26m"),
        ("final-rfdetr", "finalized stack, RF-DETR-Seg S"),
        ("final-load70", "finalized stack, YOLOE-26m, other GPU user busy 70%"),
        ("final-nanoowl_img_b32", "finalized stack, NanoOWL B/32"),
        ("final-nanoowl_img_b16", "finalized stack, NanoOWL B/16"),
        (
            "final-nanoowl_img_b32-load70",
            "finalized stack, NanoOWL B/32, other GPU user busy 70%",
        ),
    ):
        r = res.get(name)
        if r:
            t, s3, b = topic(r, DETECTIONS), topic(r, SAM3_TOPIC), topic(r, LIFTED)
            rows.append(
                [label, rate(t), lat(t), rate(s3), lat(s3), rate(b), lat(b)]
                + system_cols(r, idle)
            )
    if rows:
        print(
            "\n## Finalized: detector 10 Hz, SAM3 and BoxerNet capped at 5 Hz, all fixes\n"
        )
        print(
            table(
                [
                    "Configuration",
                    "Detector Hz",
                    "camera→result p50",
                    "SAM3 Hz",
                    "SAM3 p50",
                    "Boxer Hz",
                    "Boxer camera→3D p50",
                ]
                + SYSTEM,
                rows,
            )
        )

    errors = {
        k: r.get("probe", {}).get("errors")
        for k, r in res.items()
        if r.get("probe", {}).get("errors")
    }
    if errors:
        print("\n## Errors\n")
        for k, e in errors.items():
            print("- %s: %s" % (k, "; ".join(e)))


if __name__ == "__main__":
    main()
