"""Score detector dumps against the SAM3 reference and draw side-by-side sheets.

usage: score.py DATA_DIR OUT_DIR [owl_threshold] [owl_nms_iou]
"""
import glob
import json
import os
import sys

import cv2
import numpy as np

data, outdir = sys.argv[1], sys.argv[2]
OWL_THR = float(sys.argv[3]) if len(sys.argv) > 3 else 0.1
OWL_NMS = float(sys.argv[4]) if len(sys.argv) > 4 else 0.3
LABELS = ["food container", "drink cup", "fridge", "microwave", "table"]
# Operating thresholds: YOLOE and RF-DETR use the class list's min_score.
CLASS_THR = {"food container": 0.25, "drink cup": 0.25, "fridge": 0.25, "microwave": 0.25, "table": 0.15}
DETECTORS = ["yoloe-26m", "rfdetr-s", "owlvit-b32", "owlvit-b16"]
os.makedirs(outdir, exist_ok=True)
images = sorted(os.path.splitext(os.path.basename(p))[0] for p in glob.glob(f"{data}/eval/*.jpg"))


def iou_matrix(a, b):
    x0 = np.maximum(a[:, None, 0], b[None, :, 0]); y0 = np.maximum(a[:, None, 1], b[None, :, 1])
    x1 = np.minimum(a[:, None, 2], b[None, :, 2]); y1 = np.minimum(a[:, None, 3], b[None, :, 3])
    inter = np.clip(x1 - x0, 0, None) * np.clip(y1 - y0, 0, None)
    area = lambda r: (r[:, 2] - r[:, 0]) * (r[:, 3] - r[:, 1])
    return inter / (area(a)[:, None] + area(b)[None, :] - inter + 1e-9)


def nms(boxes, scores, iou):
    order, keep = np.argsort(-scores), []
    while order.size:
        i = order[0]; keep.append(i)
        order = order[1:][iou_matrix(boxes[i:i + 1], boxes[order[1:]])[0] < iou] if order.size > 1 else order[1:]
    return np.array(keep, int)


def load(det, img, owl_nms=OWL_NMS):
    z = np.load(f"{data}/dets/{det}/{img}.npz")
    labels, scores, boxes = z["labels"], z["scores"], z["boxes"]
    if det.startswith("owlvit") and len(labels):
        keep = np.concatenate([np.flatnonzero(labels == l)[nms(boxes[labels == l], scores[labels == l], owl_nms)] for l in np.unique(labels)])
        labels, scores, boxes = labels[keep], scores[keep], boxes[keep]
    return labels, scores, boxes


def match(det, label, owl_nms=OWL_NMS):
    """All of det's detections of label over the set as (score, true positive), and the reference count."""
    out, n_ref = [], 0
    for img in images:
        rl, _, rb = load("sam3", img)
        rb = rb[rl == label]; n_ref += len(rb)
        l, s, b = load(det, img, owl_nms)
        s, b = s[l == label], b[l == label]
        order = np.argsort(-s)
        used = set()
        ious = iou_matrix(b[order], rb) if len(rb) and len(b) else np.zeros((len(b), 0))
        for k, i in enumerate(order):
            j = next((j for j in np.argsort(-ious[k]) if ious[k, j] >= 0.5 and j not in used), None) if ious.shape[1] else None
            if j is not None:
                used.add(j)
            out.append((float(s[i]), j is not None))
    return out, n_ref


def ap(pairs, n_ref):
    if not n_ref:
        return float("nan")
    pairs = sorted(pairs, key=lambda p: -p[0])
    tp = np.cumsum([p[1] for p in pairs]); fp = np.cumsum([not p[1] for p in pairs])
    rec = tp / n_ref; prec = tp / np.maximum(tp + fp, 1)
    # All-point interpolated AP.
    r = np.concatenate([[0], rec, [rec[-1] if len(rec) else 0]]); p = np.concatenate([[1], prec, [0]])
    p = np.maximum.accumulate(p[::-1])[::-1]
    return float(np.sum((r[1:] - r[:-1]) * p[1:]))


def at(pairs, n_ref, thr):
    sel = [p for p in pairs if p[0] >= thr]
    tp = sum(p[1] for p in sel)
    return tp, len(sel) - tp, n_ref


def thr_for(det, label):
    return OWL_THR if det.startswith("owlvit") else CLASS_THR[label]


