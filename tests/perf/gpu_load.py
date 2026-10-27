"""Synthetic GPU load for the bench: keeps the GPU busy `--duty` of the time, in ~20 ms bursts of FP16 matmuls,
standing in for the other GPU work on the chair (planner, modules). Logs its achieved duty every 10 s."""

import argparse
import time

import torch

p = argparse.ArgumentParser()
p.add_argument("--duty", type=float, default=0.5)
p.add_argument("--burst-ms", type=float, default=20)
a = p.parse_args()

x = torch.randn(2048, 2048, device="cuda", dtype=torch.float16)
torch.cuda.synchronize()
busy = total = 0.0
last = time.monotonic()
while True:
    t0 = time.monotonic()
    while (time.monotonic() - t0) * 1e3 < a.burst_ms:
        for _ in range(4):
            y = x @ x
        torch.cuda.synchronize()
    work = time.monotonic() - t0
    time.sleep(work * (1 - a.duty) / a.duty)
    busy += work
    total += time.monotonic() - t0
    if time.monotonic() - last > 10:
        print("gpu_load: duty %.2f (target %.2f)" % (busy / total, a.duty), flush=True)
        busy = total = 0.0
        last = time.monotonic()
