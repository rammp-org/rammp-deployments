"""Compute benchmark of the december_2026 perception stack on one Jetson (rammp-deployments#106).

For each run: write a manifest and profile into ~/perf-bench/stack, `sheppy up` it, wait for every
measured topic, then for --duration seconds record
  - topic rate and camera-to-result latency (ros_probe.py, in the SAM3 image),
  - tegrastats: GPU load (GR3D), per-core CPU, RAM, power rails, junction temperature,
  - per-container CPU and memory from the cgroups, and the same for the bench's host processes,
and write results/<run>.json. The stack is left up between runs; `sheppy up` restarts only what changed.

  python3 perf/bench.py --list
  python3 perf/bench.py yoloe26m_gpu-s1 rfdetr_s-s3-track     # some runs
  python3 perf/bench.py all                                   # every run
"""

import argparse
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

import yaml

ROOT = Path.home() / "perf-bench"
SOURCE_MANIFEST = ROOT / "december_2026" / "sheppy-manifest.yaml"
STACK = ROOT / "stack"
RESULTS = ROOT / "results"
# Every node on this Jetson, so DDS on loopback (the copy of abra's ~/.config/rammp-bench config, shipped here).
LOOPBACK = str(Path(__file__).resolve().parent / "cyclonedds-local.xml")
SAM3_WEIGHTS = os.environ.get("BENCH_SAM3_WEIGHTS", "/home/abra/sam3-test/models/sam3")
# BENCH_CAMERA=replay: the scene camera is replay_camera.py playing BENCH_RECORDING (no camera attached).
CAMERA_ALT = (
    "replay" if os.environ.get("BENCH_CAMERA") == "replay" else "gemini336l_docker"
)
RECORDING = os.environ.get("BENCH_RECORDING", str(ROOT / "recording.npz"))
HOST_ENV = (
    "export ROS_DOMAIN_ID=0 RMW_IMPLEMENTATION=rmw_cyclonedds_cpp CYCLONEDDS_URI=file://%s; "
    % LOOPBACK
)
SHEPPY = str(Path.home() / ".local/bin/sheppy")

CAMERA = "/scene_camera/color/image_raw"
DETECTIONS = "/perception/object_detections"
SAM3_TOPIC = "/perception/sam3/object_detections"
LIFTED = "/perception/object_detections_3d"
BENCH_TOPIC = "/perception/bench/detections"

# name -> (scene_segmenter alternative, param overrides)
DETECTORS = {
    "yoloe26m_gpu": ("yoloe_gpu", {"model": "yoloe-26m-seg"}),
    "yoloe26s_gpu": ("yoloe_gpu", {"model": "yoloe-26s-seg"}),
    "yoloe26s_dla": ("yoloe_dla", {}),
    "rfdetr_s": ("rfdetr", {}),
    "owlv2": ("owl_bench_owlv2", None),
    "nanoowl_b32": ("owl_bench_nanoowl", None),
    # The rammp-perception nanoowl image (feat/nanoowl, built on abra as :dev), publishing real detections.
    "nanoowl_img_b32": ("nanoowl", {"model": "owlvit-b32"}),
    "nanoowl_img_b16": ("nanoowl", {"model": "owlvit-b16"}),
}
SAM3_PROMPTS = (
    "microwave"  # tracking session: one object, in view of abra's scene camera
)
DETECT_PROMPTS = "microwave,fridge,table,food container"


