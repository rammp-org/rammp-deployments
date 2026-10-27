"""Run one detector over /data/eval/*.jpg at a low threshold; write /data/dets/<name>/<image>.npz (labels, scores, boxes).

usage: dump_detectors.py yoloe-26m | rfdetr-s | owlvit-b32 | owlvit-b16
Thresholds and NMS are left to the scorer: YOLOE and RF-DETR go through the node's clean() (duplicate merge) with
every class threshold at LOW; OWL candidates are dumped before NMS so the scorer can sweep nms_iou.
"""

import glob
import os
import sys
import cv2
import numpy as np
from rammp_detection_common.classes import ClassMap, clean

LOW = 0.03
name = sys.argv[1]
classes = ClassMap.load("/repo/object_detection/config/classes.yaml")
classes.min_scores = {l: LOW for l in classes.labels}
out = f"/data/dets/{name}"
os.makedirs(out, exist_ok=True)

if name == "yoloe-26m":
    from ultralytics import YOLO
    from rammp_yoloe import engine

    path = engine.engine_path(
        "/cache/engines", "yoloe-26m-seg", classes.prompts, 640, "gpu"
    )
    assert os.path.exists(path), f"no engine {path}"
    model = YOLO(path, task="segment")

    def detect(rgb):
        r = model.predict(
            np.ascontiguousarray(rgb[..., ::-1]), imgsz=640, conf=LOW, verbose=False
        )[0]
        return [
            (classes.prompt_to_label[r.names[int(c)]], float(s), b)
            for c, s, b in zip(
                r.boxes.cls.cpu().numpy(),
                r.boxes.conf.cpu().numpy(),
                r.boxes.xyxy.cpu().numpy(),
            )
        ]
elif name == "rfdetr-s":
    import rfdetr
    import torch
    from rfdetr.assets.coco_classes import COCO_CLASSES

    model = rfdetr.RFDETRSegSmall()
    model.inference(dtype=torch.float16)

    def detect(rgb):
        d = model.predict(rgb, threshold=LOW)
        return [
            (classes.coco_to_label[COCO_CLASSES[int(c)]], float(s), b)
            for c, s, b in zip(d.class_id, d.confidence, d.xyxy)
            if COCO_CLASSES.get(int(c)) in classes.coco_to_label
        ]
else:
    import torch
    from rammp_nanoowl import owl
    from rammp_nanoowl.boxes import to_source_xyxy

    groups = [
        [p for p, l in classes.prompt_to_label.items() if l == g]
        for g in classes.labels
    ]
    det = owl.OwlDetector("/models/owlvit", "/engines", name, groups)
    thr = torch.full((len(groups),), LOW, device="cuda")

    def detect(rgb):
        b, s, g = det(rgb, thr)
        return [
            (classes.labels[int(gi)], float(si), bi)
            for bi, si, gi in zip(to_source_xyxy(b, *rgb.shape[:2]), s, g)
        ]


from rammp_detection_common.classes import Detection

for p in sorted(glob.glob("/data/eval/*.jpg")):
    rgb = cv2.imread(p)[..., ::-1].copy()
    dets = detect(rgb)
    if not name.startswith("owlvit"):
        dets = [
            (d.label, d.score, d.box)
            for d in clean(
                [Detection(l, s, np.asarray(b, float)) for l, s, b in dets], classes
            )
        ]
    np.savez(
        f"{out}/{os.path.splitext(os.path.basename(p))[0]}.npz",
        labels=np.array([d[0] for d in dets], dtype="U32"),
        scores=np.array([d[1] for d in dets], np.float32),
        boxes=np.array([d[2] for d in dets], np.float32).reshape(-1, 4),
    )
print(name, "done", len(glob.glob(f"{out}/*.npz")))
