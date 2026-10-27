"""Topic rates and camera-to-result latency, plus SAM3 tracking and detect calls, for one bench run.

Runs inside a rammp-perception image (it needs rammp_perception_interfaces). Every topic is subscribed raw
and only the leading std_msgs/Header is decoded from the CDR bytes, so probing a 30 fps image stream costs
almost nothing. Latency is receive time minus header.stamp: every node copies the camera frame's stamp, so
for a detector topic it is camera-to-result latency (camera driver included).

Prints one JSON object on the last line of stdout.
"""

import argparse
import json
import struct
import threading
import time

import rclpy
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data

TYPES = {
    "image": ("sensor_msgs.msg", "Image"),
    "dets": ("rammp_perception_interfaces.msg", "ObjectDetections"),
    "header": ("std_msgs.msg", "Header"),
}


def msg_type(kind):
    module, name = TYPES[kind]
    return getattr(__import__(module, fromlist=[name]), name)


def pct(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))]


class Probe(Node):
    def __init__(self, topics):
        super().__init__("perf_probe")
        self.lock = threading.Lock()
        self.recording = False
        self.samples = {t: [] for t, _ in topics}
        self.seen = {t: 0 for t, _ in topics}
        for topic, kind in topics:
            self.create_subscription(
                msg_type(kind),
                topic,
                lambda raw, t=topic: self.on_msg(t, raw),
                qos_profile_sensor_data,
                raw=True,
            )

    def on_msg(self, topic, raw):
        now = time.time()
        sec, nsec = struct.unpack_from(
            "<iI", raw, 4
        )  # after the 4-byte CDR encapsulation header
        with self.lock:
            self.seen[topic] += 1
            if self.recording:
                self.samples[topic].append((now, sec + nsec * 1e-9))


def summarize(samples, duration):
    if not samples:
        return {"count": 0, "hz": 0.0}
    recv = [r for r, _ in samples]
    lat = [(r - s) * 1e3 for r, s in samples]
    gaps = [(b - a) * 1e3 for a, b in zip(recv, recv[1:])]
    return {
        "count": len(samples),
        "hz": round(len(samples) / duration, 2),
        "latency_ms": {
            k: round(pct(lat, q), 1)
            for k, q in (("p50", 0.5), ("p95", 0.95), ("max", 1.0))
        },
        "gap_ms": {
            k: round(pct(gaps, q), 1)
            for k, q in (("p50", 0.5), ("p95", 0.95), ("max", 1.0))
        }
        if gaps
        else None,
    }


def call(node, client, request, timeout):
    t0 = time.monotonic()
    future = client.call_async(request)
    while not future.done():
        if time.monotonic() - t0 > timeout:
            return None, None
        time.sleep(0.005)
    return future.result(), (time.monotonic() - t0) * 1e3


def main():
    p = argparse.ArgumentParser()
    p.add_argument(
        "--topic",
        action="append",
        default=[],
        help="name:kind, kind one of " + ",".join(TYPES),
    )
    p.add_argument(
        "--require",
        action="append",
        default=[],
        help="topics that must publish before measuring",
    )
    p.add_argument("--wait-timeout", type=float, default=900)
    p.add_argument("--warmup", type=float, default=15)
    p.add_argument("--duration", type=float, default=60)
    p.add_argument(
        "--sam3-track",
        default="",
        help="comma-separated prompts for a tracking session",
    )
    p.add_argument(
        "--sam3-detect", default="", help="comma-separated prompts for detect calls"
    )
    p.add_argument("--sam3-detect-n", type=int, default=5)
    a = p.parse_args()

    topics = [tuple(t.rsplit(":", 1)) for t in a.topic]
    rclpy.init()
    node = Probe(topics)
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)
    threading.Thread(target=executor.spin, daemon=True).start()
    out = {"topics": {}, "errors": []}

    t0 = time.monotonic()
    while any(node.seen[t] == 0 for t in a.require):
        if time.monotonic() - t0 > a.wait_timeout:
            out["errors"].append(
                "no messages on %s after %.0f s"
                % ([t for t in a.require if not node.seen[t]], a.wait_timeout)
            )
            break
        time.sleep(1)
    out["ready_after_s"] = round(time.monotonic() - t0, 1)

    from rammp_perception_interfaces.srv import Detect, StartTracking, StopTracking

    session = None
    if a.sam3_track:
        client = node.create_client(StartTracking, "/perception/sam3/start_tracking")
        if not client.wait_for_service(timeout_sec=120):
            out["errors"].append("sam3 start_tracking unavailable")
        else:
            req = StartTracking.Request(prompts=a.sam3_track.split(","))
            res, ms = call(node, client, req, 60)
            if res is None or not res.accepted:
                out["errors"].append(
                    "start_tracking failed: %s" % (res.message if res else "timeout")
                )
            else:
                session = res.session_id
                out["sam3_start_tracking"] = {
                    "ms": round(ms),
                    "objects": len(res.detections.objects),
                }

    time.sleep(a.warmup)
    with node.lock:
        node.recording = True
    start = time.monotonic()
    print("MEASURE_START", flush=True)

    detect_ms, detect_objects = [], []
    if a.sam3_detect:
        client = node.create_client(Detect, "/perception/sam3/detect")
        if not client.wait_for_service(timeout_sec=60):
            out["errors"].append("sam3 detect unavailable")
        else:
            spacing = a.duration / (a.sam3_detect_n + 1)
            for i in range(a.sam3_detect_n):
                time.sleep(max(0.0, start + spacing * (i + 1) - time.monotonic()))
                res, ms = call(
                    node, client, Detect.Request(prompts=a.sam3_detect.split(",")), 30
                )
                if res is None or not res.success:
                    out["errors"].append(
                        "detect failed: %s" % (res.message if res else "timeout")
                    )
                else:
                    detect_ms.append(ms)
                    detect_objects.append(len(res.detections.objects))

    time.sleep(max(0.0, start + a.duration - time.monotonic()))
    with node.lock:
        node.recording = False
        elapsed = time.monotonic() - start
        for topic, _ in topics:
            out["topics"][topic] = summarize(node.samples[topic], elapsed)
    print("MEASURE_END", flush=True)

    if detect_ms:
        out["sam3_detect"] = {
            "n": len(detect_ms),
            "ms_p50": round(pct(detect_ms, 0.5)),
            "ms_max": round(max(detect_ms)),
            "objects": detect_objects,
        }
    if session is not None:
        client = node.create_client(StopTracking, "/perception/sam3/stop_tracking")
        call(node, client, StopTracking.Request(session_id=session), 10)

    print(json.dumps(out))
    executor.shutdown()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
