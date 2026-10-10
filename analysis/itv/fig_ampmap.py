"""fig_ampmap.py — 前刷の図：電空レギュレータの振幅比（出力 / 指令）を、周波数 × 指令振幅で3チャネル並べる

  DF：9/30 振幅スイープ・9/30 正弦・10/6 正弦（中心 0.30 MPa 付近）
  F・G：10/6 のチャネル別試験（pm_ch_F / pm_ch_G、2/3/4 Hz）
  実線＝実機、破線＝流量上限モデル O（DF で同定、どのチャネルにも同じ値）

使い方: python analysis/itv/fig_ampmap.py
出力:   data/user0/jfps2026/figures_paper/fig_ampmap.pdf（と .png）
"""
import os, sys, re, json, numpy as np, pandas as pd
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import pmeval as E  # noqa: E402
from base_eval import sim_O  # noqa: E402
import check_fg as C  # noqa: E402   （F・G の読み込みと振幅比。import 時に check_fg の表も表示される）
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt  # noqa: E402

OUT = os.path.join(E.JP_DEFAULT, "data", "user0", "jfps2026", "figures_paper"); os.makedirs(OUT, exist_ok=True)
plt.rcParams.update({"font.family": "serif", "font.serif": ["TeX Gyre Termes", "Times New Roman", "DejaVu Serif"],
                     "mathtext.fontset": "stix", "font.size": 8, "axes.spines.top": False, "axes.spines.right": False,
                     "lines.linewidth": 1.1, "lines.markersize": 3.5})
AMPS = [0.02, 0.04, 0.08, 0.12]
COL = {0.02: "#c2410c", 0.04: "#d97706", 0.08: "#2563eb", 0.12: "#1e3a8a"}
O = np.array(json.load(open(os.path.join(_HERE, "fit_A.json")))["O"])

# DF
D = {k: E.load(E.JP_DEFAULT, k) for k in ("ampsweep", "sine", "sine2")}
preds = {k: sim_O(d["u"], float(np.median(d["m"][:20])), *O) for k, d in D.items()}
T = E.sine_table(D, preds, keys=("ampsweep", "sine", "sine2"))
T = T[T.c.between(0.25, 0.35)].groupby(["a", "f"])[["real", "model"]].mean().reset_index()
panels = {"DF": T}
# F・G
for ch, fn in (("F", "data_pm_ch_F_1791270055.csv"), ("G", "data_pm_ch_G_1791270328.csv")):
    u, m, seg = C.load(ch, fn); p = sim_O(u, float(np.median(m[:20])), *O)
    ar, ao = C.amps(u, m, seg), C.amps(u, p, seg)
    panels[ch] = pd.DataFrame([dict(a=a, f=f, real=ar[(a, f)], model=ao[(a, f)]) for (a, f) in ar])

fig, axs = plt.subplots(1, 3, figsize=(6.7, 1.8), sharey=True)
for ax, (ch, P) in zip(axs, panels.items()):
    for a in AMPS:
        g = P[np.isclose(P.a, a)].sort_values("f")
        if len(g) == 0:
            continue
        ax.plot(g.f, g.real, "o-", color=COL[a], label=f"{a*1000:.0f} kPa")
        ax.plot(g.f, g.model, "--", color=COL[a], alpha=0.6, lw=0.9)
    ax.set_title(ch, loc="left", fontsize=9); ax.set_xlabel("Frequency [Hz]")
    ax.set_xlim(0.8, 6.2 if ch == "DF" else 4.2); ax.set_ylim(0, 1.45); ax.grid(alpha=0.25, lw=0.5)
axs[0].set_ylabel("Amplitude ratio (output / command)")
h, l = axs[0].get_legend_handles_labels()
h += [plt.Line2D([], [], color="0.4", ls="--", lw=0.9)]; l += ["Flow-limited model"]
fig.legend(h, l, loc="upper center", ncol=5, frameon=False, bbox_to_anchor=(0.55, 1.03), title=None)
fig.tight_layout(rect=(0, 0, 1, 0.9))
for ext in ("pdf", "png"):
    fig.savefig(os.path.join(OUT, f"fig_ampmap.{ext}"), dpi=200)
print("saved", os.path.relpath(os.path.join(OUT, "fig_ampmap.pdf"), E.JP_DEFAULT))
for ch, P in panels.items():
    print(ch); print(P.pivot_table(index="f", columns="a", values="real").round(2).to_string())
