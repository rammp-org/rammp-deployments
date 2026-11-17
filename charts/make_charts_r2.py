#!/usr/bin/env python3
"""Round-2 charts for the rammp-deployments#136 comment (flatbot, 2026-11-17, #160's images).

    python3 make_charts_r2.py <r2 data dir> <png dir> [<alex-style-guide dir>]

Reads the files written by extract_r2.py. Same style as make_charts.py (alex-style dark,
opaque background). Times are snorlax local (EST).
"""
import csv, json, os, sys
from datetime import datetime
from zoneinfo import ZoneInfo

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np

DATA, OUT = sys.argv[1], sys.argv[2]
STYLE = sys.argv[3] if len(sys.argv) > 3 else "/Users/alxchunlin/code-stuff/alex-style-guide"
sys.path.insert(0, os.path.join(STYLE, "matplotlib"))
import alex_style as ax  # noqa: E402

P = ax.use("dark")
S = P["series"]
plt.rcParams.update({"xtick.labelsize": 10, "ytick.labelsize": 10, "axes.labelsize": 11, "savefig.dpi": 160})
TZ = ZoneInfo("America/New_York")
os.makedirs(OUT, exist_ok=True)
D = lambda *p: os.path.join(DATA, *p)
dt = lambda t: datetime.fromtimestamp(float(t), TZ)
HM = mdates.DateFormatter("%H:%M", tz=TZ)
Y, N = "fw048_baseline", "img2_fw0410c"  # yesterday, today

CASES = [json.loads(l) for l in open(D("cases_both_days.jsonl"))]
EV = json.load(open(D("events.json")))
day = lambda tag: [c for c in CASES if c["params"].get("tag") == tag]
get = lambda tag, name: next((c for c in day(tag) if c["case"] == name), None)


def hbar_axes(a, labels):
    a.set_yticks(np.arange(len(labels)), labels)
    a.invert_yaxis()
    a.tick_params(axis="y", colors=P["ink"], length=0)
    a.grid(axis="y", visible=False); a.grid(axis="x", visible=True)


def legend_swatches(a, items):
    for lab, col in items:
        a.bar([0], [0], color=col, label=lab)
    a.legend(loc="lower right", ncol=1, borderaxespad=0.6, handlelength=0.9, handleheight=0.9)


# ---------- 1. A/B stress matrix, longest odom gap
def chart_ab():
    order = [c["case"] for c in day(Y) if "counter_delta" in c]  # yesterday's chart 4 order
    rows = []
    for name in order:
        lab, today = name, get(N, name)
        if name in ("S7_imgsub_1", "S7_imgsub_2"):  # today's camera cases ran guarded under the S7c name
            today = get(N, name.replace("S7_", "S7c_")); lab = name + " / S7c"
        rows.append((lab, get(Y, name), today))
    rows.append(("S7c_imgsub_4 (today only)", None, get(N, "S7c_imgsub_4")))
    fig, a = plt.subplots(figsize=(11, 8.6))
    y = np.arange(len(rows)); h = 0.38
    for k, (col, off) in enumerate(((S[0], -h / 2), (S[1], h / 2))):
        for i, r in enumerate(rows):
            c = r[1 + k]
            if c is None:
                a.annotate("not run", xy=(0, i + off), xytext=(4, 0), textcoords="offset points", va="center",
                           color=P["off"], fontsize=8.5, fontfamily="Roboto Mono")
                continue
            g = c["odom"]["max_gap_s"]
            a.barh(i + off, g, color=col, height=h)
            a.annotate(f"{g:.2f}", xy=(g, i + off), xytext=(4, 0), textcoords="offset points", va="center",
                       color=P["muted"], fontsize=8.5, fontfamily="Roboto Mono")
    hbar_axes(a, [r[0] for r in rows])
    a.set_xlim(0, 31)
    a.vlines(0.5, -1.05, -0.55, color=P["warning"], lw=1.5)  # marker above the rows, clear of the value labels
    a.set_ylim(len(rows) - 0.4, -1.1)
    a.annotate("fail rule 0.5 s", xy=(0.5, -0.8), xytext=(4, 0), textcoords="offset points",
               color=P["muted"], fontsize=8.5, va="center")
    a.set_xlabel("Longest /base/odom gap at the case subscriber, s"); a.set_ylabel("Stress case")
    legend_swatches(a, [("2026-11-16: wheels 0.4.8, MIB 14ba5db, old images", S[0]),
                        ("2026-11-17: wheels 0.4.10c, MIB 2b8f48e, #160 images", S[1])])
    ax.title(a, "Stress matrix, yesterday against today",
             "One run per case per day. S7 rows: today's cases ran guarded (S7c), color topic only.")
    ax.save(fig, os.path.join(OUT, "r2_1_stress_ab.png")); plt.close(fig)


