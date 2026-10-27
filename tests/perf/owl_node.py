"""OWLv2 or NanoOWL on the scene camera, for the bench only: neither has a rammp-perception image.

Takes frames the way the scene segmenter does (every `--stride`th camera frame; a model slower than that
takes the newest frame as soon as it is free), runs the class list's prompts, and publishes the source
frame's std_msgs/Header on /perception/bench/detections so ros_probe measures it like any detector.
Boxes are computed and thrown away. Logs inference time every 10 s.

  owlv2        google/owlv2-base-patch16-ensemble through rammp_box_opening's FastOwl (GPU preprocessing),
               as the box-opening owl_detector runs it; host python with RAMMP-box-opening sourced.
  nanoowl-b32  NanoOWL OWL-ViT B/32, TensorRT FP16 image encoder; ~/detector-bench/.venv python.
"""

import argparse
import threading
import time

import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image
from std_msgs.msg import Header

PROMPTS = [
    "bowl",
    "food container",
    "plastic food container",
    "tupperware",
    "lunch box",
    "cup",
    "mug",
    "drink cup",
    "glass",
    "refrigerator",
    "fridge",
    "microwave",
    "table",
    "dining table",
]  # every prompt in rammp-perception's object_detection/config/classes.yaml
THRESHOLD = 0.1


def load(model):
    if model == "owlv2":
        from transformers import Owlv2ForObjectDetection, Owlv2Processor

        from rammp_box_opening.perception.owl_fast import FastOwl

        name = "google/owlv2-base-patch16-ensemble"
        owl = FastOwl(
            Owlv2Processor.from_pretrained(name),
            Owlv2ForObjectDetection.from_pretrained(name).eval().cuda(),
        )
        return lambda rgb: owl.detect(rgb, PROMPTS, THRESHOLD)
    if model == "nanoowl-b32":
        import PIL.Image
        from nanoowl.owl_predictor import OwlPredictor

        p = OwlPredictor(
            "google/owlvit-base-patch32",
            image_encoder_engine="/home/abra/detector-bench/engines/owlvit_b32_image_encoder.engine",
        )
        enc = p.encode_text(PROMPTS)
        return lambda rgb: p.predict(
            image=PIL.Image.fromarray(rgb),
            text=PROMPTS,
            text_encodings=enc,
            threshold=THRESHOLD,
        )
    raise SystemExit("unknown model %s" % model)


class OwlBench(Node):
    def __init__(self, model, stride):
        super().__init__("owl_bench")
        self.detect = load(model)
        self.detect(np.zeros((720, 1280, 3), np.uint8))  # first call pays CUDA init
        self.stride = stride
        self.cond = threading.Condition()
        self.latest, self.n, self.taken = None, 0, -stride
        self.pub = self.create_publisher(Header, "/perception/bench/detections", 10)
        self.create_subscription(
            Image,
            "/scene_camera/color/image_raw",
            self.on_image,
            qos_profile_sensor_data,
        )
        self.get_logger().info("%s ready, stride %d" % (model, stride))

    def on_image(self, msg):
        with self.cond:
            self.latest, self.n = msg, self.n + 1
            self.cond.notify()

    def run(self):
        times, last_log = [], time.monotonic()
        while rclpy.ok():
            with self.cond:
                self.cond.wait_for(
                    lambda: self.latest is not None
                    and self.n - self.taken >= self.stride,
                    timeout=1,
                )
                if self.latest is None or self.n - self.taken < self.stride:
                    continue
                msg, self.taken = self.latest, self.n
            rgb = np.frombuffer(msg.data, np.uint8).reshape(msg.height, msg.width, 3)
            if msg.encoding == "bgr8":
                rgb = rgb[:, :, ::-1].copy()
            t0 = time.monotonic()
            self.detect(rgb)
            times.append((time.monotonic() - t0) * 1e3)
            self.pub.publish(msg.header)
            if time.monotonic() - last_log > 10:
                times.sort()
                self.get_logger().info(
                    "%d frames, infer p50 %.1f ms p95 %.1f ms"
                    % (
                        len(times),
                        times[len(times) // 2],
                        times[int(0.95 * (len(times) - 1))],
                    )
                )
                times, last_log = [], time.monotonic()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--model", required=True)
    p.add_argument("--stride", type=int, default=3)
    a = p.parse_args()
    rclpy.init()
    node = OwlBench(a.model, a.stride)
    threading.Thread(target=rclpy.spin, args=(node,), daemon=True).start()
    node.run()


if __name__ == "__main__":
    main()
