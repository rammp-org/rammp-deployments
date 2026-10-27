"""Overnight perception bench on snorlax with plain `docker run` (the sheppy daemon belongs to another session).

Live wrist OAK (640x400 @ 15 Hz) stands in for the offline scene camera. Containers are named pbench-*; the
camera and static_tf containers are someone else's and are only read. Each run: converge the pbench-*
containers to the run's spec (unchanged ones are kept), run ros_probe.py (waits, warms up, measures), sample
cgroup CPU/memory and tegrastats over the same window, save results/<run>.json.

    python3 pbench.py --list
    python3 pbench.py run1 run2 ...      # or a group: pr6 pr8 pr8b final

The pr8/alt/alt2 runs mount sam3_<node>.py from this directory over the image's node: rammp-perception's
object_detection/sam3/rammp_sam3/rammp_sam3/node.py from #8's branch (pr8), #17's (alt2; alt is an earlier
draft of it), or dev (dev). They are not committed here.
"""

import hashlib
import json
import os
import re
import shlex
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / os.environ.get("PB_RESULTS", "results")
PARAMS = HERE / "params"
PERF = HERE.parents[1]  # tests/perf/: ros_probe.py, gpu_load.py
SAM3_WEIGHTS = Path(
    os.environ.get(
        "PB_SAM3_WEIGHTS", Path.home() / "perf-bench" / "data" / "models" / "sam3"
    )
)

REG = "ghcr.io/rammp-org/rammp-perception/"
IMG = {
    "rfdetr": REG + "rfdetr:sha-8091bbee8f9a147f2811290b8ea780542ee13df7",
    "yoloe": REG + "yoloe:sha-8091bbee8f9a147f2811290b8ea780542ee13df7",
    "sam3": REG + "sam3:sha-8091bbee8f9a147f2811290b8ea780542ee13df7",
    "boxer": REG + "boxer:sha-f54ea8ed8e6e8d928a9b3ea63997229289901abe",
}
CAM = "/wrist_camera/color/image_raw"
DEPTH = "/wrist_camera/depth/image_rect_raw"
INFO = "/wrist_camera/color/camera_info"
DETS = "/perception/object_detections"
SAM3 = "/perception/sam3/object_detections"
LIFTED = "/perception/object_detections_3d"
SAM3_SITE = (
    "/module_ws/install/rammp_sam3/lib/python3.10/site-packages/rammp_sam3/node.py"
)
PROMPTS = "metal plate"


def segmenter(kind, stride):
    if kind == "rfdetr":
        return "segmenter", {
            "image": IMG["rfdetr"],
            "params": {"model": "small", "image_topic": CAM, "frame_stride": stride},
        }
    return "segmenter", {
        "image": IMG["yoloe"],
        "volumes": ["rammp_perception_engines:/cache/engines"],
        "params": {
            "model": "yoloe-26m-seg",
            "device": "gpu",
            "image_topic": CAM,
            "frame_stride": stride,
        },
    }


def sam3(hz, node="dev"):
    vols = ["%s:/models/sam3:ro" % SAM3_WEIGHTS, "sam3_engines:/cache/engines"]
    if node != "dev":
        vols.append("%s:%s:ro" % (HERE / ("sam3_%s.py" % node), SAM3_SITE))
    return "sam3", {
        "image": IMG["sam3"],
        "volumes": vols,
        "params": {
            "default_image_topic": CAM,
            "max_tracking_rate_hz": float(hz),
            "timing_log_period_s": 20.0,
        },
    }


def boxer():
    return "boxer", {
        "image": IMG["boxer"],
        "volumes": ["rammp_perception_engines:/cache/engines"],
        "params": {
            "detections_topic": DETS,
            "image_topic": CAM,
            "depth_topic": DEPTH,
            "camera_info_topic": INFO,
            "gravity_frame": "arm_end_effector_link",
            "min_prob": 0.5,
            "depth_slop_s": 0.005,
            "frame_stride": 1,
        },
    }


def gpu_load(duty):
    return "gpu_load", {
        "image": IMG["sam3"],
        "volumes": ["%s:/perf:ro" % PERF],
        "entrypoint": "python3",
        "args": ["/perf/gpu_load.py", "--duty", "%.2f" % (duty / 100)],
    }


def run(*nodes, blas=None, track=False):
    """A run: node specs, the OPENBLAS_NUM_THREADS every container gets (None: unset), and a SAM3 session."""
    return {"nodes": dict(nodes), "blas": blas, "track": track}