# ---------- 2. camera multicast by configuration
def chart_camera():
    s1, s2 = get(Y, "S7_imgsub_1"), get(Y, "S7_imgsub_2")
    sr = next(c for c in CASES if c["case"] == "S7r_imgsub_2_pubspdp")
    items = [("11-16, image default, 1 added sub", s1["eno1"]["stress"]["tx_multicast_per_s"], S[0]),
             ("11-16, image default, 2 added subs", s2["eno1"]["stress"]["tx_multicast_per_s"], S[1]),
             ("11-16, spdp file mounted, 3 readers (S7r)", sr["tx_mcast_mean_fps"], S[0])]
    for n in (1, 2, 4):
        c = get(N, f"S7c_imgsub_{n}")
        items.append((f"11-17, #160 images, {c['total_subscribers']} readers (S7c_imgsub_{n})", c["tx_mcast_mean_fps"], S[0]))
    fig, a = plt.subplots(figsize=(11, 4.4))
    vals = [i[1] for i in items]
    a.barh(np.arange(len(items)), vals, color=[i[2] for i in items], height=0.55)
    hbar_axes(a, [i[0] for i in items])
    a.set_xscale("log"); a.set_xlim(0.8, 60000)
    for i, v in enumerate(vals):
        a.annotate(f"{v:,.1f}", xy=(v, i), xytext=(5, 0), textcoords="offset points", va="center",
                   color=P["muted"], fontsize=9.5, fontfamily="Roboto Mono")
    a.set_xlabel("Multicast frames sent on eno1, frames/s (log)"); a.set_ylabel("Day, config, image readers")
    ax.title(a, "Scene camera multicast on eno1",
             "Red: the 11-16 run that took the MIB down. 11-16 S7 rows: 60 s means. S7r and S7c: means after the first 2 s.")
    ax.save(fig, os.path.join(OUT, "r2_2_camera_multicast.png")); plt.close(fig)


# ---------- 3. today's timeline
def chart_timeline():
    rows = list(csv.DictReader(open(D("odom_rate_1s.csv"))))
    T = np.array([int(r["t"]) for r in rows])
    R = {s: np.array([float(r[s]) if r[s] != "" else np.nan for r in rows]) for s in ("A", "C")}
    mc = list(csv.DictReader(open(D("eno1_mcast_1s.csv"))))
    t0, t1 = 1794927300, 1794930000  # 09:55 to 10:40
    fig = plt.figure(figsize=(11, 9.2))
    gs = fig.add_gridspec(4, 1, height_ratios=[2.0, 1, 1, 1.25], hspace=0.32)
    lane = fig.add_subplot(gs[0]); axs = [fig.add_subplot(gs[i], sharex=lane) for i in (1, 2, 3)]
    cases = sorted(day(N), key=lambda c: c["t_start"])
    ev = EV["mib_evictions"]; hl = EV["host_log"]; ug = EV["unguarded_s7"]

    def state(c):
        if c["odom"]["max_gap_s"] >= 0.5: return P["critical"]
        if c["verdict"] == "FAIL": return P["warning"]
        return P["off"]
    short = lambda n: n.replace("_baseline", "").replace("_imgsub", "").replace("_parts", "").replace("_readers", "r") \
                       .replace("_churn", "").replace("_bcast", " b").replace("_mcast", " m").replace("_pace", " pace").replace("_tcp", " tcp")
    for c in cases:
        lane.axvspan(dt(c["t_stress_start"]), dt(c["t_stress_end"]), ymin=0.80, ymax=0.96, color=state(c), lw=0)
        lane.annotate(short(c["case"]), xy=(dt((c["t_stress_start"] + c["t_stress_end"]) / 2), 3.6), rotation=90,
                      ha="center", va="top", color=P["ink"], fontsize=7.5, fontfamily="Roboto Mono")
    lane.vlines([dt(e["t"]) for e in ev], 0.75, 1.25, color=P["warning"], lw=1.0)
    lane.axvspan(dt(ug["t0"]), dt(ug["t1"]), ymin=0.07, ymax=0.21, color=P["warning"], lw=0)
    lane.annotate("unguarded S7", xy=(dt(ug["t1"]), 0), xytext=(4, 0), textcoords="offset points", va="center",
                  color=P["ink"], fontsize=8.5)
    for t, lab, yy in ((hl["enp3s0_up_1G"], "arm LAN up 10:00:20", 0.45),
                       (EV["subscriber_starts"]["C"][-1], "C restarted 10:02:30", -0.15)):
        lane.vlines(dt(t), yy - 0.25, yy + 0.25, color=P["ink"], lw=1.4)
        lane.annotate(lab, xy=(dt(t), yy), xytext=(4, 0), textcoords="offset points", va="center",
                      ha="left", color=P["ink"], fontsize=8.5)
    lane.set_ylim(-0.6, 5.0)
    lane.set_yticks([4.4, 1, 0], ["Stress case", "MIB eviction", "Host"])
    lane.tick_params(axis="y", colors=P["ink"], length=0); lane.grid(False); lane.set_ylabel("Event")
    ax.title(lane, "flatbot on 2026-11-17, #160 images",
             "Case colour: red = odom gap 0.5 s or more, yellow = PACE? only, gray = pass. Yellow ticks: MIB pool evictions.")
    for a, s, lab, col in ((axs[0], "A", "A, ~/cyclonedds.xml (eno1, AllowMulticast true)", S[0]),
                           (axs[1], "C", "C, the containers' layered config (fleet + robot.xml)", S[1])):
        for c in cases:
            if c["odom"]["max_gap_s"] >= 0.5:
                a.axvspan(dt(c["t_stress_start"]), dt(c["t_stress_end"]), color=P["critical"], alpha=0.15, lw=0)
        a.axvline(dt(hl["enp3s0_up_1G"]), color=P["ink"], lw=0.8, ls=(0, (2, 3)))
        m = (T >= t0) & (T <= t1)
        a.plot([dt(t) for t in T[m]], R[s][m], color=col, lw=1.4)
        a.set_ylim(-1.5, 23); a.set_yticks([0, 10, 20]); a.set_ylabel("Odom, Hz")
        a.set_title(lab, loc="left", color=P["ink"], fontsize=9, pad=3)
    b = axs[2]
    tm = [(float(r["t"]), float(r["tx_mc"])) for r in mc if t0 <= float(r["t"]) <= t1]
    b.plot([dt(t) for t, _ in tm], [max(v, 0.5) for _, v in tm], color=S[4], lw=1.4)
    b.set_yscale("log"); b.set_ylim(0.4, 3000); b.set_ylabel("Multicast tx, frames/s")
    b.set_title("eno1 multicast frames sent, 1 s (mcast.jsonl; 0 drawn at 0.5)", loc="left", color=P["ink"], fontsize=9, pad=3)
    lane.set_xlim(dt(t0), dt(t1))
    b.xaxis.set_major_locator(mdates.MinuteLocator(byminute=range(0, 60, 5), tz=TZ)); b.xaxis.set_major_formatter(HM)
    b.set_xlabel("Time on snorlax, EST")
    for a in [lane] + axs[:2]:
        plt.setp(a.get_xticklabels(), visible=False)
        a.set_xlabel("Time on snorlax, EST"); a.xaxis.label.set_visible(False)
    ax.save(fig, os.path.join(OUT, "r2_3_timeline.png")); plt.close(fig)


