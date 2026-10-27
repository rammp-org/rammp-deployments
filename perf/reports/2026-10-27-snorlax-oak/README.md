# snorlax with the OAKs, 2026-10-27

flatbot's perception stack with **RF-DETR-Seg S always on**, on a live OAK at 15 fps instead of #106's Gemini at 30 fps.
Seeed AGX Orin 64 GB, L4T R36.4.4, MAXN, clocks not locked. The DDS socket-buffer sysctl is applied
(`rmem_max` 32 MB), and every rate below is what a subscriber received.

**Camera.** The scene OAK-D Pro W was offline, so the **wrist OAK-D Pro** stood in for it: 640×400 @ 15 fps, bgr8,
on-device H.264 decoded in the camera container, with depth aligned to color. That is the same resolution and rate
as the scene OAK, but a sparser view: RF-DETR found <0.5 objects per frame, and YOLOE found none. BoxerNet therefore
lifted few boxes, and per-object costs (BoxerNet, the trackers' CPU) are lower bounds.

**How.** The sheppy daemon belonged to another session, so `pbench.py` (here) ran each node with plain
`docker run`, mirroring sheppy's entrypoint + `--ros-args --params-file`. It used the images' own DDS config
(no loopback override), the pinned images (`sha-8091bbe`, boxer `sha-f54ea8e`) and `../../ros_probe.py`.
- Each run: 20 s warm-up, 60 s measured, n=2. Values are the mean, with the range in brackets.
- Latency is camera `header.stamp` → received, so it includes the driver.
- CPU is per container, from cgroup `cpu.stat`; "perception" sums the perception containers, excluding the probe.
- Power is the sum of the three rails (`VDD_GPU_SOC + VDD_CPU_CV + VIN_SYS_5V0`). RAM is the system's, from tegrastats.
- SAM3 tracked "metal plate" (2 objects) at `max_tracking_rate_hz: 5` with rammp-perception#8's node.
- `OPENBLAS_NUM_THREADS=1` everywhere.
- BoxerNet used `frame_stride: 1`, which stacks on the segmenter's stride.

## The stack

| Configuration | Segmenter Hz | Segmenter p50 ms | SAM3 Hz | SAM3 p50 ms | 3D Hz | 3D p50 ms | Perception CPU cores | GPU % | RAM GB | Power W |
|---|---|---|---|---|---|---|---|---|---|---|
| camera only | – | – | – | – | – | – | 0 | 0 | 11.1 | 12.7 |
| **RF-DETR stride 3 (5 Hz)** + SAM3 + BoxerNet | 5.0 | 76 | 5.00 | 144 | 5.0 | 78 | 0.56 | 30.6 | 22.0 | 25.1 |
| RF-DETR stride 2 (7.5 Hz) + SAM3 + BoxerNet | 7.5 | 73 | 5.00 | 148 | 7.5 | 75 | 0.65 | 35.9 | 22.0 | 26.3 |
| RF-DETR stride 1 (15 Hz) + SAM3 + BoxerNet | 15.0 | 76 | 5.00 | 148 | 14.9 | 79 | 0.96 | 49.5 | 22.1 | 30.3 |
| RF-DETR 5 Hz stack + GPU load 70% (`gpu_load.py`) | 5.0 | 95 | 5.00 | 169 | 5.0 | 97 | 0.68 | 79.6 | 22.7 | 51.9 |
| YOLOE-26m stride 3 + SAM3 + BoxerNet (for comparison) | 5.0 | 64 | 5.00 | 142 | 5.0 | 66 | 0.48 | 26.6 | 21.6 | 24.5 |

- The camera alone adds ~35 ms from stamp to receipt, and the camera container uses 0.16 cores (H.264 decode included).
- **5 Hz (stride 3, the manifest default) is the recommended rate on flatbot.** It matches SAM3's and BoxerNet's
  5 Hz and leaves ~70% of the GPU free. 7.5 Hz costs +5% GPU and +1.2 W. 15 Hz costs +19% GPU and +5 W, since
  BoxerNet follows the segmenter's rate. Camera-to-result latency is the same at every stride.
- Everything held its rate next to a 70% GPU user. Latency grew by ~20 ms.
- YOLOE's lower cost here is mostly an empty scene: it found nothing, so BoxerNet had nothing to lift.
- SAM3 tracking is the largest GPU user (~22% of the 31%). RF-DETR is ~7% at 5 Hz, so a TensorRT engine for RF-DETR
  (it runs a TorchScript FP16 trace today) would save at most ~3-4% GPU at this rate. It only pays at 15 Hz.

## PR checks

rammp-perception#6 (OpenBLAS), #8 (SAM3 fresh frame under the cap) and #17 (on #8: the first frame after a step
is due, on a fixed schedule) were measured in the same session. The tables are in the PRs' comments, and
`python3 analyze.py results` regenerates every table from the run JSONs. In brief:
- **#6:** RF-DETR at 15 Hz uses 0.51 cores with 1 thread, against 0.96 / 1.83 / 2.15 with 2 / 4 / unset. Rate,
  latency and GPU are the same. The unset cost follows the detections per frame (3.0 cores at ~0.3/frame, 1.3 at
  ~0.05/frame), so it is higher in a busy scene. SAM3 and BoxerNet are unaffected.
- **#8:** SAM3 capped at 5 Hz goes from 270 to 147 ms p50. #17 brings it to 111 ms, with p95 at 115 instead of 176.
  Both hold 5.00 Hz at the same GPU %.

`pbench.py` and `analyze.py` are kept here as the record of how these numbers were made, not as a second bench.
`../../bench.py` still targets the dojo's Gemini (see its README).
