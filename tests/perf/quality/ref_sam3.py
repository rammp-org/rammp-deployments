"""SAM3 pseudo ground truth for the demo labels. Host: ~/sam3-test/.venv/bin/python ref_sam3.py DATA_DIR"""

import glob
import os
import sys
import numpy as np
import torch
from PIL import Image
from transformers import Sam3Model, Sam3Processor

# One or two SAM3 noun phrases per class-list label; a label's instances are the union, duplicates merged.
PROMPTS = {
    "food container": ["food container", "bowl"],
    "drink cup": ["cup", "mug"],
    "fridge": ["refrigerator"],
    "microwave": ["microwave"],
    "table": ["table"],
}
MODEL = os.path.expanduser("~/sam3-test/models/sam3")
data = sys.argv[1]
out = f"{data}/dets/sam3"
os.makedirs(out, exist_ok=True)
proc = Sam3Processor.from_pretrained(MODEL)
model = Sam3Model.from_pretrained(MODEL, dtype=torch.bfloat16).to("cuda").eval()
flat = [(lab, p) for lab, ps in PROMPTS.items() for p in ps]
ti = proc(text=[p for _, p in flat], return_tensors="pt").to("cuda")


def iou(a, b):
    x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    i = max(0, x1 - x0) * max(0, y1 - y0)
    return i / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - i)


for path in sorted(glob.glob(f"{data}/eval/*.jpg")):
    img = Image.open(path).convert("RGB")
    dets = []
    with torch.inference_mode():
        x = proc(images=img, return_tensors="pt").to("cuda")
        vis = model.get_vision_features(
            pixel_values=x["pixel_values"].to(torch.bfloat16)
        )
        for i, (label, _) in enumerate(flat):
            o = model(
                vision_embeds=vis,
                input_ids=ti["input_ids"][i : i + 1],
                attention_mask=ti["attention_mask"][i : i + 1],
            )
            r = proc.post_process_instance_segmentation(
                o,
                threshold=0.5,
                mask_threshold=0.5,
                target_sizes=x["original_sizes"].tolist(),
            )[0]
            dets += [
                (label, float(s), b)
                for b, s in zip(
                    r["boxes"].float().cpu().numpy(), r["scores"].float().cpu().numpy()
                )
            ]
    kept = []
    for d in sorted(dets, key=lambda d: -d[1]):
        if not any(k[0] == d[0] and iou(k[2], d[2]) >= 0.7 for k in kept):
            kept.append(d)
    name = os.path.splitext(os.path.basename(path))[0]
    np.savez(
        f"{out}/{name}.npz",
        labels=np.array([k[0] for k in kept], dtype="U32"),
        scores=np.array([k[1] for k in kept], np.float32),
        boxes=np.array([k[2] for k in kept], np.float32).reshape(-1, 4),
    )
    print(name, [k[0] for k in kept], flush=True)