# ---------- 4. MIB participant pool
def chart_pool():
    cases = sorted(day(N), key=lambda c: c["t_start"])
    pool, ev = EV["mib_pool_limit_lines"], EV["mib_evictions"]
    lab = [c["case"] for c in cases]
    lim = [sum(c["t_start"] <= t <= c["t_end"] for t in pool) for c in cases]
    evc = [sum(c["t_start"] <= e["t"] <= c["t_end"] for e in ev) for c in cases]
    idle = [x["total"] for x in EV["census"] if x["t"] < cases[0]["t_start"]]
    fig, (a, b) = plt.subplots(1, 2, figsize=(11, 6.6), sharey=True, gridspec_kw={"wspace": 0.08})
    for k, (axx, vals, xl, title) in enumerate(((a, evc, "Pool-full evictions per case, count", "MIB participant pool"),
                                                (b, lim, "RESSOURCE LIMIT EXCEEDED lines per case, count", "POOL LIMIT LINES"))):
        axx.barh(np.arange(len(lab)), vals, color=S[0], height=0.6)
        for i, v in enumerate(vals):
            axx.annotate(str(v), xy=(v, i), xytext=(4, 0), textcoords="offset points", va="center",
                         color=P["muted"], fontsize=9, fontfamily="Roboto Mono")
        axx.grid(axis="y", visible=False); axx.grid(axis="x", visible=True)
        axx.set_xlabel(xl); axx.set_xlim(0, max(vals) * 1.18)
    hbar_axes(a, lab); b.tick_params(axis="y", length=0)
    a.set_ylabel("Stress case, in run order"); b.set_ylabel("Stress case"); b.yaxis.label.set_visible(False)
    ax.title(a, "MIB participant pool", f"Idle on eno1 before the matrix: {min(idle)} to {max(idle)} participants "
             f"(median {int(np.median(idle))}) against 24 slots. {len(ev)} evictions, {len(pool)} limit lines, all inside S1, S2, S6.")
    b.set_title("POOL LIMIT LINES", loc="left", color=P["ink"], fontsize=11, fontweight="bold", fontfamily="Roboto Condensed", pad=22)
    ax.save(fig, os.path.join(OUT, "r2_4_mib_pool.png")); plt.close(fig)


chart_ab(); chart_camera(); chart_timeline(); chart_pool()
print("wrote", sorted(f for f in os.listdir(OUT) if f.startswith("r2_")))