results = {}
lines = ["| detector | " + " | ".join(LABELS) + " | mAP | total TP / FP / ref |", "|---" * (len(LABELS) + 3) + "|"]
for det in DETECTORS:
    cells, aps, T = [], [], [0, 0, 0]
    for label in LABELS:
        pairs, n = match(det, label)
        a = ap(pairs, n); tp, fp, n = at(pairs, n, thr_for(det, label))
        T = [T[0] + tp, T[1] + fp, T[2] + n]
        results[(det, label)] = (a, tp, fp, n)
        if n:
            aps.append(a)
        cells.append(f"AP {a:.2f}, {tp}/{n} found, {fp} FP" if n else f"{tp}/0, {fp} FP")
    lines.append(f"| {det} | " + " | ".join(cells) + f" | {np.mean(aps):.2f} | {T[0]} / {T[1]} / {T[2]} |")
print(f"Operating points: YOLOE/RF-DETR class list thresholds; OWL threshold {OWL_THR}, NMS IoU {OWL_NMS}\n")
print("\n".join(lines))

# OWL sweeps: global threshold x NMS IoU, by total F1.
print("\nOWL sweep (threshold, nms_iou): TP / FP / ref, F1")
for det in ["owlvit-b32", "owlvit-b16"]:
    best = []
    for nmsv in [0.3, 0.5]:
        per = {label: match(det, label, nmsv) for label in LABELS}
        for thr in [0.05, 0.08, 0.1, 0.12, 0.15, 0.2, 0.25]:
            tp = fp = n = 0
            for label in LABELS:
                a, b, c = at(*per[label], thr); tp += a; fp += b; n += c
            f1 = 2 * tp / max(2 * tp + fp + (n - tp), 1)
            best.append((f1, thr, nmsv, tp, fp, n))
    for f1, thr, nmsv, tp, fp, n in sorted(best, reverse=True)[:4]:
        print(f"  {det}: thr {thr}, nms {nmsv}: {tp} / {fp} / {n}, F1 {f1:.2f}")
    # Per-label best threshold at the chosen NMS.
    for label in LABELS:
        pairs, n = match(det, label)
        if not n:
            continue
        cand = [(2 * tp / max(2 * tp + fp + (n - tp), 1), thr, tp, fp) for thr in np.arange(0.04, 0.4, 0.01) for tp, fp, _ in [at(pairs, n, thr)]]
        f1, thr, tp, fp = max(cand, key=lambda c: (c[0], c[1]))
        print(f"    {label}: best thr {thr:.2f} F1 {f1:.2f} ({tp}/{n}, {fp} FP)")

# Sheets: reference and each detector at its operating point.
COL = {"food container": (60, 180, 75), "drink cup": (0, 130, 200), "fridge": (230, 25, 75), "microwave": (245, 130, 48), "table": (145, 30, 180)}
for img in images:
    base = cv2.imread(f"{data}/eval/{img}.jpg")
    panels = []
    for det in ["sam3"] + DETECTORS:
        p = base.copy()
        l, s, b = load(det, img)
        for li, si, bi in zip(l, s, b):
            if det != "sam3" and si < thr_for(det, li):
                continue
            x0, y0, x1, y1 = map(int, bi)
            c = COL[li][::-1]
            cv2.rectangle(p, (x0, y0), (x1, y1), c, 3)
            cv2.putText(p, f"{li} {si:.2f}", (x0 + 3, max(y0 + 22, 22)), cv2.FONT_HERSHEY_SIMPLEX, 0.8, c, 2)
        cv2.putText(p, det if det != "sam3" else "SAM3 reference", (10, p.shape[0] - 15), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 5)
        cv2.putText(p, det if det != "sam3" else "SAM3 reference", (10, p.shape[0] - 15), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 2)
        panels.append(cv2.resize(p, (640, int(640 * p.shape[0] / p.shape[1]))))
    blank = np.full_like(panels[0], 255)
    grid = np.concatenate([np.concatenate(panels[:3], 1), np.concatenate(panels[3:] + [blank], 1)], 0)
    cv2.imwrite(f"{outdir}/{img}.jpg", grid, [cv2.IMWRITE_JPEG_QUALITY, 80])
json.dump({f"{d}|{l}": v for (d, l), v in results.items()}, open(f"{outdir}/results.json", "w"))