def make_runs():
    runs = {
        "idle": {},  # nothing running: the machine's own baseline
        "base": {
            "base": True
        },  # camera, arm and arm TF (mocks), mount TF, planner, foxglove
        "base-sam3": {"base": True, "sam3": "idle"},
        "base-sam3-track": {"base": True, "sam3": "track"},
    }
    for d in DETECTORS:
        runs["%s-s1" % d] = {"base": True, "sam3": "idle", "detector": d, "stride": 1}
        runs["%s-s3-track" % d] = {
            "base": True,
            "sam3": "track",
            "detector": d,
            "stride": 3,
        }
    for d in ("yoloe26m_gpu", "yoloe26s_dla", "rfdetr_s"):
        runs["%s-s3-track-boxer" % d] = {
            "base": True,
            "sam3": "track",
            "detector": d,
            "stride": 3,
            "boxer": True,
        }
    # The target: always-on at 10 Hz, SAM3 tracking capped at 5 Hz, GPU left for the rest of the chair.
    for d in DETECTORS:
        runs["%s-s3-track5" % d] = {
            "base": True,
            "sam3": "track",
            "sam3_hz": 5,
            "detector": d,
            "stride": 3,
        }
    # ... and the same next to another GPU user (gpu_load.py busy 40% / 70% of the time).
    for d in (
        "yoloe26m_gpu",
        "yoloe26s_gpu",
        "yoloe26s_dla",
        "rfdetr_s",
        "nanoowl_b32",
    ):
        for duty in (40, 70):
            runs["%s-s3-track5-load%d" % (d, duty)] = {
                "base": True,
                "sam3": "track",
                "sam3_hz": 5,
                "detector": d,
                "stride": 3,
                "gpu_load": duty,
            }
    # ... with numpy's OpenBLAS held to one thread in the perception containers (its idle workers spin).
    for d in ("yoloe26m_gpu", "yoloe26s_gpu", "yoloe26s_dla", "rfdetr_s"):
        runs["%s-s3-track5-blas1" % d] = {
            "base": True,
            "sam3": "track",
            "sam3_hz": 5,
            "detector": d,
            "stride": 3,
            "blas1": True,
        }
        runs["%s-s3-track5-boxer-blas1" % d] = {
            "base": True,
            "sam3": "track",
            "sam3_hz": 5,
            "detector": d,
            "stride": 3,
            "boxer": True,
            "blas1": True,
        }
        for duty in (40, 70):
            runs["%s-s3-track5-load%d-blas1" % (d, duty)] = {
                "base": True,
                "sam3": "track",
                "sam3_hz": 5,
                "detector": d,
                "stride": 3,
                "gpu_load": duty,
                "blas1": True,
            }
    runs["nanoowl_b32-s3-track5-blas1"] = {
        "base": True,
        "sam3": "track",
        "sam3_hz": 5,
        "detector": "nanoowl_b32",
        "stride": 3,
        "blas1": True,
    }
    runs["base-sam3-track5-blas1"] = {
        "base": True,
        "sam3": "track",
        "sam3_hz": 5,
        "blas1": True,
    }
    # SAM3 waiting out its rate cap before taking a frame (rammp-perception fix/sam3-rate-cap-fresh-frame).
    runs["base-sam3-track5-blas1-fresh"] = {
        "base": True,
        "sam3": "track",
        "sam3_hz": 5,
        "blas1": True,
        "sam3_patch": True,
    }
    # The finalized stack: every fix, SAM3 and BoxerNet capped at 5 Hz; alone and next to a 70% GPU user.
    final = {
        "base": True,
        "sam3": "track",
        "sam3_hz": 5,
        "detector": "yoloe26m_gpu",
        "stride": 3,
        "blas1": True,
        "sam3_patch": True,
        "boxer": True,
        "boxer_hz": 5,
    }
    runs["final"] = final
    runs["final-load70"] = dict(final, gpu_load=70)
    runs["final-rfdetr"] = dict(final, detector="rfdetr_s")
    runs["final-timing"] = dict(final, sam3_timing=True)
    runs["yoloe26m_gpu-s3-track5-blas1-fresh"] = {
        "base": True,
        "sam3": "track",
        "sam3_hz": 5,
        "detector": "yoloe26m_gpu",
        "stride": 3,
        "blas1": True,
        "sam3_patch": True,
    }
    for d in ("nanoowl_img_b32", "nanoowl_img_b16"):
        runs["%s-s1" % d] = {"base": True, "sam3": "idle", "detector": d, "stride": 1}
        runs["final-%s" % d] = dict(final, detector=d)
    runs["final-nanoowl_img_b32-load70"] = dict(
        final, detector="nanoowl_img_b32", gpu_load=70
    )
    for d in (
        "yoloe26m_gpu",
        "yoloe26s_gpu",
        "yoloe26s_dla",
        "rfdetr_s",
        "nanoowl_b32",
    ):
        runs["%s-s3-track5-boxer" % d] = {
            "base": True,
            "sam3": "track",
            "sam3_hz": 5,
            "detector": d,
            "stride": 3,
            "boxer": True,
        }
    return runs


RUNS = make_runs()


def loopback(container):
    """Every container on abra's loopback Cyclone config (as abra's own manifest does)."""
    container.setdefault("volumes", []).append("%s:%s:ro" % (LOOPBACK, LOOPBACK))
    container.setdefault("environment", {})["CYCLONEDDS_URI"] = "file://" + LOOPBACK


