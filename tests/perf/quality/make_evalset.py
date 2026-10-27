"""Eval images as JPEGs in /data/eval: two scene-camera views, the D405 microwave frames, the kitchen photos."""

import glob
import os
import shutil
import cv2
import numpy as np

os.makedirs("/data/eval", exist_ok=True)
for name, path in [("scene_0", "/data/recording.npz"), ("scene_1", "/data/frames.npz")]:
    z = np.load(path)
    cv2.imwrite(
        f"/data/eval/{name}.jpg",
        z["color"][0][..., ::-1],
        [cv2.IMWRITE_JPEG_QUALITY, 95],
    )
for p in sorted(
    glob.glob("/bench/frames/mw_*.jpg") + glob.glob("/bench/kitchen/k_*.jpg")
):
    shutil.copy(p, "/data/eval/")
print(len(os.listdir("/data/eval")), "images")
