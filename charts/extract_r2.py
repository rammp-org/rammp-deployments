#!/usr/bin/env python3
"""Round 2 (2026-11-17): reduce the snorlax logs to the small files make_charts_r2.py uses.

    python3 extract_r2.py <raw dir> <data dir>

<raw dir> holds copies filtered on snorlax from ~/claude-scratch/odomdrop-20261117 and
~/claude-scratch/odomdrop-20261116/stress:
  {A,C}_rate.csv          t,odom,odom_pubs,run_s   from watch/<sub>.jsonl kind=rate
  {A,C}_events.jsonl      non-rate lines of watch/<sub>.jsonl
  spdp_census.jsonl       watch/spdp.jsonl kind=census
  console/mib_console.log, mcast/mcast.jsonl, stress/cases.jsonl  as on snorlax
"""
import csv, json, os, re, sys

raw, out = sys.argv[1], sys.argv[2]
os.makedirs(out, exist_ok=True)
J = lambda *p: os.path.join(raw, *p)

rate = {}
for s in ("A", "C"):
    for r in csv.DictReader(open(J(f"{s}_rate.csv"))):
        rate.setdefault(int(float(r["t"])), {})[s] = (r["odom"], r["odom_pubs"])
with open(os.path.join(out, "odom_rate_1s.csv"), "w") as f:
    f.write("t,A,C,A_pubs,C_pubs\n")
    for t in sorted(rate):
        d = rate[t]
        f.write(f"{t},{d.get('A', ('', ''))[0]},{d.get('C', ('', ''))[0]},{d.get('A', ('', ''))[1]},{d.get('C', ('', ''))[1]}\n")

with open(os.path.join(out, "eno1_mcast_1s.csv"), "w") as f:
    f.write("t,tx_mc,tx_bc\n")
    for l in open(J("mcast", "mcast.jsonl")):
        d = json.loads(l)
        f.write(f"{d['t']},{d['tx_mc']},{d['tx_bc']}\n")

pool, ev = [], []
for l in open(J("console", "mib_console.log"), errors="replace"):
    try:
        t = float(l.split()[0])
    except (ValueError, IndexError):
        continue
    if "RESSOURCE LIMIT EXCEEDED" in l:
        pool.append(t)
    m = re.search(r"forgot an (unused|expired) participant ([0-9a-f]+) \((\d+) s since", l)
    if m:
        ev.append({"t": t, "why": m.group(1), "id": m.group(2), "age_s": int(m.group(3))})
census = [json.loads(l) for l in open(J("spdp_census.jsonl"))]
starts = {s: [json.loads(l)["t"] for l in open(J(f"{s}_events.jsonl")) if '"kind": "start"' in l] for s in ("A", "C")}
json.dump({
    "mib_pool_limit_lines": pool, "mib_evictions": ev,
    "census": [{"t": c["t"], "total": c["total"], "live": c["live"]} for c in census],
    "subscriber_starts": starts,
    "host_log": {
        "enp3s0_first_up_10M": 1794927589.0, "enp3s0_up_1G": 1794927620.0,
        "src": "journalctl -k on snorlax: enp3s0 up 10 Mbit/s half 09:59:49, down 09:59:51, up 10 Mbit/s 10:00:10, down 10:00:18, up 1 Gbit/s 10:00:20"},
    "unguarded_s7": {"t0": 1794929054.0, "t1": 1794929129.0,
                     "src": "stress/logs/S7_imgsub_1_1794929054.log (1 subscriber, color+depth, RELIABLE, 894 msgs each); end time from the coordinator's note 10:25:29"},
}, open(os.path.join(out, "events.json"), "w"), indent=1)

with open(os.path.join(out, "cases_both_days.jsonl"), "w") as f:
    for l in open(J("stress", "cases.jsonl")):
        r = json.loads(l)
        if r["params"].get("tag") not in ("fw048_baseline", "img2_fw0410c"):
            continue
        for k in ("console_before", "console_after"):
            r.pop(k, None)
        if isinstance(r.get("mib_log"), dict):
            r["mib_log"].pop("evicted", None)
        f.write(json.dumps(r) + "\n")
