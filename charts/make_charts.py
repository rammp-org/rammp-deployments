#!/usr/bin/env python3
"""Charts for the rammp-deployments#136 test report (flatbot, 2026-11-16).

    python3 make_charts.py <data dir> <png dir> [<alex-style-guide dir>]

Reads the files written by extract.py plus probes.csv. Style: alex-style (dark, opaque
background so the PNGs read the same on GitHub light and dark). Times are snorlax local (EST).
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
HMS = mdates.DateFormatter("%H:%M:%S", tz=TZ)
SUBS = [("A", "A, ~/cyclonedds.xml (eno1), from 12:34"), ("B", "B, eno1 test config, from 12:34"),
        ("A2", "A2, ~/cyclonedds.xml (eno1), from 14:24"), ("C", "C, containers' file (autodetermine), from 14:24")]

# ---------- load
rows = list(csv.DictReader(open(D("odom_rate_1s.csv"))))
T = np.array([int(r["t"]) for r in rows])
RATE = {s: np.array([float(r[s]) if r[s] != "" else np.nan for r in rows]) for s, _ in SUBS}
EV = json.load(open(D("events.json")))
CASES = [json.loads(l) for l in open(D("cases.jsonl"))]
BLOB = [json.loads(l) for l in open(D("blob.jsonl"))]
case = lambda name, tag="fw048_baseline": next(c for c in CASES if c["case"] == name and c["params"].get("tag") == tag)
ABORTS = [e["t"] for e in EV["mib_abort"]]
WREB = EV["wheel_reboot"]
HOST = {h["what"][:4]: h for h in EV["host_log"]}
T_ARM = 1794854352.0     # enp3s0 first link up (journal 13:39:12)
T_IP = 1794856055.092    # eno1 lease .2 -> .53 (journal 14:07:35)
DROP_CASES = [c for c in CASES if c["params"].get("tag") == "fw048_baseline" and c["odom"]["max_gap_s"] > 1]


def binned(t, y, w):
    """Mean over w-second bins; bins with no samples stay NaN so stopped subscribers show as breaks."""
    k = (t - t[0]) // w
    out_t, out_y = [], []
    for b in np.unique(k):
        m = k == b
        v = y[m]
        out_t.append(t[m][0]); out_y.append(np.nan if np.all(np.isnan(v)) else np.nanmean(v))
    return np.array(out_t), np.array(out_y)


# ---------- 1. day timeline
def chart_timeline():
    fig = plt.figure(figsize=(11, 8.2))
    gs = fig.add_gridspec(5, 1, height_ratios=[1.55, 1, 1, 1, 1], hspace=0.28)
    lane = fig.add_subplot(gs[0])
    axs = [fig.add_subplot(gs[i + 1], sharex=lane) for i in range(4)]
    x0, x1 = dt(1794850200), dt(T[-1] + 60)  # 12:30 to end of data

    # event lane: y 3 stress, 2 MIB abort, 1 wheel reboots (unrelated), 0 host network
    for c in DROP_CASES:
        lane.axvspan(dt(c["t_stress_start"]), dt(c["t_stress_end"]), ymin=0.78, ymax=0.97, color=P["warning"], lw=0)
    lane.annotate("S4 10 Mbit/s bcast 12:51, mcast 12:53; S7 2 image subs 13:05", xy=(dt(case("S7_imgsub_2")["t_stress_end"]), 3),
                  xytext=(6, 0), textcoords="offset points", ha="left", va="center", color=P["ink"], fontsize=8.5)
    lane.vlines([dt(t) for t in ABORTS], 1.7, 2.3, color=P["critical"], lw=1.6)
    lane.annotate("6 aborts, 13:02:51 and 13:05:27 to 13:05:58", xy=(dt(ABORTS[-1]), 2), xytext=(6, 0),
                  textcoords="offset points", va="center", color=P["ink"], fontsize=8.5)
    lane.vlines([dt(w["t"]) for w in WREB], 0.7, 1.3, color=P["off"], lw=1.6)
    sil = EV["wheel_silence"][0]
    lane.plot([dt(sil["t_back"] - sil["silent_s"]), dt(sil["t_back"])], [1, 1], color=P["off"], lw=2.0, solid_capstyle="butt")
    lane.annotate("19 wheel reboots, fw OTA and power cycle (unrelated)", xy=(dt(WREB[-1]["t"]), 1), xytext=(6, 0),
                  textcoords="offset points", va="center", color=P["muted"], fontsize=8.5)
    for t, lab, side in ((T_ARM, "arm LAN up 13:39:12", -1), (T_IP, "eno1 .2 to .53, 14:07:35", 1)):
        lane.vlines(dt(t), -0.3, 0.3, color=P["ink"], lw=1.6)
        lane.annotate(lab, xy=(dt(t), 0), xytext=(5 * side, 0), textcoords="offset points", va="center",
                      ha="left" if side > 0 else "right", color=P["ink"], fontsize=8.5)
    lane.set_ylim(-0.6, 3.6)
    lane.set_yticks([3, 2, 1, 0], ["Stress dropped odom", "MIB abort", "Wheel reboot", "Host network"])
    lane.tick_params(axis="y", colors=P["ink"], length=0)
    lane.grid(False)
    lane.set_ylabel("Event")
    plt.setp(lane.get_xticklabels(), visible=False)
    ax.title(lane, "/base/odom on snorlax, 2026-11-16",
             "Per-subscriber receive rate, 5 s means. Gray marks are wheel firmware work, not DDS.")

    for i, (a, (s, lab)) in enumerate(zip(axs, SUBS)):
        for c in DROP_CASES:
            a.axvspan(dt(c["t_stress_start"]), dt(c["t_stress_end"]), color=P["warning"], alpha=0.18, lw=0)
        a.axvspan(dt(WREB[0]["t"] - 30), dt(WREB[-1]["t"]), color=P["off"], alpha=0.16, lw=0)
        for t in (T_ARM, T_IP):
            a.axvline(dt(t), color=P["ink"], lw=0.8, ls=(0, (2, 3)))
        for t in ABORTS:
            a.axvline(dt(t), color=P["critical"], lw=0.8, alpha=0.8)
        bt, by = binned(T, RATE[s], 5)
        a.plot([dt(t) for t in bt], by, color=S[i], lw=1.4)
        a.set_ylim(-1.5, 23)
        a.set_yticks([0, 10, 20])
        a.set_title(lab, loc="left", color=P["ink"], fontsize=9, pad=3)
        if i < 3:
            plt.setp(a.get_xticklabels(), visible=False)
    for a in axs:
        a.set_ylabel("Odom, Hz")
    lane.set_xlim(x0, x1)
    axs[-1].xaxis.set_major_locator(mdates.MinuteLocator(byminute=[0, 30], tz=TZ))
    axs[-1].xaxis.set_major_formatter(HM)
    axs[-1].set_xlabel("Time on snorlax, EST")
    for a in [lane] + axs[:-1]:  # shared x axis: labelled once on the bottom panel
        a.set_xlabel("Time on snorlax, EST"); a.xaxis.label.set_visible(False)
    ax.save(fig, os.path.join(OUT, "1_timeline.png"))
    plt.close(fig)


# ---------- 2. multicast by configuration
def chart_multicast():
    s1 = case("S7_imgsub_1"); s2 = case("S7_imgsub_2"); sr = next(c for c in CASES if c["case"] == "S7r_imgsub_2_pubspdp")
    b1 = next(b for b in BLOB if b["variant"] == "P1_1sub"); b2 = next(b for b in BLOB if b["variant"] == "P1" and "data_mean_fps" in b)
    bs = next(b for b in BLOB if b["variant"] == "P2")
    items = [
        ("Camera, image default, 1 sub (S7, 13:03)", s1["eno1"]["stress"]["tx_multicast_per_s"]),
        ("Camera, image default, 2 subs (S7, 13:05)", s2["eno1"]["stress"]["tx_multicast_per_s"]),
        ("Camera, spdp publisher, 2 added subs, 3 total (S7r, 14:03)", sr["tx_mcast_mean_fps"]),
        ("Synthetic, default, 1 sub", b1["data_mean_fps"]),
        ("Synthetic, default, 2 subs", b2["data_mean_fps"]),
        ("Synthetic, spdp publisher, 2 subs", bs["data_mean_fps"]),
    ]
    fig, a = plt.subplots(figsize=(11, 4.4))
    labs = [i[0] for i in items]; vals = [i[1] for i in items]
    cols = [S[1] if v > 1000 else S[0] for v in vals]
    y = np.arange(len(items))
    a.barh(y, vals, color=cols, height=0.55)
    a.set_xscale("log"); a.set_xlim(0.8, 60000)
    a.set_yticks(y, labs); a.invert_yaxis()
    a.tick_params(axis="y", colors=P["ink"], length=0)
    a.grid(axis="y", visible=False); a.grid(axis="x", visible=True)
    for i, v in enumerate(vals):
        a.annotate(f"{v:,.1f}", xy=(v, i), xytext=(5, 0), textcoords="offset points", va="center",
                   color=P["muted"], fontsize=9.5, fontfamily="Roboto Mono")
    idle = [s1["eno1"]["pre"]["tx_multicast_per_s"], s2["eno1"]["pre"]["tx_multicast_per_s"], sr["tx_mcast_pre_fps"]]
    a.set_ylim(len(items) - 0.5, -0.5)
    a.vlines(max(idle), 0.45, len(items) - 0.5, color=P["warning"], ls=(0, (4, 3)), lw=1)  # stops below row 0's value label
    a.annotate(f"idle before the camera runs, up to {max(idle):.1f}", xy=(max(idle), len(items) - 1), xytext=(5, 0),
               textcoords="offset points", color=P["muted"], fontsize=8.5, va="center")
    a.set_xlabel("Multicast frames sent on eno1, frames/s (log)")
    a.set_ylabel("Publisher and subscribers")
    ax.title(a, "Multicast sent on eno1 by Cyclone config",
             "Stress-window mean from the eno1 NIC tx counter. Red: the one configuration that took the MIB down.")
    ax.save(fig, os.path.join(OUT, "2_multicast.png"))
    plt.close(fig)


# ---------- 3. zoom on the 13:05 flood
def chart_flood():
    t0, t1 = 1794852150, 1794852490  # 13:02:30 to 13:08:10
    m = (T >= t0) & (T <= t1)
    tx = list(csv.DictReader(open(D("eno1_tx_1s_1258_1310.csv"))))
    fig, (a, b) = plt.subplots(2, 1, figsize=(11, 6.2), sharex=True, gridspec_kw={"height_ratios": [1.3, 1], "hspace": 0.12})
    windows = [("S7 1 sub", case("S7_imgsub_1")), ("S7 2 subs", case("S7_imgsub_2")), ("S8 10M to 7401", case("S8_mcast7401_10M"))]
    for c_ax in (a, b):
        for lab, c in windows:
            c_ax.axvspan(dt(c["t_stress_start"]), dt(c["t_stress_end"]), color=P["warning"] if c["odom"]["max_gap_s"] > 1 else P["off"],
                         alpha=0.18, lw=0)
        for t in ABORTS:
            c_ax.axvline(dt(t), color=P["critical"], lw=1.2, ls=(0, (4, 3)))
    for lab, c in windows:
        a.annotate(lab, xy=(dt(c["t_stress_start"]), 22.5), xytext=(3, 0), textcoords="offset points",
                   color=P["muted"], fontsize=8.5, va="center")
    for t in ABORTS:
        a.annotate("abort", xy=(dt(t), 6), xytext=(-2, 0), textcoords="offset points", rotation=90,
                   color=P["ink"], fontsize=8, va="bottom", ha="right")
    for i, s in enumerate(["A", "B"]):
        a.plot([dt(t) for t in T[m]], RATE[s][m], color=S[i], lw=2.0, label=s)
    g = max(case("S7_imgsub_2")["odom"]["gaps"], key=lambda g: g["s"])
    a.plot([dt(g["t0"]), dt(g["t1"])], [-1.2, -1.2], color=P["ink"], lw=3, solid_capstyle="butt")
    a.annotate(f"case subscriber gap {g['s']:.2f} s", xy=(dt(g["t1"]), -1.2), xytext=(5, 0), textcoords="offset points",
               color=P["ink"], fontsize=8.5, va="center")
    a.set_ylim(-2.5, 24); a.set_ylabel("/base/odom rate, Hz")
    a.legend(loc="upper left", bbox_to_anchor=(0, 1.0), ncol=2, handlelength=1, borderaxespad=0.2)
    ax.title(a, "The 13:05 multicast flood, zoomed",
             "Long-lived subscribers A and B, per second. Dashed red: MIB abort() in the serial console.")
    tt = [dt(r["t"]) for r in tx if t0 <= float(r["t"]) <= t1]
    tv = [float(r["eno1_tx_Bps"]) * 8 / 1e6 for r in tx if t0 <= float(r["t"]) <= t1]
    b.plot(tt, tv, color=S[4], lw=2.0)
    b.set_yscale("log"); b.set_ylim(0.1, 400)
    b.set_ylabel("eno1 transmit, Mbit/s (log)")
    b.annotate("All traffic the Jetson sends on eno1,\n1 s samples from live.py (not multicast only)",
               xy=(dt(1794852188), 300), color=P["muted"], fontsize=8.5, va="top", ha="left")
    b.xaxis.set_major_locator(mdates.SecondLocator(bysecond=[0, 30], tz=TZ)); b.xaxis.set_major_formatter(HMS)
    b.set_xlim(dt(t0), dt(t1)); b.set_xlabel("Time on snorlax, EST")
    a.set_xlabel("Time on snorlax, EST"); a.xaxis.label.set_visible(False)
    ax.save(fig, os.path.join(OUT, "3_flood_zoom.png"))
    plt.close(fig)


# ---------- 4. stress matrix
def chart_matrix():
    cs = [c for c in CASES if c["params"].get("tag") == "fw048_baseline" and "counter_delta" in c]
    labs = [c["case"] for c in cs]
    gaps = [c["odom"]["max_gap_s"] for c in cs]
    ov = []
    for c in cs:
        d = c["counter_delta"]
        ov.append((d["drive_left"].get("overrun"), d["drive_right"].get("overrun")))
    fig, (a, b) = plt.subplots(1, 2, figsize=(11, 6.2), sharey=True, gridspec_kw={"wspace": 0.08, "width_ratios": [1.25, 1]})
    y = np.arange(len(cs))
    a.barh(y, gaps, color=[S[1] if g > 1 else S[0] for g in gaps], height=0.6)
    for i, g in enumerate(gaps):
        a.annotate(f"{g:.2f}", xy=(g, i), xytext=(4, 0), textcoords="offset points", va="center", color=P["muted"],
                   fontsize=9, fontfamily="Roboto Mono")
    a.set_yticks(y, labs); a.invert_yaxis(); a.tick_params(axis="y", colors=P["ink"], length=0)
    a.grid(axis="y", visible=False); a.grid(axis="x", visible=True); a.set_xlim(0, 31)
    a.set_xlabel("Longest /base/odom gap, s"); a.set_ylabel("Stress case")
    ax.title(a, "Stress matrix on the wheel LAN", "Red: odom stopped for more than 1 s. 1 Mbit/s floods pass, 10 Mbit/s floods fail.")
    tot = [(l or 0) + (r or 0) for l, r in ov]
    b.barh(y, tot, color=S[0], height=0.6)
    for i, (l, r) in enumerate(ov):
        txt = f"{l}/{r}" if l is not None else f"R {r}, left wheel silent"
        b.annotate(txt, xy=(tot[i], i), xytext=(4, 0), textcoords="offset points", va="center", color=P["muted"],
                   fontsize=9, fontfamily="Roboto Mono")
    b.grid(axis="y", visible=False); b.grid(axis="x", visible=True); b.set_xlim(0, 1150)
    b.tick_params(axis="y", length=0)
    b.set_xlabel("Wheel receive overruns, left + right, count")
    b.set_ylabel("Stress case"); b.yaxis.label.set_visible(False)
    b.set_title("WHEEL RX OVERRUNS (L/R)", loc="left", color=P["ink"], fontsize=11, fontweight="bold",
                fontfamily="Roboto Condensed", pad=22)
    ax.save(fig, os.path.join(OUT, "4_stress_matrix.png"))
    plt.close(fig)


# ---------- 5. interface probes
def chart_probes():
    pr = list(csv.DictReader(open(D("probes.csv"))))
    fig, a = plt.subplots(figsize=(11, 5.6))
    y = np.arange(len(pr))
    vals = [int(p["odom_msgs_14s"]) if p["odom_msgs_14s"] else 0 for p in pr]
    a.barh(y, vals, color=S[0], height=0.6)
    for i, p in enumerate(pr):
        txt = p["odom_msgs_14s"] if p["odom_msgs_14s"] else ""
        if p["note"]:
            txt = (txt + ", " if txt else "") + p["note"]
        a.annotate(txt, xy=(vals[i], i), xytext=(5, 0), textcoords="offset points", va="center",
                   color=P["critical"] if "fails" in p["note"] else P["muted"], fontsize=9, fontfamily="Roboto Mono")
    a.set_yticks(y, [p["config"] for p in pr]); a.invert_yaxis()
    a.tick_params(axis="y", colors=P["ink"], length=0, labelsize=9)
    a.grid(axis="y", visible=False); a.grid(axis="x", visible=True); a.set_xlim(0, 320)
    a.set_xlabel("/base/odom messages received in 14 s, count"); a.set_ylabel("Cyclone config on the probe")
    ax.title(a, "Interface selection probes", "Host probe, 14 s run including discovery, arm LAN up. Red text: no node.")
    ax.save(fig, os.path.join(OUT, "5_interface_probes.png"))
    plt.close(fig)


chart_timeline(); chart_multicast(); chart_flood(); chart_matrix(); chart_probes()
print("wrote", sorted(os.listdir(OUT)))