def host_process(cmd):
    return {
        "kind": "process",
        "command": "bash -lc '%s source /opt/ros/humble/setup.bash && %s'"
        % (HOST_ENV, cmd),
    }


def build_stack(run):
    manifest = yaml.safe_load(SOURCE_MANIFEST.read_text())
    nodes = {n["name"]: n for n in manifest["nodes"]}
    for node in manifest["nodes"]:
        for alt in node["alternatives"]:
            if alt["kind"] == "docker":
                loopback(alt["container"])
            elif alt["kind"] == "process":
                alt["command"] = alt["command"].replace(
                    "bash -lc '", "bash -lc '" + HOST_ENV, 1
                )

    for alt in nodes["sam3"]["alternatives"]:
        if alt["kind"] == "docker":
            alt["container"]["volumes"] = [
                v.replace("/home/abra/sam3-test/models/sam3", SAM3_WEIGHTS)
                for v in alt["container"]["volumes"]
            ]
    nodes["scene_camera"]["alternatives"].append(
        dict(
            id="replay",
            **host_process(
                "exec python3 %s replay %s"
                % (Path(__file__).resolve().parent / "replay_camera.py", RECORDING)
            ),
        )
    )

    # Into the alternative's params, not the profile: sheppy drops a profile override for a param the alternative
    # does not declare, and sam3_docker declares none.
    # Set even when the run gives none: the manifest's own caps would otherwise apply. 15 is the node's default.
    for alt in nodes["sam3"]["alternatives"]:
        alt.setdefault("params", {})["max_tracking_rate_hz"] = float(
            run.get("sam3_hz", 15.0)
        )
        if run.get(
            "sam3_timing"
        ):  # the node logs where each tracking step's time goes, every 10 s
            alt["params"]["timing_log_period_s"] = 10.0
    # sam3_patch: run the SAM3 node from patches/sam3_node.py (an unreleased rammp-perception fix) over the image's.
    if run.get("sam3_patch"):
        patch = Path(__file__).resolve().parent / "patches" / "sam3_node.py"
        for alt in nodes["sam3"]["alternatives"]:
            alt["container"]["volumes"].append(
                "%s:/module_ws/install/rammp_sam3/lib/python3.10/site-packages/rammp_sam3/node.py:ro"
                % patch
            )
    # boxer_hz: BoxerNet's max_rate_hz, from patches/boxer_node.py (unreleased rammp-perception feat/boxer-max-rate).
    for alt in nodes["object_lifter"]["alternatives"]:
        if alt["id"] == "boxer":
            alt["params"]["max_rate_hz"] = float(run.get("boxer_hz", 0.0))
            if run.get("boxer_hz"):
                patch = Path(__file__).resolve().parent / "patches" / "boxer_node.py"
                alt["container"]["volumes"].append(
                    "%s:/module_ws/install/rammp_boxer/lib/python3.10/site-packages/rammp_boxer/node.py:ro"
                    % patch
                )
    if run.get("blas1"):
        for name in ("scene_segmenter", "sam3", "object_lifter"):
            for alt in nodes[name]["alternatives"]:
                if alt["kind"] == "docker":
                    alt["container"]["environment"]["OPENBLAS_NUM_THREADS"] = "1"

    stride = run.get("stride", 3)
    bench = Path(__file__).resolve().parent / "owl_node.py"
    nodes["scene_segmenter"]["alternatives"] += [
        dict(
            id="owl_bench_owlv2",
            **host_process(
                "source ~/RAMMP-box-opening/install/setup.bash && exec python3 %s --model owlv2 --stride %d"
                % (bench, stride)
            ),
        ),
        dict(
            id="owl_bench_nanoowl",
            **host_process(
                "exec ~/detector-bench/.venv/bin/python %s --model nanoowl-b32 --stride %d"
                % (bench, stride)
            ),
        ),
    ]
    yoloe = next(
        a for a in nodes["scene_segmenter"]["alternatives"] if a["id"] == "yoloe_gpu"
    )
    nodes["scene_segmenter"]["alternatives"].append(
        {
            "id": "nanoowl",
            "kind": "docker",
            "container": dict(
                yoloe["container"],
                image="ghcr.io/rammp-org/rammp-perception/nanoowl:dev",
            ),
            "params": {
                "model": "owlvit-b32",
                "image_topic": CAMERA,
                "frame_stride": stride,
            },
        }
    )
    # In the SAM3 image (torch and CUDA), not on the host: snorlax's host has no torch.
    here = Path(__file__).resolve().parent
    manifest["nodes"].append(
        {
            "name": "bench_gpu_load",
            "description": "bench only: synthetic GPU load",
            "select": "single",
            "alternatives": [
                {
                    "id": "load",
                    "kind": "docker",
                    "container": {
                        "image": image_of(manifest, "sam3", "sam3_docker"),
                        "runtime": "nvidia",
                        "network_mode": "host",
                        "user": "1000:1000",
                        "environment": {"HOME": "/tmp"},
                        "volumes": ["%s:/perf:ro" % here],
                        "entrypoint": ["python3"],
                        "command": [
                            "/perf/gpu_load.py",
                            "--duty",
                            "%.2f" % (run.get("gpu_load", 50) / 100),
                        ],
                    },
                }
            ],
        }
    )
    # Nothing in the manifest publishes the camera mount yet; boxer needs it for gravity. abra's
    # calibrated base_link -> scene_camera_link (from the box-opening launch, 2026-10-16).
    manifest["nodes"].append(
        {
            "name": "bench_tf",
            "description": "bench only: base_link -> scene_camera_link",
            "select": "single",
            "alternatives": [
                dict(
                    id="static",
                    **host_process(
                        "exec ros2 run tf2_ros static_transform_publisher --x 0.00399 --y 0.63391 --z 0.34724 --qx 0.031613"
                        " --qy 0.115385 --qz -0.268263 --qw 0.955888 --frame-id base_link --child-frame-id scene_camera_link"
                    ),
                )
            ],
        }
    )

    selections, overrides = {}, {}
    if run.get("base"):
        selections.update(
            scene_camera=CAMERA_ALT,
            foxglove="foxglove_bridge_docker",
            arm="arm_mock",
            arm_state_publisher="arm_state_publisher_mock",
            planner="curobo_planner",
            bench_tf="static",
        )
    if run.get("sam3"):
        selections["sam3"] = "sam3_docker"
    if run.get("detector"):
        alt, params = DETECTORS[run["detector"]]
        selections["scene_segmenter"] = alt
        if params is not None:
            overrides["scene_segmenter"] = dict(params, frame_stride=stride)
    if run.get("boxer"):
        selections["object_lifter"] = "boxer"
    if run.get("gpu_load"):
        selections["bench_gpu_load"] = "load"

    (STACK / "profiles").mkdir(parents=True, exist_ok=True)
    (STACK / "sheppy-manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False, width=200)
    )
    profile = {"description": "perf bench", "selections": selections}
    if overrides:
        profile["overrides"] = overrides
    (STACK / "profiles" / "bench.yaml").write_text(
        yaml.safe_dump(profile, sort_keys=False)
    )
    return manifest


