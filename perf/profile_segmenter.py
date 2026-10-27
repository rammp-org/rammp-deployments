"""Where a scene segmenter's per-frame time goes, outside ROS: runs inside a rammp-perception image on recorded
frames and times each stage of rammp_detection_common's _process, then prints cProfile's top functions.

  docker run --rm --runtime nvidia -v rammp_perception_engines:/cache/engines -v ~/perf-bench:/b \\
    --entrypoint /ros_entrypoint.sh <yoloe or rfdetr image> python3 /b/perf/profile_segmenter.py yoloe \\
    /b/recording.npz model=yoloe-26m-seg device=gpu
"""

import cProfile
import io
import logging
import pstats
import sys
import time

import numpy as np
from rclpy.serialization import serialize_message
from sensor_msgs.msg import Image

from rammp_detection_common import node as common
from rammp_detection_common.classes import ClassMap, clean


class FakeNode:
    """What a backend's constructor reads from its DetectorNode."""

    def __init__(self, params):
        self.params, self.tracking, self.track_buffer_frames = params, True, 10
        logging.basicConfig(level=logging.INFO)

    def declare_parameter(self, name, default):
        v = self.params.get(name, default)
        return type("P", (), {"value": type(default)(v) if default is not None else v})

    def get_logger(self):
        return logging.getLogger("backend")


def backend(kind, node, classes):
    if kind == "yoloe":
        from rammp_yoloe.node import YoloeBackend

        return YoloeBackend(node, classes)
    from rammp_rfdetr.node import RfdetrBackend

    return RfdetrBackend(node, classes)


def blocking_sync():
    """CUDA waits by yielding instead of spinning a core (cudaDeviceScheduleBlockingSync), set before any context."""
    import ctypes

    for name in ("libcudart.so.12", "libcudart.so"):
        try:
            lib = ctypes.CDLL(name)
            break
        except OSError:
            continue
    assert lib.cudaSetDeviceFlags(4) == 0, "cudaSetDeviceFlags failed"


def thread_cpu():
    """{(tid, name): CPU seconds} for every thread of this process."""
    import os

    tick, out = os.sysconf("SC_CLK_TCK"), {}
    for tid in os.listdir("/proc/self/task"):
        try:
            stat = open("/proc/self/task/%s/stat" % tid).read()
            name = stat[stat.index("(") + 1 : stat.rindex(")")]
            f = stat.rsplit(")", 1)[1].split()
            out[(tid, name)] = (int(f[11]) + int(f[12])) / tick
        except OSError:
            pass
    return out


def main():
    kind, path, *kv = sys.argv[1:]
    params = dict(a.split("=", 1) for a in kv)
    n = int(params.pop("frames", 150))
    threads = params.pop("threads", None)
    # period_ms: pace frames as the node does at a stride (100 = 10 Hz); cpu then covers the idle gap too, where
    # spinning worker threads keep burning CPU.
    period = float(params.pop("period_ms", 0)) / 1e3
    if params.pop("blocking_sync", "0") == "1":
        blocking_sync()
    if threads:
        import cv2
        import torch

        torch.set_num_threads(int(threads))
        cv2.setNumThreads(int(threads))
    classes = ClassMap.load("/etc/rammp/perception/classes.yaml")
    b = backend(kind, FakeNode(params), classes)
    color = np.load(path)["color"]
    h, w = color.shape[1:3]
    msgs = [
        Image(height=h, width=w, encoding="rgb8", step=w * 3, data=color[i].tobytes())
        for i in range(len(color))
    ]

    stages = {
        k: []
        for k in (
            "image_to_rgb",
            "infer",
            "clean",
            "label_image",
            "build_msg",
            "serialize",
            "total",
            "cpu",
        )
    }
    published = []
    # Stand-in for the publisher: build the message exactly as _publish does, then serialize it as rclpy would.
    fake = type(
        "N",
        (),
        {
            "pub_detections": type(
                "P", (), {"publish": lambda self, m: published.append(m)}
            )()
        },
    )()

    def frame(msg):
        c0 = time.process_time()  # CPU time of every thread in the process
        t = [time.perf_counter()]
        rgb = common.image_to_rgb(msg)
        t.append(time.perf_counter())
        dets = b.infer(rgb)
        t.append(time.perf_counter())
        dets = clean(dets, classes)
        t.append(time.perf_counter())
        labels = common.label_image(dets, h, w)
        t.append(time.perf_counter())
        common.DetectorNode._publish(fake, msg, dets, labels)
        t.append(time.perf_counter())
        serialize_message(published.pop())
        t.append(time.perf_counter())
        for k, a, z in zip(stages, t, t[1:]):
            stages[k].append((z - a) * 1e3)
        stages["total"].append((t[-1] - t[0]) * 1e3)
        time.sleep(max(0.0, period - (time.perf_counter() - t[0])))
        stages["cpu"].append((time.process_time() - c0) * 1e3)
        return len(dets)

    for i in range(20):  # warm up, and let the tracker settle
        frame(msgs[i % len(msgs)])
    for v in stages.values():
        v.clear()
    before, t0 = thread_cpu(), time.perf_counter()
    counts = [frame(msgs[i % len(msgs)]) for i in range(n)]  # stage times, profiler off
    after, wall = thread_cpu(), time.perf_counter() - t0
    print(
        "PROFILE %s %s threads=%s period=%.0fms, %d frames %dx%d, %.1f objects/frame"
        % (kind, params, threads, period * 1e3, n, w, h, np.mean(counts))
    )
    for k, v in stages.items():
        print(
            "  %-13s median %6.1f ms  p95 %6.1f ms"
            % (k, np.median(v), np.percentile(v, 95))
        )
    by_name = {}
    for k, v in after.items():
        by_name.setdefault(k[1], []).append(v - before.get(k, 0.0))
    print("  threads by name (count, cores busy over the run):")
    for name, vs in sorted(by_name.items(), key=lambda kv: -sum(kv[1]))[:8]:
        print("    %-18s %3d  %.2f" % (name, len(vs), sum(vs) / wall))
    prof = cProfile.Profile()
    prof.enable()
    period = 0.0
    for i in range(min(n, 60)):
        frame(msgs[i % len(msgs)])
    prof.disable()
    s = io.StringIO()
    pstats.Stats(prof, stream=s).sort_stats("tottime").print_stats(25)
    print("\n".join(s.getvalue().splitlines()[:45]))


if __name__ == "__main__":
    main()