RUNS = {"idle": run()}
# PR #6: OpenBLAS threads. Unset (the images today), 1 (the PR), and 2/4 to check that 1 isn't too few.
for b, tag in ((None, "U"), (1, "1"), (2, "2"), (4, "4")):
    RUNS["r6-rf15-blas" + tag] = run(segmenter("rfdetr", 1), blas=b)
    RUNS["r6-stack-blas" + tag] = run(
        segmenter("rfdetr", 3), sam3(5), boxer(), blas=b, track=True
    )
for b, tag in ((None, "U"), (1, "1")):
    RUNS["r6-rf5-blas" + tag] = run(segmenter("rfdetr", 3), blas=b)
    RUNS["r6-yo15-blas" + tag] = run(segmenter("yoloe", 1), blas=b)
    RUNS["r6-sam3-blas" + tag] = run(sam3(5), blas=b, track=True)
# PR #8: SAM3 at a 5 Hz cap; dev node, the PR's, and the fixed-schedule fresh-frame alternative.
for n in ("dev", "pr8", "alt", "alt2"):
    RUNS["r8-alone-" + n] = run(sam3(5, n), blas=1, track=True)
    RUNS["r8-stack-" + n] = run(
        segmenter("rfdetr", 3), sam3(5, n), boxer(), blas=1, track=True
    )
    RUNS["r8-uncap-" + n] = run(sam3(30, n), blas=1, track=True)
    RUNS["r8-load70-" + n] = run(
        segmenter("rfdetr", 3), sam3(5, n), boxer(), gpu_load(70), blas=1, track=True
    )
# #107: the flatbot stack with RF-DETR always on, at the rates the 15 fps OAK allows.
for stride, hz in ((3, 5), (2, 7), (1, 15)):
    RUNS["f-rf%d" % hz] = run(
        segmenter("rfdetr", stride), sam3(5, "pr8"), boxer(), blas=1, track=True
    )
RUNS["f-rf5-load70"] = run(
    segmenter("rfdetr", 3), sam3(5, "pr8"), boxer(), gpu_load(70), blas=1, track=True
)
RUNS["f-yo5"] = run(segmenter("yoloe", 3), sam3(5, "pr8"), boxer(), blas=1, track=True)

# Groups, interleaved so A/B pairs see the same drift; each comparison is run twice.
GROUPS = {
    "pr6": [
        "r6-rf15-blasU",
        "r6-rf15-blas1",
        "r6-rf15-blas2",
        "r6-rf15-blas4",
        "r6-rf5-blasU",
        "r6-rf5-blas1",
        "r6-yo15-blasU",
        "r6-yo15-blas1",
        "r6-sam3-blasU",
        "r6-sam3-blas1",
        "r6-stack-blasU",
        "r6-stack-blas1",
        "r6-stack-blas2",
        "r6-stack-blas4",
    ],
    "pr8": [
        "r8-alone-dev",
        "r8-alone-pr8",
        "r8-alone-alt",
        "r8-stack-dev",
        "r8-stack-pr8",
        "r8-stack-alt",
        "r8-uncap-dev",
        "r8-uncap-pr8",
        "r8-uncap-alt",
        "r8-load70-dev",
        "r8-load70-pr8",
        "r8-load70-alt",
    ],
    # The committed alternative (alt2) next to the PR, three times each, interleaved.
    "pr8b": [
        r % n
        for _ in range(3)
        for r in ("r8-alone-%s", "r8-stack-%s", "r8-load70-%s")
        for n in ("pr8", "alt2")
    ],
    "final": ["idle", "f-rf5", "f-rf7", "f-rf15", "f-rf5-load70", "f-yo5"],
}


# --- containers ------------------------------------------------------------------------------------------


def sh(*cmd, timeout=120):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout + r.stderr


def spec_hash(spec, blas):
    return hashlib.sha1(json.dumps([spec, blas], sort_keys=True).encode()).hexdigest()[
        :12
    ]


def running():
    """{pbench name: spec hash} of running pbench containers."""
    _, out = sh(
        "docker",
        "ps",
        "--filter",
        "name=pbench-",
        "--format",
        '{{.Names}} {{.Label "pbench.hash"}}',
    )
    return dict(ln.split() for ln in out.splitlines() if len(ln.split()) == 2)