def image_of(manifest, node, alt_id):
    for n in manifest["nodes"]:
        if n["name"] == node:
            return next(a for a in n["alternatives"] if a["id"] == alt_id)["container"][
                "image"
            ]


# --- system sampling -------------------------------------------------------------------------------------

TEGRA = {
    "ram_mb": re.compile(r"RAM (\d+)/"),
    "gpu_pct": re.compile(r"GR3D_FREQ (\d+)%"),
    "vdd_gpu_soc_mw": re.compile(r"VDD_GPU_SOC (\d+)mW"),
    "vdd_cpu_cv_mw": re.compile(r"VDD_CPU_CV (\d+)mW"),
    "vin_sys_5v0_mw": re.compile(r"VIN_SYS_5V0 (\d+)mW"),
    "tj_c": re.compile(r"tj@([\d.]+)C"),
}


def parse_tegrastats(path):
    rows = {k: [] for k in TEGRA}
    cpu = []
    for line in Path(path).read_text().splitlines():
        for k, rx in TEGRA.items():
            m = rx.search(line)
            if m:
                rows[k].append(float(m.group(1)))
        m = re.search(r"CPU \[([^\]]*)\]", line)
        if m:
            cores = [int(c.split("%")[0]) for c in m.group(1).split(",") if "%" in c]
            cpu.append(cores)
    out = {}
    for k, xs in rows.items():
        if xs:
            xs.sort()
            out[k] = {
                "mean": round(statistics.mean(xs), 1),
                "p95": xs[int(0.95 * (len(xs) - 1))],
                "max": xs[-1],
            }
    if cpu:
        per_core = [
            round(statistics.mean(c[i] for c in cpu), 1) for i in range(len(cpu[0]))
        ]
        out["cpu_core_pct"] = per_core
        out["cpu_total_pct"] = round(statistics.mean(per_core), 1)  # of all 12 cores
    out["samples"] = len(cpu)
    return out


