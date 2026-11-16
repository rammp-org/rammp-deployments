#!/usr/bin/env python3
"""Reduce the snorlax odomdrop-20261116 logs to the small files the charts use.

    python3 extract.py <raw dir> <data dir>

<raw dir> is a copy of ~/claude-scratch/odomdrop-20261116 filtered on snorlax:
  {A,B,A2,C}_rate.csv      t,odom,odom_pubs,run_s   from watch/<sub>.jsonl kind=rate
  {A,B,A2,C}_events.jsonl  non-rate lines of watch/<sub>.jsonl
  live_events.jsonl        watch/live.jsonl minus tick/send_err
  live_tick.csv            t,eno1_rx_Bps,eno1_tx_Bps  from watch/live.jsonl kind=tick
  console_events.txt       grep of console/mib_console.log (abort/rst/boot lines)
  stress/cases.jsonl, stress/blob.jsonl, mcast/mcast.jsonl  as on snorlax
Times stay epoch seconds; the chart script converts to snorlax local time (America/New_York).
"""
import csv, json, os, sys

raw, out = sys.argv[1], sys.argv[2]
os.makedirs(out, exist_ok=True)
J = lambda *p: os.path.join(raw, *p)

# 1. odom rate per second, one column per subscriber, keyed on integer second
subs = ["A", "B", "A2", "C"]
rate = {}
for s in subs:
    for r in csv.DictReader(open(J(f"{s}_rate.csv"))):
        rate.setdefault(int(float(r["t"])), {})[s] = (r["odom"], r["odom_pubs"])
with open(os.path.join(out, "odom_rate_1s.csv"), "w") as f:
    f.write("t," + ",".join(subs) + "," + ",".join(s + "_pubs" for s in subs) + "\n")
    for t in sorted(rate):
        d = rate[t]
        f.write(f"{t}," + ",".join(d.get(s, ("", ""))[0] for s in subs) + "," +
                ",".join(d.get(s, ("", ""))[1] for s in subs) + "\n")

# 2. eno1 transmit bytes per second (live.py, 1 Hz, all traffic) 12:58-13:10 for the flood zoom
with open(os.path.join(out, "eno1_tx_1s_1258_1310.csv"), "w") as f:
    f.write("t,eno1_tx_Bps,eno1_rx_Bps\n")
    for r in csv.DictReader(open(J("live_tick.csv"))):
        t = float(r["t"])
        if 1794851880 <= t <= 1794852600:
            f.write(f"{t},{r['eno1_tx_Bps']},{r['eno1_rx_Bps']}\n")

# 3. eno1 multicast/broadcast frames per second from mcast.jsonl (tx side only is trusted)
with open(os.path.join(out, "eno1_mcast_1s.csv"), "w") as f:
    f.write("t,tx_mc,tx_bc\n")
    for l in open(J("mcast", "mcast.jsonl")):
        d = json.loads(l)
        f.write(f"{d['t']},{d['tx_mc']},{d['tx_bc']}\n")

# 4. events
ev = {"mib_abort": [], "wheel_reboot": [], "wheel_silence": [], "mib_reboot": [], "sub_events": [],
      "host_log": [
          {"t": 1794854352.0, "what": "enp3s0 (arm LAN) link up 1 Gbit/s at 13:39:24 after a 10 Mbit/s blip at 13:39:12",
           "t_first": 1794854352.0, "t_1g": 1794854364.0, "src": "journalctl -k on snorlax"},
          {"t": 1794856055.092, "what": "eno1 DHCP: no lease, new lease 192.168.4.53 (was 192.168.4.2)",
           "src": "journalctl NetworkManager on snorlax; MIB console 'DHCP server assigned IP ... 192.168.4.53' at 1794856055.245"},
      ]}
for l in open(J("console_events.txt")):
    if "abort() was called" in l:
        t = float(l.split(":", 1)[1].split()[0])
        ev["mib_abort"].append({"t": t, "line": l.split(" ", 1)[1].strip()})
for l in open(J("live_events.jsonl")):
    d = json.loads(l)
    k = d["kind"]
    if k == "wheel_reboot":
        ev["wheel_reboot"].append({"t": d["t"], "name": d["name"], "prev_uptime": d["prev_uptime"], "uptime_s": d["uptime_s"]})
    elif k == "wheel_back" and d["silent_s"] > 60:
        ev["wheel_silence"].append({"t_back": d["t"], "name": d["name"], "silent_s": d["silent_s"]})
    elif k == "mib_reboot":
        ev["mib_reboot"].append({"t": d["t"], "prev_uptime": d["prev_uptime"], "uptime_s": d["uptime_s"], "reset_reason": d["reset_reason"]})
for s in subs:
    for l in open(J(f"{s}_events.jsonl")):
        d = json.loads(l)
        if d.get("topic", "/base/odom") != "/base/odom":
            continue
        if d["kind"] == "gap" and d["gap_ms"] < 1000:
            continue
        d["src"] = s
        ev["sub_events"].append(d)
json.dump(ev, open(os.path.join(out, "events.json"), "w"), indent=1)

# 5. stress records without the console excerpts
with open(os.path.join(out, "cases.jsonl"), "w") as f:
    for l in open(J("stress", "cases.jsonl")):
        r = json.loads(l)
        r.pop("console_before", None); r.pop("console_after", None)
        f.write(json.dumps(r) + "\n")
with open(os.path.join(out, "blob.jsonl"), "w") as f:
    f.write(open(J("stress", "blob.jsonl")).read())