def start(name, spec, blas):
    cname = "pbench-" + name
    cmd = [
        "docker",
        "run",
        "-d",
        "--name",
        cname,
        "--label",
        "pbench.hash=" + spec_hash(spec, blas),
        "--runtime",
        "nvidia",
        "--network",
        "host",
        "--ipc",
        "host",
        "--user",
        "1000:1000",
        "-e",
        "ROS_DOMAIN_ID=0",
        "-e",
        "HOME=/tmp",
    ]
    if blas is not None:
        cmd += ["-e", "OPENBLAS_NUM_THREADS=%d" % blas]
    for v in spec.get("volumes", []):
        cmd += ["-v", v]
    if "params" in spec:
        PARAMS.mkdir(exist_ok=True)
        pf = PARAMS / (name + ".yaml")
        pf.write_text(
            json.dumps({"/**": {"ros__parameters": spec["params"]}})
        )  # JSON is YAML
        pf.chmod(0o644)
        cmd += ["-v", "%s:/pbench/params.yaml:ro" % pf]
    if "entrypoint" in spec:
        cmd += ["--entrypoint", spec["entrypoint"], spec["image"], *spec["args"]]
    else:
        cmd += [spec["image"], "--ros-args", "--params-file", "/pbench/params.yaml"]
    code, out = sh(*cmd)
    if code:
        raise RuntimeError("docker run %s: %s" % (cname, out))


def converge(r):
    want = {"pbench-" + n: spec_hash(s, r["blas"]) for n, s in r["nodes"].items()}
    have = running()
    for cname, h in have.items():
        if want.get(cname) != h:
            sh("docker", "rm", "-f", cname, timeout=60)
    for n, s in r["nodes"].items():
        if have.get("pbench-" + n) != want["pbench-" + n]:
            sh(
                "docker", "rm", "-f", "pbench-" + n, timeout=60
            )  # an exited one still holds the name
            start(n, s, r["blas"])


# --- sampling (from rammp-deployments tests/perf/bench.py) -----------------------------------------------------


def parse_tegrastats(path):
    keys = {
        "gpu_pct": r"GR3D_FREQ (\d+)%",
        "ram_mb": r"RAM (\d+)/",
        "vdd_in_mw": r"VDD_IN (\d+)mW",
        "vdd_cpu_gpu_cv_mw": r"VDD_CPU_GPU_CV (\d+)mW",
        "tj_c": r"tj@([\d.]+)C",
    }
    rows, cpu = {k: [] for k in keys}, []
    for line in path.read_text().splitlines():
        for k, rx in keys.items():
            m = re.search(rx, line)
            if m:
                rows[k].append(float(m.group(1)))
        m = re.search(r"CPU \[([^\]]*)\]", line)
        if m:
            cpu.append(
                [int(c.split("%")[0]) for c in m.group(1).split(",") if "%" in c]
            )
    out = {}
    for k, xs in rows.items():
        if xs:
            xs.sort()
            out[k] = {
                "mean": round(statistics.mean(xs), 1),
                "p95": xs[int(0.95 * (len(xs) - 1))],
            }
    if cpu:
        per_core = [statistics.mean(c[i] for c in cpu) for i in range(len(cpu[0]))]
        out["cpu_cores_busy"] = round(sum(per_core) / 100, 2)
    out["samples"] = len(cpu)
    return out


def cgroup_read(cid):
    base = Path("/sys/fs/cgroup/system.slice/docker-%s.scope" % cid)
    try:
        usage = int(
            re.search(r"usage_usec (\d+)", (base / "cpu.stat").read_text()).group(1)
        )
        return usage / 1e6, int((base / "memory.current").read_text())
    except (OSError, AttributeError):
        return None