def containers():
    """{sheppy-ish name: full id} for running containers."""
    out = subprocess.run(
        ["docker", "ps", "--no-trunc", "--format", "{{.ID}} {{.Names}}"],
        capture_output=True,
        text=True,
    ).stdout
    return {name: cid for cid, name in (l.split() for l in out.splitlines())}


def cgroup_read(cid):
    base = Path("/sys/fs/cgroup/system.slice/docker-%s.scope" % cid)
    try:
        usage = int(
            re.search(r"usage_usec (\d+)", (base / "cpu.stat").read_text()).group(1)
        )
        return usage / 1e6, int((base / "memory.current").read_text())
    except (OSError, AttributeError):
        return None


def host_procs():
    """Bench processes on the host (owl_node, mocks, static TF): {label: [pids]}."""
    out = {}
    for d in Path("/proc").iterdir():
        if not d.name.isdigit():
            continue
        try:
            cmd = (d / "cmdline").read_bytes().replace(b"\0", b" ").decode()
        except OSError:
            continue
        for key in (
            "owl_node.py",
            "gpu_load.py",
            "replay_camera.py",
            "static_transform_publisher",
            "ros2 topic pub",
        ):
            if key in cmd and "bash -lc" not in cmd:
                out.setdefault(key, []).append(int(d.name))
    return out


def proc_read(pids):
    tick = os.sysconf("SC_CLK_TCK")
    cpu = rss = 0
    for pid in pids:
        try:
            f = Path("/proc/%d/stat" % pid).read_text().rsplit(")", 1)[1].split()
            cpu += (int(f[11]) + int(f[12])) / tick
            rss += int(f[21]) * os.sysconf("SC_PAGE_SIZE")
        except OSError:
            pass
    return cpu, rss


class Sampler(threading.Thread):
    """CPU (cores) and peak memory per container and per bench host process over the window."""

    def __init__(self):
        super().__init__(daemon=True)
        self.stop = threading.Event()
        self.ids, self.procs = containers(), host_procs()
        self.first, self.last, self.peak = {}, {}, {}

    def read(self):
        now = time.monotonic()
        vals = {name: cgroup_read(cid) for name, cid in self.ids.items()}
        vals.update({"host:" + k: proc_read(p) for k, p in self.procs.items()})
        for name, v in vals.items():
            if v is None:
                continue
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
                    "cpu_cores": round((c1 - c0) / (t1 - t0), 2),
                    "mem_peak_mb": round(self.peak[name] / 2**20),
                }
        return dict(sorted(out.items(), key=lambda kv: -kv[1]["cpu_cores"]))


# --- one run ---------------------------------------------------------------------------------------------


def sheppy(*args, timeout=900):
    r = subprocess.run([SHEPPY, *args], capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr)[-3000:]


def probe_cmd(manifest, run, duration):
    topics, require = [], []
    if run.get("base"):
        topics.append(CAMERA + ":image")
        require.append(CAMERA)
    if run.get("detector"):
        t = (
            BENCH_TOPIC + ":header"
            if DETECTORS[run["detector"]][1] is None
            else DETECTIONS + ":dets"
        )
        topics.append(t)
        require.append(t.split(":")[0])
    if run.get("sam3"):
        topics.append(SAM3_TOPIC + ":dets")
    if run.get("boxer"):
        topics.append(LIFTED + ":dets")
        require.append(LIFTED)
    args = ["--duration", str(duration)]
    for t in topics:
        args += ["--topic", t]
    for t in require:
        args += ["--require", t]
    if run.get("sam3") == "track":
        args += ["--sam3-track", SAM3_PROMPTS, "--sam3-detect", DETECT_PROMPTS]
    image = image_of(manifest, "sam3", "sam3_docker")
    inner = (
        "source /opt/ros/humble/setup.bash && source /module_ws/install/setup.bash && exec python3 -u /perf/ros_probe.py "
        + " ".join(map(shlex.quote, args))
    )
    return [
        "docker",
        "run",
        "--rm",
        "--name",
        "perf_probe",
        "--network",
        "host",
        "--ipc",
        "host",
        "-e",
        "ROS_DOMAIN_ID=0",
        "-e",
        "CYCLONEDDS_URI=file://" + LOOPBACK,
        "-v",
        "%s:%s:ro" % (LOOPBACK, LOOPBACK),
        "-v",
        "%s:/perf:ro" % Path(__file__).resolve().parent,
        "--entrypoint",
        "bash",
        image,
        "-c",
        inner,
    ]


