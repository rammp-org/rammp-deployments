# Detector quality check

Compares the always-on detectors on the same images, against SAM3 at full quality as the reference
(rammp-perception#11). Bench only: the paths are abra's (`~/detector-bench` for the older test images,
`~/sam3-test` for a SAM3 venv and weights, `~/nanoowl-dev` for the work area).

| File               | What it does                                                                                    |
| ------------------ | ----------------------------------------------------------------------------------------------- |
| `make_evalset.py`  | eval images: two scene-camera views, 15 handheld D405 microwave frames, 4 kitchen photos       |
| `ref_sam3.py`      | SAM3 boxes for the five class-list labels (one or two prompts each), duplicates merged          |
| `dump_detectors.py`| one detector over the images at a 0.03 threshold: YOLOE-26m, RF-DETR-Seg S, OWL-ViT B/32, B/16  |
| `score.py`         | per-label AP and found / false positives at each detector's threshold (IoU 0.5), OWL sweeps, sheets |
| `run.sh`           | runs a command in a perception image with the work area mounted                                 |
| `eval_all.sh`      | all of the above except scoring, which runs on the workstation                                  |

```bash
~/nanoowl-dev/scripts/eval_all.sh                                  # on abra
rsync -a abra:nanoowl-dev/data/eval abra:nanoowl-dev/data/dets data/
uv run --with numpy --with opencv-python-headless python score.py data out 0.2 0.3
```

## 2026-10-19 result

21 images, 57 reference objects. OWL at threshold 0.2, NMS IoU 0.3 (the best total F1 for both models); YOLOE and
RF-DETR at the class list's thresholds.

| Detector     | mAP  | Found / false positives | food container | drink cup | fridge | microwave | table |
| ------------ | ---- | ----------------------- | -------------- | --------- | ------ | --------- | ----- |
| YOLOE-26m    | 0.86 | 35 / 3                  | 0.77           | 0.64      | 1.00   | 0.97      | 0.92  |
| RF-DETR-Seg S| 0.68 | 37 / 8                  | 0.72           | 0.39      | 0.66   | 0.98      | 0.63  |
| OWL-ViT B/32 | 0.51 | 21 / 21                 | 0.68           | 0.03      | 1.00   | 0.60      | 0.23  |
| OWL-ViT B/16 | 0.46 | 18 / 6                  | 0.72           | 0.23      | 0.53   | 0.57      | 0.25  |

Per-label columns are AP. Caveats: the set is small and has few cups and containers (6 and 8); the reference is
SAM3, not hand labels. `../reports/2026-10-19-quality/` has two side-by-side sheets.