class Sampler(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.stop = threading.Event()
        _, out = sh("docker", "ps", "--no-trunc", "--format", "{{.ID}} {{.Names}}")
        self.ids = {
            n: c
            for c, n in (ln.split() for ln in out.splitlines())
            if n.startswith(("pbench-", "sheppy-"))
        }
        self.first, self.last, self.peak = {}, {}, {}

    def read(self):
        now = time.monotonic()
        for name, cid in self.ids.items():
            v = cgroup_read(cid)
            if v:
                self.first.setdefault(name, (now, v[0]))
                self.last[name] = (now, v[0])
                self.peak[name] = max(self.peak.get(name, 0), v[1])

    def run(self):
        while not self.stop.wait(2):
            self.read()

    def result(self):
        out = {}
        for name, (t0, c0) in self.first.items():
            t1, c1 = self.last[name]
            if t1 > t0:
                out[name] = {
                    "cpu_cores": round((c1 - c0) / (t1 - t0), 3),
                    "mem_peak_mb": round(self.peak[name] / 2**20),
                }
        return out


# --- one run ---------------------------------------------------------------------------------------------


def probe_cmd(r, duration, warmup):
    topics = [CAM + ":image"]
    require = [CAM]
    if "segmenter" in r["nodes"]:
        topics.append(DETS + ":dets")
        require.append(DETS)
    if "sam3" in r["nodes"]:
        topics.append(SAM3 + ":dets")
    if "boxer" in r["nodes"]:
        topics.append(LIFTED + ":dets")
        require.append(LIFTED)
    args = ["--duration", str(duration), "--warmup", str(warmup)]
    for t in topics:
        args += ["--topic", t]
    for t in require:
        args += ["--require", t]
    if r["track"]:
        args += ["--sam3-track", PROMPTS]
    inner = (
        "source /opt/ros/humble/setup.bash && source /module_ws/install/setup.bash && exec python3 -u "
        "/perf/ros_probe.py " + " ".join(map(shlex.quote, args))
    )
    return [
        "docker",
        "run",
        "--rm",
        "--name",
        "pbench-probe",
        "--network",
        "host",
        "--ipc",
        "host",
        "-e",
        "ROS_DOMAIN_ID=0",
        "-v",
        "%s:/perf:ro" % PERF,
        "--entrypoint",
        "bash",
        IMG["sam3"],
        "-c",
        inner,
    ]


def probe_json(lines):
    for line in reversed(lines):
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                pass
    return {"errors": ["probe output: " + " | ".join(lines[-10:])]}


def execute(name, duration, warmup):
    r = RUNS[name]
    print("== %s" % name, flush=True)
    t0 = time.monotonic()
    sh("docker", "rm", "-f", "pbench-probe")
    converge(r)
    tegra_log = RESULTS / ("%s.tegrastats" % name)
    tegra_log.unlink(missing_ok=True)
    sampler = tegra = None
    lines = []

    def start_sampling():
        nonlocal sampler, tegra
        sampler = Sampler()
        tegra = subprocess.Popen(
            ["tegrastats", "--interval", "500", "--logfile", str(tegra_log)]
        )
        sampler.read()
        sampler.start()

    probe = subprocess.Popen(
        probe_cmd(r, duration, warmup),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    timer = threading.Timer(1200, probe.kill)  # hard deadline per run
    timer.start()
    for line in probe.stdout:
        line = line.rstrip()
        if line == "MEASURE_START":
            start_sampling()
        elif line == "MEASURE_END":
            break
        else:
            lines.append(line)
    if sampler:
        sampler.read()
        sampler.stop.set()
        tegra.terminate()
        tegra.wait()
    lines += [ln.rstrip() for ln in probe.stdout]
    probe.wait()
    timer.cancel()

    result = {
        "run": name,
        "config": r,
        "probe": probe_json(lines),
        "tegrastats": parse_tegrastats(tegra_log) if tegra_log.exists() else {},
        "processes": sampler.result() if sampler else {},
        "logs": {},
    }
    for n in r["nodes"]:
        _, log = sh("docker", "logs", "--tail", "60", "pbench-" + n)
        result["logs"][n] = [
            ln
            for ln in log.splitlines()
            if re.search(r"Hz|ms|timing|WARN|ERROR|rror", ln)
        ][-12:]
    result["wall_s"] = round(time.monotonic() - t0)
    (RESULTS / ("%s.json" % name)).write_text(json.dumps(result, indent=1))
    p = result["probe"]
    print(
        json.dumps(
            {
                "run": name,
                "topics": {
                    t: (v.get("hz"), (v.get("latency_ms") or {}).get("p50"))
                    for t, v in p.get("topics", {}).items()
                },
                "gpu": result["tegrastats"].get("gpu_pct", {}).get("mean"),
                "cpu": {k: v["cpu_cores"] for k, v in result["processes"].items()},
                "errors": p.get("errors"),
                "wall_s": result["wall_s"],
            }
        ),
        flush=True,
    )
    (HERE / "heartbeat").write_text("%s %s done\n" % (time.strftime("%H:%M:%S"), name))


def main():
    a = sys.argv[1:]
    dur = float(os.environ.get("PB_DURATION", 60))
    warm = float(os.environ.get("PB_WARMUP", 20))
    reps = int(os.environ.get("PB_REPS", 1))
    if not a or a == ["--list"]:
        print("\n".join(RUNS))
        return
    names = [n for x in a for n in GROUPS.get(x, [x])]
    RESULTS.mkdir(exist_ok=True)
    count = {}
    for rep in range(reps):
        for n in names:
            i = count[n] = count.get(n, -1) + 1
            tag = n if i == 0 else "%s.rep%d" % (n, i)
            if (
                RESULTS / (tag + ".json")
            ).exists():  # resume: a result from an earlier attempt stands
                continue
            RUNS[tag] = RUNS[n]
            try:
                execute(tag, dur, warm)
            except Exception as e:  # keep the night going; the run's json is missing and the log says why
                print("!! %s failed: %r" % (tag, e), flush=True)
    if os.environ.get("PB_KEEP") != "1":
        for c in running():
            sh("docker", "rm", "-f", c, timeout=60)


if __name__ == "__main__":
    main()