def segmenter_log(run):
    node = "scene_segmenter" if run.get("detector") else None
    out = {}
    for n in filter(
        None,
        [
            node,
            "sam3" if run.get("sam3") else None,
            "object_lifter" if run.get("boxer") else None,
        ],
    ):
        _, log = sheppy("logs", n, "-n", "40", timeout=30)
        lines = [l for l in log.splitlines() if re.search(r"Hz|ms|infer|rate", l)]
        out[n] = lines[-4:]
    return out


def probe_json(lines):
    """The probe's JSON line: the last line that parses (shutdown warnings may follow it)."""
    for line in reversed(lines):
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                pass
    return {"errors": ["probe output: " + " | ".join(lines[-10:])]}


def execute(name, duration, settle):
    run = RUNS[name]
    print("== %s %s" % (name, json.dumps(run)), flush=True)
    t0 = time.monotonic()
    manifest = build_stack(run)
    if run:
        code, log = sheppy(
            "up", "bench", "--manifest", str(STACK / "sheppy-manifest.yaml")
        )
    else:
        code, log = sheppy("down")
    result = {
        "run": name,
        "config": run,
        "sheppy_exit": code,
        "sheppy_tail": log[-1200:],
    }

    tegra_log = RESULTS / ("%s.tegrastats" % name)
    tegra_log.unlink(missing_ok=True)
    sampler = tegra = None

    def start_sampling():
        nonlocal sampler, tegra
        sampler = Sampler()
        tegra = subprocess.Popen(
            ["tegrastats", "--interval", "500", "--logfile", str(tegra_log)]
        )
        sampler.read()
        sampler.start()

    lines = []
    if not run:  # idle: just the machine
        time.sleep(settle)
        start_sampling()
        time.sleep(duration)
    else:
        # The probe waits for its topics and warms up, then prints MEASURE_START and MEASURE_END around
        # its window; the system samplers cover the same window.
        probe = subprocess.Popen(
            probe_cmd(manifest, run, duration),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        for line in probe.stdout:
            line = line.rstrip()
            if line == "MEASURE_START":
                start_sampling()
            elif line == "MEASURE_END":
                break
            else:
                lines.append(line)
        lines += [l.rstrip() for l in probe.stdout]
        probe.wait()
    if sampler:
        sampler.read()
        sampler.stop.set()
        tegra.terminate()
        tegra.wait()

    if run:
        result["probe"] = probe_json(lines)
    result["tegrastats"] = parse_tegrastats(tegra_log) if tegra_log.exists() else {}
    result["processes"] = sampler.result() if sampler else {}
    result["node_logs"] = segmenter_log(run)
    result["wall_s"] = round(time.monotonic() - t0)
    (RESULTS / ("%s.json" % name)).write_text(json.dumps(result, indent=1))
    p = result.get("probe", {})
    print(
        json.dumps(
            {
                "run": name,
                "topics": {
                    t: (v.get("hz"), (v.get("latency_ms") or {}).get("p50"))
                    for t, v in p.get("topics", {}).items()
                },
                "gpu": result["tegrastats"].get("gpu_pct", {}).get("mean"),
                "cpu": result["tegrastats"].get("cpu_total_pct"),
                "errors": p.get("errors"),
            }
        ),
        flush=True,
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="*")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--duration", type=float, default=60)
    ap.add_argument(
        "--settle", type=float, default=10, help="idle run: seconds before sampling"
    )
    a = ap.parse_args()
    if a.list or not a.runs:
        print("\n".join("%-28s %s" % (k, json.dumps(v)) for k, v in RUNS.items()))
        return
    names = list(RUNS) if a.runs == ["all"] else a.runs
    unknown = [n for n in names if n not in RUNS]
    if unknown:
        sys.exit("unknown runs: %s" % unknown)
    RESULTS.mkdir(parents=True, exist_ok=True)
    for n in names:
        execute(n, a.duration, a.settle)


if __name__ == "__main__":
    main()
